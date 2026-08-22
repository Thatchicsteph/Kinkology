"""Iteration 17: verify the TURN/STUN escape hatch is actually deliverable to
self-hosted users, and that the startup ICE log honors + masks credentials.

Covers:
  * docker-compose.yml backend.environment pass-throughs (${VAR:-} form)
  * env.docker.example documentation block
  * stream._log_ice_config() record content: defaults / empty warn / TURN redaction
  * server.py imports and calls _log_ice_config() after stream_patch.apply()
"""
import logging
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, "/app/backend")

import stream  # noqa: E402

COMPOSE = Path("/app/docker-compose.yml")
ENV_EXAMPLE = Path("/app/env.docker.example")
SERVER_PY = Path("/app/backend/server.py")

ICE_ENV_KEYS = ["STREAM_STUN_SERVERS", "STREAM_TURN_URL",
                "STREAM_TURN_USERNAME", "STREAM_TURN_PASSWORD"]


@pytest.fixture
def clean_ice_env():
    saved = {k: os.environ.get(k) for k in ICE_ENV_KEYS}
    for k in ICE_ENV_KEYS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def captured():
    """Attach a list handler to the stream logger and return it."""
    lg = logging.getLogger("ossm-bridge.stream")
    h = _ListHandler()
    h.setLevel(logging.DEBUG)
    prev_level, prev_prop = lg.level, lg.propagate
    lg.setLevel(logging.DEBUG)
    lg.addHandler(h)
    yield h
    lg.removeHandler(h)
    lg.setLevel(prev_level)
    lg.propagate = prev_prop


def _msgs(handler, level=None):
    return [r.getMessage() for r in handler.records
            if level is None or r.levelno == level]


# --- docker-compose pass-through -------------------------------------------
class TestDockerComposePassThrough:
    def test_compose_parses(self):
        assert COMPOSE.exists(), "docker-compose.yml missing"
        data = yaml.safe_load(COMPOSE.read_text())
        assert "backend" in data["services"]

    @pytest.mark.parametrize("key", ICE_ENV_KEYS)
    def test_key_present_with_default_interpolation(self, key):
        data = yaml.safe_load(COMPOSE.read_text())
        env = data["services"]["backend"]["environment"]
        assert isinstance(env, dict), "backend.environment must be a mapping"
        assert key in env, f"{key} missing from backend.environment"
        val = str(env[key])
        # Must use the ${VAR:-} form so unset .env vars don't crash compose.
        assert re.fullmatch(r"\$\{%s:-[^}]*\}" % key, val), \
            f"{key} value {val!r} is not in ${{{key}:-}} form"


# --- env.docker.example documentation --------------------------------------
class TestEnvExampleDocs:
    def test_file_exists(self):
        assert ENV_EXAMPLE.exists()

    @pytest.mark.parametrize("key", ICE_ENV_KEYS)
    def test_commented_example_entry(self, key):
        lines = ENV_EXAMPLE.read_text().splitlines()
        hits = [l for l in lines if l.strip().startswith("#") and f"{key}=" in l]
        assert hits, f"No commented-out example line for {key}"

    def test_mentions_mobile_cgnat_turn_rationale(self):
        text = ENV_EXAMPLE.read_text().lower()
        assert "turn" in text
        assert "cgnat" in text or "symmetric nat" in text, \
            "No symmetric NAT / CGNAT explanation in env.docker.example"
        assert "mobile" in text or "4g" in text or "5g" in text


# --- _log_ice_config() behaviour -------------------------------------------
class TestLogIceConfig:
    def test_default_logs_both_public_stun(self, clean_ice_env, captured):
        stream._log_ice_config()
        infos = _msgs(captured, logging.INFO)
        assert infos, "no INFO record emitted"
        joined = " ".join(infos)
        assert "stun:stun.l.google.com:19302" in joined
        assert "stun:stun.cloudflare.com:3478" in joined
        warns = _msgs(captured, logging.WARNING)
        assert not any("NO ICE servers" in w for w in warns), \
            f"unexpected empty-list warning: {warns}"

    def test_explicit_empty_stun_warns(self, clean_ice_env, captured):
        os.environ["STREAM_STUN_SERVERS"] = ""
        stream._log_ice_config()
        warns = _msgs(captured, logging.WARNING)
        assert any("NO ICE servers configured" in w for w in warns), \
            f"expected empty-list WARNING, got {warns}"

    def test_turn_credentials_redacted(self, clean_ice_env, captured):
        os.environ["STREAM_TURN_URL"] = "turn:t.example:3478"
        os.environ["STREAM_TURN_USERNAME"] = "u"
        os.environ["STREAM_TURN_PASSWORD"] = "super-secret-abcd"
        stream._log_ice_config()
        infos = _msgs(captured, logging.INFO)
        assert infos, "no INFO record emitted"
        joined = " ".join(infos)
        assert "turn:t.example:3478" in joined
        assert "user=u" in joined
        assert "***" in joined
        # secret must not leak in the formatted message NOR in raw args
        assert "super-secret-abcd" not in joined
        for r in captured.records:
            assert "super-secret-abcd" not in repr(r.args), "secret leaked via record args"

    def test_turns_scheme_also_redacted(self, clean_ice_env, captured):
        os.environ["STREAM_TURN_URL"] = "turns:t.example:5349"
        os.environ["STREAM_TURN_PASSWORD"] = "another-secret-xyz"
        stream._log_ice_config()
        joined = " ".join(_msgs(captured, logging.INFO))
        assert "turns:t.example:5349" in joined
        assert "another-secret-xyz" not in joined
        assert "***" in joined


# --- server.py wiring ------------------------------------------------------
class TestServerWiring:
    def test_imports_and_calls_log_ice_config(self):
        src = SERVER_PY.read_text()
        assert re.search(r"from stream import .*_log_ice_config", src), \
            "server.py does not import _log_ice_config from stream"
        lines = [l.strip() for l in src.splitlines()]
        assert "_log_ice_config()" in lines, "_log_ice_config() not called at module level"
        apply_i = lines.index("stream_patch.apply()")
        call_i = lines.index("_log_ice_config()")
        assert call_i > apply_i, "_log_ice_config() must be called after stream_patch.apply()"

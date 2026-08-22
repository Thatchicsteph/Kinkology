"""stream_patch opt-in / no-op behaviour + docker-compose default assertions.

Feature under test (iteration 19): the aioice monkey-patch must be a NO-OP
unless the operator explicitly sets STREAM_UDP_MIN/MAX/STREAM_PUBLIC_IP, and
docker-compose must no longer default those vars nor publish a UDP range.
"""
import os
import subprocess
import sys
import re
import textwrap

import pytest
import yaml

BACKEND_DIR = "/app/backend"
COMPOSE = "/app/docker-compose.yml"


def _run_probe(extra_env: dict, load_env_file: bool) -> dict:
    """Import stream_patch in a clean subprocess and report patch state."""
    script = textwrap.dedent(
        f"""
        import json, os, sys
        sys.path.insert(0, {BACKEND_DIR!r})
        if {load_env_file!r}:
            # replicate server.py's load_dotenv of backend/.env
            from dotenv import load_dotenv
            load_dotenv({BACKEND_DIR!r} + '/.env')
        import aioice.ice as _ice
        original = _ice.Connection.get_component_candidates
        import stream_patch
        stream_patch.apply()
        print(json.dumps({{
            "UDP_MIN": stream_patch.UDP_MIN,
            "UDP_MAX": stream_patch.UDP_MAX,
            "PUBLIC_IP": stream_patch.PUBLIC_IP,
            "patched": _ice.Connection.get_component_candidates is stream_patch._patched_get_component_candidates,
            "is_original": _ice.Connection.get_component_candidates is original,
        }}))
        """
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("STREAM_UDP_MIN", "STREAM_UDP_MAX", "STREAM_PUBLIC_IP")}
    env.update(extra_env)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=env, cwd=BACKEND_DIR, timeout=120)
    assert out.returncode == 0, f"probe failed: {out.stderr[-2000:]}"
    import json as _json
    return _json.loads(out.stdout.strip().splitlines()[-1])


# --- module: stream_patch (default = no-op) --------------------------------
class TestPatchNoOpByDefault:
    def test_constants_blank_without_env(self):
        r = _run_probe({}, load_env_file=False)
        assert r["UDP_MIN"] == 0
        assert r["UDP_MAX"] == 0
        assert r["PUBLIC_IP"] == ""

    def test_get_component_candidates_untouched(self):
        r = _run_probe({}, load_env_file=False)
        assert r["patched"] is False, "patch installed despite no STREAM_* env vars"
        assert r["is_original"] is True, "aioice method was replaced"

    def test_no_op_when_env_file_loaded(self):
        """The real runtime path: backend/.env loaded, no STREAM_* keys in it."""
        r = _run_probe({}, load_env_file=True)
        assert r["patched"] is False, r
        assert r["UDP_MIN"] == 0 and r["UDP_MAX"] == 0 and r["PUBLIC_IP"] == ""


# --- module: stream_patch (explicit opt-in) -------------------------------
class TestPatchOptIn:
    def test_patch_installs_when_env_set(self):
        r = _run_probe({"STREAM_UDP_MIN": "50000", "STREAM_UDP_MAX": "50003",
                        "STREAM_PUBLIC_IP": "127.0.0.1"}, load_env_file=False)
        assert r["UDP_MIN"] == 50000 and r["UDP_MAX"] == 50003
        assert r["PUBLIC_IP"] == "127.0.0.1"
        assert r["patched"] is True, "patch did NOT install with opt-in env vars"

    def test_patch_installs_with_public_ip_only(self):
        r = _run_probe({"STREAM_PUBLIC_IP": "1.2.3.4"}, load_env_file=False)
        assert r["patched"] is True, r


# --- module: server.py bootstrap order ------------------------------------
class TestBootstrapOrder:
    def test_stream_patch_apply_after_dotenv(self):
        """stream_patch reads env at import time, so load_dotenv() must run
        BEFORE `import stream_patch` / apply() or .env-based opt-in is ignored."""
        src = open("/app/backend/server.py").read().splitlines()
        idx_dotenv = next(i for i, l in enumerate(src) if l.strip().startswith("load_dotenv("))
        idx_import = next(i for i, l in enumerate(src) if l.strip() == "import stream_patch")
        idx_apply = next(i for i, l in enumerate(src) if l.strip() == "stream_patch.apply()")
        assert idx_dotenv < idx_import and idx_dotenv < idx_apply, (
            f"load_dotenv at line {idx_dotenv+1} runs AFTER import stream_patch "
            f"(line {idx_import+1}) / apply() (line {idx_apply+1}) — STREAM_UDP_MIN/MAX/"
            "STREAM_PUBLIC_IP set in backend/.env are silently ignored."
        )


# --- config: docker-compose.yml -------------------------------------------
class TestDockerCompose:
    @pytest.fixture(scope="class")
    def compose(self):
        with open(COMPOSE) as f:
            return yaml.safe_load(f)

    def test_stream_env_defaults_blank(self, compose):
        env = compose["services"]["backend"]["environment"]
        for key in ("STREAM_UDP_MIN", "STREAM_UDP_MAX", "STREAM_PUBLIC_IP", "STREAM_PEER_IP"):
            assert key in env, f"{key} missing from compose environment"
            val = str(env[key])
            assert val.endswith(":-}") or val in ("${%s:-}" % key,), (
                f"{key} must default to blank, got {val!r}")
            assert ":-}" in val, f"{key} has a non-blank default: {val!r}"

    def test_no_unconditional_udp_port_publish(self, compose):
        ports = compose["services"]["backend"].get("ports") or []
        udp = [p for p in ports if "udp" in str(p).lower()]
        assert not udp, f"backend still publishes UDP ports unconditionally: {udp}"

    def test_ports_key_is_valid_yaml_type(self, compose):
        """`ports:` with only comments parses as null, which Docker Compose
        rejects with 'ports must be a list'. It must be absent or a list."""
        backend = compose["services"]["backend"]
        if "ports" in backend:
            assert isinstance(backend["ports"], list), (
                "docker-compose backend.ports is null (key present but empty) — "
                "`docker compose up` will fail schema validation; comment out the key too.")


# --- module: stream_patch (functional gather with opt-in env) --------------
class TestPatchFunctionalGather:
    def test_host_candidates_use_public_ip_and_port_range(self):
        """With env set in the PROCESS env, gathered host candidates must use
        STREAM_PUBLIC_IP and a port inside [STREAM_UDP_MIN, STREAM_UDP_MAX]."""
        script = textwrap.dedent(
            """
            import asyncio, json, os, sys, re
            sys.path.insert(0, '/app/backend')
            import stream_patch
            stream_patch.apply()
            from aiortc import RTCPeerConnection

            async def main():
                pc = RTCPeerConnection()
                pc.addTransceiver('video', direction='recvonly')
                await pc.setLocalDescription(await pc.createOffer())
                lines = [l for l in pc.localDescription.sdp.splitlines()
                         if 'candidate:' in l and ' typ host' in l]
                await pc.close()
                print(json.dumps(lines))

            asyncio.run(main())
            """
        )
        env = dict(os.environ)
        env.update({"STREAM_UDP_MIN": "50010", "STREAM_UDP_MAX": "50013",
                    "STREAM_PUBLIC_IP": "127.0.0.1"})
        out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, env=env, cwd=BACKEND_DIR, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        import json as _json
        lines = _json.loads(out.stdout.strip().splitlines()[-1])
        assert lines, "no host candidates gathered"
        for line in lines:
            m = re.search(r"candidate:\S+ \d+ udp \d+ (\S+) (\d+) typ host", line)
            assert m, line
            assert m.group(1) == "127.0.0.1", line
            assert 50010 <= int(m.group(2)) <= 50013, line

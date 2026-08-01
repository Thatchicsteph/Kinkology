import pytest_asyncio  # noqa: F401

# enable async tests
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark async tests")

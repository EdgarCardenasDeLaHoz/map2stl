"""The default test run has no network (tests/conftest.py::_block_network)."""

import socket

import pytest


def test_outbound_connection_is_blocked():
    # Matched by message: pytest imports conftest as its own module, so the
    # NetworkBlocked class there is not tests.conftest.NetworkBlocked.
    with socket.socket() as s, pytest.raises(OSError, match="network access"):
        s.connect(("93.184.216.34", 80))


def test_requests_get_is_blocked():
    import requests

    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("https://example.com", timeout=5)


def test_loopback_is_allowed():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        with socket.socket() as c:
            c.connect(srv.getsockname())

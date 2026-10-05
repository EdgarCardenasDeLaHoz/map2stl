"""/static/{path} stays inside the static and dist roots (audit 2026-10-05)."""

import pytest


@pytest.mark.parametrize("path", [
    "/static/..%2F..%2F..%2Fpyproject.toml",
    "/static/..%2F..%2F..%2Fconfig.json",
    "/static/%2Fetc%2Fpasswd",
])
def test_traversal_is_404(client, path):
    assert client.get(path).status_code == 404


def test_static_file_still_served(client):
    r = client.get("/static/js/main.js")
    assert r.status_code == 200
    assert "main.js" in r.text[:200]

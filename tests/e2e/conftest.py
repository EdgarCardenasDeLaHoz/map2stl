"""E2E test fixtures: live server + console-error gate.

Starts uvicorn once per test session, exposes the URL as `live_server_url`,
and wraps `page` with a strict console-error / page-error gate so any test
that triggers a JS error or unhandled promise rejection fails automatically.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def live_server_url() -> str:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.server.server:app",
         "--port", str(port), "--host", "127.0.0.1"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urlopen(f"{url}/api/regions", timeout=1)
            break
        except URLError:
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError("uvicorn did not become ready in 30s")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def strict_page(page):
    """A `page` with a console-error / page-error gate.

    Errors that match `_IGNORED_PATTERNS` are filtered (third-party noise);
    everything else fails the test at teardown.
    """
    errors: list[str] = []
    page_errors: list[str] = []

    def _on_console(msg):
        if msg.type == "error":
            text = msg.text
            if not any(p in text for p in _IGNORED_PATTERNS):
                errors.append(text)

    def _on_page_error(exc):
        page_errors.append(str(exc))

    page.on("console", _on_console)
    page.on("pageerror", _on_page_error)

    yield page

    failures = []
    if errors:
        failures.append(f"console errors:\n  - " + "\n  - ".join(errors))
    if page_errors:
        failures.append(f"page errors:\n  - " + "\n  - ".join(page_errors))
    if failures:
        pytest.fail("\n\n".join(failures))


# Add patterns here only for known-noisy third-party warnings, not real bugs.
_IGNORED_PATTERNS: tuple[str, ...] = (
    # Leaflet logs a 404 for the default marker icon when served via custom
    # static handler; not relevant to app correctness.
    "marker-icon",
)

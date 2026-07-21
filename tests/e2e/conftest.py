"""E2E test fixtures: live server + console-error gate.

Starts uvicorn once per test session, exposes the URL as `live_server_url`,
and wraps `page` with a strict console-error / page-error gate so any test
that triggers a JS error or unhandled promise rejection fails automatically.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
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


def _run_uvicorn(*, test_mode: bool) -> Iterator[str]:
    """Start `app.server.server:app` on a free port, yield its URL, then stop it.

    When *test_mode* is True the server runs with STRM2STL_TEST_MODE=1, so the DEM
    endpoint returns a fast deterministic gradient with no Earth Engine / network I/O
    (see app/server/routers/terrain.py).

    Each session gets its own STRM2STL_CACHE directory (a fresh tempdir), so a
    stale/flat entry from a real (non-test) DEM fetch in the shared repo cache
    can never leak into a test run and cause a false "DEM has no elevation
    data" preview failure — this bit an earlier version of the export-pipeline
    e2e tests via a poisoned cache entry left over from manual testing.
    """
    port = _free_port()
    env = dict(os.environ)
    if test_mode:
        env["STRM2STL_TEST_MODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="strm2stl_e2e_cache_") as cache_dir:
        env["STRM2STL_CACHE"] = cache_dir
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.server.server:app",
             "--port", str(port), "--host", "127.0.0.1"],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
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

        try:
            yield url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def live_server_url() -> Iterator[str]:
    """Live server (normal mode) for load/contract/interaction smoke tests."""
    yield from _run_uvicorn(test_mode=False)


@pytest.fixture(scope="session")
def live_server_url_testmode() -> Iterator[str]:
    """Live server with STRM2STL_TEST_MODE=1 — deterministic DEM, no network.

    Used by data-flow tests (e.g. terrain fetch) that must run reliably offline.
    """
    yield from _run_uvicorn(test_mode=True)


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

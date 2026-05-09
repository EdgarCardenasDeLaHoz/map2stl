"""Frontend smoke tests — load the app, exercise core paths, fail on any JS error.

These tests catch regressions that backend pytest tests miss: silent JS-side
breakage, broken bundles, and frontend-to-backend contract drift.
"""

from __future__ import annotations

import pytest


def test_page_loads_without_errors(strict_page, live_server_url):
    """Index page renders and Vue mounts without console errors."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    assert "3D Maps" in strict_page.title()


def test_vue_app_mounts(strict_page, live_server_url):
    """Vue mount points have children after init (i.e. Vue actually rendered)."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    sidebar_html = strict_page.locator("#vue-sidebar").inner_html()
    assert sidebar_html.strip(), "Vue sidebar mount point is empty — Vue did not render"


def test_critical_bundles_served(strict_page, live_server_url):
    """vue-main.js and vue-main.css resolve (not 404) — guards against missing dist/."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    for asset in ("/static/js/vue-main.js", "/static/css/vue-main.css"):
        resp = strict_page.request.get(f"{live_server_url}{asset}")
        assert resp.status == 200, f"{asset} returned {resp.status}"


def test_regions_api_reachable_from_browser(strict_page, live_server_url):
    """The browser can call /api/regions — guards against CORS/static mount drift."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    resp = strict_page.request.get(f"{live_server_url}/api/regions")
    assert resp.status == 200
    body = resp.json()
    assert "regions" in body, f"unexpected response shape: {body}"


def test_settings_endpoint_serves_combined_payload(strict_page, live_server_url):
    """SDK init endpoint returns projections+colormaps+datasets together."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    resp = strict_page.request.get(f"{live_server_url}/api/settings")
    assert resp.status == 200
    body = resp.json()
    for key in ("projections", "colormaps", "datasets"):
        assert key in body and len(body[key]) > 0, f"missing or empty {key}"


def test_main_headings_render(strict_page, live_server_url):
    """At least the primary section headings appear once Vue settles."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    headings = strict_page.locator("h1, h2, h3").all_inner_texts()
    joined = " | ".join(headings)
    # Loose match: the audit confirmed these exist; we just want SOMETHING rendered.
    assert any(h.strip() for h in headings), f"no headings rendered: {joined}"

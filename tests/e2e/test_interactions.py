"""Frontend interaction e2e tests — drive real user flows, fail on any JS error.

Where test_smoke.py only loads the page and checks APIs, these click through actual
flows (select a region, switch workflow tabs) and capture a screenshot artifact, all
under the strict console-error gate from conftest. They guard against Vue event-handler
regressions and frontend→backend contract drift that load-only tests miss.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_DIR = REPO_ROOT.parent / "_reports" / "e2e"   # gitignored repo-level _reports/


def test_selecting_region_loads_its_settings(strict_page, live_server_url):
    """Clicking a saved region in the sidebar fetches that region's settings.

    Picks whatever region renders first (DB-independent), clicks it, and asserts the
    `GET /api/regions/<name>/settings` round-trip the click triggers returns 200 — a
    real user-driven frontend→backend contract. The strict_page gate also fails the
    test on any JS console/page error raised during the interaction.
    """
    strict_page.goto(live_server_url, wait_until="networkidle")

    first_region = strict_page.locator("span.coordinate-item-name").first
    first_region.wait_for(state="visible", timeout=10_000)

    with strict_page.expect_response(
        lambda r: "/api/regions/" in r.url
        and r.url.endswith("/settings")
        and r.request.method == "GET"
    ) as resp_info:
        first_region.click()

    assert resp_info.value.status == 200, (
        f"region settings load failed: {resp_info.value.status}"
    )


def test_workflow_tab_navigation(strict_page, live_server_url):
    """The Explore / Edit / Extrude workflow tabs switch the active view on click."""
    strict_page.goto(live_server_url, wait_until="networkidle")

    def active_tab() -> str:
        return strict_page.evaluate(
            "() => [...document.querySelectorAll('[id^=tab]')]"
            ".filter(x => x.className.includes('active')).map(x => x.id).join(',')"
        )

    assert "tabExplore" in active_tab(), "Explore should be the default active tab"

    strict_page.locator("#tabEdit").click()
    assert "tabEdit" in active_tab(), "Edit tab did not activate on click"

    strict_page.locator("#tabExtrude").click()
    assert "tabExtrude" in active_tab(), "Extrude tab did not activate on click"


def test_terrain_fetch_renders_dem(strict_page, live_server_url_testmode):
    """Full terrain fetch flow: select region → Edit (Load DEM) step → Load DEM →
    `/api/terrain/dem` returns 200 → the DEM renders into a visible canvas.

    Runs against a STRM2STL_TEST_MODE server, so the DEM endpoint returns a
    deterministic gradient with no Earth Engine / network calls — reliable offline.
    The strict_page gate additionally fails on any JS error during the flow.
    """
    page = strict_page
    page.goto(live_server_url_testmode, wait_until="domcontentloaded")

    # 1. Select a region (sets the bbox the DEM fetch uses).
    region = page.locator("span.coordinate-item-name").first
    region.wait_for(state="visible", timeout=10_000)
    region.click()

    # 2. Go to the "Load DEM" step (the Edit tab).
    page.locator("#tabEdit").click()

    # 3. Reveal the Load DEM button (its collapsible section starts collapsed).
    load_btn = page.locator("#loadDemBtn")
    if not load_btn.is_visible():
        section = page.locator(".collapsible-section", has=page.locator("#loadDemBtn")).first
        section.locator(".collapsible-header").first.click()
    load_btn.wait_for(state="visible", timeout=10_000)

    # 4. Fetch the DEM and assert the (deterministic) endpoint succeeds.
    with page.expect_response(
        lambda r: "/api/terrain/dem" in r.url, timeout=20_000
    ) as resp_info:
        load_btn.click()
    assert resp_info.value.status == 200, (
        f"terrain DEM fetch failed: HTTP {resp_info.value.status}"
    )

    # 5. Assert the DEM actually rendered into a visible canvas (not blank).
    page.wait_for_function(
        "() => [...document.querySelectorAll('canvas')]"
        ".some(c => c.offsetParent !== null && c.width > 100 && c.height > 100)",
        timeout=10_000,
    )


def test_app_renders_screenshot(strict_page, live_server_url):
    """Full-page screenshot renders to a non-trivial image (catches blank-render
    regressions) and is saved as an inspectable artifact under _reports/e2e/."""
    strict_page.goto(live_server_url, wait_until="networkidle")
    strict_page.wait_for_timeout(800)  # let Vue + map settle

    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = _ARTIFACT_DIR / "app_loaded.png"
    strict_page.screenshot(path=str(out), full_page=True)

    assert out.stat().st_size > 10_000, (
        f"screenshot is suspiciously small ({out.stat().st_size} bytes) — "
        "the app may have rendered blank"
    )


def test_combined_view_resamples_mismatched_water_mask(strict_page, live_server_url):
    """renderCombinedView() must resample the water mask onto the DEM's exact
    grid, not assume the two independently-fetched layers share a pixel count.

    Found via manual exploration (2026-07-19): DEM and water-mask endpoints
    derive their output resolution through different algorithms (DEM resizes
    to `dim` on its longest axis; water-mask derives an Earth-Engine
    metres-per-pixel scale from `dim`), so with `maintain_dimensions=False`
    (the default since F-PROJ-DIMS) they can land on very different absolute
    pixel counts while still sharing the same aspect ratio. The combined view
    does direct flat-index math over both arrays, so a real mismatch used to
    log a warning and uselessly re-fetch the same mismatched shape — fixed by
    nearest-neighbour resampling the water mask onto the DEM grid first.
    """
    page = strict_page
    page.goto(live_server_url, wait_until="networkidle")

    region = page.locator("span.coordinate-item-name").first
    region.wait_for(state="visible", timeout=10_000)
    region.click()

    # Deliberately mismatched shapes (different aspect-preserving resolutions,
    # as the two real endpoints can legitimately produce).
    dem_h, dem_w = 40, 65
    wm_h, wm_w = 61, 99

    def _flat(h, w, fill=0.0):
        return [fill] * (h * w)

    def fulfill_dem(route):
        route.fulfill(json={
            "dem_values": _flat(dem_h, dem_w, 10.0),
            "dimensions": [dem_h, dem_w],
            "width": dem_w, "height": dem_h,
            "min_elevation": 0.0, "max_elevation": 10.0, "mean_elevation": 5.0,
            "bbox": [-122.514, 37.708, -122.353, 37.812],
        })

    def fulfill_water(route):
        water_vals = [1.0 if i % 3 == 0 else 0.0 for i in range(wm_h * wm_w)]
        route.fulfill(json={
            "water_mask_values": water_vals,
            "water_mask_dimensions": [wm_h, wm_w],
            "water_pixels": sum(1 for v in water_vals if v > 0.5),
            "total_pixels": wm_h * wm_w,
            "water_percentage": 33.0,
            "esa_values": [10.0] * (wm_h * wm_w),
            "esa_dimensions": [wm_h, wm_w],
        })

    page.route("**/api/terrain/dem*", fulfill_dem)
    page.route("**/api/terrain/water-mask*", fulfill_water)

    page.locator("#tabEdit").click()
    load_btn = page.locator("#loadDemBtn")
    if not load_btn.is_visible():
        section = page.locator(".collapsible-section", has=page.locator("#loadDemBtn")).first
        section.locator(".collapsible-header").first.click()
    load_btn.wait_for(state="visible", timeout=10_000)
    with page.expect_response(lambda r: "/api/terrain/dem" in r.url, timeout=15_000):
        load_btn.click()

    page.evaluate("() => window.loadWaterMask && window.loadWaterMask()")
    page.wait_for_timeout(1500)

    dem_dims = page.evaluate(
        "() => window.appState.lastDemData ? "
        "[window.appState.lastDemData.height, window.appState.lastDemData.width] : null"
    )
    water_dims = page.evaluate(
        "() => window.appState.lastWaterMaskData ? "
        "window.appState.lastWaterMaskData.water_mask_dimensions : null"
    )
    assert dem_dims == [dem_h, dem_w], f"mocked DEM dims not applied: {dem_dims}"
    assert water_dims == [wm_h, wm_w], f"mocked water dims not applied: {water_dims}"

    result = page.evaluate(
        "async () => { await window.renderCombinedView(); "
        "const c = document.querySelector('#combinedImage canvas'); "
        "return c ? {w: c.width, h: c.height} : null; }"
    )
    assert result is not None, "combined view did not render a canvas"
    assert result["w"] == dem_w and result["h"] == dem_h, (
        f"combined canvas should match DEM's own grid ({dem_h}x{dem_w}), got {result}"
    )

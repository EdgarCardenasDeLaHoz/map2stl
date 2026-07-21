"""UI-correctness e2e tests — layout overflow and the city-layer size gate.

Ported from an exploratory Playwright audit (2026-07-19). Each test documents
a concrete bug that audit found and this repo fixed:
  - the DEM settings strip overflowed its panel width, pushing the JSON-view
    toggle button off-screen (app.css .dem-strip);
  - window.appState.haversineDiagKm was referenced but never assigned, so the
    city/building-data size gate silently never worked anywhere it was used.
"""

from __future__ import annotations


def test_no_horizontal_page_overflow(strict_page, live_server_url):
    """The document must not be wider than the viewport in the default view.

    Regression guard for the DEM settings strip overflow found 2026-07-19: five
    strip buttons needed more width than the panel had, pushing the trailing
    button off-screen with no scrollbar to reach it.
    """
    page = strict_page
    page.goto(live_server_url, wait_until="networkidle")
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.locator("#tabEdit").click()
    page.wait_for_timeout(500)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 4, f"document overflows viewport by {overflow}px"


def test_dem_strip_buttons_all_reachable(strict_page, live_server_url):
    """Every button in the DEM settings tab strip must be fully on-screen.

    Direct regression test for the jsonViewToggleBtn overflow: it previously
    sat at x=1656 in a 1600px viewport (off-screen, unreachable by click).
    """
    page = strict_page
    page.goto(live_server_url, wait_until="networkidle")
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.locator("#tabEdit").click()
    page.wait_for_timeout(500)

    for btn_id in ("settingsHideBtn", "jsonViewToggleBtn"):
        box = page.locator(f"#{btn_id}").bounding_box()
        assert box is not None, f"#{btn_id} not found or not visible"
        assert box["x"] + box["width"] <= 1600 + 1, (
            f"#{btn_id} extends to x={box['x'] + box['width']}, past the 1600px viewport"
        )


def test_city_size_gate_uses_shared_constant(strict_page, live_server_url):
    """The city/building-data size limit must be a single shared constant
    (window.CITY_MAX_DIAG_KM), applied consistently to the manual Load-Cities
    button gate. Regression guard for the 2026-07-19 bug where
    appState.haversineDiagKm was referenced but never assigned, silently
    breaking this gate (and, separately, bulk region loading never included
    city data for any region size at all)."""
    page = strict_page
    page.goto(live_server_url, wait_until="networkidle")
    page.wait_for_function(
        "() => typeof window.haversineDiagKm === 'function'", timeout=10_000
    )

    max_diag = page.evaluate("() => window.CITY_MAX_DIAG_KM")
    assert isinstance(max_diag, (int, float)) and max_diag > 0

    # A region comfortably under the limit.
    small_diag = page.evaluate(
        "() => window.haversineDiagKm(46.02, 45.95, 7.70, 7.62)"
    )
    assert small_diag < max_diag

    page.evaluate(
        """(region) => {
            window.appState.selectedRegion = region;
            window._updateCitiesLoadButton && window._updateCitiesLoadButton(region);
        }""",
        {"name": "SmallGateTest", "north": 46.02, "south": 45.95, "east": 7.70, "west": 7.62},
    )
    assert not page.locator("#loadCityDataBtn").is_disabled(), (
        "Load Cities button disabled for a region under the size limit"
    )

    # A region far over the limit.
    page.evaluate(
        """(region) => {
            window.appState.selectedRegion = region;
            window._updateCitiesLoadButton && window._updateCitiesLoadButton(region);
        }""",
        {"name": "BigGateTest", "north": 47.0, "south": 45.0, "east": 9.0, "west": 6.0},
    )
    assert page.locator("#loadCityDataBtn").is_disabled(), (
        "Load Cities button NOT disabled for a region far over the size limit "
        "— the haversineDiagKm gate is broken again"
    )

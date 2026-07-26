"""Export pipeline e2e tests — DEM load through Extrude preview to STL download.

Runs against STRM2STL_TEST_MODE (deterministic gradient DEM, no network) so it
is reliable offline/in CI. Guards against the class of bug found 2026-07-19:
the settings-only export/preview path silently failing with "Missing DEM data"
because its disk-cache key didn't match the DEM write key (fixed in
export_params.py). Also covers the follow-up feature: contour/label settings
now affect the live 3D preview, not just the downloaded file.
"""

from __future__ import annotations


def _select_region_and_load_dem(page, live_server_url_testmode) -> None:
    page.goto(live_server_url_testmode, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => !!window.appState && typeof window.selectCoordinate === 'function'",
        timeout=10_000,
    )

    region = page.locator("span.coordinate-item-name").first
    region.wait_for(state="visible", timeout=10_000)
    region.click()

    page.locator("#tabEdit").click()
    load_btn = page.locator("#loadDemBtn")
    if not load_btn.is_visible():
        section = page.locator(".collapsible-section", has=page.locator("#loadDemBtn")).first
        section.locator(".collapsible-header").first.click()
    load_btn.wait_for(state="visible", timeout=10_000)

    with page.expect_response(lambda r: "/api/terrain/dem" in r.url, timeout=20_000):
        load_btn.click()

    page.wait_for_function(
        "() => !!(window.appState.lastDemData && window.appState.lastDemData.values?.length)",
        timeout=15_000,
    )


def _open_extrude_export_subtab(page) -> None:
    """Switch to the Extrude workflow tab, then its internal Export sub-tab.

    ModelContainer.vue has its own Fetch/View/Export strip nested inside the
    Extrude view; download buttons and engrave/contour controls live in a
    `v-show="activeTab==='export'"` block that stays hidden until that
    sub-tab is clicked (separate from the outer #tabExtrude click).
    """
    page.locator("#tabExtrude").click()
    page.wait_for_function(
        "() => !!(window.appState && window.appState.generatedModelData)",
        timeout=15_000,
    )
    page.locator(".dem-strip-btn", has_text="Export").click()


def test_extrude_preview_builds_and_exports_stl(strict_page, live_server_url_testmode):
    """Full journey: region -> DEM -> Extrude auto-rebuild -> STL download.

    This is the exact path that broke silently before the export cache-key fix
    (2026-07-19): the DEM loaded fine, but /api/export/preview always missed
    the cache and returned "Missing DEM data", so the Extrude tab never
    produced a model and export was impossible.
    """
    page = strict_page
    _select_region_and_load_dem(page, live_server_url_testmode)
    _open_extrude_export_subtab(page)

    stl_btn = page.locator("#downloadSTLBtn")
    assert not stl_btn.is_disabled(), "model generated but STL export button still disabled"

    with page.expect_download(timeout=30_000) as dl_info:
        stl_btn.click()
    download = dl_info.value
    path = download.path()
    assert path is not None and path.stat().st_size > 0, "STL export produced an empty file"


def test_extrude_tab_export_settings_are_editable(strict_page, live_server_url_testmode):
    """The Extrude tab exposes editable label/contour/puzzle controls, and
    engrave-label + contours now trigger a live preview rebuild (previously
    these only applied to the downloaded file, never the on-screen preview)."""
    page = strict_page
    _select_region_and_load_dem(page, live_server_url_testmode)
    _open_extrude_export_subtab(page)

    for control_id in (
        "exportEngraveLabel", "exportLabelText", "exportContours",
        "exportContourInterval", "exportContourStyle",
        "puzzleEnabled", "splitCols", "splitRows",
    ):
        assert page.locator(f"#{control_id}").count() == 1, f"missing export control #{control_id}"

    # "Engraving & Contours" is a collapsible section that starts collapsed;
    # expand it so the checkboxes are actually interactable.
    engrave_chk = page.locator("#exportEngraveLabel")
    if not engrave_chk.is_visible():
        section = page.locator(".collapsible-section", has=engrave_chk).first
        section.locator(".collapsible-header").first.click()
    engrave_chk.wait_for(state="visible", timeout=5_000)

    # Toggling "Engrave label" and setting contours should re-trigger the
    # preview build (a fresh /api/export/preview call) rather than silently
    # doing nothing until the user downloads the file.
    with page.expect_response(lambda r: "/api/export/preview" in r.url, timeout=15_000):
        engrave_chk.check()

    with page.expect_response(lambda r: "/api/export/preview" in r.url, timeout=15_000):
        page.locator("#exportContours").check()


def test_applied_composite_dem_is_exported_not_the_plain_dem(strict_page, live_server_url_testmode):
    """Guards against the bug found 2026-07-26: applyCompositeToDem() (the new
    Composite DEM panel) mutates appState.lastDemData.values in memory and the
    live 3D preview reads it directly, but the export POST body only carried
    bbox + DEM settings -- resolve_dem_from_cache() then re-read the plain,
    non-composite DEM straight from the server-side disk cache, so the
    exported file silently reverted to unmodified terrain while the on-screen
    preview kept showing the composite. Fixed by having applyCompositeToDem()
    set appState._newCompositeApplied, which _demSettings() (export-handlers.js)
    now checks to ship the composite values inline instead."""
    page = strict_page
    _select_region_and_load_dem(page, live_server_url_testmode)

    # Simulate "apply composite" without depending on OSM/water network calls:
    # overwrite lastDemData with a distinctive array (offset +10000 from the
    # loaded DEM, so it's trivially distinguishable, but with the same shape
    # of variation so the server's flat-DEM guard doesn't reject it) and set
    # the same flag applyCompositeToDem() sets, exactly as that function does.
    page.evaluate(
        """() => {
            const dem = window.appState.lastDemData;
            const composite = Float32Array.from(dem.values, v => v + 10000);
            dem.values = composite;
            dem.min += 10000; dem.max += 10000;
            window.appState.lastDemData = dem;
            window.appState._newCompositeApplied = true;
        }"""
    )

    _open_extrude_export_subtab(page)

    with page.expect_request("**/api/export/start") as req_info:
        page.locator("#downloadSTLBtn").click()
    body = req_info.value.post_data_json

    assert body.get("dem_values"), "composite applied but export request carried no inline dem_values"
    assert body["dem_values"][0] > 9999, (
        "export request's dem_values did not match the applied composite array (got plain DEM instead)"
    )

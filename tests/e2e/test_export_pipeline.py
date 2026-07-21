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

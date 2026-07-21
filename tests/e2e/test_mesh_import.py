"""F-MESHIMPORT e2e — STL/OBJ mesh import, manual registration, apply-to-DEM.

Drives the full user-facing flow through the real UI: select a region, load
the (deterministic, test-mode) DEM, expand the Composite tab's Mesh Import
section, upload a synthetic STL, preview its heightmap, open the manual
side-by-side registration picker, click 3 point pairs, compute the
registration, then apply it to the DEM. All under the strict console-error
gate from conftest.py — any JS error during the flow fails the test.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_binary_stl(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 80)
        fh.write(struct.pack("<I", len(faces)))
        for tri in faces:
            v0, v1, v2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            n = np.cross(v1 - v0, v2 - v0)
            nlen = np.linalg.norm(n)
            n = n / nlen if nlen > 0 else n
            fh.write(struct.pack("<fff", *n))
            fh.write(struct.pack("<fff", *v0))
            fh.write(struct.pack("<fff", *v1))
            fh.write(struct.pack("<fff", *v2))
            fh.write(struct.pack("<H", 0))


@pytest.fixture()
def box_stl(tmp_path) -> Path:
    """A small closed box (12 triangles) — enough for a real ray-cast hit."""
    x1, y1, z1 = 10.0, 10.0, 5.0
    verts = np.array([
        [0, 0, 0], [x1, 0, 0], [x1, y1, 0], [0, y1, 0],
        [0, 0, z1], [x1, 0, z1], [x1, y1, z1], [0, y1, z1],
    ], dtype=np.float32)
    faces = np.array([
        [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
        [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
        [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
    ])
    out = tmp_path / "box.stl"
    _write_binary_stl(out, verts, faces)
    return out


def _open_edit_and_load_dem(page):
    region = page.locator("span.coordinate-item-name").first
    region.wait_for(state="visible", timeout=10_000)
    with page.expect_response(lambda r: "/settings" in r.url, timeout=10_000):
        region.click()

    page.locator("#tabEdit").click()
    load_btn = page.locator("#loadDemBtn")
    if not load_btn.is_visible():
        section = page.locator(".collapsible-section", has=page.locator("#loadDemBtn")).first
        section.locator(".collapsible-header").first.click()
    load_btn.wait_for(state="visible", timeout=10_000)
    with page.expect_response(lambda r: "/api/terrain/dem" in r.url, timeout=15_000) as resp:
        load_btn.click()
    assert resp.value.status == 200
    page.wait_for_function(
        "() => [...document.querySelectorAll('canvas')]"
        ".some(c => c.offsetParent !== null && c.width > 100 && c.height > 100)",
        timeout=10_000,
    )


def _open_mesh_import_section(page):
    composite_tab = page.locator("#demStrip .dem-strip-btn", has_text="Composite")
    composite_tab.wait_for(state="visible", timeout=10_000)
    composite_tab.click()
    mesh_header = page.locator(".collapsible-header", has_text="Mesh Import")
    mesh_header.wait_for(state="visible", timeout=10_000)
    mesh_header.click()


def test_upload_and_preview_heightmap(strict_page, live_server_url_testmode, box_stl):
    """Upload an STL, compute its heightmap, and confirm the preview renders."""
    page = strict_page
    page.goto(live_server_url_testmode, wait_until="domcontentloaded")

    _open_edit_and_load_dem(page)
    _open_mesh_import_section(page)

    file_input = page.locator('input[type="file"][accept*="stl"]')
    with page.expect_response(lambda r: "/api/layers/mesh/upload" in r.url, timeout=15_000) as up:
        file_input.set_input_files(str(box_stl))
    assert up.value.status == 200

    preview_btn = page.locator("#meshComputeHeightmapBtn")
    preview_btn.wait_for(state="visible", timeout=5_000)
    assert preview_btn.get_attribute("disabled") is None, (
        "Preview Heightmap button should enable once a mesh is uploaded"
    )

    with page.expect_response(lambda r: "/heightmap" in r.url, timeout=30_000) as hm:
        preview_btn.click()
    assert hm.value.status == 200

    stats = page.locator("#meshImportStats")
    stats.wait_for(state="visible", timeout=5_000)
    assert "valid" in stats.inner_text()


def test_register_and_apply_to_dem(strict_page, live_server_url_testmode, box_stl):
    """Full flow: upload -> heightmap -> manual 3-point registration -> apply to DEM."""
    page = strict_page
    # Default Playwright viewport (1280x720) is too small for the registration
    # modal's two side-by-side canvases to lay out predictably — match the
    # 1600x1000 viewport other e2e tests already use for this reason
    # (test_ui_correctness.py).
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.goto(live_server_url_testmode, wait_until="domcontentloaded")

    _open_edit_and_load_dem(page)
    _open_mesh_import_section(page)

    file_input = page.locator('input[type="file"][accept*="stl"]')
    with page.expect_response(lambda r: "/api/layers/mesh/upload" in r.url, timeout=15_000):
        file_input.set_input_files(str(box_stl))

    preview_btn = page.locator("#meshComputeHeightmapBtn")
    preview_btn.wait_for(state="visible", timeout=5_000)
    with page.expect_response(lambda r: "/heightmap" in r.url, timeout=30_000):
        preview_btn.click()

    register_btn = page.locator("#meshRegisterBtn")
    register_btn.wait_for(state="visible", timeout=5_000)
    assert register_btn.get_attribute("disabled") is None, (
        "Register button should enable once a heightmap has been computed"
    )
    register_btn.click()

    modal = page.locator("#meshRegistrationModal")
    modal.wait_for(state="visible", timeout=5_000)

    # Click 3 matched point pairs: (ref, mesh) x3, at deterministic fractional
    # positions on each canvas so the affine fit is well-conditioned.
    ref_canvas = page.locator("#meshRegRefCanvas")
    mesh_canvas = page.locator("#meshRegMeshCanvas")
    ref_box = ref_canvas.bounding_box()
    mesh_box = mesh_canvas.bounding_box()
    assert ref_box and mesh_box, "registration canvases did not render"

    for fx, fy in [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7)]:
        page.mouse.click(ref_box["x"] + ref_box["width"] * fx, ref_box["y"] + ref_box["height"] * fy)
        page.wait_for_timeout(100)
        page.mouse.click(mesh_box["x"] + mesh_box["width"] * fx, mesh_box["y"] + mesh_box["height"] * fy)
        page.wait_for_timeout(100)

    pair_rows = page.locator("#meshRegPairList .mesh-reg-pair-row")
    assert pair_rows.count() >= 3, (
        f"Expected >=3 point-pair rows after 3 click pairs, got {pair_rows.count()}"
    )

    compute_btn = page.locator("#meshRegComputeBtn")
    assert compute_btn.get_attribute("disabled") is None, (
        "Compute Registration should enable after 3 point pairs are placed"
    )
    with page.expect_response(lambda r: "/register" in r.url, timeout=15_000) as reg:
        compute_btn.click()
    assert reg.value.status == 200

    # Modal closes itself on successful registration (mesh-registration.js).
    modal.wait_for(state="hidden", timeout=5_000)

    apply_btn = page.locator("#meshApplyToDemBtn")
    apply_btn.wait_for(state="visible", timeout=5_000)
    assert apply_btn.get_attribute("disabled") is None, (
        "Apply to DEM should enable once registration succeeds"
    )

    dem_range_before = page.evaluate("() => [window.appState.lastDemData.min, window.appState.lastDemData.max]")
    apply_btn.click()
    # applyMeshToDem() (mesh-layer.js) widens dem.min/max via Math.min/max
    # against the registered mesh's elevation range — a reliable signal that
    # doesn't depend on which exact pixels the mesh's footprint covered.
    page.wait_for_function(
        "(before) => { const d = window.appState.lastDemData; "
        "return d.min !== before[0] || d.max !== before[1]; }",
        arg=dem_range_before,
        timeout=5_000,
    )
    toast = page.locator(".toast", has_text="Mesh applied to DEM")
    toast.wait_for(state="visible", timeout=5_000)


def test_mesh_layer_toggle_in_layer_view(strict_page, live_server_url_testmode, box_stl):
    """The MeshImport layer button appears in the View tab's layer selector."""
    page = strict_page
    page.goto(live_server_url_testmode, wait_until="domcontentloaded")

    _open_edit_and_load_dem(page)

    view_tab = page.locator("#demStrip .dem-strip-btn", has_text="View")
    view_tab.click()

    # The "🗺️ Layers" section (LayerViewSection.vue) starts collapsed.
    # "Fetch Layers" (a different section) also matches a loose "Layers"
    # text search, so anchor on the h4 title exactly instead.
    layers_header = page.locator(".collapsible-header").filter(has=page.locator("h4", has_text="🗺️ Layers"))
    layers_header.wait_for(state="visible", timeout=10_000)
    layers_header.click()

    mesh_layer_btn = page.locator('.layer-mode-btn[data-mode="MeshImport"]')
    mesh_layer_btn.wait_for(state="visible", timeout=10_000)
    assert "Mesh" in mesh_layer_btn.inner_text()


# ── Auto mode (geocode + automatic OSM registration) ─────────────────────
#
# Real network (Nominatim geocoding + OSM Overpass building fetch), 30-90s
# even with a warm cache, hundreds of seconds cold. Opt-in only — not run
# as part of the default `pytest tests/e2e/` invocation. Run explicitly:
#   STRM2STL_RUN_SLOW_NETWORK_TESTS=1 pytest tests/e2e/test_mesh_import.py -k auto -v
#
# Requires a real "Miami, FL"-named file in the configured mesh library dir
# (config.MICROPOLITAN_STL_DIR) — skips cleanly if that's not present, since
# CI/most dev machines won't have the (non-repo, licensed) micropolitan STL
# set checked out.

import os

_RUN_SLOW = os.environ.get("STRM2STL_RUN_SLOW_NETWORK_TESTS") == "1"


def _miami_library_rel_path() -> str | None:
    """Find a Miami STL in the configured mesh library, or None."""
    try:
        from app.server.config import MICROPOLITAN_STL_DIR
    except ImportError:
        return None
    if not MICROPOLITAN_STL_DIR.is_dir():
        return None
    for p in MICROPOLITAN_STL_DIR.rglob("*.stl"):
        if "miami" in p.parent.name.lower():
            return p.relative_to(MICROPOLITAN_STL_DIR).as_posix()
    return None


@pytest.mark.skipif(not _RUN_SLOW, reason="slow (30-90s+) + requires real network access; opt in via STRM2STL_RUN_SLOW_NETWORK_TESTS=1")
def test_auto_register_geocodes_and_opens_prefilled_picker(strict_page, live_server_url):
    """Full auto-mode flow against a real library file: click Auto, wait for
    geocode + OSM registration, and confirm the manual picker opens
    pre-populated (not a silent trust of the automatic result).

    Uses live_server_url (real network mode), not live_server_url_testmode —
    auto mode needs real DEM/OSM data to be meaningful.
    """
    rel_path = _miami_library_rel_path()
    if rel_path is None:
        pytest.skip("no Miami STL found in the configured mesh library "
                    "(config.MICROPOLITAN_STL_DIR) — not present on this machine")

    page = strict_page
    page.set_viewport_size({"width": 1600, "height": 1000})
    reqs = []
    page.on("request", lambda r: reqs.append((r.method, r.url)) if "layers/mesh" in r.url else None)
    resps = []
    page.on("response", lambda r: resps.append((r.status, r.url)) if "layers/mesh" in r.url else None)
    page.goto(live_server_url, wait_until="domcontentloaded")

    _open_edit_and_load_dem(page)
    _open_mesh_import_section(page)

    page.locator("button", has_text="Browse Library").click()
    file_row = page.locator(".mesh-import-file-row", has_text="Miami").first
    file_row.wait_for(state="visible", timeout=10_000)
    file_row.click()

    auto_btn = page.locator("#meshAutoRegisterBtn")
    auto_btn.wait_for(state="visible", timeout=5_000)
    with page.expect_response(lambda r: "auto-register" in r.url, timeout=180_000) as auto_resp:
        auto_btn.click()
    assert auto_resp.value.status == 200
    body = auto_resp.value.json()
    assert body["status"] == "ok", f"auto-register did not succeed: {body}"
    assert body["city_name"]
    assert body["bbox"]
    assert body["region"]["name"]

    # computeMeshHeightmap + openMeshRegistrationModal run after the
    # auto-register response resolves — give the async chain room to finish
    # (loadDEM() here hits a real DEM source, not the instant test-mode
    # gradient, so this can take a while on top of the auto-register call).
    # A cold cache (live_server_url gives each session its own fresh
    # STRM2STL_CACHE dir — see conftest.py) means both the auto-register
    # call's internal STL->heightmap conversion AND this component's own
    # separate /heightmap call each pay a full ray-cast against the real
    # ~940K-face Miami mesh (~35s each measured standalone), on top of the
    # OSM building fetch (~50s+ cold) — comfortably over 120s combined.
    modal = page.locator("#meshRegistrationModal")
    try:
        modal.wait_for(state="visible", timeout=240_000)
    except Exception:
        mesh_state = page.evaluate("() => window.appState?.meshImport")
        dem_bbox = page.evaluate("() => window.appState?.currentDemBbox")
        dem_dims = page.evaluate(
            "() => window.appState?.lastDemData ? "
            "[window.appState.lastDemData.width, window.appState.lastDemData.height] : null")
        raise AssertionError(
            f"registration modal never opened — meshImport={mesh_state}, "
            f"currentDemBbox={dem_bbox}, demDims={dem_dims}, "
            f"mesh-related requests={reqs}, responses={resps}")

    ref_canvas = page.locator("#meshRegRefCanvas")
    mesh_canvas = page.locator("#meshRegMeshCanvas")
    assert ref_canvas.bounding_box() is not None
    assert mesh_canvas.bounding_box() is not None

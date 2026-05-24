"""Tests for city2stl.skyline_cv.html_report (F-SKY15 Phase A).

Covers the pure rendering functions and the top-level
``write_region_report`` flow with a synthetic ``SeedViewRegistration``.
We don't exercise the matplotlib PNG path here — that's implicitly
tested by the existing PDF tests, and the HTML pure renderer handles a
missing PNG by emitting a placeholder.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from city2stl.skyline_cv.html_report import (
    render_region_index,
    render_seed_page,
)


@pytest.fixture
def fake_sv():
    """A minimal SeedViewRegistration-shaped object for rendering tests.

    We deliberately don't import the real dataclass here — the renderer
    only reads attributes, so a simple namespace is enough and keeps the
    test light. (Pure-function tests by design.)
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        seed_name="5",
        seed_lat=10.4069,
        seed_lon=-75.5559,
        heading=321.2,
        fov=75.0,
        registration_score=0.82,
        best_offset=-1.5,
        estimates_count=8,
        matched_segments=[{}] * 8,
        is_aerial=False,
        iou=0.61,
        is_negative=False,
        building_mask=None,
        pano_osm_iou=0.78,
        pano_osm_n_keypoints=42,
        pano_projected_coastline=[(-75.555, 10.4068)] * 12,
        image=np.zeros((720, 1280, 3), dtype=np.uint8),
    )


class TestRenderSeedPage:
    def test_minimum_output_shape(self, fake_sv):
        html = render_seed_page(fake_sv, "cartagena", minimap_rel_path=None)
        assert html.startswith("<!doctype html>")
        assert "</html>" in html.strip().splitlines()[-1]
        # Title and seed name appear
        assert "Seed 5" in html
        assert "cartagena" in html

    def test_fsky13_iou_visible(self, fake_sv):
        html = render_seed_page(fake_sv, "cartagena", minimap_rel_path=None)
        # IoU score and keypoint count both surface in the summary
        assert "0.78" in html
        assert "42 keypoints" in html

    def test_no_pano_iou_omits_row(self, fake_sv):
        fake_sv.pano_osm_iou = None
        fake_sv.pano_osm_n_keypoints = None
        html = render_seed_page(fake_sv, "cartagena", minimap_rel_path=None)
        # Row label should not appear when the diagnostic is unavailable
        assert "pano↔OSM IoU" not in html

    def test_minimap_link_renders_when_provided(self, fake_sv):
        html = render_seed_page(
            fake_sv, "cartagena", minimap_rel_path="assets/minimap/5.png"
        )
        assert 'src="assets/minimap/5.png"' in html
        assert "Minimap unavailable" not in html

    def test_minimap_placeholder_when_missing(self, fake_sv):
        html = render_seed_page(fake_sv, "cartagena", minimap_rel_path=None)
        assert "Minimap unavailable" in html

    def test_estimates_table_renders_depth_columns(self, fake_sv):
        from types import SimpleNamespace
        ests = [
            SimpleNamespace(
                feature_id="b0142",
                name="Torre del Reloj",
                view_name="v3",
                forward_m=180.5,
                estimated_height_m=52.0,
                depth_height_m=48.0,
                depth_disagreement=False,
                confidence=0.91,
            ),
            SimpleNamespace(
                feature_id="b0150",
                name="",
                view_name="v4",
                forward_m=210.0,
                estimated_height_m=72.0,
                depth_height_m=20.0,
                depth_disagreement=True,
                confidence=0.62,
            ),
        ]
        html = render_seed_page(fake_sv, "cartagena", None, estimates=ests)
        assert "b0142" in html
        assert "Torre del Reloj" in html
        # Depth heights appear with their unit
        assert "48.0 m" in html
        assert "20.0 m" in html
        # Disagreement row gets the "disagree" CSS class
        assert 'class="disagree"' in html

    def test_depth_dash_when_unavailable(self, fake_sv):
        from types import SimpleNamespace
        ests = [SimpleNamespace(
            feature_id="b0001", name="", view_name="v1",
            forward_m=100.0, estimated_height_m=30.0,
            depth_height_m=None, depth_disagreement=None,
            confidence=0.5,
        )]
        html = render_seed_page(fake_sv, "cartagena", None, estimates=ests)
        # The depth column shows the em-dash placeholder
        assert "—" in html

    def test_html_escaping(self):
        from types import SimpleNamespace
        sv = SimpleNamespace(
            seed_name="<script>alert(1)</script>",
            seed_lat=0.0, seed_lon=0.0, heading=0.0, fov=75.0,
            registration_score=0.0, best_offset=0.0, estimates_count=0,
            matched_segments=[], is_aerial=False, iou=0.0,
            is_negative=False, building_mask=None,
            pano_osm_iou=None, pano_osm_n_keypoints=None,
            pano_projected_coastline=None, image=None,
        )
        html = render_seed_page(sv, "region", None)
        # Raw <script> must not appear in the rendered HTML
        assert "<script>alert" not in html
        # But the escaped form should
        assert "&lt;script&gt;" in html


class TestRenderRegionIndex:
    def test_lists_each_seed(self, fake_sv):
        from types import SimpleNamespace
        sv2 = SimpleNamespace(**vars(fake_sv))
        sv2.seed_name = "6"
        sv2.pano_osm_iou = 0.42
        html = render_region_index("cartagena", [fake_sv, sv2])
        # Each seed has its own row + link
        assert "seed_5.html" in html
        assert "seed_6.html" in html

    def test_dedupes_views_to_seeds(self, fake_sv):
        # Three views of the same seed → one row
        html = render_region_index("cartagena", [fake_sv, fake_sv, fake_sv])
        # Count of href occurrences for this seed
        assert html.count('href="seed_5.html"') == 1

    def test_building_count_in_header(self, fake_sv):
        html = render_region_index(
            "cartagena", [fake_sv],
            building_heights=[{"feature_id": "b0001"}] * 17,
        )
        assert "17" in html

    def test_handles_missing_iou(self, fake_sv):
        fake_sv.pano_osm_iou = None
        html = render_region_index("cartagena", [fake_sv])
        # The em-dash placeholder appears in the IoU column
        assert "—" in html


class TestWriteRegionReport:
    def test_writes_index_and_seed_pages(self, fake_sv, tmp_path):
        from city2stl.skyline_cv.html_report import write_region_report
        # Two distinct seeds
        from types import SimpleNamespace
        sv2 = SimpleNamespace(**vars(fake_sv))
        sv2.seed_name = "6"

        write_region_report(
            tmp_path,
            region_name="cartagena",
            seed_views=[fake_sv, sv2],
            osm_data={},  # empty → minimap render will still run but no overlays
            buildings_by_id={},
        )

        assert (tmp_path / "index.html").exists()
        assert (tmp_path / "seed_5.html").exists()
        assert (tmp_path / "seed_6.html").exists()
        # PNGs may or may not exist depending on matplotlib; the HTML
        # should still be valid either way.
        idx = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "cartagena" in idx

    def test_empty_seed_views_creates_index_only(self, tmp_path):
        from city2stl.skyline_cv.html_report import write_region_report
        write_region_report(
            tmp_path, region_name="empty",
            seed_views=[], osm_data={},
        )
        assert (tmp_path / "index.html").exists()
        # No seed_*.html files
        seed_files = list(tmp_path.glob("seed_*.html"))
        assert seed_files == []

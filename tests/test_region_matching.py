"""
Tests for bbox-overlap region matching / create-if-missing
(routers/regions.py: find_overlapping_region, find_or_create_region_for_bbox).

Added for F-MESHIMPORT auto-register — reuses an existing saved region when
its bbox substantially overlaps the auto-detected mesh bbox, otherwise
creates a new one rather than silently overwriting an unrelated region with
the same generated name.

Uses tmp_data_dir's pre-seeded "TestRegion" (N=40.0, S=39.9, E=-75.1, W=-75.2).
"""
from __future__ import annotations

from app.server.routers.regions import (
    _bbox_iou,
    find_overlapping_region,
    find_or_create_region_for_bbox,
)

_TEST_REGION_BBOX = {"north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2}


class TestBboxIou:
    def test_identical_bboxes_iou_1(self):
        assert _bbox_iou(_TEST_REGION_BBOX, _TEST_REGION_BBOX) == 1.0

    def test_disjoint_bboxes_iou_0(self):
        far = {"north": 10.0, "south": 9.0, "east": 10.0, "west": 9.0}
        assert _bbox_iou(_TEST_REGION_BBOX, far) == 0.0

    def test_partial_overlap_between_0_and_1(self):
        shifted = {"north": 40.05, "south": 39.95, "east": -75.05, "west": -75.15}
        iou = _bbox_iou(_TEST_REGION_BBOX, shifted)
        assert 0.0 < iou < 1.0


class TestFindOverlappingRegion:
    def test_finds_region_with_high_overlap(self, client, tmp_data_dir):
        # Nearly identical bbox to TestRegion -> should match with high IoU.
        bbox = {"north": 40.001, "south": 39.899, "east": -75.099, "west": -75.201}
        match = find_overlapping_region(bbox, min_iou=0.5)
        assert match is not None
        assert match["name"] == "TestRegion"
        assert match["iou"] > 0.9

    def test_no_match_for_distant_bbox(self, client, tmp_data_dir):
        bbox = {"north": 10.0, "south": 9.9, "east": 10.0, "west": 9.9}
        match = find_overlapping_region(bbox, min_iou=0.5)
        assert match is None

    def test_no_match_when_overlap_below_threshold(self, client, tmp_data_dir):
        # Overlaps TestRegion only slightly at a corner.
        bbox = {"north": 39.91, "south": 39.81, "east": -75.11, "west": -75.21}
        match = find_overlapping_region(bbox, min_iou=0.8)
        assert match is None


class TestFindOrCreateRegionForBbox:
    def test_reuses_overlapping_region(self, client, tmp_data_dir):
        bbox = {"north": 40.001, "south": 39.899, "east": -75.099, "west": -75.201}
        result = find_or_create_region_for_bbox(bbox, label_hint="Should Not Be Used")
        assert result["created"] is False
        assert result["name"] == "TestRegion"
        assert result["iou"] > 0.9

        # No new region was created.
        r = client.get("/api/regions")
        names = [reg["name"] for reg in r.json()["regions"]]
        assert names.count("TestRegion") == 1

    def test_creates_new_region_for_distant_bbox(self, client, tmp_data_dir):
        bbox = {"north": 25.86, "south": 25.71, "east": -80.14, "west": -80.32}
        result = find_or_create_region_for_bbox(bbox, label_hint="Miami, FL")
        assert result["created"] is True
        assert result["name"] == "Miami, FL"

        r = client.get("/api/regions")
        names = [reg["name"] for reg in r.json()["regions"]]
        assert "Miami, FL" in names

    def test_deduplicates_name_collision(self, client, tmp_data_dir):
        # Create a region literally named "TestRegion" via a bbox that does
        # NOT overlap the real TestRegion — the name collides but the bbox
        # doesn't, so find_or_create must not silently overwrite it.
        far_bbox = {"north": 10.0, "south": 9.9, "east": 10.0, "west": 9.9}
        result = find_or_create_region_for_bbox(far_bbox, label_hint="TestRegion")
        assert result["created"] is True
        assert result["name"] == "TestRegion (2)"

        r = client.get("/api/regions")
        names = [reg["name"] for reg in r.json()["regions"]]
        assert "TestRegion" in names
        assert "TestRegion (2)" in names

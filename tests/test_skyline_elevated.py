"""F-DET6 in the region report: the adapter from footprint measurements to pipeline records."""

import numpy as np
import pytest
from shapely.geometry import Polygon

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline._core.height import aggregate_building_heights
from city2stl.skyline._core.types import BuildingRecord
from city2stl.skyline._pano import elevated as el
from city2stl.skyline.region_types import SkylinePoint

H, W = 100, 360


def _pano(offset_frame=0.0):
    frame = (np.arange(W) * 1.0 + offset_frame) % 360.0         # 1 deg per column
    labels = np.full((H, W), fd.SKY_CLASS, np.int16)
    labels[60:, :] = 21                                            # water
    return fd.Pano("seed_9", 10.4, -75.55, np.zeros((H, W, 3), np.uint8), labels, frame, 57.3, 0.0)


def _m(i, x0, x1, h, dist, base=True, frac=1.0, name=""):
    return fd.Measured(i, name, x0, x1, 40.0, 60.0, 61.0, dist, h, x1 - x0 + 1, base, None, frac, "sky")


def test_pano_result_centres_north_and_carries_one_box_per_footprint():
    pano = _pano()
    pose = fd.PanoPose(30.0, 80.0, 0.0, 0.3, W)                    # bearing = frame + 30
    ms = [_m(0, 10, 20, 120.0, 400.0, name="tower"), _m(1, 200, 210, 40.0, 900.0)]
    res = el.pano_result(SkylinePoint("seed_9", 10.4, -75.55, 0.0, "seed", 1.0), pano, pose, ms,
                         ["way/1", "way/2"], depth=np.zeros((H, W)))
    assert res.headings_per_col[W // 2] == 0.0                    # north mid-strip
    seg = next(s for s in res.matched_segments if s["matched_projection"]["feature_id"] == "way/1")
    assert seg["true_bearing_deg"] == 45.0                         # column 15: frame 15 + 30
    assert seg["height_m"] == 120.0 and seg["height_src"] == "footprint"
    assert res.headings_per_col[seg["mid_x"]] == seg["true_bearing_deg"]
    assert res.n_matched == 2 and res.geom_K == 80.0 * 57.3


def test_estimates_drop_a_footprint_the_seeds_disagree_on():
    pano = _pano()
    pose = fd.PanoPose(0.0, 80.0, 0.0, 0.3, W)
    pf = fd.PositionFit(0.0, 0.0, pose, 0.3, 0.3, 0.4, 0.4, "recorded")
    seed = SkylinePoint("seed_9", 10.4, -75.55, 0.0, "seed", 1.0)
    near = el.ElevatedSeed("seed_1", pf, [_m(0, 10, 20, 26.0, 600.0)], ["way/7"],
                           el.pano_result(seed, pano, pose, [_m(0, 10, 20, 26.0, 600.0)], ["way/7"], None))
    far_m = _m(0, 10, 20, 111.0, 1116.0)                           # read the tower behind
    far = el.ElevatedSeed("seed_5", pf, [far_m], ["way/7"],
                          el.pano_result(seed, pano, pose, [far_m], ["way/7"], None))
    assert el.elevated_estimates([near, far]) == []                # disputed: no drone height
    est = el.elevated_estimates([near])                            # one seed: kept
    assert est[0].view_name == "seed_1_015" and est[0].estimated_height_m == 26.0
    agg = aggregate_building_heights(est)
    assert agg[0]["feature_id"] == "way/7" and agg[0]["effective_height_m"] == 26.0


def test_footprints_from_records_keep_ids_and_tags():
    poly = Polygon([(-75.55, 10.40), (-75.549, 10.40), (-75.549, 10.401), (-75.55, 10.401)])
    rec = BuildingRecord("way/42", "Ravello", poly, 10.4005, -75.5495, 160.0, "osm_tag", 1200.0)
    fps, fids = el.footprints_from_records([rec, BuildingRecord("node/1", "", None, 0, 0, None, "", 0)])
    assert fids == ["way/42"] and fps[0].osm_height_m == 160.0 and fps[0].ring.shape == (5, 2)


def test_site_lists_the_cartagena_drone_seeds():
    from city2stl.skyline.region_data import _load_site_elevated_seeds

    assert _load_site_elevated_seeds("cartagena") == {"seed_1", "seed_4", "seed_5"}
    assert _load_site_elevated_seeds("miami") == set()


def test_chunked_upsample_gives_the_full_argmax_labels():
    """The full-resolution upsample of all 150 class scores needed 6.6 GB for one Commons
    photo; the chunked running maximum must give exactly the same labels, ties included."""
    torch = pytest.importorskip("torch")              # the cloud venv has no torch
    import torch.nn.functional as F

    from city2stl.skyline._core.segmentation import _upsampled_labels

    g = torch.Generator().manual_seed(0)
    logits = torch.randn(1, 150, 17, 23, generator=g)
    logits[0, 37] = logits[0, 5]                       # exact ties: the first class must win
    full = F.interpolate(logits, size=(70, 90), mode="bilinear", align_corners=False)[0].argmax(0)
    got = _upsampled_labels(logits, 70, 90)
    assert got.dtype == np.uint8 and got.shape == (70, 90)
    assert np.array_equal(got, full.numpy())


def test_only_sky_topped_base_visible_drone_readings_are_trusted():
    from types import SimpleNamespace

    from city2stl.skyline._pano.elevated import trusted

    assert trusted(SimpleNamespace(top_edge="sky", base_visible=True))
    assert not trusted(SimpleNamespace(top_edge="depth", base_visible=True))
    assert not trusted(SimpleNamespace(top_edge="sky", base_visible=False))

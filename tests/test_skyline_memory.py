"""T21 (compact pixel storage) and T22 (report hygiene) for city2stl.skyline.

Synthetic data only: no network, GPU or Street View.
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from city2stl.skyline._region_render._draw import (  # noqa: E402
    _registration_overlay,
    overlay_spec,
)
from city2stl.skyline._report_plots import _pano_plots, _view_plots  # noqa: E402
from city2stl.skyline._report_plots._plot_utils import colormap_u8  # noqa: E402
from city2stl.skyline.region_types import (  # noqa: E402
    SeedViewRegistration,
    StitchedPanoResult,
    pack_mask,
    unpack_mask,
)

RNG = np.random.default_rng(7)
H, W = 120, 200


def _frame():
    return RNG.integers(0, 256, (H, W, 3), dtype=np.uint8)


def _mask():
    return RNG.random((H, W)) > 0.6


def _segs():
    return [
        {"x_left": 10, "x_right": 60, "top_y": 20, "base_y": 90, "seed_index": 1, "extra": [1]},
        {"x_left": 80, "x_right": 140, "top_y": 30, "base_y": 100, "seed_index": 2},
    ]


def _reg():
    return {"contour": np.linspace(40, 60, W),
            "projections": [{"x_px": 35.2, "other": 1}, {"x_px": 150}]}


def _row(raw, segs, reg, masks):
    return SeedViewRegistration(
        seed_name="s", seed_lat=1.0, seed_lon=2.0, heading=0.0, fov=80.0, registration_score=0.5,
        best_offset=0.0, estimates_count=1, image=None, overlay_spec=overlay_spec(reg, segs),
        matched_segments=segs, building_mask=masks[0], sky_mask=masks[1], water_mask=masks[2],
        vegetation_mask=masks[3], raw_image=raw)


class TestPackMask:
    @pytest.mark.parametrize("dtype", [np.bool_, np.uint8, np.int32])
    def test_roundtrip(self, dtype):
        m = (RNG.random((37, 51)) > 0.5).astype(dtype)
        packed = pack_mask(m)
        assert isinstance(packed, tuple) and len(packed[0]) < m.size
        out = unpack_mask(packed)
        assert out.dtype == m.dtype and out.shape == m.shape and np.array_equal(out, m)
        assert out.flags.writeable

    def test_non_binary_is_kept_unpacked(self):
        m = np.array([[0, 255], [3, 0]], dtype=np.uint8)
        assert unpack_mask(pack_mask(m)) is m

    def test_none_and_empty(self):
        assert pack_mask(None) is None and unpack_mask(None) is None
        e = np.zeros((0, 4), dtype=bool)
        assert unpack_mask(pack_mask(e)) is e


class TestSeedViewRegistration:
    def test_attribute_access_unchanged_and_smaller(self):
        raw, segs, reg = _frame(), _segs(), _reg()
        masks = [_mask() for _ in range(4)]
        sv = _row(raw, segs, reg, masks)
        assert np.array_equal(sv.raw_image, raw) and sv.raw_image.dtype == raw.dtype
        got_masks = (sv.building_mask, sv.sky_mask, sv.water_mask, sv.vegetation_mask)
        for got, want in zip(got_masks, masks, strict=True):
            assert got.dtype == want.dtype and got.shape == want.shape and np.array_equal(got, want)
        want_img = _registration_overlay(raw, reg, matched_segments=segs)
        assert np.array_equal(sv.image, want_img)
        before = want_img.nbytes + raw.nbytes + sum(m.nbytes for m in masks)
        print(f"stored bytes: before {before}, after {sv.stored_bytes}")
        assert sv.stored_bytes < before * 0.5

    def test_overlay_ignores_later_segment_edits(self):
        raw, segs, reg = _frame(), _segs(), _reg()
        sv = _row(raw, segs, reg, [None] * 4)
        want = _registration_overlay(raw, reg, matched_segments=_segs())
        segs[0]["seed_index"] = 9
        segs[1]["x_left"] = 1
        assert np.array_equal(sv.image, want)

    def test_set_overlay_spec_redraws_current_matches(self):
        raw, segs, reg = _frame(), _segs(), _reg()
        sv = _row(raw, segs, reg, [None] * 4)
        segs[0]["seed_index"] = 5
        sv.set_overlay_spec(overlay_spec({}, segs))
        assert np.array_equal(sv.image, _registration_overlay(raw, {}, matched_segments=segs))

    def test_explicit_image_and_shared_raw(self):
        img = _frame()
        sv = SeedViewRegistration("s", 0, 0, 0, 80, 0, 0, 0, image=img, matched_segments=[])
        assert sv.image is img and sv.raw_image is None and sv.building_mask is None
        sv2 = SeedViewRegistration("s", 0, 0, 0, 80, 0, 0, 0, image=img, matched_segments=[],
                                   raw_image=img)
        assert np.array_equal(sv2.image, img) and np.array_equal(sv2.raw_image, img)
        assert sv2.stored_bytes < img.nbytes * 1.1      # one copy, not two (noise does not compress)

    def test_non_uint8_raw_is_kept(self):
        f = RNG.random((8, 9, 3)).astype(np.float32)
        sv = SeedViewRegistration("s", 0, 0, 0, 80, 0, 0, 0, image=None, matched_segments=[],
                                  raw_image=f)
        assert np.array_equal(sv.raw_image, f) and sv.raw_image.dtype == np.float32

    def test_mask_setter_via_object_setattr(self):
        sv = _row(_frame(), _segs(), _reg(), [None] * 4)
        m = _mask()
        object.__setattr__(sv, "building_mask", m)
        assert np.array_equal(sv.building_mask, m)


class TestStitchedPano:
    def test_masks_packed_sky_dropped(self):
        b, w, v, sky = _mask(), _mask(), _mask(), _mask()
        depth = RNG.random((H, W)).astype(np.float32)
        p = StitchedPanoResult("s", 0, 0, _frame(), None, [], 0, 0, 0, pano_building_mask=b,
                               pano_water_mask=w, pano_sky_mask=sky, pano_vegetation_mask=v,
                               pano_depth=depth)
        assert p.pano_sky_mask is None
        assert np.array_equal(p.pano_building_mask, b) and np.array_equal(p.pano_water_mask, w)
        assert np.array_equal(p.pano_vegetation_mask, v)
        assert p.pano_depth is depth
        assert p.stored_bytes < (b.nbytes + w.nbytes + v.nbytes) / 4

    def test_unset_masks_are_none(self):
        p = StitchedPanoResult("s", 0, 0, _frame(), None, [], 0, 0, 0)
        assert p.pano_building_mask is None and p.pano_water_mask is None


class TestRenderedOutputUnchanged:
    def test_mask_png_and_view_image_bytes(self, tmp_path):
        raw, segs, reg = _frame(), _segs(), _reg()
        masks = [_mask() for _ in range(4)]
        sv = _row(raw, segs, reg, masks)
        plain = SimpleNamespace(
            seed_name="s", raw_image=raw,
            image=_registration_overlay(raw, reg, matched_segments=segs),
            building_mask=masks[0], sky_mask=masks[1], water_mask=masks[2],
            vegetation_mask=masks[3])
        for fn in (_view_plots._render_view_mask_png, _view_plots._save_view_image_png):
            a, b = tmp_path / "a.png", tmp_path / "b.png"
            assert fn(a, sv) and fn(b, plain)
            assert a.read_bytes() == b.read_bytes(), fn.__name__

    def test_pano_overlay_png_matches_with_packed_masks(self, tmp_path):
        pano = _frame()
        b, w, v = _mask(), _mask(), _mask()
        packed = StitchedPanoResult("s", 0, 0, pano, None, [], 0, 0, 0, pano_building_mask=b,
                                    pano_water_mask=w, pano_vegetation_mask=v)
        plain = SimpleNamespace(pano_building_mask=b, pano_water_mask=w, pano_vegetation_mask=v,
                                pano_instances=None)
        a, c = tmp_path / "a.png", tmp_path / "c.png"
        fn = _pano_plots._render_pano_segformer_overlay_png
        assert fn(a, pano, [], 80.0, pano_result=packed)
        assert fn(c, pano, [], 80.0, pano_result=plain)
        assert a.read_bytes() == c.read_bytes()


class TestColormapLut:
    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    @pytest.mark.parametrize("name", ["turbo", "viridis"])
    def test_equals_direct_colormap(self, dtype, name):
        cmap = plt.get_cmap(name)
        x = RNG.random((300, 257)).astype(dtype)
        x[0, :4] = [0.0, 1.0, 0.5, 255 / 256]
        old = (cmap(x) * 255).astype(np.uint8)[..., :3]
        new = colormap_u8(cmap, x)
        assert new.dtype == np.uint8 and new.shape == old.shape
        assert int(np.abs(new.astype(int) - old.astype(int)).max()) == 0

    def test_out_of_range_and_nan(self):
        cmap = plt.get_cmap("turbo")
        x = np.array([[-2.0, -0.5, np.nan, 1.5, 1.0]], dtype=np.float32)
        old = (cmap(x) * 255).astype(np.uint8)[..., :3]
        assert np.array_equal(colormap_u8(cmap, x), old)


class TestFiguresClosed:
    @pytest.fixture(autouse=True)
    def _boom(self, monkeypatch):
        plt.close("all")

        def _raise(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr("matplotlib.figure.Figure.savefig", _raise)
        yield
        plt.close("all")

    def _pano(self):
        h, w = 40, 90
        mask = np.zeros((h, w), bool)
        mask[10:30, 20:60] = True
        seg = {"x_left": 20, "x_right": 60, "peak_x": 40, "mid_x": 40, "top_y": 10, "base_y": 30,
               "true_bearing_deg": 120.0, "seed_index": 1,
               "matched_projection": {"feature_id": "b1", "forward_m": 100.0,
                                      "distance_m": 100.0, "x_px": 40.0}}
        return StitchedPanoResult(
            "s", 10.0, 20.0, np.zeros((h, w, 3), np.uint8), (5, 35), [seg], 1, 1, 1,
            headings_per_col=np.linspace(0, 359, w), pano_building_mask=mask,
            pano_depth=RNG.random((h, w)).astype(np.float32))

    def test_polar_plots(self, tmp_path):
        sv = SimpleNamespace(seed_lat=10.0, seed_lon=20.0, seed_name="s")
        with pytest.raises(RuntimeError):
            _pano_plots._render_pano_heights_polar_png(tmp_path / "a.png", sv, {}, None)
        assert plt.get_fignums() == []
        with pytest.raises(RuntimeError):
            _pano_plots._render_pano_minimap_polar_png(tmp_path / "b.png", sv, {}, {}, None)
        assert plt.get_fignums() == []

    def test_bearing_scan_and_reconstruction(self, tmp_path):
        pr = self._pano()
        assert _pano_plots._render_pano_bearing_scan_png(tmp_path / "c.png", pr, {}) is False
        assert plt.get_fignums() == []
        assert _pano_plots._render_pano_reconstruction_png(tmp_path / "d.png", pr, {}, {}) is False
        assert plt.get_fignums() == []

    def test_view_reconstruction(self, tmp_path, monkeypatch):
        monkeypatch.setattr("city2stl.skyline.depth_estimation.predict_pano_depth",
                            lambda img: np.full(img.shape[:2], 0.5, np.float32))
        seg = {**_segs()[0], "peak_x": 40, "matched_projection": {
            "feature_id": "b1", "forward_m": 100.0, "distance_m": 100.0, "x_px": 40.0}}
        sv = _row(_frame(), [seg], _reg(), [None] * 4)
        assert _view_plots._render_view_reconstruction_png(tmp_path / "e.png", sv, {}, {}) is False
        assert plt.get_fignums() == []

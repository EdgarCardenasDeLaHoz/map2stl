"""F-SATTALL: Geopose overhead heights (city2stl.height.satellite.geopose), the ``geopose``
satellite reading and the off-by-default tall gate of ``measure.search_cap``."""

import numpy as np
import pytest
from PIL import Image

from city2stl.height.satellite import geopose as gp
from city2stl.height.satellite import measure as sm
from city2stl.height.satellite import readings as sr
from city2stl.height.satellite import scene as ss
from city2stl.height.satellite import weights as sw
from city2stl.skyline import satellite_fusion as sf

LAT = 10.40
TX, TY = 77_900, 0           # TY set below from the latitude


def _ty() -> int:
    from geo2stl.imagery import lat_to_global_px

    return int(lat_to_global_px(LAT, 18) // 256)


class FakePredictor:
    """Reads 'roof' off the red channel: 100 m where red > 200, else 0 (no network)."""

    name, tile, has_lean = "fake", 64, False

    def __init__(self, gsd_m, lean=None):
        self.gsd_m, self.lean, self.calls = gsd_m, lean, 0

    def predict(self, rgb):
        self.calls += 1
        return np.where(rgb[..., 0] > 200, 100.0, 0.0).astype(np.float32), self.lean


def _scene(tmp_path, shift_px):
    """Two z18 tiles; a 12 x 12 px footprint at the tile's middle and its 'roof' displaced
    ``shift_px`` east, as a leaning 100 m tower would show."""
    ty = _ty()
    img = np.zeros((256, 512, 3), np.uint8)
    y0, x0 = 120, 100
    img[y0:y0 + 12, x0 + shift_px:x0 + shift_px + 12, 0] = 255
    Image.fromarray(img[:, :256]).save(tmp_path / f"18_{TX}_{ty}.jpg", quality=100)
    Image.fromarray(img[:, 256:]).save(tmp_path / f"18_{TX + 1}_{ty}.jpg", quality=100)
    gx, gy = TX * 256 + x0, ty * 256 + y0
    sq = np.array([[gx, gy], [gx + 12, gy], [gx + 12, gy + 12], [gx, gy + 12]], float)
    fp = dict(fid="t1", P=[sq], bb=(gx, gy, gx + 12, gy + 12))
    return ss.TileSource(tmp_path), fp


def _gsd(fp):
    return ss.mpp(gp._lat_of_global_px((fp["bb"][1] + fp["bb"][3]) / 2))


# ------------------------------------------------------------------------------ footprints
def test_height_is_read_where_the_roof_is_not_over_the_osm_footprint(tmp_path):
    """A 100 m roof displaced by its lean: the unshifted outline sees ground (0 m), the search
    along the lean finds the roof and the height where the two agree."""
    M = None
    src, fp = _scene(tmp_path, shift_px=42)
    M = _gsd(fp)
    pred = FakePredictor(M)
    flat = gp.heights_for_footprints(src, [fp], predictor=pred)
    assert flat["t1"]["h_m"] < 5 and flat["t1"]["lean_dx"] is None
    lean_m = (42 * M / 100.0, 0.0)                       # 42 px over 100 m of height
    out = gp.heights_for_footprints(src, [fp], predictor=FakePredictor(M), lean_m=lean_m)["t1"]
    assert out["h_m"] == pytest.approx(100.0, abs=6) and out["consistent"]
    assert out["backend"] == "fake" and out["lean_dx"] == pytest.approx(lean_m[0], abs=1e-3)


def test_registration_offset_moves_the_outline(tmp_path):
    src, fp = _scene(tmp_path, shift_px=0)
    M = _gsd(fp)
    fp["P"] = [fp["P"][0] + (80.0, 0.0)]                  # OSM outline 80 px east of the roof
    fp["bb"] = (fp["bb"][0] + 80, fp["bb"][1], fp["bb"][2] + 80, fp["bb"][3])
    miss = gp.heights_for_footprints(src, [fp], predictor=FakePredictor(M))["t1"]["h_m"]
    hit = gp.heights_for_footprints(src, [fp], predictor=FakePredictor(M),
                                    reg_m=(-80 * M, 0.0))["t1"]["h_m"]
    assert miss < 5 and hit == pytest.approx(100.0, abs=6)


def test_model_lean_comes_from_the_nearest_tile_and_is_scaled_to_metres(tmp_path):
    src, fp = _scene(tmp_path, shift_px=0)
    M = _gsd(fp)
    pred = FakePredictor(M, lean=(np.pi / 2, 0.3))        # 0.3 px per metre, pointing along +x
    pred.has_lean = True
    out = gp.heights_for_footprints(src, [fp], predictor=pred)["t1"]
    assert out["lean_dx"] == pytest.approx(gp.LEAN_SIGN[0] * 0.3 * M, rel=1e-3)
    assert out["lean_dy"] == pytest.approx(0.0, abs=1e-3)


def test_lean_from_model_axes():
    assert gp.lean_from_model(0.0, 0.2, 0.5) == pytest.approx((0.0, gp.LEAN_SIGN[1] * 0.1))
    assert gp.lean_from_model(np.pi / 2, 0.2, 0.5) == pytest.approx((gp.LEAN_SIGN[0] * 0.1, 0.0), abs=1e-9)


def test_predict_tiled_blends_to_the_input_size_and_skips_empty_tiles():
    class Const:
        tile, calls = 64, 0

        def predict(self, rgb):
            self.calls += 1
            return np.full(rgb.shape[:2], 7.0, np.float32), (0.0, 0.1)

    p = Const()
    valid = np.zeros((100, 150), bool)
    valid[:, :64] = True
    agl, leans = gp.predict_tiled(p, np.zeros((100, 150, 3), np.uint8), valid)
    assert agl.shape == (100, 150) and np.nanmax(agl) == pytest.approx(7.0)
    assert np.isnan(agl[:, 140:]).all() and p.calls <= 6 and leans


# ------------------------------------------------------------------------------ backends
def test_backends_without_weights_or_gpu_say_why(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOPOSE_MODELS", str(tmp_path))
    monkeypatch.delenv("GEOPOSE_1ST_DIR", raising=False)
    assert gp.available_backends() == []
    with pytest.raises(gp.BackendUnavailable, match="no weights"):
        gp.load_predictor("pubgeo")
    with pytest.raises(gp.BackendUnavailable, match="1st place"):
        gp.load_predictor("first")
    w = tmp_path / gp.SPECS["rs3dada"].weights
    w.parent.mkdir(parents=True)
    w.write_bytes(b"x")
    assert gp.available_backends() == ["rs3dada"]
    monkeypatch.setattr("city2stl.resources.wait_for_gpu", lambda need: False)
    with pytest.raises(gp.GpuUnavailable):                # never a silent CPU fallback
        gp.load_predictor("rs3dada")
    monkeypatch.setenv("GEOPOSE_1ST_DIR", str(tmp_path))
    assert gp.available_backends()[0] == "first"
    with pytest.raises(gp.BackendUnavailable):
        gp.first_predictor(order=("first", "pubgeo"))


def test_pubgeo_checkpoint_loads_strictly_and_runs_a_tiny_cpu_tile():
    torch = pytest.importorskip("torch")
    path = gp.model_root() / gp.SPECS["pubgeo"].weights
    if not path.is_file():
        pytest.skip("pubgeo weights not downloaded")
    from city2stl.height.satellite import geopose_nets as gn

    net = gn.UnetVFLOW()
    net.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    net.eval()
    with torch.no_grad():
        xy, h, scale = net(torch.zeros(1, 3, 64, 64))
    assert xy.shape == (1, 2) and h.shape == (1, 1, 64, 64) and scale.shape == (1,)


def test_dpt_head_shapes_on_a_tiny_cpu_input():
    torch = pytest.importorskip("torch")
    from city2stl.height.satellite import geopose_nets as gn

    head = gn._DPTHead().eval()
    feats = [torch.zeros(1, 4, 1024) for _ in range(4)]   # 2 x 2 patches
    with torch.no_grad():
        out = head(feats, 2, 2)
    assert out.shape == (1, 1, 28, 28)


# ------------------------------------------------------------------------------ readings
def test_geopose_sigma_is_wide_and_dropped_when_low():
    assert sw.sigma_log("geopose", 5, 0.5) is None
    assert sw.sigma_log("geopose", 60, 0.5) == 0.35 and sw.sigma_log("geopose", 160, 0.5) == 0.45
    assert "geopose" in sw.KINDS and sw.sigma_log("geopose", 60, 0.5) > sw.sigma_log("shadow", 60, 0.5)


RES = dict(h_m=156.0, lean_dx=0.12, lean_dy=-0.2, backend="pubgeo", n_px=900, consistent=True)


def test_geopose_reading_round_trips_and_calibrate_leaves_it_alone(tmp_path):
    rs = sr.footprint_readings(lean=dict(height_m=30.0, conf=0.9), geopose=RES, ref_scene="s1")
    by = {r.method: r for r in rs}
    assert by["geopose"].height_m == 156.0 and by["geopose"].conf == gp.GEOPOSE_CONF
    assert by["geopose"].extra["backend"] == "pubgeo" and by["geopose"].extra["lean_dx"] == 0.12
    assert by["lean"].conf == sr.LOW_LEAN_MAX_CONF        # the lean rules still apply
    assert [r.method for r in rs] == [m for m in sr.METHODS if m in by]
    sr.save(tmp_path / "r.json", {"f": rs}, {"scenes": {}})
    _, back = sr.load(tmp_path / "r.json")
    assert [r.to_json() for r in back["f"]["readings"]] == [r.to_json() for r in rs]
    assert sr.footprint_readings(geopose=dict(RES, h_m=6.0)) == []     # under 10 m: no reading


def test_geopose_cannot_publish_or_support_a_height_in_skyline():
    rs = sr.footprint_readings(geopose=RES)
    assert sf.publishable(rs) is None and sf.tier_readings(rs) == [] and sf.fusion_readings({"f": rs}) == {}
    from city2stl.skyline._core.tiers import single_support

    assert single_support(150.0, [(r.method, r.height_m, r.conf) for r in rs]) == []


# ------------------------------------------------------------------------------ the gate
def test_gate_is_off_by_default_and_widens_only_untagged_footprints_when_on():
    cap = sm.search_cap
    tall = dict(tag=None, geopose=150.0)
    assert sm.GEOPOSE_GATE is False
    assert cap(tall, 60.0, 320.0) == 60.0 and cap(tall, 80.0, 330.0) == 80.0   # off: unchanged
    assert cap(tall, 60.0, 320.0, geopose_gate=True) == sm.HINT_TALL_CAP_M
    assert cap(tall, 80.0, 330.0, geopose_gate=True) == sm.HINT_TALL_CAP_M
    assert cap(dict(tag=None, geopose=70.0), 60.0, 320.0, geopose_gate=True) == 60.0   # not over 80 m
    assert cap(dict(tag=150.0, geopose=300.0), 60.0, 320.0, geopose_gate=True) == 240.0  # tag sizes it
    assert cap(dict(tag=None, geopose=150.0, hint=300.0), 80.0, 330.0, geopose_gate=True) == 330.0
    assert cap(dict(tag=None), 60.0, 320.0, geopose_gate=True) == 60.0


def test_gate_default_follows_the_module_switch(monkeypatch):
    f = dict(tag=None, geopose=150.0)
    monkeypatch.setattr(sm, "GEOPOSE_GATE", True)
    assert sm.search_cap(f, 60.0, 320.0) == sm.HINT_TALL_CAP_M
    assert sm.search_cap(f, 60.0, 320.0, geopose_gate=False) == 60.0
    assert sm._margin_h(f) == pytest.approx(sm.search_cap(f, 80.0, 330.0) / 1.2)
    monkeypatch.setattr(sm, "GEOPOSE_GATE", False)
    assert sm._margin_h(f) == 30.0

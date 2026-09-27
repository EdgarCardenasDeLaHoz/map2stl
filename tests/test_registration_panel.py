"""Tests for F-REGION §5 / F-LANDMARK §6: plate registration in the UI and the model critic.

Covers the logic promoted from ``tools/align_tool`` into ``city2stl.registration``
(``correlate``, ``consensus``), the new ``critic`` module, ``core/plate_registration``
(pack matching, placement geometry, the task registry) and ``routers/registration.py``.

Everything runs on a synthetic align pack built in a temp dir (``ALIGN_DATA_DIR``):
a random block city as the OSM raster and the same city cut out and scaled as the
plate raster, so the true placement is known exactly.  No network: the street
placement itself (Overpass + DEM) is replaced by a stub where a task runs it.
"""

from __future__ import annotations

import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.server.core import plate_registration
from app.server.server import app
from city2stl.registration import consensus, critic
from city2stl.registration.correlate import find_peaks, height_channel, ncc_surface

RES = 128
# plate px -> OSM px: the plate (RES x RES) covers the middle half of the OSM window.
TRUE_M = np.array([[0.5, 0.0, 32.0], [0.0, 0.5, 32.0]])
BBOX = (41.40, 41.38, 2.20, 2.17)          # N, S, E, W


def _city(seed: int = 3) -> np.ndarray:
    """Random rectangular blocks, 6-40 m tall, NaN for ground (the OSM raster's convention)."""
    rng = np.random.default_rng(seed)
    osm = np.full((RES, RES), np.nan, np.float32)
    for _ in range(260):
        y, x = rng.integers(0, RES - 6, 2)
        h, w = rng.integers(2, 6, 2)
        osm[y:y + h, x:x + w] = rng.uniform(6, 40)
    return osm


def _plate_from(osm: np.ndarray, M: np.ndarray) -> np.ndarray:
    """What the vendor plate raster would be under placement M (plate px -> OSM px)."""
    inv = cv2.invertAffineTransform(M)
    filled = np.nan_to_num(osm, nan=0.0).astype(np.float32)
    return cv2.warpAffine(filled, inv, (RES, RES), flags=cv2.INTER_NEAREST)


def write_pack(root, slug="testcity_spain", city="Testcity", *, matrix=TRUE_M, surveyed=False):
    d = root / slug
    d.mkdir(parents=True)
    osm = _city()
    plate_m = _plate_from(osm, TRUE_M)           # metres
    tallest = float(np.nanmax(plate_m))
    units = plate_m / tallest * 5.0              # plate model units, tallest roof = 5
    meta = {
        "slug": slug, "city": city, "region": f"{city}, Spain", "tallest_m": tallest,
        "resolution": RES, "cell_size_m": 8.0, "osm_bbox_nsew": list(BBOX),
        "stl_shape": [RES, RES], "refined_span_m": RES * 0.5 * 8.0,
    }
    if surveyed:
        meta.pop("stl_shape")
        np.save(d / "stl_heightmap.npy", np.where(plate_m > 0, plate_m, np.nan).astype(np.float32))
        np.save(d / "stl_residual.npy", plate_m.astype(np.float32))
    else:
        meta["pipeline_guess"] = {"matrix": np.asarray(matrix).tolist(), "scale": 0.5,
                                  "rot_deg": 0.0, "source": "street_place"}
        meta["refinement"] = {"accepted": True, "r": 0.8}
        np.save(d / "stl_heightmap.npy", np.where(units > 0, units, np.nan).astype(np.float32))
        np.save(d / "stl_residual.npy", units.astype(np.float32))
    np.save(d / "stl_relief.npy", (units + 1.0).astype(np.float32))
    np.save(d / "osm_buildings.npy", osm)
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d, osm, plate_m


@pytest.fixture()
def pack_dir(tmp_path, monkeypatch):
    root = tmp_path / "align_data"
    root.mkdir()
    monkeypatch.setenv("ALIGN_DATA_DIR", str(root))
    return root


# ---------------------------------------------------------------------------
# correlate + consensus (promoted from tools/align_tool)
# ---------------------------------------------------------------------------

class TestCorrelate:
    def test_height_channel_unit_max_no_nan(self):
        a = height_channel(np.array([[np.nan, 2.0], [-1.0, 4.0]]))
        assert np.isfinite(a).all() and a.max() == 1.0 and a.min() == 0.0

    def test_find_peaks_suppresses_neighbourhood(self):
        s = np.zeros((40, 40))
        s[10, 10], s[11, 11], s[30, 30] = 5.0, 4.9, 3.0
        peaks = find_peaks(s, separation=4, count=2)
        assert (peaks[0]["y"], peaks[0]["x"]) == (10, 10)
        assert (peaks[1]["y"], peaks[1]["x"]) == (30, 30)

    def test_ncc_surface_peaks_at_zero_shift_for_identical(self):
        rng = np.random.default_rng(0)
        f = rng.random((32, 32)).astype(np.float32)
        support = np.zeros_like(f)
        support[8:24, 8:24] = 1
        tmpl = f * support
        surf = ncc_surface(f, tmpl, support)
        y, x = np.unravel_index(np.argmax(surf), surf.shape)
        assert (y, x) == (16, 16)          # zero shift sits at the centre
        assert surf[16, 16] == pytest.approx(1.0, abs=1e-4)


class TestConsensus:
    def test_true_placement_passes(self, pack_dir):
        write_pack(pack_dir)
        out = consensus.score_export("testcity_spain", fix=False)
        assert out["status"] == "pass"
        assert out["lead"] == pytest.approx(1.0)
        assert out["voting"] >= consensus.MIN_VOTERS

    def test_shifted_placement_does_not_pass_and_is_corrected(self, pack_dir):
        write_pack(pack_dir)
        bad = TRUE_M.copy()
        bad[0, 2] += 6.0                     # 6 OSM px = 48 m east
        unfixed = consensus.score_export("testcity_spain", fix=False, matrix=bad)
        assert unfixed["status"] != "pass"
        fixed = consensus.score_export("testcity_spain", fix=True, matrix=bad)
        assert fixed["corrected_m"] > 0
        assert fixed["matrix"][0][2] == pytest.approx(TRUE_M[0, 2], abs=1.0)

    def test_record_verdict_writes_meta(self, pack_dir):
        d, *_ = write_pack(pack_dir)
        out = consensus.score_export("testcity_spain", fix=False)
        consensus.record_verdict("testcity_spain", out)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        assert meta["registration"]["status"] == "pass"

    def test_missing_pack_returns_none(self, pack_dir):
        assert consensus.score_export("nope") is None


# ---------------------------------------------------------------------------
# critic
# ---------------------------------------------------------------------------

class TestCriticMetrics:
    def _blocks(self):
        ref = np.zeros((40, 40))
        ref[5:15, 5:15] = 20.0
        ref[20:30, 20:35] = 10.0
        return ref

    def test_perfect_model_scores_zero_error(self):
        ref = self._blocks()
        r = critic.score_heights(ref, ref, 1.0)
        assert r["buildings"]["n"] == 2
        assert r["buildings"]["median_abs_error_m"] == 0.0
        assert r["footprint"]["iou"] == 1.0
        assert r["roofs"]["resolvable"] is True

    def test_uniform_offset_is_reported_as_bias(self):
        ref = self._blocks()
        model = np.where(ref > 0, ref + 3.0, 0.0)
        r = critic.score_heights(model, ref, 5.0)
        assert r["buildings"]["median_abs_error_m"] == pytest.approx(3.0)
        assert r["buildings"]["mean_error_m"] == pytest.approx(3.0)
        assert r["cells"]["bias_m"] == pytest.approx(3.0)
        assert r["roofs"]["resolvable"] is False and r["roofs"]["note"]

    def test_fit_scale_recovers_units(self):
        ref = self._blocks()
        r = critic.score_heights(ref / 4.0, ref, 1.0, fit_scale=True)
        assert r["scale"]["m_per_unit"] == pytest.approx(4.0)
        assert r["buildings"]["median_abs_error_m"] == pytest.approx(0.0, abs=1e-9)

    def test_missing_building_lowers_recall_not_precision(self):
        ref = self._blocks()
        model = ref.copy()
        model[20:30, 20:35] = 0.0
        r = critic.score_heights(model, ref, 1.0)
        assert r["footprint"]["precision"] == 1.0
        assert r["footprint"]["recall"] < 1.0

    def test_labels_score_per_footprint(self):
        ref = self._blocks()
        labels = np.zeros(ref.shape, int)
        labels[5:15, 5:10], labels[5:15, 10:15] = 1, 2
        model = ref.copy()
        model[5:15, 10:15] = 30.0
        r = critic.score_heights(model, ref, 1.0, labels=labels)
        assert r["buildings"]["n"] == 2
        assert r["buildings"]["p90_abs_error_m"] > 5.0

    def test_roof_shape_error_sees_a_gable(self):
        ref = np.zeros((30, 30))
        ref[5:25, 5:25] = 10.0 + np.abs(np.arange(20) - 9.5)[None, :] * -0.5 + 5.0
        flat = np.where(ref > 0, np.median(ref[ref > 0]), 0.0)
        r = critic.score_heights(flat, ref, 1.0)
        assert r["roofs"]["n"] == 1
        assert r["roofs"]["median_relief_model_m"] == 0.0
        assert r["roofs"]["median_shape_error_m"] > 0.5

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            critic.score_heights(np.zeros((3, 3)), np.zeros((4, 4)), 1.0)

    def test_resample_to_grid_identity_and_outside(self):
        src = np.arange(16, dtype=float).reshape(4, 4)
        same = critic.resample_to_grid(src, (4, 0, 4, 0), (4, 0, 4, 0), (4, 4), nearest=True)
        np.testing.assert_allclose(same, src)
        north = critic.resample_to_grid(src, (4, 0, 4, 0), (4, 0, 4, 0), (4, 4),
                                        src_row0="north", nearest=True)
        np.testing.assert_allclose(north, src[::-1])
        out = critic.resample_to_grid(src, (4, 0, 4, 0), (8, 4, 4, 0), (4, 4))
        assert np.isnan(out).all()


class TestCriticReferencesAndModels:
    def test_pack_reference_is_in_metres_in_the_osm_frame(self, pack_dir):
        _d, osm, _plate = write_pack(pack_dir)
        ref = critic.pack_reference("testcity_spain")
        assert ref.shape == (RES, RES)
        assert ref.meta["kind"] == "plate"
        inside = ref.valid
        assert inside[40:90, 40:90].all() and not inside[:20, :20].any()
        truth = np.nan_to_num(osm, nan=0.0)
        err = np.abs(ref.heights_m - truth)[40:90, 40:90]
        assert np.median(err) < 0.5

    def test_surveyed_pack_spans_the_window(self, pack_dir):
        write_pack(pack_dir, slug="survey_pr", surveyed=True)
        ref = critic.pack_reference("survey_pr")
        assert ref.meta["kind"] == "survey" and ref.meta["m_per_unit"] == 1.0

    def test_missing_pack_raises(self, pack_dir):
        with pytest.raises(FileNotFoundError):
            critic.pack_reference("nope")

    def _feature(self, n, s, e, w, h):
        return {"type": "Feature", "properties": {"height_m": h},
                "geometry": {"type": "Polygon",
                             "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}

    def test_buildings_raster_fills_polygons_south_up(self):
        ref = critic.Reference(np.zeros((20, 20)), (1.0, 0.0, 1.0, 0.0), 5.0, "test")
        feats = [self._feature(0.2, 0.0, 0.2, 0.0, 12.0)]   # south-west corner
        h, lab = critic.buildings_raster(feats, ref)
        assert h[0, 0] == 12.0 and h[-1, -1] == 0.0         # row 0 = south
        assert lab[0, 0] == 1

    def test_score_model_buildings_against_pack(self, pack_dir):
        write_pack(pack_dir)
        ref = critic.pack_reference("testcity_spain")
        n, s, e, w = BBOX
        # One building across the whole window at 15 m: wrong everywhere but scoreable.
        feats = [self._feature(n, s, e, w, 15.0)]
        r = critic.score_model({"kind": "buildings", "features": feats}, ref)
        assert r["model"]["kind"] == "buildings"
        assert r["reference"]["source"] == "pack:testcity_spain"
        assert r["footprint"]["precision"] < 1.0

    def test_score_model_rejects_unknown_kind(self):
        ref = critic.Reference(np.zeros((4, 4)), (1, 0, 1, 0), 1.0, "t")
        with pytest.raises(ValueError):
            critic.score_model({"kind": "nope"}, ref)


# ---------------------------------------------------------------------------
# core/plate_registration
# ---------------------------------------------------------------------------

class TestPlateRegistrationCore:
    def test_list_packs(self, pack_dir):
        write_pack(pack_dir)
        write_pack(pack_dir, slug="survey_pr", city="Surveyed", surveyed=True)
        packs = {p["slug"]: p for p in plate_registration.list_packs()}
        assert packs["testcity_spain"]["placeable"] and packs["testcity_spain"]["scorable"]
        assert packs["survey_pr"]["surveyed"] and not packs["survey_pr"]["scorable"]

    def test_pack_for_library_path(self, pack_dir):
        write_pack(pack_dir)
        write_pack(pack_dir, slug="testcity_spain_miniature", city="Testcity Miniature")
        assert plate_registration.pack_for_library_path(
            "Testcity,_Spain_-_S,_M,_L/Testcity_L_Solid.stl") == "testcity_spain"
        assert plate_registration.pack_for_library_path(
            "Testcity Spain 3D Miniature - 123/x.stl") == "testcity_spain_miniature"
        assert plate_registration.pack_for_library_path("Elsewhere_-_L/x.stl") is None

    def test_placement_geometry_centre_and_size(self):
        meta = {"osm_bbox_nsew": list(BBOX), "resolution": RES, "cell_size_m": 8.0}
        g = plate_registration.placement_geometry(TRUE_M, meta, (RES, RES))
        n, s, e, w = BBOX
        assert g["center"]["lat"] == pytest.approx((n + s) / 2)
        assert g["center"]["lon"] == pytest.approx((e + w) / 2)
        assert g["width_m"] == pytest.approx(RES * 0.5 * 8.0)
        assert g["turn_deg"] == pytest.approx(0.0)
        assert g["bbox"]["north"] > g["center"]["lat"] > g["bbox"]["south"]
        assert len(g["corners"]) == 4

    def test_placement_geometry_reads_rotation(self):
        meta = {"osm_bbox_nsew": list(BBOX), "resolution": RES, "cell_size_m": 8.0}
        M = cv2.getRotationMatrix2D((64.0, 64.0), -30.0, 0.5)   # cv2 angle is counter-clockwise in image
        g = plate_registration.placement_geometry(M, meta, (RES, RES))
        assert abs(g["turn_deg"]) == pytest.approx(30.0, abs=1e-6)

    def test_run_verify_only(self, pack_dir):
        write_pack(pack_dir)
        stages = []
        r = plate_registration.run_plate_registration(
            "testcity_spain", place=False, progress=lambda st, pct, msg: stages.append(st))
        assert r["verdict"]["status"] == "pass"
        assert r["placement"] is None and r["placed"] is False
        assert stages[-1] == "done"
        json.dumps(r)                                   # JSON-safe (no NaN objects)

    def test_run_with_placement_uses_its_matrix(self, pack_dir, monkeypatch):
        write_pack(pack_dir)
        from city2stl.registration import street_place

        n, s, e, w = BBOX

        def fake_place(plate, *a, **k):
            return {"lat": (n + s) / 2, "lon": (e + w) / 2, "turn_deg": 0.0, "size": 1.0,
                    "confident": True, "position_confident": True, "moved_m": 3.0,
                    "channels": ["buildings"], "gscale": 0.5}

        monkeypatch.setattr(street_place, "place_plate", fake_place)
        monkeypatch.setattr(street_place, "Plate", lambda slug: object())
        r = plate_registration.run_plate_registration("testcity_spain", place=True)
        assert r["placed"] and r["placement"]["moved_m"] == 3.0
        assert r["verdict"]["status"] == "pass"
        assert r["matrix_source"] == "street_place"

    def test_surveyed_pack_cannot_be_registered(self, pack_dir):
        write_pack(pack_dir, slug="survey_pr", surveyed=True)
        with pytest.raises(ValueError):
            plate_registration.run_plate_registration("survey_pr", place=False)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    return TestClient(app)


def _wait(client, task_id, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = client.get(f"/api/registration/plate/status/{task_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("task did not finish")


class TestRegistrationRoutes:
    def test_packs_and_match(self, client, pack_dir):
        write_pack(pack_dir)
        packs = client.get("/api/registration/packs").json()["packs"]
        assert [p["slug"] for p in packs] == ["testcity_spain"]
        m = client.get("/api/registration/match",
                       params={"rel_path": "Testcity,_Spain_-_L/a.stl"}).json()
        assert m["slug"] == "testcity_spain"

    def test_task_lifecycle_verify_only(self, client, pack_dir):
        write_pack(pack_dir)
        start = client.post("/api/registration/plate/start",
                            json={"slug": "testcity_spain", "place": False,
                                  "rel_path": "Testcity/a.stl"}).json()
        snap = _wait(client, start["task_id"])
        assert snap["status"] == "done"
        assert snap["result"]["verdict"]["status"] == "pass"
        assert snap["result"]["rel_path"] == "Testcity/a.stl"

    def test_identical_running_task_is_joined(self, pack_dir, monkeypatch):
        write_pack(pack_dir)
        gate = __import__("threading").Event()

        def slow(slug, **kw):
            gate.wait(5)
            return {"slug": slug}

        monkeypatch.setattr(plate_registration, "run_plate_registration", slow)
        a = plate_registration.start_task("testcity_spain", place=False)
        b = plate_registration.start_task("testcity_spain", place=False)
        assert a.task_id == b.task_id
        cancelled = plate_registration.cancel_task(a.task_id)
        assert cancelled.status == "cancelled"
        gate.set()
        time.sleep(0.1)
        assert plate_registration.get_task(a.task_id).result is None   # discarded

    def test_error_is_reported(self, client, pack_dir, monkeypatch):
        write_pack(pack_dir)

        def boom(slug, **kw):
            raise RuntimeError("overpass down")

        monkeypatch.setattr(plate_registration, "run_plate_registration", boom)
        start = client.post("/api/registration/plate/start",
                            json={"slug": "testcity_spain", "place": True}).json()
        snap = _wait(client, start["task_id"])
        assert snap["status"] == "error" and "overpass down" in snap["error"]

    def test_unknown_pack_and_task(self, client, pack_dir):
        assert client.post("/api/registration/plate/start",
                           json={"slug": "nope"}).status_code == 404
        assert client.get("/api/registration/plate/status/nope").status_code == 404
        assert client.post("/api/registration/plate/cancel/nope").status_code == 404

    def test_critic_references_and_score(self, client, pack_dir):
        write_pack(pack_dir)
        n, s, e, w = BBOX
        refs = client.get("/api/registration/critic/references",
                          params=dict(north=n, south=s, east=e, west=w)).json()["references"]
        assert refs[0]["slug"] == "testcity_spain" and refs[0]["overlap"] == pytest.approx(1.0)
        assert refs[-1]["kind"] == "ndsm"
        feat = {"type": "Feature", "properties": {"height_m": 20.0},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [w + 0.01, s + 0.005], [w + 0.02, s + 0.005], [w + 0.02, s + 0.01],
                    [w + 0.01, s + 0.01], [w + 0.01, s + 0.005]]]}}
        r = client.post("/api/registration/critic/score", json={
            "reference": {"kind": "pack", "slug": "testcity_spain"},
            "model": {"kind": "buildings", "features": [feat]}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reference"]["pack"] == "testcity_spain"
        assert "median_abs_error_m" in body["buildings"] or body["buildings"]["n"] == 0

    def test_critic_score_validation(self, client, pack_dir):
        write_pack(pack_dir)
        r = client.post("/api/registration/critic/score", json={
            "reference": {"kind": "pack", "slug": "testcity_spain"},
            "model": {"kind": "buildings", "features": []}})
        assert r.status_code == 400
        r = client.post("/api/registration/critic/score", json={
            "reference": {"kind": "pack", "slug": "missing"},
            "model": {"kind": "buildings", "features": [{"geometry": None}]}})
        assert r.status_code == 400
        r = client.post("/api/registration/critic/score", json={
            "reference": {"kind": "pack", "slug": "testcity_spain"},
            "model": {"kind": "stl"}})
        assert r.status_code == 400

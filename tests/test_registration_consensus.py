"""city2stl.registration.consensus: tile-consensus check, verdict, correction, pack scoring."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from city2stl.registration import consensus as cs

CELL = 5.0
N = 128
SPAN = N * CELL
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _blocks(n=N, seed=0):
    """A blocky field of random building heights (about half the cells built)."""
    rng = np.random.default_rng(seed)
    small = (rng.random((n // 4, n // 4)) > 0.5) * rng.uniform(5.0, 30.0, (n // 4, n // 4))
    return np.kron(small, np.ones((4, 4)))


@pytest.fixture(scope="module")
def osm():
    return _blocks(seed=0)


def _con(**over):
    """A hand-built consensus dict (the shape crop_consensus returns, minus the votes)."""
    base = {"tiles": 4, "voting": 16, "agreeing": 16, "consensus": 1.0, "lead": 1.0,
            "biggest_cluster": 16, "endorsing": 16, "rival_votes": 0,
            "rival_dist_m": float("nan"), "median_dist_m": 0.0, "median_r": 0.9,
            "tol_m": 12.8, "votes": []}
    base.update(over)
    return base


# ------------------------------------------------------------------------------ crop_consensus

def test_correct_placement_is_unanimous(osm):
    con = cs.crop_consensus(osm.copy(), osm, IDENTITY, CELL)
    assert con["voting"] == 16 and con["agreeing"] == 16
    assert con["consensus"] == 1.0 and con["lead"] == 1.0
    assert con["endorsing"] == 16 and con["rival_votes"] == 0
    assert con["median_dist_m"] == 0.0
    assert con["median_r"] == pytest.approx(1.0, abs=1e-6)
    assert math.isnan(con["rival_dist_m"])
    assert con["tol_m"] == pytest.approx(max(2.0, cs.AGREE_FRAC * N) * CELL)
    assert len(con["votes"]) == 16 and not any(v["abstained"] for v in con["votes"])
    assert all(v["agrees"] and v["dy_px"] == 0 and v["dx_px"] == 0 for v in con["votes"])
    assert cs.verdict({}, con)["status"] == "pass"


def test_misplaced_plate_gives_a_rival_cluster_that_measures_the_error(osm):
    M = IDENTITY.copy()
    M[0, 2], M[1, 2] = 10.0, -6.0         # plate moved +10 px in x, -6 px in y
    con = cs.crop_consensus(osm.copy(), osm, M, CELL, span_m=SPAN)
    assert con["agreeing"] == 0 and con["lead"] == 0.0
    assert con["endorsing"] == 0
    assert con["rival_votes"] == con["biggest_cluster"] == 16
    assert con["rival_dist_m"] == pytest.approx(math.hypot(10, 6) * CELL)
    assert con["median_dist_m"] == pytest.approx(math.hypot(10, 6) * CELL)
    # Every tile measures the same offset as the spoiling; correct() subtracts it.
    assert {(v["dx_px"], v["dy_px"]) for v in con["votes"]} == {(10.0, -6.0)}
    v = cs.verdict({}, con)
    assert v["status"] == "fail"
    assert "somewhere else" in v["reasons"][0]
    assert "16 tiles agree" in v["reasons"][0]


def test_unrelated_map_gives_scatter_and_a_wrong_window_verdict(osm):
    con = cs.crop_consensus(osm.copy(), _blocks(seed=9), IDENTITY, CELL)
    assert con["agreeing"] == 0
    assert con["biggest_cluster"] <= 2
    assert con["median_r"] < 0.6
    v = cs.verdict({}, con)
    assert v["status"] == "fail"
    assert "no part of the map recognises this plate" in v["reasons"][0]


def test_tiles_without_buildings_abstain(osm):
    con = cs.crop_consensus(osm.copy(), np.zeros_like(osm), IDENTITY, CELL)
    assert con["voting"] == 0
    assert all(v["abstained"] for v in con["votes"])
    assert math.isnan(con["lead"]) and math.isnan(con["consensus"])
    assert math.isnan(con["median_dist_m"])
    v = cs.verdict({}, con)
    assert v["status"] == "fail" and v["reasons"] == ["no tile had enough buildings to vote"]


def test_plate_entirely_outside_the_frame_has_nothing_to_vote_on(osm):
    M = IDENTITY.copy()
    M[0, 2] = 10_000.0
    con = cs.crop_consensus(osm.copy(), osm, M, CELL)
    assert con["votes"] == [] and con["voting"] == 0
    assert math.isnan(con["lead"])
    assert con["tol_m"] > 0


def test_span_defaults_from_the_plates_footprint(osm):
    a = cs.crop_consensus(osm.copy(), osm, IDENTITY, CELL)
    b = cs.crop_consensus(osm.copy(), osm, IDENTITY, CELL, span_m=N * CELL)
    assert a["tol_m"] == b["tol_m"]
    c = cs.crop_consensus(osm.copy(), osm, IDENTITY, CELL, span_m=10 * CELL)
    assert c["tol_m"] == 2.0 * CELL                       # the 2 px floor


def test_plate_nan_outside_its_outline_is_not_footprint(osm):
    stl = osm.copy()
    stl[:, N // 2:] = np.nan                              # the plate fills only the west half
    con = cs.crop_consensus(stl, osm, IDENTITY, CELL)
    xs = [v["x"] for v in con["votes"] if not v["abstained"]]
    assert con["voting"] > 0 and max(xs) < 4              # tiles sit on the footprint
    assert con["lead"] == 1.0


# ------------------------------------------------------------------------------ verdict

def test_verdict_pass_review_fail_by_lead():
    assert cs.verdict({}, _con(lead=0.9))["status"] == "pass"
    review = cs.verdict({}, _con(lead=0.55, rival_votes=6, rival_dist_m=80.0))
    assert review["status"] == "review"
    assert "leads by only 0.55" in review["reasons"][0]
    fail = cs.verdict({}, _con(lead=0.3, rival_votes=9, rival_dist_m=120.0,
                               biggest_cluster=9, endorsing=3))
    assert fail["status"] == "fail" and "somewhere else" in fail["reasons"][0]
    # thresholds are inclusive on the pass side
    assert cs.verdict({}, _con(lead=cs.MIN_LEAD))["status"] == "pass"
    assert cs.verdict({}, _con(lead=cs.FAIL_LEAD))["status"] == "review"


def test_verdict_few_voters_downgrade_a_pass_to_review():
    v = cs.verdict({}, _con(voting=3, agreeing=3, endorsing=3, biggest_cluster=3))
    assert v["status"] == "review"
    assert v["reasons"] == ["only 3 tiles could vote"]


def test_verdict_notes_a_rejected_refinement_and_echoes_meta():
    meta = {"refinement": {"accepted": False, "r": 0.4, "peak_z": 3.2},
            "pipeline_guess": {"rot_deg": 12.5}}
    v = cs.verdict(meta, _con())
    assert v["status"] == "pass"
    assert v["reasons"] == ["refinement was rejected; the placement is the geometric one"]
    assert (v["refine_r"], v["refine_z"], v["rot_deg"]) == (0.4, 3.2, 12.5)
    ok = cs.verdict({"refinement": {"accepted": True}}, _con())
    assert ok["reasons"] == []


# ------------------------------------------------------------------------------ correct

def test_correct_moves_a_spoiled_placement_back(osm):
    M = IDENTITY.copy()
    M[0, 2], M[1, 2] = 10.0, -6.0
    M2, con, moved = cs.correct(osm.copy(), osm, M, CELL, span_m=SPAN)
    np.testing.assert_allclose(M2, IDENTITY, atol=1e-9)
    assert con["lead"] == 1.0
    assert moved == pytest.approx(math.hypot(10, 6) * CELL)
    assert M[0, 2] == 10.0            # the input matrix is left alone


def test_correct_leaves_a_good_placement_and_needs_a_span_to_move(osm):
    M2, con, moved = cs.correct(osm.copy(), osm, IDENTITY, CELL, span_m=SPAN)
    np.testing.assert_array_equal(M2, IDENTITY)
    assert moved == 0.0 and con["lead"] == 1.0
    spoiled = IDENTITY.copy()
    spoiled[0, 2] = 10.0
    M3, con3, moved3 = cs.correct(osm.copy(), osm, spoiled, CELL, span_m=None)
    np.testing.assert_array_equal(M3, spoiled)           # no span, no correction
    assert moved3 == 0.0 and con3["lead"] == 0.0


def test_correct_refuses_a_correction_beyond_a_third_of_the_plate(osm):
    M = IDENTITY.copy()
    M[0, 2] = 50.0                                       # 50 px > 0.35 * 128 = 44.8 px
    M2, con, moved = cs.correct(osm.copy(), osm, M, CELL, span_m=SPAN)
    np.testing.assert_array_equal(M2, M)
    assert moved == 0.0
    assert con["refused_correction_m"] == pytest.approx(50 * CELL)


def test_correct_does_not_chase_scatter(osm):
    M2, con, moved = cs.correct(osm.copy(), _blocks(seed=9), IDENTITY, CELL, span_m=SPAN)
    np.testing.assert_array_equal(M2, IDENTITY)
    assert moved == 0.0 and "refused_correction_m" not in con


def test_rival_offset_is_the_r_weighted_centre_of_the_biggest_away_cluster():
    votes = [
        {"abstained": False, "dy_px": 0.0, "dx_px": 0.0, "r": 0.9},       # endorses
        {"abstained": False, "dy_px": 10.0, "dx_px": -4.0, "r": 0.8},
        {"abstained": False, "dy_px": 10.0, "dx_px": -4.0, "r": 0.6},
        {"abstained": False, "dy_px": -30.0, "dx_px": 25.0, "r": 0.5},    # a lone dissenter
        {"abstained": True},
    ]
    con = {"votes": votes, "tol_m": 10.0}
    assert cs._rival_offset(con, 5.0) == (10.0, -4.0)
    only_here = {"votes": votes[:1], "tol_m": 10.0}
    assert cs._rival_offset(only_here, 5.0) is None


# ------------------------------------------------------------------------------ pack on disk

def _write_pack(root, slug, osm, matrix, **meta_extra):
    d = root / slug
    d.mkdir(parents=True)
    np.save(d / "stl_heightmap.npy", osm.copy())
    np.save(d / "osm_buildings.npy", osm)
    meta = {"slug": slug, "city": "Testville", "cell_size_m": CELL, "refined_span_m": SPAN,
            "pipeline_guess": {"matrix": np.asarray(matrix).tolist(), "rot_deg": 0.0,
                               "tx": float(matrix[0][2]), "ty": float(matrix[1][2])}}
    meta.update(meta_extra)
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def test_score_export_unknown_slug_is_none(tmp_path):
    assert cs.score_export("nope", data_dir=tmp_path) is None


def test_score_export_corrects_and_reports(tmp_path, osm, monkeypatch):
    spoiled = IDENTITY.copy()
    spoiled[0, 2] = 8.0
    _write_pack(tmp_path, "ville", osm, spoiled)
    out = cs.score_export("ville", data_dir=tmp_path)
    assert out["slug"] == "ville" and out["city"] == "Testville"
    assert out["status"] == "pass"
    assert out["corrected_m"] == pytest.approx(8.0 * CELL)
    np.testing.assert_allclose(out["matrix"], IDENTITY, atol=1e-9)
    assert out["reasons"][0].startswith("the tiles moved the placement 40 m")
    assert out["consensus_detail"]["lead"] == 1.0

    uncorrected = cs.score_export("ville", data_dir=tmp_path, fix=False)
    assert uncorrected["status"] == "fail" and uncorrected["corrected_m"] == 0.0
    np.testing.assert_allclose(uncorrected["matrix"], spoiled)

    # an explicit matrix replaces the pack's guess; data_dir comes from ALIGN_DATA_DIR by default
    monkeypatch.setenv("ALIGN_DATA_DIR", str(tmp_path))
    explicit = cs.score_export("ville", fix=False, matrix=IDENTITY)
    assert explicit["status"] == "pass" and explicit["corrected_m"] == 0.0


def test_record_verdict_writes_meta_and_bundle(tmp_path, osm):
    spoiled = IDENTITY.copy()
    spoiled[0, 2] = 8.0
    d = _write_pack(tmp_path, "ville", osm, spoiled)
    meta0 = json.loads((d / "meta.json").read_text())
    bundle = {"ville": {"images": {}, "meta": json.loads(json.dumps(meta0))},
              "other": {"images": {}, "meta": {"slug": "other"}}}
    (tmp_path / "align_data.js").write_text("window.ALIGN_DATA = " + json.dumps(bundle) + ";\n")

    out = cs.score_export("ville", data_dir=tmp_path)
    out["rival_dist_m"] = float("nan")                   # NaN must come out as null
    cs.record_verdict("ville", out, data_dir=tmp_path)

    meta = json.loads((d / "meta.json").read_text())
    assert meta["registration"]["status"] == "pass"
    assert meta["registration"]["rival_dist_m"] is None
    assert meta["registration"]["corrected_m"] == pytest.approx(40.0)
    assert meta["pipeline_guess"]["source"] == "consensus-corrected"
    assert meta["pipeline_guess"]["tx"] == pytest.approx(0.0, abs=1e-9)
    assert meta["uncorrected_guess"]["matrix"] == spoiled.tolist()

    js = (tmp_path / "align_data.js").read_text()
    assert js.startswith("window.ALIGN_DATA = ") and js.endswith(";\n")
    b = json.loads(js.split("=", 1)[1].rstrip().rstrip(";"))
    assert b["ville"]["meta"]["registration"]["status"] == "pass"
    assert b["ville"]["meta"]["pipeline_guess"]["source"] == "consensus-corrected"
    assert "registration" not in b["other"]["meta"]


def test_record_verdict_without_a_correction_leaves_the_matrix(tmp_path, osm):
    d = _write_pack(tmp_path, "ville", osm, IDENTITY)
    out = cs.score_export("ville", data_dir=tmp_path)
    cs.record_verdict("ville", out, data_dir=tmp_path)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["registration"]["status"] == "pass"
    assert "uncorrected_guess" not in meta
    assert meta["pipeline_guess"]["matrix"] == IDENTITY.tolist()


def test_record_verdict_tolerates_missing_pack_and_broken_bundle(tmp_path, osm, capsys):
    cs.record_verdict("ghost", {"status": "pass"}, data_dir=tmp_path)       # nothing to write
    assert not (tmp_path / "ghost").exists()
    _write_pack(tmp_path, "ville", osm, IDENTITY)
    (tmp_path / "align_data.js").write_text("window.ALIGN_DATA = {broken")
    cs.record_verdict("ville", {"status": "pass"}, data_dir=tmp_path)
    assert "could not read align_data.js" in capsys.readouterr().out
    assert (tmp_path / "align_data.js").read_text() == "window.ALIGN_DATA = {broken"

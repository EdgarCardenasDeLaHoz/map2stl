"""Google 2.5D low-rise prior (``city2stl/skyline/lowrise_prior.py``): correction, gate, folds,
cadastre cross-check."""

from dataclasses import dataclass

import pytest

from city2stl.height.providers.google_ob25d import Reading
from city2stl.skyline import lowrise_prior as lp
from city2stl.skyline.cadastre_heights import CadastreReading

TABLE = {"stat": "p50", "tau": 0.5,
         "offsets": {"all": {"B1": -2.0, "B2": -4.0},
                     "san_juan": {"B1": -1.0, "B2": None}}}


@pytest.fixture(autouse=True)
def table(monkeypatch):
    monkeypatch.setattr(lp, "CORRECTION", TABLE)


def rd(p50, valid=True):
    return Reading(p50 if valid else None, None, None, 0.9, 400, None, 0.5, valid)


def cad(floors, h, ph=False):
    return CadastreReading(floors=floors, height_m=h, conf=0.7, iou=0.5, zone=None, ph=ph,
                           publishable=not ph, defers=False)


def test_bands_by_raw_value():
    assert [lp.band_of(v) for v in (3.0, 14.99, 15.0, 39.9, 40.0)] == ["B1", "B1", "B2", "B2", None]


def test_corrected_uses_the_band_offset():
    assert lp.corrected(rd(9.0)) == pytest.approx(7.0)
    assert lp.corrected(rd(20.0)) == pytest.approx(16.0)
    assert lp.corrected(rd(45.0)) is None                    # above the corrected bands
    assert lp.corrected(rd(9.0, valid=False)) is None
    assert lp.corrected(None) is None


def test_held_out_city_uses_its_own_fold():
    assert lp.corrected(rd(9.0), exclude_city="San Juan") == pytest.approx(8.0)
    assert lp.corrected(rd(20.0), exclude_city="san_juan") is None   # no B2 offset in that fold
    assert lp.corrected(rd(9.0), exclude_city="cartagena") == pytest.approx(7.0)  # -> "all"


def test_prior_for_gates_at_20_m():
    t41 = (11.5, "prior_gbm")
    assert lp.prior_for(t41, rd(9.0)) == (7.0, lp.SOURCE)
    assert lp.prior_for(t41, rd(25.0)) == t41               # corrected 21 m >= gate: T41
    assert lp.prior_for(t41, rd(9.0, valid=False)) == t41
    assert lp.prior_for(t41, None) == t41
    assert lp.prior_for(t41, rd(1.0)) == t41                 # corrected <= 0: no prior from it


def test_cross_check_flags_over_2x_on_non_ph_low_rise_only():
    assert lp.cross_check(8.1, cad(1, 4.0))                  # ratio 2.025
    assert not lp.cross_check(7.9, cad(1, 4.0))              # ratio 1.975
    assert lp.cross_check(2.0, cad(2, 7.0))                  # below, too
    assert not lp.cross_check(30.0, cad(1, 4.0, ph=True))    # PH: a unit's floor count
    assert not lp.cross_check(100.0, cad(12, 40.0))          # tower: over TOWER_FLOORS
    assert not lp.cross_check(None, cad(1, 4.0))
    assert not lp.cross_check(8.0, None)


@dataclass
class Rec:
    feature_id: str
    geometry: object = None


def test_fallback_chains_over_t41():
    readings = {"a": rd(9.0), "b": rd(30.0), "c": rd(9.0, valid=False)}
    fb = lp.fallback_for([Rec("a"), Rec("b"), Rec("c"), Rec("d")], "cartagena",
                         t41=lambda r: (12.0, "prior_gbm"), readings=readings)
    assert fb(Rec("a")) == (7.0, lp.SOURCE)
    assert fb(Rec("b")) == (12.0, "prior_gbm")
    assert fb(Rec("c")) == (12.0, "prior_gbm")
    assert fb(Rec("d")) == (12.0, "prior_gbm")


def test_row_fields():
    f = lp.row_fields(rd(9.0))["google_2p5d"]
    assert f["raw_m"] == 9.0 and f["corrected_m"] == 7.0 and f["used"] and f["year"] == 2023
    assert lp.row_fields(None) == {"google_2p5d": None}
    assert lp.row_fields(rd(9.0, valid=False))["google_2p5d"]["used"] is False


def test_load_region_never_raises(monkeypatch):
    from city2stl.height.providers import google_ob25d as gob

    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(gob, "footprint_heights", boom)
    from shapely.geometry import box
    assert lp.load_region("x", [Rec("a", box(0, 0, 1e-4, 1e-4))]) == {}
    assert lp.load_region("x", [Rec("a")]) == {}


def test_shipped_table_is_complete(monkeypatch):
    monkeypatch.undo()                     # the module's own CORRECTION, not the fixture's
    c = lp.CORRECTION
    assert c["stat"] in ("p50", "p70", "p90") and 0 < c["tau"] < 1
    assert set(c["offsets"]) == {"all", "san_juan", "mayaguez", "charlotte_amalie"}
    assert all(set(v) == {"B1", "B2"} for v in c["offsets"].values())
    assert lp.STATUS in ("admitted", "refused")

"""Cadastre floors in the skyline run (``cadastre_heights``): tiers, fallback, attribution."""

from types import SimpleNamespace

import pytest

from city2stl.skyline import cadastre_heights as ch
from city2stl.skyline import tier_display as td
from city2stl.skyline._core import tiers
from city2stl.skyline._core.tiers import reading, tier_fields, verification_tier


def C(h=7.0, floors=2, ph=False, publishable=True, defers=False):
    return ch.CadastreReading(floors=floors, height_m=h, conf=0.7, iou=0.6, zone="MANGA", ph=ph,
                              publishable=publishable, defers=defers, codigo="1" * 30,
                              dataset="cartagena")


def sat(method, h, conf=0.9):
    return SimpleNamespace(method=method, height_m=h, conf=conf)


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(tiers, "INDEPENDENT", dict(tiers.INDEPENDENT))
    ch.register_tier_rules()


def test_cadastre_plus_one_image_reading_verifies():
    cad = reading("cadastre", 7.0)
    assert verification_tier([cad, reading("drone", 7.5, "seed_1")]) == (
        "verified_2", ["cadastre", "drone:seed_1"])
    assert verification_tier([cad, reading("multiview", 6.0)])[0] == "verified_2"
    assert verification_tier([cad, reading("lean", 8.0)])[0] == "verified_2"


@pytest.mark.parametrize("other", [
    reading("shadow", 7.0),                 # a lower bound
    reading("floors", 7.0),                 # facade floors
    reading("street", 7.0, "seed_2"),       # Street View
    reading("stereo", 7.0),                 # stereo below SAT_MIN_M
    reading("drone", 20.0, "seed_1"),       # disagrees
])
def test_cadastre_never_verified_by(other):
    assert verification_tier([reading("cadastre", 7.0), other])[0] == "single"


def test_tall_stereo_verifies_a_cadastre_tower():
    assert verification_tier([reading("cadastre", 90.0), reading("stereo", 85.0)])[0] == "verified_2"


def test_tier_readings_skip_propiedad_horizontal():
    assert ch.tier_readings(C()) == [reading("cadastre", 7.0)]
    assert ch.tier_readings(C(ph=True, publishable=False)) == []
    assert ch.tier_readings(None) == []
    # a tower's count is evidence (a reading), not a publishable height
    assert ch.tier_readings(C(h=90.0, floors=21, publishable=False, defers=True)) == [
        reading("cadastre", 90.0)]


def test_prior_for_replaces_the_prior_only_when_publishable_and_undisputed():
    prior = (12.1, "prior_gbm")
    assert ch.prior_for(prior, C()) == (7.0, "cadastre")
    assert ch.prior_for(prior, None) == prior
    assert ch.prior_for(prior, C(ph=True, publishable=False)) == prior
    assert ch.prior_for(prior, C(h=90.0, floors=21, publishable=False, defers=True)) == prior
    # a confident satellite reading of 40 m or more says the footprint is a tower
    assert ch.prior_for(prior, C(), [sat("stereo", 66.5)]) == prior
    assert ch.prior_for(prior, C(), [sat("stereo", 66.5, conf=0.3)]) == (7.0, "cadastre")
    assert ch.prior_for(prior, C(), [sat("shadow", 66.5)]) == (7.0, "cadastre")
    assert ch.prior_for(prior, C(), [sat("stereo", 3.0)]) == (7.0, "cadastre")


def test_two_x_rule_against_the_cadastre():
    # the hook makes the cadastre the prior: a lone drone reading of 99 m on a 2-floor house is
    # withheld and the cadastre published
    assert tiers.single_withheld("single", 99.0, ch.prior_for((12.0, "prior_gbm"), C())[0])
    f = tier_fields([reading("drone", 99.0, "seed_6"), reading("cadastre", 7.0)],
                    published_m=99.0, prior_m=7.0, prior_source="cadastre")
    assert f["tier"] == "single" and f["prior_disagrees"]


def test_load_region_matches_and_registers(monkeypatch):
    from shapely.geometry import box

    from city2stl.height.providers import co_catastro as cc

    m = cc.CadastreMatch(floors=2, height_m=7.0, conf=0.7, iou=0.8, zone="MANGA", storey_m=3.0,
                         index=0, codigo="1" * 30)
    monkeypatch.setattr(cc, "match_footprints", lambda fps, ds=None, **k: {"b0001": m})
    monkeypatch.setattr(tiers, "INDEPENDENT", {})
    recs = [SimpleNamespace(feature_id="b0001", geometry=box(-75.55, 10.41, -75.549, 10.411),
                            centroid_lat=10.4105, centroid_lon=-75.5495)]
    out = ch.load_region("cartagena", recs)
    assert out["b0001"].publishable and out["b0001"].dataset == "cartagena"
    assert frozenset({"cadastre", "drone"}) in tiers.INDEPENDENT
    # outside every cadastre: nothing, no error
    far = [SimpleNamespace(feature_id="x", geometry=box(2.0, 41.0, 2.001, 41.001),
                           centroid_lat=41.0005, centroid_lon=2.0005)]
    assert ch.load_region("barcelona", far) == {}


def test_load_region_never_fails_the_run(monkeypatch):
    from shapely.geometry import box

    from city2stl.height.providers import co_catastro as cc

    def boom(*a, **k):
        raise OSError("datos.gov.co down")
    monkeypatch.setattr(cc, "match_footprints", boom)
    recs = [SimpleNamespace(feature_id="b0001", geometry=box(-75.55, 10.41, -75.549, 10.411),
                            centroid_lat=10.4105, centroid_lon=-75.5495)]
    assert ch.load_region("cartagena", recs) == {}


def test_row_fields_and_attribution():
    row = {"effective_height_m": 7.0, "effective_height_source": "withheld:cadastre",
           "tier": "single", "tier_methods": ["cadastre"], "cadastre": ch.row_fields(C())}
    assert row["cadastre"]["floors"] == 2
    assert td.uses_cadastre(row)
    assert td.source_label(row) == "cadastre floors"
    lines = td.height_attributions([row])
    assert len(lines) == 1 and "CC BY-SA 4.0" in lines[0] and "same licence" in lines[0]
    verified = {"effective_height_source": "withheld:elevated", "tier": "verified_2",
                "tier_methods": ["cadastre", "drone:seed_1"]}
    assert td.cadastre_attributions([verified]) == [td.CADASTRE_ATTRIBUTIONS["cartagena"]]
    assert td.height_attributions([{"effective_height_source": "withheld:prior_gbm"}]) == []

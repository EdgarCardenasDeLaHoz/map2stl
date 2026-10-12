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
        "corroborated", ["cadastre", "drone:seed_1"])
    assert verification_tier([cad, reading("multiview", 6.0)])[0] == "corroborated"
    assert verification_tier([cad, reading("lean", 8.0)])[0] == "corroborated"


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
    assert verification_tier([reading("cadastre", 90.0), reading("stereo", 85.0)])[0] == "corroborated"


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
    verified = {"effective_height_source": "withheld:elevated", "tier": "corroborated",
                "tier_methods": ["cadastre", "drone:seed_1"]}
    assert td.cadastre_attributions([verified]) == [td.CADASTRE_ATTRIBUTIONS["cartagena"]]
    assert td.height_attributions([{"effective_height_source": "withheld:prior_gbm"}]) == []


# ------------------------------------------------------------------------------ plausibility band
def test_band_of_a_non_ph_plot_is_floors_times_the_fitted_storey_within_half_and_one_and_a_half():
    # 2 floors, 150 m2 house: 3.60 m a floor -> 7.2 m, band 3.6 .. 10.8 m
    b = ch.make_band(C(floors=2), 150.0, "Residencial.Vivienda_Hasta_3_Pisos")
    assert (b.storey_m, round(b.lo_m, 2), round(b.hi_m, 2)) == (3.6, 3.6, 10.8) and not b.ph
    assert b.contains(7.0) and not b.contains(3.0) and not b.contains(11.0)
    # 1 floor is one tall storey, taller with area: 150 m2 house 4.78 m, 500 m2 6.66 m
    assert ch.make_band(C(floors=1), 150.0, "Residencial.Vivienda_Hasta_3_Pisos").hi_m == pytest.approx(7.17)
    assert ch.make_band(C(floors=1), 500.0, "Residencial.Vivienda_Hasta_3_Pisos").storey_m == 6.66
    # no use (or one the table has no class for): the area band over all classes
    assert ch.make_band(C(floors=3), 120.0, None).storey_m == 3.61
    assert ch.make_band(C(floors=3), 120.0, "Otro.Algo").storey_m == 3.61
    assert ch.cad_class("Comercial.Bodegas_Comerciales") == "industrial"
    assert ch.cad_class("Residencial.Apartamentos_4_y_mas_pisos") == "apartments"
    assert ch.cad_class("Residencial.Garajes") == "annex" and ch.cad_class(None) is None


def test_band_of_a_propiedad_horizontal_plot_is_two_floors_either_side_of_the_count():
    b = ch.make_band(C(floors=12, ph=True), 900.0, None)
    assert (b.lo_m, b.hi_m, b.ph) == (35.0, 49.0, True)
    assert ch.make_band(C(floors=1, ph=True), 900.0).lo_m == 0.0       # the lower end stops at 0


def _band_rows(single_m):
    from city2stl.skyline._core.height import withhold_untagged_street_view
    rec = SimpleNamespace(feature_id="f1", height_source="default", area_m2=150.0)
    row = {"feature_id": "f1", "effective_height_m": 20.0, "effective_height_source": "geometric",
           "per_seed_median_m": {"seed_1": single_m}}
    return withhold_untagged_street_view, rec, row


def test_a_single_outside_the_band_is_withheld_and_inside_it_stays_single(monkeypatch):
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    band = {"f1": ch.make_band(C(floors=2), 150.0, "Residencial.Vivienda_Hasta_3_Pisos")}   # 3.6-10.8
    fn, rec, row = _band_rows(30.0)                      # 30 m on a 2-floor house: outside
    fn([row], [rec], fallback=lambda r: (8.0, "prior"), measured_seeds={"seed_1"}, cadastre_band=band)
    assert row["tier"] == "prior" and row["effective_height_m"] == 8.0
    assert row["withheld_reason"] == "cadastre_band" and row["single_reading_m"] == 30.0
    assert row["cadastre_band"]["inside"] is False and row["cadastre_band"]["hi_m"] == 10.8
    assert "cadastre_band" in tiers.selection_reason(row)
    fn, rec, row = _band_rows(10.0)                      # 10 m: inside, although 1.25x the 8 m prior
    fn([row], [rec], fallback=lambda r: (4.0, "prior"), measured_seeds={"seed_1"}, cadastre_band=band)
    assert row["tier"] == "single" and row["effective_height_m"] == 10.0   # 2.5x the prior: kept anyway
    assert row["cadastre_band"]["inside"] is True and "withheld_reason" not in row
    assert "single_support" not in row
    fn, rec, row = _band_rows(2.0)                       # under the band: withheld too
    fn([row], [rec], fallback=lambda r: (8.0, "prior"), measured_seeds={"seed_1"}, cadastre_band=band)
    assert row["tier"] == "prior" and row["withheld_reason"] == "cadastre_band"
    # no band for the row: the 2x rule as before (30 m over 2x the 8 m prior, nothing supports it)
    fn, rec, row = _band_rows(30.0)
    fn([row], [rec], fallback=lambda r: (8.0, "prior"), measured_seeds={"seed_1"}, cadastre_band={})
    assert row["tier"] == "prior" and row["withheld_reason"] == "single over 2x prior"
    assert "cadastre_band" not in row


def test_the_band_never_publishes_a_cadastre_height_or_adds_a_tier_reading(monkeypatch):
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    band = {"f1": ch.make_band(C(floors=2), 150.0, "Residencial.Vivienda_Hasta_3_Pisos")}
    fn, rec, row = _band_rows(None)
    row["per_seed_median_m"] = {}                         # no reading at all: the prior, not the cadastre
    fn([row], [rec], fallback=lambda r: (12.0, "prior"), measured_seeds={"seed_1"}, cadastre_band=band)
    assert row["effective_height_m"] == 12.0 and row["tier"] == "prior"
    assert "cadastre" not in row["tier_methods"] and "cadastre_band" not in row


def test_a_ph_plot_ignores_its_band_and_keeps_the_2x_rule(monkeypatch):
    """The user (2026-10-10): a PH count is often a unit's, so the band never overrides a measured
    height there; the 2x rule with satellite support applies as before."""
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    band = {"f1": ch.make_band(C(floors=10, ph=True), 900.0)}           # 28-42 m
    fn, rec, row = _band_rows(55.0)                                      # b0440: ls 55 m at 1.0
    fn([row], [rec], fallback=lambda r: (23.0, "prior"), measured_seeds={"seed_1"},
       satellite={"f1": [sat("ls", 55.0, 1.0)]}, cadastre_band=band)
    assert row["tier"] == "single" and row["effective_height_m"] == 55.0
    assert row["single_support"] == ["ls"] and "cadastre_band" not in row
    fn, rec, row = _band_rows(55.0)                                      # unsupported: the 2x rule
    fn([row], [rec], fallback=lambda r: (23.0, "prior"), measured_seeds={"seed_1"}, cadastre_band=band)
    assert row["tier"] == "prior" and row["withheld_reason"] == "single over 2x prior"
    assert "cadastre_band" not in row
    fn, rec, row = _band_rows(20.0)                                      # under the PH band: kept
    fn([row], [rec], fallback=lambda r: (23.0, "prior"), measured_seeds={"seed_1"}, cadastre_band=band)
    assert row["tier"] == "single" and row["effective_height_m"] == 20.0


def test_a_supported_non_ph_single_outside_the_band_is_tolerated_within_2x_its_upper_end(monkeypatch):
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    band = {"f1": ch.make_band(C(floors=15), 900.0, "Comercial.Hoteles")}   # 15 x 3.93: 29.5-88.4 m
    fn, rec, row = _band_rows(120.0)                                     # 1.36x the upper end
    fn([row], [rec], fallback=lambda r: (15.0, "prior"), measured_seeds={"seed_1"},
       satellite={"f1": [sat("lean", 90.0, 0.9)]}, cadastre_band=band)   # 1.33x: support, not agreement
    assert row["tier"] == "single" and row["effective_height_m"] == 120.0
    assert row["single_support"] == ["lean"] and row["cadastre_band"]["inside"] is False
    assert row["cadastre_band"]["reason"] == "cadastre_band_tolerated"
    assert "cadastre_band_tolerated" in tiers.selection_reason(row) and "withheld_reason" not in row
    fn, rec, row = _band_rows(180.0)                                     # over 2x the upper end
    fn([row], [rec], fallback=lambda r: (15.0, "prior"), measured_seeds={"seed_1"},
       satellite={"f1": [sat("lean", 130.0, 0.9)]}, cadastre_band=band)
    assert row["tier"] == "prior" and row["withheld_reason"] == "cadastre_band"
    assert "single_support" not in row and "reason" not in row["cadastre_band"]
    fn, rec, row = _band_rows(120.0)                                     # stereo alone: no support
    fn([row], [rec], fallback=lambda r: (15.0, "prior"), measured_seeds={"seed_1"},
       satellite={"f1": [sat("stereo", 90.0, 1.0)]}, cadastre_band=band)
    assert row["tier"] == "prior" and row["withheld_reason"] == "cadastre_band"

"""city2stl.panorama_heights: panorama rows replace default / raster heights, nothing measured."""

import json
import os

import pytest

from city2stl import panorama_heights as ph
from city2stl.panorama_heights import apply_panorama_heights, load_rows, resolve_report


def _square(x, y, s=0.0004):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]


def _feature(x, y, height=10.0, source="default", s=0.0004):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_square(x, y, s)]},
            "properties": {"height_m": height, "height_source": source}}


def _row(x, y, tier="single", height=30.0, s=0.0004):
    return {"tier": tier, "effective_height_m": height, "footprint_lonlat": _square(x, y, s)}


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def test_corroborated_and_single_replace_default_and_raster_heights():
    fc = _fc(_feature(0, 0), _feature(1, 1, 7.0, "ms_buildings"), _feature(2, 2, 12.0, "default"))
    out, summary = apply_panorama_heights(
        fc, [_row(0, 0, "single", 31.2), _row(1, 1, "corroborated", 44.0)])
    props = [f["properties"] for f in out["features"]]
    assert (props[0]["height_m"], props[0]["height_source"]) == (31.2, "panorama_single")
    assert (props[1]["height_m"], props[1]["height_source"]) == (44.0, "panorama_corroborated")
    assert props[1]["height_prev_m"] == 7.0 and props[1]["height_prev_source"] == "ms_buildings"
    assert props[2]["height_m"] == 12.0
    assert summary["applied"] == {"single": 1, "corroborated": 1}


def test_input_is_not_modified():
    fc = _fc(_feature(0, 0))
    apply_panorama_heights(fc, [_row(0, 0)])
    assert fc["features"][0]["properties"] == {"height_m": 10.0, "height_source": "default"}


@pytest.mark.parametrize("source", ["osm_tag", "osm_levels", "landmark", "survey", "lidar_3dep_copc", "user_override"])
def test_measured_or_stated_heights_are_kept(source):
    out, summary = apply_panorama_heights(_fc(_feature(0, 0, 25.0, source)), [_row(0, 0)])
    assert out["features"][0]["properties"]["height_m"] == 25.0
    assert summary["skipped"] == {"kept_measured": 1}
    assert summary["applied"] == {}


def test_tag_prior_and_survey_tiers_are_ignored():
    rows = [_row(0, 0, "tag"), _row(0, 0, "prior"), _row(0, 0, "survey")]
    out, summary = apply_panorama_heights(_fc(_feature(0, 0)), rows)
    assert out["features"][0]["properties"]["height_source"] == "default"
    assert summary["rows"] == {}


def test_withheld_or_missing_height_rows_are_ignored():
    rows = [_row(0, 0, height=None), _row(0, 0, height=0)]
    out, _ = apply_panorama_heights(_fc(_feature(0, 0)), rows)
    assert out["features"][0]["properties"]["height_source"] == "default"


def test_footprint_must_overlap_enough():
    # shifted by a quarter of the width: IoU 0.6 -> matches; shifted by a whole width: none
    near = _row(0.0001, 0, height=20.0)
    far = _row(0.0004, 0, height=40.0)
    out, summary = apply_panorama_heights(_fc(_feature(0, 0)), [far])
    assert out["features"][0]["properties"]["height_source"] == "default"
    assert summary["skipped"] == {"no_match": 1}
    out, _ = apply_panorama_heights(_fc(_feature(0, 0)), [near])
    assert out["features"][0]["properties"]["height_m"] == 20.0


def test_each_feature_takes_its_best_overlapping_row():
    rows = [_row(0.0001, 0, height=20.0), _row(0.00002, 0, height=33.0)]
    out, summary = apply_panorama_heights(_fc(_feature(0, 0)), rows)
    assert out["features"][0]["properties"]["height_m"] == 33.0
    assert summary["skipped"] == {"lost_to_better_overlap": 1}
    assert summary["applied"] == {"single": 1}


def test_row_without_footprint_and_empty_layer():
    row = {"tier": "single", "effective_height_m": 30.0}
    _, summary = apply_panorama_heights(_fc(_feature(0, 0)), [row])
    assert summary["skipped"] == {"no_footprint": 1}
    out, summary = apply_panorama_heights(_fc(), [_row(0, 0)])
    assert out["features"] == [] and summary["skipped"] == {"no_match": 1}


def test_load_rows_resolves_name_folder_and_file(tmp_path, monkeypatch):
    report = tmp_path / "Demo_v1_skyline_report"
    report.mkdir()
    (report / "heights.json").write_text(json.dumps({"buildings": [_row(0, 0), "junk"]}), "utf-8")
    monkeypatch.setenv("MAP2STL_SKYLINE_REPORTS", str(tmp_path))
    for spec in ("Demo_v1", "Demo_v1_skyline_report", report, report / "heights.json"):
        assert len(load_rows(spec)) == 1
    with pytest.raises(FileNotFoundError):
        resolve_report("Nope")
    with pytest.raises(FileNotFoundError):
        resolve_report("../escape")


def test_precedence_through_the_export_merge_order():
    """Export order (city_model_task): survey fill, client edits merged on, then panorama rows.

    A survey height, a user's typed height and a tag keep theirs; only the fallback takes the row.
    """
    from app.server.core.city_data import merge_layer_data

    server = _fc(_feature(0, 0, 10.0, "survey"), _feature(1, 1, 10.0, "default"),
                 _feature(2, 2, 10.0, "default"), _feature(3, 3, 14.0, "osm_tag"))
    client = _fc(_feature(0, 0, 10.0, "default"),          # client copy predates the survey fill
                 _feature(1, 1, 10.0, "default"),
                 _feature(2, 2, 33.0, "user_override"),
                 _feature(3, 3, 14.0, "osm_tag"))
    server["features"][0]["properties"]["height_m"] = 21.0
    merged = merge_layer_data({"buildings": server}, {"buildings": client}, ["buildings"])["buildings"]
    rows = [_row(i, i, "corroborated", 40.0) for i in range(4)]
    out, summary = apply_panorama_heights(merged, rows)
    got = [(f["properties"]["height_m"], f["properties"]["height_source"]) for f in out["features"]]
    assert got == [(21.0, "survey"), (40.0, "panorama_corroborated"), (33.0, "user_override"),
                   (14.0, "osm_tag")]
    assert summary["skipped"] == {"kept_measured": 3} and summary["applied"] == {"corroborated": 1}


# ---------------------------------------------------------------- Phase 5: discovery + read path

BOX = (0.01, -0.01, 0.01, -0.01)            # north, south, east, west around (0, 0)


def _report(root, name, rows, *, region="Demo", bbox=BOX, sidecar=None, mtime=None,
            head=True):
    """A report folder ``<root>/<name>_skyline_report`` whose heights.json carries region,
    bbox_nsew and tier_counts before buildings, as the writer does."""
    d = root / f"{name}_skyline_report"
    d.mkdir()
    counts = {}
    for r in rows:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    doc = {"region": region, "bbox_nsew": list(bbox), "tier_counts": counts} if head else {}
    doc["buildings"] = rows
    (d / "heights.json").write_text(json.dumps(doc), "utf-8")
    if mtime is not None:
        os.utime(d / "heights.json", (mtime, mtime))
    if sidecar is not None:
        (d / "report_summary.json").write_text(json.dumps({"schema": 1, **sidecar}), "utf-8")
    return d


def _roots():
    return ph.reports_root(), ph.photo_root()   # the per-test folders of conftest


def test_find_reports_by_bbox_head_and_sidecar():
    root, _ = _roots()
    _report(root, "Here_v1", [_row(0, 0, "single")], region="Here")
    _report(root, "Far_v1", [_row(50, 50, "single")], region="Far", bbox=(51, 49, 51, 49))
    _report(root, "Prior_v1", [_row(0, 0, "tag_prior")], region="Prior")
    # The sidecar's box wins over heights.json's (and says it is far away): skipped unparsed.
    _report(root, "Side_v1", [_row(0, 0, "single")], region="Side",
            sidecar={"bbox_nsew": [51, 49, 51, 49], "tier_counts": {"single": 1}})
    _report(root, "Side_v2", [_row(0, 0, "corroborated")], region="Side2",
            sidecar={"bbox_nsew": list(BOX), "region": "Side two", "generated_at": "2026-11-01",
                     "tier_counts": {"corroborated": 1}})
    found = {r["name"]: r for r in ph.find_reports(BOX)}
    assert set(found) == {"Here_v1", "Side_v2"}
    assert found["Here_v1"]["rows"] == {"single": 1} and found["Here_v1"]["rows_in_bbox"] == 1
    assert found["Here_v1"]["sidecar"] is False
    assert found["Side_v2"]["sidecar"] is True and found["Side_v2"]["region"] == "Side two"
    assert found["Side_v2"]["generated_at"] == "2026-11-01"
    assert {r["name"] for r in ph.find_reports(None)} == {"Here_v1", "Far_v1", "Side_v1",
                                                         "Side_v2"}


def test_find_reports_without_a_head_parses_the_file():
    root, _ = _roots()
    _report(root, "Old_v1", [_row(0, 0, "single")], head=False)
    _report(root, "OldFar_v1", [_row(50, 50, "single")], head=False, region="x")
    assert [r["name"] for r in ph.find_reports(BOX)] == ["Old_v1"]


def test_newest_run_of_a_region_supersedes_older_ones():
    root, _ = _roots()
    _report(root, "Demo_v1", [_row(0, 0, "single", 20.0)], mtime=1_000_000)
    _report(root, "Demo_v2", [_row(0, 0, "single", 25.0)], region="demo", mtime=2_000_000)
    _report(root, "Other_v1", [_row(0, 0, "single", 30.0)], region="Other", mtime=1_500_000)
    assert [r["name"] for r in ph.find_reports(BOX)] == ["Demo_v2", "Other_v1"]
    every = ph.find_reports(BOX, include_superseded=True)
    assert [(r["name"], r["superseded_by"]) for r in every] == [
        ("Demo_v2", None), ("Other_v1", None), ("Demo_v1", "Demo_v2")]
    # The newest report wins a building two regions' reports cover.
    out, summary = ph.apply_heights(_fc(_feature(0, 0)), BOX)
    p = out["features"][0]["properties"]
    assert (p["height_m"], p["height_source"], p["height_report"]) == (
        25.0, "panorama_single", "Demo_v2")
    assert summary["buildings"] == 1 and summary["skipped"] == {"lost_to_newer_report": 1}
    assert [(r["name"], r["applied"]) for r in summary["reports"]] == [
        ("Demo_v2", 1), ("Other_v1", 0)]


def _photo_store(root, region, readings):
    doc = {"source": "photo", "photos": {"a.jpg": {"saved": "2026-11-01", "heights": [
        {"building": f"b{i}", "lat": lat, "lon": lon, "height_m": m, "osm_height_m": 10.0}
        for i, (lon, lat, m) in enumerate(readings)]}}}
    (root / f"{region}_photo_heights.json").write_text(json.dumps(doc), "utf-8")


def test_photo_readings_win_and_take_the_median_per_footprint():
    root, photos = _roots()
    _report(root, "Demo_v1", [_row(0, 0, "corroborated", 40.0), _row(1, 1, "single", 22.0)],
            bbox=(2, -1, 2, -1))
    _photo_store(photos, "Demo", [(0.0002, 0.0002, 30.0), (0.0001, 0.0003, 34.0),
                                  (0.0003, 0.0001, 50.0), (9.0, 9.0, 99.0)])
    assert len(ph.photo_rows(BOX)) == 3
    fc = _fc(_feature(0, 0), _feature(1, 1), _feature(0.002, 0.002, 9.0, "osm_tag"))
    out, summary = ph.apply_heights(fc, (2, -1, 2, -1))
    props = [f["properties"] for f in out["features"]]
    assert (props[0]["height_m"], props[0]["height_source"], props[0]["height_tier"]) == (
        34.0, "photo_solve", "photo")
    assert (props[1]["height_m"], props[1]["height_source"]) == (22.0, "panorama_single")
    assert props[2]["height_m"] == 9.0
    assert summary["photo"] == {"readings": 3, "applied": 1} and summary["buildings"] == 2


def test_apply_is_idempotent_off_undoes_and_named_reports():
    root, _ = _roots()
    _report(root, "Demo_v1", [_row(0, 0, "single", 20.0)], mtime=1_000_000)
    _report(root, "Demo_v2", [_row(0, 0, "single", 25.0)], mtime=2_000_000)
    fc = _fc(_feature(0, 0, 10.0, "default"))
    once, _ = ph.apply_heights(fc, BOX)
    twice, summary = ph.apply_heights(once, BOX)
    assert twice == once and summary["buildings"] == 1
    off, summary = ph.apply_heights(once, BOX, "off")
    assert off["features"][0]["properties"] == fc["features"][0]["properties"]
    assert summary == {"mode": "off", "buildings": 0, "reports": [],
                       "photo": {"readings": 0, "applied": 0}}
    named, summary = ph.apply_heights(once, BOX, "Demo_v1")
    assert named["features"][0]["properties"]["height_m"] == 20.0 and summary["mode"] == "named"
    _, summary = ph.apply_heights(fc, BOX, ["Nope"])
    assert "Nope" in summary["error"] and summary["buildings"] == 0


@pytest.mark.parametrize("value,expect", [
    (None, "auto"), (True, "auto"), ("auto", "auto"), ("on", "auto"), (False, "off"),
    ("off", "off"), ([], "off"), ("Demo_v1", ["Demo_v1"]), (["A", "B"], ["A", "B"])])
def test_heights_mode(value, expect):
    assert ph.heights_mode(value) == expect


def test_signature_is_empty_without_sources_and_changes_with_a_new_run():
    root, _ = _roots()
    assert ph.signature(BOX) == "" and ph.signature(BOX, "off") == ""
    _report(root, "Demo_v1", [_row(0, 0, "single")], mtime=1_000_000)
    sig = ph.signature(BOX)
    assert sig.startswith("_ph") and ph.signature(BOX) == sig
    _report(root, "Demo_v2", [_row(0, 0, "single")], mtime=2_000_000)
    assert ph.signature(BOX) not in ("", sig)
    assert ph.signature((51, 49, 51, 49)) == ""


def test_city_data_applies_heights_on_read(tmp_data_dir):
    """get_city_layers (preview, overlay and export all read through it) applies the reports."""
    from unittest.mock import patch

    from app.server.core import city_data
    from app.server.core.cache import osm_cache_key, write_osm_cache
    from city2stl.cache_policy import CITY_PIPELINE_VERSION

    root, _ = _roots()
    _report(root, "Demo_v1", [_row(0, 0, "corroborated", 41.0)])
    bbox = dict(north=BOX[0], south=BOX[1], east=BOX[2], west=BOX[3])
    payload = {"buildings": _fc(_feature(0, 0), _feature(0.003, 0.003)), "roads": _fc(),
               "city_pipeline_version": CITY_PIPELINE_VERSION}
    write_osm_cache(osm_cache_key(*BOX, 0.5, 5.0), payload, {**bbox, "tol": 0.5, "min_area": 5.0})

    def get(mode):
        with patch("app.server.core.city_data.fetch_osm_data", return_value={}):
            return city_data.get_city_layers(*BOX, ["buildings", "roads"], 0.5, 5.0,
                                             roof_shapes="off", panorama_heights=mode)

    out = get("auto")
    props = [f["properties"] for f in out["buildings"]["features"]]
    assert (props[0]["height_m"], props[0]["height_source"]) == (41.0, "panorama_corroborated")
    assert props[1]["height_source"] == "default"
    stats = out["panorama_heights"]
    assert stats["buildings"] == 1 and [r["name"] for r in stats["reports"]] == ["Demo_v1"]
    off = get("off")
    assert off["buildings"]["features"][0]["properties"]["height_source"] == "default"
    assert off["panorama_heights"]["mode"] == "off"
    any_tol, _ = city_data.read_city_layers_any_tolerance(*BOX)
    assert any_tol["buildings"]["features"][0]["properties"]["height_m"] == 41.0


def test_city_model_task_uses_the_same_function(client, monkeypatch):
    """The export applies heights after merging the client copy, through
    with_panorama_heights, and the report records it."""
    import app.server.core.city_model_task as task_mod
    from app.server.core.export_tasks import ExportTask

    root, _ = _roots()
    bbox = dict(north=37.19, south=37.172, east=-3.578, west=-3.605)
    x, y = -3.59, 37.18
    _report(root, "Granada_v1", [_row(x, y, "single", 27.0)], region="Granada",
            bbox=(bbox["north"], bbox["south"], bbox["east"], bbox["west"]))
    asked, seen = [], []
    monkeypatch.setattr(task_mod, "get_city_layers",
                        lambda *a, **k: asked.append(k.get("panorama_heights"))
                        or {"buildings": _fc(_feature(x, y))})
    real = task_mod.with_panorama_heights

    def spy(payload, *a):
        out = real(payload, *a)
        seen.append((a[-1], out["buildings"]["features"][0]["properties"],
                     out["panorama_heights"]))
        return out

    monkeypatch.setattr(task_mod, "with_panorama_heights", spy)
    r = client.get("/api/terrain/dem", params={**bbox, "dim": 60, "projection": "none"})
    body = {"format": "city", "dem_id": r.json()["dem_id"], "name": "t"}
    task_mod.run_city_model(dict(body), ExportTask("p1"))
    assert asked == ["off"]
    mode, props, stats = seen[-1]
    assert mode == "auto"
    assert (props["height_m"], props["height_source"]) == (27.0, "panorama_single")
    assert stats["buildings"] == 1
    task_mod.run_city_model({**body, "panorama_heights": "off"}, ExportTask("p2"))
    assert seen[-1][1]["height_source"] == "default"


def test_height_reports_endpoint(client):
    root, photos = _roots()
    _report(root, "Demo_v1", [_row(0, 0, "single")], mtime=1_000_000)
    _report(root, "Demo_v2", [_row(0, 0, "single"), _row(0.002, 0.002, "corroborated")],
            mtime=2_000_000)
    _photo_store(photos, "Demo", [(0.0002, 0.0002, 30.0)])
    q = dict(north=BOX[0], south=BOX[1], east=BOX[2], west=BOX[3])
    body = client.get("/api/cities/height-reports", params=q).json()
    assert [r["name"] for r in body["reports"]] == ["Demo_v2"]
    assert body["reports"][0]["rows"] == {"single": 1, "corroborated": 1}
    assert body["photo_readings"] == 1 and body["rows"] == 3
    every = client.get("/api/cities/height-reports", params={**q, "all": True}).json()
    assert [r["superseded_by"] for r in every["reports"]] == [None, "Demo_v2"]
    assert every["rows"] == 3


# ---------------------------------------------------------------- Phase 5 wiring: preflight, composite, session

def _seed_cache(tmp_data_dir):
    from app.server.core.cache import osm_cache_key, write_osm_cache
    from city2stl.cache_policy import CITY_PIPELINE_VERSION

    bbox = dict(north=BOX[0], south=BOX[1], east=BOX[2], west=BOX[3])
    payload = {"buildings": _fc(_feature(0, 0)), "city_pipeline_version": CITY_PIPELINE_VERSION}
    write_osm_cache(osm_cache_key(*BOX, 0.5, 5.0), payload, {**bbox, "tol": 0.5, "min_area": 5.0})
    return bbox


def test_preflight_cached_layers_honour_the_mode(tmp_data_dir):
    from app.server.core import preflight

    root, _ = _roots()
    _report(root, "Demo_v1", [_row(0, 0, "corroborated", 41.0)])
    bbox = _seed_cache(tmp_data_dir)
    data = {"simplify_tolerance": 0.5, "min_area": 5.0}

    def height(extra):
        have, _, _ = preflight._cached_layers(bbox, ["buildings"], {**data, **extra})
        return have["buildings"]["features"][0]["properties"]

    assert height({})["height_m"] == 41.0                                # default is auto
    assert height({"panorama_heights": "auto"})["height_source"] == "panorama_corroborated"
    assert height({"panorama_heights": "off"})["height_source"] == "default"
    assert height({"panorama_heights": "Demo_v1"})["height_m"] == 41.0   # a named report


def test_composite_raster_key_follows_the_signature(tmp_data_dir, monkeypatch):
    from app.server.routers import composite

    keys = []
    monkeypatch.setattr(composite, "read_array_cache", lambda ns, key: keys.append(key))
    monkeypatch.setattr(composite, "write_array_cache", lambda *a, **k: None)
    monkeypatch.setattr(composite, "_rasterize_city", lambda req: {
        n: [0.0] * 4 for n in (*composite._CITY_CHANNELS,)} | {"width": 2, "height": 2})

    def key(mode="auto"):
        req = composite.CompositeCityRasterRequest(
            north=BOX[0], south=BOX[1], east=BOX[2], west=BOX[3], width=2, height=2,
            panorama_heights=mode)
        composite._city_raster_arrays(req)
        return keys[-1]

    root, _ = _roots()
    before, off_before = key(), key("off")
    assert before == off_before                        # nothing covers the bbox: old keys valid
    _report(root, "Demo_v1", [_row(0, 0)], mtime=1_000_000)
    after = key()
    assert after != before and key("off") == off_before
    _report(root, "Demo_v2", [_row(0, 0)], mtime=2_000_000)
    assert key() != after


def test_composite_raster_passes_the_mode_to_the_reader(tmp_data_dir, monkeypatch):
    from app.server.routers import composite

    seen = []
    monkeypatch.setattr(composite, "read_array_cache", lambda *a: None)
    monkeypatch.setattr(composite, "write_array_cache", lambda *a, **k: None)
    monkeypatch.setattr(composite, "read_city_layers_any_tolerance",
                        lambda *a, **k: seen.append(k.get("panorama_heights")) or ({}, None))
    for mode in ("auto", "off"):
        composite._city_raster_arrays(composite.CompositeCityRasterRequest(
            north=BOX[0], south=BOX[1], east=BOX[2], west=BOX[3], width=2, height=2,
            panorama_heights=mode))
    assert seen == ["auto", "off"]


def test_osm_layer_source_forwards_the_option(monkeypatch):
    from app.server.routers import composite

    got = []
    monkeypatch.setattr(composite, "_city_raster_arrays",
                        lambda req: got.append(req.panorama_heights) or {
                            "buildings": __import__("numpy").zeros((2, 2))})
    provider = composite._make_city_layer_source("buildings")
    provider(*BOX, 2, {"panorama_heights": "off"})
    provider(*BOX, 2, {})
    assert got == ["off", "auto"]


def test_composite_dem_layers_carry_the_signature(tmp_data_dir):
    from app.server.routers.composite import _composite_cache_key, _with_panorama_signature
    from app.server.schemas import MergeLayerSpec

    specs = [MergeLayerSpec(source="local"), MergeLayerSpec(source="osm_buildings")]
    key = lambda s: _composite_cache_key(*BOX, 64, _with_panorama_signature(s, BOX))  # noqa: E731
    root, _ = _roots()
    off = [specs[0], MergeLayerSpec(source="osm_buildings", options={"panorama_heights": "off"})]
    k0, off0 = key(specs), key(off)
    _report(root, "Demo_v1", [_row(0, 0)], mtime=1_000_000)
    assert key(specs) != k0
    assert key(off) == off0                             # "off": a new report changes nothing
    only_local = [specs[0]]
    assert key(only_local) == _composite_cache_key(*BOX, 64, only_local)
    assert specs[1].options == {}                       # the caller's specs are not mutated


def test_terrain_session_sends_the_setting(monkeypatch):
    from app.session.terrain_session import TerrainSession

    s = TerrainSession.__new__(TerrainSession)
    s.settings = {"city": {"layers": ["buildings"], "simplify_tolerance": 0.5, "min_area": 5.0,
                           "panorama_heights": "off"},
                  "dem": {"dim": 64}, "projection": {"projection": "none", "clip_valid_region": True}}
    s.bbox = dict(north=0.5, south=0.4, east=0.5, west=0.4)
    s.city_data = {"buildings": {"features": []}}
    sent = []
    s._api_request = lambda method, ep, **kw: sent.append((ep, kw["json"])) or {
        "width": 1, "height": 1, "task_id": "t"}
    s.composite_city_raster()
    assert sent[-1][0] == "/api/composite/city-raster"
    assert sent[-1][1]["panorama_heights"] == "off"
    s.settings["city"].pop("panorama_heights")             # unset: the server default applies
    s.composite_city_raster()
    assert sent[-1][1]["panorama_heights"] == "auto"

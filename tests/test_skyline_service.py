"""F-UNIFY Phase 2: SkylineConfig precedence, run_region with the pipeline stubbed, and the
report sidecar (written by run_region, read by the listings, with the index.html fallback)."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from city2stl.skyline import config as c
from city2stl.skyline import report_index as ri
from city2stl.skyline.config import SkylineConfig
from geo2stl import google_usage as gu

# --------------------------------------------------------------------------- config


def test_precedence_defaults_site_env_overrides():
    assert SkylineConfig.build(None, site={}, env={}).cadastre_band is False
    site = {"use_cadastre_band": True, "render_pdf": True, "max_plausible_height_m": 450}
    cfg = SkylineConfig.build("x", site=site, env={})
    assert (cfg.region, cfg.cadastre_band, cfg.render_pdf, cfg.max_plausible_height_m) == (
        "x", True, True, 450.0)
    cfg = SkylineConfig.build("x", site=site, env={"SKYLINE_CADASTRE_BAND": "0"})
    assert cfg.cadastre_band is False                         # env beats the site file
    cfg = SkylineConfig.build("x", site=site, env={"SKYLINE_CADASTRE_BAND": "0"},
                              overrides={"cadastre_band": True, "cache_only": True})
    assert cfg.cadastre_band is True and cfg.cache_only is True  # the request beats env
    with pytest.raises(ValueError):
        SkylineConfig.build(None, site={}, env={}, overrides={"no_such_field": 1})


def test_optional_site_flags_apply_only_when_present():
    assert SkylineConfig.build(None, site={}, env={}).ownership is False
    assert SkylineConfig.build(None, site={"use_ownership": True}, env={}).ownership is True
    # "" keeps the site value; "0" turns it off
    assert SkylineConfig.build(None, site={"use_ownership": True},
                               env={"SKYLINE_OWNERSHIP": ""}).ownership is True
    assert SkylineConfig.build(None, site={"use_ownership": True},
                               env={"SKYLINE_OWNERSHIP": "0"}).ownership is False


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("on", True), ("TRUE", True), ("0", False), ("", False), ("maybe", False)])
def test_on_parser_matches_the_old_read(raw, expected):
    assert SkylineConfig.build(None, site={}, env={"SKYLINE_CV_F_SKY12": raw}).f_sky12 is expected


@pytest.mark.parametrize("env,field,expected", [
    ({"SKYLINE_TAG_FILTER": ""}, "tag_filter", True),             # "" counts as on
    ({"SKYLINE_TAG_FILTER": "off"}, "tag_filter", False),
    ({"SKYLINE_CADASTRE_BAND": ""}, "cadastre_band", False),      # "" counts as off
    ({"SKYLINE_PREFER_TAGS": "false"}, "prefer_tags", True),      # only "0" turns it off
    ({"SKYLINE_PREFER_TAGS": "0"}, "prefer_tags", False),
    ({"SKYLINE_CV_MULTIRES": "ON"}, "multires", False),           # compared without lower()
    ({"SKYLINE_CV_SEGFORMER_SIZE": "B5"}, "segformer_size", "b5"),
    ({"SKYLINE_CV_SEGFORMER_SIZE": "b9"}, "segformer_size", "b3"),
    ({"SKYLINE_CV_SEGFORMER_INPUT_SIZE": "64"}, "segformer_input_size", 128),
    ({"SKYLINE_CV_SEGFORMER_INPUT_SIZE": "x"}, "segformer_input_size", 512),
    ({"SKYLINE_CV_SEGFORMER_BATCH": "0"}, "segformer_batch", 1),
    ({"SKYLINE_CV_SEGFORMER_BATCH": "x"}, "segformer_batch", None),
    ({"SKYLINE_STAGE_CACHE": "rebuild"}, "stage_cache", "refresh"),
    ({"SKYLINE_STAGE_CACHE": "none"}, "stage_cache", "off"),
    ({"SKYLINE_LOCAL_DIR": "  "}, "local_dir", None),
    ({"MAP2STL_TRUTH_WORKERS": "x"}, "truth_workers", None),
    ({"SKYLINE_SURVEY_HEIGHTS": "junk"}, "survey_heights", False),  # keeps the site value
])
def test_env_parsers(env, field, expected):
    assert getattr(SkylineConfig.build(None, site={}, env=env), field) == expected


def test_survey_unrecognised_value_keeps_the_site_flag():
    cfg = SkylineConfig.build(None, site={"use_survey_heights": True},
                              env={"SKYLINE_SURVEY_HEIGHTS": "junk"})
    assert cfg.survey_heights is True


def test_current_follows_env_outside_a_run(monkeypatch):
    monkeypatch.setenv("SKYLINE_CV_F_SKY12", "1")
    assert c.current().f_sky12 is True
    monkeypatch.setenv("SKYLINE_CV_F_SKY12", "0")
    assert c.current().f_sky12 is False
    assert c.active() is None


def test_use_is_per_context_and_bind_carries_it():
    a = SkylineConfig.build(None, site={}, env={}, overrides={"f_sky12": True})
    b = SkylineConfig.build(None, site={}, env={}, overrides={"f_sky12": False})
    seen, barrier = {}, threading.Barrier(2)

    def job(name, cfg):
        with c.use(cfg):
            barrier.wait(5)                       # both runs active at once
            with ThreadPoolExecutor(2) as pool:   # a pool worker sees its job's config
                seen[name] = list(pool.map(gu.bind(lambda _: c.current().f_sky12), range(3)))

    ts = [threading.Thread(target=job, args=("a", a)), threading.Thread(target=job, args=("b", b))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert seen == {"a": [True] * 3, "b": [False] * 3}
    assert c.active() is None


def test_tally_counts_paid_and_cached_through_bind():
    with gu.tally() as t:
        gu.record("streetview_image", 2)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(gu.bind(lambda _: gu.note_cached("streetview_image")), range(5)))
    assert t == {"paid": {"streetview_image": 2}, "cached": {"streetview_image": 5}}
    gu.note_cached("streetview_image")            # outside a tally: not counted, no error


# --------------------------------------------------------------------------- run_region

_INDEX = """<html><body>
<div class="stats">
  <div class="stat"><div class="num">2</div>seeds</div>
  <div class="stat"><div class="num">7</div>aggregated buildings</div>
</div>
<table><tbody>
<tr><td><a href="seed_1.html">seed_1</a></td><td>+3.0</td><td>10</td><td>8</td><td>80%</td><td>6</td><td style="background:rgba(46,160,67,0.3)">good</td></tr>
<tr><td><a href="seed_2.html">seed_2</a></td><td>+0.0</td><td>4</td><td>1</td><td>25%</td><td>1</td><td style="background:rgba(214,40,40,0.3)">weak</td></tr>
</tbody></table></body></html>
"""


def _fake_pipeline(seen):
    def pipeline(region, out_pdf, *, seed_urls, explicit_api_key, bbox, trace, skip_pdf,
                 progress_cb):
        seen.update(region=region, paid=gu.paid_allowed(), cfg=c.current(), bbox=bbox,
                    skip_pdf=skip_pdf)
        if progress_cb:
            progress_cb("OSM fetch", None, None)
        gu.note_cached("streetview_image", 3)
        if gu.paid_allowed():
            gu.record("streetview_image", 1)
        else:
            gu.note_blocked("streetview_image", 4)
        d = out_pdf.parent / out_pdf.stem
        d.mkdir(parents=True)
        (d / "heights.json").write_text(json.dumps({
            "region": region, "bbox_nsew": [1, 0, 1, 0],
            "tier_counts": {"tag": 1},
            "buildings": [{"feature_id": "w1", "height_m": 30.0}]}), encoding="utf-8")
        (d / "index.html").write_text(_INDEX, encoding="utf-8")
        return {"region": region, "buildings": 1}
    return pipeline


def test_run_region_cache_only_counts_and_writes_the_sidecar(tmp_path):
    from city2stl.skyline import RegionResult, run_region
    seen = {}
    cfg = SkylineConfig.build("toy", site={}, env={}, overrides={"cache_only": True})
    labels = []
    res = run_region("toy", cfg, lambda label, d, t: labels.append(label),
                     output_pdf=tmp_path / "toy_skyline_report.pdf", skip_pdf=True,
                     pipeline=_fake_pipeline(seen))
    assert isinstance(res, RegionResult)
    assert seen["paid"] is False and seen["cfg"] is cfg and seen["skip_pdf"] is True
    assert labels == ["OSM fetch"]
    assert gu.paid_allowed() and c.active() is None          # both released afterwards
    assert res.summary == {"region": "toy", "buildings": 1}
    assert res.heights == [{"feature_id": "w1", "height_m": 30.0}]
    assert res.cost["paid_images"] == 0 and res.cost["cached_hits"] == 3
    assert res.cost["skipped_paid"] == {"streetview_image": 4}
    side = ri.read_sidecar(res.report_dir)
    assert side["schema"] == ri.SCHEMA and side["region"] == "toy"
    assert side["tier_counts"]["tag"] == 1 and side["n_buildings"] == 1
    assert side["stats"] == {"seeds": 2, "aggregated_buildings": 7}
    assert side["panoramas"] == ri.parse_pano_rows(_INDEX)
    assert side["cost"] == res.cost and side["config"]["cache_only"] is True
    assert side["paths"]["heights"] == "heights.json" and side["paths"]["pdf"] is None


def test_run_region_paid_and_bbox(tmp_path):
    import dataclasses

    from city2stl.skyline import run_region
    from city2stl.skyline.region_types import RegionBBox
    fields = {f.name for f in dataclasses.fields(RegionBBox)}
    vals = {k: v for k, v in dict(name="toy", north=1.0, south=0.0, east=1.0, west=0.0).items()
            if k in fields}
    bbox = RegionBBox(**vals)
    seen = {}
    res = run_region(bbox, SkylineConfig.build("toy", site={}, env={}),
                     output_pdf=tmp_path / "r.pdf", pipeline=_fake_pipeline(seen))
    assert seen["region"] == "toy" and seen["bbox"] is bbox and seen["paid"] is True
    assert res.cost["paid_images"] == 1 and res.cost["skipped_paid"] == {}


def test_run_region_propagates_a_cancel(tmp_path):
    from city2stl.skyline import run_region

    class Stop(Exception):
        pass

    def cb(*_):
        raise Stop

    with pytest.raises(Stop):
        run_region("toy", SkylineConfig.build("toy", site={}, env={}, overrides={
            "cache_only": True}), cb, output_pdf=tmp_path / "r.pdf",
            pipeline=_fake_pipeline({}))
    assert gu.paid_allowed() and c.active() is None


# --------------------------------------------------------------------------- sidecar readers

def test_read_sidecar_rejects_missing_stale_and_other_schema(tmp_path):
    assert ri.read_sidecar(tmp_path) is None
    ri.write_sidecar(tmp_path, {"region": "x"})
    assert ri.read_sidecar(tmp_path)["region"] == "x"
    (tmp_path / "heights.json").write_text("{}", encoding="utf-8")
    side = tmp_path / ri.SIDECAR
    os.utime(side, (side.stat().st_atime, (tmp_path / "heights.json").stat().st_mtime - 10))
    assert ri.read_sidecar(tmp_path) is None                  # heights rewritten after it
    side.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    assert ri.read_sidecar(tmp_path) is None
    side.write_text("not json", encoding="utf-8")
    assert ri.read_sidecar(tmp_path) is None


def _report(tmp_path, name, *, sidecar):
    d = tmp_path / name
    d.mkdir()
    (d / "heights.json").write_text(json.dumps({
        "region": "toy", "tier_counts": {"tag": 1},
        "buildings": [{"feature_id": "w1"}]}), encoding="utf-8")
    (d / "index.html").write_text(_INDEX, encoding="utf-8")
    if sidecar:
        ri.write_sidecar(d, {"region": "toy", "n_buildings": 5, "tier_counts": {"tag": 5},
                             "bbox_nsew": None, "stats": {"seeds": 9, "aggregated_buildings": 11},
                             "panoramas": ri.parse_pano_rows(_INDEX)[:1]})
    return d


def test_reports_router_reads_the_sidecar_else_index_html(tmp_path):
    from app.server.routers import reports
    legacy = reports._region_entry(_report(tmp_path, "old_skyline_report", sidecar=False))
    assert (legacy["seeds"], legacy["buildings"], len(legacy["rows"])) == (2, 7, 2)
    assert legacy["quality"] == {"good": 1, "medium": 0, "weak": 1}
    new = reports._region_entry(_report(tmp_path, "new_skyline_report", sidecar=True))
    assert (new["seeds"], new["buildings"], len(new["rows"])) == (9, 11, 1)
    assert new["rows"][0]["seed"] == "seed_1" and new["quality"]["good"] == 1


def test_heights_header_reads_the_sidecar_else_heights_json(tmp_path):
    from app.server.core import skyline_runs as sr
    old = sr._header(_report(tmp_path, "a", sidecar=False) / "heights.json")
    assert old["n_buildings"] == 1 and old["tier_counts"]["tag"] == 1
    new = sr._header(_report(tmp_path, "b", sidecar=True) / "heights.json")
    assert new["n_buildings"] == 5 and new["tier_counts"] == {"tag": 5}

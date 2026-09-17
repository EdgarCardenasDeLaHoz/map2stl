"""Tests for the /reports pipeline results browser (app/server/routers/reports.py).

The inventory is built by scanning directories, so every test points ``_ROOTS`` at a temporary
tree it builds itself rather than at the real ``runs/region_reports``: the checked-in reports come
and go with each batch, and a test that asserts on them would be asserting on whatever was last
run locally.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.server.routers import reports as reports_router
from app.server.server import app


#: One row of a rendered region index.html, in the shape build_landing_page's regex expects.
def _row(seed: str, detected: int, matched: int, rate: str, coverage: int,
         bg: str, label: str) -> str:
    return (f'<tr><td><a href="seed_{seed}.html">{seed}</a></td>'
            f'<td>+1.0</td><td>{detected}</td><td>{matched}</td>'
            f'<td>{rate}</td><td>{coverage}</td>'
            f'<td style="background:{bg},0.3)">{label}</td></tr>')


def _write_region(root, name: str, rows: str, seeds: int = 2,
                  buildings: int = 42) -> object:
    d = root / f"{name}_skyline_report"
    (d / "assets" / "pano").mkdir(parents=True)
    (d / "assets" / "minimap").mkdir(parents=True)
    (d / "assets" / "views").mkdir(parents=True)
    (d / "index.html").write_text(
        f'<div class="num">{seeds}</div>seeds'
        f'<div class="num">{buildings}</div>aggregated buildings'
        f"<table><tbody>{rows}</tbody></table>", encoding="utf-8")
    return d


@pytest.fixture()
def report_tree(tmp_path, monkeypatch):
    """A miniature runs/region_reports plus a height report and a trace."""
    regions = tmp_path / "region_reports"
    heights = tmp_path / "height_reports"
    traces = tmp_path / "height_traces"
    for p in (regions, heights, traces):
        p.mkdir()

    rows = (_row("seed_1", 40, 30, "75%", 30, "rgba(46,160,67", "good")
            + _row("auto_090_1400m", 8, 2, "25%", 2, "rgba(214,40,40", "weak (no detection)"))
    d = _write_region(regions, "testville", rows)
    for name in ("seed_1.html", "seed_auto_090_1400m.html"):
        (d / name).write_text("<h1>seed</h1>", encoding="utf-8")
    for name in ("1_pano.png", "1_pano_seg.png", "1_pano_recon.png"):
        (d / "assets" / "pano" / name).write_bytes(b"\x89PNG")
    for name in ("1.png", "1_polar_fp.png"):
        (d / "assets" / "minimap" / name).write_bytes(b"\x89PNG")
    for name in ("1_view_0.png", "1_view_0_mask.png", "1_view_1.png"):
        (d / "assets" / "views" / name).write_bytes(b"\x89PNG")
    (d / "heights.json").write_text(json.dumps({
        "region": "Testville",
        "n_building_records": 9,
        "known_heights": [],
        "buildings": [
            {"effective_height_m": 10.0, "effective_height_source": "geometric"},
            {"effective_height_m": 30.0, "effective_height_source": "geometric"},
            {"effective_height_m": 50.0, "effective_height_source": "osm_tag"},
        ],
    }), encoding="utf-8")

    (heights / "testville_height_report.html").write_text("<h1>heights</h1>", encoding="utf-8")
    (traces / "Testville__all.json").write_text('{"region": "Testville"}', encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not reachable", encoding="utf-8")

    monkeypatch.setattr(reports_router, "_ROOTS", {
        "region": regions, "height": heights, "trace": traces})
    return tmp_path


@pytest.fixture()
def client(report_tree):
    return TestClient(app)


class TestInventory:
    def test_lists_regions_height_reports_and_traces(self, client):
        data = client.get("/api/reports/index").json()
        assert [r["name"] for r in data["regions"]] == ["testville"]
        assert [h["name"] for h in data["height_reports"]] == ["testville"]
        assert [t["region"] for t in data["traces"]] == ["Testville"]

    def test_totals_count_seeds_by_quality(self, client):
        totals = client.get("/api/reports/index").json()["totals"]
        assert totals["regions"] == 1
        assert totals["seeds"] == 2
        assert (totals["good"], totals["medium"], totals["weak"]) == (1, 0, 1)
        assert totals["buildings"] == 42

    def test_seed_rows_carry_their_own_artifact_urls(self, client):
        region = client.get("/api/reports/index").json()["regions"][0]
        row = next(r for r in region["rows"] if r["seed"] == "seed_1")
        assert row["quality"] == "good"
        assert row["detected"] == 40 and row["coverage"] == 30
        assert row["url"].endswith("/testville_skyline_report/seed_seed_1.html")
        # Slug drops the leading "seed_", matching how html_report.py names assets.
        assert row["slug"] == "1"
        assert set(row["pano"]) == {"pano", "pano_seg", "pano_recon"}
        assert [m["label"] for m in row["minimaps"]] == ["minimap", "polar footprints"]
        assert [v["index"] for v in row["views"]] == [0, 1]
        assert "mask" in row["views"][0] and "mask" not in row["views"][1]

    def test_a_region_with_no_rendered_index_still_appears(self, tmp_path, client,
                                                           report_tree):
        (report_tree / "region_reports" / "half_done_skyline_report").mkdir()
        data = client.get("/api/reports/index").json()
        entry = next(r for r in data["regions"] if r["name"] == "half_done")
        assert entry["index_url"] is None
        assert entry["rows"] == []

    def test_empty_roots_are_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reports_router, "_ROOTS", {
            "region": tmp_path / "nope", "height": tmp_path / "nope",
            "trace": tmp_path / "nope"})
        data = TestClient(app).get("/api/reports/index").json()
        assert data["totals"]["regions"] == 0
        assert data["legacy_landing_url"] is None


class TestHeightsSummary:
    def test_summarises_without_returning_every_building(self, client):
        data = client.get(
            "/api/reports/heights/testville_skyline_report").json()
        assert data["region"] == "Testville"
        assert data["n_buildings"] == 3
        assert data["height_sources"] == {"geometric": 2, "osm_tag": 1}
        assert data["height_median"] == 30.0
        assert data["height_max"] == 50.0
        assert "buildings" not in data

    def test_missing_heights_json_is_a_404(self, client):
        assert client.get(
            "/api/reports/heights/nosuch_skyline_report").status_code == 404


class TestFileServing:
    def test_serves_a_report_asset(self, client):
        res = client.get(
            "/reports/files/region/testville_skyline_report/assets/pano/1_pano.png")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"

    @pytest.mark.parametrize("rel", [
        "../secret.txt",
        "testville_skyline_report/../../secret.txt",
        "%2e%2e/secret.txt",
    ])
    def test_refuses_to_escape_its_root(self, client, rel):
        res = client.get(f"/reports/files/region/{rel}")
        assert res.status_code == 404
        assert "not reachable" not in res.text

    def test_unknown_root_is_a_404(self, client):
        assert client.get("/reports/files/bogus/x.png").status_code == 404

    def test_directories_are_not_served(self, client):
        assert client.get(
            "/reports/files/region/testville_skyline_report").status_code == 404

    def test_only_viewable_extensions_are_served(self, client, report_tree):
        d = report_tree / "region_reports" / "testville_skyline_report"
        (d / "model.stl").write_bytes(b"solid")
        res = client.get("/reports/files/region/testville_skyline_report/model.stl")
        assert res.status_code == 404


class TestPage:
    def test_reports_page_loads_the_browser_script(self):
        res = TestClient(app).get("/reports")
        assert res.status_code == 200
        assert "/static/js/reports.js" in res.text

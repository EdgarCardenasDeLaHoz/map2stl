"""Verification tiers in the reports (F-SKY26 step 2f): ``tier_display``, the region HTML index
(headline, tier table, drone pano rows), the PDF heights page and the /reports heights API."""

from __future__ import annotations

import json
from types import SimpleNamespace

import matplotlib
import pytest

matplotlib.use("Agg")

from city2stl.skyline import tier_display as td  # noqa: E402
from city2stl.skyline.html_report import (  # noqa: E402
    _segments_table_html,
    render_region_index,
)
from city2stl.skyline.report_index import parse_pano_rows  # noqa: E402

SQUARE = [[-75.55, 10.40], [-75.5499, 10.40], [-75.5499, 10.4001], [-75.55, 10.4001],
          [-75.55, 10.40]]


def _row(fid, tier, h, **kw):
    r = {"feature_id": fid, "name": fid, "tier": tier, "effective_height_m": h,
         "effective_height_source": kw.pop("src", "withheld:elevated"), "n_seeds": 1,
         "tier_methods": kw.pop("methods", []), "footprint_lonlat": SQUARE}
    r.update(kw)
    return r


@pytest.fixture
def rows():
    return [
        _row("b1", "verified_2", 202.0, src="osm_tag", n_seeds=2,
             methods=["drone:seed_1", "drone:seed_6"],
             per_seed_median_m={"seed_1": 195.0, "seed_6": 199.0, "auto_090": 150.0},
             seed_disagreement_m=49.0, satellite={"shadow": [179.2, 0.62]},
             height_tag_m=202.0, height_source="osm_tag"),
        _row("b2", "tag", 190.0, src="osm_tag", methods=["osm_tag"]),
        _row("b3", "single", 233.0, methods=["drone:seed_1", "lean"], prior_disagrees=True,
             per_seed_median_m={"seed_1": 233.0}),
        _row("b4", "prior", 20.0, src="withheld:prior_gbm", methods=["prior_gbm"],
             single_reading_m=98.4, withheld_reason="single over 2x prior"),
    ]


class TestTierDisplay:
    def test_counts_use_the_stored_counts_and_add_unlabelled(self, rows):
        assert td.tier_counts(rows) == {"survey": 0, "verified_2": 1, "tag": 1, "single": 1,
                                        "prior": 1}
        assert td.tier_counts(rows + [{"feature_id": "old"}])["unlabelled"] == 1
        assert td.tier_counts([], {"verified_2": 5, "prior": 2})["verified_2"] == 5

    def test_hover_lists_methods_and_prior_disagreement(self, rows):
        assert "drone:seed_1, lean" in td.tier_hover(rows[2])
        assert "2x away from the prior" in td.tier_hover(rows[2])

    def test_withheld_note_only_on_withheld_rows(self, rows):
        assert td.withheld_note(rows[3]) == (
            "Reading of 98 m withheld (single over 2x prior); prior published")
        assert td.withheld_note(rows[2]) is None

    def test_unverified_tiers(self, rows):
        assert [td.is_unverified(r) for r in rows] == [False, True, True, True]

    def test_survey_attributions_follow_the_providers(self):
        rows = [{"tier": "survey", "survey_provider": "usgs_3dep"},
                {"tier": "survey", "effective_height_source": "survey:cnig_mdsn"},
                {"tier": "tag", "survey_provider": "ign_lidarhd"}]
        assert td.survey_providers(rows) == {"usgs_3dep": 1, "cnig_mdsn": 1}
        lines = td.survey_attributions(td.survey_providers(rows))
        assert any("Public domain" in x for x in lines)
        assert any("CC BY 4.0" in x and "CNIG" in x for x in lines)
        assert td.survey_attributions(["ign_lidarhd"])[0].endswith("Etalab 2.0.")

    def test_every_survey_provider_has_an_attribution(self):
        from city2stl.height.providers.survey import PROVIDERS
        assert set(PROVIDERS) <= set(td.SURVEY_ATTRIBUTIONS)

    def test_unmeasured_tagged_records_become_tag_rows(self, rows):
        recs = [SimpleNamespace(feature_id="b9", name="Tower", height_source="osm_tag",
                                height_tag_m=88.0),
                SimpleNamespace(feature_id="b1", name="b1", height_source="osm_tag",
                                height_tag_m=202.0),
                SimpleNamespace(feature_id="b8", name="x", height_source=None,
                                height_tag_m=None)]
        out = td.with_unmeasured_tags(rows, recs)
        assert [r["feature_id"] for r in out[len(rows):]] == ["b9"]
        assert out[-1]["tier"] == "tag" and out[-1]["measured"] is False

    def test_drone_disagreement_ignores_street_seeds(self, rows):
        seeds = td.drone_seeds(rows)
        assert seeds == {"seed_1", "seed_6"}
        assert td.drone_disagreements(rows, seeds) == [4.0]


class TestRegionIndex:
    def test_headline_comes_first_and_timing_last(self, rows):
        known = [{"name": "Hotel Estelar", "ctbuh_m": 202.0, "matched_id": "b1"},
                 {"name": "Lost", "ctbuh_m": 150.0, "matched_id": "nope"}]
        page = render_region_index("cartagena", [], rows, known_heights=known,
                                   tier_map_rel="assets/tiers_map.png",
                                   step_timings=[("Multiview registration", 10.0)])
        i_answer = page.index("Answer: published heights")
        assert i_answer < page.index("<h2>Seeds</h2>") < page.index("Pipeline timing")
        assert "<details class=\"timing\">" in page
        assert "Hotel Estelar" in page and "seed_1 195, seed_6 199" in page
        assert "shadow 179" in page and "no published row" in page
        assert 'src="assets/tiers_map.png"' in page
        assert "1 of 4</b> published heights are verified" in page

    def test_table_has_tier_hover_unverified_and_withheld_note(self, rows):
        page = render_region_index("cartagena", [], rows)
        assert 'title="Single reading: one kind of image reading' in page
        assert page.count("<span class=unv>unverified</span>") == 3
        assert "Reading of 98 m withheld (single over 2x prior); prior published" in page
        assert "more than 2x the prior" in page
        assert 'id="tier-filter"' in page

    def test_drone_and_street_disagreement_are_labelled_apart(self, rows):
        page = render_region_index("cartagena", [], rows)
        assert "Drone seeds only (trusted readings that reached the published value): " \
               "median 4.0 m" in page
        assert "All seeds, street level and drone mixed: median 49.0 m" in page

    def test_survey_licence_line_only_with_survey_rows(self, rows):
        assert "Public domain" not in render_region_index("x", [], rows)
        survey = rows + [_row("b5", "survey", 40.0, src="survey:usgs_3dep",
                              survey_provider="usgs_3dep")]
        assert "USGS 3D Elevation Program (3DEP) lidar. Public domain." in render_region_index(
            "x", [], survey)

    def test_pages_set_a_dark_scheme_background(self, rows):
        page = render_region_index("x", [], rows)
        assert "prefers-color-scheme: dark" in page
        assert "body { background: var(--bg); color: var(--ink); }" in page

    def test_drone_pano_row_has_no_match_rate_and_still_parses(self, rows):
        seg = {"height_src": "footprint", "matched_projection": {"feature_id": "b1"}}
        drone = SimpleNamespace(seed_name="seed_1", n_matched=2, n_segments=2,
                                bearing_shift_deg=0.0, matched_segments=[seg, dict(seg)])
        street = SimpleNamespace(seed_name="auto_090", n_matched=12, n_segments=15,
                                 bearing_shift_deg=3.0,
                                 matched_segments=[{"matched_projection": {"feature_id": "b1"}}])
        page = render_region_index("x", [], rows, pano_results=[drone, street])
        parsed = {r["seed"]: r for r in parse_pano_rows(page)}
        assert parsed["seed_1"]["match_rate"] == "n/a"
        assert parsed["seed_1"]["quality_label"] == "drone — too few tags"
        assert parsed["auto_090"]["match_rate"] == "80%"
        assert "<td>drone</td>" in page and "<td>street</td>" in page

    def test_old_pano_rows_still_parse(self):
        old = ('<tr><td><a href="seed_1.html">seed_1</a></td><td>+1.0</td><td>9</td>'
               '<td>6</td><td>67%</td><td>5</td>'
               '<td style="background:rgba(230,180,40,0.3)">medium</td></tr>')
        assert parse_pano_rows(old)[0]["quality"] == "medium"


class TestSeedPageReadings:
    def test_reading_says_whether_it_was_used(self, rows):
        segs = [{"seed_index": 1, "height_m": 38.0, "base_visible": True,
                 "matched_projection": {"feature_id": "b1", "name": "Nautica",
                                        "distance_m": 300.0}},
                {"seed_index": 2, "height_m": 233.0, "behind": True,
                 "matched_projection": {"feature_id": "b3", "distance_m": 500.0}}]
        pr = SimpleNamespace(seed_name="seed_4", seed_lat=10.4, seed_lon=-75.55,
                             matched_segments=segs)
        sv = SimpleNamespace(seed_lat=10.4, seed_lon=-75.55, seed_name="seed_4")
        html = _segments_table_html(sv, pr, {r["feature_id"]: r for r in rows})
        assert "published m" in html and "Verified (2 sources)" in html
        assert '<td class="unused">no</td><td>202</td>' in html
        assert "(tower behind)" in html
        # without published rows the table keeps its old columns
        assert "published m" not in _segments_table_html(sv, pr)


class TestPdfHeightsPage:
    def test_page_draws_tier_map_and_licence(self, rows, tmp_path):
        from matplotlib.backends.backend_pdf import PdfPages

        from city2stl.skyline._region_render._pages import (
            _render_heights_page,
            render_tier_map_png,
        )
        survey = rows + [_row("b5", "survey", 40.0, survey_provider="cuzk_dmp")]
        texts = []

        class Spy:
            def savefig(self, fig, **kw):
                texts.extend(t.get_text() for t in fig.texts)
                for ax in fig.axes:
                    texts.extend(t.get_text() for t in ax.texts)
                    if ax.get_legend():
                        texts.extend(t.get_text() for t in ax.get_legend().get_texts())
                pdf.savefig(fig, **kw)

        with PdfPages(tmp_path / "h.pdf") as pdf:
            _render_heights_page(Spy(), survey, None, pano_only=True)
        joined = "\n".join(texts)
        assert "Verified (2 sources) (1)" in joined and "Survey lidar (1)" in joined
        assert "ČÚZK" in joined
        assert "verified: 2 (40 %)" in joined
        assert (tmp_path / "h.pdf").stat().st_size > 0
        assert render_tier_map_png(tmp_path / "m.png", rows)
        assert not render_tier_map_png(tmp_path / "none.png", [{"tier": "tag"}])


class TestReportsHeightsApi:
    def test_returns_tier_counts_and_survey_info(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from app.server.routers import reports as reports_router
        from app.server.server import app

        d = tmp_path / "region_reports" / "x_skyline_report"
        d.mkdir(parents=True)
        (d / "heights.json").write_text(json.dumps({
            "schema_version": 2, "region": "x", "known_heights": [{"name": "T"}],
            "tier_counts": {"survey": 1, "verified_2": 2, "tag": 0, "single": 0, "prior": 1},
            "buildings": [
                {"tier": "survey", "survey_provider": "rediam_mdhn", "effective_height_m": 30},
                {"tier": "verified_2", "effective_height_m": 50},
                {"tier": "verified_2", "effective_height_m": 60},
                {"tier": "prior", "effective_height_m": 9, "single_reading_m": 40.0,
                 "withheld_reason": "single over 2x prior"}]}), encoding="utf-8")
        monkeypatch.setattr(reports_router, "_ROOTS", {
            "region": tmp_path / "region_reports", "height": tmp_path / "h",
            "trace": tmp_path / "t"})
        data = TestClient(app).get("/api/reports/heights/x_skyline_report").json()
        assert data["tier_counts"]["verified_2"] == 2
        assert data["n_verified"] == 3 and data["n_withheld"] == 1
        assert data["n_known_heights"] == 1
        assert data["tiers"]["single"]["color"] == "#D55E00"
        assert data["survey"]["providers"] == {"rediam_mdhn": 1}
        assert "REDIAM" in data["survey"]["attributions"][0]

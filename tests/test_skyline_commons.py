"""F-WEB2: Commons skyline photo finder, offline (canned API pages)."""

import math

import pytest

from city2stl.skyline import commons_photos as cp

MIAMI = (25.8257, 25.7301, -80.1366, -80.2428)


def _page(title="File:A.jpg", w=4032, h=3024, mime="image/jpeg", coords=(25.77, -80.19),
          f35=26, heading="29500/100", ref="T", taken="2023:08:31 10:15:00"):
    md = [{"name": "FocalLengthIn35mmFilm", "value": f35},
          {"name": "GPSImgDirection", "value": heading},
          {"name": "GPSImgDirectionRef", "value": ref},
          {"name": "DateTimeOriginal", "value": taken}]
    return {
        "title": title,
        "coordinates": [{"lat": coords[0], "lon": coords[1]}] if coords else [],
        "imageinfo": [{"mime": mime, "width": w, "height": h,
                       "thumburl": "https://upload.example/thumb.jpg",
                       "descriptionurl": "https://commons.example/A",
                       "metadata": [m for m in md if m["value"] is not None],
                       "extmetadata": {"Artist": {"value": "<a href='x'>Ann</a>"},
                                       "LicenseShortName": {"value": "CC BY 2.0"}}}],
    }


def test_hfov_from_35mm_equivalent():
    # 26 mm-equivalent phone main camera, 4:3 landscape: ~67 deg horizontal
    assert cp.hfov_from_35mm(26, 4032, 3024) == pytest.approx(67.3, abs=0.5)
    # the same lens in portrait sees less horizontally
    assert cp.hfov_from_35mm(26, 3024, 4032) < cp.hfov_from_35mm(26, 4032, 3024)
    # 3:2 at 36 mm-wide full frame: 2*atan(18/f)
    assert cp.hfov_from_35mm(50, 3000, 2000) == pytest.approx(2 * math.degrees(math.atan(18 / 50)),
                                                              abs=0.05)


@pytest.mark.parametrize("raw,val", [("29500/100", 295.0), (12.5, 12.5), ("7", 7.0), ("1/0", None),
                                     ("abc", None), (None, None)])
def test_exif_numbers(raw, val):
    assert cp._num(raw) == val


def test_photo_from_page_reads_pose_and_credit():
    p = cp.photo_from_page(_page())
    assert (p.lat, p.lon) == (25.77, -80.19)
    assert p.heading_deg == pytest.approx(295.0) and p.heading_ref == "T"
    assert p.hfov_deg == pytest.approx(67.3, abs=0.5)
    assert p.year == 2023 and p.hour == 10
    assert p.attribution.startswith("Ann, CC BY 2.0")
    assert cp.photo_from_page(_page(mime="image/png")) is None


@pytest.mark.parametrize("kw,why", [
    ({}, None),
    ({"coords": None}, "no camera location"),
    ({"coords": (26.1, -80.19)}, "camera far from region"),
    ({"w": 12000, "h": 3000}, "panorama wider than 3:1"),
    ({"w": 3024, "h": 4032}, "portrait"),
    ({"taken": "2023:08:31 21:40:00"}, None),  # EXIF hour no longer decides night
    ({"coords": (25.70, -80.17)}, None),  # a few km off the bbox (offshore) is fine
])
def test_usable(kw, why):
    assert cp.usable(cp.photo_from_page(_page(**kw)), MIAMI) == why


def test_ranking_prefers_new_then_compass():
    old = cp.photo_from_page(_page(title="File:old.jpg", taken="2019:05:01 12:00:00"))
    new_nocompass = cp.photo_from_page(_page(title="File:n.jpg", heading=None, ref=None))
    new = cp.photo_from_page(_page(title="File:best.jpg"))
    ranked = sorted([old, new_nocompass, new], key=cp.rank_key)
    assert [p.title for p in ranked] == ["File:best.jpg", "File:n.jpg", "File:old.jpg"]


def test_find_skyline_photos_end_to_end(monkeypatch):
    calls = []

    def fake_query(session, **params):
        calls.append(params)
        if params.get("list") == "search":
            return {"query": {"search": [{"title": "Category:Miami, Florida skyline in the 2020s"},
                                         {"title": "Category:Houston skyline in the 2010s"}]}}
        if params.get("list") == "categorymembers":
            return {"query": {"categorymembers": [{"ns": 6, "title": "File:A.jpg"},
                                                  {"ns": 6, "title": "File:B.jpg"},
                                                  {"ns": 14, "title": "Category:Miami streets"}]}}
        return {"query": {"pages": [_page("File:A.jpg"), _page("File:B.jpg", coords=None)]}}

    monkeypatch.setattr(cp, "_query", fake_query)
    got = cp.find_skyline_photos("Miami", MIAMI, session=object())
    assert [p.title for p in got] == ["File:A.jpg"]
    cats = [c["cmtitle"] for c in calls if c.get("list") == "categorymembers"]
    assert cats == ["Category:Miami, Florida skyline in the 2020s"]  # no Houston, no street subcat


def test_is_dark_reads_the_sky_not_the_clock():
    import numpy as np
    day = np.full((90, 120, 3), 200, np.uint8)
    night = np.full((90, 120, 3), 20, np.uint8)
    night[60:] = 120                       # lit buildings and water at the bottom
    assert not cp.is_dark(day) and cp.is_dark(night)


def test_deep_blue_day_sky_is_not_dark():
    import numpy as np
    blue = np.zeros((90, 120, 3), np.uint8)
    blue[...] = (40, 90, 170)              # Bridgemiami's noon sky: luma ~ 84, value 170
    storm = np.zeros((90, 120, 3), np.uint8)
    storm[...] = (100, 70, 40)             # orange storm-lit night: luma ~ 75, value 100
    assert not cp.is_dark(blue) and cp.is_dark(storm)


# --------------------------------------------------------------------------- pipeline screen
# User review of 38 rejected photos (2026-10-07): 25 were usable skylines.


def _row(**kw):
    row = {"title": "File:A.jpg", "width": 4000, "height": 3000, "lat": 25.77, "lon": -80.19,
           "hfov_deg": 30.0, "px": [1600, 1200], "quality": {"score": 0.2}, "sky_value": 200.0}
    row.update(kw)
    return row


@pytest.mark.parametrize("kw,status", [
    ({}, cp.FIT),
    ({"width": 11020, "height": 1814}, cp.FIT),                     # wide pano: cylindrical fit
    ({"lat": None, "lon": None}, cp.PLACEMENT),                     # unlocated: placement queue
    ({"lat": 37.59, "lon": -0.97}, cp.PLACEMENT),                   # geotag of another city
    ({"lat": 25.75, "lon": -80.40}, cp.FIT),                        # 16 km out: a far shore
    ({"lat": 25.45, "lon": -80.20}, cp.REJECT),                     # 30 km out
    ({"width": 3000, "height": 4000}, cp.REJECT),                   # portrait
    ({"px": None}, cp.REJECT),                                      # never outlined
    ({"sky_value": 120.0, "quality": {"score": 0.05}}, cp.REJECT),  # murky dusk
    ({"sky_value": 60.0, "quality": {"score": 0.25}}, cp.FIT),      # clear night skyline
    ({"sky_value": 200.0, "quality": {"score": 0.05}}, cp.FIT),     # day: not gated on clarity
])
def test_pipeline_status(kw, status):
    row = _row(**kw)
    if row["px"] is None:
        row.pop("px")
    assert cp.pipeline_status(row, MIAMI)[0] == status


def test_fit_prior_by_photo_kind():
    assert cp.fit_prior(_row()) == (30.0, cp.EXIF_FOV_SPAN, "pinhole")
    fov, span, proj = cp.fit_prior(_row(hfov_deg=None))
    assert proj == "pinhole" and span > cp.EXIF_FOV_SPAN and fov == cp.DEFAULT_HFOV_DEG
    fov, span, proj = cp.fit_prior(_row(width=11020, height=1814))   # EXIF is one frame's
    assert proj == "cylindrical" and 150 < fov <= 360 and span == cp.PANO_FOV_SPAN


def test_skyline_quality_edge_and_coverage():
    np = pytest.importorskip("numpy")
    h, w = 120, 160
    y = np.full(w, 60.0)
    clear = np.zeros((h, w, 3), np.uint8)
    clear[60:] = 200                                  # lit towers below a black sky
    murky = np.full((h, w, 3), 90, np.uint8)
    murky[60:] = 100                                  # dusk haze: barely an edge
    q_clear, q_murky = (cp.skyline_quality(im, y, h) for im in (clear, murky))
    assert q_clear["score"] > cp.MIN_SKYLINE_QUALITY > q_murky["score"]
    half = y.copy()
    half[: w // 2] = np.nan                           # half the skyline lost
    assert cp.skyline_quality(clear, half, h)["coverage"] == pytest.approx(0.5)
    # the outline may come from a larger frame than the cached image
    assert cp.skyline_quality(clear, y * 2, 2 * h)["score"] == pytest.approx(q_clear["score"])


def test_pipeline_reuses_only_unchanged_outcomes(monkeypatch):
    import importlib

    pp = importlib.import_module("city2stl.skyline.scripts.17_photo_pipeline")
    monkeypatch.setattr(pp, "_REUSE", {
        "p1": {"key": "p1", "title": "A.jpg", "route": "search", "gate_ok": False},
        "p2": {"key": "p2", "title": "File:B.jpg", "route": "recorded", "gate_ok": False},
        "p3": {"key": "p3", "title": "File:C.jpg", "route": "recorded", "gate_ok": True},
    })

    def job(key, title, route, **kw):
        return {"meta": {"key": key, "title": title}, "route": route, **kw}

    assert pp._reusable(job("p1", "File:A.jpg", "search")) is not None
    assert pp._reusable(job("p1", "File:X.jpg", "search")) is None       # keys renumbered
    assert pp._reusable(job("p2", "File:B.jpg", "recorded")) is None     # failed: FOV retry now
    assert pp._reusable(job("p3", "File:C.jpg", "recorded")) is not None
    assert pp._reusable(job("p3", "File:C.jpg", "recorded", projection="cylindrical")) is None
    assert pp._reusable(job("p4", "File:D.jpg", "recorded")) is None     # newly reaches the fit

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
    ({"coords": (26.0, -80.19)}, "camera far from region"),
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

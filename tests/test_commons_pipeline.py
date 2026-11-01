"""The Commons photo pipeline stages (``city2stl.skyline.commons_pipeline``), offline: synthetic
photos, fake HTTP sessions and a temporary cache folder (``SKYLINE_LOCAL_DIR``)."""

import io
import json
from argparse import ArgumentParser
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from city2stl.skyline import commons_photos as cp
from city2stl.skyline.commons_pipeline import cache, embed, groups, pipeline, profiles


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYLINE_LOCAL_DIR", str(tmp_path))
    return tmp_path


def _jpeg(w=64, h=48) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.full((h, w, 3), 120, np.uint8)).save(buf, "JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status=200, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Answers ``get`` from a list of statuses (the last one repeats)."""

    def __init__(self, statuses=(200,), content=b""):
        self.statuses, self.content, self.calls = list(statuses), content, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return FakeResponse(st, self.content)


def _photo(title="File:a.jpg", w=800, h=600, lat=25.77, lon=-80.19) -> cp.CommonsPhoto:
    return cp.CommonsPhoto(title=title, url="https://x/thumb/a/1600px-a.jpg", page_url="https://x/p",
                           width=w, height=h, lat=lat, lon=lon, heading_deg=90.0, heading_ref="T",
                           hfov_deg=60.0, taken="2020:01:01 12:00:00")


# ---- cache ----------------------------------------------------------------------------------

def test_thumb_url_swaps_the_width():
    u = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/1600px-X.jpg"
    assert cache.thumb_url(u).endswith("/500px-X.jpg")
    assert cache.thumb_url(u, 330).endswith("/330px-X.jpg")
    assert cache.thumb_url("https://x/orig.jpg") == "https://x/orig.jpg"       # nothing to swap


def test_get_with_retry_waits_out_429(monkeypatch):
    sleeps = []
    monkeypatch.setattr(cache.time, "sleep", sleeps.append)
    S = FakeSession([429, 429, 200])
    assert cache.get_with_retry(S, "u").status_code == 200
    assert sleeps == [4, 8] and len(S.calls) == 3
    S = FakeSession([429])                                  # never clears: the last answer is returned
    assert cache.get_with_retry(S, "u", tries=2).status_code == 429


def test_profile_files_round_trip(local):
    assert cache.read_profiles("r") == [] and cache.outlined("r") == []
    d = cache.cache_dir("r")
    d.mkdir(parents=True)
    meta = [{"key": "p0", "px": [10, 5]}, {"key": "p1", "skip": "portrait"}]
    cache.save_profiles(d, meta, {"p0": np.arange(10, dtype=np.float32)})
    m2, arrs = cache.load_profiles("r")
    assert m2 == meta and list(arrs) == ["p0"]
    assert [m["key"] for m in cache.outlined("r")] == ["p0"]


# ---- stage 1: profiles ----------------------------------------------------------------------

def test_screen_row_keeps_metadata_and_the_reason():
    row = profiles.screen_row(_photo(), (26.0, 25.0, -80.0, -81.0), "p3")
    assert row["key"] == "p3" and row["title"] == "File:a.jpg" and "notes" not in row
    assert row["why_not_usable"] is None
    far = profiles.screen_row(_photo(lat=40.0), (26.0, 25.0, -80.0, -81.0), "p4")
    assert far["why_not_usable"]                              # outside the region


def _masks(img):
    sky = np.zeros(img.shape[:2], bool)
    sky[: img.shape[0] // 2] = True
    return sky, ~sky


def test_outline_into_fills_the_row_and_the_array(monkeypatch):
    monkeypatch.setattr(cp, "skyline_quality", lambda img, y, h: {"score": 0.5, "coverage": 1.0})
    row, arrs = {"key": "p0", "skip": "dark"}, {}
    profiles.outline_into(row, np.full((40, 80, 3), 90, np.uint8), arrs, _masks)
    assert "skip" not in row and row["px"] == [row["px"][0], row["px"][1]]
    assert row["coverage"] == 1.0 and row["quality"]["score"] == 0.5
    assert arrs["p0"].dtype == np.float32 and len(arrs["p0"]) == row["px"][0]
    failed = {"key": "p1"}
    profiles.outline_into(failed, np.zeros((4, 4, 3), np.uint8), arrs, lambda img: (None, None))
    assert failed["skip"] == "segmentation failed" and "p1" not in arrs


def test_fetch_and_outline_records_http_failure():
    row = {"key": "p0"}
    profiles.fetch_and_outline(FakeSession([404]), row, "u", {}, _masks)
    assert row["skip"] == "http 404"


def test_profile_region_skips_portraits_and_cached_titles(local, monkeypatch):
    portrait, wide, again = _photo("File:p.jpg", w=300, h=600), _photo("File:w.jpg"), _photo("File:c.jpg")
    monkeypatch.setattr(profiles, "load_site", lambda r: {"north": 26.0, "south": 25.0, "east": -80.0, "west": -81.0})
    monkeypatch.setattr(profiles, "region_photos", lambda S, region, extra=(): [portrait, wide, again])
    monkeypatch.setattr(profiles, "get_with_retry", lambda S, url, **k: FakeResponse(200, _jpeg()))
    monkeypatch.setattr(profiles.time, "sleep", lambda s: None)
    monkeypatch.setattr(cp, "skyline_quality", lambda img, y, h: {"score": 0.5, "coverage": 1.0})
    d = cache.cache_dir("r")
    d.mkdir(parents=True)
    cache.save_profiles(d, [{"title": "File:c.jpg", "key": "p0", "px": [8, 4], "lat": 25.7}], {"p0": np.zeros(8, np.float32)})
    meta, arrs = profiles.profile_region("r", _masks)
    assert [m["title"] for m in meta] == ["File:c.jpg", "File:p.jpg", "File:w.jpg"]
    assert meta[1]["skip"] == "portrait" and "px" in meta[2] and meta[2]["key"] == "p2"
    assert set(arrs) == {"p0", "p2"}
    assert [m["key"] for m in cache.read_profiles("r")] == ["p0", "p1", "p2"]      # saved


# ---- stage 2: groups ------------------------------------------------------------------------

def _skyline(seed, w=200):
    rng = np.random.default_rng(seed)
    y = np.full(w, 200.0)
    for x0 in range(0, w, 25):
        y[x0:x0 + 25] = 200.0 - rng.integers(20, 150)
    return y.astype(np.float32)


def test_group_photos_links_unlocated_to_located():
    meta = [{"key": "a", "lat": 1.0}, {"key": "b", "lat": None}, {"key": "c", "lat": None},
            {"key": "d", "lat": None}]
    pairs = [("a", "b", 0.03, 1.0, 0.0, 1.0), ("c", "d", 0.04, 1.0, 0.0, 1.0),
             ("a", "c", 0.30, 1.0, 0.0, 1.0)]
    links, grp = groups.group_photos(meta, pairs, 0.07)
    assert links == {"b": ("a", 0.03)}
    assert [sorted(g) for g in grp] == [["c", "d"]]
    links, grp = groups.group_photos(meta, pairs, 0.02)          # a stricter threshold links nothing
    assert links == {} and grp == []


def test_score_pairs_finds_the_same_outline_twice(local):
    d = cache.cache_dir("r")
    d.mkdir(parents=True)
    a, c = _skyline(1), _skyline(2)
    meta = [{"key": "p0", "px": [200, 220], "lat": None}, {"key": "p1", "px": [200, 220], "lat": None},
            {"key": "p2", "px": [200, 220], "lat": None}]
    np.savez_compressed(d / "profiles.npz", p0=a, p1=a, p2=c)
    pairs = groups.score_pairs(d, meta, workers=1)
    best = min(pairs, key=lambda p: p[2])
    assert best[:2] == ("p0", "p1") and best[2] < 0.01
    assert all(p[2] <= groups.PAIR_MAX_MISFIT for p in pairs)


def test_camera_distance_check_uses_located_pairs_only():
    by = {"a": {"lat": 10.0, "lon": 20.0}, "b": {"lat": 10.0, "lon": 20.0},
          "c": {"lat": None, "lon": None}}
    out = groups.camera_distance_check([("a", "b", 0.05, 1, 0, 1), ("a", "c", 0.05, 1, 0, 1)], by)
    assert out == [(0.05, 0.0)]


def test_write_groups_and_gallery_region(local):
    d = cache.cache_dir("r")
    d.mkdir(parents=True)
    rows = [{"key": "p0", "px": [8, 4], "lat": 25.7, "lon": -80.2, "title": "File:Located.jpg",
             "url": "https://x/thumb/a/1600px-a.jpg", "year": 2020, "hfov_deg": 60.0},
            {"key": "p1", "px": [8, 4], "lat": None, "lon": None, "title": "File:Unlocated.jpg",
             "url": "https://x/thumb/b/1600px-b.jpg", "year": None, "hfov_deg": None}]
    (d / "profiles.json").write_text(json.dumps(rows), encoding="utf-8")
    (d / "pairs.json").write_text(json.dumps([["p0", "p1", 0.03, 1.0, 0.0, 1.0]]), encoding="utf-8")
    out = groups.gallery_region("r", 0.07, root=local / "root")
    page = out.read_text(encoding="utf-8")
    assert out == local / "root" / "runs" / "commons_review" / "r_groups.html"
    assert "Unlocated.jpg" in page and "330px-a.jpg" in page and "1 of 1 unlocated photos" in page
    g = json.loads((d / "groups.json").read_text(encoding="utf-8"))
    assert g["links"] == {"p1": {"located": "p0", "misfit": 0.03}} and g["groups"] == []


# ---- stage 3: embeddings --------------------------------------------------------------------

def test_cache_thumbnails_downloads_each_photo_once(tmp_path):
    meta = [{"key": "p0", "url": "https://x/thumb/a/1600px-a.jpg"},
            {"key": "p1", "url": "https://x/thumb/b/1600px-b.jpg"}]
    (tmp_path / "p0.jpg").write_bytes(b"cached")
    S = FakeSession([200], b"new")
    assert embed.cache_thumbnails(S, meta, tmp_path) == 1
    assert S.calls == ["https://x/thumb/b/500px-b.jpg"]
    assert (tmp_path / "p0.jpg").read_bytes() == b"cached" and (tmp_path / "p1.jpg").read_bytes() == b"new"


def test_cache_thumbnails_falls_back_to_the_original(tmp_path):
    S = FakeSession([404, 200], b"orig")                    # a small original refuses 500 px
    embed.cache_thumbnails(S, [{"key": "p0", "url": "https://x/thumb/a/1600px-a.jpg"}], tmp_path)
    assert S.calls == ["https://x/thumb/a/500px-a.jpg", "https://x/thumb/a/1600px-a.jpg"]


# ---- stage 5: pipeline arguments ------------------------------------------------------------

def test_pipeline_cli_defaults_come_from_the_dataclass():
    ap = ArgumentParser()
    pipeline.add_arguments(ap)
    a = ap.parse_args(["--region", "miami", "--report", "rep"])
    ref = pipeline.PipelineArgs("miami", Path("rep"))
    assert vars(a) == {k: getattr(ref, k) for k in vars(a)} and set(vars(a)) == set(vars(ref))
    assert (a.max_misfit, a.min_margin, a.min_coverage, a.workers) == (0.3, 0.1, 0.25, 8)
    assert a.h_cams == [2.0, 30.0, 60.0] and a.rescue_anchor_dev == 20.0 and a.max_anchor_dev is None
    b = ap.parse_args(["--region", "x", "--report", "r", "--h-cams", "2", "9", "--untagged",
                       "--top", "median", "--reuse", "old.json", "--min-towers", "6"])
    assert b.h_cams == [2.0, 9.0] and b.untagged and b.top == "median" and b.min_towers == 6
    assert b.reuse == Path("old.json")
    with pytest.raises(SystemExit):
        ap.parse_args(["--region", "x", "--report", "r", "--top", "bogus"])

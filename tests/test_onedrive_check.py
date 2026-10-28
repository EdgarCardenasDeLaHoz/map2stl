"""Tests for app.server.core.onedrive_check (OneDrive conflict copies in the source tree)."""
from app.server.core.onedrive_check import CONFLICT_RE, find_onedrive_problems


def test_conflict_names():
    assert CONFLICT_RE.search("osm-DESKTOP-K0K0EAO.py")
    assert CONFLICT_RE.search("README-DESKTOP-K0K0EAO-2.md")
    assert CONFLICT_RE.search("lastfailed-DESKTOP-TMJM22C")
    assert not CONFLICT_RE.search("projection-default.js")
    assert not CONFLICT_RE.search("osm.py")


def test_finds_conflicts_and_skips_caches(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "osm.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "osm-DESKTOP-K0K0EAO.py").write_text("x = 0\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a-DESKTOP-K0K0EAO.js").write_text("")
    conflicts, cloud_only = find_onedrive_problems([tmp_path, tmp_path / "missing"])
    assert [p.name for p in conflicts] == ["osm-DESKTOP-K0K0EAO.py"]
    assert cloud_only == []

"""DEM handles (core/dem_store.py): export by id instead of by re-derived cache key."""

import numpy as np
import pytest

import app.server.core.dem_store as dem_store_mod
from app.server.core.dem_store import DemGone, DemStore

_BBOX = {"north": 40.01, "south": 40.0, "east": -75.0, "west": -75.01}


@pytest.fixture(autouse=True)
def _store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dem_store_mod, "STORE_DIR", tmp_path / "dem_handles")


class TestDemStore:
    def test_round_trip(self):
        store = DemStore()
        grid = np.arange(12, dtype=np.float32).reshape(3, 4)
        dem_id = store.put(grid, _BBOX)
        got, bbox = store.get(dem_id)
        np.testing.assert_array_equal(got, grid)
        assert bbox == _BBOX

    def test_unknown_id_raises(self):
        with pytest.raises(DemGone, match="reload the DEM"):
            DemStore().get("nope")

    def test_expired_id_raises_and_is_dropped(self, monkeypatch):
        store = DemStore()
        dem_id = store.put(np.zeros((2, 2)), _BBOX)
        monkeypatch.setattr(dem_store_mod, "TTL_SECONDS", -1)
        with pytest.raises(DemGone):
            store.get(dem_id)
        monkeypatch.setattr(dem_store_mod, "TTL_SECONDS", 3600)
        with pytest.raises(DemGone):   # dropped, not merely hidden
            store.get(dem_id)

    def test_lru_spills_to_disk_and_reloads(self, monkeypatch):
        monkeypatch.setattr(dem_store_mod, "MEMORY_LIMIT", 1)
        store = DemStore()
        first = store.put(np.full((2, 2), 1.0), _BBOX)
        store.put(np.full((2, 2), 2.0), _BBOX)           # evicts `first` to disk
        assert store._entries[first].array is None
        assert (dem_store_mod.STORE_DIR / f"{first}.npy").exists()
        got, _ = store.get(first)
        assert float(got[0, 0]) == 1.0


class TestExportByHandle:
    def _load_dem(self, client):
        r = client.get("/api/terrain/dem", params={**_BBOX, "dim": 40, "projection": "none"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("dem_id"), "DEM response must carry a handle"
        return body

    def test_dem_response_carries_handle(self, client):
        self._load_dem(client)

    def test_export_stl_by_handle_only(self, client):
        dem_id = self._load_dem(client)["dem_id"]
        # No bbox, no settings: the handle alone identifies the grid.
        r = client.post("/api/export/stl", json={"dem_id": dem_id, "name": "t"})
        assert r.status_code == 200, r.text
        assert len(r.content) > 84   # binary STL header + at least one triangle

    def test_unknown_handle_is_410(self, client):
        r = client.post("/api/export/stl", json={"dem_id": "0000000000000000"})
        assert r.status_code == 410
        assert "reload the DEM" in r.json()["error"]

    def test_stale_handle_falls_back_to_settings(self, client):
        """After a restart the handle is gone but the disk cache still has the grid."""
        body = self._load_dem(client)
        r = client.post("/api/export/stl", json={
            "dem_id": "0000000000000000",
            "bbox": _BBOX,
            "dem": {"dim": 40, "projection": "none"},
        })
        assert r.status_code == 200, r.text
        assert body["dem_id"] != "0000000000000000"

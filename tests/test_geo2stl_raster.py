"""geo2stl.raster.read_geotiff — the one GeoTIFF bytes/path → array reader."""
from __future__ import annotations

import io

import numpy as np
import pytest

from geo2stl.raster import read_geotiff

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_bounds  # noqa: E402


def _tiff_bytes(arr: np.ndarray, nodata=None) -> bytes:
    buf = io.BytesIO()
    h, w = arr.shape
    with rasterio.open(
        buf, "w", driver="GTiff", height=h, width=w, count=1, dtype=arr.dtype,
        crs="EPSG:4326", transform=from_bounds(10, 40, 11, 41, w, h), nodata=nodata,
    ) as dst:
        dst.write(arr, 1)
    return buf.getvalue()


def test_bytes_nodata_transform_crs():
    arr = np.array([[1, 2], [-9999, 4]], dtype=np.int16)
    out, transform, crs, nodata = read_geotiff(_tiff_bytes(arr, nodata=-9999))
    assert out.dtype == np.float32
    assert np.isnan(out[1, 0]) and out[0, 1] == 2
    assert nodata == -9999
    assert crs == "EPSG:4326"
    assert transform.c == pytest.approx(10.0) and transform.f == pytest.approx(41.0)


def test_path_zero_as_nodata_and_fit_dim(tmp_path):
    arr = np.arange(8 * 4, dtype=np.float32).reshape(8, 4)
    path = tmp_path / "t.tif"
    path.write_bytes(_tiff_bytes(arr))
    out = read_geotiff(path, zero_as_nodata=True)[0]
    assert np.isnan(out[0, 0]) and out[0, 1] == 1
    fitted = read_geotiff(path, fit_dim=16, dtype=np.float64)[0]
    assert fitted.shape == (16, 8) and fitted.dtype == np.float64


def test_read_decodes_on_every_core_with_identical_values(tmp_path, monkeypatch):
    """GDAL_NUM_THREADS=ALL_CPUS for the read only (F-SPEED #5A); same values
    as a plain single-threaded rasterio read, resampled or not."""
    from rasterio.enums import Resampling

    rng = np.random.default_rng(3)
    arr = rng.uniform(-50, 3000, (300, 500)).astype(np.float32)
    path = tmp_path / "tiled.tif"
    with rasterio.open(path, "w", driver="GTiff", height=300, width=500, count=1,
                       dtype="float32", crs="EPSG:4326", tiled=True, blockxsize=64,
                       blockysize=64, compress="deflate",
                       transform=from_bounds(10, 40, 11, 41, 500, 300)) as dst:
        dst.write(arr, 1)

    seen = []
    real_open = rasterio.open

    def spy(*a, **k):
        seen.append(rasterio.env.getenv().get("GDAL_NUM_THREADS"))
        return real_open(*a, **k)
    monkeypatch.setattr(rasterio, "open", spy)
    full = read_geotiff(path)[0]
    fitted = read_geotiff(path, fit_dim=200, dtype=np.float64)[0]
    monkeypatch.setattr(rasterio, "open", real_open)
    assert seen == ["ALL_CPUS", "ALL_CPUS"]
    assert not rasterio.env.hasenv() or "GDAL_NUM_THREADS" not in rasterio.env.getenv()

    with real_open(path) as src:
        ref_full = src.read(1)
        ref_fit = src.read(1, out_shape=(120, 200), resampling=Resampling.bilinear)
    assert full.tobytes() == ref_full.tobytes()
    assert fitted.tobytes() == ref_fit.astype(np.float64).tobytes()


def test_undecodable_raises():
    with pytest.raises(ValueError):
        read_geotiff(b"not a geotiff")

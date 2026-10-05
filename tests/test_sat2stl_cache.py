"""geo2stl.sat2stl.fetch_bbox_image: a finer cached image serves a coarser request.

Earth Engine is stubbed: ``_ee_download`` is replaced by a fake that counts calls
and writes the cache the way the real one does.
"""

import numpy as np
import pytest

import geo2stl.sat2stl as sat

BBOX = (40.0, 39.9, -75.1, -75.2)


@pytest.fixture
def fake_ee(tmp_path, monkeypatch):
    monkeypatch.setenv("MAP2STL_TEST_MODE", "0")   # the real cache path, not the zeros stub
    monkeypatch.setattr(sat, "CACHE_DIR", tmp_path)
    calls = []

    def download(N, S, E, W, scale, dataset, use_cache, cache_path, meta_path):
        calls.append(scale)
        n = int(round(1200 / scale))                    # a 1.2 km square, scale m/px
        if dataset == "esa":
            arr = np.full((n, n), 10, np.uint8)
            arr[:, n // 2:] = 80                         # two classes, left / right
        else:
            arr = np.linspace(0, 1000, n * n, dtype=np.float32).reshape(n, n).astype(np.int16)
        if use_cache:
            sat._write_ee_cache(arr, N, S, E, W, scale, dataset, cache_path, meta_path)
        return arr

    monkeypatch.setattr(sat, "_ee_download", download)
    return calls


def test_coarser_request_is_resampled_from_finer_cache(fake_ee):
    fine = sat.fetch_bbox_image(*BBOX, scale=10, dataset="copernicus")
    coarse = sat.fetch_bbox_image(*BBOX, scale=40, dataset="copernicus")
    assert fake_ee == [10]                               # no second download
    assert coarse.shape == (30, 30) and fine.shape == (120, 120)
    assert coarse.dtype == np.int16
    assert abs(float(coarse.mean()) - float(fine.mean())) < 5    # area-averaged


def test_finer_request_still_downloads(fake_ee):
    sat.fetch_bbox_image(*BBOX, scale=40, dataset="copernicus")
    sat.fetch_bbox_image(*BBOX, scale=10, dataset="copernicus")
    assert fake_ee == [40, 10]


def test_coarsest_finer_entry_is_used(fake_ee, monkeypatch):
    sat.fetch_bbox_image(*BBOX, scale=20, dataset="copernicus")
    sat.fetch_bbox_image(*BBOX, scale=10, dataset="copernicus")   # finer: downloads
    used = []
    real = sat._resample_to_scale
    monkeypatch.setattr(sat, "_resample_to_scale",
                        lambda arr, src, dst, **k: used.append(src) or real(arr, src, dst, **k))
    sat.fetch_bbox_image(*BBOX, scale=40, dataset="copernicus")
    assert fake_ee == [20, 10]
    assert used == [20.0]                                # least resampling


def test_other_bbox_or_dataset_is_not_reused(fake_ee):
    sat.fetch_bbox_image(*BBOX, scale=10, dataset="copernicus")
    sat.fetch_bbox_image(40.0, 39.9, -75.1, -75.3, scale=40, dataset="copernicus")
    sat.fetch_bbox_image(*BBOX, scale=40, dataset="nasadem")
    assert fake_ee == [10, 40, 40]


def test_categorical_classes_are_not_blended(fake_ee):
    sat.fetch_bbox_image(*BBOX, scale=10, dataset="esa")
    coarse = sat.fetch_bbox_image(*BBOX, scale=40, dataset="esa")
    assert fake_ee == [10]
    assert set(np.unique(coarse)) <= {10, 80}           # nearest: no in-between codes


def test_use_cache_false_downloads(fake_ee):
    sat.fetch_bbox_image(*BBOX, scale=10, dataset="copernicus")
    sat.fetch_bbox_image(*BBOX, scale=40, dataset="copernicus", use_cache=False)
    assert fake_ee == [10, 40]



def test_resample_keeps_float_values():
    """Area-averaged float data (elevation) keeps its fraction; integer data is rounded."""
    from geo2stl.sat2stl import _resample_to_scale
    f = np.array([[0.25, 0.75], [0.25, 0.75]], dtype=np.float32)
    out = _resample_to_scale(f, 10.0, 20.0, categorical=False)
    assert out.shape[:2] == (1, 1) and out.dtype == np.float32
    assert float(out.ravel()[0]) == pytest.approx(0.5)
    i = np.array([[1, 2], [1, 2]], dtype=np.uint8)
    assert _resample_to_scale(i, 10.0, 20.0, categorical=False).ravel()[0] in (1, 2)

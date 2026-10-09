"""Surveyed nDSM providers (city2stl/height/providers: _survey, survey, ign_lidarhd,
cuzk_dmp, cnig_mdsn, rediam_mdhn, lidar_3dep_copc/ept). No network: every
endpoint is replaced by a GeoTIFF built here in the shape the service answers
(checked live 2026-09-27, see each module's docstring)."""

import io

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_bounds

import geo2stl.cache as cache_mod
from city2stl.height.providers import (
    _survey,
    cnig_mdsn,
    cuzk_dmp,
    ign_lidarhd,
    lidar_3dep_copc,
    lidar_3dep_ept,
    rediam_mdhn,
    survey,
)

PARIS = (48.8540, 48.8520, 2.3510, 2.3480)
PRAGUE = (50.0915, 50.0900, 14.4015, 14.3990)
GRANADA = (37.1772, 37.1758, -3.5985, -3.6005)
CARTAGENA = (37.6006, 37.5988, -0.9845, -0.9875)
BOSTON = (42.3601, 42.3590, -71.0570, -71.0585)


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)


def _tiff(arr, transform, crs="EPSG:4326", nodata=None, dtype="float32", count=1):
    buf = io.BytesIO()
    with rasterio.MemoryFile() as mem:
        with mem.open(driver="GTiff", width=arr.shape[-1], height=arr.shape[-2], count=count,
                      dtype=dtype, crs=crs, transform=transform, nodata=nodata) as ds:
            ds.write(arr.astype(dtype) if count > 1 else arr.astype(dtype)[None])
        buf.write(mem.read())
    return buf.getvalue()


def _grid_tiff(bbox, h, w, values, nodata=None):
    n, s, e, wst = bbox
    return _tiff(values, from_bounds(wst, s, e, n, w, h), nodata=nodata)


class TestGrid:
    def test_lonlat_grid_covers_bbox(self):
        h, w, t = _survey.lonlat_grid(PARIS, 0.5)
        assert (h, w) == (446, 440)
        assert t * (0, 0) == pytest.approx((PARIS[3], PARIS[0]))
        assert t * (w, h) == pytest.approx((PARIS[2], PARIS[1]))

    def test_oversize_request_refused(self):
        with pytest.raises(_survey.SurveyError, match="limit"):
            _survey.lonlat_grid((38, 37, -3, -4), 1.0)

    def test_dict_bbox(self):
        assert _survey.as_nsew({"north": 2, "south": 1, "east": 4, "west": 3}) == (2, 1, 4, 3)

    def test_clean_heights(self):
        out = _survey.clean_heights(np.array([-9999, -1, 3, 500, np.nan]))
        np.testing.assert_array_equal(np.isnan(out), [True, False, False, True, True])
        assert out[1] == 0 and out[2] == 3

    def test_picture_is_refused(self):
        rgb = _tiff(np.zeros((3, 4, 4)), from_bounds(0, 0, 1, 1, 4, 4), dtype="uint8", count=3)
        with pytest.raises(_survey.SurveyError, match="picture"):
            _survey.read_geotiff_array(rgb, "x")
        with pytest.raises(_survey.SurveyError, match="expected a GeoTIFF"):
            _survey.read_geotiff_array(b"<ServiceException>no</ServiceException>", "x")


class TestIGN:
    def test_fetch_params_nodata_and_cache(self, monkeypatch):
        calls = []

        def fake_get(url, params, context, timeout=90.0):
            calls.append(params)
            h, w = params["HEIGHT"], params["WIDTH"]
            vals = np.full((h, w), 12.5, np.float32)
            vals[0, 0] = -9999
            return _grid_tiff(PARIS, h, w, vals, nodata=-9999)

        monkeypatch.setattr(ign_lidarhd, "http_get", fake_get)
        arr, t = ign_lidarhd.ndsm_for_bbox(PARIS)
        p = calls[0]
        assert p["LAYERS"].endswith("WGS84G") and p["CRS"] == "EPSG:4326"
        assert p["BBOX"] == f"{PARIS[1]},{PARIS[3]},{PARIS[0]},{PARIS[2]}"     # lat first
        assert arr.shape == (446, 440) and np.isnan(arr[0, 0]) and arr[5, 5] == 12.5
        again, _ = ign_lidarhd.ndsm_for_bbox(PARIS)
        assert len(calls) == 1 and np.array_equal(np.isnan(again), np.isnan(arr))

    def test_outside_and_uncovered(self, monkeypatch):
        assert ign_lidarhd.ndsm_for_bbox(PRAGUE) is None
        monkeypatch.setattr(ign_lidarhd, "http_get", lambda url, params, ctx, timeout=90.0: _grid_tiff(
            PARIS, params["HEIGHT"], params["WIDTH"], np.full((params["HEIGHT"], params["WIDTH"]), -9999.0),
            nodata=-9999))
        assert ign_lidarhd.ndsm_for_bbox(PARIS) is None       # not flown yet: all nodata

    def test_http_error(self, monkeypatch):
        class R:
            status_code = 503
            text = "maintenance"
        monkeypatch.setattr("requests.get", lambda *a, **k: R())
        with pytest.raises(_survey.SurveyError, match="HTTP 503"):
            ign_lidarhd.ndsm_for_bbox(PARIS)


class TestCUZK:
    def test_surface_minus_ground(self, monkeypatch):
        seen = []

        def fake_get(url, params, context, timeout=90.0):
            seen.append((url, params))
            w, h = (int(v) for v in params["size"].split(","))
            base = 225.0 if "dmr5g" in url else 250.0
            return _grid_tiff(PRAGUE, h, w, np.full((h, w), base, np.float32))

        monkeypatch.setattr(cuzk_dmp, "http_get", fake_get)
        arr, _ = cuzk_dmp.ndsm_for_bbox(PRAGUE, 2.0)
        assert {u.split("/")[-3] for u, _ in seen} == {"dmp1g", "dmr5g"}
        assert seen[0][1]["bboxSR"] == 4326 and seen[0][1]["pixelType"] == "F32"
        assert np.nanmax(arr) == pytest.approx(25.0)

    def test_outside(self):
        assert cuzk_dmp.ndsm_for_bbox(PARIS) is None

    def test_square_pixel_answer_is_placed_where_the_server_says(self, monkeypatch):
        # The ImageServer keeps pixels square in degrees: asked wd x h over PRAGUE it returns
        # wd x h over a taller extent centred on the bbox. A roof in the northern quarter
        # of the bbox must stay there (it was read 0.56 x its offset further north).
        n, s, e, w = PRAGUE
        roof_lat = s + 0.75 * (n - s)

        def fake_get(url, params, context, timeout=90.0):
            wd, h = (int(v) for v in params["size"].split(","))
            px = (e - w) / wd                       # square pixels, extent grown in latitude
            mid = (n + s) / 2
            t = from_bounds(w, mid - h * px / 2, e, mid + h * px / 2, wd, h)
            rows, cols = np.mgrid[0:h, 0:wd]
            _, lat = t * (cols + 0.5, rows + 0.5)
            base = 225.0 if "dmr5g" in url else 225.0 + 30.0 * (np.abs(lat - roof_lat) < 2e-5)
            return _tiff(np.broadcast_to(base, (h, wd)).astype(np.float32), t)

        monkeypatch.setattr(cuzk_dmp, "http_get", fake_get)
        arr, t = cuzk_dmp.ndsm_for_bbox(PRAGUE, 1.0)
        rows = np.where(np.nanmax(arr, axis=1) > 15)[0]
        _, lat = t * (0.5, rows.mean() + 0.5)
        assert lat == pytest.approx(roof_lat, abs=2e-5)


class TestCNIG:
    def test_wcs_subset_and_warp(self, monkeypatch):
        from pyproj import Transformer
        to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25830", always_xy=True)
        x0, y0 = to_utm.transform(CARTAGENA[3], CARTAGENA[1])
        x1, y1 = to_utm.transform(CARTAGENA[2], CARTAGENA[0])
        seen = []

        def fake_get(url, params, context, timeout=90.0):
            seen.append(params)
            h, w = 100, 120
            vals = np.full((h, w), 30, np.int16)
            vals[:, : w // 2] = 0                           # west half ground
            return _tiff(vals, from_bounds(x0 - 10, y0 - 10, x1 + 10, y1 + 10, w, h),
                         crs="EPSG:25830", dtype="int16")

        monkeypatch.setattr(cnig_mdsn, "http_get", fake_get)
        arr, t = cnig_mdsn.ndsm_for_bbox(CARTAGENA)
        p = dict((k, v) for k, v in seen[0] if k != "SUBSET")
        assert p["COVERAGEID"] == "mdsn_e025"
        assert [v for k, v in seen[0] if k == "SUBSET"][0].startswith("x(")
        # West half 0, east half 30 m after the warp to lon/lat.
        assert np.nanmean(arr[:, :5]) == pytest.approx(0, abs=0.5)
        assert np.nanmean(arr[:, -5:]) == pytest.approx(30, abs=0.5)

    def test_canaries_covered_paris_not(self):
        assert cnig_mdsn.covers((28.2, 28.1, -16.2, -16.3))
        assert not cnig_mdsn.covers(PARIS)


class TestREDIAM:
    def test_window_is_warped(self, monkeypatch):
        from pyproj import Transformer
        to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25830", always_xy=True)
        x0, y0 = to_utm.transform(GRANADA[3] - 0.0002, GRANADA[1] - 0.0002)
        x1, y1 = to_utm.transform(GRANADA[2] + 0.0002, GRANADA[0] + 0.0002)
        w, h = int(x1 - x0), int(y1 - y0)
        vals = np.full((h, w), 8.0, np.float32)
        vals[: h // 2] = -9999                                # north half: void
        vals[-3:, :] = 255                                    # the nodata tag

        monkeypatch.setattr(rediam_mdhn, "_read_window",
                            lambda bbox: (vals.copy(), from_bounds(x0, y0, x1, y1, w, h), "EPSG:25830"))
        arr, _ = rediam_mdhn.ndsm_for_bbox(GRANADA)
        assert np.isnan(arr[:5]).all()
        assert np.nanmax(arr) == pytest.approx(8.0)

    def test_outside(self):
        assert rediam_mdhn.ndsm_for_bbox(PRAGUE) is None


class TestUSGS:
    def _fake_tile(self, monkeypatch):
        from pyproj import CRS
        monkeypatch.setattr(lidar_3dep_copc, "available", lambda: True)
        monkeypatch.setattr(lidar_3dep_copc, "_search_items", lambda bbox: [{"id": "t1"}])
        monkeypatch.setattr(lidar_3dep_copc, "_item_crs", lambda item: (CRS.from_epsg(4326), 1.0, 1.0))

        def pts(item, bounds, spacing, v_unit, tokens):
            n, s, e, w = BOSTON
            rng = np.random.default_rng(0)
            lon = rng.uniform(w - 0.0001, e + 0.0001, 20000)
            lat = rng.uniform(s - 0.0001, n + 0.0001, 20000)
            roof = (np.abs(lon - (e + w) / 2) < 0.0003) & (np.abs(lat - (n + s) / 2) < 0.0003)
            cls = np.where(roof, 6, 2)
            z = np.where(roof, 30.0, 10.0)
            return lon, lat, z, cls

        monkeypatch.setattr(lidar_3dep_copc, "_query_points", pts)

    def test_copc_grid(self, monkeypatch):
        self._fake_tile(monkeypatch)
        arr, t = lidar_3dep_copc.ndsm_for_bbox(BOSTON, 2.0)
        h, w = arr.shape
        assert arr[h // 2, w // 2] == pytest.approx(20.0, abs=0.5)
        assert np.nanmin(arr) == pytest.approx(0.0, abs=0.5)

    def test_registry_prefers_copc(self, monkeypatch):
        self._fake_tile(monkeypatch)
        arr, _ = survey.ndsm_for_bbox("usgs_3dep", BOSTON, 2.0)
        assert np.nanmax(arr) == pytest.approx(20.0, abs=0.5)

    def test_no_tiles(self, monkeypatch):
        monkeypatch.setattr(lidar_3dep_copc, "available", lambda: True)
        monkeypatch.setattr(lidar_3dep_copc, "_search_items", lambda bbox: [])
        assert lidar_3dep_copc.ndsm_for_bbox(BOSTON) is None
        assert lidar_3dep_copc.ndsm_for_bbox(PARIS) is None

    def test_ept_wrapper_flips_north_up(self, monkeypatch):
        grid = np.zeros((4, 4), np.float32)
        grid[0, :] = 7.0                                      # row 0 = south in get_ndsm
        monkeypatch.setattr(lidar_3dep_ept, "_deps", lambda: True)
        monkeypatch.setattr(lidar_3dep_ept, "get_ndsm", lambda bbox, resolution=512: grid)
        arr, t = lidar_3dep_ept.ndsm_for_bbox(BOSTON, 30.0)
        assert (arr[-1] == 7.0).all() and isinstance(t, Affine)


class TestRegistry:
    def test_available_for_bbox(self):
        got = {s["name"]: s for s in survey.available_for_bbox(GRANADA)}
        assert got["rediam_mdhn"]["available"] and got["cnig_mdsn"]["available"]
        assert not got["ign_lidarhd"]["available"] and not got["cuzk_dmp"]["available"]
        assert list(got) == list(survey.PROVIDERS)

    def test_unknown_provider(self):
        with pytest.raises(KeyError, match="unknown nDSM provider"):
            survey.ndsm_for_bbox("nope", PARIS)

    def test_native_resolution_default(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(ign_lidarhd, "ndsm_for_bbox", lambda bbox, res: seen.setdefault("r", res))
        survey.ndsm_for_bbox("ign_lidarhd", PARIS)
        assert seen["r"] == 0.5


class TestEptLaspy:
    """USGS EPT read with laspy (F-SKYBENCH): project choice and the octree walk, offline."""

    def test_project_year_takes_latest(self):
        from city2stl.height.providers.lidar_3dep_ept_laspy import _project_year
        assert _project_year("USGS_LPC_IL_4County_Cook_2017_LAS_2019") == 2019
        assert _project_year("MA_NE_CMGP_Sandy_Z19_A1_2015") == 2015
        assert _project_year("NoYearHere") == 0

    def test_newest_project_first(self, monkeypatch):
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept
        sq = {"type": "Polygon", "coordinates": [[[-72, 42], [-70, 42], [-70, 43], [-72, 43], [-72, 42]]]}
        far = {"type": "Polygon", "coordinates": [[[-100, 30], [-99, 30], [-99, 31], [-100, 31], [-100, 30]]]}
        monkeypatch.setattr(ept, "_resources", lambda: {"features": [
            {"properties": {"name": "MA_Old_2013"}, "geometry": sq},
            {"properties": {"name": "MA_New_2021"}, "geometry": sq},
            {"properties": {"name": "TX_Far_2022"}, "geometry": far},
        ]})
        names = [p["name"] for p in ept.projects_for_bbox((42.5, 42.4, -71.0, -71.1))]
        assert names == ["MA_New_2021", "MA_Old_2013"]

    def test_project_year_table_and_work_unit_suffix(self):
        from city2stl.height.providers.lidar_3dep_ept_laspy import PROJECT_YEARS, _project_year
        # measured flight years win over the name
        assert _project_year("HI_NOAAMauiOahu_2_B20") == PROJECT_YEARS["HI_NOAAMauiOahu_2_B20"] == 2023
        assert _project_year("USGS_LPC_HI_Oahu_2012_LAS_2015") == 2013
        assert _project_year("USGS_LPC_PR_PuertoRico_2015_LAS_2018") == 2016
        # no four-digit year: the work-unit suffix (_<letter><yy>)
        assert _project_year("CA_LosAngeles_1_B23") == 2023
        assert _project_year("AL_19Co_1_B24") == 2024
        assert _project_year("HI_NOAAMauiOahu_1_B20") == 2020
        assert _project_year("IA_FullState") == 0

    def test_honolulu_reads_the_2023_noaa_survey(self, monkeypatch):
        """Waikiki: the NOAA topobathy (flown 2023-03, no year in its name) used to rank last,
        so the 2013 USGS survey was read and the 2017-2018 towers were missing."""
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept
        oahu = {"type": "Polygon", "coordinates": [[[-158.3, 21.2], [-157.6, 21.2], [-157.6, 21.8],
                                                    [-158.3, 21.8], [-158.3, 21.2]]]}
        monkeypatch.setattr(ept, "_resources", lambda: {"features": [
            {"properties": {"name": "USGS_LPC_HI_Oahu_2012_LAS_2015"}, "geometry": oahu},
            {"properties": {"name": "HI_NOAAMauiOahu_2_B20"}, "geometry": oahu},
        ]})
        got = ept.projects_for_bbox((21.295, 21.264, -157.815, -157.848))
        assert [(p["name"], p["year"]) for p in got] == [
            ("HI_NOAAMauiOahu_2_B20", 2023), ("USGS_LPC_HI_Oahu_2012_LAS_2015", 2013)]

    def test_san_juan_reads_the_2018_post_maria_survey(self, monkeypatch):
        """The 2016 Puerto Rico flight ranked 2018 by its LAS year, level with the 2018 flight."""
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept
        pr = {"type": "Polygon", "coordinates": [[[-67.3, 17.9], [-65.2, 17.9], [-65.2, 18.6],
                                                  [-67.3, 18.6], [-67.3, 17.9]]]}
        monkeypatch.setattr(ept, "_resources", lambda: {"features": [
            {"properties": {"name": n}, "geometry": pr}
            for n in ("USGS_LPC_PR_PuertoRico_2015_LAS_2018", "USGS_LPC_PR_PuertoRico_2016_LAS_2017",
                      "USGS_LPC_PR_PRVI_E_2018")]})
        got = ept.projects_for_bbox((18.472, 18.432, -65.996, -66.125))
        assert got[0]["name"] == "USGS_LPC_PR_PRVI_E_2018"
        assert {p["year"] for p in got[1:]} == {2016}

    def test_suffix_year_beats_an_older_named_project(self, monkeypatch):
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept
        sq = {"type": "Polygon", "coordinates": [[[-119, 33], [-117, 33], [-117, 35], [-119, 35], [-119, 33]]]}
        monkeypatch.setattr(ept, "_resources", lambda: {"features": [
            {"properties": {"name": "CA_LosAngeles_2016"}, "geometry": sq},
            {"properties": {"name": "CA_LosAngeles_1_B23"}, "geometry": sq},
        ]})
        assert [p["name"] for p in ept.projects_for_bbox((34.1, 34.0, -118.2, -118.3))] == [
            "CA_LosAngeles_1_B23", "CA_LosAngeles_2016"]

    def test_index_is_built_once_and_answers_like_a_full_scan(self, monkeypatch):
        """The boundary index is parsed once per process (it was re-read and every shape rebuilt
        per call: 3.3 s per footprint), with the same answers as testing every feature."""
        from shapely.geometry import box, shape

        from city2stl.height.providers import lidar_3dep_ept_laspy as ept

        def sq(w, s, e, n):
            return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}

        ring = {"type": "Polygon", "coordinates": [  # a square with a hole in the middle
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]]}
        multi = {"type": "MultiPolygon", "coordinates": [
            [[[20, 0], [21, 0], [21, 1], [20, 1], [20, 0]]],
            [[[25, 5], [26, 5], [26, 6], [25, 6], [25, 5]]]]}
        feats = [
            {"properties": {"name": "Ring_2019"}, "geometry": ring},
            {"properties": {"name": "Multi_1_B22"}, "geometry": multi},
            {"properties": {"name": "Big_2010"}, "geometry": sq(-1, -1, 30, 12)},
            {"properties": {"name": "Small_2015"}, "geometry": sq(4, 4, 6, 6)},
            {"properties": {"name": "Bad_2020"}, "geometry": {"type": "Polygon", "coordinates": "x"}},
            {"properties": {}, "geometry": sq(0, 0, 1, 1)},
        ]
        calls = []
        monkeypatch.setattr(ept, "_resources", lambda: calls.append(1) or {"features": feats})

        def full_scan(n, s, e, w):
            area, out = box(w, s, e, n), []
            for f in feats:
                name = (f.get("properties") or {}).get("name")
                try:
                    if name and shape(f["geometry"]).intersects(area):
                        out.append({"name": name, "url": f"{ept._BUCKET}/{name}/ept.json",
                                    "year": ept._project_year(name)})
                except Exception:
                    continue
            return sorted(out, key=lambda p: (-p["year"], p["name"]))

        boxes = [(5.5, 4.5, 5.5, 4.5), (2, 1, 2, 1), (0.5, 0.2, 20.5, 20.2), (5.6, 5.2, 25.6, 25.2),
                 (50, 40, 50, 40), (8, 2, 8, 2), (11.5, 10.5, 11.5, 10.5)]
        for bb in boxes * 2:
            assert ept.projects_for_bbox(bb) == full_scan(*bb), bb
        assert len(calls) == 1

    def test_index_rebuilt_when_the_resources_change(self, monkeypatch):
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept
        sq = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        monkeypatch.setattr(ept, "_resources", lambda: {"features": [
            {"properties": {"name": "A_2020"}, "geometry": sq}]})
        assert [p["name"] for p in ept.projects_for_bbox((0.6, 0.4, 0.6, 0.4))] == ["A_2020"]
        monkeypatch.setattr(ept, "_resources", lambda: {"features": [
            {"properties": {"name": "B_2021"}, "geometry": sq}]})
        assert [p["name"] for p in ept.projects_for_bbox((0.6, 0.4, 0.6, 0.4))] == ["B_2021"]

    def test_node_read_retries_a_dropped_connection(self, monkeypatch):
        import requests

        from city2stl.height.providers import lidar_3dep_ept_laspy as ept

        class Resp:
            def __init__(self, code, body=b"laz"):
                self.status_code, self.content = code, body

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(str(self.status_code))

        script = [requests.ConnectionError("Connection aborted: ConnectionResetError(10054)"),
                  Resp(503), Resp(200, b"points")]
        seen = []

        def fake_get(url, timeout):
            seen.append(url)
            step = script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        monkeypatch.setattr(ept.requests, "get", fake_get)
        monkeypatch.setattr(ept, "_NODE_BACKOFF_S", 0.0)
        assert ept._get_node_bytes("u/ept-data/1-0-0-0.laz") == b"points"
        assert len(seen) == 3

    def test_node_read_gives_up_after_the_last_attempt(self, monkeypatch):
        import requests

        from city2stl.height.providers import lidar_3dep_ept_laspy as ept

        def fake_get(url, timeout):
            raise requests.ConnectionError("reset")

        monkeypatch.setattr(ept.requests, "get", fake_get)
        monkeypatch.setattr(ept, "_NODE_BACKOFF_S", 0.0)
        with pytest.raises(requests.ConnectionError):
            ept._get_node_bytes("u/ept-data/1-0-0-0.laz")

    def test_node_read_does_not_retry_a_404(self, monkeypatch):
        import requests

        from city2stl.height.providers import lidar_3dep_ept_laspy as ept

        class Resp:
            status_code, content = 404, b""

            def raise_for_status(self):
                raise requests.HTTPError("404")

        seen = []
        monkeypatch.setattr(ept.requests, "get", lambda url, timeout: seen.append(url) or Resp())
        with pytest.raises(requests.HTTPError):
            ept._get_node_bytes("u/ept-data/1-0-0-0.laz")
        assert len(seen) == 1

    def test_node_bounds_halves_per_level(self):
        from city2stl.height.providers.lidar_3dep_ept_laspy import node_bounds
        cube = [0.0, 0.0, 0.0, 100.0, 100.0, 100.0]
        assert node_bounds(cube, "0-0-0-0") == (0.0, 0.0, 100.0, 100.0)
        assert node_bounds(cube, "1-1-0-0") == (50.0, 0.0, 100.0, 50.0)
        assert node_bounds(cube, "2-3-3-1") == (75.0, 75.0, 100.0, 100.0)

    def test_walk_reads_every_level_and_sub_hierarchies(self):
        from city2stl.height.providers.lidar_3dep_ept_laspy import nodes_for_bounds
        ept = {"bounds": [0.0, 0.0, 0.0, 100.0, 100.0, 100.0]}
        files = {
            "b/ept-hierarchy/0-0-0-0.json": {"0-0-0-0": 10, "1-0-0-0": 5, "1-1-1-0": 5,
                                             "2-0-0-0": -1},
            "b/ept-hierarchy/2-0-0-0.json": {"2-0-0-0": 7, "3-0-0-0": 3},
        }
        got = nodes_for_bounds("b", ept, (1.0, 1.0, 10.0, 10.0), max_depth=3,
                               get_json=files.__getitem__)
        # root, the SW child, its sub-hierarchy node and that node's child; not the NE child
        assert got == ["0-0-0-0", "1-0-0-0", "2-0-0-0", "3-0-0-0"]
        shallow = nodes_for_bounds("b", ept, (1.0, 1.0, 10.0, 10.0), max_depth=1,
                                   get_json=files.__getitem__)
        assert shallow == ["0-0-0-0", "1-0-0-0"]

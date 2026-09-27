"""geo2stl.opentopo key handling / request errors and geo2stl.dem.fetch_dem routing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from geo2stl import dem, opentopo


def test_set_api_key_is_used_by_requests(monkeypatch):
    monkeypatch.setattr(opentopo, "_api_key", None)
    opentopo.set_api_key("abc")
    assert opentopo.get_api_key() == "abc"
    resp = MagicMock(status_code=200, headers={"Content-Type": "image/tiff"}, content=b"TIF")
    with patch.object(opentopo.requests, "get", return_value=resp) as get:
        assert opentopo.request_geotiff("COP30", 1, 0, 1, 0) == b"TIF"
    assert get.call_args.kwargs["params"]["API_Key"] == "abc"
    opentopo.set_api_key("")
    assert opentopo.get_api_key() is None


@pytest.mark.parametrize("status, ctype", [(401, "text/plain"), (200, "text/html")])
def test_request_geotiff_errors(status, ctype):
    resp = MagicMock(status_code=status, headers={"Content-Type": ctype}, text="nope")
    with patch.object(opentopo.requests, "get", return_value=resp), \
            pytest.raises(RuntimeError, match="OpenTopography API error"):
        opentopo.request_geotiff("COP30", 1, 0, 1, 0, api_key="k")


def test_fetch_dem_routes_opentopo_with_key():
    with patch.object(dem, "fetch_opentopo_dem", return_value=np.ones((2, 2))) as f:
        out = dem.fetch_dem({"north": 1, "south": 0, "east": 1, "west": 0}, 64, "COP30", "k")
    assert out.shape == (2, 2)
    assert f.call_args.kwargs["api_key"] == "k" and f.call_args.kwargs["demtype"] == "COP30"


def test_fetch_dem_local_failure_returns_zero_array():
    with patch.object(dem, "fetch_local_dem", side_effect=RuntimeError("no tiles")):
        out = dem.fetch_dem((2.0, 0.0, 1.0, 0.0), 100)
    assert out.shape == (100, 50) and not out.any()

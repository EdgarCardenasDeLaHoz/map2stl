"""The finished city model's parts file for the Extrude viewer (2026-10-03).

``numpy2stl.io.write_parts_file`` writes it (and ``read_parts_file`` reads it); ``city-fullmodel.js`` reads the same
layout: uint32 header length, JSON header, then float32 vertices and uint32 faces
per part.
"""
import json

import numpy as np
from numpy2stl.io import read_parts_file, write_parts_file

from city2stl.city_model import build_city_model

BBOX = dict(north=37.19, south=37.172, east=-3.578, west=-3.605)


def _read(path):
    raw = open(path, "rb").read()
    n = int(np.frombuffer(raw[:4], np.uint32)[0])
    header = json.loads(raw[4:4 + n])
    off, parts = 4 + n, {}
    for p in header["parts"]:
        v = np.frombuffer(raw, np.float32, p["vertices"] * 3, off).reshape(-1, 3)
        off += v.nbytes
        f = np.frombuffer(raw, np.uint32, p["faces"] * 3, off).reshape(-1, 3)
        off += f.nbytes
        parts[p["name"]] = (v, f)
    assert off == len(raw)
    return parts


def test_parts_round_trip(tmp_path):
    ring = [[-3.590, 37.180], [-3.589, 37.180], [-3.589, 37.181], [-3.590, 37.181], [-3.590, 37.180]]
    layers = {"buildings": {"type": "FeatureCollection", "features": [
        {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"height_m": 20}}]}}
    m = build_city_model(np.full((60, 80), 100.0), BBOX, layers)
    path = tmp_path / "model.parts"
    write_parts_file(m.parts, str(path))
    got = _read(path)
    assert {k: len(v.faces) for k, v in read_parts_file(path).items()} == {k: len(f) for k, (_, f) in got.items()}
    assert set(got) == {k for k, part in m.parts.items() if len(part.faces)}
    for k, (v, f) in got.items():
        assert np.allclose(v, m.parts[k].vertices, atol=1e-4)
        assert (f == m.parts[k].faces).all()


def test_unknown_task_has_no_parts(client):
    assert client.get("/api/export/model-parts/nope").status_code == 404

"""F-FRAME P0: golden hashes of today's output.

Pins the legacy projection path (``none`` / ``cosine`` / ``mercator``) byte for byte, the
shapes of the equal-area family (``laea`` / ``lcc`` / ``sinusoidal``), and the none-projection
terrain STL, so the later F-FRAME phases can prove the legacy path did not move.
Everything is offline and deterministic. To re-capture after an *intended* change, run this
file with ``FRAME_BASELINE_PRINT=1 pytest -s`` and paste the printed values.
"""
import hashlib
import io
import os

import numpy as np
import pytest

from geo2stl.projections import (
    project_grid,
    project_rgb_image,
    project_water_arrays,
    projected_shape,
)

EUROPE = (60.0, 35.0, 30.0, -10.0)   # north, south, east, west
CITY = (40.05, 40.00, -75.10, -75.15)
LEGACY = ("none", "cosine", "mercator")
BBOXES = {"europe": EUROPE, "city": CITY}
DIM = 160


def _digest(arr: np.ndarray) -> str:
    arr = np.ascontiguousarray(arr)
    h = hashlib.sha256(arr.tobytes()).hexdigest()
    return f"{arr.shape}|{arr.dtype}|{h}"


def _dem() -> np.ndarray:
    rng = np.random.default_rng(0)
    ramp = np.linspace(0, 500, 240, dtype=np.float32)[None, :] + \
        np.linspace(0, 300, 200, dtype=np.float32)[:, None]
    return (ramp + rng.random((200, 240), dtype=np.float32) * 50).astype(np.float32)


def _water() -> np.ndarray:
    return (_dem() > 450).astype(np.float32)


def _esa() -> np.ndarray:
    rng = np.random.default_rng(1)
    return rng.integers(10, 100, size=(200, 240)).astype(np.float32)


def _rgb() -> np.ndarray:
    rng = np.random.default_rng(2)
    return rng.integers(0, 256, size=(200, 240, 3)).astype(np.uint8)


def _compute(kind: str, bbox_name: str, proj: str):
    n, s, e, w = BBOXES[bbox_name]
    if kind == "grid":
        return _digest(project_grid(_dem(), n, s, e, w, proj, clip_nans=False,
                                    target_dim=DIM))
    if kind == "grid_clip":
        return _digest(project_grid(_dem(), n, s, e, w, proj, clip_nans=True))
    if kind == "water":
        wm, esa = project_water_arrays(_water(), _esa(), n, s, e, w, proj,
                                       clip_nans=True)
        return _digest(wm) + "||" + _digest(esa)
    if kind == "rgb":
        return _digest(project_rgb_image(_rgb(), n, s, e, w, proj, clip_nans=True))
    raise AssertionError(kind)


def _check(key: str, got: str):
    if os.environ.get("FRAME_BASELINE_PRINT"):
        print(f"GOLDEN {key!r}: {got!r},")
        return
    assert got == GOLDEN[key], f"{key} moved"


@pytest.mark.parametrize("kind", ["grid", "grid_clip", "water", "rgb"])
@pytest.mark.parametrize("bbox_name", list(BBOXES))
@pytest.mark.parametrize("proj", LEGACY)
def test_legacy_projection_bytes(kind, bbox_name, proj):
    key = f"{kind}/{bbox_name}/{proj}"
    _check(key, _compute(kind, bbox_name, proj))


@pytest.mark.parametrize("proj", ["laea", "lcc", "sinusoidal"])
def test_projected_shape_europe(proj):
    shape = tuple(projected_shape(EUROPE, proj, DIM))
    if os.environ.get("FRAME_BASELINE_PRINT"):
        print(f"SHAPE {proj}: {shape}")
        return
    assert shape == SHAPES[proj]


def test_laea_wings_nan_count():
    n, s, e, w = EUROPE
    out = project_grid(_dem(), n, s, e, w, "laea", clip_nans=False, target_dim=DIM)
    got = (tuple(out.shape), int(np.isnan(out).sum()))
    if os.environ.get("FRAME_BASELINE_PRINT"):
        print(f"LAEA {got}")
        return
    assert got == LAEA_NAN


def test_none_terrain_stl_bytes():
    from app.server.core.export import _parse_export_params, _prepare_export_mesh

    rng = np.random.default_rng(3)
    h, w = 24, 30
    dem = (np.linspace(0, 80, w)[None, :] + np.linspace(0, 40, h)[:, None]
           + rng.random((h, w)) * 5).round(3)
    data = {
        "dem_values": dem.ravel().tolist(), "height": h, "width": w,
        "model_height": 10.0, "base_height": 2.0, "exaggeration": 1.0,
        "sea_level_cap": False, "name": "baseline",
        "bbox": {"north": 40.05, "south": 40.0, "east": -75.10, "west": -75.15},
    }
    p = _parse_export_params(data)
    mesh = _prepare_export_mesh(p, data)
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    # The 80-byte header may carry a version string; hash the triangle payload only.
    payload = buf.getvalue()[80:]
    got = f"{len(mesh.faces)}|{hashlib.sha256(payload).hexdigest()}"
    if os.environ.get("FRAME_BASELINE_PRINT"):
        print(f"STL {got!r}")
        return
    assert got == STL_GOLDEN


# ---- captured from the code before F-FRAME (2026-10-17) -------------------------
GOLDEN: dict[str, str] = {
    'grid/city/cosine':
        '(160, 123)|float64|b46cbed6a0721c85bd475fc716a2ae2a5006bb3724620ddf3b6ba8f23f28f18a',
    'grid/city/mercator':
        '(160, 123)|float64|d97898609c18f5f3f7013b3f014f2b318b316e20983bf0755dee68ed52b51f91',
    'grid/city/none':
        '(160, 160)|float32|966a92e795002101462552ffc52d420d4d5af3ad098c5759c992f8bb24de3f40',
    'grid/europe/cosine':
        '(148, 160)|float64|a30cc101f6e0f7f577e110d5740cb5f63e54e0d3ba392172792f0bfa10e3f1d5',
    'grid/europe/mercator':
        '(152, 160)|float64|8c28d0b80cc0e9e21c2483f106b4d12d25087c1acc6255c4a45bd497b2ad50cb',
    'grid/europe/none':
        '(100, 160)|float32|36f058a158e9af7b4577502a12bdcfb83b85e1f36a1846e9939c45ef97f605cd',
    'grid_clip/city/cosine':
        '(200, 184)|float64|e44660f32677d6ac147c455a9568668436ff37d70a23357dabe439bf9f89d4ed',
    'grid_clip/city/mercator':
        '(200, 153)|float64|2425eb9cee6ea6be2bab28d88ebe349994a0e7218726aea9777c5b043cff9ea0',
    'grid_clip/city/none':
        '(200, 240)|float32|821b34f2e95863a91a33b2b082bb441afde1f58e586dc680c0d3d166acd53782',
    'grid_clip/europe/cosine':
        '(200, 161)|float64|669b677d801901bdc2d99e48a04c3e9e6aa0994f42ce0a186300f664d06c4579',
    'grid_clip/europe/mercator':
        '(200, 210)|float64|97fa504d3e2caa3281580f7782299734cb34ac404226e9fbc6aa9a109237bd0e',
    'grid_clip/europe/none':
        '(200, 240)|float32|821b34f2e95863a91a33b2b082bb441afde1f58e586dc680c0d3d166acd53782',
    'rgb/city/cosine':
        '(200, 184, 3)|uint8|067a57a869d9a8e5b5a6821baf6982c4e544f896a025b61fd5b0707e61851f5d',
    'rgb/city/mercator':
        '(200, 153, 3)|uint8|51fff2768868a6b546a52bd17350f295894272beba457806ccdaa67a535a7762',
    'rgb/city/none':
        '(200, 240, 3)|uint8|a5df96786d06c5806b24fd4602dbcbce2e5b466aca0ef6eaa9509a2891560ea0',
    'rgb/europe/cosine':
        '(200, 161, 3)|uint8|9b81b8bf186ce709de295b55b58e248286f7b758dcc3fc8814f7647869b191df',
    'rgb/europe/mercator':
        '(200, 210, 3)|uint8|7d33ac1153cba5e34965ecf3ee6810eb07ed3f3fc00018aa6614d6a10c6fe362',
    'rgb/europe/none':
        '(200, 240, 3)|uint8|a5df96786d06c5806b24fd4602dbcbce2e5b466aca0ef6eaa9509a2891560ea0',
    'water/city/cosine':
        '(200, 184)|float32|4e4549e50de5bb19fbd25ef324637302c1d5d6e51974d824c84798f9324d0b6c||(200, 184)|float64|ca640d5d65c90be1bb549a3eb92dedb79fe165ff3539d0cf7e5018458e8c2b66',
    'water/city/mercator':
        '(200, 153)|float32|a23203112fdf22f6b282a870a5f7144ddd8b1927f58a0e03267e096168beaf33||(200, 153)|float64|bdb764f31811a7aa1c13d62cdc4e4cf300170f7fc0969753a1c50dc5ca817301',
    'water/city/none':
        '(200, 240)|float32|155c1056943f82ed6b659daac117e945641af6e581b2e6ce8454146e915543f6||(200, 240)|float32|c1d3709cc7ae7512116434fdb89bf97aed3d556a03386e1d1fc25fd22658bf04',
    'water/europe/cosine':
        '(200, 161)|float32|9015d80342eefe20938528be94525e599d25690ad90cd8e75bf364695cd159ac||(200, 161)|float64|08a847a25293ab7d3af80281d2a2b8918b242780d62019e1e5261100e387c6dc',
    'water/europe/mercator':
        '(200, 210)|float32|f4fa2d6189bda00a2ed243df7b190c8f6948f7294c843a3c511271b5d1c2b806||(200, 210)|float64|7db9855346ff1ea069bc7235a521611fbcb1d05b2f7a4c747c7936adf5f6fac0',
    'water/europe/none':
        '(200, 240)|float32|155c1056943f82ed6b659daac117e945641af6e581b2e6ce8454146e915543f6||(200, 240)|float32|c1d3709cc7ae7512116434fdb89bf97aed3d556a03386e1d1fc25fd22658bf04',
}
SHAPES: dict[str, tuple[int, int]] = {"laea": (129, 160), "lcc": (128, 160), "sinusoidal": (122, 160)}
LAEA_NAN = ((129, 160), 4744)   # (shape, NaN count): the laea "wings"
STL_GOLDEN = (
    "224|54ffd28794b5758d41f77ff2a8128920e36375926c6431122fb2dfa8f148d11b"
)

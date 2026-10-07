"""skyline._pano.stage_cache — on-disk cache of each drone-seed (elevated pano) stage.

A drone seed (``elevated.measure_elevated_seed``) runs: views -> stitched, levelled pano ->
SegFormer labels -> Depth Anything depth -> MobileSAM instances -> camera pose -> footprint
measurements. Depth and instances were cached as raw ``.npy`` (~62 MB each per pano) and the
rest ran again on every review round (~90-125 s a seed, 2026-10-06). This module caches any
stage behind one call::

    out = cached(stage, version, key_parts, compute, kind="npy" | "pickle")

- **key**: sha1 over the stage, its version and ``key_parts`` (:func:`hash_parts`: arrays by
  dtype, shape and bytes; dataclasses field by field; shapely geometries by WKB; dicts, lists,
  numbers, strings). Bumping a stage's version invalidates that stage only (its entries live
  under ``<root>/<stage>/v<version>/``). :func:`source_hash` adds the source of the modules a
  cheap stage depends on, so an edit to them invalidates it without a manual bump.
- **storage**: one ``.npz`` (deflate) per entry. Integer arrays are stored in the smallest
  integer type that holds their range (MobileSAM instances, int32 0..219: 62 MB -> 0.08 MB),
  bool arrays bit-packed, floats losslessly (row delta of the bit pattern + byte shuffle:
  depth 62 MB -> ~24 MB) or, on request (``lossy_float16=True``), as float16 (~3.6 MB, relative
  error <= 4.9e-4). ``kind="pickle"`` pickles any object with its large arrays stored the same
  way out of band (a whole ``footprint_detect.Pano``, a pose fit, a list of ``Measured``).
  Arrays come back with their original dtype and shape.
- **safe in parallel**: each write goes to a temp file in the same folder, then ``os.replace``;
  a reader never sees a partial file, and two processes computing the same entry just both
  write it. A corrupt or unreadable entry is recomputed and rewritten.
- **switch**: ``SKYLINE_STAGE_CACHE=off`` (or 0/false/no) computes everything and touches no
  file; ``=refresh`` recomputes and overwrites. ``SKYLINE_STAGE_CACHE_DIR`` moves the root
  (default ``city2stl/skyline/runs/stage_cache``).
- **legacy**: ``cached(..., legacy_path=...)`` reads an old raw ``runs/pano_cache/*.npy`` and
  rewrites it compact; :func:`migrate_pano_cache` converts the whole folder at once.
- **housekeeping**: :func:`entries`, :func:`summary`, :func:`prune`, and a CLI::

      python -m city2stl.skyline._pano.stage_cache list [--stage depth] [--legacy]
      python -m city2stl.skyline._pano.stage_cache prune --stage pose --keep-version 2
      python -m city2stl.skyline._pano.stage_cache prune --older-than-days 30 --dry-run
      python -m city2stl.skyline._pano.stage_cache migrate [--delete]

Stages of the drone path and their keys (see the wiring in ``elevated.py``):

====================  ===========================================================  =========
stage                 key parts                                                    kind
====================  ===========================================================  =========
``labels``            view image digest, SegFormer model id + input size           npy
``pano``              (heading, pitch, view digest, label digest) per view, fov,   pickle
                      step, seed name/lat/lon, stitch version
``depth``             pano rgb digest (20 hex, as the legacy ``pano_cache``)       npy
``instances``         pano rgb digest, MobileSAM window and grid (px)              npy
``pose``              pano (all fields), ground layers, buildings, source hash      pickle
``measured``          pose key, footprints, branch                                  pickle
====================  ===========================================================  =========

SegFormer on the GPU is not bit-exact, so labels never enter a key by their own run: the first
output of a view is stored and becomes the reference every later run reads.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import io
import json
import logging
import math
import os
import pickle
import re
import sys
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_RUNS = Path(__file__).resolve().parents[1] / "runs"
#: Default cache root (gitignored with the rest of ``runs/``).
DEFAULT_ROOT = _RUNS / "stage_cache"
#: The raw-``.npy`` cache this module replaces (``elevated._pano_cached``).
LEGACY_PANO_CACHE_DIR = _RUNS / "pano_cache"

ENV_SWITCH = "SKYLINE_STAGE_CACHE"
ENV_ROOT = "SKYLINE_STAGE_CACHE_DIR"
#: Opt-in float16 storage of depth maps (``1``/``on``); off by default, see :func:`depth_float16`.
ENV_DEPTH_F16 = "SKYLINE_STAGE_CACHE_DEPTH_F16"

#: On-disk format of an entry; a change here makes old entries unreadable -> recomputed.
FORMAT = 1
#: Arrays at least this large go out of band in ``kind="pickle"`` entries.
_OOB_MIN_BYTES = 1024

_STAGE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


# --------------------------------------------------------------------------- switches


def mode() -> str:
    """``on`` (default), ``off`` or ``refresh``, from ``SKYLINE_STAGE_CACHE``."""
    v = os.environ.get(ENV_SWITCH, "on").strip().lower()
    if v in ("0", "off", "false", "no", "disabled", "none"):
        return "off"
    if v in ("refresh", "rebuild", "overwrite"):
        return "refresh"
    return "on"


def enabled() -> bool:
    return mode() != "off"


def cache_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get(ENV_ROOT, "").strip()
    return Path(env) if env else DEFAULT_ROOT


def depth_float16() -> bool:
    """Whether depth maps go to the cache as float16 (``SKYLINE_STAGE_CACHE_DEPTH_F16=1``).

    Off by default: Depth Anything's inverse depth (0..1) loses up to 4.9e-4 of its value in
    float16, and ``footprint_detect._column_run`` / ``measure_footprints`` compare depths
    against ratios (``depth_ratio``, ``stop_ratio``) and floors, so a few edge columns could
    end a run one row earlier or later: not bit-identical to a fresh run. Lossless float32
    already brings a depth map from 62 MB to ~24 MB."""
    return os.environ.get(ENV_DEPTH_F16, "").strip().lower() in ("1", "on", "true", "yes")


# --------------------------------------------------------------------------- keys


class Digest(str):
    """A precomputed digest: hashed as the string it is (e.g. a parent stage's key)."""


def _feed(h, obj: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("stage_cache key: structure nested too deep")
    if obj is None:
        h.update(b"N;")
    elif isinstance(obj, (bool, np.bool_)):
        h.update(b"B1;" if obj else b"B0;")
    elif isinstance(obj, (int, np.integer)):
        h.update(b"I" + str(int(obj)).encode() + b";")
    elif isinstance(obj, (float, np.floating)):
        f = float(obj)
        h.update(b"F" + (b"nan" if math.isnan(f) else f.hex().encode()) + b";")
    elif isinstance(obj, str):
        b = obj.encode("utf-8")
        h.update(b"S%d:" % len(b) + b)
    elif isinstance(obj, (bytes, bytearray, memoryview)):
        b = bytes(obj)
        h.update(b"Y%d:" % len(b) + b)
    elif isinstance(obj, np.ndarray):
        if obj.dtype == object:
            h.update(b"O" + repr(obj.shape).encode())
            for x in obj.ravel():
                _feed(h, x, depth + 1)
        else:
            a = np.ascontiguousarray(obj)
            h.update(b"A" + a.dtype.str.encode() + repr(a.shape).encode() + b":")
            h.update(memoryview(a).cast("B"))
    elif isinstance(obj, Path):
        _feed(h, obj.as_posix(), depth + 1)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        h.update(b"D" + type(obj).__qualname__.encode() + b"{")
        for f in dataclasses.fields(obj):
            _feed(h, f.name, depth + 1)
            _feed(h, getattr(obj, f.name), depth + 1)
        h.update(b"}")
    elif isinstance(obj, dict):
        items = sorted((hash_parts(k), k, v) for k, v in obj.items()) if obj else []
        h.update(b"M%d{" % len(items))
        for kd, _k, v in items:
            h.update(kd.encode())
            _feed(h, v, depth + 1)
        h.update(b"}")
    elif isinstance(obj, (list, tuple)):
        h.update(b"L%d[" % len(obj))
        for x in obj:
            _feed(h, x, depth + 1)
        h.update(b"]")
    elif isinstance(obj, (set, frozenset)):
        ds = sorted(hash_parts(x) for x in obj)
        h.update(b"T%d[" % len(ds) + "".join(ds).encode() + b"]")
    elif hasattr(obj, "wkb") and hasattr(obj, "geom_type"):        # shapely geometry
        h.update(b"G" + bytes(obj.wkb))
    else:
        try:
            b = pickle.dumps(obj, protocol=4)
        except Exception:                                           # noqa: BLE001
            b = repr(obj).encode("utf-8", "replace")
        h.update(b"P" + type(obj).__qualname__.encode() + b"%d:" % len(b) + b)


def hash_parts(*parts: Any) -> str:
    """sha1 hex of ``parts`` (see the module docstring for what counts)."""
    h = hashlib.sha1()
    _feed(h, parts)
    return h.hexdigest()


def array_digest(a: np.ndarray, n: int = 20) -> Digest:
    """sha1 of an array's raw bytes (not dtype or shape), first ``n`` hex: the legacy
    ``pano_cache`` file prefix for ``n=20`` and ``a = pano.rgb``."""
    return Digest(hashlib.sha1(np.ascontiguousarray(a).tobytes()).hexdigest()[:n])


pano_digest = array_digest


def source_hash(*objs: Any) -> Digest:
    """Digest of the source of modules, classes or functions (a module: its whole file).

    Put it in the key of a cheap stage whose code is still moving (pose, measurements): any
    edit to that code then invalidates the stage without a manual version bump."""
    h = hashlib.sha1()
    for o in objs:
        try:
            if inspect.ismodule(o) and getattr(o, "__file__", None):
                h.update(Path(o.__file__).read_bytes())
            else:
                h.update(inspect.getsource(o).encode("utf-8"))
        except (OSError, TypeError):
            h.update(repr(o).encode())
    return Digest(h.hexdigest()[:16])


# --------------------------------------------------------------------------- array codec


_INT_LADDER = (np.uint8, np.int8, np.uint16, np.int16, np.uint32, np.int32, np.uint64, np.int64)
_UINT_OF_SIZE = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}


def _smallest_int(a: np.ndarray):
    if a.size == 0:
        return a.dtype
    lo, hi = int(a.min()), int(a.max())
    for t in _INT_LADDER:
        info = np.iinfo(t)
        if info.min <= lo and hi <= info.max and np.dtype(t).itemsize <= a.dtype.itemsize:
            return np.dtype(t)
    return a.dtype


def _delta_last(u: np.ndarray, axis: int) -> np.ndarray:
    """Unsigned wrap-around difference along ``axis`` (first element kept)."""
    out = u.copy()
    sl_hi = [slice(None)] * u.ndim
    sl_lo = [slice(None)] * u.ndim
    sl_hi[axis], sl_lo[axis] = slice(1, None), slice(None, -1)
    out[tuple(sl_hi)] = u[tuple(sl_hi)] - u[tuple(sl_lo)]
    return out


def _shuffle(u: np.ndarray) -> np.ndarray:
    """Byte planes: all first bytes, then all second bytes, ... (deflate likes them)."""
    k = u.dtype.itemsize
    return np.ascontiguousarray(u.reshape(-1).view(np.uint8).reshape(-1, k).T)


def _unshuffle(planes: np.ndarray, udt) -> np.ndarray:
    return np.ascontiguousarray(planes.T).reshape(-1).view(udt)


def encode_array(a: np.ndarray, lossy_float16: bool = False) -> tuple[dict, np.ndarray]:
    """``(meta, stored)``: a compact, exactly invertible form of ``a`` for deflate
    (float16 only with ``lossy_float16`` and a wider float input). See :func:`decode_array`."""
    a = np.asarray(a)
    meta = {"dtype": a.dtype.str, "shape": list(a.shape)}
    if a.dtype == object:
        raise TypeError("stage_cache: object arrays are not stored as arrays")
    if a.size == 0 or a.ndim == 0 or a.dtype.kind in "cSUVMm":
        meta["enc"] = "raw"
        return meta, np.ascontiguousarray(a)
    if a.dtype.kind == "b":
        meta["enc"] = "bits"
        return meta, np.packbits(a.reshape(-1))
    if a.dtype.kind in "iu":
        small = _smallest_int(a)
        s = a.astype(small, copy=False)
        meta.update(enc="int", store=small.str)
        if s.ndim == 3 and s.shape[-1] in (3, 4) and small.itemsize == 1:
            # an image: neighbour difference along each row (PNG's Sub filter)
            u = s.view(_UINT_OF_SIZE[1])
            meta["delta_axis"] = 1
            return meta, _delta_last(u, 1)
        return meta, np.ascontiguousarray(s)
    if a.dtype.kind == "f":
        f = a
        if lossy_float16 and a.dtype.itemsize > 2:
            f = a.astype(np.float16)
            meta["lossy"] = "float16"
        f = np.ascontiguousarray(f)
        u = f.view(_UINT_OF_SIZE[f.dtype.itemsize])
        meta.update(enc="float", store=f.dtype.str)
        if u.ndim >= 1:
            u = _delta_last(u, u.ndim - 1)
            meta["delta_axis"] = u.ndim - 1
        return meta, _shuffle(u)
    meta["enc"] = "raw"
    return meta, np.ascontiguousarray(a)


def decode_array(meta: dict, stored: np.ndarray) -> np.ndarray:
    dt, shape, enc = np.dtype(meta["dtype"]), tuple(meta["shape"]), meta["enc"]
    if enc == "raw":
        return stored.astype(dt, copy=False).reshape(shape)
    if enc == "bits":
        n = int(np.prod(shape))
        return np.unpackbits(stored, count=n).astype(bool).reshape(shape)
    if enc == "int":
        st = np.dtype(meta["store"])
        u = stored
        if "delta_axis" in meta:
            u = np.cumsum(u.view(_UINT_OF_SIZE[st.itemsize]), axis=meta["delta_axis"],
                          dtype=_UINT_OF_SIZE[st.itemsize])
        return u.view(st).reshape(shape).astype(dt)
    if enc == "float":
        st = np.dtype(meta["store"])
        udt = _UINT_OF_SIZE[st.itemsize]
        u = _unshuffle(stored, udt).reshape(shape)
        if "delta_axis" in meta:
            u = np.cumsum(u, axis=meta["delta_axis"], dtype=udt)
        return np.ascontiguousarray(u).view(st).astype(dt, copy=False)
    raise ValueError(f"stage_cache: unknown array encoding {enc!r}")


# --------------------------------------------------------------------------- entry files


class _OOBPickler(pickle.Pickler):
    def __init__(self, fh, arrays: list, lossy_float16: bool):
        super().__init__(fh, protocol=4)
        self._arrays, self._lossy = arrays, lossy_float16

    def persistent_id(self, obj):                                    # noqa: D102
        if (type(obj) is np.ndarray and obj.dtype != object and obj.nbytes >= _OOB_MIN_BYTES):
            self._arrays.append(encode_array(obj, self._lossy))
            return ("arr", len(self._arrays) - 1)
        return None


class _OOBUnpickler(pickle.Unpickler):
    def __init__(self, fh, arrays: list):
        super().__init__(fh)
        self._arrays = arrays

    def persistent_load(self, pid):                                  # noqa: D102
        tag, i = pid
        if tag != "arr":
            raise pickle.UnpicklingError(f"unknown persistent id {pid!r}")
        return self._arrays[i]


def _members(obj: Any, kind: str, lossy_float16: bool) -> tuple[dict, dict]:
    if kind == "npy":
        if not isinstance(obj, np.ndarray):
            raise TypeError(f"stage_cache kind='npy' needs an ndarray, got {type(obj).__name__}")
        m, s = encode_array(obj, lossy_float16)
        return {"format": FORMAT, "kind": kind, "arrays": [m]}, {"a0": s}
    if kind == "pickle":
        arrays: list = []
        buf = io.BytesIO()
        _OOBPickler(buf, arrays, lossy_float16).dump(obj)
        mem = {f"a{i}": s for i, (_m, s) in enumerate(arrays)}
        mem["pickle"] = np.frombuffer(buf.getvalue(), np.uint8)
        return {"format": FORMAT, "kind": kind, "arrays": [m for m, _s in arrays]}, mem
    raise ValueError(f"stage_cache: unknown kind {kind!r}")


def _replace(tmp: Path, path: Path, tries: int = 8) -> None:
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows: the target is open in another process (a reader); it holds the same
            # bytes, so retry briefly and otherwise keep the one already there
            if i == tries - 1:
                if path.exists():
                    return
                raise
            time.sleep(0.05 * (i + 1))


def write_entry(path: Path, obj: Any, kind: str = "npy", lossy_float16: bool = False,
                extra_meta: dict | None = None) -> int:
    """Write one entry atomically (temp file + ``os.replace``); returns its size in bytes."""
    meta, mem = _members(obj, kind, lossy_float16)
    if extra_meta:
        meta.update(extra_meta)
    meta["lossy"] = any(m.get("lossy") for m in meta["arrays"])
    mem["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **mem)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path.stat().st_size


def read_entry(path: Path) -> tuple[Any, dict]:
    """``(object, meta)`` of an entry written by :func:`write_entry`."""
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(z["meta"].tobytes().decode("utf-8"))
        if meta.get("format") != FORMAT:
            raise ValueError(f"stage_cache: format {meta.get('format')} != {FORMAT}")
        arrays = [decode_array(m, z[f"a{i}"]) for i, m in enumerate(meta["arrays"])]
        if meta["kind"] == "npy":
            return arrays[0], meta
        raw = z["pickle"].tobytes()
    return _OOBUnpickler(io.BytesIO(raw), arrays).load(), meta


def entry_path(stage: str, version: Any, key: str, root: str | Path | None = None) -> Path:
    if not _STAGE_RE.match(stage) or not _STAGE_RE.match(str(version)):
        raise ValueError(f"stage_cache: bad stage/version {stage!r}/{version!r}")
    return cache_root(root) / stage / f"v{version}" / f"{key[:32]}.npz"


_READ_ERRORS = (OSError, ValueError, KeyError, EOFError, TypeError, AttributeError,
                ImportError, IndexError, zipfile.BadZipFile, pickle.UnpicklingError)


# --------------------------------------------------------------------------- the call


def stage_key(stage: str, version: Any, key_parts: Any) -> Digest:
    return Digest(hash_parts(stage, str(version), key_parts))


def cached(stage: str, version: Any, key_parts: Any, compute: Callable[[], Any],
           kind: str = "npy", *, root: str | Path | None = None,
           legacy_path: str | Path | None = None, lossy_float16: bool = False,
           cache_none: bool = False) -> Any:
    """``compute()``, cached on disk under ``stage``/``version`` by a hash of ``key_parts``.

    ``kind``: ``"npy"`` (one ndarray) or ``"pickle"`` (any picklable object; its arrays are
    stored compact). ``legacy_path``: an old raw ``.npy`` read (and rewritten compact) on a
    miss. ``lossy_float16``: store float arrays as float16; an entry stored lossy is not served
    to a caller asking for lossless. ``cache_none``: also cache a ``None`` result (default: a
    ``None`` is returned and the stage runs again next time). Read or write failures fall back
    to computing; they never fail the caller."""
    m = mode()
    if m == "off":
        return compute()
    path = entry_path(stage, version, stage_key(stage, version, key_parts), root)
    if m == "on" and path.exists():
        t0 = time.perf_counter()
        try:
            obj, meta = read_entry(path)
            if meta.get("lossy") and not lossy_float16:
                raise ValueError("entry stored lossy, lossless asked")
            if meta.get("none"):
                obj = None
            try:
                os.utime(path)                     # last use, for prune --older-than-days
            except OSError:
                pass
            logger.debug("[stage_cache] hit %s/v%s %s (%.1f MB, %.2f s)", stage, version,
                         path.stem[:12], path.stat().st_size / 1e6, time.perf_counter() - t0)
            return obj
        except _READ_ERRORS as exc:
            logger.warning("[stage_cache] %s/v%s: unreadable %s (%s); recomputing", stage,
                           version, path.name, exc)
    if m == "on" and legacy_path is not None and Path(legacy_path).exists():
        try:
            obj = np.load(legacy_path, allow_pickle=False)
            _store(path, obj, kind, lossy_float16, stage, version)
            logger.info("[stage_cache] %s/v%s: legacy %s converted", stage, version,
                        Path(legacy_path).name)
            return obj
        except _READ_ERRORS as exc:
            logger.warning("[stage_cache] legacy %s unreadable: %s", legacy_path, exc)
    t0 = time.perf_counter()
    obj = compute()
    dt = time.perf_counter() - t0
    if obj is None and not cache_none:
        return None
    _store(path, obj, kind, lossy_float16, stage, version, none=obj is None, seconds=dt)
    return obj


def _store(path, obj, kind, lossy_float16, stage, version, none=False, seconds=None):
    try:
        extra = {"stage": stage, "version": str(version), "created": time.time()}
        if seconds is not None:
            extra["compute_s"] = round(seconds, 3)
        if none:
            extra["none"] = True
            kind = "pickle"
        size = write_entry(path, obj, kind, lossy_float16, extra)
        logger.debug("[stage_cache] stored %s/v%s %s (%.1f MB)", stage, version,
                     path.stem[:12], size / 1e6)
    except (OSError, TypeError, ValueError, pickle.PicklingError) as exc:
        logger.warning("[stage_cache] %s/v%s: not stored (%s)", stage, version, exc)


# --------------------------------------------------------------------------- housekeeping


@dataclass(frozen=True)
class Entry:
    stage: str
    version: str
    key: str
    path: Path
    size: int
    mtime: float


def entries(stage: str | None = None, root: str | Path | None = None) -> list[Entry]:
    """Every entry under the root (or one stage's), oldest use first."""
    base = cache_root(root)
    out = []
    if not base.exists():
        return out
    for sdir in sorted(p for p in base.iterdir() if p.is_dir()):
        if stage is not None and sdir.name != stage:
            continue
        for vdir in sorted(p for p in sdir.iterdir() if p.is_dir() and p.name.startswith("v")):
            for f in vdir.glob("*.npz"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                out.append(Entry(sdir.name, vdir.name[1:], f.stem, f, st.st_size, st.st_mtime))
    return sorted(out, key=lambda e: e.mtime)


_LEGACY_RE = re.compile(r"^(?P<h>[0-9a-f]{20})_(?P<what>depth|inst)_v(?P<v>\d+)(?P<rest>.*)\.npy$")
_LEGACY_INST_RE = re.compile(r"^_w(?P<w>\d+)_g(?P<g>\d+)$")


def depth_parts(pano_dig: str) -> tuple:
    """Key parts of the ``depth`` stage (the legacy ``pano_cache`` key: the pano rgb digest)."""
    return ("pano", Digest(pano_dig))


def instances_parts(pano_dig: str, window_px: int, grid_px: int) -> tuple:
    """Key parts of the ``instances`` stage (rgb digest, MobileSAM window and grid)."""
    return ("pano", Digest(pano_dig), "w", int(window_px), "g", int(grid_px))


def legacy_target(name: str) -> tuple[str, str, tuple] | None:
    """``(stage, version, key_parts)`` of a legacy ``pano_cache`` file name, or None."""
    m = _LEGACY_RE.match(name)
    if not m:
        return None
    if m["what"] == "depth":
        return ("depth", m["v"], depth_parts(m["h"])) if not m["rest"] else None
    mi = _LEGACY_INST_RE.match(m["rest"])
    if not mi:
        return None
    return "instances", m["v"], instances_parts(m["h"], int(mi["w"]), int(mi["g"]))


def legacy_path(pano_dig: str, what: str, legacy_dir: str | Path | None = None) -> Path:
    """The old ``pano_cache`` file of a pano (``what``: ``depth_v1``, ``inst_v3_w.._g..``)."""
    return Path(legacy_dir or LEGACY_PANO_CACHE_DIR) / f"{pano_dig}_{what}.npy"


def legacy_entries(legacy_dir: str | Path | None = None) -> list[Path]:
    d = Path(legacy_dir or LEGACY_PANO_CACHE_DIR)
    return sorted(d.glob("*.npy")) if d.exists() else []


def migrate_pano_cache(legacy_dir: str | Path | None = None, root: str | Path | None = None,
                       delete: bool = False, dry_run: bool = False,
                       lossy_float16: bool = False) -> list[dict]:
    """Convert every legacy ``pano_cache/*.npy`` to a compact entry (the key the wiring
    computes for the same pano), checked by reading it back (exact unless ``lossy_float16``).
    ``delete``: remove each legacy file once its entry checks out. Returns one row per file."""
    rows = []
    for f in legacy_entries(legacy_dir):
        tgt = legacy_target(f.name)
        row = {"file": f.name, "old_bytes": f.stat().st_size, "new_bytes": None, "status": ""}
        rows.append(row)
        if tgt is None:
            row["status"] = "skipped (unknown name)"
            continue
        stage, version, parts = tgt
        dst = entry_path(stage, version, stage_key(stage, version, parts), root)
        row["entry"] = str(dst)
        if dry_run:
            row["status"] = "would convert"
            continue
        try:
            a = np.load(f, allow_pickle=False)
            if not dst.exists():
                write_entry(dst, a, "npy", lossy_float16,
                            {"stage": stage, "version": version, "created": time.time(),
                             "migrated_from": f.name})
            back, _meta = read_entry(dst)
            ok = back.shape == a.shape and back.dtype == a.dtype and (
                np.array_equal(back, a) if not lossy_float16 else
                np.allclose(back, a, rtol=1e-3, atol=1e-4, equal_nan=True))
            if not ok:
                row["status"] = "MISMATCH (kept legacy)"
                continue
            row["new_bytes"] = dst.stat().st_size
            row["status"] = "converted"
            if delete:
                f.unlink()
                row["status"] += ", legacy deleted"
        except _READ_ERRORS as exc:
            row["status"] = f"failed: {exc}"
    return rows


def summary(root: str | Path | None = None) -> list[dict]:
    """Count and size per stage and version."""
    agg: dict = {}
    for e in entries(root=root):
        r = agg.setdefault((e.stage, e.version), {"stage": e.stage, "version": e.version,
                                                  "n": 0, "bytes": 0, "last_used": 0.0})
        r["n"] += 1
        r["bytes"] += e.size
        r["last_used"] = max(r["last_used"], e.mtime)
    return [agg[k] for k in sorted(agg)]


def prune(stage: str | None = None, version: str | None = None,
          keep_version: str | None = None, older_than_days: float | None = None,
          max_total_mb: float | None = None, root: str | Path | None = None,
          dry_run: bool = False) -> list[Entry]:
    """Remove entries; returns those removed (or that would be, with ``dry_run``).

    Filters combine: ``stage`` limits to one stage; ``version`` removes that version;
    ``keep_version`` removes every other version (of ``stage``, or of every stage);
    ``older_than_days`` removes entries unused for that long; ``max_total_mb`` then removes the
    least recently used until what is left fits. With no filter at all nothing is removed;
    pass ``older_than_days=0`` to clear."""
    es = entries(stage, root)
    now = time.time()
    drop: list[Entry] = []
    filtered = version is not None or keep_version is not None or older_than_days is not None
    if filtered:
        for e in es:
            if version is not None and e.version != str(version):
                continue
            if keep_version is not None and e.version == str(keep_version):
                continue
            if older_than_days is not None and now - e.mtime < older_than_days * 86400.0:
                continue
            drop.append(e)
    if max_total_mb is not None:
        gone = {e.path for e in drop}
        left = [e for e in es if e.path not in gone]
        total = sum(e.size for e in left)
        for e in left:                                   # oldest use first
            if total <= max_total_mb * 1e6:
                break
            drop.append(e)
            total -= e.size
    if not dry_run:
        for e in drop:
            try:
                e.path.unlink()
            except OSError as exc:
                logger.warning("[stage_cache] could not remove %s: %s", e.path, exc)
        for d in {e.path.parent for e in drop}:
            try:
                d.rmdir()                                # only when empty
                d.parent.rmdir()
            except OSError:
                pass
    return drop


# --------------------------------------------------------------------------- CLI


def _mb(n) -> str:
    return "-" if n is None else f"{n / 1e6:8.2f} MB"


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m city2stl.skyline._pano.stage_cache",
                                 description="List, prune and migrate the drone-seed stage cache.")
    ap.add_argument("--root", default=None, help=f"cache root (default {DEFAULT_ROOT})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="count and size per stage/version")
    p.add_argument("--stage")
    p.add_argument("--entries", action="store_true", help="one line per entry")
    p.add_argument("--legacy", action="store_true", help="also the old pano_cache/*.npy")
    p = sub.add_parser("prune", help="remove entries")
    p.add_argument("--stage")
    p.add_argument("--version")
    p.add_argument("--keep-version")
    p.add_argument("--older-than-days", type=float)
    p.add_argument("--max-mb", type=float)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("migrate", help="convert pano_cache/*.npy to compact entries")
    p.add_argument("--src", default=None, help=f"legacy folder (default {LEGACY_PANO_CACHE_DIR})")
    p.add_argument("--delete", action="store_true", help="delete each legacy file once checked")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(list(argv) if argv is not None else None)

    if a.cmd == "list":
        rows = summary(a.root)
        if a.stage:
            rows = [r for r in rows if r["stage"] == a.stage]
        print(f"root: {cache_root(a.root)}  (mode: {mode()})")
        for r in rows:
            print(f"  {r['stage']:<10} v{r['version']:<8} {r['n']:5d} entries {_mb(r['bytes'])}"
                  f"  last used {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['last_used']))}")
        print(f"  total {_mb(sum(r['bytes'] for r in rows))}")
        if a.entries:
            for e in entries(a.stage, a.root):
                print(f"    {e.stage}/v{e.version}/{e.key}  {_mb(e.size)}")
        if a.legacy:
            leg = legacy_entries()
            print(f"legacy {LEGACY_PANO_CACHE_DIR}: {len(leg)} files "
                  f"{_mb(sum(f.stat().st_size for f in leg))}")
        return 0
    if a.cmd == "prune":
        if a.version is None and a.keep_version is None and a.older_than_days is None \
                and a.max_mb is None:
            print("prune: give --version, --keep-version, --older-than-days or --max-mb")
            return 2
        gone = prune(a.stage, a.version, a.keep_version, a.older_than_days, a.max_mb, a.root,
                     a.dry_run)
        verb = "would remove" if a.dry_run else "removed"
        print(f"{verb} {len(gone)} entries, {_mb(sum(e.size for e in gone))}")
        return 0
    if a.cmd == "migrate":
        rows = migrate_pano_cache(a.src, a.root, delete=a.delete, dry_run=a.dry_run)
        for r in rows:
            print(f"  {r['file']:<48} {_mb(r['old_bytes'])} -> {_mb(r['new_bytes'])}  {r['status']}")
        old = sum(r["old_bytes"] for r in rows if r["new_bytes"])
        new = sum(r["new_bytes"] for r in rows if r["new_bytes"])
        if new:
            print(f"  total {_mb(old)} -> {_mb(new)} ({old / new:.1f}x smaller)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

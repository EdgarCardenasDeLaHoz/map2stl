"""Classified roof shapes kept on disk, one row per footprint.

Classifying a city's untagged roofs (``roof_classifier.classify_roof_shapes`` with the
``roof_shape_gbm`` checkpoint) costs about 11 ms a building, so Granada's 24k buildings
took minutes on every server start: the result only lived in an in-memory memo
(``app/server/core/city_data.py::with_roof_shapes``). This keeps each answer in
``<CACHE_ROOT>/roof_shapes.sqlite`` keyed by what the model reads, so a restart, a
preview after an export, or a slightly moved box classifies only the footprints it has
not seen.

The key is a hash of :data:`VERSION`, the checkpoint's content hash (:func:`model_id`),
the footprint geometry and the tags the model reads (:data:`TAG_KEYS`,
``roof_features._tag_features``). A retrained checkpoint therefore misses every row
and reclassifies; old rows are never read again. Only classified buildings are
stored: a skip (no tile, too large) may succeed next time. Every failure is logged and
treated as a miss; the cache never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import geo2stl.cache as _gc

logger = logging.getLogger(__name__)

#: Bump when the stored meaning of a row changes (forces a reclassification).
VERSION = 1
#: Tags ``roof_features._tag_features`` reads; part of the key.
TAG_KEYS = ("building", "building:levels", "height")
_SQL_CHUNK = 900          # under SQLite's default 999 host parameters
_model_ids: dict = {}


def db_path() -> Path:
    """The cache file, under ``geo2stl.cache.CACHE_ROOT`` (read at call time)."""
    return Path(_gc.CACHE_ROOT) / "roof_shapes.sqlite"


def model_id(path: str | None = None) -> str:
    """A short content hash of the checkpoint at *path* (default ``roof_model.MODEL_PATH``),
    computed once per file size and mtime."""
    if path is None:
        from city2stl import roof_model
        path = roof_model.MODEL_PATH
    st = os.stat(path)
    k = (path, st.st_size, st.st_mtime_ns)
    if k not in _model_ids:
        with open(path, "rb") as f:
            _model_ids[k] = hashlib.sha1(f.read()).hexdigest()[:16]
    return _model_ids[k]


def feature_key(feat: dict, model: str) -> str:
    """The cache key of one building feature for checkpoint *model*."""
    props = (feat or {}).get("properties") or {}
    blob = json.dumps([VERSION, model, (feat or {}).get("geometry"),
                       [props.get(k) for k in TAG_KEYS]], separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def lookup(keys: list[str]) -> dict[str, tuple[str, float, str]]:
    """``{key: (roof:shape, confidence, roof_source)}`` for the *keys* on disk."""
    path = db_path()
    if not keys or not path.exists():
        return {}
    out: dict[str, tuple[str, float, str]] = {}
    try:
        with closing(sqlite3.connect(path, timeout=10)) as con:
            for i in range(0, len(keys), _SQL_CHUNK):
                chunk = keys[i:i + _SQL_CHUNK]
                q = ("SELECT key, shape, confidence, source FROM roofs WHERE key IN ("
                     + ",".join("?" * len(chunk)) + ")")
                for key, shape, conf, source in con.execute(q, chunk):
                    out[key] = (shape, conf, source)
    except sqlite3.Error as exc:
        logger.warning("Roof cache read failed (%s); classifying again", exc)
        return {}
    return out


def store(rows) -> None:
    """Write ``(key, roof:shape, confidence, roof_source)`` rows (replacing old ones)."""
    rows = list(rows)
    if not rows:
        return
    path = db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as con, con:
            con.execute("CREATE TABLE IF NOT EXISTS roofs (key TEXT PRIMARY KEY, "
                        "shape TEXT NOT NULL, confidence REAL, source TEXT)")
            con.executemany("INSERT OR REPLACE INTO roofs VALUES (?, ?, ?, ?)", rows)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Roof cache write failed: %s", exc)

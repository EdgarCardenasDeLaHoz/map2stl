"""
core/db.py — SQLite database initialisation and connection helpers.

Extracted from location_picker.py (backend refactor, step 10).
Replaces the dual-JSON storage (coordinates.json + region_settings.json)
with a single WAL-mode SQLite database at map2stl/data.db.

Schema
------
regions
    name           TEXT PRIMARY KEY
    label          TEXT
    description    TEXT
    north          REAL NOT NULL
    south          REAL NOT NULL
    east           REAL NOT NULL
    west           REAL NOT NULL
    dim            INTEGER DEFAULT 600
    depth_scale    REAL    DEFAULT 0.5
    water_scale    REAL    DEFAULT 0.05
    height         REAL    DEFAULT 25.0
    base           REAL    DEFAULT 5.0
    subtract_water INTEGER DEFAULT 1
    sat_scale      INTEGER DEFAULT 500
    CHECK (north > south)

region_settings
    region_name   TEXT PRIMARY KEY REFERENCES regions(name) ON DELETE CASCADE
    settings_json TEXT   -- full panel settings blob (JSON string)

region_landmarks   (F-LANDMARK §3: per-building overrides, one row per OSM id)
    region_name   TEXT REFERENCES regions(name) ON DELETE CASCADE
    osm_id        TEXT   -- "way/123"
    spec_json     TEXT   -- {"kind": "mesh"|"ndsm"|"osm", ...} (city2stl.landmarks)
    updated_at    REAL
    PRIMARY KEY (region_name, osm_id)

    Its own table rather than a key in region_settings: the settings blob is
    replaced wholesale by every panel save, which would drop overrides it did
    not know about.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from app.server.config import COORDINATES_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
# MAP2STL_DB_PATH points a server at another database (the performance audit runs on
# a copy, so its settings changes never reach the user's regions).
DB_PATH: Path = (Path(os.environ["MAP2STL_DB_PATH"]) if os.environ.get("MAP2STL_DB_PATH")
                 else COORDINATES_PATH.parent / "data.db")

_CREATE_REGIONS = """
CREATE TABLE IF NOT EXISTS regions (
    name           TEXT PRIMARY KEY,
    label          TEXT,
    description    TEXT,
    north          REAL NOT NULL,
    south          REAL NOT NULL,
    east           REAL NOT NULL,
    west           REAL NOT NULL,
    dim            INTEGER DEFAULT 600,
    depth_scale    REAL    DEFAULT 0.5,
    water_scale    REAL    DEFAULT 0.05,
    height         REAL    DEFAULT 25.0,
    base           REAL    DEFAULT 5.0,
    subtract_water INTEGER DEFAULT 1,
    sat_scale      INTEGER DEFAULT 500,
    CHECK (north > south)
);
"""

_CREATE_REGION_SETTINGS = """
CREATE TABLE IF NOT EXISTS region_settings (
    region_name   TEXT PRIMARY KEY REFERENCES regions(name) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}'
);
"""

_CREATE_REGION_LANDMARKS = """
CREATE TABLE IF NOT EXISTS region_landmarks (
    region_name   TEXT NOT NULL REFERENCES regions(name) ON DELETE CASCADE,
    osm_id        TEXT NOT NULL,
    spec_json     TEXT NOT NULL DEFAULT '{}',
    updated_at    REAL,
    PRIMARY KEY (region_name, osm_id)
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_db(path: Path | None = None) -> sqlite3.Connection:
    """
    Return a sqlite3 Connection to *path* (defaults to DB_PATH).

    - WAL mode is enabled for crash safety.
    - Row factory is set so rows behave like dicts.
    - Foreign keys are enforced.
    """
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path | None = None) -> None:
    """
    Create the database schema if it does not already exist.
    Safe to call multiple times (all statements use IF NOT EXISTS).
    """
    p = path or DB_PATH
    with get_db(p) as conn:
        conn.execute(_CREATE_REGIONS)
        conn.execute(_CREATE_REGION_SETTINGS)
        conn.execute(_CREATE_REGION_LANDMARKS)
        conn.commit()
    logger.info(f"Database initialised at {p}")

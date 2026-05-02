"""
core/db.py — SQLite database initialisation and connection helpers.

Extracted from location_picker.py (backend refactor, step 10).
Replaces the dual-JSON storage (coordinates.json + region_settings.json)
with a single WAL-mode SQLite database at strm2stl/data.db.

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
"""

from __future__ import annotations

import sqlite3
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
try:
    from app.server.config import COORDINATES_PATH
    _STRM2STL_DIR = COORDINATES_PATH.parent
except ImportError:
    _STRM2STL_DIR = Path(__file__).parent.parent.parent

DB_PATH: Path = _STRM2STL_DIR / "data.db"

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
    tags           TEXT    DEFAULT '',
    CHECK (north > south)
);
"""

_CREATE_REGION_SETTINGS = """
CREATE TABLE IF NOT EXISTS region_settings (
    region_name   TEXT PRIMARY KEY REFERENCES regions(name) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}'
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_db(path: Optional[Path] = None) -> sqlite3.Connection:
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


def init_db(path: Optional[Path] = None) -> None:
    """
    Create the database schema if it does not already exist.
    Safe to call multiple times (all statements use IF NOT EXISTS).
    Runs lightweight migrations for columns added after initial release.
    """
    p = path or DB_PATH
    with get_db(p) as conn:
        conn.execute(_CREATE_REGIONS)
        conn.execute(_CREATE_REGION_SETTINGS)
        # Migration: add tags column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE regions ADD COLUMN tags TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Seed: tag Granada as training data city if it exists and has no tags yet
        conn.execute(
            "UPDATE regions SET tags='training_city' WHERE name='Granada' AND (tags IS NULL OR tags='')"
        )
        conn.commit()
    logger.info(f"Database initialised at {p}")

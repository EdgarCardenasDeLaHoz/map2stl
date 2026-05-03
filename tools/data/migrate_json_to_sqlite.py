"""
scripts/migrate_json_to_sqlite.py — One-time migration from JSON files to SQLite.

Reads:
  strm2stl/coordinates.json      → regions table
  strm2stl/region_settings.json  → region_settings table

Writes:
  strm2stl/data.db               (created / updated via core.db)

After a successful migration the originals are renamed to .json.bak.
The script is idempotent: re-running it inserts or replaces rows without
duplicating data, and skips backing up files that were already renamed.

Usage (from the strm2stl/ directory or any location):
    python scripts/migrate_json_to_sqlite.py
    python scripts/migrate_json_to_sqlite.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths relative to this script's location (strm2stl/tools/)
# ---------------------------------------------------------------------------
_TOOLS_DIR      = Path(__file__).resolve().parent   # strm2stl/tools/
_STRM2STL_DIR   = _TOOLS_DIR.parent                 # strm2stl/

# Add strm2stl/ to path so we can import app.server.core.db and app.config
sys.path.insert(0, str(_STRM2STL_DIR))

try:
    from app.server.core.db import get_db, init_db, DB_PATH
except ImportError as exc:
    sys.exit(f"Cannot import app.server.core.db: {exc}\nRun this script from the strm2stl root or ensure the venv is active.")

try:
    from app.server.config import COORDINATES_PATH, REGION_SETTINGS_PATH
except ImportError:
    COORDINATES_PATH     = _STRM2STL_DIR / "coordinates.json"
    REGION_SETTINGS_PATH = _STRM2STL_DIR / "region_settings.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> object:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _migrate_regions(conn, regions: list[dict]) -> int:
    sql = """
    INSERT OR REPLACE INTO regions
        (name, label, description, north, south, east, west,
         dim, depth_scale, water_scale, height, base, subtract_water, sat_scale)
    VALUES
        (:name, :label, :description, :north, :south, :east, :west,
         :dim, :depth_scale, :water_scale, :height, :base, :subtract_water, :sat_scale)
    """
    count = 0
    for r in regions:
        params_raw = r.get("parameters") or {}
        # parameters may be a nested dict or already flat
        if isinstance(params_raw, dict):
            params = params_raw
        else:
            params = {}

        row = {
            "name":           r.get("name", ""),
            "label":          r.get("label") or r.get("continent"),
            "description":    r.get("description"),
            "north":          float(r["north"]),
            "south":          float(r["south"]),
            "east":           float(r["east"]),
            "west":           float(r["west"]),
            "dim":            int(params.get("dim",            r.get("dim",            600))),
            "depth_scale":    float(params.get("depth_scale",  r.get("depth_scale",    0.5))),
            "water_scale":    float(params.get("water_scale",  r.get("water_scale",    0.05))),
            "height":         float(params.get("height",       r.get("height",         25.0))),
            "base":           float(params.get("base",         r.get("base",           5.0))),
            "subtract_water": int(bool(params.get("subtract_water", r.get("subtract_water", True)))),
            "sat_scale":      int(params.get("sat_scale",      r.get("sat_scale",      500))),
        }
        if not row["name"]:
            print(f"  [skip] region missing name: {r}")
            continue
        conn.execute(sql, row)
        count += 1
    return count


def _migrate_settings(conn, settings: dict) -> int:
    sql = """
    INSERT OR REPLACE INTO region_settings (region_name, settings_json)
    VALUES (?, ?)
    """
    count = 0
    for name, blob in settings.items():
        if not name:
            continue
        settings_json = json.dumps(blob) if not isinstance(blob, str) else blob
        conn.execute(sql, (name, settings_json))
        count += 1
    return count


def _backup(path: Path, dry_run: bool) -> None:
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        print(f"  backup already exists, skipping: {bak.name}")
        return
    if dry_run:
        print(f"  [dry-run] would rename {path.name} -> {bak.name}")
    else:
        path.rename(bak)
        print(f"  renamed {path.name} -> {bak.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate coordinates.json and region_settings.json to SQLite.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing anything")
    parser.add_argument("--db", default=None, help="Override database path")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    dry_run: bool = args.dry_run

    print(f"Database : {db_path}")
    print(f"Regions  : {COORDINATES_PATH}")
    print(f"Settings : {REGION_SETTINGS_PATH}")
    print(f"Dry run  : {dry_run}\n")

    # Load source data
    raw_coords   = _load_json(COORDINATES_PATH)
    raw_settings = _load_json(REGION_SETTINGS_PATH)

    # Normalise: coordinates.json may be a list or {"regions": [...]}
    if raw_coords is None:
        regions_list: list[dict] = []
        print("  coordinates.json not found — skipping region migration")
    elif isinstance(raw_coords, list):
        regions_list = raw_coords
    elif isinstance(raw_coords, dict):
        regions_list = raw_coords.get("regions") or raw_coords.get("coordinates") or list(raw_coords.values())
    else:
        regions_list = []

    # Normalise: region_settings.json is {name: {settings_dict}}
    if raw_settings is None:
        settings_dict: dict = {}
        print("  region_settings.json not found — skipping settings migration")
    elif isinstance(raw_settings, dict):
        settings_dict = raw_settings
    else:
        settings_dict = {}

    if not regions_list and not settings_dict:
        print("Nothing to migrate.")
        return

    if dry_run:
        print(f"[dry-run] would insert/replace {len(regions_list)} region(s) and {len(settings_dict)} settings entry(ies).")
        return

    # Initialise schema
    init_db(db_path)

    with get_db(db_path) as conn:
        reg_count = _migrate_regions(conn, regions_list)
        set_count = _migrate_settings(conn, settings_dict)
        conn.commit()

    print(f"Migrated {reg_count} region(s) and {set_count} settings entry(ies) to {db_path.name}")

    # Backup originals
    if regions_list and COORDINATES_PATH.exists():
        _backup(COORDINATES_PATH, dry_run=False)
    if settings_dict and REGION_SETTINGS_PATH.exists():
        _backup(REGION_SETTINGS_PATH, dry_run=False)

    print("\nMigration complete.")


if __name__ == "__main__":
    main()

"""
tools/import_regions.py — Import a geographic-coordinate CSV into the strm2stl region database.

Expects CSV columns: name, label, north, south, east, west, rotation
(as produced by tile_to_geo.py).

Features:
  - Assigns a configurable label prefix to distinguish imported regions from hand-drawn ones
  - Enforces unique names: if "Lake George" already exists, inserts as "Lake George_2", etc.
  - Stores rotation angle in region_settings JSON (rotation field not yet in regions table)
  - Dry-run mode prints what would be inserted without touching the database
  - Can be imported as a module or run as a script

Usage (script):
    # Step 1 — convert tile CSV to geo CSV
    python tools/tile_to_geo.py

    # Step 2 — import geo CSV into database
    python tools/import_regions.py [geo_csv] [--label coorlist] [--dry-run]

Usage (module):
    from tools.import_regions import import_geo_csv
    import_geo_csv(Path("locations/CoOrLists_geo.csv"), label_prefix="coorlist")
"""

import csv
import json
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Database helpers (mirrors core/db.py without FastAPI dependency)
# ---------------------------------------------------------------------------

def _get_db(db_path: Path):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _existing_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM regions").fetchall()
    return {r["name"] for r in rows}


def _unique_name(base: str, existing: set[str]) -> str:
    """Return `base` if unused, otherwise `base_2`, `base_3`, …"""
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


# ---------------------------------------------------------------------------
# Import function
# ---------------------------------------------------------------------------

def import_geo_csv(
    csv_path: Path,
    db_path: Optional[Path] = None,
    label_prefix: str = "coorlist",
    dry_run: bool = False,
) -> list[dict]:
    """
    Import a geographic-coordinate CSV into the strm2stl SQLite database.

    Parameters
    ----------
    csv_path     : Path to CSV with columns: name, label, north, south, east, west, rotation
    db_path      : Path to data.db (auto-detected from strm2stl layout if None)
    label_prefix : Label assigned to every imported region. Shown in the UI region list.
                   Use a distinct value (e.g. "coorlist") so these regions are visually
                   distinct from hand-drawn ones which have label=None or label="".
    dry_run      : If True, print what would be inserted without writing anything.

    Returns
    -------
    List of dicts describing each inserted (or would-be-inserted) row.
    """
    # Locate DB
    if db_path is None:
        here = Path(__file__).parent
        db_path = here.parent / "data.db"
    if not db_path.exists() and not dry_run:
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = _get_db(db_path) if not dry_run else None
    existing = _existing_names(conn) if conn else set()

    inserted = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name_raw = row.get("name", "").strip()
            if not name_raw:
                continue

            try:
                north    = float(row["north"])
                south    = float(row["south"])
                east     = float(row["east"])
                west     = float(row["west"])
                rotation = float(row.get("rotation", 0) or 0)
            except (KeyError, ValueError) as e:
                print(f"  [skip] {name_raw!r}: {e}")
                continue

            if north <= south:
                print(f"  [skip] {name_raw!r}: north ({north}) not > south ({south})")
                continue

            unique = _unique_name(name_raw, existing)
            existing.add(unique)   # reserve the name for subsequent rows

            settings_json = json.dumps({"rotation": rotation}) if rotation != 0 else "{}"

            entry = {
                "name":          unique,
                "label":         label_prefix,
                "description":   f"Imported from {csv_path.name}",
                "north":         north,
                "south":         south,
                "east":          east,
                "west":          west,
                "settings_json": settings_json,
                "rotation":      rotation,
            }
            inserted.append(entry)

            if dry_run:
                rot_str = f"  rotation={rotation}°" if rotation else ""
                renamed = f"  (renamed from {name_raw!r})" if unique != name_raw else ""
                print(f"  WOULD INSERT: {unique!r} [{north:.4f},{south:.4f},{east:.4f},{west:.4f}]{rot_str}{renamed}")
            else:
                conn.execute(
                    """INSERT INTO regions (name, label, description, north, south, east, west)
                       VALUES (:name, :label, :description, :north, :south, :east, :west)""",
                    entry,
                )
                # Store rotation in region_settings if nonzero
                conn.execute(
                    """INSERT INTO region_settings (region_name, settings_json)
                       VALUES (:name, :settings_json)
                       ON CONFLICT(region_name) DO UPDATE SET settings_json=excluded.settings_json""",
                    entry,
                )

    if conn:
        conn.commit()
        conn.close()

    if not dry_run:
        print(f"Imported {len(inserted)} regions (label={label_prefix!r}) -> {db_path}")
    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Import a geo CSV into the strm2stl region database")
    parser.add_argument(
        "csv",
        nargs="?",
        default=str(Path(__file__).parent.parent.parent / "locations" / "CoOrLists_geo.csv"),
        help="Path to geo CSV (default: locations/CoOrLists_geo.csv)",
    )
    parser.add_argument(
        "--label",
        default="coorlist",
        help="Label prefix assigned to imported regions (default: 'coorlist')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted without writing to the database",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        print("Run tools/tile_to_geo.py first to generate it.")
        sys.exit(1)

    import_geo_csv(csv_path, label_prefix=args.label, dry_run=args.dry_run)

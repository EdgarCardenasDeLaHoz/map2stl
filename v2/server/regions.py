"""Saved locations, read from v1's database.

v2 shares data.db with v1 so both versions describe the same places and a comparison is
about the software rather than about which one has better test data. Reads open the file
read-only; the only write is the settings blob, and only when the user saves.

The settings blob is where v2 diverges. v1 stored an unversioned, unmigrated JSON object,
so two field renames that happened long ago are still present in live rows and are guessed
at by the client every time it loads a region. v2 keeps its own blob under a separate key
inside the same JSON, stamped with a schema version and migrated on read in exactly one
place. v1's keys are left untouched, so both versions can run against the same row.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .config import DB_PATH
from .models import SETTINGS_SCHEMA_VERSION, BBox, Region, RegionSettings

logger = logging.getLogger(__name__)

# v2's settings live under this key inside the existing settings_json object, so writing
# them cannot corrupt what v1 reads.
V2_KEY = "v2"


def _connect(readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_regions() -> list[Region]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, label, description, north, south, east, west FROM regions ORDER BY name"
        ).fetchall()
    return [
        Region(
            name=r["name"],
            label=r["label"],
            description=r["description"],
            bbox=BBox(north=r["north"], south=r["south"], east=r["east"], west=r["west"]),
        )
        for r in rows
    ]


def get_region(name: str) -> Region | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT name, label, description, north, south, east, west FROM regions WHERE name = ?",
            (name,),
        ).fetchone()
    if row is None:
        return None
    return Region(
        name=row["name"],
        label=row["label"],
        description=row["description"],
        bbox=BBox(north=row["north"], south=row["south"], east=row["east"], west=row["west"]),
    )


def get_settings(name: str) -> RegionSettings:
    """Return this region's v2 settings, seeding them from v1's blob the first time.

    A region that has only ever been used in v1 still opens with the user's own numbers
    rather than with defaults, which is what makes a side-by-side comparison fair.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT settings_json FROM region_settings WHERE region_name = ?", (name,)
        ).fetchone()

    blob: dict[str, Any] = {}
    if row and row["settings_json"]:
        try:
            blob = json.loads(row["settings_json"])
        except json.JSONDecodeError:
            logger.warning("settings_json for %s is not valid JSON; using defaults", name)

    if isinstance(blob.get(V2_KEY), dict):
        return _migrate(blob[V2_KEY])
    return _from_v1_blob(blob)


def save_settings(name: str, settings: RegionSettings) -> None:
    """Write v2's settings into the region's blob, preserving every v1 key.

    v1 had a defect here that this shape avoids: re-saving a region destroyed its stored
    settings, because the write replaced the whole row rather than merging into it.
    """
    with _connect(readonly=False) as conn:
        row = conn.execute(
            "SELECT settings_json FROM region_settings WHERE region_name = ?", (name,)
        ).fetchone()
        blob: dict[str, Any] = {}
        if row and row["settings_json"]:
            try:
                blob = json.loads(row["settings_json"])
            except json.JSONDecodeError:
                logger.warning("Replacing unparseable settings_json for %s", name)
        blob[V2_KEY] = settings.model_dump()
        conn.execute(
            "INSERT INTO region_settings (region_name, settings_json) VALUES (?, ?) "
            "ON CONFLICT(region_name) DO UPDATE SET settings_json = excluded.settings_json",
            (name, json.dumps(blob)),
        )
        conn.commit()


def _migrate(raw: dict[str, Any]) -> RegionSettings:
    """Bring a stored v2 blob up to the current schema version.

    There is only one version so far, so this is a validation pass. The point is that the
    hook exists before it is needed: v1 reached four renames deep with no migration path,
    and by then the only place left to guess the shape was the client.
    """
    version = int(raw.get("schemaVersion", 0))
    if version > SETTINGS_SCHEMA_VERSION:
        logger.warning(
            "Region settings claim schema %d but this build knows %d; "
            "unknown fields will be dropped", version, SETTINGS_SCHEMA_VERSION,
        )
    try:
        return RegionSettings.model_validate({**raw, "schemaVersion": SETTINGS_SCHEMA_VERSION})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stored settings failed validation (%s); falling back to defaults", exc)
        return RegionSettings()


def _from_v1_blob(blob: dict[str, Any]) -> RegionSettings:
    """Translate v1's ten-group settings object into v2's five.

    v1's groups are dem, projection, view, water, esa, satellite, export, split, city and
    hydrology. Only the parts that drive this pipeline are carried across; the rest belong
    to subsystems v2 does not have. Where v1 renamed a field and never migrated, both spellings
    are accepted, which is the debt being paid off here rather than passed on.
    """
    dem_v1 = blob.get("dem") or {}
    proj_v1 = blob.get("projection") or {}
    export_v1 = blob.get("export") or {}
    # "esa" was renamed to "satellite" and never migrated, so live rows hold either.
    sat_v1 = blob.get("satellite") or blob.get("esa") or {}

    settings = RegionSettings()
    settings.dem.source = dem_v1.get("dem_source", settings.dem.source)
    settings.dem.dim = int(dem_v1.get("dim", settings.dem.dim))
    settings.dem.depthScale = float(dem_v1.get("depth_scale", settings.dem.depthScale))
    settings.dem.waterScale = float(dem_v1.get("water_scale", settings.dem.waterScale))
    settings.dem.subtractWater = bool(dem_v1.get("subtract_water", settings.dem.subtractWater))
    settings.dem.maintainDimensions = bool(
        dem_v1.get("maintain_dimensions", settings.dem.maintainDimensions)
    )

    settings.projection.name = proj_v1.get("projection", settings.projection.name)
    settings.projection.clipValidRegion = bool(
        proj_v1.get("clip_valid_region", settings.projection.clipValidRegion)
    )

    settings.overlays.satellite = bool(sat_v1.get("show_sat", False))

    settings.model.modelHeight = float(export_v1.get("model_height", settings.model.modelHeight))
    settings.model.baseHeight = float(export_v1.get("base_height", settings.model.baseHeight))
    settings.model.exaggeration = float(export_v1.get("exaggeration", settings.model.exaggeration))
    settings.model.mmPerPixel = float(export_v1.get("mm_per_pixel", settings.model.mmPerPixel))
    settings.model.seaLevelCap = bool(export_v1.get("sea_level_cap", settings.model.seaLevelCap))

    if export_v1.get("label_text"):
        settings.export.engraveLabel = bool(export_v1.get("engrave_label", False))
        settings.export.labelText = str(export_v1["label_text"])

    return settings

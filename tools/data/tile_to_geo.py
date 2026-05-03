"""
tools/tile_to_geo.py — Convert a tile-index coordinate list to geographic coordinates.

The legacy CoOrLists.csv stores bounding boxes as SRTM tile numbers + pixel offsets:
    Filename, Rotation, GridX1, GridX2, GridY1, GridY2, X1, X2, Y1, Y2

Where:
    GridX = tile column (TX): each tile spans 5° longitude; TX=37 → 0°, increases east
    GridY = tile row    (TY): each tile spans 5° latitude;  TY=13 → 0°, increases south
    X     = row pixel in the mosaic — the LATITUDE axis (north→south, 0 at top)
    Y     = col pixel in the mosaic — the LONGITUDE axis (west→east, 0 at left)

Axis convention (from dem2stl.py):
    get_bounds_geo passes (TX, TY, Y, X) to tile_num_2_geo_coor — i.e. longitude
    pixel first, latitude pixel second.  X indexes rows (latitude) and Y indexes
    columns (longitude), opposite to the column names' implied order.

Both corners use GridX1/GridY1 as the tile anchor (matching get_bounds_geo).
X2/Y2 may exceed 6000 for multi-tile regions — the offset is absolute within
the mosaic, as computed by get_dem_geo.

Corner 1 (GridX1, GridY1, Y1_lon, X1_lat) → northwest corner (north, west)
Corner 2 (GridX1, GridY1, Y2_lon, X2_lat) → southeast corner (south, east)

Usage:
    python tools/tile_to_geo.py [input_csv] [output_csv]

Defaults:
    input  = locations/CoOrLists.csv  (relative to project root)
    output = locations/CoOrLists_geo.csv
"""

import csv
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Coordinate conversion — mirrors dem2stl.tile_num_2_geo_coor exactly
# ---------------------------------------------------------------------------

def tile_to_geo(tx: float, ty: float, py_lon: float, px_lat: float) -> tuple[float, float]:
    """
    Convert tile anchor + mosaic pixel offsets to (latitude, longitude).

    Matches dem2stl.tile_num_2_geo_coor(TX, TY, PX, PY) exactly, where the
    original call order is (TX, TY, Y_csv, X_csv) — Y (longitude col) first,
    X (latitude row) second.

    Parameters
    ----------
    tx      : tile X index (longitude tile; TX=37 → 0° lon, increases east)
    ty      : tile Y index (latitude tile; TY=13 → 0° lat, increases south)
    py_lon  : longitude pixel — the CSV "Y" column (col offset, west→east)
    px_lat  : latitude pixel  — the CSV "X" column (row offset, north→south)
              May exceed 6000 for multi-tile regions.

    Returns
    -------
    (lat, lon) in decimal degrees
    """
    lon = 180.0 * (tx - 37) / 36.0 + round(py_lon / 6000.0 * 5.0, 4)
    lat_internal = 60.0 * (ty - 13) / 12.0 + round(px_lat / 6000.0 * 5.0, 4)
    lat = -lat_internal
    return (round(lat, 6), round(lon, 6))


# ---------------------------------------------------------------------------
# CSV conversion
# ---------------------------------------------------------------------------

def convert_csv(input_path: Path, output_path: Path) -> int:
    """
    Read a tile-index CSV and write a geographic-coordinate CSV.

    Input columns:  Filename, Rotation, GridX1, GridX2, GridY1, GridY2, X1, X2, Y1, Y2
    Output columns: name, label, north, south, east, west, rotation

    Returns the number of rows written.
    """
    rows_written = 0
    with open(input_path, newline="", encoding="utf-8-sig") as fin, \
         open(output_path, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin)
        fieldnames = ["name", "label", "north", "south", "east", "west", "rotation"]
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()

        for i, row in enumerate(reader, start=2):   # line 2 = first data row
            name = row["Filename"].strip()
            if not name:
                continue

            try:
                gx1 = float(row["GridX1"])
                gx2 = float(row["GridX2"])
                gy1 = float(row["GridY1"])
                gy2 = float(row["GridY2"])
                x1  = float(row["X1"])
                x2  = float(row["X2"])
                y1  = float(row["Y1"])
                y2  = float(row["Y2"])
                rotation = float(row["Rotation"])
            except (KeyError, ValueError) as e:
                print(f"  [skip] row {i} ({name!r}): {e}")
                continue

            # Both corners use GridX1/GridY1 as the tile anchor.
            # Call order is (TX, TY, Y_lon, X_lat) matching get_bounds_geo:
            #   tile_num_2_geo_coor(GridX1, GridY1, Y1, X1)  → NW corner
            #   tile_num_2_geo_coor(GridX1, GridY1, Y2, X2)  → SE corner
            # X2/Y2 may exceed 6000 for multi-tile mosaic regions.
            north, west = tile_to_geo(gx1, gy1, y1, x1)
            south, east = tile_to_geo(gx1, gy1, y2, x2)

            # Safety: ensure north > south (rotation entries can have swapped extents)
            if north < south:
                north, south = south, north
            if east < west:
                east, west = east, west

            writer.writerow({
                "name":     name,
                "label":    "coorlist",
                "north":    north,
                "south":    south,
                "east":     east,
                "west":     west,
                "rotation": rotation,
            })
            rows_written += 1

    return rows_written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = Path(__file__).parent.parent.parent  # …/3D Maps/Code

    input_csv  = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "locations" / "CoOrLists.csv"
    output_csv = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "locations" / "CoOrLists_geo.csv"

    if not input_csv.exists():
        print(f"Input file not found: {input_csv}")
        sys.exit(1)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    n = convert_csv(input_csv, output_csv)
    print(f"Converted {n} rows -> {output_csv}")

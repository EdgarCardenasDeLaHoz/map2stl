# TODO_ADVANCED — 3D Building Data Research

> Research-phase notes. Nothing here is ready to implement — requires more investigation before any coding.
> Primary goal: get **photogrammetry-accurate 3D building geometry** into the app for STL/3MF export.

---

## Context

The app currently renders city overlays using OSM 2D footprints + estimated heights (via `fill_building_heights()`).
This is good for schematic 3D prints but lacks real building geometry (rooflines, setbacks, architectural detail).

The best sources of actual 3D building geometry:

| Source | Quality | Access | Cost | Status |
|--------|---------|--------|------|--------|
| **Google 3D Tiles (Photogrammetry)** | Excellent — real scans | API key required | Pay-per-request | **Research target** |
| OSM Building footprints + height | Schematic only | Free/Overpass | Free | Already in app |
| CityGML / LOD2 city models | Good — gov data | City-specific | Free (some cities) | Not researched |
| OpenAerialMap | Imagery only | Free | Free | Not applicable |

---

## Approach 1 — Pure Python: py3dtiles + trimesh pipeline

> Documented by user research. No Blender required. Fully automatable server-side.

### Pipeline

```
1. Google Map Tiles API → tileset.json       (root manifest — lists tile tree)
2. Walk tile tree → find tiles for bbox      (hierarchical .b3dm tile IDs)
3. Download .b3dm tiles                       (Batched 3D Model format)
4. py3dtiles → extract .glb from .b3dm       (glTF mesh inside each tile)
5. trimesh → ECEF-to-local transform + merge (align to gravity / flat ground)
6. trimesh → export STL or OBJ               (ready for app's existing pipeline)
```

### Code sketch (from research notes)

```python
from pathlib import Path
from py3dtiles.tileset.content import read_binary_tile_content

# Step 4 — extract glTF from b3dm
b3dm_path = Path("philly_tile.b3dm")
tile_content = read_binary_tile_content(b3dm_path)
gltf = tile_content.body.gltf
with open("philly_mesh.glb", "wb") as f:
    f.write(gltf.to_array())

# Step 5+6 — transform + export
import trimesh
mesh = trimesh.load("philly_mesh.glb", force="mesh")
mesh.export("philly_final.stl")
```

### Critical: The ECEF Coordinate Problem

Google 3D Tiles use **ECEF (Earth-Centered, Earth-Fixed)** coordinates.
Without a coordinate transform, extracted geometry will:
- Appear tilted at ~40° (Philadelphia's latitude from Earth's equatorial plane)
- Have a curved "ground" (follows Earth's radius)

**Fix required:** rotate mesh from ECEF to Local ENU (East-North-Up) frame.
ENU origin = center of requested bbox.
Rotation matrix derived from lat/lon of the origin point.

```python
import numpy as np

def ecef_to_enu_matrix(lat_deg, lon_deg):
    """Rotation matrix from ECEF to local ENU at (lat, lon)."""
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    R = np.array([
        [-np.sin(lon),               np.cos(lon),              0           ],
        [-np.sin(lat)*np.cos(lon),  -np.sin(lat)*np.sin(lon),  np.cos(lat) ],
        [ np.cos(lat)*np.cos(lon),   np.cos(lat)*np.sin(lon),  np.sin(lat) ],
    ])
    return R

# Apply to trimesh
center_lat, center_lon = 39.952, -75.164  # Philadelphia
R = ecef_to_enu_matrix(center_lat, center_lon)
mesh.vertices = (R @ (mesh.vertices - ecef_origin).T).T
```

ECEF origin for the tile must be extracted from the tile's `transform` matrix
(the 4×4 affine in the tileset JSON — column 4 rows 1–3 are the ECEF translation).

### Install

```bash
pip install py3dtiles trimesh
```

### Open questions to research

- [ ] Does the Google Map Tiles API (v1) serve 3D Tiles at a usable resolution for 5km city areas?
- [ ] What is the rate limit / pricing for 3D Tiles tile downloads at city scale?
- [ ] Does py3dtiles 7.x / 8.x still support `read_binary_tile_content` + `.body.gltf`? API may have changed.
- [ ] How many `.b3dm` tiles cover a 2500m-radius Philadelphia area at LOD2?
- [ ] Are multiple tiles automatically merged in the trimesh step, or does each tile need individual ECEF correction before merge?
- [ ] Does the extracted geometry include texture? (For STL we don't need it; for display we might want it.)
- [ ] Is the Google 3D Tiles API available without a billing account (free tier)?

---

## Approach 2 — Blender/Blosm (Desktop-only, semi-automated)

> Already in `notebooks/Cities.ipynb` (cell-19). Requires Blender installed + Blosm addon.

```python
import bpy
bpy.ops.blosm.import_data(
    type='google_3d_tiles',
    minLat=39.9515, maxLat=39.9535,
    minLon=-75.1645, maxLon=-75.1625
)
bpy.ops.export_scene.obj(filepath="./Philly_City_Hall.obj")
```

**Advantages over pure Python:**
- Blosm handles the ECEF rotation automatically
- Handles multi-tile stitching automatically
- GUI for manual area selection

**Why it can't be in the app (yet):**
- Requires Blender to be installed on the server machine
- Requires Blosm addon installed in Blender
- `bpy` is only available inside Blender's bundled Python — not installable via pip
- Cannot be called from a FastAPI endpoint without a Blender subprocess wrapper

**Possible future integration:** run Blender headless via subprocess:
```bash
blender --background --python extract_tiles.py -- --lat 39.952 --lon -75.164 --radius 500
```
Output: OBJ/GLB file → app picks it up from a temp dir.
This works but adds a hard Blender dependency to the server.

---

## Comparison

| | py3dtiles approach | Blender/Blosm |
|-|--------------------|---------------|
| Server-side automatable | ✅ Yes | ⚠️ Subprocess only |
| ECEF transform | ❌ Manual (10–20 lines) | ✅ Automatic |
| No extra software | ✅ pip install only | ❌ Needs Blender + addon |
| Multi-tile stitching | ❌ Must implement | ✅ Automatic |
| Result quality | Same (same source data) | Same |
| Recommended for app | ✅ If ECEF/stitching solved | ❌ Advanced/optional |

---

## Recommended research path

1. **Prototype the py3dtiles pipeline in a notebook** (`notebooks/py3dtiles_test.ipynb`)
   - Get a Google Map Tiles API key
   - Fetch `tileset.json` for Philadelphia center
   - Download 1–3 `.b3dm` tiles
   - Extract `.glb`, apply ECEF transform, verify in napari/trimesh viewer
   - Confirm the resulting mesh is correctly oriented and to scale

2. **If step 1 works:** design a `core/tiles3d.py` server module
   - `fetch_tile_tree(lat, lon, radius_m)` → list of tile URLs
   - `download_tile(url)` → `.b3dm` bytes (cached to disk)
   - `extract_mesh(b3dm_bytes, ecef_origin)` → trimesh object in ENU coords
   - `merge_tiles(meshes)` → single trimesh, stitched at seams

3. **If step 1 fails (API changes / pricing):** fall back to Blender subprocess

---

## Related files

- `notebooks/Cities.ipynb` — current city pipeline (OSM buildings + terrain, no Google Tiles)
- `notebooks/Buildings.ipynb` — investigate this for related work
- `notebooks/granada.ipynb` — investigate this for related work
- `geo2stl/sat2stl.py` — Earth Engine fetch; similar pattern for tile traversal
- `city2stl/dem2stl.py` — DEM-to-model pipeline that city meshes plug into

---

## Deferred — Pending Research

> Items requiring hardware/workflow decisions before implementation.

---

### P2. Print-bed fit optimizer
Input: printer bed dimensions (X × Y mm) + desired number of puzzle pieces.
Output: recommended `dim`, scale, and whether to split.
- "At 1:50 000 this is 240×180 mm — fits your 256mm bed with 8mm to spare"
- "At 1:25 000 this is 480×360 mm — needs a 2×2 puzzle"
Ties directly into the existing puzzle export feature.

**Decisions needed before implementing:**
- What printer(s) / bed size? Ender 220mm, Bambu 256mm, something larger?
- Do you print at a fixed real-world scale (e.g. 1:50 000) or fit to bed?

---

### P6. Elevation band export for multi-material
Split the terrain into N elevation-range meshes, each exported as a separate body in the 3MF.
- User defines band breakpoints (e.g. <0m water, 0–500m lowland, 500–2000m highland, >2000m alpine)
- Each band gets a separate mesh + material in the 3MF container
- Pairs with colour swatch UI in the Extrude tab

**Decisions needed before implementing:**
- Single or multi-material printer? (Bambu AMS, Prusa MMU)
- Is ocean/bathymetry printed with a flat sea surface, or as pure underwater terrain?

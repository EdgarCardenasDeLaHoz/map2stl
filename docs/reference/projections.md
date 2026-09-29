# Map Projections

_Last updated: 2026-09-28_

All projections are implemented in `geo2stl/projections.py`.
The active entry point is `geo2stl/projections.py::project_coordinates`; the app calls it through
`geo2stl/projections.py::project_grid`, `geo2stl/projections.py::project_water_arrays` and
`geo2stl/projections.py::project_rgb_image`.

- Why the raster conventions are what they are → [projections-raster.md](../decisions/projections-raster.md).
  - Its "row 0 = south everywhere" entry is superseded: rasters are **row 0 = north by default**
    → [architecture.md](../decisions/architecture.md), "Raster row 0 is north by default".
- Library map → [packages.md](packages.md).

---

## Why projections matter for 3D prints

A raw DEM tile stores one elevation value per degree-cell. One degree of longitude is ~111 km at the equator but only ~55 km at 60° N. Without correction, a terrain model of Scandinavia would be stretched roughly twice as wide as it should be. Projections fix this by remapping the pixel grid so that horizontal and vertical distances are geographically correct.

---

## Projection types

### `none` — Plate Carrée (Equirectangular)

No transformation. Each pixel represents equal degrees of latitude **and** longitude.

**Formula:** `x = lon`, `y = lat` (identity)

**Output shape:** Same as input. No NaNs.

| | |
|---|---|
| **Pros** | Fastest — zero computation. No NaN gaps. Output shape is predictable. |
| **Cons** | Severe east-west stretch at high latitudes. A region at 60° N will be ~2× too wide in the model. |
| **Best for** | Equatorial regions (±20°), quick previews, debugging. |

---

### `cosine` — Cosine Latitude Correction

Squishes each row horizontally by `cos(lat)`. A row at 60° N is scaled to 50% of its original width because `cos(60°) = 0.5`.

**Formula:** `x_new = (x - cx) * cos(lat) + cx`

This is the original `proj_map_geo_to_2D` behavior from the Oceans notebook.

**Two sub-modes via `maintain_dimensions`:**

- `maintain_dimensions=False` (the pipeline default since F-PROJ-DIMS, 2026-07-19): resizes the whole row-scaled image to the true `cos(lat)`-corrected aspect ratio via `cv2.resize` — no NaN gaps, output width is narrower than input, deterministic from `(bbox, input_shape)` so independently-projected layers for the same bbox/dim stay aligned. (Prior to F-PROJ-DIMS this was a broken per-row integer-index scatter that could misalign layers — replaced, not just re-enabled.)
- `maintain_dimensions=True`: Gather approach — each output row is resampled via `np.interp` to always fill the full output width. No NaNs, same shape as input, but the image is stretched back out to fill the frame.

| | |
|---|---|
| **Pros** | Simple and fast. Good approximation for mid-latitudes. |
| **Cons** | Not conformal or equal-area, just a local approximation. |
| **Best for** | General purpose terrain at 20°–60° latitude. |

---

### `mercator` — Web Mercator

Conformal cylindrical projection. Latitudes are stretched vertically by `1/cos(lat)` to preserve local angles. This is the same projection used by Google Maps and OpenStreetMap.

**Formula:** `y_merc = log(tan(π/4 + lat/2))`, output sampled via bicubic interpolation.

Clamped to ±85° (poles are infinite in Mercator).

**`maintain_dimensions=False`:** Output width is computed from the true Mercator aspect ratio. A region at high latitude gets a taller, narrower output.

| | |
|---|---|
| **Pros** | Shapes are locally correct (conformal). Coastlines and borders look right. Standard for comparison with online maps. Smooth interpolation — no NaN gaps. |
| **Cons** | Severe vertical exaggeration above 60° N/S (Greenland appears as large as Africa). Not equal-area. |
| **Best for** | Coastal regions, islands, any shape where angular accuracy matters. Avoid above 70° latitude. |

---

### `equidistant` — Equidistant Cylindrical

Preserves distances along meridians (north-south). Horizontal scale is corrected by `cos(center_lat)` using the center latitude as the standard parallel.

**`maintain_dimensions=True`:** Delegates to `cosine` with `maintain_dimensions=True` — identical result.

**`maintain_dimensions=False`:** Resizes the whole image to the true aspect ratio using `cv2.resize`. No per-row scatter, no NaN gaps.

| | |
|---|---|
| **Pros** | No NaN gaps even with `maintain_dimensions=False`. Simple and predictable output shape. |
| **Cons** | Only correct at the center latitude. Identical to cosine when `maintain_dimensions=True`. |
| **Best for** | When you want a clean shape change without NaN handling complexity. |

---

### `lambert` — Lambert Cylindrical Equal-Area

Compresses latitudes vertically by `sin(lat)` so that every unit of area in the model corresponds to the same geographic area.

**Formula:** `y_lambert = sin(lat)`, output sampled via bicubic interpolation.

**`maintain_dimensions=False`:** Output width computed from the Lambert equal-area aspect ratio.

| | |
|---|---|
| **Pros** | Area is preserved — a mountain range that covers 10% of a country will cover 10% of the model. Good for thematic maps. Smooth interpolation. |
| **Cons** | Shapes are distorted, especially at high latitudes (features appear squashed north-south). |
| **Best for** | Comparing sizes of geographic features. Continental-scale models where area matters more than shape. |

---

### `sinusoidal` — Sinusoidal (Sanson-Flamsteed)

Pseudocylindrical equal-area. Each row is scaled by `cos(lat)` (like cosine), but longitudes are also shifted so the left and right edges curve inward like a sine wave. The central meridian is straight.

**Formula (per row):** `lon_source = x_out * (lon_range/2) / cos(lat) + center_lon`

Rows near the poles reach outside the valid longitude range → NaN margins on the sides.

**`maintain_dimensions=False`:** Output width computed from the true projected width at the bbox's equator-ward edge (widest row), so no valid data is clipped by undersizing; `clip_nans=True` then trims the empty wing corners as usual. Since `clip_nans` on this shape is geometric (depends only on bbox, not on the source data), the post-clip result is still consistent across independently-projected layers for the same bbox — verified by cross-layer alignment tests, though the exact post-clip shape isn't modeled by `expected_aspect_ratio()`'s formula (only the cross-layer comparison is checked for sinusoidal, not an absolute target).

| | |
|---|---|
| **Pros** | Equal-area. Good visual balance for single-continent maps. Central meridian is undistorted. |
| **Cons** | Curved edges produce NaN margins that must be stripped. Distortion increases away from the central meridian. |
| **Best for** | Africa, South America, single large continents centered on their own meridian. |

---

## NaN handling

Projections whose valid region is not the full rectangle (sinusoidal's curved edges; lat-warping projections at the border) produce NaN values where no source pixel lands. Cosine with `maintain_dimensions=False` no longer does: it resizes the whole image (see above). Two stripping strategies exist:

| Strategy | Code | Meaning |
|---|---|---|
| `~np.all(nan, axis=0)` | Keep column if **any** pixel has data | Keeps partial columns — may leave edge NaNs in the mesh |
| `~np.any(nan, axis=0)` | Keep column only if **all** pixels have data | Strips any column with even one NaN — matches original notebook behavior |

The notebook uses `~np.any()`. The `clip_nans=True` parameter uses `~np.any()` consistently.

---

## Parameter reference

```python
project_coordinates(
    mat,                          # 2D numpy array (elevation)
    bbox,                         # (north, south, east, west) in degrees
    projection='cosine',          # see types above
    maintain_dimensions=False,    # False (default since F-PROJ-DIMS) = true aspect ratio, True = same shape as input
    fill_value=np.nan,            # value for pixels outside valid projection area
    clip_nans=False,              # strip columns with any NaN after projection
)
```

`maintain_dimensions=False` is the pipeline default (F-PROJ-DIMS, 2026-07-19) — output reflects the projection's true geographic aspect ratio, so switching projections visibly changes the canvas/mesh shape. Every projection function computes its output shape deterministically from `(input_shape, bbox)`, so independently-projected layers (DEM, water, ESA, city, hydrology) for the same bbox/dim/projection still land on identical shapes — see `geo2stl/projections.py::verify_layer_alignment` for a runtime/test guard, and `geo2stl/projections.py::expected_aspect_ratio` for the shared formula. Set `maintain_dimensions=True` (client: the "Keep fixed canvas shape across projections" checkbox next to the projection dropdown) to opt into the old fixed-shape behavior — same output shape as input, no NaN gaps, predictable mesh size, but the rendered aspect ratio no longer changes with projection.

**Important:** `clip_nans=True` is automatically skipped whenever `maintain_dimensions=True` — trimming NaN border rows/cols would otherwise shrink the output below the input shape (a lat-warping projection can introduce genuine edge NaNs even when `out_m == m`), silently violating the "same shape as input" contract. This was a real bug found via Playwright verification (mercator + `clip_nans=True` + `maintain_dimensions=True` returned a shape 1px smaller than the input) — fixed in `project_coordinates()`.

`clip_nans=True` is equivalent to the notebook line:
```python
im = im[:, ~np.any(np.isnan(im), axis=0)]
```

---

## Technical facts & gotchas

These implementation details are essential for correct layer alignment and projection behavior.

### Data source coordinate systems

| Source | Native CRS | Notes |
|--------|------------|-------|
| ESRI World Imagery (WMTS tiles) | **Web Mercator (EPSG:3857)** | Pixel rows are NOT uniformly spaced in latitude. At 60° N, Mercator stretches vertically by ~2×. Must convert to Plate Carrée before applying map projections. |
| Earth Engine (ESA WorldCover, JRC Water) | Geographic (EPSG:4326 / Plate Carrée) | Pixels are uniform in lat/lon — no Mercator conversion needed. |
| OpenTopography DEM tiles | Geographic (EPSG:4326) | Standard geographic grid. |
| OSM / Overpass API | Geographic (EPSG:4326) | Vector data in lat/lon. |

### Satellite Mercator→Plate Carrée conversion

`geo2stl/sat2stl.py::_mercator_to_plate_carree` resamples ESRI satellite imagery from Web Mercator to Plate Carrée by computing per-row latitude→Mercator-y mapping and applying bilinear interpolation along the y-axis. Tiles are fetched and stitched by `geo2stl/imagery.py::fetch_rgb`; the conversion is called in `geo2stl/sat2stl.py::fetch_satellite_tiles` after the bbox crop and before the final resize. Without this step, satellite tiles at high latitudes (e.g., Norway ~60° N) appear shifted south.

### ESA land cover — categorical data

ESA WorldCover uses integer class IDs (10=Tree cover, 20=Shrubland, 30=Grassland, 40=Cropland, 50=Built-up, 60=Bare, 70=Snow/ice, 80=Water, 90=Wetland, 95=Mangroves, 100=Moss/lichen). These are **categorical**, not continuous — interpolation between classes is meaningless (e.g., averaging "Tree cover" and "Water" produces "Cropland").

- Always use **nearest-neighbour interpolation** (`order=0`) when resampling ESA data.
- In `project_grid()`, categorical arrays use `fill_value=np.nan` (same as continuous) so that `clip_nans` works identically for all layer types. After clipping, NaN pixels are replaced with `0` via `np.nan_to_num()`.
- Previously, categorical data used `fill_value=0` with `clip_nans=False`, which caused dimension mismatches between ESA and DEM layers.

### Projection pipeline uniformity

All raster endpoints pass data through `geo2stl/projections.py`. Key rules:

- `maintain_dimensions` defaults to `False` (F-PROJ-DIMS) and is threaded through from the client's `#paramMaintainDimensions` checkbox (`window.getProjectionParams()`) uniformly to every layer fetch — same value for DEM/water/ESA/satellite/hydrology/city so their outputs land on matching aspect ratios.
- `clip_nans` must use `fill_value=np.nan` for ALL data types (categorical and continuous) so that NaN-border clipping produces consistent dimensions across layers. Automatically skipped when `maintain_dimensions=True` (see above).
- `project_water_arrays()` handles water mask + ESA as a paired operation, clipping both arrays identically from the water mask's NaN pattern.
- `project_rgb_image()` projects RGB channel-by-channel with bilinear interpolation.
- Cache keys generally exclude `projection`/`clip_nans`/`maintain_dimensions` — raw (unprojected) data is cached once per bbox, and projection is (re-)applied fresh on every request, **including cache hits**. The ESA land-cover and hydrology endpoints previously baked the projected result into the cache and never re-projected on a cache hit (a real bug, fixed 2026-07-19) — if you add a new raster endpoint, follow the DEM/water-mask pattern (cache raw, project after every read) not that one.

### Cosine projection geometry

Cosine projection squishes each row horizontally by `cos(lat)`. At 60° N, `cos(60°) = 0.5`, so the row is half its original width. With `maintain_dimensions=True`, the output is resampled back to the original width via `np.interp`, preserving shape but stretching the content. With `maintain_dimensions=False` (default), the whole image is resized via `cv2.resize` to `avg_cos * input_width` — narrower, no stretching.

### Layer compositing

`app/client/static/js/modules/layers/stacked-layers.js` uses a shared letterboxed rectangle based on bbox aspect ratio. All layers are stretched to the same target rect via `drawImage` 9-arg form, regardless of native pixel dimensions. This means layers can have different resolutions and still align correctly as long as they cover the same geographic bbox.

### City data constraints

OSM building queries via Overpass API require bbox diagonal < 15 km. The server validates this with `app/server/core/validation.py::validate_bbox_diagonal`. For larger regions, city data should not be fetched.

---

## Caching model and projection performance

### Current model: cache plate carrée, project on every request

Raster endpoints cache the **raw (unprojected)** array once per bbox and apply projection after
every read, cache hits included:

```
request (bbox + projection) → cache miss → fetch (plate carrée) → write cache → project → return
                                cache hit  → read raw array                    → project → return
```

- Switching projection on the same bbox is a cache hit plus a projection.
- `projection` and `clip_nans` / `clip_valid_region` are **not** in the keys. The DEM key does
  carry `maintain_dimensions` (`md`); the other layers re-project on a hit, so a changed
  setting takes effect without a new key.
- Endpoints:
  - `/api/terrain/dem` — key from `app/server/core/dem_cache.py::dem_cache_key` (the one
    definition, shared with export); projected with `project_grid`.
  - `/api/terrain/water-mask` — `water` namespace; `project_water_arrays` after the read.
  - `/api/terrain/esa-land-cover` — `esa_lc` namespace; categorical `project_grid`.
  - `/api/terrain/hydrology` — `hydrology` namespace; `project_grid` after the read.
  - `/api/terrain/satellite` — no disk cache; `project_rgb_image` on every request.
  - `/api/height/fetch` — each provider's raw raster cached in `height_<provider>`
    (`app/server/core/height/service.py::fetch_height_payload`); merged, then projected.
- ESA land cover and hydrology once baked the projected result into the cache and never
  re-projected on a hit (fixed 2026-07-19). A new raster endpoint follows the DEM / water-mask
  pattern.
- Why → [terrain-dem.md](../decisions/terrain-dem.md), "Rasterize at the pre-projection size,
  project once" and "The DEM cache key has exactly one definition".

### Performance characteristics of projection

`project_coordinates` in `geo2stl/projections.py` is pure NumPy with `scipy.ndimage.map_coordinates`
for bilinear resampling. For a typical 256×256 raster:

| Projection | Dominant cost | Approx. time |
|---|---|---|
| `none` | Zero | <0.1 ms |
| `cosine` (maintain_dimensions=True) | `np.interp` per row | ~1–3 ms |
| `mercator` / `sinusoidal` / `lambert` | `map_coordinates` | ~5–15 ms |
| `equidistant` | `cv2.resize` | ~1–2 ms |

For 512×512 rasters costs scale approximately 4× (area).

Projection is fast enough to re-run on every request. The cost caching saves is the **data
fetch** (network or disk I/O), not the projection — which is why the cache stores raw
plate-carrée data and projects after every read.

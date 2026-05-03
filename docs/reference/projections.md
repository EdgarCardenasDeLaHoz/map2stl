# Map Projections

_Last updated: 2026-04-19_

All projections are implemented in `geo2stl/projections.py`.
The active entry point is `project_coordinates(mat, bbox, projection=..., ...)`.

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

- `maintain_dimensions=False` (default for this projection): Scatter approach — each source pixel is placed at its projected x position. Leaves NaN gaps between pixels at high latitudes. Output width is narrower than input. Use `clip_nans=True` to strip empty columns.
- `maintain_dimensions=True`: Gather approach — each output row is resampled via `np.interp` to always fill the full output width. No NaNs, same shape as input, but the image is stretched back out to fill the frame.

| | |
|---|---|
| **Pros** | Simple and fast. Good approximation for mid-latitudes. Matches the original notebook behavior exactly when `maintain_dimensions=False, clip_nans=True`. |
| **Cons** | Discrete pixel scatter (not smooth interpolation) — can leave NaN gaps. Not conformal or equal-area, just a local approximation. |
| **Best for** | General purpose terrain at 20°–60° latitude. Default choice. |

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

| | |
|---|---|
| **Pros** | Equal-area. Good visual balance for single-continent maps. Central meridian is undistorted. |
| **Cons** | Curved edges produce NaN margins that must be stripped. Distortion increases away from the central meridian. Currently always returns same `(m, n)` shape regardless of `maintain_dimensions`. |
| **Best for** | Africa, South America, single large continents centered on their own meridian. |

---

## NaN handling

Projections that remap pixels by scatter (cosine with `maintain_dimensions=False`, sinusoidal) produce NaN values where no source pixel lands. Two stripping strategies exist:

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
    maintain_dimensions=True,     # True = same shape as input, False = true aspect ratio
    fill_value=np.nan,            # value for pixels outside valid projection area
    clip_nans=False,              # strip columns with any NaN after projection
)
```

`maintain_dimensions=True` is the safe default — same output shape, no NaN gaps, predictable mesh size. Use `maintain_dimensions=False` when you want the true geographic aspect ratio in the output model.

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

`core/sat.py → _mercator_to_plate_carree(img, north, south)` resamples ESRI satellite imagery from Web Mercator to Plate Carrée by computing per-row latitude→Mercator-y mapping and applying bilinear interpolation along the y-axis. This is called in `fetch_satellite_tiles()` after the bbox crop and before the final resize. Without this step, satellite tiles at high latitudes (e.g., Norway ~60° N) appear shifted south.

### ESA land cover — categorical data

ESA WorldCover uses integer class IDs (10=Tree cover, 20=Shrubland, 30=Grassland, 40=Cropland, 50=Built-up, 60=Bare, 70=Snow/ice, 80=Water, 90=Wetland, 95=Mangroves, 100=Moss/lichen). These are **categorical**, not continuous — interpolation between classes is meaningless (e.g., averaging "Tree cover" and "Water" produces "Cropland").

- Always use **nearest-neighbour interpolation** (`order=0`) when resampling ESA data.
- In `project_grid()`, categorical arrays use `fill_value=np.nan` (same as continuous) so that `clip_nans` works identically for all layer types. After clipping, NaN pixels are replaced with `0` via `np.nan_to_num()`.
- Previously, categorical data used `fill_value=0` with `clip_nans=False`, which caused dimension mismatches between ESA and DEM layers.

### Projection pipeline uniformity

All raster endpoints pass data through `core/projection.py`. Key rules:

- `maintain_dimensions=True` is used uniformly — output shape matches input shape.
- `clip_nans` must use `fill_value=np.nan` for ALL data types (categorical and continuous) so that NaN-border clipping produces consistent dimensions across layers.
- `project_water_arrays()` handles water mask + ESA as a paired operation, clipping both arrays identically from the water mask's NaN pattern.
- `project_rgb_image()` projects RGB channel-by-channel with bilinear interpolation.
- Cache keys include `projection` and `dataset` to avoid serving stale projected data.

### Cosine projection geometry

Cosine projection squishes each row horizontally by `cos(lat)`. At 60° N, `cos(60°) = 0.5`, so the row is half its original width. With `maintain_dimensions=True`, the output is resampled back to the original width via `np.interp`, preserving shape but stretching the content.

### Layer compositing

`stacked-layers.js` uses a shared letterboxed rectangle based on bbox aspect ratio. All layers are stretched to the same target rect via `drawImage` 9-arg form, regardless of native pixel dimensions. This means layers can have different resolutions and still align correctly as long as they cover the same geographic bbox.

### City data constraints

OSM building queries via Overpass API require bbox diagonal < 15 km. The server validates this with `validate_bbox_diagonal()`. For larger regions, city data should not be fetched.

---

## Caching model and projection performance

### Current model: cache-keyed-by-projection

Every raster endpoint (terrain DEM, water mask, ESA land cover, height fetch) computes the
cache key **after** projection parameters are known. Both `projection` and `clip_nans` are
included in `make_cache_key(...)`. The flow is:

```
request (bbox + projection) → cache miss → fetch (Plate Carrée) → project → write cache → return
                                cache hit  →  skip fetch + project entirely  →  return
```

**Consequence:** the same bbox requested with different projections produces separate cache
entries. A cosine cache hit still serves the pre-projected result with zero recomputation.

**Endpoints using this model:**
- `GET /api/terrain/dem` — `proj`, `cn` in key; `_project_grid` applied after fetch
- `GET /api/terrain/water-mask` — `proj`, `cn` in key; `_project_water_arrays` applied after fetch
- `GET /api/terrain/esa-land-cover` — `proj`, `cn` in key; `_project_grid` (categorical) applied after fetch
- `GET /api/terrain/satellite` — no disk cache; `_project_rgb_image` applied on every request
- `POST /api/height/fetch` — `projection`/`clip_nans` **not yet in key** (see below)

### Known gap: `/api/height/fetch` projection not in cache key

The height providers cache their raw Plate Carrée rasters internally (inside each provider via
`write_array_cache`). The `/api/height/fetch` endpoint then applies `project_grid` *after*
reading from the provider cache. However, the endpoint does **not** maintain its own disk cache,
so projection is re-run on every request even when the underlying raster is cache-hot.

This is correct for correctness (no stale projected data) but wasteful for repeated requests
with the same `(bbox, providers, projection)` triple. If `/api/height/fetch` gains an endpoint-
level disk cache, `projection` and `clip_nans` must be included in the key — matching the
terrain endpoint pattern exactly.

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

Projection is fast enough to re-run on every cache miss without concern. The main cost
saving from caching is avoiding the **data fetch** (network or disk I/O), not the projection
itself. This is why the terrain endpoints cache the post-projection result by default —
it avoids both costs — rather than storing Plate Carrée and re-projecting per request.

### Alternative model: cache Plate Carrée, project on request

Storing raw Plate Carrée in the cache and projecting on every request is a valid alternative
with different trade-offs:

| | Current (cache post-projection) | Alternative (cache Plate Carrée) |
|---|---|---|
| Cache entries per bbox | One per `(bbox, projection)` combination | One per `(bbox, params)` — projection-agnostic |
| Cache size | Larger (N projections × M bboxes) | Smaller |
| Repeated same projection | O(1) — cache hit, no projection | O(projection) — re-project every time |
| Switching projection on same bbox | Cache miss, full fetch + project | Cache hit, project only |
| Alignment risk | None — each layer independently projected | Low — all layers must use same projection call |

If projection switching per-request becomes a common use case (e.g., a UI dropdown that
switches projection without re-fetching data), the Plate Carrée cache model would be
preferable. The current model is optimal for the existing use case where projection is
fixed per session/request.

To move to the Plate Carrée model:
1. Remove `proj` and `cn` from all `make_cache_key(...)` calls.
2. Read the raw array from cache.
3. Apply `project_grid` at the endpoint before returning.
4. Accept that repeated requests with the same projection re-run the projection step.

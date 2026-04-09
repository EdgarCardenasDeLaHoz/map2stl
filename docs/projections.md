# Map Projections

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

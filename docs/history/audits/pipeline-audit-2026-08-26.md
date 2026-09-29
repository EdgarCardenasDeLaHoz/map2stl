# Pipeline audit — saved location to STL

> **Historical (archived 2026-09-28).** Re-checked against the code that day: 12 findings fixed,
> 2 obsolete; every still-open item is in [issues.md](../../issues.md) as PA-<id>.
> Paths and line numbers below are as of 2026-08-26.

_2026-08-26. Scope: the main pipeline only — `map2stl/app/` (server, session, client),
`map2stl/geo2stl/`, and the `numpy2stl` mesh/writer entry points it calls. Registration,
skyline, and the ML height work are out of scope._

Read the pipeline reference (then Code/docs/app.md) first for what the pipeline is. This page is only what is wrong with it.

## Verification status

Every finding is tagged:

- **CONFIRMED** — reproduced, or read directly in the file at the cited line.
- **REPORTED** — surfaced during the audit but not independently reproduced. Treat as a lead.

Four claims raised during this audit were **refuted** on checking and are recorded at the bottom
so nobody re-files them.

## Health summary

| | |
|---|---|
| Tests | 119 pipeline tests pass in 37 s. E2E harness is genuinely good. |
| Saved data | 125 regions; 79 settings rows, of which **65 are `{}`** and 11 hold only `rotation`. Just 3 regions have a real settings blob. |
| Biggest structural risk | The DEM handoff between two endpoints via a hand-rebuilt cache key. |
| Biggest data risk | Re-saving a region deletes its settings. |

The low blob-population number matters: the settings feature is **barely exercised**, which is
consistent with several of the round-trip bugs below having gone unnoticed.

---

## CRITICAL

### 1. Re-saving a region destroys its saved settings — CONFIRMED (reproduced)

`map2stl/app/server/routers/regions.py:164` uses `INSERT OR REPLACE INTO regions`. With
`PRAGMA foreign_keys=ON` (`core/db.py:95`) and `ON DELETE CASCADE` on `region_settings`
(`core/db.py:74`), REPLACE **deletes the parent row first**, firing the cascade.

Reproduced on a copy of the live `data.db`: region `Lake George Turned`, settings
`{"rotation": 35.0}` before, row **gone** after a single `INSERT OR REPLACE` of its own unchanged
values.

**Fix:** `INSERT INTO regions (...) VALUES (...) ON CONFLICT(name) DO UPDATE SET ...`.

### 2. Contour engraving is dead code and fails silently — CONFIRMED (reproduced)

`map2stl/app/server/core/export.py:235`:

```python
index_band = index_phase < (band_half * 2) | (index_phase > (1.0 - band_half * 2))
```

`|` binds tighter than `<` and `>`, so Python evaluates `float | ndarray` and raises
`TypeError: ufunc 'bitwise_or' not supported...`. Reproduced standalone. The exception is caught
by the blanket handler at `:245`, which logs at **warning** level and returns the untouched array.

Every export requested with contours has silently shipped without them.

**Fix:** parenthesise both comparands. Then reconsider the `except Exception` at `:245` — it is
what turned a hard crash into three years of quiet wrong output.

### 3. The binary STL writer can leave a corrupt file at the final path — CONFIRMED

`numpy2stl/io/writers.py:44` opens the destination with a bare `open(file_name, "wb")` — no
`with`, no `try`, no temp-then-rename. `_build_binary_stl` writes the **triangle count into the
header first**, then the facets. Any error mid-write leaves a file whose header claims N triangles
over a short payload, at the real output path, having already clobbered a good previous file.

That is the worst failure shape for this format: structurally plausible, so slicers may load it
and produce a wrong print rather than refusing it.

**Fix:** write to `path + ".tmp"` inside a `with`, then `os.replace()`.

### 4. `array_to_mesh` has variable return arity on its error path — CONFIRMED

`numpy2stl/core/generate.py:126-134` returns `(verts, faces_idx)` normally, but its
`except Exception` fallback returns bare `all_triangles`, an `(M,3,3)` array. Callers that unpack
two values get either a `ValueError` or, worse, a silent garbage mesh.

**Fix:** re-raise. A function must never vary its return arity.

### 5. Export enforces no resolution cap — CONFIRMED

`MAX_DIM = 2000` (`app/server/config.py:128`) and `validate_dim()` (`core/validation.py:140`) are
applied by `routers/terrain.py:237` and `core/mesh_import.py:108` — but **grep shows neither is
imported anywhere in the export path**. `height` and `width` travel from the request body into
`np.array(...).reshape()` in `core/export.py:141` unchecked.

**Fix:** call `validate_dim` in `_parse_export_params`, and assert `height * width ==
len(dem_values)` before the reshape.

---

## HIGH

### 6. The DEM handoff is an unenforced contract between two endpoints — CONFIRMED

`routers/terrain.py:248` builds a cache key and writes the DEM array; `core/export_params.py:57-63`
**reconstructs that key by hand** from the export request and reads it back. Nothing ties the two
computations together. Any drift means either `"Missing DEM data"` (`core/export.py:49`) or —
worse — a *hit on a stale array* with no error at all.

This has already broken once. The post-mortem is in the code comment at
`core/export_params.py:37-45`.

Two live drift instances already exist:
- `export_params.py:46` defaults `dim=200`; `terrain.py:220` writes under `dim=600`. Any export
  omitting `dim` misses the cache.
- `maintain_dimensions` is hand-mirrored at `export_params.py:54` with a comment warning it must
  match `terrain.py`.

**Fix:** one exported `dem_cache_key(bbox, dem_settings)` function that both sides call. This is
the single highest-value change in this document — it removes a whole bug class.

### 7. Stale-task cleanup evicts running exports — CONFIRMED

`core/export_tasks.py:63` selects stale tasks on `t.created < cutoff` with **no status check**, and
`_TASK_TTL = 300` s. Any export running longer than five minutes is deleted from the registry
mid-run. The client's next poll 404s; the worker thread keeps burning CPU and orphans its temp file.

**Fix:** `if t.created < cutoff and t.status != "running"`.

### 8. Three export routes block the event loop — CONFIRMED

`routers/export.py:31,38,45` — `/api/export/stl`, `/obj`, `/3mf` are `async def` that call the
blocking `generate_*` pipeline directly. Only `/preview:52` uses `run_in_executor`. One large
export freezes every other request on the server.

These three are also **unused by the client**, which only calls `/api/export/start`.

**Fix:** delete them, or wrap them in `run_in_executor`.

### 9. Region create/update silently resets parameters — CONFIRMED

`routers/regions.py:161,193`: `params = region.parameters or RegionParameters()`. A PUT that omits
`parameters` writes **Pydantic defaults explicitly**, and those disagree with the DB defaults:

| param | DB DEFAULT (`db.py`) | Pydantic (`schemas.py:68-79`) |
|---|---|---|
| `dim` | 600 | **200** |
| `height` | 25.0 | **10.0** |
| `base` | 5.0 | **2.0** |

Because every INSERT supplies a value, the DB defaults can never fire.

**Fix:** align the Pydantic defaults to the DB, and build the SET clause only from supplied fields.

### 10. Settings are stored with no validation and no versioning — CONFIRMED

`routers/regions.py:253-275` takes a raw `Request`, accepts any dict, and stores `json.dumps()`
verbatim. `RegionSettings` is **imported at `:27` and never used** — the validation model is dead
code. No key whitelist, no bounds, no body-size cap.

There is also no `schema_version` and no migration anywhere. Renames that were never migrated are
visible in the live data as ragged key counts (`water.sat_scale` → `water.dim`, `esa` →
`satellite`). The client compensates by guessing shapes at read time
(`ui/presets.js:342,404`). Ironically `presets.js:31,173` *does* implement `PRESET_VERSION` and
`_migratePreset` — but only for localStorage, never for the server payload.

**Fix:** bind `settings: RegionSettings`; add `schema_version` to the blob and a server-side
upgrade function.

### 11. The OSM cache key omits `layers` — CONFIRMED

`core/cache.py:79` keys on `bbox + tol + min_area` only. `routers/cities.py:191` passes `layers` to
the fetch, so it changes the payload. A `["buildings"]` request therefore poisons the entry for a
later `["buildings","roads","waterways"]` one.

The adjacent comment at `cities.py:157` correctly explains that `detail` *is* folded in via
`min_area` — that part is fine. `layers` is the gap.

**Fix:** fold `sorted(layers)` into `osm_cache_key`.

### 12. Two cache trees escape the configured cache root — CONFIRMED

`core/cache.py:45` honours `MAP2STL_CACHE` so the large regeneratable cache can live outside the
OneDrive-synced tree. But `config.py:39` hardcodes `OPENTOPO_CACHE_PATH = _MAP2STL_DIR/"cache"/
"opentopo"` and `config.py:42` uses a third root for `EE_CACHE_DIR`.

So the two **largest** artifact caches ignore the setting, land in the synced folder, and are
swept by nothing — `prune_all_caches` walks a different root.

**Fix:** derive both from `CACHE_ROOT`.

### 13. `ExportRequest` is defined, imported, and never used — CONFIRMED

`schemas.py:295` declares constraints (`ge=0.1`, `ge=0.0`) that never execute; every export route
reads `await request.json()` raw. The model is also out of sync with reality — missing
`sea_level_cap`, `mm_per_pixel`, `composite_layers`, `composite_dim`, all of which
`export_params.py:143-148` consumes.

Same shape for `ExportResponse:319`, `DEMResponse:224`, `RawDEMResponse:238`.

### 14. Silent zero-fill on data-source failure — REPORTED

`geo2stl/dem.py:284,411`, `hydrology.py:164,200,229,750`, `sat2stl.py:617` catch broad exceptions
and return `np.zeros` or `None`. Network failure, missing credentials, and a missing dependency all
render as **flat sea-level terrain** — indistinguishable from a genuinely flat region.

`sat2stl.py:251` is the same pattern for imagery: a failed tile is left black at DEBUG level and
the fetch succeeds if any one tile loads.

**Fix:** typed `DemUnavailable` / `HydroUnavailable`; zero-fill only on explicit opt-in.

### 15. SRTM void marker clamps real bathymetry to zero — REPORTED

`geo2stl/dem.py:393` does `np.maximum(cropped, 0.0)`, which flattens SRTM's `-32768` void marker
**and** every genuinely below-sea-level elevation to 0.

**Fix:** NaN out `<= -32000` first, then clamp.

### 16. Non-atomic cache writes poison caches permanently — REPORTED

`dem.py:475` `write_bytes` straight to the final `.tif`; a truncated file or an HTML error body
returned with a 200 is cached forever, since `:453` only checks `exists()`. Same class at
`hydrology.py:462` (`extractall` into the final dir) and `sat2stl.py:623`.

The project has already been bitten by cache poisoning — `tests/e2e/conftest.py:38-46` gives every
E2E session its own temp cache dir specifically because a stale entry caused a false failure.

**Fix:** temp + `os.replace` everywhere; validate magic bytes and size before accepting.

### 17. `select()` discards saved settings on any error — CONFIRMED

`app/session/terrain_session.py:838`: `except Exception: saved = {}`. A network blip or a 500 makes
`select()` report success while quietly reverting to `_DEFAULT_SETTINGS`. The next export then
renders the wrong mesh, with no indication why.

**Fix:** catch `requests.RequestException` and 404 only; warn naming the region whose settings
were lost.

---

## MEDIUM

- **Base thickness is inert** — **FIXED 2026-09-04.** All seven `array_to_mesh` call sites in
  `core/export.py` now pass `floor_val=0.0`; measured 35.00 mm for a 30 mm relief on a 5 mm base,
  floor at exactly z=0. Original finding, unchanged, below.
  CONFIRMED (measured, 2026-08-26, while building v2).
  `core/export.py` adds `base_height` to every elevation, but `array_to_mesh`
  (`numpy2stl/core/generate.py:26`) defaults `floor_val` to the surface minimum minus one, so
  the bottom cap rises with the surface and the solid is always `model_height + 1` mm tall no
  matter what the "Base thickness" control says. Measured with `model_height=30`,
  `base_height=10`: exported z extent 31 mm, expected 40 mm. **Fix:** pass `floor_val=0` when
  the base is above zero — the parameter exists for exactly this and its docstring recommends
  it. Done in v2 at `map2stl/v2/server/pipeline.py:292`.
- **Sea-level cap is inverted** — **FIXED 2026-09-04**, now `np.maximum`. Verified: with the cap
  on, the flat area of the output equals the below-sea-level area of the input to within a pixel
  (46.0 percent both ways on the test ridge). Original finding: CONFIRMED, `core/export.py:145`
  did `im = np.minimum(im, 0.0)`, keeping only bathymetry and flattening all land. Latent at the
  time because the control exists only in `ModelContainer.vue:68`, so the flag was always false.
- **Vertical exaggeration does nothing** — FOUND AND **FIXED 2026-09-04**, not in the original
  audit. `_prepare_dem_array` multiplied the grid by the exaggeration and then min-max normalised
  onto `model_height`; a positive constant cancels exactly through that normalisation, and
  reordering the sea-level clamp does not change it. The multiply now lands after the
  normalisation, so relief is `model_height * exaggeration` mm: measured 30 / 60 / 90 mm at
  1x / 2x / 3x, where all three previously gave 30 mm. Contour spacing follows the same height.
  **Still live in v2** at `v2/server/pipeline.py:259-285`, under a comment asserting that the
  order matters.
- **`_CREATE_REGIONS` is four columns behind the live DB** — CONFIRMED. `core/db.py:52` declares
  14 columns; the live table has 18 (`continent`, `source`, `city`, `tags` were added
  out-of-band). `source` is populated on 76 rows. A fresh install silently loses all four, and
  they are invisible to the API — absent from `_PARAM_FIELDS`, the SELECT, and `RegionCreate`.
- **Connections leak** — REPORTED. `core/db.py:84` returns a new connection per call and every
  caller uses `with get_db() as conn`. `sqlite3.Connection.__exit__` **commits; it does not
  close.** No `.close()` exists in `db.py` or `routers/`. Fix: `contextlib.closing`.
- **Region routes skip the validators they already own** — CONFIRMED. `validate_bbox`,
  `validate_dim`, `validate_bbox_diagonal` (`core/validation.py:119,140,148`) are applied by
  `terrain.py` and `cities.py` but never by region create/update. Only the `CHECK (north > south)`
  constraint catches anything.
- **`_ensure_db()` runs on every request** — CONFIRMED (`routers/regions.py:49`). New connection,
  two CREATE TABLEs, a WAL pragma, per request. Move to startup.
- **Blocking sqlite on the event loop** — CONFIRMED. All six region routes are `async def` calling
  blocking sqlite3. `run_sync` exists unused at `validation.py:172`.
- **`user`-controlled export filename reaches `Content-Disposition` unsanitised** — REPORTED
  (`core/export.py:92,103,110,...`, name from `export_params.py:90`). A newline permits response
  header injection. Local-only server, so severity is bounded — but it is a one-line fix.
- **Temp files leak on failure and on restart** — REPORTED. `core/export.py:182-186` and four
  sibling sites create the temp file, then can raise before any unlink. There is no cancellation
  path and no boot-time sweep (`server.py:105` has nothing after `yield`).
- **Writers materialise the whole mesh before writing** — REPORTED. Binary ≈2.7× peak, ASCII ≈4×,
  3MF builds one XML node per vertex *and* triangle. Combined with finding 5 (no dim cap), a large
  request is an OOM.
- **Half-pixel misregistration** — REPORTED. `projections.py:581` uses node convention;
  `hydrology.py:757` `from_bounds` uses edge convention.
- **`blend_layers` stretches instead of reprojecting** — REPORTED. `processing.py:91` compares only
  `.shape`, never bbox or projection. `verify_layer_alignment` (`projections.py:184`) exists and is
  never called.
- **Scale-constant drift** — REPORTED. `111000` (`sat2stl.py:515`) vs `111320` (`sat2stl.py:32`,
  `raster.py:11`); the zoom heuristic at `sat2stl.py:86` and `raster.py:8` yield different ground
  resolutions for the same `dim`.
- **Missing cache version tokens** — REPORTED. Only `dem` carries `"v":2`. `water`, `esa_lc`,
  `hydrology`, `composite`, `osm` have none, so a code change never invalidates them.
- **`city2stl` still routes through osmnx** — CONFIRMED. `city2stl/fetch.py:50` uses
  `ox.features_from_bbox`, the path known to time out on this machine, despite the endpoint probe
  at `:243`. A passing `requests.head` probe does not mean osmnx will work.
  `_align_tool/locate.py:276` has the working direct-Overpass pattern. `geo2stl` is already clean.
- **No concurrency limit on exports** — REPORTED. `export_tasks.py:110` caps nothing; N clicks
  spawn N threads each materialising a full mesh.
- **Encoding-free file IO** — REPORTED. `mesh_import.py:414,372`, `writers.py:128` use
  `read_text`/`write_text`/`open` with no `encoding=`. The console here is cp1252, so an accented
  city name raises mid-write.

---

## Structural observations

### `terrain_session.py` is a 3100-line god object off the main path

It is **not** the pipeline's state holder — it is a `requests`-based notebook SDK that speaks HTTP
to the same server, with zero `geo2stl`/`numpy2stl` imports. A single class carries seven unrelated
domains.

Roughly 500 lines (16%) are dead with zero callers repo-wide: `check_alignment:1319` (365 lines,
the largest method in the file), `merge_hydrology_with_dem:2085`, `enrich_buildings_with_heights:2531`.
`export_obj:2184` POSTs to `/api/export` with `"format":"obj_split"` — **no such route is
registered**, so it should 404, which would break `run_all():3090`.

The natural seams are already visible in the private-helper clusters: `HttpClient`,
`ServerProcess`, `RegionStore`, `LayerFetcher`, `Visualizer`, `AlignmentChecker`, `HeightML`.

Also: `_kill_stale_server:507` kills **any** process listening on the port regardless of owner, and
every instance defaults to port 9090 — two sessions in one notebook kill each other.

### Duplicate and dead paths

- `geo2stl/write.py:13 savefile` — dead, and **divergent**: it flips rows (`im[::-1]`) where the
  live export path does not, so the notebooks that call it produce mirrored geometry.
- `geo2stl/raster.py` — reachable only via `core/terrain_raster.py:7`, which is self-labelled
  deprecated and imported by zero modules.
- `core/osm_cache_policy.py` — a pure re-export of `city2stl/cache_policy.py`.
- `dem.py:252 fetch_dem_from_source` — a wrapper the app never calls.
- `dem.py:134,561,594` — notebook-era, no caller under `app/`.
- `routers/composite.py:242` vs `routers/cities.py:233` — duplicate city-raster routes with
  different request models.
- `core/export.py:652-666 _apply_edge_tabs_v` is byte-identical to `_apply_edge_tabs:609`.
- Dead params: `composite_layers`, `composite_dim`, `bbox` (`export_params.py:97-99`) are stored
  and never read; `tab_width_px` (`export.py:515`) is computed and unused, so `connector_size_mm`
  has no effect.

### Settings that do not round-trip

- `satellite.dim` is collected (`presets.js:287`) but never re-applied — satellite resolution
  resets on every region load.
- `hydrology.scale_m` is served as a default (`settings.py:89`) but never collected, so it is
  dropped by the next save.
- `water.dim` defaulting to 600 (`settings.py:45`) layers *over* two legacy rows storing
  `water.sat_scale: 10`, so `presets.js:404`'s `wat.dim ?? wat.sat_scale` picks the default over
  the user's saved value.
- `rotation` is saved on 11 regions and has **no consumer** in any `.py` or `.js`.

### Frontend

Two client stacks load together from one template — `main.js` (8 plain-JS module groups) and
`vue-main.js` (38 Vue files), ~16.8k lines total. Plotly and **three.js r128** are pulled from
public CDNs at runtime, so the app does not work offline and pins a 2021-era three.js.

---

## Refuted during this audit

Recorded so they are not re-filed:

1. **`geo2stl/tiles.py:132` "missing return"** — false. `stitch_tiles_no_rasterio` does
   `return final_image` on the last line; the file simply has no trailing newline, which made the
   line look merged.
2. **"The OpenTopography API key is committed"** — false. `map2stl/.gitignore:14` ignores
   `config.json` and `git ls-files --error-unmatch config.json` reports it untracked. (`map2stl/`
   is a git repo even though `Code/` is not.) The real residue is that it sits in a
   OneDrive-synced folder.
3. **"`dem.py:469` leaks the key via `resp.url`"** — false. The `RuntimeError` carries
   `resp.text[:500]`, the response body. `resp.url` appears nowhere in `geo2stl/` or `app/server/`.
4. **"`cities.py` cache key omits `detail`"** — false, and documented at `cities.py:157`: `detail`
   is folded in via `min_area`. Only `layers` is genuinely missing (finding 11).

Also checked and clean: **no SQL string interpolation** — every query in `regions.py` uses bound
parameters. **No path traversal** in the cache or terrain routers — no endpoint accepts a
user-supplied path.

---

## Suggested order of work

1. Finding 1 (settings destroyed on save) — data loss, one-line fix.
2. Finding 6 (shared `dem_cache_key` function) — removes a bug class, not just a bug.
3. Findings 2, 4, 3 — silently wrong output, all small.
4. Findings 5 + 7 + 8 — resource and liveness bugs in the export path.
5. Finding 10 (validate + version the settings blob) before more settings are added, while only 3
   regions hold real blobs and migration is nearly free.
6. Deletions: `geo2stl/write.py`, `geo2stl/raster.py`, `core/terrain_raster.py`,
   `core/osm_cache_policy.py`, the three sync export routes, the ~500 dead lines in
   `terrain_session.py`.

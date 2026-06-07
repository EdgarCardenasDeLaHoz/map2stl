# F-SKY8 — Satellite-derived building footprints as a second polygon source

Proposal entry: `docs/proposals.md` F-SKY8

## Test case

Cartagena seed_5, heading 298° (PDF page 31 under b0, similar view
under b3). The F-SKY7 mask panel shows the SegFormer building mask
covers a continuous central strip of obvious tall towers — but the
matcher has nothing to assign there because **OSM has no polygons for
those waterfront-row buildings**. The orange "considered but lost"
dots from F-SKY6 in that x-band are also sparse — confirming the
issue is upstream OSM coverage, not matcher rejection.

Satellite-derived building datasets DO cover those buildings:

- **Microsoft Global ML Building Footprints**
  (`microsoft/GlobalMLBuildingFootprints`) — ~1B footprints worldwide
  derived from Bing aerial imagery, free, GeoJSON. Colombia / Cartagena
  coverage confirmed.
- **Google Open Buildings** — Africa, South America (incl. Colombia),
  parts of South Asia, free, S2-cell-tiled CSV with WKT polygons.

Either source would 2–5× the matchable polygon set in the gap.

## Approach

### Stage A — fetch + cache

Add `city2stl/skyline/satellite_footprints.py`:

```python
def fetch_microsoft_buildings_for_bbox(
    bbox: tuple[float, float, float, float],   # (S, W, N, E)
    cache_dir: Path = Path("runs/satellite_footprints_cache"),
) -> list[dict]:
    """Return list of {polygon_wkt, source} dicts inside the bbox.

    Caches per-quadkey to disk so repeat fetches are free. First fetch
    of a region tile is ~30s and tens of MB; subsequent runs are
    free.
    """
```

Microsoft hosts the data tiled by quadkey at zoom 9 — each tile is a
geojsonl file. We compute which quadkey tile(s) cover the bbox,
download the missing ones, parse them, and filter to the bbox.

Quadkey-9 tile for Cartagena covers ~150 × 150 km, so one tile = one
download per region. The tile file for the Cartagena tile is
~10–50 MB after gzip.

Source preference: **Microsoft Buildings primary** (better quality,
wider coverage), Google Open Buildings as fallback. Google's data is
available where Microsoft's isn't (some African regions); not relevant
for Cartagena.

### Stage B — merge with OSM into BuildingRecord list

In `region_pdf.py`'s building-loading path, after OSM is parsed:

1. Call `fetch_microsoft_buildings_for_bbox(bbox)`.
2. For each satellite polygon: compute centroid, area, and shapely
   geometry exactly like OSM polygons.
3. De-dup against OSM: if a satellite polygon's centroid is within
   any OSM polygon, OR overlaps an OSM polygon by ≥ 50% IoU, drop
   the satellite polygon (OSM wins — it has height tags and OSM IDs).
4. The surviving satellite-only polygons become BuildingRecord
   entries with `height_tag_m=None`, `height_source="ms_buildings"`,
   `feature_id="ms_<hash>"`. Untagged → existing `_height_proxy`
   sqrt-area fallback kicks in.

Site config gets a new optional field:

```json
{
  "name": "cartagena",
  "use_satellite_footprints": true,
  "satellite_source": "microsoft",
  ...
}
```

Default `false` so existing runs are unaffected. Cartagena's site
config gets it set to true.

### Stage C — verify on the diagnostic minimap

The orange "considered but lost" dots from F-SKY6 will now include
satellite-derived buildings in the central seed_5 gap if the data
helps. We can also color satellite-sourced polygons differently in
the minimap context layer (faint blue tint vs OSM's grey) to make
the new source visible.

## Target files

- `city2stl/skyline/satellite_footprints.py` (new) —
  `fetch_microsoft_buildings_for_bbox`, `_dedup_against_osm`,
  quadkey helpers, cache.
- `city2stl/skyline/region_pdf.py` — read site config flag, call
  the fetcher, merge into building list before the per-seed loop.
- `city2stl/skyline/sites/cartagena.json` — add
  `"use_satellite_footprints": true`.
- `city2stl/skyline/README.md` — document the new field + the
  one-time download cost.

## Success criteria

- For Cartagena seed_5 around heading 298°, the matcher's segment
  count rises (new satellite-derived polygons in the central x-band
  give the matcher new candidates to assign).
- The mask panel's central cyan blob acquires numbered segment
  overlays where it previously had none.
- For b3 + F-SKY7 baseline (n=17 MAE 13.73), F-SKY8 should not
  REGRESS the tagged-building MAE. Satellite polygons don't carry
  OSM `height` tags, so they only affect the un-tagged building
  count — they shouldn't displace tagged matches.
- `seed_extracted_buildings` rises meaningfully (+20-50 % expected
  on a Cartagena-style waterfront).
- 21 unit tests still pass.
- Repeat runs hit the local cache and don't re-download.

## Known risks

- **Download size / repo storage**: the quadkey-9 tile for Cartagena
  is ~10–50 MB. Cached under `runs/satellite_footprints_cache/` which
  is gitignored.
- **Microsoft data licence**: Open Data Commons Open Database License
  (ODbL). Same as OSM — derived works share-alike. Documented in
  README; doesn't affect the pipeline.
- **Polygon quality**: ML-derived footprints sometimes mis-merge
  adjacent buildings into one polygon (single polygon for what's
  really three towers). When that happens the matcher treats it as
  one wide building. That's a recoverable failure (no spurious match;
  worst case identical to OSM's "no polygon" outcome).
- **De-dup false positives**: a satellite polygon might be discarded
  because it overlaps an OSM polygon, even if it represents a
  different building (e.g. close towers OSM merged into one). Use
  IoU ≥ 0.50 to be conservative.

## Out of scope (deferred)

- Google Open Buildings as primary source — defer until we have a
  region where Microsoft is missing coverage.
- Auto-detection of satellite-source quadkey URL (use a fixed CDN
  template for now; URL refresh handled by the cache invalidation
  step when needed).
- Pulling satellite imagery itself (the visual pixels) — F-SKY10
  territory.

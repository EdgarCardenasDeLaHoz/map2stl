# Building Height Pipeline: Limitations and Improvement Plan

_Last updated: 2026-04-30_

## Status snapshot

| Area | Status |
|---|---|
| Multi-provider parallel fetch + merge | Done (production) |
| Phase 1b providers (GHSL, Open Buildings, Shadow) | All wired with real fetch paths (Overture Maps for OpenBuildings, shadow detection for Shadow) |
| ML-based height inference | **Active** — Retna_V1 in iterative training. See [`docs/plans/height-training-status.md`](plans/height-training-status.md) |
| Roof geometry from OSM tags | F-ROOF1 done (slanted roofs in city raster); flat-only fallback when shapes unset |
| STL import + IDW infill (Phase 3) | Done |
| Performance | Per-provider parallelism done; bandwidth/COG streaming not done |

---

## Open work, ranked

### 1. ML height model — close the tall-building gap (in progress)

The current Retna_V1 model achieves MAE 4.14m / IoU 0.58 / r=+0.87 on the 130-tile combined dataset, but tall-building tiles still see MAE 13–16m. Capacity is not the bottleneck (7× param growth did not help).

**Levers being tried:**
- 512px tile collection (4× pixel signal) — done
- HEIGHT_NORM_M raised 100 → 200 (less saturation) — done
- Cubic residual loss (`dice_l3`) for tall-building emphasis — done

**Levers not yet tried:**
- Wider receptive field (5+ blocks, or dilated convs)
- Auxiliary loss on per-tile mean height (force amplitude calibration)
- Pretrain on synthetic shadow-length → height pairs

### 2. Wire active Retna model into the height-fetch pool

The legacy `RoofNetProvider` is auto-disabled. A Retna provider does not yet exist. Once Retna stabilizes, write a thin provider:
```
class RetnaProvider:
    def fetch_heights(self, bbox, dim) -> HeightResult:
        rgb = fetch_satellite(bbox, dim) / 255.0
        pred = self.model(rgb) * HEIGHT_NORM_M
        return HeightResult(raster=pred, confidence=0.65, …)
```

### 3. Bandwidth / streaming improvements

Rasters are returned as raw arrays. Improvements deferred until model quality reaches a usable bar:
- WebP / Cloud-Optimized GeoTIFF for large-bbox responses
- Progressive / chunked streaming for previews
- Explicit min/max + colormap metadata in API responses

### 4. Roof geometry refinement

F-ROOF1 (slanted roofs in raster) shipped. Pending:
- `building:part` support (multi-part skyscrapers)
- Roof-orientation refinement from OSM `roof:direction` tag (basic compass parsing in place; PCA fallback works)

---

## Closed (kept for reference)

- **Provider robustness:** parallel fetch pool with per-provider error isolation in place; coverage maps logged at fetch time; 532 backend tests pass.
- **Phase 1a / Phase 3:** see `docs/height-pipeline-plan.md`.
- **F-ROOF1 (slanted roofs):** rasterizer in `city2stl/rasterize.py` paints per-pixel roof heights for gabled / hipped / pyramidal / skillion / dome / flat shapes.

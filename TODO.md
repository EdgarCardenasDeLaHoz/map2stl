# TODO — strm2stl

> See `docs/` for architecture reference. Completed items: see `docs/functionality_doc.md` and `docs/issues.md`.
> Per-module TODOs and improvement plans are in each module's `TODO.md`.
> **AI-proposed features live in [`docs/proposals.md`](docs/proposals.md) — set status to `approved` there to queue implementation.**
> **Linked working list:** [`docs/todo-linked-index.md`](docs/todo-linked-index.md)

_Last updated: 2026-04-30_

---

## Genuinely open

### Frontend

| ID | Module | Description |
|---|---|---|
| MAP-2 | `map/bbox-panel.js` | Keyboard accessibility on bbox drag handles (`tabindex` + arrow-key handlers) |
| UX-1 | `map/region-creation.js` | Single entry point for region creation (currently multiple buttons); add empty-state hint |
| Plan B | `layers/composite-dem.js` | Progressive (downsampled) preview during heavy composite computation |
| P6 | `export/` | Multi-material STL export by elevation band (server endpoint + client UI) |
| EXP-1 | `export/` | Progress indicator during STL generation (status conflicting — verify before working) |

### Backend / ML

| ID | Description | Detail link |
|---|---|---|
| ML-1 | Wire active Retna_V1 model into the height-fetch pool as a `RetnaProvider` | [`docs/height-pipeline-improvement-plan.md`](docs/height-pipeline-improvement-plan.md) |
| ML-2 | Close tall-building MAE gap (currently 13–17 m on skyscraper tiles) | [`docs/plans/height-training-status.md`](docs/plans/height-training-status.md) |
| 1b-OB | Open Buildings provider — already implemented (Overture Maps); needs production validation | [`docs/open-todo-plans.md#11`](docs/open-todo-plans.md#11-open-buildings-v3-real-fetch-path) |
| 1b-Sh | Shadow height provider — already implemented; needs validation against ground truth | [`docs/open-todo-plans.md#12`](docs/open-todo-plans.md#12-shadow-height-actual-inference-pipeline) |

---

## Module TODO Files

| Module | File |
|---|---|
| `dem/` | [`modules/dem/TODO.md`](app/client/static/js/modules/dem/TODO.md) |
| `layers/` | [`modules/layers/TODO.md`](app/client/static/js/modules/layers/TODO.md) |
| `ui/` | [`modules/ui/TODO.md`](app/client/static/js/modules/ui/TODO.md) |
| `core/` | [`modules/core/TODO.md`](app/client/static/js/modules/core/TODO.md) |
| `events/` | [`modules/events/TODO.md`](app/client/static/js/modules/events/TODO.md) |
| `map/` | [`modules/map/TODO.md`](app/client/static/js/modules/map/TODO.md) |
| `regions/` | [`modules/regions/TODO.md`](app/client/static/js/modules/regions/TODO.md) |
| `export/` | [`modules/export/TODO.md`](app/client/static/js/modules/export/TODO.md) |

---

## Recently completed (condensed)

### ML pipeline

- Replaced RoofNetV3 (1.4 M params, marginal-mean collapse) with **Retna_V1** (9.7k–75k params, first-principles)
- Added grow/prune NAS with all-layer growth + smart-init (clone top-scoring neurons into new slots) + deepen (add new blocks)
- Single-channel zero-ablation prune (final + periodic) — halved current champion's params (149k → 75k) at equal val_loss
- Persistent Adam optimizer state across cycles when arch unchanged
- Visual tile-review tool (`scripts/tile_review.py`) for manual GT-quality filtering
- Standalone `scripts/train.py` entry point: train / grow / deep / collect / full / inspect
- Cleaned `models/` from 50+ failed checkpoints to 5 keepers (~409 MB → 7.5 MB)
- Per-block contribution + weight-L2 panels in `tools/ml/inspect_retna.py` PDF output
- Current champion: `retna_pruned.pt` — [8,8,10,20,14,14,16,16,22], 75.5k params, MAE 3.82m / IoU 0.625 / r=+0.90

### Height pipeline

- Phase 1a (4 production providers + router + merge), Phase 1b (GHSL, Open Buildings via Overture, shadow inference), Phase 3 (STL import + IDW infill) — all complete
- F-ROOF1 — slanted roofs (gabled / hipped / pyramidal / skillion / dome / flat) burned into per-pixel city raster

### Frontend (last sprint)

- PERF6B (Web Worker / OffscreenCanvas), UX-M (lazy canvas allocation), CLEAN-1 (inline → CSS), Plan A (off-thread DEM render), curve-editor refactor + presets versioning, region pagination/import-export, single-entry region creation (partial — UX-1 still open), event-bus consolidation, magic-number extraction

### Session client

- Refactor (REFACTOR-1..4): 7 helpers + 5 settings properties, ~150 lines reduced
- Server lifecycle: graceful start, bbox validation, longer wait timeout

---

## Where things go

| Want to... | Edit |
|---|---|
| Add a new AI-proposed feature | `docs/proposals.md` (set `status: approved` to queue) |
| Run an ML experiment | `tools/ml/pipeline.py` |
| Update the height-fetch pool | `app/server/core/height/providers/` + `app/server/routers/height.py` |
| Add a frontend module | Read `docs/modules.md` first, then `app/client/static/js/modules/<module>/TODO.md` |
| Expand a region's coverage in tests | `tests/test_height/`, `tests/test_geo/` |

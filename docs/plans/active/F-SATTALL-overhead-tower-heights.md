# F-SATTALL — Overhead heights for untagged towers (Geopose gate + existing lean/shadow)

Scope: let the satellite height code read untagged towers over 80 m, which it cannot today,
by adding a learned overhead height model (Geopose) as an independent "this is tall" gate and
second reading. Publishing rules in `city2stl/skyline/` are not changed (read only).

Status: steps 1–2 coded 2026-10-31; gate switched to `photo_mask` (see Decisions); GPU smoke and scoring of Geopose wait for the CUDA PC. Approved 2026-10-31; start with the pubgeo baseline and RS3DAda, swap in the 1st-place model when the user's zip lands. Runs alongside F-PHOTOPANO A+B (disjoint files); the tool layer waits for A+B. User choices (2026-10-31 form):
- **Approach:** unlock the existing lean/shadow measurement for towers, plus Geopose as an
  independent second reading.
- **Weights allowed:** pubgeo Geopose baseline (GitHub, Apache-2.0), RS3DAda (Hugging Face,
  MIT), Geopose 1st place (10.5 GB IEEE DataPort zip, the user downloads it behind their login).
- **Output:** both a satellite readings file and a review layer in the photo-label tool.
- **Validation:** San Juan lidar pack, the 7 published Bocagrande towers, the 7-city lidar set.

## Why towers go unread today

- `city2stl/height/satellite/measure.py::search_cap`: an untagged footprint is searched to
  60 m (shadow, lean) / 80 m (sweep). Only a drone `hint` over 80 m widens it to 220 m, and that
  was refused for publishing on 2026-10-09: the wide window found spurious tall leans on low
  buildings (Honolulu lidar, untagged: 35.6 % of footprints under 30 m vs 11.1 % at 60 m).
- `city2stl/skyline/_core/tiers.py::single_withheld`: a single reading over 2x the prior
  publishes the prior unless a validated satellite reading supports it
  (`tiers.py::single_support`: lean at conf 0.7, `ls` at any, multiview at 0.3).
- Cartagena: b0564 (148 m measured / 31 m published), b0400 (170 / 28) and four more are this case.

## Approach

1. **Geopose reading.** `city2stl/height/satellite/geopose.py` runs a Geopose model over the
   same Esri zoom-18 scene the lean/shadow code reads (`scene.py::fetch_tiles`) and writes a
   per-footprint height (median over the footprint, roof area only) plus the predicted lean
   vector. GPU queue (`wait_for_gpu`, no CPU fallback). Backends in order: Geopose 1st place
   (if the user's zip is present), pubgeo baseline, RS3DAda (low/mid rise only; it under-reads
   towers).
2. **Tall gate.** `search_cap` widens an untagged footprint to `HINT_TALL_CAP_M` when the
   Geopose height is over `HINT_TALL_M` (a new `geopose` field beside `hint`). This replaces the
   refused "any drone hint" gate with an independent model, and is measured against the same
   Honolulu false-tall rate before it is turned on.
3. **Lean cross-check.** The Geopose lean vector is compared with `scene.py::fit_lean` per scene;
   a footprint whose measured lean disagrees with the predicted one by more than a tolerance is
   capped at low confidence (catches the facade-feature false lean, e.g. Ravello 38 m).
4. **Outputs.** Readings go to the region's satellite readings file through
   `readings.py::save` as a new method `geopose` (not in `SUPPORT_MIN_CONF`, so it cannot
   publish on its own; the widened lean/shadow readings publish through the existing rules).
   The photo-label tool gets an overhead layer: footprint, Geopose height, lean, shadow and
   published height side by side.
5. **Scoring.** One script scores lean, shadow, Geopose and their agreement on San Juan lidar,
   the 7-city lidar set (1,794 buildings) and the 7 published Bocagrande towers (Estelar 202,
   Allure 190, Gran Bay 170, Portomarine 188, Nautica 161, Ravello 160, Palmetto 156). Result
   and the gate's go/no-go go to `docs/decisions/building-heights.md`.

## Target files

`city2stl/height/satellite/geopose.py` (new), `city2stl/height/satellite/measure.py`
(`search_cap`, lean check), `city2stl/height/satellite/readings.py` (method `geopose`),
`tools/photo_label/server.py` + `index.html` (overhead layer), `claude/scripts/score_overhead.py`
(new), tests `tests/test_satellite_geopose.py` and additions to `tests/test_satellite_heights.py`,
`models/README.md` (weights), `docs/INDEX.md`. No edits to `city2stl/skyline/`.

## Success criteria

- The circled Bocagrande tower and b0564/b0400 get a lean or shadow reading within 25 % of
  their published or lidar height.
- The Geopose gate keeps the Honolulu untagged false-tall rate at or below the 60 m window's
  11.1 %.
- Per-method error table on all three yardsticks, with a decision entry.

## Risks

- Geopose was trained on Atlanta/Jacksonville/San Fernando/Rio at ~30-50 cm; Esri zoom 18 is
  ~60 cm at Cartagena's latitude. Resampling may cost accuracy.
- The 1st-place model (EfficientNetV2-L) may not fit 4 GB VRAM at full tile size; tile it or
  fall back to the baseline.
- Withholding is decided in `city2stl/skyline/` (read only): a tower whose widened reading is
  below the support confidence still publishes the prior. That needs the panoramic session.
- The 2026-10-09 refusal applies until the gate is scored; it stays off by default until then.

## Decisions

- 2026-10-31: the pubgeo baseline cannot size towers. On the 7 Bocagrande towers it reads
  2.3–24.5 m against 156–202 m published; its raw AGL peaks at 14–27 m on Estelar, so it marks
  where a tower is but under-reads it 10–80x. Not usable as a height or as the tall gate.
- 2026-10-31 (user): the tall gate uses `photo_mask` agreement (F-PHOTOPANO step F, 2+ views
  agree, over `HINT_TALL_M`) instead of Geopose: `measure.py::PHOTO_MASK_GATE`, off by default,
  scored on Cartagena with `claude/scripts/score_tall_gate.py`. Cartagena only: Honolulu has
  no photo seeds, so the false-tall check is done on the gated Cartagena footprints.
- 2026-10-31 (user): GPU backends (RS3DAda, AltiMap, 1st place) run on the CUDA PC. This PC
  has an AMD RX 580 and a CPU-only torch (2.4.1+cpu). Smoke script:
  `claude/scripts/smoke_geopose.py`. The code (`geopose.py`, `geopose_nets.py`, 34 tests) is in
  place; `LEAN_SIGN` is unchecked.

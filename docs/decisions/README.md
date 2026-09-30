# Decisions

Why the code is the way it is: one file per topic.
- Each entry records a choice, the reason for it, and what was rejected.
  - Purpose: stop the same question being re-argued in the next session.
- Entries were split out of the old `Code/claude/memory-bank/decisions.md` log on 2026-09-28.
  - Old `decisions.md:NN` line references map to new anchors in `claude/scripts/decisions_line_map.json` (in `Code/`, unversioned).

## Topics

| File | Covers |
|---|---|
| [architecture.md](architecture.md) | Package layering (numpy2stl / geo2stl / city2stl / app), SDK vs pipeline, F-ARCH |
| [mesh-pipeline.md](mesh-pipeline.md) | Two-stage mesh (terrain stage, then buildings on terrain), export formats, build speed, caches |
| [terrain-dem.md](terrain-dem.md) | DEM sources, cache keys, availability, empty-DEM handling |
| [projections-raster.md](projections-raster.md) | Raster orientation, projection order, Mercator area |
| [composite.md](composite.md) | Composite DEM layers, panel order, engines |
| [osm-water-hydrology.md](osm-water-hydrology.md) | Overpass/osmnx, sea recovery, water rasters, rivers |
| [trails.md](trails.md) | Ski/hiking trails layer |
| [building-heights.md](building-heights.md) | Height providers, priority, units, 3D Tiles, high-rise detection |
| [survey-lidar.md](survey-lidar.md) | Survey and lidar sources, OpenTopography |
| [roofs-landmarks.md](roofs-landmarks.md) | Roof pitch/shape, landmark overrides |
| [ml-height.md](ml-height.md) | Learned height models (not live) and checkpoints |
| [registration-plate-location.md](registration-plate-location.md) | Locating a vendor plate: scale, rotation, water and building solves |
| [registration-refinement.md](registration-refinement.md) | Refinement, wide window, street placement, footprint channel |
| [registration-validation.md](registration-validation.md) | Ground truth, consensus, measured-and-refused hypotheses |
| [plate-critic.md](plate-critic.md) | What counts as evidence when judging a plate |
| [frontend.md](frontend.md) | Vue UI, layer panels, state ownership |
| [repo-tooling-docs.md](repo-tooling-docs.md) | Repos, venvs, rename, docs layout, agent scripts |

Research notebooks behind some of these: [shadow heights](../research/shadow-heights.md), [roof shapes](../research/roof-shape-research.md), [plate critic](../research/plate-critic.md).

## Entry format

```
### YYYY-MM-DD — <decision, stated as a fact>
- **Decision:** what was chosen.
- **Why:** the reason; the measurement that settled it; user quote if any.
- **Rejected:** <alternative> — why not.
- **Supersedes / superseded by:** link to the other entry, or "—".
- **Source:** plan link or commit (optional).
```

- Newest first within each file.
- Superseded entries stay.
  - The heading gets ` [superseded]`.
  - The bullet links the replacement, so the old reasoning is still readable.
- Findings that were measured and refused go under `## Rejected hypotheses` at the end of the file.
  - Format: **Hypothesis**, **Measured**, **Verdict**.
- Cite code as a backticked path::symbol (e.g. `city2stl/registration/street_place.py::mesa_additions`), never path:line, because line numbers rot.

## How to add a decision

1. Pick the topic file. If none fits, add a file and a row to the table above.
2. Add an entry at the top of the file (newest first), in the format above.
   - Keep it to bullets: the decision, why, and what was rejected. Leave out the session narrative.
3. If it replaces an older entry, tag the old heading ` [superseded]` and link both ways.
4. When a plan in `../plans/active/` moves to `../plans/done/`, lift its "Decisions" section here.
   - The plan keeps a link to the entry.
5. Check links: run `claude/scripts/check_doc_links.py` from `Code/` with `--only "map2stl/docs/decisions/**"`.

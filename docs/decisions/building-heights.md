# Building heights

Where per-building heights come from (OSM tags, raster providers, Google 3D Tiles, shadows), how
providers are merged and ranked, and how height accuracy is measured. Related:
[survey-lidar.md](survey-lidar.md) (surveyed lidar references), [roofs-landmarks.md](roofs-landmarks.md)
(roof geometry). Research notebook behind the shadow entries: [../research/shadow-heights.md](../research/shadow-heights.md).

Scratch paths cited below (`bench/`, `tiers/`, `sat/`, `sat2/`, `own/`, `own2/`, `ob25/`, `support/`, `tri/`, `modeleval/`, `depthstudy/`, `docs_*.md`, `new_cities/README.md`) were temporary; copies (no images, no file over 50 MB) are in `Code/claude/scratch_backup_2026-10-09/`; `anchors/` (2026-10-10) is in `Code/claude/scratch_backup_2026-10-10/` (scripts, logs, tables and the changed-row list, no pickles or tiles).

### 2026-10-23 — Google 3D Tiles ground can be a sloped plane per window (`ground="plane"`), opt-in
- **Problem:** `_ground_from_dsm` takes a per-window p5 of the DSM as flat ground. On a hillside the
  window's low corner sets the ground, so buildings uphill read tall: about +1.26 m per 1 % of slope.
  This is the likely cause of Seattle's +3.9 m bias against its LiDAR truth.
- **Fix:** `Google3DProvider(ground="plane")` fits a plane per window: a least-squares fit, then 4
  refits on the lowest 15 % of residuals. The windows are tent-blended.
  - The keep share must stay under the window's ground share. At 30 %, roof cells got into a
    synthetic scene with 28 % ground and read -0.75 m on every slope.
- **Synthetic check** (`tests/test_height/test_google_3d.py::test_plane_ground_holds_on_slopes`), mean
  roof error:

  | Slope | Old p5 | Plane |
  |---|---|---|
  | 0 % | 0.0 m | 0.0 m |
  | 3 % | -3.3 m | 0.0 m |
  | 10 % | -11 m | 0.0 m |

- **Why opt-in:** the cache stores ground-subtracted heights. Changing the default would re-key and
  re-download (paid) every cached raster. So `ground` enters the cache key only when it is not the
  default.
- **Seattle check (2026-10-24, user-approved, 55 tile fetches):** 402 truth footprints against the
  3DEP LiDAR survey.

  | Height band | n | p5 median | p5 MAE | Plane median | Plane MAE |
  |---|---|---|---|---|---|
  | All | 402 | +4.3 m | 8.8 m | +1.3 m | 3.9 m |
  | 0–15 m | 228 | +4.7 m | 10.8 m | +2.0 m | 5.1 m |
  | 15–40 m | 135 | +4.3 m | 6.8 m | +0.8 m | 2.1 m |
  | 40–100 m | 39 | +1.4 m | 4.5 m | +0.1 m | 3.1 m |

  - The plane is worse by more than 1 m on 48 of the 402.
  - So the benchmark now uses `ground="plane"` (`benchmark.tiles_ndsm`). The provider default stays
    `percentile` to keep the cached rasters valid.
  - Truth records cached before this keep their p5 `tiles_m` until they are refreshed. The plane
    rasters from this check are cached under the scratch tile bboxes, not `tiles_for`'s, so a
    Seattle refresh would fetch again.

### 2026-10-23 — Commons photo screen: a rescued photo needs p75 |photo - OSM| <= 40 m; a low-light photo needs 80 % outline coverage
- **Decision:** `17_photo_pipeline.py::_keep` rescues a gate-failing photo only when also `_abs_dev_p75(towers) <= --rescue-max-p75` (default 40 m). `commons_photos.quality_reject` keeps a low-light photo (sky value < 170) only when `score >= MIN_SKYLINE_QUALITY` (0.11) **and** `coverage >= MIN_LOW_LIGHT_COVERAGE` (0.80). Day photos stay ungated.
- **Why:** the median OSM deviation let Miami p20 (19.1 m, rejected by the user) through; its p75 is 76 m (p79 too), against 23-33 m on all 10 other rescued Miami photos. On the 9 reviewed low-light photos no score threshold separates usable from bad (usable Chicago p655 scores 0.075, below four bad ones), but coverage does for 8 of 9 (bad <= 0.77, usable >= 0.86; the miss, p655, the score already rejects), and it also rejects p20.
- **Rejected:** a tighter median cutoff or a higher tower count (p20 sits between usable p191 15.9 m and p264 20.0 m, at 20 towers); a coverage-only clarity rule replacing the score (same verdicts on this set, less conservative).
- **Supersedes / superseded by:** refines the 2026-10-07 screen entry below; —.
- **Source:** `skyline-local/benchmark/2026-10-05_2148/miami_skyline_report/photo_results.json`, `2026-10-03_1952/chicago_.../photo_results.json`, `commons_cache/<city>/profiles.json`, the 37-photo verdict table from the 2026-10-07 review. Chicago: drops 11 of 53 rescued, their truth MAE 26.9 m vs 18.1 m kept (no verdicts). Evidence is two bad photos and one reviewer: confirm on the next reviewed screen (p79's rescued fit was never saved).

### 2026-10-23 — T41: the untagged-building fallback is the geometry prior, with a 12 m constant behind it; scored leave-one-city-out on cached truth
- **Decision:** `city2stl/skyline/_core/height.py::untagged_fallback` hands `withhold_untagged_street_view` the T38 geometry model (`city2stl/skyline/untagged_prior.py::fallback_for`, trained on `city2stl/skyline/data/untagged_prior_train.csv`, 872 confirmed untagged buildings in 8 cities). `SKYLINE_UNTAGGED_FALLBACK=constant`, a missing training file or no scikit-learn gives `UNTAGGED_FALLBACK_M` (12 m, was 10 m). One switch, the existing T28 path.
- **Why:** measured 2026-10-23 with `10_benchmark --score-only --cached-truth` and a re-score of each newest report (Street View put back, then withheld three ways). The model is fitted without the scored city (`exclude_city`), so every number is out-of-sample. MAE in m on confirmed untagged buildings (all confirmed buildings in brackets; tagged rows keep their tag):

  | City | n untagged | 10 m | 12 m | model |
  |---|---|---|---|---|
  | Miami | 83 | 6.4 (9.7) | 6.4 (9.7) | 6.7 (9.9) |
  | Chicago | 133 | 19.8 (20.8) | 19.1 (20.5) | **17.4 (19.6)** |
  | Seattle | 120 | 12.0 (13.5) | 12.0 (13.5) | **9.9 (11.9)** |
  | Boston | 393 | 10.1 (12.8) | 9.4 (12.4) | **8.0 (11.6)** |
  | Benidorm | 9 | 5.7 (19.5) | 7.3 (19.6) | 8.3 (19.7) |
  | La Défense | 87 | 8.0 (8.3) | 8.1 (8.3) | **5.5 (6.0)** |
  | Madrid | 59 | 11.5 (17.9) | 11.6 (18.0) | **10.4 (17.3)** |
  | Prague | 9 | 26.0 (25.5) | 24.6 (25.0) | 25.7 (25.4) |
  | **pooled** | 893 | 11.4 | 11.0 | **9.6** |

  - Model beats both constants in 5 of the 6 cities with 50 or more untagged buildings; Miami is 0.3 m worse than either constant; Benidorm and Prague (9 each) are too small to read. Pooled gain over 12 m: 13 %; over 10 m: 16 %. Matches T38's own leave-one-city-out (9.4 against 11.3 m).
  - The 12 m constant beats 10 m pooled (11.0 against 11.4 m), so it is the floor when the model is absent.
  - Street View left in scores 51-102 m headline MAE per city (Miami 77 m unwithheld), against 6-20 m withheld.
- **Rejected:** a per-city calibrated constant — the median untagged truth is 11.5-13.1 m whichever city is left out, so there is nothing to calibrate. Tagged rows in training — worse in the 0-30 m band (T38).
- **Supersedes / superseded by:** — (T28 flag unchanged).
- **Source:** `docs/research/ml-blind-2026-10-05/T38-results.md`; `tests/test_skyline_untagged_prior.py`.

### 2026-10-21 — Cached floor-count heights are re-derived on read with the pitched-roof rule
- **Decision:** `city2stl/heights.py::apply_levels_heights` re-runs `height_from_tags` on every `"osm_levels"` building of a cached payload (clip 3–300 m, as `fetch.py` does), first thing in `app/server/core/city_data.py::with_low_default_heights`, so every read path gets it. No OSM cache version bump and no refetch; the model cache follows by itself because its keys hash the features.
- **Why:** the 2026-10-20 rule (a roof floor only for a pitched `roof:shape` or a `roof:levels` tag) reached only fresh fetches. Both cached Granada payloads (pipeline v5) had 22,992 of 23,538 floor-count buildings exactly 3.2 m above the rule, which was the whole `osm_levels` bias. Granada rebuilt against the CNIG truth (`runs/panoview/granada/score_after.json` -> `score_levels_fix.json`): intended, all rows, MAE 3.57 -> 2.31 m, bias +2.80 -> +0.74 m, within 25 % 59.9 -> 74.3 %; `osm_levels` bias +3.19 -> +0.91 m; mesh reading MAE 6.81 -> 5.87 m, within 25 % 36.5 -> 46.6 %, bias -0.94 -> -2.45 m (the mesh was already reading about 3 m under intended; the old roof floor hid it).
- **Rejected:** bumping the OSM cache version (a full Overpass refetch of every cached city for a rule the cached tags already decide).
- **Supersedes / superseded by:** —; —.

### 2026-10-21 — Granada truth is CNIG-first, REDIAM only where CNIG shows no building; survey rows are held out of the ranking
- **Decision:** `benchmark.REGIONS["granada"]` is `cnig_mdsn`; `benchmark.FALLBACKS["granada"] = "rediam_mdhn"` is read only where CNIG has no value or under `NO_BUILDING_M` (`survey_heights.survey_footprint_heights(fallback=)`, the order `survey_fill.py` uses). Every truth record carries `survey_provider`. `19_refresh_survey_truth.py --rebuild-fallback [--offline-rings]` rebuilt `truth/granada.json`; the old file is `granada.rediam_mdhn.json` beside it. `tools/eval/score_export_heights.py` adds `--exclude-source` (default `survey`): `*_excluding` blocks hold survey-filled rows out, since a CNIG truth is not independent of a CNIG fill.
- **Why:** REDIAM MDHN suppresses building cells. On the same footprints CNIG p95 is a median +3.5 m above REDIAM (9,794 records); on the old truth every Granada source read +5 m high (all rows +5.4 intended, mesh +2.3).
- **Rejected:** keeping REDIAM as primary (biased low); per-city calibration of the old truth.
- **Supersedes / superseded by:** the 2026-10-13 REDIAM-first choice in [survey-lidar.md](survey-lidar.md); —.
- **Source:** export bbox only. The WCS was unreachable and the CNIG/REDIAM rasters are cached for the export bbox alone, so 9,794 records are CNIG, 165 fell back to REDIAM, and 3,173 (outside the bbox or relation outlines with no cached ring) keep their old REDIAM value (`survey_provider: rediam_mdhn`); rings came from the cached Overpass ways by osm_id, unsimplified. Re-run with network to finish them. Scores (`runs/panoview/granada/score_{before,after}.json`), intended all-rows bias +5.4 -> +2.0 m (before model) and +6.2 -> +2.8 m (after model) against old -> new truth; the CNIG-based truth now shows `osm_levels` +3.2 m high.
- **Finished 2026-10-23** (network rebuild): 12,805 CNIG, 327 REDIAM records (234 fallbacks, 93 failed reads), 8 without a ring; backup `granada.rediam_mdhn.json` is the original REDIAM truth. Fresh export (`2ee6aaa3…npz`) scored: intended MAE 3.04 m, bias +2.22 m, 66 % within 25 % (6.60 / +6.21 / 26 % on the old truth), without survey rows 3.26 / +2.55 / 63 %; `osm_levels` +2.5 m; mesh 5.9 / -0.5 / 44 % (`runs/panoview/granada/score_2026-10-23_truth-cnig.json`).

### 2026-10-20 — Survey only raises landmarks, also checks floor counts, and the roof level needs a pitched roof
- **Decision:** (B1) `survey_fill.py::is_landmark` (building:part rows, outlines with parts, towers, churches and places of worship, castles, any named building with `historic=*` or `tourism=attraction`, and the towers/churches layers) marks rows the survey may only RAISE, never lower. (B2) `_burn_stats` measures once per layer, and an outline over its full footprint. (B3) `osm_levels` rows get the same 3 m check as `osm_tag`; footprints under `SMALL_FOOTPRINT_M2` (30) read the median (`SMALL_STAT_PERCENTILE` 50) instead of p90. `user_override` and `merged` stay untouched. (C) `heights.py::height_from_tags` adds the implicit roof floor only when `roof:shape` is tagged and not `flat` (`has_pitched_roof_shape`); 3.2 m per floor kept. `MODEL_CACHE_VERSION` 7.
- **Why:** the old fill lowered the cathedral (way/124069145) from 45 to 19.8 m and its parts to a 4.3 m minimum; the +1 roof floor put floor-count rows a median 3.2 m above the survey on flat-roofed outlines (56% over 3 m off).
- **Rejected:** keeping landmarks fully survey-driven (lead roofs read low under the spire); a per-city calibration.
- **Supersedes:** the "`osm_levels` rows are never touched" part of the 2026-10-17 survey entry.
- **Source:** cached Granada. Floor-count outlines: median bias +3.19 -> +0.04 m, over-3 m share 56% -> 16%; parts 56% -> 19% (landmark parts only rise). Cathedral outline 45, spire 50, parts 16.5-45 kept. Cached payload heights stay stale for C until a fresh fetch or the survey re-measures them.

### 2026-10-17 — Survey wins when far off: an osm_tag height is replaced by the survey's beyond 3 m [restored 2026-10-18 after a OneDrive conflict dropped it]
- **Decision (user, 2026-10-17):** in the survey fill (`city2stl/height/survey_fill.py::fill_layer_heights`) an `osm_tag` feature
  (any value) that a validated survey covers is replaced when the measured height differs from the tag by more than
  `TAG_OVERRIDE_MIN_DIFF_M` (3.0 m): `height_source "survey"`, `survey_source` as usual, the tag kept in `osm_tag_height_m`.
  Within 3 m the tag stays. `osm_levels`, `merged` and `user_override` rows are never touched. Bare 10.0 m tags (`BULK_TAG_HEIGHT_M`)
  keep their earlier behaviour: always filled. Stats: `tag_checked`, `tag_replaced` (also per layer); `filled` / `defaults` count only
  fillable rows.
- **Why:** the tag can be a mapper's guess (Torre de la Vela, del Homenaje and de Comares carry an explicit `height=10` in OSM); lidar
  is a measurement, but within 3 m the two agree about as well as the survey can be trusted, so the tag is not overwritten.
- **Rejected:** replacing every tag — survey noise would overwrite good tags; never replacing tags — leaves 10 m towers at 10 m.
- **Supersedes / superseded by:** refines the fill's "bare 10.0 m `osm_tag` only" rule; — .
- **Source:** conflict copy `city2stl/height/survey_fill-DESKTOP-TMJM22C.py`. Granada cached payload then: 113 tags checked, 77
  replaced (Vela 10 -> 28.8 m, Comares 10 -> 36.5 m, Homenaje 10 -> 23.0 m).

### 2026-10-17 — The city export can take corroborated + single panorama heights, opt-in; tag and prior rows stay out
- **Decision (user, 2026-10-17):** a city export request may carry `panorama_heights` (a region report
  name, folder or `heights.json`). `city2stl/panorama_heights.py` replaces the height of a footprint
  that has the default or a raster height with the report's `effective_height_m`, for tiers
  `corroborated` and `single` only; source becomes `panorama_corroborated` / `panorama_single`, the
  old value is kept as `height_prev_m` / `height_prev_source`, and report.json gets a
  `panorama_heights` block (rows, applied, skipped).
- **Why these tiers:** `tag` rows already carry the OSM tag (and the exporter reads the tag itself),
  `prior` rows are the height model's guess, `survey` rows are lidar the exporter fetches on its own.
  Never replaced: `osm_tag`, `osm_levels`, `landmark`, `survey*`, `lidar*`: a measurement or a
  stated height beats one panorama reading.
- **Why by footprint overlap (IoU ≥ 0.5, best overlap wins):** a report is built from an earlier fetch
  of the area, so OSM ids and vertices do not match.
- **Measured (Cartagena, v19_extras, OSM fetched 2026-10-17, 3,104 buildings):** 147 rows → 133
  features changed (21 corroborated, 112 single), 13 rows kept because the footprint has an OSM tag
  or levels, 1 without a footprint match. The Bocagrande tower rows rise in the 3D export where the
  base build had raster / default heights.
- **Not validated:** the panorama heights are only as good as their tiers (see the 2026-10-12
  entries); the option is off by default.

### 2026-10-17 — The survey fill covers the landmark layers and walls, and treats a bare 10.0 m height tag as a fallback
- **Decision:** `city2stl/height/survey_fill.py::fill_layer_heights` fills `towers`, `churches`,
  `fortifications` and `walls` as well as `buildings` (walls are lines, buffered 1.5 m each side).
  A feature is fillable (`fillable`) on `height_source` `"default"`, or on an `osm_tag` of exactly
  10.0 m unless a raw `height` / `building:height` of 10 is kept on it. The fill stats go into the
  payload as `survey_fill`, and into `report.json`. Client `layer_data` (height overrides, roof
  edits) is merged onto the server's layers (`app/server/core/city_data.py::merge_layer_data`):
  the client wins, except a feature still fillable takes the server's survey height. The fill
  also runs in `read_city_layers_any_tolerance` (composite, raster) and the pre-flight. Still on
  read, so no `CITY_PIPELINE_VERSION` bump.
- **Why:** F-DETAIL D1. Granada's landmarks stood at 10 m or 12 m: 24 of its 31 `osm_tag` towers
  are exactly 10.0 and 6 more 12.0, round-number mapper estimates. The payload keeps no raw tags,
  so a deliberate `height=10` cannot be told apart; the survey must still measure the feature or
  it keeps its 10.0. Granada after: towers 31 survey / 9 osm_tag / 3 default (was 31 osm_tag,
  12 default); churches 43 survey; fortifications 10 survey; walls 18 survey; 427 of 440
  candidates filled (327 CNIG, 100 REDIAM). Torre de la Vela 10 -> 28.8 m, Torre del Homenaje
  10 -> 23.0 m, Torre de Comares 10 -> 36.5 m, Torres Bermejas 12 -> 19.0 m, Alcazaba 12 -> 7.8 m.
  An override export kept the 325 survey building heights.
- **Rejected:** 12.0 m as a fallback too — not asked for; it stays a tag. Keeping the
  `layer_data` swap — it dropped the survey heights when the client copy predated them.
- **Open:** the buildings-layer Torre del Homenaje footprint stays 10.0 (survey could not measure
  it). To protect a genuine `height=10`, `city2stl/fetch.py` would have to keep `height` in the
  payload; the rule honours it once present.
- **Supersedes / superseded by:** extends the entry below.
- **Source:** [../plans/active/F-DETAIL-survey-detail.md](../plans/active/F-DETAIL-survey-detail.md) D1.

### 2026-10-17 — In Spain, default-height buildings take their height from CNIG's building nDSM, with REDIAM only as a fallback
- **Decision:** a footprint still on `height_source` `"default"` (no height tag, no levels, or
  lowered by the low-default rule below) gets the p90 of CNIG MDSn edificación (`mdsn_e025`, IDEE
  WCS, 2.5 m, 1st PNOA coverage 2008-15) over its cells after a 1 px inward buffer, when at least
  half the cells are valid and the value is 1 m or more; clamped 3-300 m, `height_source`
  `"survey"`, `survey_source` names the raster. Where CNIG reads under 1 m (the structure is not in
  its building class) and REDIAM MDHN covers the city (Andalucía), REDIAM's value is used instead.
  REDIAM is never the primary source. Other survey providers (IGN LiDAR HD, ČÚZK, USGS EPT) are
  left out of the fill until validated the same way; the US keeps its 3DEP pass. Code:
  `city2stl/height/survey_fill.py::fill_default_heights` (`FILL_SOURCES`, `footprint_stats`),
  run by `city2stl/height/service.py::_enhance_from_survey` on a fresh fetch and by
  `app/server/core/city_data.py::with_survey_heights` on every cached read in `get_city_layers`
  (before this, `enhance_city_data` ran only when buildings were fetched, so a cached city never
  got any enhancement; it is not behind a setting). No UI default changed.
- **Why:** Granada, 1 m grid, p90 after a 1 px buffer, scored against OSM levels heights less one
  level (OSM's levels formula adds a roof level, 3.2 m): CNIG median |Δ| **2.81 m** (median Δ
  +1.2 m, n=23,308; 3 levels 9.3 vs 9.6 m, 5 levels 16.0 vs 16.0 m, 10 levels 28.9 vs 32 m), 100 %
  valid, 92.7 % of building area ≥ 1 m, 86 % of footprints ≥ 80 % covered. That passes the gate
  the user relaxed to "levels-derived median |Δ| ≤ 3 m and coverage ≥ 80 %" and chose "CNIG +
  REDIAM fallback". On the cached Granada payloads the fill measures 251 of 253 default footprints
  (209 CNIG, 42 REDIAM; wider payload 348 of 352: 286 / 62), and 55 of the 57 low-default rows:
  Torres Bermejas 3 -> 19.0 m and Silla del Moro 3 -> 5.6 m (CNIG), Palacio del Partal 3 -> 15.5 m
  (REDIAM), Alcazaba 3 -> 9.5 m (CNIG; the enclosure's p90 is its towers, so the whole 4,127 m²
  outline stands at 9.5 m).
- **Rejected:** REDIAM MDHN as the primary source — measured and refused. Its metadata (GeoNetwork
  `4650aa97-6756-4cb1-af27-2ef4ccf9f740`) calls it a vegetation height model: building-class cells
  are suppressed. No pipeline fault: zero shift is the best offset (mean over footprints 6.60 m vs
  ≤ 6.55 m for every shift within ±10 m), no constant scale (ratio 0.48 at 1 level to 0.74 at 10),
  1 m to 1 m bilinear, nodata handled. Against levels heights less one level median |Δ| 3.91 m
  (median Δ −2.33 m); against the 74 height-tagged buildings 5.49 m; 24 % of in-building cells read
  under 1 m and 17 % are void; on CNIG's building cells it reads p90 8.9 m where CNIG reads
  14.6 m. It does read structures outside the building class (293 footprints: 4.9 m at 99 % valid,
  CNIG 0.2 m), hence the fallback. CNIG's own tagged-building score (10.0 m, Alhambra towers at 0)
  is the same class gap, which the fallback covers. CNIG's 2nd-coverage MDSnE 0.5 m — download
  centre form only, not scriptable; no 3rd-coverage MDSn exists.
- **Risks:** CNIG is 2008-15: a building put up later reads 0 there and falls back to REDIAM, which
  reads it low (or void, leaving it on default). REDIAM includes trees, so a ruin under canopy can
  read the trees. An enclosure's p90 is its towers (Alcazaba above). The user compared 3 m,
  6.2 m (median of built cells) and 9.5 m renders on 2026-10-17 and kept 9.5 m; per-cell lidar
  relief inside enclosures was offered and declined.
- **Supersedes / superseded by:** —
- **Source:** Granada study scripts (`p1_*.py`, `p2_verify.py`, session scratch, not kept).

### 2026-10-17 — An untagged ruin or fortress enclosure defaults to 3 m, not 10 m, and a ruin's levels tag is ignored
- **Decision:** a building with no `height` / levels tag gets `LOW_DEFAULT_HEIGHT_M` (3 m, the
  building clamp's floor) instead of the flat 10 m when it is a ruin (`building=ruins`,
  `historic=ruins`, `ruins=yes`) or a fortress enclosure / courtyard (`building=courtyard`, or
  `historic` castle / fort / fortification / citywalls unless `castle_type` is palace, stately,
  manor, schloss or chateau). A ruin whose height comes only from `building:levels` also gets 3 m:
  its levels describe the building it was, not what stands. An explicit `height=` tag still wins.
  These rows carry `height_source` `"default"` (the levels-ruins are moved to it), so height
  providers and the lidar fill can override them. New fetches apply it in
  `city2stl/heights.py::_fill_heights` (`low_height_override`, `default_height_for`; `ruins` and
  `castle_type` are now kept in `LANDMARK_TAG_COLS`); cached payloads get it on read
  (`app/server/core/city_data.py::with_low_default_heights`, idempotent, never raises a height),
  with no cache-version bump, so nothing is refetched while Overpass is down.
- **Why:** Granada Alhambra study: lidar reads the Alcazaba at about 0.2 m and tagged ruins at
  0.1-2.5 m, while the 10 m default (and 6-22 m from the ruins' levels tags) stood them up as tall
  blocks. User asked to "lower the default height for courtyards and ruins", chose ruins + fortress
  enclosures at 3 m, then chose to ignore levels on ruins. In the cached Granada payload it changes
  57 footprints: the 4 untagged `historic=castle` (Alcazaba 4,127 m², Silla del Moro, Torres
  Bermejas, Palacio del Partal) and 53 levels-tagged `building=ruins`.
- **Rejected:** applying it to every `historic=*` footprint — most are roofed buildings. Bumping
  `CITY_PIPELINE_VERSION` — forces refetches during an Overpass outage. Changing
  `city2stl/landmarks.py::DEFAULT_HEIGHT_M` — it mirrors `city_model`'s 10 m extrusion of a
  feature without `height_m`, and every building now carries one. A special case for Granada's
  false hits — the lidar fill will correct them.
- **Risk accepted:** a roofed castle tagged only `historic=castle` (no `castle_type`) drops to 3 m.
  Granada has two: Palacio del Partal (a roofed pavilion) and Torres Bermejas (towers); they stay
  at 3 m until a height provider fills them.
- **Supersedes / superseded by:** —

### 2026-10-12 — Truth-city drone spheres are wired; the ownership rule passes its catch mark in two truth cities but not its false-flag mark (the second-city acceptance stays open)
- **Decision:**
  - The 10 fitted drone Photo Spheres are in `seed_urls` + `elevated_seeds` (San Juan d1/d2,
    Fort Lauderdale d1/d3, Honolulu d1/d2, Sunny Isles d1-d4; seed names = position in `seed_urls`);
    the 5 that failed the outline gate stay in `pending_seed_urls` with a `_fit_note` (commit
    `b48a711`). `SKYLINE_ELEVATED_HIRES_HEADINGS=N` (`_pano/orchestrator.py`) lets a region run
    rebuild the hi-res pano from the N-heading capture cache (the spheres are cached at 15 headings,
    the run's spin is 12).
  - Cartagena's `use_ownership` / `use_cadastre_occluders` are **not** changed: the catch mark passed
    in both new cities, the false-flag mark failed in both, and the kept-within mark is not testable
    (n < 30) everywhere. The ownership rule is not turned on for the new sites either.
- **Measured** (pre-registered in scratch `truth_drone/notes.md` before the runs; region reports
  `runs/benchmark/2026-10-12_drones` from cache, no Street View request, no 3D Tiles, survey truth
  `--no-tiles`; scored in `runs/benchmark/2026-10-12_1626`):
  - Tiers (report rows, survey / corroborated / tag / single / prior): San Juan 10,825 / 1 / 7,194 /
    0 / 6; Fort Lauderdale 873 / 0 / 40 / 0 / 3; Honolulu 945 / 6 / 273 / 0 / 4; Sunny Isles 0 / 3 /
    1,049 / 6 / 234. The first `corroborated` rows outside Miami: 10 in all, every one within tol
    (San Juan 3, Honolulu 5 with truth; MAE 3.5 / 8.5 m).
  - Drone readings against survey p95 by truth band (survey-blind; n / MAE / bias / within
    max(25 %, 2 m); a band is not testable under n 30, n 10 above 100 m):

    | city | <15 | 15-40 | 40-100 | >100 |
    |---|---|---|---|---|
    | San Juan | 202 / 31.5 / +31.4 / 0.00 | 45 / 18.2 / +17.1 / 0.16 | 17 / 17.9 / -8.5 / 0.29 (n.t.) | none |
    | Fort Lauderdale | 52 / 81.9 / +81.8 / 0.00 | 4 (n.t.) | 13 / 77.7 / +77.7 / 0.31 (n.t.) | 6 / 27.2 / +27.2 / 0.67 (n.t.) |
    | Honolulu | 12 (n.t.) | 7 (n.t.) | 15 / 56.4 / +56.4 / 0.13 (n.t.) | 10 / 11.7 / +8.8 / 0.90 |

    The low bands are wrong-owner readings (a tower top credited to the small footprint in front),
    the Miami failure the ownership rule addresses; the pipeline withholds nearly all of them
    (`single over 2x prior`: San Juan 27, Fort Lauderdale 46, Honolulu 8), so the published tables
    barely move: published-row MAE vs the no-drone reports within +-1 m in every band except
    Fort Lauderdale 40-100 (9.6 -> 13.7 m, n 72 -> 80) and >100 (29.6 -> 33.3, n 22 -> 12) and
    Honolulu >100 (27.3 -> 28.6).
  - Sunny Isles (30-tower list only, no survey): report MAE 1.8 m on 30 towers (all OSM tags); a
    drone median on 5 towers, 3 within tol (Trump Towers III 145 vs 152, Turnberry Colony N/S 115 /
    104 vs 131); Acqualina South 25 m and Ritz-Carlton 20 m are wrong-owner readings. Not testable.
  - Second-city acceptance (`seed_experiment.py --region <city> --remeasure --own` on the capture
    states, survey truth >= 20 m, wrong = more than 25 % off; marks as pre-registered on Miami):

    | city | trusted with truth | wrong caught (>= 50 %) | right flagged (<= 5 %) | within tol: before -> kept (>= 0.80) |
    |---|---|---|---|---|
    | Fort Lauderdale | 33 | 10 of 16 (62 %) n.t. | 6 of 17 (35 %) FAIL, n.t. | 0.52 -> 0.65 (n 17) n.t. |
    | Honolulu | 39 | 19 of 20 (95 %) n.t. | 5 of 19 (26 %) FAIL, n.t. | 0.49 -> 0.93 (n 15) n.t. |
    | San Juan | 53 | 30 of 40 (75 %) PASS | 5 of 13 (38 %) FAIL, n.t. | 0.25 -> 0.44 (n 18) n.t. |
    | Miami (2026-10-09) | 64 | 20 of 28 (71 %) | 1 of 36 (2.8 %) | 0.56 -> 0.81 (n 43) |

    Flag reasons: owner_farther 17 / 20 / 181, owner_above_tag 16 / 19 / 46, owner_nearer 4 / 4 / 31
    (Fort Lauderdale / Honolulu / San Juan). The false-flag rate is 26-38 % against Miami's 2.8 %:
    the mark fails in both new cities, but on 13-19 right readings each (not testable at the
    pre-registered n 30).
  - Withhold rule `single over 2x prior` (region reports, survey truth, `pipeline_metrics`): precision
    1.00 / 0.96 / 1.00 and recall 0.93 / 0.90 / 0.73 (San Juan 34 readings, Fort Lauderdale 52,
    Honolulu 16): PASS where testable (San Juan, Fort Lauderdale); Honolulu n.t.
  - False-corroborated rate by band (corroborated rows off by more than tol, Wilson 95 %):
    San Juan 15-40 0/1 (0-0.79), 40-100 0/2 (0-0.66); Honolulu 40-100 0/1 (0-0.79), >100 0/4
    (0-0.49); Fort Lauderdale none. Not testable everywhere.
- **Why:** the audit's item 2 asked for two truth cities before the ownership and withhold rules
  count as more than "Miami only". The withhold rule now has them. The ownership rule catches wrong
  tops in all three cities but flags 26-38 % of right ones, so it cannot go on for the new sites,
  and Cartagena's flags are left as they are (the plan's "set them false" applies to a fail in both
  cities on testable marks; the false-flag denominators are 13-19).
- **Rejected:**
  - Fetching the 12-heading spin or the seed-resolution probe for the drone seeds (one 75-deg image
    per seed): the run reused the capture (a scratch shim answers the probe with the cached
    heading-0 view; the three-row spheres are rebuilt on their three cached rows). A plain region run
    would fetch one probe per drone seed.
  - Turning `use_ownership` on for San Juan, Fort Lauderdale or Honolulu.
- **Not applied:** the region-run `seed_resolution_cache.json` entries (tracked, left unstaged).
- **Supersedes / superseded by:** —.
- **Source:** scratch `truth_drone/` (`notes.md`, `region_run.py`, `analyse.py`, `final_tables.txt`,
  `final_analysis.txt`, `exp_<city>.json`); `claude/scripts/seed_experiment.py` (regions
  san_juan / fort_lauderdale / honolulu, survey truth keyed on the report ring).

### 2026-10-12 — Survey tier published for Honolulu, Fort Lauderdale and Miami (audit item 8), by overlay on the existing reports
- **Decision:**
  - `use_survey_heights` true in `sites/honolulu.json`, `sites/fort_lauderdale.json`, `sites/miami.json`
    (San Juan since 2026-10-09). The next pipeline run of each city publishes the cached survey p95 as
    tier `survey` (decision "Survey LiDAR heights publish as tier `survey`").
  - `survey_publish.republish(region, heights_json, out_dir)` applies the same `SurveyOverlay` to the
    rows of an existing report and writes a new `heights.json` (`RowRecord` stands in for the
    `BuildingRecord`; a pre-tier report is labelled by `benchmark.label_tiers` first; the OSM data for
    the `start_date` check comes from the OSM cache). It is how the reports below were made: no Street
    View, no GPU, no Google request, and the survey-blind answer of every row is the input's.
  - Reports: `runs/region_reports/<city>_survey_skyline_report/` for the four truth cities (San Juan's
    is the 2026-10-09 scratch `2d/sj` run saved into `runs/`). Rows / cached survey / published survey /
    nested withheld / stale: Honolulu 1,179 / 1,142 / 897 / 245 / 0 (HI_NOAAMauiOahu_2_B20);
    Fort Lauderdale 716 / 713 / 700 / 13 / 0; Miami 27,987 / 352 / 299 / 53 / 0 (usgs_3dep_ept 2019;
    the Miami cache has 1,464 records, 385 without a survey value); San Juan 17,916 / - / 10,726 / 17 / 0.
  - Marks (`10_benchmark --report ... --no-tiles --cached-truth`, `runs/benchmark/2026-10-10_1015` and
    `_1021`): survey-blind headline and every band x tier table (measured and published scopes, review
    and EUBUCCO bands, pipeline metrics) identical to `2026-10-09_1333`: Miami n 168, MAE 5.62 m, tag
    85 / prior 83 by tier; Honolulu single-source n 142, MAE 9.12; Fort Lauderdale n 254, MAE 4.61.
    0 survey rows on nested parts (Miami: 0 of 299; the 53 nested carry `survey_withheld_m`).
  - **Not met:** Miami survey rows within 25 % of `tiles_m`: 81.5 % of the 173 measured rows (MAE
    4.38 m, median 1.16, bias -3.64), 82.3 % of all 299, 85.3 % within max(25 %, 2 m); the mark was
    >= 90 %. By truth status: 94.7 % of the 244 `confirmed` rows, 27 % of the 55 `disputed` ones. The
    big misses are buildings newer than the 2019 survey (The Elser Hotel: 3D Tiles 172 m, survey 9 m,
    tag 160; Miami Plaza 113 / 12 / 115; b19902 113 / 13 / 115; Waldorf Astoria site 67 / 10), then
    low buildings where survey and 3D Tiles disagree by a few metres. Neither stale flag fired: OSM has
    19 `start_date` tags in the Miami bbox (fetched 2026-10-10, `truth/miami.start_dates.json`), none
    in or after 2019; `readings_2x` needs agreeing drone or satellite readings, and these reports have
    none (no drone seeds then, no satellite readings). Honolulu and Fort Lauderdale have no 3D Tiles,
    so the mark is not testable there.
  - Stale-flag counts by reason: 0 `start_date`, 0 `readings_2x` in all four cities.
- **Why:** audit 2026-10-09 item 8, approved by the user. The overlay on the existing rows is exactly
  the pipeline's last step, so the marks test the rule, not a rerun; a full rerun needed the GPU the
  drone-seed reruns were using and would not have reproduced the 2026-10-06 Miami rows anyway.
- **Rejected:** a `tag_2x` stale code (a tag >= 2x above the survey). It would flag 11 of the 299 Miami
  survey rows: 5 disputed (3 where the tag is right: Elser, Miami Plaza, b19902) and 6 confirmed where the
  tag is wrong. Recorded, not shipped: the user keeps the survey over tags; a flag that is wrong 6 of
  11 times needs a second city before it goes on rows.
- **Open:** the `readings_2x` check gets its first real test when the drone-seed reruns (b48a711) run
  with the flag on; `10_benchmark._newest_report` now also finds the `<city>_survey_` folders (they
  start with the region name), which is fine because every score is survey-blind.
- **Source:** scratch `survey_tags/` (logs, tables; copy in `Code/claude/scratch_backup_2026-10-10/survey_tags/`).

### 2026-10-12 — OSM tag bias is a selection effect of banding by truth; split-conformal intervals per tier x band are published, not validated across cities (audit item 9)
- **Decision:**
  - `skyline/intervals.py`: residuals e = ln(published / truth) of the survey-blind published rows
    (tag-only rows too, nested out, `scoring_truth`'s records), banded by the **published** height
    (the only height known at publish time), per tier. `fit` per tier x band with n >= 30: bias term
    b = median(e) and the split-conformal quantile q of |e - b| at 80 % and 90 %, the fit cities
    weighted equally (one unit of weight per city per cell); a cell under 30 falls back to the tier's
    all-band cell; `coverage` / `leave_one_out` test a held-out city. The fit on Miami, Honolulu, Fort
    Lauderdale and San Juan ships in `data/height_intervals.json` (with the per-city bias tables and
    the leave-one-out coverage inside).
  - Rows carry `interval_lo_m` / `interval_hi_m` (80 %: truth in `[pub x exp(-b - q), pub x exp(-b + q)]`)
    and, on `tag` rows, `tag_bias_m` = pub - pub x exp(-b), the tag's expected signed error. **Tags
    are not corrected** (the user keeps tags until they decide). `survey` rows get no interval; tiers
    with no truth-city rows (`single`, `corroborated`) get none yet. `heights.json` gets an `intervals`
    block (level, fit cities, cells, note). Written by `intervals.annotate_report` (`python -m
    city2stl.skyline.intervals apply <heights.json>`); applied to the four `_survey_` reports and to
    `Cartagena_v15_band` (911 of 1,019 rows: 775 prior, 136 tag) and `Cartagena_v16` (873: 737 prior,
    136 tag), each with the note: no truth, pooled fit, not validated.
  - **Tag bias, tag tier, by published band** (n / median ln(pub/truth) / mean bias m / MAE m):

    | city | <15 m | 15-40 m | 40-100 m | >100 m |
    |---|---|---|---|---|
    | Miami | 83 / +0.189 / +0.5 / 2.8 | 42 / +0.110 / +0.8 / 5.0 | 26 / +0.089 / +4.8 / 6.6 | 17 / +0.013 / +3.6 / 4.2 |
    | Honolulu | 360 / -0.050 / -11.3 / 13.8 | 200 / -0.014 / -3.3 / 7.3 | 220 / -0.028 / -0.5 / 5.2 | 59 / +0.011 / +5.8 / 7.6 |
    | Fort Lauderdale | 268 / +0.090 / -0.6 / 3.7 | 97 / +0.079 / -3.1 / 9.3 | 74 / +0.065 / +2.4 / 9.0 | 19 / -0.012 / +8.1 / 16.2 |
    | San Juan | 10,084 / -0.124 / -1.5 / 1.9 | 531 / -0.093 / -2.6 / 3.8 | 106 / -0.048 / -0.9 / 5.3 | - |

    The audit's figures (Honolulu 40-100 m -12.1 m, >100 m -18.4 m; Fort Lauderdale >100 m -23.5 m;
    San Juan 40-100 m -8.7 m) band by *truth*: a truth band collects the tags that under-read into it,
    so its mean bias is negative by construction. By published band the tag's median log bias is
    within +-0.12 in every cell and within +-0.05 from 40 m up (San Juan 40-100 m -0.048); the metre
    biases at 40 m up are +2 to +8 m (tags above the p95: parapets and roof plant). The Honolulu <15 m
    cell (-11.3 m, MAE 13.8) is low tags on tall buildings, not a band-wide offset (median -0.050).
  - **Coverage, leave one city out, nominal 80 %** (n / covered / median ln bias after the band's
    term; pass = 75-85 % and |bias| <= 0.05; nt = n < 30):

    | held out | <15 m | 15-40 m | 40-100 m | >100 m |
    |---|---|---|---|---|
    | Miami | 83 / 88.0 % / +0.237 | 42 / 73.8 % / +0.127 | 26 nt | 17 nt |
    | Honolulu | 360 / 66.4 % / -0.064 | 200 / 70.0 % / -0.026 | 220 / 85.0 % / -0.070 | 59 / 79.7 % / -0.002 **pass** |
    | Fort Lauderdale | 268 / 81.3 % / +0.111 | 97 / 77.3 % / +0.094 | 74 / 73.0 % / +0.056 | 19 nt |
    | San Juan | 10,084 / 80.6 % / -0.222 | 531 / 91.0 % / -0.156 | 106 / 76.4 % / -0.092 | - |

    Nominal 90 %: Miami 92.8 / 90.5 / nt / nt; Honolulu 73.6 / 92.5 / 96.4 / 100; Fort Lauderdale
    96.3 / 81.4 / 89.2 / nt; San Juan 96.0 / 97.7 / 91.5 / -. With the rows pooled instead of the
    cities weighted, 80 % coverage passed 1 band of 11 (San Juan's 10,084 tags set every quantile:
    Miami <15 m 62.7 %, Honolulu 47.2 %); weighting the cities equally passes 1 of 11 on both marks and
    6 of 11 on coverage alone, so it is the fit rule.
  - **Verdict: not passed.** The tag's error is city-specific below 40 m (bias after the band's term
    -0.22 to +0.24): a conformal interval fitted on other cities covers 66-91 % where 75-85 % was
    required. From 40 m up the bias term holds (|bias| <= 0.09) and coverage sits at 73-85 %.
    The pooled intervals are published as the best available error bar, with this table beside them,
    not as a validated one. The `prior` tier's <15 m cell has q = 4.39 (its rows in Honolulu and
    Miami include priors on towers), so its interval says nothing; the prior's intervals need the
    prior's own calibration.
- **Why:** audit 2026-10-09 item 9 (review item 9), approved by the user. Honest error bars need a
  held-out test; the test says a city's own truth is needed for its tags below 40 m.
- **Rejected:** correcting published tags by the bias term (the user's call; the measured bias is also
  city-specific, so a pooled correction would move Miami's tags the wrong way); banding by truth for
  the fit (unknown at publish time, and it is what produced the audit's spurious -12 to -24 m biases);
  per-city intervals for Cartagena (no truth there).
- **Supersedes / superseded by:** —. Review 2026-10-09 item 9; audit §8.3 item 9.
- **Source:** scratch `survey_tags/` (`intervals_fit_weighted.log`, `equal_weight.py`,
  `tag_residuals.json`; copy in `Code/claude/scratch_backup_2026-10-10/survey_tags/`); tests
  `tests/test_skyline_intervals.py`.

### 2026-10-10 — The cadastre floor count bounds a single image reading (plausibility band) in place of "2x the prior"
- **Decision:**
  - `cadastre_heights.py`: `load_band` / `CadastreBand` / `make_band` / `STOREY_M` / `fetch_uses`; site flag
    `use_cadastre_band` (`region_data._load_site_use_cadastre_band`, env `SKYLINE_CADASTRE_BAND` overrides;
    on in `sites/cartagena.json`); `withhold_untagged_street_view(cadastre_band=)`; `region_pdf` loads it.
  - Non-PH match: bound = `floors x storey(use, area band, floors)`, a reading is plausible from x0.5 to
    x1.5. Storey: Puerto Rico LiDAR p95 / OSM `building:levels` (3,546 footprints; model B of scratch
    `storey/results.md`): 1 floor 4.2-4.8 m (< 300 m2 house), 6.7 m (300-1000 m2); 2 floors 3.2-5.2 m; 3+
    floors 3.3-3.9 m. Use class from the R2 table (`yk9q-cw89`, `uso1-3`, cached in `runs/cadastre/`); no
    use: the area band over all classes. The 1-floor > 1000 m2 cells use the 14 footprints there (12.83 m),
    not the model's 5.4 m global fallback (n < 20): a one-floor shed is the tall case.
  - PH match: `(floors +- 2) x 3.5 m` around the count (the match's max floor), lower end not under 0;
    computed, **not applied** (amended 2026-10-12, the user: a PH count is often one unit's floor, not
    the building's, and the cadastre is a cross-check, never a source that overrides a measured height
    there). PH plots keep the 2x rule with `tiers.single_support`, and carry no `cadastre_band`.
  - Non-PH: a `single` outside the band is withheld: tier `prior`, `withheld_reason` `cadastre_band`, the
    reading kept in `single_reading_m`; inside it stays `single` with no satellite support needed.
    Tolerance (2026-10-12): a single outside the band that a validated satellite reading supports
    (`single_support` without the high-rise flag: lean >= 0.7, `ls`, multiview >= 0.3, >= 40 m, within
    1.5x) is withheld only if it exceeds the band's upper end by more than 2x (`tiers.band_tolerated`,
    `BAND_TOLERANCE_FACTOR`); otherwise it is published as `single` with `single_support` and
    `cadastre_band["reason"]` `cadastre_band_tolerated` (`selection_reason` says so). Rows with no
    match keep the 2x rule. Corroborated and tag rows are untouched; the cadastre height is never
    published and no cadastre reading enters the tiers (`load_band` does not register the cadastre rules).
    The row carries `cadastre_band` (`floors`, `ph`, `use`, `storey_m`, `lo_m`, `hi_m`, `inside`, `reason`).
- **Measured** (replay `Cartagena_v15_band` against v14; `_pano/elevated.py` and `footprint_detect.py`, which
  another agent is editing in the same worktree, were loaded at HEAD; `Cartagena_v15_sat`, which only adds
  stored sat_joint readings, reproduces v14 exactly: 0 rows changed):
  - the 7 towers unchanged (202 / 190 / 170 / 188 / 161 / 160 / 156, corroborated); 10 of 10 tag-correct
    readings kept; used drone readings on non-PH 1-3-floor plots 2 (gate <= 2);
  - tiers corroborated / tag / single / prior 34 / 136 / 131 / 718 -> 34 / 136 / 74 / 775; withheld rows
    165 (2x rule) -> 168 `cadastre_band` + 54 `single over 2x prior` (no match);
  - 85 rows change (list: scratch `anchors/band_changed_rows.md`): 71 singles -> prior (41 PH, 30 non-PH),
    14 priors -> single (9 PH; 5 non-PH hotels of 14-15 floors read 45-81 m, inside their 33-108 m band);
  - the Miami drone harness (`seed_experiment.py --region miami --remeasure`) is identical before and
    after except its timing fields (9 differences, all `t_s` / `*_s`).
- **Measured, non-PH only + tolerance** (replay `Cartagena_v16`, 2026-10-12, against v14 and v15_band; 0
  Google requests):
  - gates: the 7 towers unchanged (202 / 190 / 170 / 188 / 161 / 160 / 156); 10 of 10 tag-correct readings
    kept; used drone readings on non-PH 1-3-floor plots 2 (<= 2); b0440 and b0038 publish their satellite
    readings again (55.9 m and 53.8 m, `ls` + lean at conf 1.0, were 23 m / 14 m in v15_band); rows >= 30 m
    on non-PH 1-3-floor plots 3 (v14 24, v15_band 2; b0508 tolerated, b1068 a 32 m prior, b1540 a tag);
  - tiers 34 / 136 / 131 / 718 (v14) and 34 / 136 / 74 / 775 (v15_band) -> 34 / 136 / 112 / 737; withheld
    rows 139 `single over 2x prior` (all PH and unmatched rows) + 45 `cadastre_band`; 0 PH rows carry a band;
  - 29 rows change against v14, all non-PH (list: scratch `band16/band16_changed_rows.md`): 24 singles ->
    prior (22 of them satellite-supported in v14; 1-2-floor plots reading 45-100 m: b0136, b0128, b0774,
    b0788 ...), 5 priors -> single (the 14-15-floor hotels inside their band: b0369, b0075, b0567, b0699,
    b0443). The 56 PH rows v15_band changed are back at their v14 values;
  - tolerated: 2 (b0508, 6 floors, band 14-43 m, lean 57 m at 0.87; b1393, 5 floors, band 10-30 m, lean
    47 m at 0.97). The 22 supported singles the band still withholds exceed their band's upper end by
    2.4-14x (b0934 2.4x and b0988 2.9x nearest the line, both 1-floor plots reading 47-55 m); the 2
    unsupported ones just over it (b0482 7.7 m vs 7.2, b0920 10.2 m vs 10.0) publish a prior of 10 m.
- **What the list shows** (for the user's call; Cartagena has no LiDAR, so none of it is truth):
  - 64 of the 71 withheld singles had been kept by v14 as supported (lean conf >= 0.7, `ls`, multiview
    >= 0.3 or the high-rise flag): the band overrules a validated satellite reading on them.
  - Non-PH: 1- and 2-floor plots read 45-100 m (b0136 100 m on a 1-floor house; b0128 78 m, stereo 79 m, on
    a 2-floor house): the tower-behind cases the band is for. 17 of the 30 publish a prior above their own band.
  - PH is the weak side: the count is often a unit's. Of the 41 singles withheld, 26 publish a prior below
    the band's lower end (b0440 and b0038, 10 floors, lean + shadow `ls` 54-56 m at conf 1.0, band 28-42 m,
    now 23 m and 14 m; b0112, 7 floors, lean 67 m at 0.9 and stereo 65 m; b0076, 1 floor, lean 66 m at 1.0).
    A PH reading above the band may be a taller building as well as a wrong reading.
  - Options not taken (not asked): let a validated satellite reading override the band; publish the band's
    nearest bound instead of the T41 prior (the cadastre never publishes).
- **Supersedes / superseded by:** the "2x the prior" rule of 2026-10-08 on non-PH plots with a match (PH
  plots keep it since 2026-10-12); the `use_cadastre_heights` path (cadastre heights replacing the prior,
  tier readings) stays off.
- **Source:** scratch `anchors/` (`cart_v15_band.log`, `gate15.py`, `gate15_band.json`,
  `band_changed_rows.md`, `tier_map_v15.py`); tier map
  `runs/region_reports/Cartagena_v15_band_skyline_report/tiers_map.png` (red edge: withheld by the band,
  green: kept inside it; in Bocagrande most orange singles turn grey). v16: scratch `band16/`
  (`run_cart_v16.py`, `cart_v16.log`, `gate16.py`, `gate16.json`, `band16_changed_rows.md`,
  `tier_map_v16.py`; copy in `Code/claude/scratch_backup_2026-10-10/band16/`); tier map
  `runs/region_reports/Cartagena_v16_skyline_report/tiers_map.png` (Bocagrande's orange PH singles are
  back; the red edges are 1-2-floor plots on the mainland and Manga; cyan: tolerated).
- **Tests:** `tests/test_skyline_cadastre_heights.py` (band values, PH band, outside / inside, never
  publishes; PH ignored, tolerated within 2x the upper end), `tests/test_skyline_tiers.py` (`band_tolerated`).

### 2026-10-10 — sat_joint: lean and shadow fitted together with per-scene vectors from anchors; refused on its pre-registered marks
- **Decision:**
  - Code kept, **off**: `height/satellite/joint.py` (`lean_evidence`, `shadow_evidence`, `joint_reading`,
    `fit_kappas`, `JointModel`, `CONF_TABLE`, `C_PUB` = 1.01); profile outputs in `measure.shadow_height` /
    `lean_height(profile=True)` and `measure_multi(profiles=True, sweep=False)`; reading method `sat_joint`
    (`readings.from_measurements(joint=)`); `20_satellite_heights.py --anchors FILE --joint` (vectors fitted
    on buildings of known height, not OSM tags; `--out` + `--merge-joint` keep the other readings);
    `tiers.SUPPORT_MIN_CONF["sat_joint"]` = 1.01. Nothing reads a sat_joint reading.
  - **The low-lean cap stays** (`readings.LOW_LEAN_MAX_CONF` 0.3): no lean height band x conf_peak band
    reaches a 0.8 share within max(25 %, 2 m) on Honolulu, San Juan and Fort Lauderdale (below).
- **Method** (pre-registered in scratch `anchors/notes.md` before any run):
  - Anchors: truth 40-175 m, area > 250 m2, the OSM outline is the tower (tag within 25 % of truth, not
    nested in a taller truth footprint): Honolulu 65, San Juan 153, Fort Lauderdale 138 (Cartagena: the 7
    tagged towers and 6 drone + drone corroborated rows, 13). Per scene `fit_lean` + `solve_sun` on them,
    then a scale `kappa` for the lean shift and the shadow length (the evidence at the anchors' truth heights
    is maximised). Fitting on all 538 Honolulu truth towers >= 40 m (podium slices read the tower's p95)
    gave lean k 0.05 for the Waikiki scene (OSM-tag fit 0.43): hence the outline rule.
  - `sat_joint`: every scene's shadow profile and lean profile scored at every height on a 0.5 m grid, scenes
    averaged within a kind, `J = Zl + Zs`, argmax, made only when both kinds support it (>= 0.3). Confidence
    = share within tol of the training cities' readings in the (height band, J bucket) cell.
  - Measured untagged (60 m windows): a 35 % hash sample of truth < 10 m (weighted back) and all truth >= 10 m:
    Honolulu 1,805 footprints (6 Wayback scenes), San Juan 4,058 (6), Fort Lauderdale 1,844 (5 aerial
    orthos, leans 0.07-0.22 m/m).
- **Low-lean cap table** (reference-scene lean by its peak confidence `conf_peak`; raw n / share within tol,
  low-truth rows weighted; the 3 cities):

  | lean band | .15-.3 | .3-.5 | .5-.7 | .7-.9 | .9-1 |
  |---|---|---|---|---|---|
  | < 15 m | 730 / 0.43 | 679 / 0.41 | 254 / 0.35 | 158 / 0.33 | 411 / 0.33 |
  | 15-40 m | 378 / 0.05 | 341 / 0.04 | 266 / 0.05 | 168 / 0.05 | 282 / 0.11 |

  The best cell is Honolulu < 15 m at conf 0.7-0.9, 0.43 (n 51); 15-40 m is 0.00-0.21 in every city (Chicago,
  2026-10-08 validation, 0.00-0.12). Threshold per band (smallest `conf_peak` edge with share >= 0.8 and
  n >= 20): none, in every leave-one-city-out fold: the cap stays at 0.3 for both bands.
- **Pass marks** (the user's; scored on all readings, because no confidence reaches a 0.8 share, so `c_pub`
  does not exist in any fold):
  - A-T1, 10-40 m truth, leave-one-city-out, sat_joint MAE <= 5 m and within tol >= 0.6: **fail**.
    Honolulu 120 readings of 641 footprints (19 %): MAE 8.91 m, within 0.392. San Juan 362 of 1,591 (23 %):
    MAE 5.17 m, within 0.492. (Fort Lauderdale 106 of 615: MAE 6.45 m, within 0.651; not a mark.) The current
    readings on the same footprints: lean within 0.253 / 0.184 / 0.174, shadow 0.340 / 0.361 / 0.484.
  - A-T2, Honolulu towers >= 100 m, no loss against the current lean within tol (220 m windows, cross-fit
    anchors): **fail**. sat_joint 4 hits of 13 readings, lean 34 hits of 85 (129 towers).
  - A-T3, Cartagena replay `Cartagena_v15_sat` (13 anchors; 118 of 3,018 footprints get a sat_joint reading,
    6 of them >= 40 m, conf <= 0.39): the 7 towers unchanged; used drone readings on non-PH 1-3-floor plots 2;
    tiers 34 / 136 / 131 / 718 as v14, 0 rows changed; withheld rows gaining support: 0 (of 165, none has a
    sat_joint >= 40 m within 1.5x of its single, at any confidence). It passes because sat_joint is off.
- **Why it fails:** each profile alone is poor under 40 m (Honolulu 15-40 m: the per-kind argmax is within tol
  of truth for 0.24-0.29 of footprints, unbiased after the kappas, errors two-sided with the 25th-75th
  percentile at 0.4-1.3x truth). The joint fit lifts it (15-40 m within tol: San Juan 0.65, Honolulu 0.37) but
  J, the per-kind evidence, the argmax agreement and the scene count separate right from wrong readings in
  none of the cities. The lean kappas are 0.8-1.0 (mostly 0.8-0.9): `fit_lean`'s shift per metre of height is
  about 15 % high.
- **Open:** a lean profile that sees the facade base better (the base shift), occlusion-aware shadows; a
  second truth source for the footprints a profile cannot read.
- **Source:** scratch `anchors/` (`notes.md`, `run_city.py`, `eval_joint.py`, `loco.py`, `cap_table.py`,
  `eval_towers.py`, outputs `*.txt`); copy in `Code/claude/scratch_backup_2026-10-10/anchors/`.
- **Tests:** `tests/test_satellite_heights.py` (profiles, joint reading, kappas, confidence table),
  `tests/test_skyline_tiers.py` (sat_joint support wired but off).

### 2026-10-10 — Cross-sphere triangulation of roof corners fails its Miami marks on yield LiDAR heights publish as tier `survey` (F-SKY26 2d), flagged where stale (rejected; code kept off)
- **Decision:** `_pano/triangulate.py` is not turned on (`TRIANGULATE = False`, env
  `SKYLINE_TRIANGULATE`); no Cartagena replay (v15) was run, as the pre-registration says. The code,
  its hooks and `seed_experiment.py --tri` stay for a denser sphere set.
  - Method (fixed before the run, `tri/notes.md`): per sphere and measured footprint, the two limb
    corners (`seed_corners`): the OSM vertex at the lowest / highest bearing only *names* the corner;
    the ray is the image's: the column is the outer boundary of the footprint's MobileSAM instance in
    a band of rows under the measured top (or a depth step >= 1.15 there), within +-max(8 px, 25 %
    of the width) of the projected corner, the elevation the top of the mask 2-5 px inside it. Two
    spheres of different camera groups that name the same vertex: nearest approach of the rays
    (`intersect`), height = z of the midpoint, uncertainty = the miss. Gates: baseline >= 200 m, line
    angle 10-170 deg, miss <= 15 m, both ranges >= 20 m, midpoint within 40 m of the named vertex.
    A footprint's reading = median of its passing corners (`triangulate`, kind `triangulated`).
  - Wired but off: `ElevatedSeed.corners` (`elevated.seed_corners`, after the cached measurement,
    stage keys unchanged), `elevated.triangulated`, `elevated_estimates` joins the fusion as pseudo-seed
    `tri` (`footprint_detect.fuse_heights` accepts kind `triangulated`) and publishes it under its
    first sphere's seed name.
  - **Independence (decided before the run):** a triangulated reading contains both spheres'
    elevations and bearings, so it is not independent of a single from either; it is of a third
    camera's and of satellite readings. `fuse_heights` counts it as a source only when its camera
    groups are disjoint from the drone groups kept; `elevated_estimates` emits it only then, so
    `tiers.independent` (drone pairs of different seeds) needs no change: the emitted row is a plain
    view row of its first sphere. `seed_3` and `seed_3_sphere` are one camera (`camera_group`): their
    position fits differ by 220 m, which is not a baseline.
- **Measured** (Miami saved states seed_2, seed_3, seed_3_sphere, seed_4, seed_4_sphere_p8m18;
  `seed_experiment.py --region miami --remeasure --tri`; LiDAR `confirmed`, truth >= 20 m; the
  Martinez sphere is 4.9-7.4 km from the others and pairs with nothing):

  | pass mark (pre-registered) | result |
  |---|---|
  | MAE of triangulated < MAE of the best single on the same footprints | n = 1: 26.5 m against 27.3 m (anchor), both wrong: YVE Hotel Miami, tri 33.6 (miss 1.8 m), single 32.8, LiDAR 60.1 |
  | within max(25 %, 2 m) >= 0.80 | **0 of 1: fails** |
  | the 4 tower-behind cases never a wrong height | no wrong height; untestable: Kaseya Center is read by one camera only (138 m untrusted, LiDAR 43), The Loft 1, The Guild Downtown X Miami and Brickell Key I have no reading in the current states |

  - Funnel: 362 limb corners observed; not observed: instance merged into a block 350, edge too
    far from the corner 31, no top 35, and 168 footprints with no start inside the mask or off the
    pano and 69 with too small a band (both corners lost);
    232 footprints have a corner, 10 have corners from two camera groups (9 with truth); only 33
    footprints are measured by two camera groups at all (11 with truth >= 20 m, 6 trusted by both);
    12 camera-pair x footprint combinations, 3 name the same limb vertex, 1 passes the gates (2
    miss > 15 m, 1 angle). With every gate off the same 3 give 33.6 (LiDAR 60.1), 27.8 / 26.3 (no
    truth) and 106.8 at a 170 deg line angle (LiDAR 126.8, miss 49 m).
  - Geometry works: a rendered tower seen from two cameras comes back within 4 m
    (`tests/test_skyline_triangulate.py`), and the one Miami pair agrees with the single reading.
- **Yield diagnostic on Cartagena** (corner level on the saved seeds 1/4/5/6/7; not the v15 replay
  and not a port): 398 footprints are measured by two or more seeds; 518 corner pairings, 364 name
  different limb vertices, 154 the same one, of which 21 pass (90 miss > 15 m, 42 angle < 10 deg, 1
  vertex); 18 footprints get a reading (4.5 % of the 398; 13 of the 362 not already trusted by two
  seeds), none tagged. 15 have a trusted single and 14 agree with one within 25 %; 3 have none
  (b0191 61 m, b0162 99 m, b0781 54 m). So at most 18 of the 142 withheld rows could gain a
  triangulated reading.
- **Why it fails:** a limb corner is view-dependent. The limb flips to the next vertex when the line
  of sight crosses an edge direction, so two spheres 10 deg apart name the same one in 3 of 12 Miami
  and 30 % of the Cartagena pairings. About half of the attempted corners are never observed (a block merged
  into one instance, the corner off the pano), and the camera fits are not good enough for the rest
  (the same camera's two fits are 220 m apart; 90 of Cartagena's 154 same-vertex pairs miss by more
  than 15 m). The Miami cameras mostly look in different directions: 11 pairable footprints with
  truth cannot support an accuracy mark.
- **Rejected:** requiring nothing of the vertex (rays of different limbs of one footprint do not
  meet); using the OSM vertex position for the distance (that is the single reading again, with no
  independence); loosening the gates (the same 3 pairings, 2 of them wrong or without truth).
- **Open:** non-limb vertical edges (facade corners) as observations, which would let any two
  spheres share a corner; a sphere set with several cameras on one side of each target; a second
  truth city with overlapping spheres (Chicago has two cameras, far apart).
- **Source:** scratch `tri/` (notes.md = pre-registration; `out/` the runs and corner dumps;
  diag1-3.py); harness flags `--tri`, `--tri-gates`, `--tri-dump`; tests
  `tests/test_skyline_triangulate.py`.

### 2026-10-09 — Survey LiDAR heights publish as tier `survey` (F-SKY26 2d), flagged where stale
- **Decision:**
  - `survey_publish.py`, site flag `use_survey_heights` (env `SKYLINE_SURVEY_HEIGHTS` overrides; on
    for `san_juan` only). `region_pdf` reads the cached survey (`runs/survey/<region>.json`, stat 2)
    and never measures. A footprint with a cached p95 publishes it: `tier` `survey`, `effective_height_m`
    the p95, and on the row `survey_provider` (the lidar project, e.g. `USGS_LPC_PR_PRVI_E_2018`; the
    truth cache's `survey_project`, new survey records carry `project`), `survey_source` (provider key),
    `survey_year`, `survey_stat` `p95`, `survey_roof_m` (stored percentiles), `survey_cells`,
    `selection_reason`. Survey outranks tags and image readings. The image readings and the
    pre-survey answer stay on the row (`per_seed_median_m`, `satellite`, `no_survey_*`).
  - **Stale flags** keep tier `survey` with `survey_stale`, `survey_stale_codes`, `survey_stale_reason`
    (`verified` False, shown in the HTML hover and table note):
    - `start_date`: OSM `start_date` in or after the survey's last year (the benchmark's
      `temporal_flag` rule, not "strictly older"; the start dates come from the cached
      `runs/benchmark/truth/<region>.start_dates.json`, the OSM id by centroid);
    - `readings_2x`: the drone / satellite readings agree (`tiers.agree`; or the row was
      `corroborated`) but sit more than 2x from the survey, either way.
  - **Nested** footprints (`benchmark.nested_keys` over the published set) do not publish the survey
    value: they keep their tier and height and carry `survey_withheld_reason` / `survey_withheld_m`.
  - `heights.json` gets a `survey` block (`n_survey`, `n_stale`, `stale_by_code`, `n_nested_withheld`,
    `providers`); schema stays 3 (additive). Tag-only rows get the survey too (`_write_heights_json`
    and, through `survey_publish.set_active`, the HTML / PDF counts).
  - **Benchmark:** `survey_blind` puts a `survey` row back to its `no_survey_*` height, source and tier
    before anything is scored; a survey row without them is dropped, never scored on the survey.
    `10_benchmark.score_report` scores the headline, `tiers`, `bench` and the benchmark page on those
    rows; `survey_rows` scores survey rows against `tiles_m` only, stale ones counted apart
    (`n_stale`). Tests: `tests/test_skyline_survey_publish.py`.
  - **Wording:** the reports said "N of M verified" while counting corroborated rows. Now "N survey,
    K corroborated of M" (HTML, PDF heights page, the app's reports pane); `n_verified` in the
    heights API keeps its name.
- **Why:** the user approved 2d. A survey height is the best reading of a footprint, but it shows the
  building as it stood on the flight day: a newer building, or an empty lot built since, is the
  failure the flags catch. The survey sees only the top of a stack, so a podium or part under a
  taller footprint would publish the tower's height.
- **Rejected:** scoring survey rows on the survey (circular); fetching the survey at run time (the
  run stays offline, like satellite readings); publishing a stale-flagged row as its pre-survey tier
  (the user asked for the flag on the row, not a silent fallback); flagging only a start_date strictly
  after the year (the benchmark's rule includes the survey year).
- **Open:** the flag is on for San Juan only; the other cities' caches are there. The stale check
  for `readings_2x` needs readings, so it fires only where drone or satellite readings exist (none in
  San Juan's report). Rows whose footprint key differs (survey cached under a full-precision key)
  are not matched.
- **Source:** commit `9f9e611`; scratch `2d/` (San Juan report `2d/sj/`).

### 2026-10-09 — The `verified_2` tier is renamed `corroborated`
- **Decision:**
  - Tier `verified_2` is now `corroborated` in the tier value, `tier_counts`, the labels
    ("Corroborated"), the legends, the PDF / HTML tables and the benchmark. Meaning unchanged: two
    independent readings agree (or an OSM height tag one independent reading agrees with).
  - `heights.json` `schema_version` 3 (was 2). Schema 1-2 files stay readable:
    `_core/tiers.py::upgrade_tiers` / `canonical_tier` map `verified_2` on read (row `tier` and
    `no_survey_tier`, `tier_counts` keys); `benchmark.load_report`, `score_by_tier`,
    `tier_display.row_tier` / `tier_counts` and so the `/reports` heights API use them.
  - Kept: function and field names (`verified_pair`, `VERIFIED_TIERS`, the row field `verified`,
    "N of M verified" in the reports). Not renamed because they are internal, or a wider change
    than the tier name.
- **Why:** user, 2026-10-09: "verified" is for survey or human review; two independent methods
  agreeing is "corroborated". The benchmark already called the failure rate false-corroboration.
- **Rejected:** rewriting old `heights.json` files (read-time mapping keeps them as the run wrote
  them); renaming `verified_pair` (cheap but churns the call sites for no reader gain).
- **Historical entries** below keep the name they were written with.
- **Source:** commit `c3a3fe9`; `tests/test_skyline_tiers.py::test_old_verified_2_reads_as_corroborated`.

### 2026-10-09 — Cartagena runs footprint ownership with the cadastre occluders
- **Decision:**
  - Site flags `use_ownership` and `use_cadastre_occluders`, both true in `sites/cartagena.json`.
    `region_pdf` sets them through `ownership.set_site_flags`; the env vars `SKYLINE_OWNERSHIP` and
    `SKYLINE_CADASTRE_OCCLUDERS` still override. The module defaults stay off.
- **Why:** only with the cadastre occluders does Cartagena meet the low-rise gate of "A drone
  reading's top belongs to the footprint line of sight gives it" (used drone readings on non-PH
  1-3-floor plots, gate <= 10).
- **Measured** (replay `Cartagena_v14` against `Cartagena_v13_sat`):
  - the 7 towers unchanged; 10 of 10 tag-correct readings kept;
  - used drone readings on non-PH 1-3-floor plots 53 -> 2; b0490 now 11 m;
  - tiers verified_2 (now `corroborated`) / tag / single / prior 38 / 136 / 127 / 715 -> 34 / 136 / 131 / 718 (v14).
- **Supersedes / superseded by:** the **Default** bullet of the ownership entry, for Cartagena.
- **Source:** commit `86dca7a`; gate script scratch `own2/gate14.py`; tier map
  `runs/region_reports/Cartagena_v14_skyline_report/tiers_map.png`.

### 2026-10-09 — The tower-behind veto counts a low lean with peak confidence >= 0.7 as satellite low
- **Decision:**
  - `_pano/elevated.py` (`_sat_max`, the veto): a low lean whose uncapped peak confidence
    (`extra.conf_peak`) is >= 0.7 counts as "satellite low". Veto only: fusion and the tiers keep
    the calibrated 0.3.
  - Cartagena's `readings.json` re-measured with all 7 scenes' outlines (sha be8da4d5 -> 73515f8a,
    2,922 footprints).
- **Why:** closes the open question of "Stored satellite readings are calibrated on load".
  Honolulu (untagged, 60 m window, `hon2` before / after): leans under 40 m with peak confidence
  >= 0.7: 580 / 350; of those on truth under 40 m: 533 (91.9 %) / 318 (90.9 %); truth >= 40 m:
  47 / 32; truth >= 100 m: 8 / 5 of the 126 towers.
- **Measured** (`Cartagena_v13_sat`, `SKYLINE_OWNERSHIP=0`, against `v12_support`):
  - tiers verified_2 / tag / single / prior 38 / 136 / 127 / 715 against 42 / 134 / 125 / 715;
  - the 7 towers unchanged; 10 of 10 tag-correct readings kept;
  - used drone readings on non-PH 1-3-floor plots 53 (gate <= 50 fails);
  - b0490 100 m -> 10.9 m prior (verified_2 -> prior); b1285 55 -> 10 m (verified_2 -> prior).
- **Supersedes / superseded by:** the **Open** bullet of "Stored satellite readings are calibrated
  on load".
- **Source:** commit `978eee1`; scratch `sat/`.

### 2026-10-09 — Google Open Buildings 2.5D does not replace the T41 prior for low-rises (refused on the pre-registered test)
- **Decision:**
  - `city2stl/skyline/lowrise_prior.py` stays off (`STATUS = "refused"`, site flag
    `use_google_2p5d_prior` unset everywhere).
  - The reader `city2stl/height/providers/google_ob25d.py` is kept for later tests. It is not in
    `_REGISTRY`.
- **Why:** the test was pre-registered (scratch `ob25/notes.md`).
  - Truth: survey p95 on untagged footprints of San Juan (2,221 rows), Mayagüez (2,285, from the 5
    densest 500 m tiles) and Charlotte Amalie (1,471, from 6 tiles); USGS PR/VI 2018.
  - Method: leave-one-city-out; p90 at presence >= 0.7 (chosen by every fold), plus a median
    offset per band of the raw value.
  - Under 15 m, the composite beats T41 in every held-out city and is nearly unbiased:

    | City | Composite MAE | T41 MAE | Composite bias | T41 bias |
    |---|---|---|---|---|
    | San Juan | 2.03 | 3.16 | +0.62 | +2.13 |
    | Mayagüez | 1.91 | 2.46 | +0.06 | +1.36 |
    | Charlotte Amalie | 1.89 | 2.01 | +0.05 | +0.78 |

    Within max(25 %, 2 m): 0.63 / 0.66 / 0.64 against 0.40 / 0.48 / 0.60.
  - It fails four of the five marks (P1, P3, P4, P5):
    - P1, the 1 m margin, in 2 of 3 cities (gain 0.55 in Mayagüez, 0.12 in Charlotte Amalie);
    - P3, the band-2 offset span <= 1 m: 1.89 m (San Juan fold fitted on 27 rows; band 1 span 0.19 m);
    - P4, the Cartagena gate: 38 of 286 prior rows within max(25 % of the cadastre, 2 m) of the
      non-PH cadastre, against >= 143 required (16 today; MAE 7.73 -> 5.05 m; `tiers.agree`: 16 ->
      29; with the 2 m floor 16 -> 36);
    - P5, the 7 OSM tags on non-PH plots within tol: 6 of 7 (b2663: tag 3.0 m, Google corrected
      5.15 m).
  - P2 passes: 15-20 m no worse than T41 (San Juan 6.16 vs 7.01, n 83; Mayagüez 6.62 vs 6.84, n 30;
    Charlotte Amalie n 11 not counted).
  - Offsets (m) B1 / B2: all three cities +0.15 / +0.74; San Juan held out +0.27 / +1.84; Mayagüez
    held out +0.09 / -0.05; Charlotte Amalie held out +0.08 / +0.78.
  - Cartagena, post hoc: on "1-floor" cadastre plots (n 185, median footprint 463 m2) Google p90
    reads 8.5 m against the cadastre's 4.0 m; on 2-floor plots 9.0 against 7.0. No Cartagena survey
    can say which source is wrong.
- **Rejected:**
  - Lowering the margin after seeing the numbers.
  - Fitting the correction on Cartagena's cadastre, which is not survey truth.
  - Using Google above 20 m: its heights are capped at 100 m.
- **Data:** 275,426,892 bytes in 248 logged range requests (5 headers of 64 KB, plus the city
  windows of the 2023 layer), saved in `~/.cache/ob25d/2023/`. The tiles are tiled COGs: 512 px
  blocks, DEFLATE with predictor 3, band-separate planes, 14 overviews.
- **Next evidence that could reopen it:** Medellín LiDAR (review item 14), a Colombian survey; a
  field check of 20-30 "1-floor" Cartagena plots over 300 m2.
- **Supersedes / superseded by:** —. Review 2026-10-09 item 4.
- **Source:** commit `a220104`; scratch `ob25/` (`notes.md`: the pre-registration and one deviation
  made before scoring, smaller truth areas; `results.md`; `fetch_log.md`).

### 2026-10-09 — A drone reading's top belongs to the footprint line of sight gives it; only tags and cadastre counts take a top away
- **Decision:** `_pano/ownership.py` decides from geometry which footprint owns each trusted
  drone reading's top; `_pano/elevated.py::seed_ownership` runs it after the cached measurement
  (stage-cache keys unchanged) and `elevated_estimates` applies it (`not_owned`,
  `_mark_not_owner`, `recredited`). Switch `ownership.OWNERSHIP` / env `SKYLINE_OWNERSHIP`
  (default: see the end of this entry).
  - Upper plausible height (`upper_heights`): as an occluder the OSM tag; else, with the cadastre
    occluders on (`CADASTRE_OCCLUDERS` / `SKYLINE_CADASTRE_OCCLUDERS`), the non-PH cadastre
    floors x 1.5 x 4.13 m; else the untagged prior's p90 (`prior_upper`: quantile GBM, the
    `untagged_prior` features and training set, scored city left out). As the owner of its own
    top, tag + 25 % (`TAG_MARGIN`). Never a measured top.
  - Claims, near to far (`Scene.owners`): every footprint claims its silhouette at that height
    (`claim_top`). Per core column the top pixel belongs to the first footprint whose claim holds
    it: nearer (4 px of the spin pano, scaled, inside its claimed top and its left/right edges:
    the isovist fails), own, farther (the ray clears the own claim) or none. The footprint keeps
    the top with a third of its columns (`SELF_MIN`, as `measure_footprints`).
  - Rule (`not_owned`): the walk counts, besides the footprint itself, only footprints with a
    hard bound (tag, cadastre count). `owner_nearer`, `owner_farther` and `owner_above_tag` (above
    its own tag + 25 % or cadastre bound, nothing tagged explains it) leave fusion. A prior p90
    claim never takes a top away.
  - Re-credit (`Scene.recredit`): a reading that left, whose top a farther *untagged* footprint's
    claim holds (every claim counted), joins as that footprint's reading when `trusted` accepts it
    with its base hidden (visible share above the nearer claims). Line of sight picks it, so the
    2026-10-07 refusal (owner chosen by agreeing evidence) does not apply; never to a tagged
    footprint (its tag publishes; agreeing re-credits would confirm it by construction).
- **Why:** calibrated depth caught 0 of the 4 known tower-behind cases (depth study 2026-10-09);
  CBHE and OpenFACADES decide ownership by geometry (review §2.6).
- **Measured** (pre-registered marks, review §3 item 3; Miami drone states, LiDAR `confirmed`,
  truth >= 20 m; wrong = more than 25 % off):

  | set | trusted with truth | wrong caught (>= 50 %) | right flagged (<= 5 %) | within 25 %: before -> kept (>= 0.80) |
  |---|---|---|---|---|
  | current pipeline, re-measured | 64 | 20 of 28 (71 %) | 1 of 36 (2.8 %) | 0.56 -> 0.81 (n 43) |
  | older reading set (F-SKY26:111, saved states) | 90 | 42 of 51 (82 %) | 1 of 39 (2.6 %) | 0.43 -> 0.81 (n 47) |

  - The 4 known cases are caught: Kaseya Center (read 138 m, LiDAR 43; the tagged 900 Biscayne
    Bay behind owns the top), The Loft 1 (177 / 82), The Guild (183 / 106), Brickell Key I
    (157 / 68) (above their tags, nothing tagged explains them). All four are tagged; untagged,
    with no tagged footprint behind, they would be kept.
  - The false flag: Venetia (read 83 m, LiDAR 99; prior p90 30 m), whose top the tagged b21280
    (115 m tag, 107 LiDAR) behind it claims.
  - Miami has almost every tower tagged: the rule with or without the p90 claims scores the same.
  - Miami fusion (`seed_experiment.py --region miami --remeasure --own`): 95 of 243 trusted
    readings left out (55 farther, 29 above tag, 11 nearer); fused OSM-tag error within 25 %
    0.76 (n 46) -> 0.85 (n 41); 9 re-credits to untagged footprints, none with LiDAR truth.
  - Cost: 6-13 s a seed on CPU; 25-44 s with the cadastre occluders.
- **Cartagena** (region replay from the stage cache; treatment and control on the same satellite
  readings, `readings.json` sha 79252747, rebuilt by the satellite agent at 10:33):

  | | v11 | control (ownership off) | ownership | ownership + cadastre occluders |
  |---|---|---|---|---|
  | verified_2 / tag / single / prior | 43 / 133 / 124 / 716 | 42 / 134 / 124 / 716 | 40 / 134 / 127 / 716 | 37 / 134 / 131 / 717 |
  | the 7 towers (Estelar 202, Allure 190, Gran Bay 170, Portomarine 188, Nautica 161, Ravello 160, Palmetto 156) | v11 values | unchanged | unchanged | unchanged |
  | tag-correct used readings kept (of 10) | 10 | 10 | 10 | 10 |
  | tag-wrong used readings (b0303 90/6, b0619 131/10, b0639 154/10, ...) | 3 | 4 | 0 | 0 |
  | used readings on non-PH 1-3-floor plots (gate: <= 10) | 50 | 54 | 52 | **2** |
  | trusted readings left out by ownership / re-credits kept | - | - | 45 / 3 | 175 / 19 |
  | verified_2 on non-PH 1-floor plots | b0788, b1761, b0806 | b0788, b1761 | b0788, b1761 | none |

  - Without the cadastre the gate's low-rise mark fails: the 1-3-floor plots are untagged, and
    their prior p90 does not take a top away.
  - Lost corroborations (ownership vs control): b0411 (70 m, drone + stereo) and b0339 (66 m),
    PH plots of 21 and 16 floors, whose tops a tagged tower behind claims: the Venetia case.
  - Re-credits, cadastre off: 3 untagged footprints gain a reading (b0369 81 m, b0271 89-91 m,
    b0458 99 m); none is corroborated; all three publish their prior (a single over 2x it).
  - With the cadastre occluders: 12 footprints behind take a re-credited reading (11 new to that
    seed), 2 corroborated: b0383 (19-floor non-PH plot) becomes verified_2 at 96 m from seeds 5
    and 6 (it had no row in v11); b0896 stays verified_2 (126 -> 130 m). Lost corroborations
    against the control: b0788 (55 m, 1 floor), b1761 (68/84 m, 1 floor), b0490 (91/110 m,
    2 floors) — the review's suspect low-rise verified_2 rows — and b0411, b0339, b1505 (PH,
    21/16/15 floors, 66-70 m: likely right).
  - Tier map: `runs/region_reports/Cartagena_v12_own_cad_skyline_report/tiers_map*.png` (and
    `Cartagena_v12_own_skyline_report/`): v11 and v12 look alike; a few green Bocagrande
    footprints turn single or prior, the labelled towers do not move.
- **Rejected** (measured; see Rejected hypotheses): the own-corner rule; the claims as the run's
  start (`occ[]`); untagged (prior p90) claims as ownership evidence.
- **Limits:** one truth city (the Chicago states have no `confirmed` truth on their 8 trusted
  readings; Honolulu / Fort Lauderdale spheres not captured), so review §4.5's second city is
  open. The rule was set while looking at the Miami readings (iterations in scratch `own/notes.md`);
  the older reading set and Cartagena are the checks. Untagged towers with a low p90 in front of a
  tagged one lose their reading (Venetia, b0411, b0339).
- **Default:** module off (`OWNERSHIP = False`, `CADASTRE_OCCLUDERS = False`): it passes the Miami
  marks, but review §4.5 asks for two truth cities, and Cartagena meets the low-rise gate only
  with the cadastre occluders. Env `SKYLINE_OWNERSHIP=1` (and `SKYLINE_CADASTRE_OCCLUDERS=1`) turn
  it on for a run. **Cartagena has both on through its site flags since `86dca7a`** (entry
  "Cartagena runs footprint ownership with the cadastre occluders", v14).
- **Source:** commit `b2fb7d6`; harness `Code/claude/scripts/seed_experiment.py --own [--own-cad]`;
  review `Code/claude/research/panorama-pipeline-review-2026-10-09.md` §3 item 3.

### 2026-10-09 — A tall satellite reading supports a single within 1.5x, not the strict `agree`
- **Decision:** `_core/tiers.py::SUPPORT_RATIO = 1.5`. `single_support` keeps a single over 2x the
  prior when a validated satellite reading (`validated_satellite`: lean >= 0.7, multiview >= 0.3,
  `ls`; >= 40 m) is within 1.5x of it (`|ln a/b| <= ln 1.5`). Corroboration (`verified_pair`,
  `tag_witness`, the tag check, `disputed_by`) keeps `agree` (ratio 1.25).
- **Why:** the agreement change (next entry but one) made `agree` stricter, and `single_support`
  used it: b0691 (drone 55.9 + lean 72.5, conf 1.0, ratio 1.30) lost support and published an
  18.9 m prior. The user chose a looser ratio for support only.
- **Evidence:** labelled stereo singles (no drone building has a validated satellite reading in
  `buildings.json`) supported by a validated reading: ratio 1.25 n 126, 84.9 % right; 1.33 n 135,
  84.4 %; 1.5 n 147, 83.7 % (the 21 added by 1.5: 16 right).
- **Not changed:** the validation bar. b0806's real lean is 48.4 m at conf 0.37, so it is not
  validated and still publishes the 15.6 m prior.
- **Measured:** Cartagena `v12_support` (GPU, `SKYLINE_OWNERSHIP=0`) against `v12_tiers`: one row
  changes, b0691 prior 18.9 -> single 55.9 m (lean 72.5); tiers verified_2 42 / tag 134 / single
  125 / prior 715; the 7 towers unchanged.
- **Supersedes / superseded by:** settles the "To decide" item of the agreement entry (b0691, b0806).
- **Source:** commits `45f5c41`, `8e26925`; scratch `support/`.

### 2026-10-09 — The benchmark scores by height band and tier, survey-only cities score, and roof percentiles are stored
- **Decision:**
  - Beside the unchanged headline (`10_benchmark` table, confirmed truth, survey-blind height),
    every scored report carries `bench` (`benchmark.py::bench_tables`), twice: `measured` (the
    rows the run measured, the headline's set) and `published` (every published row, tag-only
    rows too: a region report lists every OSM-tagged building as a `measured: False` row).
  - Bands: the review's <15 / 15-40 / 40-100 / >100 m (`REVIEW_BANDS`) and EUBUCCO's 0-5 / 5-10 /
    10-20 / 20+ (`EUBUCCO_BANDS`). Per band x tier and per band x method x camera-distance band
    (<500 / 500-1,000 / >1,000 m, drone and street seeds; `DISTANCE_BANDS`): n, MAE, median AE,
    signed bias, P90 AE, tol (within max(25 %, 2 m) of truth: `TOL_REL`, `TOL_ABS_M`), shares off
    by > 5 m and > 10 m (`band_errors`).
  - Pipeline metrics (`pipeline_metrics`): coverage (non-prior published rows, of rows and of all
    footprints), false-corroborated rate (`verified_2` / `corroborated` rows off by more than
    tol), withhold precision (withheld readings that were wrong) and recall (wrong readings that
    were withheld).
  - Single-source truth: a region whose footprints have no 3D Tiles reading (San Juan, Honolulu,
    Fort Lauderdale: monthly cap) scores its `survey_only` records (`truth_mode`,
    `scoring_truth`), printed in a table of its own; its headline row stays n = 0 as before.
  - Truth statistics: each survey record stores p50 / p70 / p90 / p95 / p99 / max roof heights and a
    ground p5 (`footprint_stats`, `ROOF_STATS`, `ROOF_STATS_VERSION` 2). p95 stays the headline;
    the stat version (`STAT_VERSION` 2) is unchanged, old caches stay readable. Readings are also
    scored against their method's statistic (`METHOD_STAT`): silhouettes (drone, street, lean,
    multiview, stereo, shadow) against p99, floor counts against p70.
  - Truth age: `temporal` = `may_postdate` (OSM `start_date` in or after the survey's last year),
    `predates` or `unknown` (`temporal_flag`); single-source scoring leaves `may_postdate` out.
  - Survey-only truth is keyed on the ring as a report writes it (6 decimals, `report_key`).
  - Nested footprints (an OSM `building:part` with half its area under a footprint whose height
    tag is higher, or under a larger one when untagged) are left out of the band tables
    (`nested_keys`, `NESTED_COVER` 0.5): the survey sees only the top of the stack.
- **Why:**
  - Review 2026-10-09 §3 item 1 and §4.4: one aggregate (Miami MAE 5.62 m) hides which band fails,
    and no drone change could be proven outside the Miami harness. The acceptance rule (§4.5)
    needs n, MAE and tol per band and the false-corroborated rate.
  - Tag-only rows are most of a report (San Juan 17,902 of 17,916 rows, Miami 27,612 of 27,987);
    a change to the tag rule or the prior moves them, so the tables must see them. The headline
    keeps its rows so it does not move.
  - The raw max is noise-bound on some surveys: Miami's 2019 topobathy has max > p95 + 20 m on
    24 % of 914 records (up to 294 m; Honolulu 1.7 %); p99 2.8 % (Honolulu 1.0 %). The max is
    stored as the review asked; silhouettes are matched on p99.
  - The 2026-10-09 scratch truth keyed full-precision OSM rings, which no report row has (San
    Juan 0 of 1,121 keys matched): that, not only the confirmed-only rule, made those cities n = 0.
  - Nested parts: Honolulu 241 of 1,138 published rows with truth; with them the tag tier's MAE at
    40-100 m was 23.1 m (bias -20.5), without them 15.1 m.
- **Measured (pre-registered in scratch `bench/notes.md` before the runs):**
  - Miami `--score-only --no-tiles`: headline n 168, MAE 5.62 m, every 2026-10-07 summary key
    identical. Passed.
  - Stored p95 = cached stat-2 `survey_m` on 914 / 914 Miami, 2582 / 2582 Honolulu, 1121 / 1121 San
    Juan, 442 / 442 Fort Lauderdale records (pass mark >= 99 %), for roof-stat versions 1 and 2.
    Passed.
  - Survey-only cities score in every band with truth (published rows): San Juan 10,726 scored,
    Fort Lauderdale 700, Honolulu 897. Passed. On measured rows only, the San Juan EUBUCCO 10-20 m
    band has one truth record, nested and left out, so n = 0 there (a fail of the pre-registered
    wording; the nested rule came after it).
  - Published rows, review bands, all tiers (n / MAE / bias / tol), nested parts left out:

    | city (truth) | <15 | 15-40 | 40-100 | >100 | all |
    |---|---|---|---|---|---|
    | Miami (two-source) | 155 / 3.3 / +2.3 / 48 % | 47 / 7.1 / -1.4 / 53 % | 26 / 6.2 / +3.4 / 88 % | 16 / 11.8 / -4.4 / 94 % | 244 / 4.9 / +1.2 / 57 % |
    | San Juan (survey only) | 9,839 / 1.7 / -1.2 / 74 % | 737 / 4.9 / -4.4 / 69 % | 150 / 11.0 / -8.7 / 79 % | no truth (max 85.9 m) | 10,726 / 2.0 / -1.5 / 74 % |
    | Fort Lauderdale (survey only) | 508 / 4.2 / +3.1 / 39 % | 98 / 5.2 / -1.0 / 72 % | 72 / 9.6 / -0.8 / 81 % | 22 / 29.6 / -23.5 / 73 % | 700 / 5.7 / +1.3 / 49 % |
    | Honolulu (survey only) | 361 / 3.5 / +2.1 / 42 % | 180 / 5.5 / -2.9 / 70 % | 279 / 15.3 / -12.1 / 76 % | 77 / 27.3 / -18.4 / 75 % | 897 / 9.6 / -5.1 / 61 % |

  - Measured rows (the headline's set), all tiers: Miami 136 scored (+32 nested = the headline's
    168), MAE 5.7 m, tol 54 %; San Juan 11, MAE 12.8 m; Fort Lauderdale 254, MAE 4.6 m, tol 32 %
    (242 prior rows: +4.5 m bias under 15 m); Honolulu 142, MAE 9.1 m, tol 58 %.
  - Pipeline metrics, published rows: coverage (non-prior rows / all footprints) Miami 85.4 %, San
    Juan 79.5 %, Fort Lauderdale 6.0 %, Honolulu 35.9 %; withhold precision 94.7 % / 100 % / 93.0 %
    / 98.3 %, recall 100 % everywhere (every wrong Street View reading was withheld; no image
    reading is published in these reports); false-corroborated: n = 0 everywhere.
  - Final run `runs/benchmark/2026-10-09_1333` (Miami, San Juan, Fort Lauderdale, Honolulu,
    `--score-only --no-tiles`); tables in scratch `bench/final_tables.txt`.
- **Rejected:**
  - Bumping `STAT_VERSION`: the p95 does not change; `roof_stats` versions the new fields.
  - Ground p5 as an absolute elevation (3DBAG): the providers return only the nDSM (the DTM is
    dropped in `lidar_3dep_copc.py::grid_points_ndsm`), so ground p5 is the p5 of the nDSM in a 4 m
    ring outside the footprint (~0 where the survey's ground model fits; median 0.02-0.03 m in all
    four cities; > 2 m on 13 % of Miami, 7 % Honolulu, 2 % San Juan, 5 % Fort Lauderdale records).
  - Scoring tag-only rows in the headline: it would move Miami's headline (n 168 -> 292).
- **Supersedes / superseded by:** refines the survey-only handling of "Low-rises: survey LiDAR
  where free, tiers everywhere, survey-blind benchmark" (Rejected hypotheses).
- **Source:** review `claude/research/panorama-pipeline-review-2026-10-09.md` §3 item 1, §4.4;
  commits `0a3fa6d`, `7f4cb9a`, `36ae357`, `5d94ade`, `d25201d`, `9c76611`, `348a513`, `57ca936`,
  `7334a93`, `de058ff`; scratch `bench/`.

### 2026-10-09 — Readings agree within 25 % of the smaller, per band; a measurement that disagrees with a published tag is shown beside it
- **Decision:**
  - `_core/tiers.py::agree`: two readings agree when the larger is at most `1 + tol` times the
    smaller (`|ln a/b| <= ln(1 + tol)`), `tol` from `AGREE_TOL` by the smaller value's band
    (`AGREE_BANDS`: <15 / 15-40 / 40-100 / >100 m). Fitted table: 25 % in every band. It was 25 %
    of the larger (a ratio up to 1.33; now 1.25). `tag_witness`, `verified_pair` (closest pair
    by ratio), the tag check, `disputed_by`, `single_support` and `cadastre_heights` use it
    (`single_support` later moved to its own 1.5x ratio: previous entry).
  - The 2 m floor below 15 m is implemented (`agree(..., floor_m=)`, `AGREE_FLOOR_M`,
    `REVIEW_FLOOR_M = 2.0`) but **off** (`AGREE_FLOOR_M = 0`).
  - `tiers.py::tag_measurement`: a tagged row whose independent agreeing pair (`verified_pair`)
    has a mean more than 10 % (`TAG_DISAGREE_REL`, on the smaller) from the published tag keeps the
    tag and carries `measured_m`, `measured_methods`, `tag_disagrees`; shown by
    `tier_display.py::tag_note` (in `tier_hover`), listed by `tag_disagreements` and in
    `heights.json` `tag_disagreements`. The user decides any switch.
  - `tiers.py::selection_reason`: every published row says why its value was chosen
    (`selection_reason`, `SELECTION_REASONS`), as 3DBAG's selection reason; shown in `tier_hover`.
- **Why:**
  - Labelled pairs (scratch `tiers/pairs.py`, 1,946 buildings with confirmed LiDAR / 3D Tiles truth;
    528 pairs of a kind the independence rules can corroborate: tag + witness, drone + drone,
    lean + shadow over 100 m): the 2026-10-07 satellite validation (7 cities, readings rebuilt
    with `readings.footprint_readings`) and the Miami drone harness (6 review states, trusted
    readings, median per camera). "Right" = within max(25 %, 2 m) of truth. Floor off:

    | band (smaller) | n pairs | old rule: agree / P(both right) | 25 % of smaller: agree / P | held out by city |
    |---|---|---|---|---|
    | < 15 m | 59 | 1 / 1.00 | 1 / 1.00 | 0 agreeing |
    | 15-40 m | 53 | 7 / 0.57 | 7 / 0.57 (no tol 5-25 % reaches 0.85) | 3 / 0.33 |
    | 40-100 m | 138 | 65 / 0.82 | 54 / 0.89 | 40 / 0.93 |
    | > 100 m | 278 | 234 / 0.97 | 216 / 0.99 | 215 / 0.99 |

  - False-corroborated rate (corroborated buildings whose published value is off truth by more
    than max(25 %, 2 m)): 6 of 218 (2.8 %) before, 5 of 207 (2.4 %) after. 40-100 m 3/38 -> 2/34;
    > 100 m 1/173 -> 1/166 (the same building; bootstrap 90 % interval of the change +0.00 to
    +0.08 points); 15-40 m 2/6 both. The 11 buildings that lose corroboration are a tag + one
    reading 1.25-1.33x apart (10 of them with a right tag, so coverage falls more than errors).
  - Measured vs tag: Palmetto's lean 136.4 m and shadow 135.3 m agree; the tag says 156 m.
    The review asked to publish what was measured; the user keeps the tag until they decide.
- **Cartagena replay** (`Cartagena_v12_tiers`, GPU, `SKYLINE_OWNERSHIP=0`, about 10 min, 0 Street
  View cache misses; HEAD 57ca936 with other agents' uncommitted edits in the tree):
  - The 7 towers are unchanged (same footprint, same value, all `verified_2`: Estelar 202, Allure
    190, Gran Bay 170, Portomarine 188, Nautica 161, Ravello 160, Palmetto 156).
  - Tiers verified_2 43 -> 42, tag 133 -> 134, single 124 -> 124, prior 716 -> 716; 1,016 rows in
    both runs.
  - Caused by the rule (4 rows; the same 4 come out of v11's own readings):
    - b0348, tag 51 vs lean 39.7 (1.28x): verified_2 -> tag; the value stays 51;
    - b0605, 117 m2, drone seed_5 135 + seed_4 101 (1.33x), satellite all low (stereo 11.8):
      verified_2 -> single -> withheld (satellite-low veto): 118 -> 10.1 m (prior);
    - b0691, drone seed_6 55.9 + lean 72.5 at conf 1.0 (1.30x): verified_2 -> single, the lean no
      longer supports it under the 2x rule -> withheld: 55.9 -> 18.9 m (prior) (later restored
      by `SUPPORT_RATIO`);
    - b0806, drone seed_4 62.8 + lean 48.4 at conf 0.37 (1.30x): verified_2 -> single -> withheld:
      62.8 -> 15.6 m (still, the lean is not validated).
  - Caused by `d2f2d5e` (satellite readings calibrated on load; not this rule): a low lean now has
    conf 0.3, so the tower-behind test no longer drops a drone reading: b0490 prior -> verified_2
    100.3 (drone 90.8 + 109.8), b0561 prior -> verified_2 111.3 (drone 104.6 + 118.0), b1505 prior
    -> verified_2 67.2 (drone 67.2 + lean 72.5), b0259 prior -> single 66.6 (supported by
    high_rise_floors); b0486 single 3.7 (satellite) -> prior 9.6 (a new drone reading of 115 m,
    over 2x the prior and unsupported); stereo under a confident tall lean dropped: b0777 single
    43.8 -> 41.1, b1591 single 47.3 -> 51.8.
  - Prior drift: 60 rows prior -> prior, mostly within 1-15 % (the T41 prior's neighbour set
    changed with those rows). Not this rule.
  - `tag_disagrees`, whole run: 1 row, Palmetto Eliptic b0598: measured 135.8 m (lean 136.4 +
    shadow 135.3) against the tag 156 m (-12.9 %); `verified_2`, tag published. Ravello is not
    flagged: drone 145.5 + stereo 161, mean 153 against the tag 160 (4 %).
  - `selection_reason` on all 1,016 rows: 231 prior with no usable reading, 221 prior after a
    withheld reading, 98 satellite-only, 46 osm_tag, 45 drone median, 8 high-rise; the rest are
    osm_levels / street.
  - 2 m floor what-if (not on): 1 row changes, b2663 (tag 3 m + multiview 1.5 m, a ground match):
    tag -> verified_2.
  - Review §4.5 sanity: the 7 towers move 0 m; no tagged row changes value; used readings removed
    by this rule are 3 drone medians (b0605, b0691, b0806), all untagged (b0605 is a tower-behind
    pattern; b0691 and b0806 were doubtful, see the support entry). The tier map
    `tiers_map.png` / `tiers_map_bocagrande.png` (v11 left, v12 right) was rendered and looked at:
    alike at map scale.
- **Rejected / not accepted:**
  - The 2 m floor (the review's max(25 %, 2 m)): on the labelled pairs its only new
    corroboration is wrong (Seattle: tag 3 m + lean 4.2 m, truth 12.5 m), the overall rate rises
    2.75 % -> 2.88 % with it on, and the < 15 m band has 2 corroborated buildings, short of the
    30 the acceptance rule (review §4.5) needs. `AGREE_FLOOR_M = REVIEW_FLOOR_M` turns it on once
    low-rise labels exist (Honolulu, San Juan, Fort Lauderdale drone runs).
  - Bands looser than 25 %: 40-100 m stays >= 0.85 only up to 30 %, and the review set 25 % as
    the rule; looser values were scored for information only.
  - The claim "corroborated >= 0.85 within tol" (F-SKY26 criterion) holds only from 40 m up;
    under 40 m too few readings agree to tell (8 agreeing pairs in 112).
- **Done 2026-10-23:** the other "25 %" call sites (fusion `fuse_heights` bound and keep,
  satellite `readings._agree` / `group`, elevated `CREDIT_TOL` in `tower_behind` and
  `drop_contradicted_floors`) now call `tiers.agree` (ratio 1.33 -> 1.25; a 0 reading never
  agrees). `READINGS_VERSION` 2. Measured: only the satellite readings could be replayed (no
  elevated review states exist): Cartagena 56 of 755 footprints change grouping, `ls` rows 5 -> 3
  (b0539, b0265 at ratios 1.27, 1.28), one tagged row changes; 13 footprints sit in the 1.25-1.33
  window for the other sites, 12 already withheld (b1436 is not). Tests unchanged (141 pass).
  Fusion / elevated effect is to be read on the next cache-only region run.
- **Open:** the PDF / HTML-table display lines for `tag_disagrees` are not wired.
- **Supersedes / superseded by:** the "within 25 %" of the 2026-10-07 tier entry (25 % of the
  larger). The rename to `corroborated` is the 2026-10-09 entry at the top.
- **Source:** commit `b04b22f`; scratch `tiers/` (scripts and logs); Cartagena replay
  `runs/region_reports/Cartagena_v12_tiers_skyline_report/`.

### 2026-10-09 — An untagged footprint's search window does not follow its drone or floors reading (refused)
- **Tried:** `measure.search_cap`: untagged, the shadow / lean / sweep windows (60 / 60 / 80 m)
  grow to 1.6x the highest drone or floors reading, and to at least 220 m when a seed reads over
  80 m (review 2026-10-09 item 2). `20_satellite_heights.py --hints <heights.json>` re-measures
  only the footprints whose window changes (Cartagena: 256 hinted, 241 windows changed, 2,554 s).
- **Pre-registered** (scratch `sat/notes.md`, before any run): Honolulu survey p95 (stat 2), tags
  stripped for measuring (scene geometry fitted with the tags), the "after" run with a 100 m hint
  on every footprint (a 220 m window: the regime the lift applies to; for low plots the worst
  case). Tol = max(25 %, 2 m) of the survey.
  - H1: lean or multiview within tol on >= 80 % of the 126 footprints >= 100 m.
  - H2: leans >= 40 m on <= 2 % of the 1,982 footprints under 30 m.
- **Result (Honolulu; reference scenes 2025-02-01 WV02 and 2025-05-11 WV03, 4 older scenes; all
  126 and 1,974 of 1,982 measured):**

  | | tagged (reference) | untagged, 60 / 80 m | untagged, 220 m |
  |---|---|---|---|
  | H1 lean or multiview within tol, of 126 | 37 (29.4 %) | 2 (1.6 %) | **47 (37.3 %)** |
  | lean within tol / with a lean | 32 / 55 | 0 / 59 | 36 / 79 |
  | lean within tol at conf >= 0.7 | 22 | 0 | 19 |
  | lean > 1.25x survey | 6 | 0 | 21 |
  | own-outline towers (tag within 25 %), of 78 | 35 | 2 | 39 |
  | H2 lean >= 40 m on < 30 m, of 1,982 | - | 219 (11.1 %) | **706 (35.6 %)** |
  | ... at conf >= 0.5 / >= 0.7 | - | 113 / 64 (3.2 %) | 412 / 209 (10.5 %) |
  | multiview / stereo >= 40 m on < 30 m | - | 16 / 177 | 62 / 314 |

  - H1 fails (37.3 % against 80 %), H2 fails (35.6 % against 2 %; the 60 m window already
    fails it at 11.1 %). Even tagged, lean and multiview reach 29.4 %: Waikiki's lean is missing on
    71 of 126 towers, and 48 of the 126 have no OSM tag within 25 % of the survey, mostly podium
    slices or building:parts inside a tower outline that read the tower's p95.
  - Why (single-scene diagnostic, 64 towers in the WV02 outline, scratch `sat/hon_reasons.py`):
    tagged, 24 leans; missing because "roof outline hidden by neighbours" 14 (the tagged
    neighbours' occlusion labels), conf < 0.15 13, "facade bottom hidden" 11. Untagged with 220 m:
    44 leans (hidden 1) but only 18 within tol: without labels the neighbours' roof edges are read
    as the footprint's own. The same mechanism gives H2.
- **Chicago** (second city, pre-registered H3; lean only, 2025-04-24 WV03 core, repo
  `measure_single`, truth = LiDAR and 3D Tiles agreeing (cached), scratch `sat/chi_lean.py`):

  | | tagged (reference) | untagged, 60 m | untagged, 220 m |
  |---|---|---|---|
  | lean within tol, 100-200 m (of 244) | 140 (57.4 %) | 0 | **124 (50.8 %)** |
  | ... at conf >= 0.7 | 87 | 0 | 66 |
  | lean >= 40 m on < 30 m (of 223) | 45 (20.2 %) | 45 (20.2 %) | **112 (50.2 %)** |
  | ... at conf >= 0.7 | 10 | 12 | 28 |

  H3a fails (50.8 % against 80 %), H3b fails (50.2 % against 2 %; 20.2 % even tagged).
- **Cartagena replay** (`Cartagena_v12_sat`, hinted + recalibrated readings): the 7 towers and the
  10 tag-correct readings unchanged, but 6 rows published above 205 m (the city's tallest is
  Estelar, 202 m): b0655 256 single (lean 255.6 at conf 1.0 + floors flag), b0549 233 verified_2,
  b0586 231 single, b0646 223 verified_2 (lean 241.8), b0282 219 single, b1152 206 verified_2
  (stereo 249.5); b0060 203 verified_2 rests on a lean of 195 m at conf 0.21. 26 hinted rows
  gained a lean >= 40 m at conf >= 0.7. The window a drone reading opens lets the lean find a
  peak that then corroborates that reading (tiers ignore confidence).
- **Decision:** refused for publishing (two truth cities fail both marks; review 4.5). `search_cap`
  and `--hints` stay as a measuring tool (hints only widen windows when passed); Cartagena's
  `readings.json` stays unhinted (the hinted file is scratch `sat/cart_readings_hinted.json`).
- **Would need, before trying again:** occlusion labels for untagged neighbours (their drone,
  floors or prior height instead of 10 m), and a hinted reading counting only when it does not
  depend on the reading it supports (or at conf >= 0.7 and under the site's
  `max_plausible_height_m`). Pre-register H2 at conf >= 0.7 for that.
- **Supersedes / superseded by:** none.
- **Source:** commits `d2f2d5e`, `78f06b9`; scratch `sat/`.

### 2026-10-09 — Stored satellite readings are calibrated on load; Cartagena's file recalibrated
- **Decision:**
  - `readings.load(calibrate=True)` applies `readings.calibrate` to every footprint and reports
    `meta["n_recalibrated"]`; `satellite_fusion.load_region` logs a warning when it is not 0.
    `--recalibrate` reads the file raw. `--add` / `--hints` no longer restore old readings for a
    re-measured footprint that now has none.
  - `20_satellite_heights.py --recalibrate` run on Cartagena's region file: 218 of 2,911
    footprints changed (178 leans under 40 m capped at 0.3; 40 stereo and 14 multiview readings
    dropped under a confident tall lean).
- **Why:** the file mixed calibrated and uncalibrated footprints (an earlier `--add` kept old
  readings as stored): 178 leans under 40 m kept conf > 0.3, and on 32 rows the "satellite low"
  verdict (`_core/height.py::_satellite_low`, `elevated.tower_behind`) rested only on such a lean
  (review 2026-10-09 item 2, 2026-10-08 decision).
- **Replay** (`Cartagena_v12_recal` against the control `Cartagena_v12_ctrl`: same tree, the
  pre-change file loaded raw; tree hash in scratch `sat/cart_v12_*.log`):
  - 7 towers unchanged; 10 of 10 tag-correct used drone readings kept;
  - satellite-low rows 537 -> 505: exactly the 32, none newly low; tower-behind 94 readings on
    84 footprints -> 86 on 76;
  - tiers verified_2 39 -> 42, prior 719 -> 716 (single 124, tag 134); new verified_2: b0561
    111 m (two drones, no cadastre match), b0490 100 m (two drones; non-PH cadastre 2 floors,
    7 m), b1505 67 m (drone + lean; PH cadastre 65 m);
  - **used drone readings on non-PH 1-3-floor plots 50 -> 54** (b0246 133 m 2 fl, b0484 106 m
    1 fl, b0486 115 m 1 fl, b0490 91 m 2 fl): review 4.5 "do not rise" fails by 4.
  - Against v11 (other agents' changes included): verified_2 43 -> 42, tag 133 -> 134, single
    124, prior 716.
- **Open:** on Honolulu (untagged, 60 m window) a lean under 40 m with peak confidence >= 0.5 is on
  a building under 40 m 90 % of the time (719 of 798; base rate 79.7 %), on one >= 100 m 2.3 %
  (base 4.9 %). Chicago's 52 % (2026-10-08) came from a downtown. As a tower-behind veto the
  uncalibrated low lean carried some signal in a beach city; whether the veto should read
  `extra.conf_peak` was left to the owner of `elevated.py` (it would bring back b0490):
  **done 2026-10-09, see "The tower-behind veto counts a low lean with peak confidence >= 0.7 as
  satellite low".**
- **Supersedes / superseded by:** applies the 2026-10-08 calibration entry to stored files.
- **Source:** commit `d2f2d5e`; scratch `sat/`.

### 2026-10-09 — A scene's outline is every polygon of it in the region
- **Decision:** `scene.scene_outlines(cfg, release, bbox)` queries the release's source layer
  with the region bbox and merges each scene's polygons; `scene_polygon(..., bbox=)` uses it,
  identify at points only as a fallback; `20_satellite_heights.py --outlines` re-queries the
  cached `polygons.json`.
- **Why:** a scene is often several polygons in one release, and identify at one point returned
  one of them. Honolulu: 2025-05-11 WV03 218 -> 515 of 3,114 footprints, 2025-02-01 WV02
  1,559 -> 2,571, 2022-10-03 WV02 1,856 -> 3,086, 2024-01-14 WV03 1,819 -> 3,113. Cartagena:
  2023-02-19 WV02 1,274 -> 3,018 (the other six unchanged). Cartagena has 3,017 footprints after
  the water filter.
- **Done later:** Cartagena's stored readings were re-measured with all 7 scenes' outlines
  (`readings.json` sha be8da4d5 -> 73515f8a, 2,922 footprints). Earlier
  they used the old 2023-02-19 outline.
- **Source:** commit `f52145d`.

### 2026-10-09 — Seed pages show a reading's reason when its footprint has no measured row
- `html_report.py::_reading_status`: the segment's `untrusted_reason` (or tower behind / top
  edge) is shown when the footprint's row is missing from the report rows; "no published row"
  only when there is no reason. Cartagena v11: 308 trusted drone readings, and 39 readings showed
  only "no published row" on the seed pages; 0 in v12_ctrl / v12_recal.
- The row itself is still missing from `published_by_id`: the report gets `building_heights`
  without the unmeasured-tag rows, so "published m" and the tier stay empty for them. The fix is in
  `region_pdf.py` (write `heights.json` first and pass its rows to `write_region_report`); not
  applied.
- **Source:** commit `d2f2d5e`.

### 2026-10-09 — A single reading over 2x the prior publishes the prior unless supported
- **Decision:** an untagged `single` row (one drone median or a satellite-only height, no
  independent agreeing pair) more than 2x its prior publishes the prior (`_core/tiers.py::single_withheld`,
  `SINGLE_WITHHOLD_FACTOR`; flag `SKYLINE_WITHHOLD_SINGLE`, default on): tier `prior`,
  `prior_disagrees`, the reading kept as `single_reading_m` / `single_source` / `single_methods`,
  `withheld_reason` "single over 2x prior". It stays `single`, with `single_support`, when
  supported (`tiers.py::single_support`):
  - a validated satellite reading of 40 m or more agrees within 25 %, or is the single itself:
    lean at conf >= 0.7, lean + shadow (`ls`), multiview at conf >= 0.3 (`SUPPORT_MIN_CONF`);
    support `["lean", ...]`;
  - floor counts flagged the plot a high-rise (`floor_bands.high_rise_plots`, satellite veto
    inside): `["high_rise_floors"]`. This also keeps the `withheld:high_rise` rows (an untagged
    plot with no drone reading and no publishable satellite height publishes
    `floor_bands.high_rise_height`), exempt since 2026-10-08.
  Never when the plot's confident satellite readings are all under 40 m (`_core/height.py::_satellite_low`,
  the tower-behind test's rule). Rows floors counted carry `high_rise_seen`, `floors`, `storey_m`.
- **Why:** the user's choices: the rule on 2026-10-07/08, the high-rise exemption on 2026-10-08,
  the refinement on 2026-10-09.
  - Rule: on v9 the tower-behind check showed a lone drone reading far over the prior is often a
    farther tower's top; v9 had 164 drone singles over 2x the prior at a median of ~99 m.
  - Refinement: v10 withheld 302 singles (median reading 69 m; 152 drone, 150 satellite) and
    Bocagrande, a tower district, published mostly at the ~12 m prior. Many had a validated
    satellite reading behind them (b1429 lean 60.4 at conf 1.0 + stereo 64.2; b0112 lean 67.4 at
    0.9 + stereo 65.5), and the high-rise flag reached only plots with no drone reading.
  - Thresholds from Chicago LiDAR (entries 2026-10-07 below): lean over 100 m within 25 % 94 %,
    multiview over 40 m 84 %; stereo at 40-100 m only ~59 %, so stereo alone never supports
    (b1211: lean 53.5 at conf 0.64 + stereo 54 stays withheld).
- **Measured** (Cartagena region report, cache-only; v9 2026-10-07, v10 2026-10-08, v11
  2026-10-09 with this rule and the floors fix below):

  | | v9 | v10 | v11 |
  |---|---|---|---|
  | rows | 646 | 1,016 | 1,016 |
  | verified_2 / tag / single / prior | 32 / 143 / 223 / 248 | 40 / 133 / 42 / 801 | 43 / 133 / 124 / 716 |
  | singles over 2x withheld (drone / satellite), median reading | 0 | 302 (152 / 150), 69 m | 218 (142 / 76), 77 m |
  | kept with `single_support` (satellite / high-rise flag only) | - | - | 90 (51 / 39) |
  | `withheld:high_rise` rows | 0 | 8 | 8 |
  | Bocagrande rows >= 40 m (of 588 in v10/v11) | 215 of 366 | 60 | 127 |

  - Support kinds: lean 34, high-rise flag only 39 (11 of them drone singles), lean + flag 6,
    lean + ls 5, ls 3, multiview 2, lean + ls + flag 1.
  - The 218 still withheld: satellite says low 90; no confident satellite reading 45 (drone);
    only stereo agrees 47; a lean at conf 0.5-0.69 agrees 19; satellite >= 40 m disagrees 15; no
    satellite reading 2.
  - The 7 published towers keep their values and tiers (Estelar, Gran Bay, Portomarine, Nautica,
    Ravello verified_2 by drone / satellite pairs; Allure and Palmetto verified_2 by tag + lean
    and lean + shadow).
- **Rejected:** counting plain stereo as support (~59 % at 40-100 m: it would keep 47 more);
  publishing every single (v9: 164 drone singles over 2x at median ~99 m, the tower-behind
  readings among them).
- **Supersedes / superseded by:** the 2026-10-08 rule (no support) is this entry without the
  refinement; the high-rise exemption is the floors branch.
- **Source:** commits `4a73d5a`, `53bb58b`, `050ccbc`; [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md)
  Progress 2026-10-08 / 2026-10-09; maps `runs/region_reports/Cartagena_v11_skyline_report/tiers_map_bocagrande.png`.

### 2026-10-08 — An OSM height tag one agreeing image reading confirms is verified_2
- **Decision:** `_core/tiers.py::tag_witness`: an `osm_tag` height (never `osm_levels`) that one
  trusted drone reading, satellite lean or multiview, or a stereo reading of 40 m or more agrees
  with within 25 % is `verified_2`, methods `["osm_tag", <reading>]`. Shadow (a lower bound),
  floors and Street View never witness; an independent pair that disputes the tag blocks it.
  Unmeasured tagged rows in `heights.json` carry their satellite readings so a lean can witness.
- **Why:** the user's rule (2026-10-08). A tag is an independent source, so tag + one image reading
  is two sources. v10 and v11: 9 tag rows verified_2 this way (lean 6, multiview 2, stereo 1),
  Allure (190 m, osm_tag + lean 168 m) among them; Palmetto's unmeasured row got its satellite
  readings back and verifies by lean + shadow.
- **Rejected:** levels-derived tags (one storey guess per building, not a measurement).
- **Supersedes / superseded by:** —
- **Source:** commit `53bb58b`.

### 2026-10-08 — Footprints a drone seed measured without a usable reading get a prior row
- **Decision:** `region_pdf.py::_drone_seen_rows` adds a row for every untagged footprint an
  elevated seed measured that has none (its reading untrusted, left out by the tower-behind check
  or disputed in fusion), `drone_seen` naming the seeds; `withhold_untagged_street_view` gives it
  the prior (or a publishable satellite / high-rise height). Their prior comes from a second prior
  over both record sets (`_chain_fallbacks`): `untagged_prior.fallback_for` predicts only the
  records it was built on.
- **Why:** v9 dropped 41 singles and 3 verified rows of v8 whose only drone reading was left out:
  no estimate, no row, so the map showed holes where a seed had looked. v10 run 1 added 368 rows
  but, without the wider prior, dropped all but the satellite / high-rise ones again; v10 run 2:
  376 rows added, 646 -> 1,016 rows.
- **Supersedes / superseded by:** —
- **Source:** commits `4a73d5a`, `c92f224`.

### 2026-10-09 — A floor count its own seed's reading contradicts leaves the fusion
- **Decision:** `_pano/elevated.py::drop_contradicted_floors` (called in `floors_info`): a floors
  reading of a plot leaves the fusion when the same seed's base-visible reading of that plot,
  trusted or not, differs from it by more than 25 % (`FLOORS_SAME_SEED_AGREE`). The high-rise flag
  and the storey calibration are unchanged.
- **Why:** Cartagena v10 lost the drone heights of b0582 (Hotel Cartagena Plaza), b0426, b0635
  and b0075 (v9: verified_2, verified_2, verified_2, single). Replaying v10's fusion from the run's
  own stage-cache entries (measured and floors of seeds 1/4/5/6) found the cause: floors readings
  in fusion (commit `1c9a182`). One count, entered as a fully seen drone reading at 440-510 m
  (sigma_log 0.13-0.16), outweighed the trusted drone reading, anchored the fusion and outvoted it:
  - b0582: seed_1 66 m (base in view; satellite shadow 66, stereo 61) vs seed_1's own count, 35
    floors = 148 m;
  - b0426: seed_4 72 m, stereo 74.5 vs seed_5's count 98.5 m (seed_5's own reading 72 m);
  - b0635: seed_4 44 m, lean 53.5 at conf 1.0, stereo 44 vs seed_6's count 73 m (its own roof
    fit 49 m);
  - b0075: seed_4 67 m vs seed_5's count 35 m (its own reading 118 m).
  Every one contradicts its own seed's reading of the plot. One image cannot overrule itself: the
  count is an instance matched to the plot by range, and it usually takes its "base in view" from
  that very reading, so a count far from it is another building's facade.
- **Measured** (the v10 replay): 20 of 145 floors readings drop. Footprints with a drone height:
  without floors 256; with floors as in v10 256 (7 lost, 7 gained); with the filter 261 (2 lost,
  both to another seed's count: b0170, b0403; 7 gained).
  The four plots get their reading back; the b1158 catch (drone 183 m) and every tagged plot are
  unchanged. Stage-cache keys are untouched (the filter is outside the hashed functions).
- **Rejected:**
  - the saved review states as evidence (`seed_experiment.py --remeasure`): older captures (137-186
    footprints a seed vs the run's 176-276) with an unreliable storey (n 1), so no floors readings
    and every reading kept;
  - re-weighting floors readings (a plot-match term in sigma_log): needs truth on wrong-plot counts
    that we do not have;
  - dropping only counts against the same seed's *trusted* reading: restores b0582 only.
- **Supersedes / superseded by:** refines "Floor counts flag high-rises and join fusion" below.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) Progress 2026-10-09.

### 2026-10-08 — Cadastre floors (Cartagena, AMB) as an opt-in height source; propiedad horizontal never published
- **Decision:** `city2stl/height/providers/co_catastro.py` reads the AMB cadastre `Construccion`
  layer (datos.gov.co `d7hk-qg8h`: range reads of ~25 MB of a 1.2 GB zip, cleaned layer cached
  90 days) and matches it to model footprints: the largest-overlap polygon or the union of the
  parts lying inside, IoU >= 0.3; floors = the tallest part. Height = floors x storey + roof:
  - storey 3.0 m + 1 m roof; colonial Centro, San Diego and Getsemaní 4.5 m; over 10 floors
    4.13 m (skyline storey calibration) + 3 m;
  - published only for non-PH predios with 1-10 floors (confidence 0.7 for 1-5, 0.6 for 6-10).
    Propiedad horizontal (digit 22 of the predial number is 9) and towers never publish;
  - skyline: `cadastre_heights.py`, site flag `use_cadastre_heights` (off). A publishable match
    replaces the prior, so the 2x rule now withholds a lone drone reading over twice the cadastre
    height; a confident satellite reading >= 40 m that disagrees vetoes it. Cadastre + one drone,
    lean, multiview or stereo >= 40 m reading is `verified_2` (the user's rule). Reports print the
    CC BY-SA 4.0 attribution and share-alike line (`tier_display.height_attributions`).
- **Why (v9 region, 3,018 OSM footprints; tables in the 2026-10-08 report):**
  - Match: 2,092 footprints (69 %), 1,363 publishable, 719 PH. 55 % of the 12,427 cadastre
    polygons in the box touch no OSM footprint (Bocagrande 20 %, Manga 15 %, Centro 3 %).
  - Publishable vs OSM `height` tags: n 8, median abs error 0.5 m, 88 % within 25 % (all under
    10 m, in Espinal). No height tag on a publishable match in the target barrios.
  - PH vs tags: n 16, median abs error 28 m, 19 % within 25 %. Hotel Estelar (52 floors), Gran
    Bay Club (42) and Nautica read 1 floor. Seed_6 drone pano: PH "1 floor" b0748 and b0666 are
    12-15-floor blocks, PH "4 floors" b0791 about 10.
  - Towers: 16 of 48 matched v9 confirmed / tagged rows >= 30 m read <= 3 floors; median abs
    error 29 m. Floors are not a tower source here.
  - R2 floors (`yk9q-cw89`) on non-PH matches: 82 % exact, 97 % within one floor (n 1,175; same
    office, so a consistency check). R2 has no floors for PH units.
  - OSM `building:levels`: 33 % exact, 85 % within one (n 52). In the colonial barrios the
    cadastre is lower in 26 of 47 (OSM 2 / cadastre 1: 13).
  - Satellite (weak at these heights): multi-scene shadows vs <= 5-floor matches, median abs
    error 3.2 m, 26 % within 25 % (shadows read 7-8 m for 1- and 2-floor houses alike);
    stereo / multiview conf >= 0.5, median abs error 2.0-2.5 m; 13 of 249 stereo readings are
    >= 40 m on a "low" match (a tower's podium polygon): hence the veto.
  - On v9 it would change 117 prior rows (median 12.1 m; cadastre median 7 m lower) and 62 lone
    drone readings of 25-233 m on 1-3-floor cadastre houses. Of those 70 singles, 39 have a
    confident satellite reading under 25 m (the building is low), 10 one over 25 m.
- **Not adopted:** a synthetic `building:levels` tag (`height_from_tags` has one 3.2 m storey plus
  a roof level, and the row would count as OSM-tagged and train the prior); `altura_tot` (97 %
  are the 3.0 m default); +1 m for commercial ground floors (`height_m(commercial=)` exists,
  unused: on the shadows commercial and other 1-floor predios read 7.3 and 7.5 m).
- **Open:** colonial floor counts and the 4.5 m storey are unchecked (no tags, no drone or
  satellite coverage of the old town): spot-check 20-30 facades before relying on them there.
  The publishing hook (`withhold_untagged_street_view(cadastre=)`, `region_pdf`) is written out
  in the `cadastre_heights` docstring for the owner of those files.

### 2026-10-08 — Satellite lean under 40 m is never confident; stereo under a confident tall lean is dropped
- **Decision:** `city2stl/height/satellite/readings.py::calibrate` (applied in
  `footprint_readings`; `20_satellite_heights.py --recalibrate` for stored files):
  - a lean under 40 m keeps its height but its confidence is capped at 0.3 (`LOW_LEAN_MAX_CONF`;
    the peak-shape value stays in `extra.conf_peak`);
  - a stereo or multiview reading that a lean of >= 40 m at conf >= 0.7 is 1.25x or more above is
    dropped; its height stays in the lean's `extra` (`stereo_under_lean`).
- **Why:** Cartagena review of v8/v9. Ravello (published 144 m) had lean 38 m at conf 1.0; Allure
  (180 m) had stereo 77 m at conf 0.83 beside a correct lean of 168 m. The confidences are peak
  shapes, not reliabilities. Chicago LiDAR (`S/satellite_cities/val`, stored v1/w4 results):
  - lean 15-40 m at conf >= 0.7 is within 25 % of truth 1 of 61 times (44 % under 0.6x), 3-15 m
    1 of 16; of confident (>= 0.5) leans under 40 m, 57 of 110 are on buildings of 40 m or more;
  - with such a lean 1.25x or more above it, stereo is within 25 % 1 of 20 times, the lean 16 of 20.
  - Effect: stereo >= 40 m within 25 % 0.53 -> 0.57 (n 479 -> 449), multiview 0.80 -> 0.84
    (51 -> 49); the 66 dropped stereo/multiview readings were within 25 % 3 % of the time, their
    leans 55 %. Confident leans under 40 m 108 -> 0. Miami's reference scene has no lean (near
    nadir): no change there.
- **Not podiums:** both Cartagena footprints fit their roofs (lean crops,
  `S/sat_cov/lean_*.png`). Ravello's 38 m is a bright feature part way up the facade; Allure's 77 m
  stereo a lower match. A "footprint much larger than the matched roof" rule would not catch
  either; the cross-method rule does.
- **Consequence:** the cap changes neither fusion (the weights table already drops lean < 40 m)
  nor the tiers (they ignore confidence); a dropped stereo is one satellite reading fewer in both.
  The tower-behind test (`elevated._sat_max`, conf >= 0.5) no longer takes such a
  lean as "satellite says low": 35 of Cartagena's 749 Bocagrande footprints (none tagged) lose
  that cue. Re-check step 6's flags with `seed_experiment.py --remeasure --behind`.
- **Supersedes / superseded by:** refines the 2026-10-07 satellite weights and stereo entries.

### 2026-10-08 — Floor counts flag high-rises and join fusion; they never publish alone
- **Why:** F-SKY26 steps 4 and 5. Untagged towers no drone read fall to the ~12 m prior; floor
  counts (MobileSAM instances, `floor_bands.pano_floors`) say which plots hold towers and give a
  second, pose-free height.
- **Changed:**
  - `floor_bands.calibrate_storey` (a city's storey from OSM *height* tags, never levels;
    leave-one-out sigma_log), `high_rise_plots` (>= 10 floors, a lower bound counts, `low=`
    veto), `floor_readings` (floors x storey + 3 m; lower bound when the base is hidden; sigma_log
    = hypot(calibration sigma, strip spread), weighted as a drone reading at 3300 x sigma m; none
    when the calibration's sigma_log > 0.25), `high_rise_height` (the publisher's hook).
  - `footprint_detect.fuse_heights`: `kind` "floors" readings carry their `seed`; they are one
    source together, counted only when one comes from a seed with no drone reading kept (`group=`
    merges captures from one camera); `floors_only` in the result.
  - `_pano/elevated.py`: `seed_floors` (stage-cached, `ElevatedSeed.floors`), `floors_info`,
    `elevated_estimates` returns an `ElevatedEstimates` list with `.floors` (`high_rise_seen`,
    `floors`, `storey_m` ...) and `.storey`; `orchestrator` puts it in `elevated_state["floors"]`.
- **Base in view** = ground under the mask (`InstanceFloors.base_seen`) **or** the same seed's
  reading of that plot saw its base. The mask test alone left Cartagena 2 calibration samples
  (140 of 153 counts lower bounds); with either: 85 lower bounds, 6 samples, storey 4.13 m,
  sigma_log 0.13 (reliable). Miami: 10 samples, sigma_log 1.01: not reliable, so no Miami floors
  readings (the hook uses the nominal 3.1 m there).
- **Satellite veto on the flag:** Cartagena's 3 flagged tagged plots under 30 m (b0634 10 m,
  b0639 10 m, b0645 6 m) all had their base hidden: the count was the tower behind the low
  building. A plot whose confident satellite readings are all under 40 m (`elevated._sat_max`,
  the tower-behind test's rule) is never flagged; it removes b0634 and b0639 (stereo 5.5 / 7 m).
  b0645 stays (stereo 49 m, shadow 56 m: its 6 m tag may be the wrong one).
- **Measured** (`seed_experiment.py --floors --s45`; Cartagena hi-res states seeds 1/4/5/6/7, truth
  = tags + published; Miami 6 states, truth = confirmed LiDAR; prior for the hook 12 m):

  | high-rise flag (>= 10 floors) | flagged | with truth | precision | recall (truth >= 30 m) | untagged precision | hook within 25 % (prior) |
  |---|---|---|---|---|---|---|
  | Cartagena, no veto | 113 | 11 | 0.73 | 0.36 (22) | n 0 | 0.45 (0.18) |
  | Cartagena, satellite veto | 72 | 9 | 0.89 | 0.36 | n 0 | 0.56 (0.00) |
  | Cartagena, base seen only | 57 | 6 | 1.00 | 0.27 | n 0 | 0.83 (0.00) |
  | Miami (no satellite) | 21 | 12 | 0.92 | 0.11 (101) | 1.00 (n 2) | 0.33 (0.08) |

  - Fusion, drone + floors vs drone only: Cartagena fused within 25 % 0.89 (n 9) -> 1.00 (n 8),
    sigma_log 0.44 -> 0.06; 10 footprints newly disputed, 1 with truth (b1158: drone 183 m, tag
    45 m: caught), 0 false; 24 newly verified (2 with truth, both within 25 %). With the
    satellite readings too: 0.83 -> 0.83 (n 6), sigma_log 0.114 -> 0.119, 9 newly disputed, none
    with truth. Miami: unchanged (no readings).
  - Published towers: the 7 publish their tags, unchanged. Fused values: Gran Bay 159 -> 176
    (170), Portomarine 188 -> 178 (162.5), Nautica 161 -> 159 (160), Ravello 146 -> 164 (144),
    Estelar 198 (202) unchanged; Allure gets a floors-only lower bound of 98 m (180), not
    published.
  - Floors-only footprints: 47 (42 untagged); the 2 with truth are lower bounds reading 37 % low,
    none with the base seen has truth.
- **Verdict:**
  - high-rise flag: >= 10 floors, lower bounds count, satellite veto. Precision 0.89-0.92 on all
    plots with truth, 1.0 on Miami's only 2 untagged confirmed ones (Cartagena has no untagged
    truth): the >= 0.9 target is met only on small samples. 63 untagged Cartagena plots flagged,
    22 of them with no drone reading.
  - floors in fusion: on (`elevated.FLOORS_IN_FUSION`), only where the storey calibration is
    reliable. `FLOORS_PUBLISH_ALONE` = False.
  - publishing: the hook is written, not wired into `withhold_untagged_street_view`. It
    conflicts with the 2026-10-08 rule "a single reading over 2x the prior publishes the prior":
    every high-rise height (>= 10 x 3.1 + 3 = 34 m) is over 2x a 12 m prior, so wiring it needs
    the user's call on exempting `withheld:high_rise` rows from that rule.
- **Supersedes / superseded by:** publishing wired, and the flag supports a single over 2x the
  prior (entry "A single reading over 2x the prior publishes the prior unless supported",
  2026-10-09); counts their own seed contradicts leave the fusion (entry "A floor count its own
  seed's reading contradicts leaves the fusion", 2026-10-09).
- **Source:** plan F-SKY26 steps 4/5; outputs `<scratch>/out/seed_exp/s45_cart.json`,
  `s45_miami.json`.

### 2026-10-07 — Commons photo screen loosened after the user's review of 38 rejects
- **Why:** the user reviewed 38 Commons photos the photo pipeline had rejected (Miami, Chicago,
  Cartagena; review item set c3): 25 were usable skyline photos.
  - wide (> 3:1) 6 of 6 usable; no location 7 of 8; camera fit failed 7 of 12; "night" 3 of 6;
    camera far from region 2 of 4; not a skyline 0 of 1; portraits 0 of 1.
- **Changed** (`commons_photos.screen`, `pipeline_status`, `fit_prior`, `skyline_quality`):
  - wide panoramas fit as cylindrical views (FOV guessed from the aspect at 35 deg vertical,
    +-60 % free); not split into crops: a crop of a cylindrical image is not pinhole either.
  - unlocated photos, and geotags > 200 km away (Cartagena p107 carries Cartagena, Spain), go
    to a placement queue: linked refinement or the EXIF + 0.1-lead search (the gate from
    "Placing no-GPS Commons photos with trusted heights or LightGlue"), else
    `placement_queue.json` "manual_label" (labels place to +-5 m).
  - cameras up to 20 km outside the region (was 15): the usable Chicago one sat at 16.3 km, the
    two the user agreed were too far at 29.9 and 36.3 km.
  - night: no pixel-dark drop. A low-light photo (sky value < 170) is kept when its skyline
    clarity (edge step across the outline x coverage) is >= 0.11. The three "night" photos the
    user called usable were bright (sky 194-207; EXIF-hour night); the three rejected are
    low-light and score 0.02-0.108; Chicago's usable night panorama scores 0.110 (thin margin,
    tuned on these verdicts). Day photos are not gated on clarity (23 % of day outlines score
    < 0.11 in haze, unreviewed). No previously kept Miami or Chicago photo is lost to it.
  - located photos without EXIF focal length now reach the fit (FOV 55 deg, +-50 % free).
  - camera fit: when the EXIF FOV fails the misfit gate, retry at +-40 % (Commons crops keep
    the full frame's EXIF).
- **Camera-fit failures examined** (the 4 located usable ones, 4 the user agreed were bad as
  controls; refine within 500 m unless stated):
  - p234: a black-and-white photo, SegFormer found no sky (outline flat at row 0, misfit 1.0):
    segmentation, not the camera. Now "flat outline", manual-label list.
  - p78: EXIF 16.8 deg, fits 25 deg at misfit 0.28 (passes at +-40 %); p276: EXIF 74 deg, best
    50 deg (a crop; misfit 0.32 in the pipeline, kept by the OSM-agreement rescue; its bases are
    hidden by a cruise ship); p170: 0.54 at best (unexplained).
  - +-40 % FOV: no control passed (0.40-0.93). A wider radius (1.5 km) moved poses 1.6-2 km
    and turned heading 100 deg: not used.
  - **Roll (T19) refused here:** a +-3 deg roll scan let the control p121 pass (0.93 -> 0.24)
    and p79 nearly (0.67 -> 0.37); it fits wrong poses.
  - the 3 unlocated ones (p2, p3, p15) fail the 0.1 lead at 0.01: manual-label list (p15 is
    still measured through the rescue gate).
- **Result** (re-run on the cached outlines; Chicago's new fits at camera heights 2 and 30 m):
  - screen, Miami: fit 30 -> 62, placement queue 71 -> 169, rejected 199 -> 69; Chicago: fit
    153 -> 439, queue 229 -> 719, rejected 1,315 -> 539.
  - photos fitted / kept: Miami 101 / 27 -> 130 / 44 (towers read 502 -> 844; MAE 14.0 -> 15.3 m,
    pair order 0.92 -> 0.91); Chicago 415 / 147 -> 686 / 277 (towers 2,979 -> 5,884).
  - the 38 reviewed: 25 match the user (was 13); 16 of 25 usable no longer rejected, 9 of 13 bad
    still rejected.
- **Refused:** roll in the fit (above); crops of panoramas; a clarity gate on day photos.
- **Source:** user review 2026-10-07 (review items c3_*, `Code/claude/review_feedback/`); scratch `t40f/`.

### 2026-10-07 — A drone reading of the tower behind is untrusted when the satellite says low and a farther footprint explains its top
- **Decision:** `_pano/elevated.py::tower_behind` leaves a trusted drone reading out of fusion when
  both hold:
  - its footprint's confident satellite readings (lean, ls, stereo, multiview; conf >= 0.5; >= 3 m,
    lower is a failed match) are all under 40 m, the reading is over 2x their largest, and no
    confident shadow says >= 40 m;
  - a footprint >= 1.15x farther over its columns (`behind_map`) would have the reading's top row as
    its top at a height its own evidence matches within 25 %: OSM tag, any seed's trusted drone
    reading, or a satellite reading >= 40 m.
  No re-credit: the reading is dropped, not moved to the tower.
- **Why:** each test alone fails; together they flag no right reading (saved states of Cartagena
  seeds 1/4/5/6/7 and Miami seeds 2/3/4 and spheres, every trusted reading):
  - geometry alone (a farther footprint with matching evidence) flagged 6 of 14 tag-correct
    Cartagena readings and 20 of 37 LiDAR-correct Miami ones: adjacent towers explain each
    other's tops, as the refused shared-top-edge check found;
  - satellite-low alone is unsafe on 40-90 m towers whose stereo failed (b0289, tag 90 m: stereo
    and multiview 2 m); 9 of 16 tagged 40-80 m footprints have every confident reading < 40 m;
  - together: 65 readings on 61 footprints, 0 of 14 tag-correct, 3 of 4 tag-wrong, 51 of 141 over
    2x every satellite reading. Published towers unchanged; tagged within 25 % fused 0.73 -> 0.80.
  - Region run v9 vs v8: single 270 -> 223, drone singles > 2x prior 212 -> 164 (all satellite
    readings < 40 m: 155 -> 111); verified_2 38 -> 32; the 7 towers unchanged.
- **Rejected:**
  - re-crediting the height to the tower behind: the tower is chosen because its evidence agrees,
    so the re-credited reading would verify it by construction (29 of 65 towers already read by
    the same seed);
  - the floor-distance cue of the F-SKY26 design (below, Rejected hypotheses);
  - an instance change as a requirement: present on 7 % of the readings over 2x the satellite.
- **Limits:** needs satellite readings, so Miami (none yet) is unchanged; 158 of v8's 212 drone
  singles stay, mostly with no tagged, satellite-tall or drone-read footprint behind them.
- **Supersedes / superseded by:** extends the tagged-tower `_untrust_behind` (commit `9238e00`).
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) step 6.

### 2026-10-07 — A floor count is a lower bound unless ground is seen under the instance
- **Decision:** `floor_bands.InstanceFloors.base_seen` is true only with ground (not building, not
  sky) under the mask; otherwise `lower_bound`. Instances flatter than `MIN_ASPECT` (0.1 rows per
  column) are refused. OSM `building:levels` is not truth for floors.
- **Why:** user review of 16 floor labels: 8 right (including seed_6 inst 63 "16 fl / OSM 2"), 7 too
  few floors with the base or lower floors hidden, 1 a road (seed_6 inst 288, 18 rows over 276
  columns; the flattest accepted building is 0.14). The depth "own podium" test read 0.98-1.03
  under/inside on all 27 instances checked, partial or not, so it does not mark a base seen.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) Progress, 2026-10-07.

### 2026-10-07 — Satellite heights go live in the skyline run, opt-in per site
- **Decision:** the region run reads offline satellite readings when the site sets
  `use_satellite_heights` (on for Cartagena). It never measures or fetches imagery itself.
  - Producer: `city2stl/skyline/scripts/20_satellite_heights.py` (library
    `city2stl/height/satellite/`) writes `runs/satellite/<region>/readings.json`, cached by scene
    names and dates. Free Esri Wayback / World Imagery only, 4 concurrent requests.
  - One reading per footprint and method: lean (reference scene), shadow (all scenes merged to
    one lower-bound reading), pair-consensus stereo, multiview peak (conf >= 0.3, >= 40 m: sigma_log
    0.08 / 0.10 from the Chicago table), and `ls` when lean and shadow agree.
  - Drone fusion: the readings join `fuse_heights` as `sat_*` pseudo-seeds on footprints a drone
    read, at `dist_m = max(100, 3300 sigma_log)`; they can outvote and so dispute a drone reading.
    Only drone readings are published from fusion.
  - Tiers: drone + satellite (>= 40 m) and lean + shadow (> 100 m) count as `verified_2`; two
    satellite readings of one kind never do (`tiers.INDEPENDENT`, unchanged).
  - Untagged rows with no drone reading publish the satellite-only height
    (`withheld:satellite`, tier `single`) only when the best kept reading's sigma_log is <= 0.25;
    otherwise the T41 prior stays and the readings only label the row (`satellite`).
  - Low-rises (user, 2026-10-07): an unverified low-rise publishes its single reading, labelled
    unverified (tier `single`), flagged `prior_disagrees` when it is more than 2x from the prior
    estimate. This was already the behaviour; kept as the rule.
- **Why:** the weights (entries below) were validated on 7 LiDAR cities; the library reproduces
  the validated scratch code exactly (Cartagena, 67 footprints, 7 scenes: 0 differences in lean,
  per-scene shadows, multiview, consensus and combine; readings layer 1225/1225 identical to the
  harness `load_sat`). Opt-in because readings exist only where scenes are cached and solved.
- **Not done / rejected:**
  - publishing noisy satellite-only readings (sigma_log > 0.25: low-rise shadows at conf < 0.7,
    15-40 m shadows) over the prior;
  - the Cartagena-only scratch variant (`s28`/`s29`, behind the old `all_buildings_multi.json`):
    the library ports the generalised v1/w4 code the weights were scored on. On Cartagena the two
    agree within 25 % on 68 % of shadow readings and 52 % of stereo readings (stereo under 40 m is
    dropped anyway).
  - the Cartagena scene geometry comes from scratch `s27` (lean from satellite heights; LG01 sun
    fixed at bearing 320, el 58.07, visually verified), not from `scene.fit_lean` / `solve_sun`
    (ported, not yet run on Cartagena).
- **Consequence (Cartagena v8 vs v7):** same 7 published towers and values (all tagged); towers
  verified 1 -> 5 (Estelar drone seed_1 + seed_6; Gran Bay drone + lean; Portomarine two drones;
  Nautica drone + stereo; Ravello drone + stereo). Region tiers (all rows, v7 via
  `benchmark.label_tiers`): verified_2 3 -> 38, tag 148 -> 143, single 80 -> 270, prior 353 -> 237
  (584 -> 688 rows: seed_6 and hi-res spheres read more footprints). 52 rows publish a satellite
  height. Open: 212 drone `single` rows are > 2x the prior (median 102 m); on 160 of them every
  satellite reading is under 40 m, which suggests tower-behind credit (F-SKY26 step 6), but those
  satellite readings are too noisy to dispute a drone at 400-900 m.
- **Supersedes / superseded by:** extends the two satellite entries below.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) step 7 and 8.

### 2026-10-07 — Two agreeing satellite shadows do not verify a height; satellite readings are weighted by method and height
- **Decision:** a building counts as verified by two images only when independent methods agree:
  two drone seeds, drone and satellite, or satellite lean and shadow on a tall building. Shadow
  agreement across scenes alone does not count. In fusion, a satellite reading gets
  `weight_scale = min(1, (0.15 / sigma_log)^2)` by method and band:
  - lean over 100 m: 1;
  - lean 40-100 m: 0.35-0.9 by confidence;
  - lean under 40 m: dropped;
  - shadow over 40 m: 1;
  - shadow 15-40 m: 0.05;
  - shadow under 15 m: 0.03-0.5 by confidence.
  A shadow is a lower bound and never outvotes a higher photo reading. Several shadow scenes of
  one building count as one reading.
- **Why:** tested on 1,794 LiDAR-confirmed buildings in 7 cities (Prague, Miami, Seattle,
  Madrid, Benidorm, La Défense, Chicago).
  - One satellite reading is within 25 % of truth 37 % of the time; two agreeing readings 64 %.
  - Miami shadows from different scenes agreed on 8 towers over 100 m and 1 was right: a shadow
    cut by a podium or neighbour is cut the same way in every scene. Shadows read 20-40 % low.
  - Lean over 100 m is within 25 % 94 % of the time (Chicago WV03). Where lean and shadow agree
    on a building over 100 m, 26 of 27 are right.
- **Rejected:** counting any two agreeing satellite scenes as verified. On Cartagena that gave
  214 "sat-sat verified" buildings, overstating the result.
- **Supersedes / superseded by:** —
- **Source:** scratchpad satellite_cities/validation.md (2026-10-07), copied to
  `Code/claude/scratch_backup_2026-10-07/`. Multi-scene stereo is unvalidated: Miami's archive is
  near-nadir orthos, and Chicago's scenes are not fetched yet.

### 2026-10-06 — Drone fusion and trust rules for elevated seeds
- **Decision:** `city2stl/skyline/footprint_detect.py::fuse_heights` overrules by a factor `overrule=3`.
  - A reading 3x off the other seeds' agreeing value is dropped.
- **Decision:** `city2stl/skyline/_pano/elevated.py::trusted` accepts a roof reading only with confidence >= 0.5 and distance <= 1000 m.
- **Decision:** the overhead camera fit (`_pano/elevated.py::overhead_pose`) is used when the waterline camera height is < 15 m, the waterline fit fails, or the height is > 130 m.
- **Why:**
  - Gran Bay: seed_4 read 20 m at a depth edge, seed_6 read 170 m from a confident roof fit; the tag agrees with 170.
  - Untrusted readings are wrong (median 200-400 % against tags), so the gates are not relaxed.
- **Rejected:** looser trust gates — coverage has to come from more images, not weaker gates (see Rejected hypotheses, 2026-10-06).
- **Supersedes / superseded by:** —
- **Source:** T43 in [TASKS](../agents/TASKS.md); `city2stl/skyline/_pano/roof_fit.py::fit_roof_heights`.

### 2026-10-03 — Skyline height accuracy is judged on surveyed truth, not on Cartagena
- **Decision:** skyline (street-view) height changes are scored on eight cities with per-footprint
  truth from survey lidar and Google 3D Tiles, cross-checked: Miami, Chicago, Seattle, Boston,
  Benidorm, Paris La Défense, Madrid Cuatro Torres, Prague Pankrác. Cartagena stays a heading and
  registration smoke test only.
- **Why:** user, 2026-10-03: Cartagena "has poor ground truth for building heights, it might
  confuse results". Its truth is 22 OSM `building:levels` × 3.4 m values and no open survey exists.
  Earlier skyline decisions rested on about 20 such buildings.
- **Rejected:** OSM height tags as the yardstick (sparse, floor-count estimates); one truth source
  alone (the user chose both, so new construction and ground-estimate errors show as disputes).
- **Supersedes / superseded by:** —
- **Source:** [F-SKYBENCH](../plans/active/F-SKYBENCH-height-benchmark.md)

### 2026-09-27 — Google 3D Tiles is allowed as a height and geometry source
- **Decision:** Google Photorealistic 3D Tiles stay a supported provider
  (`city2stl/height/providers/google_3d.py::Google3DProvider`), usable for heights, roof geometry and
  as a label source (ray-cast shadow labels, roof-pitch measurement).
- **Why:** user decision 2026-09-27, reversing the earlier "off limits / not approved" note. The
  project owner had already determined in Aug 2026 that Google's terms do not bar this use, the work
  being non-commercial research ([shadow notebook](../research/shadow-heights.md#google-3d-tiles-as-a-label-source)).
  Scope of that determination: research use, not redistribution of a derived dataset or a
  commercial product built on one. Revisit if the project's purpose changes.
- **Rejected:** treating the tiles as barred by ToS — the restriction was never written down
  anywhere in the repo (only listed as an unexplained item in
  [AUDIT-2026-05-17](../history/audits/AUDIT-2026-05-17.md)).
- **Supersedes / superseded by:** supersedes the "Google 3D Tiles probe: not approved, do not start"
  line in claude/memory-bank/activeContext.md and the AUDIT-2026-05-17 "can't be used (ToS)" item. No
  decisions.md entry had forbidden it.
- **Source:** memory project_building_heights.md; claude/memory-bank/activeContext.md (2026-09-27);
  [F-LANDMARK plan](../plans/active/F-LANDMARK-roofs-and-building-parts.md) "Where it stands".

### 2026-09-04 — The WSF3D per-tile endpoint is a 453-tile sample, so a global fallback reads the BigTIFF
- **Decision:** when every one-degree WSF3D tile 404s, `city2stl/height/providers/wsf3d.py::WSF3DProvider`
  falls back to range reads of the single global BigTIFF (`city2stl/height/providers/wsf3d_global.py::read_grid`,
  86.58 m/px, Int16 × 0.1), cached under the same namespace. A failed global read returns the old empty
  raster — a coarse provider that raises would take the whole merge down.
- **Why:** the tiles directory lists only 453 entries; Frankfurt, Rotterdam, Panama, Miami, Dubai
  all 404'd and were silently read as "no settlements". Range reads fetch a few hundred kB per 3.5 km box.
  Frankfurt after the fix: 245 099 cells, p90 33.1 m, max 226.7 m, 21 s cold / 1.4 s cached.
- **Rejected:** opening it with GDAL `/vsicurl/` — the Windows GDAL build's schannel fails the DLR
  chain's revocation check and every CA/verify env var failed; disabling certificate verification
  was deliberately not done. Reads use `tifffile` + `imagecodecs` over a small range adapter.
- **Note:** changes no current output where fine providers exist — `resolution_priority(90)` = 0.236,
  below GBA's 0.55, so a 90 m product never outranks a 3 m one.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry (landed 2026-09-05).

### 2026-09-04 — An OSM height is a floor, not an estimate
- **Decision:** treat tagged OSM heights as a lower bound when comparing sources.
- **Why:** in Old San Juan, buildings with a height or storey tag claim 3.48 m less than the lidar
  survey at the median (MAE 4.99 m), with a consistent sign. Part is the per-storey constant, part
  roof structure the survey sees.
- **Note (re-measured 2026-10-09):** with today's rule (levels x 3.2 m plus a roof level) and
  survey-only truth from `USGS_LPC_PR_PRVI_E_2018` (all 25 Old San Juan tiles read; truth keyed on
  the report ring), the 822 tagged Old San Juan footprints sit 2.04 m under the survey p95 at the
  median (MAE 3.25 m, tol 0.65), down from 3.48 m / 4.99 m under 3.0 m per level:
  - `osm_levels` (527): -2.91 m median vs p95 (MAE 3.84 m, tol 0.56); against the p70 (the
    floor-matched statistic) -1.11 m (MAE 2.52 m, tol 0.72);
  - `osm_tag` height tags (295): -0.75 m vs p95 (MAE 2.21 m, tol 0.81).
  - Condado / Miramar for comparison (877 tagged): -1.17 m vs p95 (MAE 2.91 m), -0.18 m vs p70.
  - Still a floor: the sign holds, but against the floor-matched p70 the gap is ~1 m for levels
    and ~0 for height tags. (F-ARCH made the rule 3.2 m per level plus one roof level:
    `city2stl/heights.py::height_from_tags`, [F-ARCH](../plans/active/F-ARCH-consolidation.md).)
    Script scratch `bench/osj_tag_gap.py`.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 "OSM building heights in Old San Juan run about three and a half metres short".

### 2026-09-04 — A two-stage high-rise router works only fitted per city, and is not landed
- **Decision:** do not ship a low/high-rise classifier that routes tall footprints to
  `max(fine, wsf3d)`. If revisited, fit it per city on that city's own OSM tags.
- **Why:**
  - Oracle bound on Cartagena: MAE 6.43 → 4.62 (tall 24.83 → 14.87); break-even at ~80 % specificity / 60 % sensitivity.
  - Rebuilt on height-independent features (footprint geometry, neighbourhood, satellite
    appearance), cross-validated in Cartagena: 5.15 at sens 0.70 / spec 0.96 — 71 % of the oracle gain.
  - Does not transfer: Miami-trained model gives 6.51 in Cartagena. Miami leans on `log_area`
    (0.564), Cartagena on `halo_dark_p90` (0.608).
  - Gain is 1.01 ± 0.89 m with 60 labels (110 tagged, 20 tall) — one standard deviation. Needs a
    second city with both WSF3D and OSM tags to validate.
- **Rejected:** using Overture/GBA samples (or their presence flags) as features — AUC 0.914 but
  zero gain; routing gain correlated −0.415 with the model's confidence. A model that corrects an
  estimator must never see that estimator's output, since coverage is itself a proxy for "the
  estimator works here".
- **Lesson:** shadow presence is useless as a regressor (MAE 265.84, corr −0.347 at 59° sun) yet the
  best single classifier feature: detection survives conditions that destroy measurement.
- **Supersedes / superseded by:** the router approach is superseded by the [high-rise mask](plate-critic.md#2026-09-04--the-high-rise-mask-is-a-real-signal-with-no-downstream-consumer); the "do not ship the router" decision stands.
- **Source:** decisions.md 2026-09-04 entry (scripts were scratch highrise_*.py, not kept).

### 2026-09-04 — The merge stays per-pixel; Cartagena's tall bias is missing data, not policy
- **Decision:** keep `city2stl/height/__init__.py::merge_height_rasters` per-pixel with priority =
  confidence × `resolution_priority`.
- **Why:** Miami (1492 tagged footprints vs Cartagena's 110) settles it: the same merge reads Miami's
  tall band at +5.18 m bias, MAE 10.36, and wins overall (2.62 vs footprint-max 2.79). Overture covers
  23.9 % of Miami up to 265 m; in Cartagena 1.6 % and GBA tops out at 87 m. Where a
  building-resolution product has data the merge is right; where not, nothing recovers the towers
  without inflating the low city.
- **Rejected (all measured in Cartagena):**
  - lifting `BUILDING_RESOLUTION_LIMIT_M` (10 m) to admit WSF3D — no measurable change;
  - provider reordering — a 90 m source can never outrank a 3 m one, identical numbers;
  - per-footprint priority selection — worse in both bands;
  - WSF3D only on large footprints (area gate) — smooth trade, never a win (1600 m² gate: tall
    24.83 → 17.85, short 2.35 → 4.79, all 6.43 → 7.16); old-town blocks are large and short.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-09-04 — The IQR outlier fence was removed from the height merge
- **Decision:** `city2stl/height/__init__.py::_filter_outliers` keeps only the below-ground and >600 m
  checks; the `iqr_scale` parameter was deleted (not defaulted off) so no caller can reintroduce it.
- **Why:** quartiles over all pixels track the ground (most pixels ≈ 0 m), so Q3 + 3·IQR cut off
  the skyline. Unfiltered: Miami MAE 45.61 → 35.48, corr +0.306 → +0.662, tallest 147 → 233 m (tag
  max 228); Cartagena MAE 34.68 → 20.09, corr +0.345 → +0.773, merged max 36.5 → 190 m.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-08-30 — GlobalBuildingAtlas admitted at confidence 0.55, below Overture
- **Decision:** GBA.LoD1 is a registered provider (`city2stl/height/providers/gba.py::GBAProvider`),
  read from the Source Cooperative GeoParquet mirror, ranked below Overture (0.60).
- **Why:** it is the only globally complete per-footprint source. Against Miami tags Overture scores
  MAE 5.86 / corr +0.912, GBA 25.90 / +0.489 and under-predicts by 67.4 m above 50 m — but pixels
  above 50 m are 18 % of Miami, 0.31 % of Cartagena, 0 % of Tunis, where GBA is actually needed.
  The parquet has per-row-group bbox stats (pyarrow pushdown); it is WGS84 despite the upstream README
  claiming EPSG:3857.
- **Rejected:** upstream HuggingFace GeoJSON / mediaTUM zips (5° tiles of 0.7-2 GB, no index);
  scaling confidence by the `var` column or correcting the tall bias — both would be fitted on one
  city, the same mistake as the per-city `roof:height` calibration.
- **Rule:** score per-footprint sources against the plate's `osm_heights` (same grid, zero
  registration error), never the warped plate. The warped plate reversed this ranking (GBA 22.0 vs
  Overture 39.8, both corr ≈ 0.1); the symptom was the two providers agreeing with each other
  (+0.519) far better than with the reference.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Height provider priority accounts for resolution, and sources coarser than 10 m are refused
- **Decision:**
  - merge ranks on `confidence × min(1, (5 / resolution_m) ** 0.5)` (`city2stl/height/__init__.py::resolution_priority`);
  - `city2stl/height/service.py::enhance_city_data` refuses providers coarser than
    `BUILDING_RESOLUTION_LIMIT_M` (10 m), so a city with only coarse sources keeps the 10 m fallback;
  - the emitted confidence stays unscaled — resolution decides who wins a pixel, not whether it is
    usable, so downstream `min_confidence` gates keep their tuned meaning.
- **Why:** a 30 m nDSM (0.80) outranked 5 m Open Buildings (0.60); it averages roofs with streets
  (median 2.0-2.4 m), which `city2stl/heights.py::enhance_buildings_with_raster`'s 3 m clamp turned
  into a field of 3 m boxes. Plain 10 m fallback beat it in 7 of 7 plate cities. Re-ranking alone was a
  near no-op (nDSM covers 100 % of every plate, Overture 20-61 %); the 10 m gate moved the error.
  Evidence tables: bug 3 in [issues.md](../issues.md).
- **Rejected:** a global shrinkage toward the constant — best k spans 0.00-0.90 across seven cities,
  fitted on plates already documented as untrustworthy referees ([plate-critic](../research/plate-critic.md)).
- **Principle:** for per-building heights, resolution matters and source reputation is no substitute.
  Median absolute error cannot referee "is this estimator informative" — at low correlation a constant
  beats an unbiased noisy estimate by construction; use correlation and best-k.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Height coverage is measured from height_source, weighted by built area
- **Decision:** coverage metric = share of built area whose `height_source` is real OSM (`osm_tag`,
  `osm_levels`) or our estimate (`merged`) vs the 10 m constant (`default`); set in
  `city2stl/heights.py::_fill_heights`.
- **Why:** needs no ground truth, so works for every city, not just the eight with plates. Weight by
  area: a thousand tagged sheds and one untagged tower are not 99.9 % coverage.
- **Rejected:** measuring coverage against a plate.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-28 — Shadow heights stay a research track; the old shadow provider stays deprecated
- **Decision:** do not promote satellite shadow heights to a production provider;
  `city2stl/height/providers/shadow_height.py` stays deprecated.
- **Why (Miami, ESRI 2025-01-08 scene, 41° sun):**
  - accepted estimates: n=17 of 122 footprints, MAE 17.48 m, corr +0.792 vs the plate — coverage is the
    real limit;
  - ray-cast ablation: the length operator is near exact (1.82 m on isolated shadows); shadow merging
    downtown costs ~30 m, imperfect segmentation about as much again;
  - a U-Net segmenter only helps if its receptive field spans the shadow reach
    (`max_height / tan(elevation)`); 2 m/px was the working point; segmentation IoU does not predict
    height error (shown three times);
  - cross-city transfer failed on the class prior; sun elevation has to be an input.
- **Findings worth keeping:** ESRI's `identify` endpoint returns the scene date (Google Static does
  not); sun elevation is solved from the shadow bearing + date; score plates with the `register`
  alignment, not `truth` (the latter correlated −0.017 with OSM tags). Old provider had five disqualifying
  defects (fixed June/10:00 sun, azimuth unused, no footprint association, long shadows rejected,
  heights clamped to 1-50 m).
- **Rejected:** greedy tallest-first occlusion claiming (MAE 62.5 → 75.5); quantile profiles;
  first-local-edge length; fitting acceptance thresholds to OSM tags (fits reference noise — plate and
  tags disagree by 26 m).
- **Supersedes / superseded by:** see the Cartagena failure under [Rejected hypotheses](#2026-09-04--satellite-shadow-heights-work-in-cartagena).
- **Source:** claude/memory-bank/shadow_heights.md → [../research/shadow-heights.md](../research/shadow-heights.md).

### 2026-08-28 — Google 3D Tiles fetch is level-order, depth tied to the output cell, faces sampled, tiles binned one at a time
- **Decision:** in `city2stl/height/providers/google_3d.py`:
  - walk the tile tree level-order, fetching each level's routing documents together;
  - target geometric error = 6 × output cell (`_target_error_m`, `_ERROR_PER_CELL`);
  - sample each face in proportion to its area (`_surface_points`), keeping vertices for exact ridges;
  - download in bounded chunks and fold each mesh into one accumulator (`_accumulate_mesh`).
- **Why:**
  - depth-first: 1505 requests / 131 s for a 1500-tile budget; level-order 31 s, and an exhausted
    budget leaves a coarser but complete surface instead of one detailed corner;
  - finer targets are worse under a fixed budget (Miami: 32 m target → 99 % fill in 28 s, MAE 14.01;
    8 m → 82 % in 240 s, MAE 14.72); 6× sits at the coarse end of the flat range (64/32/16 m →
    13.79/14.01/14.02) so the choice does not rest on noise;
  - vertex-only binning filled 19 % of the grid at coarse levels; face sampling 99 % on the same fetch;
  - holding every mesh until the end made a 4000-tile run die in trimesh's cache hashing.
- **Rejected:** the original fixed 4 m target (from the tiles' own resolution, not the raster's).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsections inside "Vegetation, not bridges" (misfiled there).

### 2026-08-28 — Outside Google's photorealistic coverage the provider returns empty, not flat
- **Decision:** `city2stl/height/providers/google_3d.py::_looks_built` measures relief in block-sized
  windows (250 m); if under 10 % of windows exceed 8 m, return an empty result with a warning.
- **Why:** outside covered cities the same endpoint serves a global base mesh that looks like a
  successful fetch — over Cartagena a surface flat to 1 m for a skyline of 40-storey towers. A wrong
  height that looks like a measurement is worse than a missing one. Measured relief: ≥35 m in every
  covered city, ≤4.3 m in every uncovered one.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection "An empty raster is preferred to a flat one".

### 2026-08-28 — Provider accuracy is scored with the tag filter disabled and split by tagged/untagged
- **Decision:** score every height source on the `nofilter` region report and split every claim by
  whether OSM tags the building.
- **Why:** the skyline tag-disagreement filter makes accuracy against tagged buildings partly
  self-fulfilling. This test established Overture as independent of OSM: better on untagged
  buildings than tagged, and matching no OSM tag exactly.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-28 — Google 3D Tiles ground comes from the mesh, not from a DEM
- **Decision:** ground = a low percentile (5th) of mesh altitudes in a ~200 m neighbourhood
  (`city2stl/height/providers/google_3d.py::_ground_from_dsm`); a caller may pass a DEM to override.
- **Why:** tiles are WGS84 ellipsoidal; project DEMs are orthometric; the geoid separation is tens of
  metres and city-specific (≈ −26 m at Miami), so DEM subtraction adds an uncalibratable bias. Mesh
  ground cancels the datum.
- **Limit:** assumes terrain varies less across the neighbourhood than buildings rise; fails in hills,
  where DEM + geoid model is the honest answer.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-28 — An unmeasured building ground means the camera's ground, not sea level
- **Decision:** skyline heights default a building's unknown ground to the camera's ground plane,
  flagged by `city2stl/skyline/_core/types.py::BuildingRecord` `terrain_known`; elevation lookups
  return `None` on failure, not 0.0.
- **Why:** 0.0 is silently wrong (adds the camera's elevation to every height; plausible at the
  coast, gated away inland). Same-ground cancels the absolute term; error is bounded by local slope.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-05 — STL and OSM height units are deliberately different; do not "fix" them
- **Decision:** STL heightmaps keep raw mesh Z units, OSM heightmaps metres;
  `city2stl/osm_raster.py::derive_scale_m_per_unit` is not applied to the STL heightmap.
  `numpy2stl/src/numpy2stl/registration/compare.py::compare` reconciles them by robust regression
  (`stl_m = stl * height_scale + offset`), recorded as `ComparisonResult.height_scale_used`.
- **Why:** Barcelona's 3.83 vs 100.0 max is expected; the fitted scale should land near 26 m/unit. A
  value near 1.0 means too few buildings matched — a registration symptom, not a units bug.
  Segmentation never compares the two heights and its threshold is Z-scale invariant.
- **Measured:** Barcelona coverage OSM 0.642 vs STL 0.754 — over-segmentation ≈ 0.11, replacing the
  earlier "83.3 % of the raster" figure.
- **Status 2026-09-28:** still holds after F-ARCH (numpy2stl is geo-free; registration takes rasters).
  The F-ARCH 3.2 m/level change affects OSM tag heights only, not this units split.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-05 entry.

## Rejected hypotheses

### 2026-10-17 - Split merged OSM footprints by cadastre constructions (Cartagena)

- **Idea:** cut OSM footprints that hold several cadastre constructions into parts, each with its own floors x storey height.
- **Not testable as briefed:** Cartagena has no survey-LiDAR truth (`benchmark.REGIONS` has no Cartagena; its truth cache is
  900 of 900 "unmeasured"), and its cadastre is the Colombian AMB `Construccion` layer. Truth agreement was never measured.
- **Potential:** 681 of 3,020 footprints (22.5%) hold at least 2 constructions (part at least 15 m2, at least 50% inside,
  stacked polygons merged, Z stripped). Cadastre parts differ by more than max(25%, 2 m) in 39% of the 447 non-PH eligible footprints.
- **Proxy (Open Buildings 2.5D, n=121, not truth):** it orders the parts like the cadastre in 52% (90% CI 40-64%). Per-part
  agreement 36% vs 40% for one height per footprint. No gain.
- **Ownership:** 173 non-PH footprints would get differing claims, but parts are narrower than the pose error at range
  (all parts wider than 9 m at 1 km in 35 of 173; wider than 17 m at 2 km in 6 of 173). In `Cartagena_v18_ctrl` 12 of 42
  drone-read multi-footprints and 4 published rows are affected, all 4 already nearer the tallest part.
- **Decision:** not adopted. Re-test where truth and part-level cadastre both exist (Spanish Catastro INSPIRE building parts
  plus floors with LiDAR: Granada, Benidorm). Scratch: `split/01_splits.py` to `04_ownership.py`.

### 2026-10-17 - Satellite lean/shadow as a gate on pano readings

- **Test:** reject a pano reading when |ln(pano/sat)| of the same footprint exceeds X; footprints with no satellite reading kept.
- **San Juan, 605 readings (38 right):** AUC 0.62 (lean 0.53, shadow 0.44, stereo 0.63, multiview 0.68). X=0.5 keeps 13/38 right and
  drops 384/567 wrong. The satellite height is itself right on 15% of footprints (7% of the 58 with truth of 30 m or more).
- **Honolulu, 38 readings (28 covered):** AUC 1.00; half/half split-tuned: 13/15 right kept, 25/26 wrong dropped. Only 14 right readings.
- **Cartagena:** no survey truth. A 28-row proxy (CTBUH towers, non-PH cadastre bands, tags) is confounded: wrong rows are all low
  plots, where lean reads the neighbouring tower (45-60 m on 3-10 m plots).
- **Decision:** rejected as a standalone gate; worse than the ownership rule (flags 5/13 right, catches 30/40 wrong). Wrong-owner
  cases are mostly low buildings, where lean and shadow are weakest. Revisit with Cartagena survey truth and lean conf >= 0.7 only.
  Offline scene fit may be below production quality, so this is a weak negative. Scratch: `lean/gate.py`, `cart_gate.py`.

### 2026-10-14 - Unmapped cadastre polygons measured in a separate pass as read-only claims (Cartagena, stage A 31 non-PH, stage B 139) leave every OSM row alone but get almost no reading of their own: 0 of 13 trusted readings fit the cadastre band, the rest are tops an OSM footprint owns

- **Hypothesis (user, "Extras as read-only claims", after the in-run replay below failed):** measure the extras in a pass that
  cannot disturb an OSM row, so the 7 towers and every OSM row stay as in the flag-off control, and see whether the extras
  read right.
- **Design, built and run (not committed; scratch `extra2/`: `code/extras.py`, `extras_pass_wiring.patch`, `run_extras.py`,
  `gate19.py`):**
  - the main run is the control's: the extras are never in `building_records`, the seed's footprints, the prior, fusion or
    tower-behind. `measure_elevated_seed` builds its result first, then reads the same pano with the frozen pose and instances
    for OSM footprints + extras and keeps only the extras' readings (`measure_waterline`; `roof_fit.fit_roof_heights` for the
    overhead seed_6);
  - ownership over OSM footprints, cadastre occluders and extras (`ownership.Scene`; an extra's own claim is the non-PH
    floors x 1.5 x 4.13 m, for PH the prior p90). A column is taken when another polygon's claim holds the reading's top
    pixel, or a frozen OSM reading (trusted or not) spans it with its top row within 4 px (x pano scale) of the extra's.
    An extra keeps a reading with a third of its columns free: the taken ones are blanked and it is read again (never
    needed);
  - rows: an `extras` section of `heights.json`, `source: cadastre_extra`, tier `single` or none, never `tag`, `corroborated`
    or `prior`, no cadastre height; PH rows flagged `floors_unreliable`. Drone seeds 1/4/5/6 only (street-level views not
    measured). Stages A and B ran in one main pass: the OSM path does not depend on the extras.
- **Pass marks (fixed before):** the OSM rows of `heights.json` byte-identical to `Cartagena_v18_ctrl`, the 7 towers unchanged,
  no extra reading from a column an OSM footprint owns; stage B >= 10 extras with a trusted reading inside the cadastre band
  (plausibility, not truth), >= 3 of them non-PH, <= 25 % of 8 eyeballed crops obviously wrong.
- **Measured** (`Cartagena_v19_extras`, 0 Google requests, 0 Street View cache misses):
  - hard checks held: the 1,012 OSM rows are byte-identical (same sha1 of the serialized `buildings`; the other top-level
    keys equal, only `extras` added); the 7 towers 202 / 190 / 170 / 188 / 161 / 160 / 156 m unchanged; no extra row above
    `single`, none `tag`; every kept reading has 0 taken columns;
  - stage A (31, all non-PH): 6 extras with any reading (7 readings), 1 `trusted()` (cx0042, seed_1, 79 m on a 4-floor plot,
    band 7-22 m), 0 kept: all 7 share their top row with a frozen OSM reading, 3 also sit inside an OSM claim;
  - stage B (139, 108 PH): 29 extras with a reading (43 readings: seed_6 22, seed_5 9, seed_4 7, seed_1 5), 13 `trusted()`
    on 11 extras (1 non-PH); 42 of the 43 dropped as owned (37 share a frozen OSM top row, 32 sit inside an OSM claim); 1
    kept: cx0003, seed_4, 67 m (10 PH floors, band 28-42 m, outside it), published as `single`;
  - **the plausibility mark fails with or without the ownership rule:** 0 of the 13 raw trusted readings are inside the band
    (55-149 m on 4-27-floor plots; 12 above it, cx0021 75 m below 87.5-101.5 m), so 0 of 10 extras, 0 of 3 non-PH;
  - eyeball (8 pano crops, scratch `extra2/extras_sheet2.png`): cx0003 a real tower top at the pano edge (the one kept);
    cx0007 inside the big tower an OSM footprint reads; cx0017, cx0023 and cx0042 real building tops that an OSM reading
    already holds at the same row (cx0042 is a 4-floor plot reading the 79 m mid-rise); cx0021 a real tower, 83 % of its
    columns share a row with OSM readings; cx0005 the far shore over water; cx0039 a low white block under a sky-level row.
    2 of 8 obviously wrong, 6 real tops of which 5 an OSM row already owns.
- **Cause:** at 400-2,000 m the unmapped polygons stand in front of or inside blocks whose towers OSM maps. A column run that
  ends at the sky ends on a tower an OSM footprint reads, so where an extra's reading is a real top it is also an OSM row's,
  and the columns left to the extra read the shore, the horizon or a roof behind. The in-run replay's 7 used readings were
  this kind (67-149 m on PH plots); here they are held back and what is left is 1 reading outside the band.
- **Rejected:** no module, flag or site setting committed. Not tried (new hypotheses to pre-register): extras read from the
  street-level views (lower and 30-300 m away, where a 4-8 floor block is the nearest top); the extra's own MobileSAM
  instance (the one at its base) as its column set instead of its projected span; a looser rule (free-column share or the
  frozen-top test), which would be tuned on the same 13 readings.
- Scratch: `extra2/` (`run_extras.py`, `run_crops.py`, `gate19.py`, `gate19.json`, `full.log`, `crops/`, `code/`).

### 2026-10-14 - Unmapped cadastre polygons as extra footprints (Cartagena, 139 of >= 4 floors) fail the hard checks: they take 11 trusted readings from OSM rows, and the readings they get are mostly wrong or untestable

- **Hypothesis (plan section (a), "cheap shortcut"):** passing the AMB cadastre polygons that have no OSM footprint to the
  drone / Street View column measurement as extra footprints (ids `cx0000` ..., row `source: cadastre_extra`, height from pano
  evidence only, tiers and trust rules unchanged) measures buildings OSM lacks without touching the OSM rows.
- **Rule, stated before the replay:** a cadastre polygon is unmapped when OSM footprints cover <= 30 % of its area; it is an
  extra when its largest part is >= 60 m2 and its floor count is a valid >= 4 (of two extras overlapping >= 30 % of the smaller,
  the larger stays). Counts (scratch `extra/count.py`): 7,297 unmapped polygons; floors 1-3 4,373 (>= 60 m2; not measured),
  4-7 93 (23 non-PH), 8+ 46 (8 non-PH), no valid count 4; selected 139 (108 PH, 31 non-PH). The plan's "48 of 8+ floors, 8 non-PH"
  holds (46 / 8 at >= 60 m2).
- **Pass marks (fixed before):** the 7 towers and every OSM row unchanged against the same-data control; no existing row loses a
  trusted reading; no extra row has tier `tag`; >= 10 extras get a trusted reading.
- **Control:** the OSM cache entry `f1e495b6` was rewritten on 2026-10-12 22:14 (3,021 buildings, 24 coastlines; v16 had 3,019 and
  23), so ids shifted by +2 and `Cartagena_v16` is no baseline. A flag-off replay `Cartagena_v18_ctrl` on the same data is.
  Replays `Cartagena_v18_extra` (extras among the prior's neighbours, as any footprint) and `Cartagena_v18_extra2` (excluded
  from them, with a drone-segment dump), seeds 1/4/5/6, 0 Google requests. Both give the same OSM-row differences below; the
  prior of the 713 OSM rows that are prior in both is unchanged to 0.05 m in each.
- **Measured** (`Cartagena_v18_extra` against `Cartagena_v18_ctrl`; the extras' drone counts from `extra2`'s dump, the same 7
  readings in both):
  - held: the 7 towers (202 / 190 / 170 / 188 / 161 / 160 / 156) unchanged; no extra row has tier `tag`;
  - failed: 11 of 143 drone readings on OSM rows are gone (b0898 s1 121 m, b0257 s5 130, b0963 s6 132, b1248 s6 174, b1405 s4
    139 ...); 10 OSM rows change tier or height (b0846 single 79 m -> prior, b0106 prior -> single 31 m, b0752 54 -> 44 m); other
    readings move (b1108 s6 63 -> 165 m, b0396 172 -> 177 m); 16 rows (15 prior, 1 single) vanish and 17 appear. Causes in the log: the extras'
    claims and evidence leave more readings out (ownership 175 -> 179, tower-behind 87 -> 93 readings), one OSM reading is
    re-credited to an extra (b0694 -> cx0039), and the seed_6 camera fit moves (189 -> 190 m) because the extras join the
    footprints it fits;
  - extras: 32 of 139 get any pano reading (28 a drone-seen row or a reading left out, 4 a published single, all four
    `withheld:high_rise` from image floor counts, no drone reading). `extra2`'s dump: 29 measured by a drone seed; 11 with a
    reading `trusted()` before the ownership / tower-behind marks, 9 after, 7 used in fusion (cx0003, 0005, 0007, 0017, 0023,
    0036, 0039; all withheld by the single-over-2x-prior rule); 0 corroborated; none is non-PH. Raw `trusted()` meets the >= 10
    mark, used readings (7) do not;
  - plausibility (a check, not truth): the 7 used readings are 67-149 m on PH plots counting 4-14 floors (5.4-37 m a floor; a PH
    count is a unit's); only cx0003 (10 floors, 67 m) and cx0039 (14 floors, 76 m) sit near 4-7 m a floor;
  - eyeball (8 pano crops, scratch `extra/extras_sheet.png`): cx0003 and cx0023 are real tower tops; cx0007's box sits inside
    the big tower an OSM footprint already owns (111 m); cx0005 and cx0017 sit over water or a low block; cx0036 and cx0039 are
    in clutter or on a roof. At most 2-3 of 8 are right.
- **Side finding:** keeping the extras out of the prior's neighbours (`extra2`) leaves them no fallback, so the withhold rule no
  longer applies and six untrusted Street View readings (cx0045 174 m, cx0014 120 m ...) publish as `single`; an extra must stay
  inside the prior, as in `extra`.
- **Rejected:** no module, flag or site setting was committed (module, tests and wiring patch are in scratch `extra/`). Not tried
  (new hypotheses, to pre-register): measure the extras in a separate pass after the main fit, as read-only claims that cannot
  take a top from an OSM footprint or move the camera fit; and restrict them to non-PH plots (31), whose floor counts can bound
  a reading. The separate pass was tried the same day (the entry above this one).
- Scratch: `extra/` (`count.py`, `code/cadastre_extra.py`, `code/test_skyline_cadastre_extra.py`, `cadastre_extra_wiring.patch`,
  `gate18.py`, `extra_segments.json`, run logs).

### 2026-10-14 - A pre-capture OSM filter (drop alleys and narrow streets, keep viewpoints and open sight lines) fails its marks: it drops 8-16 % of requests, not 40 %, and loses 12.5-19 % of the readings that matter
- **Hypothesis (user, 2026-10-14):** "alleys and narrow streets could be filtered upfront". A pure-OSM, metadata-free predicate
  `keep = class not in (alley, narrow) and (viewpoint or a tall footprint visible within 300 m)` would save images without
  losing trusted readings. Classes (precedence viewpoint > alley > narrow > street), thresholds and the pass marks were
  written before any number was computed (scratch `cap/prereg.txt`): alley = nearest way (<= 60 m) is `highway=service`,
  footway / path / pedestrian / steps / cycleway / track, or under 5 m wide; narrow = width (tag, else class estimate:
  residential 7 m) under 8 m, or footprints within 12 m on both sides; viewpoint = bridge / pier / embankment way, ground
  >= 15 m above the median of a 300 m ring, or within 30 m of water or the coastline (or on the sea side); openness = a
  footprint tagged >= 30 m (or >= 9 levels) reached by a sight line over the other footprints (tagged height, else 10 m).
  **Marks:** drops >= 40 % of images; loses <= 10 % of the trusted readings within max(25 %, 2 m) of the survey (Miami, San
  Juan, Honolulu, Fort Lauderdale; nested footprints out); in Cartagena loses <= 10 % of corroborated + trusted readings.
- **Measured** (cached run files and Overpass ways, no Google request; 47 capture locations, 1,093 images; image counts =
  12 spin views per street seed, 15 headings x pitch rows per sphere; Cartagena seeds 1/4/5/6 assumed 30, not logged):

  | test | images | dropped | value lost | mark |
  |---|---|---|---|---|
  | truth cities (30 locations) | 723 | 117 (**16 %**) | trusted truth-agreeing 4 of 32 (**12.5 %**) | >= 40 % / <= 10 %: **fail both** |
  | Cartagena published seeds (17) | 370 | 30 (**8 %**) | corroborated + trusted 34 of 177 (**19 %**; 8.5 % when the three unconfigured near spheres' 223 trusted readings are counted) | **fail** |

  Confusion (location has >= 1 trusted truth-agreeing reading, or in Cartagena a corroborated / trusted one): positive kept 11 (360
  images), positive dropped 2 (75), negative kept 28 (586), negative dropped 6 (72).
- **Yield by class** (truth cities / Cartagena): viewpoint 23 locations, 606 images, 279 trusted, 28 truth-agreeing / 16, 340,
  337 trusted, 29 corroborated; ordinary street 4, 48 images, 0 trusted / 0; alley or narrow 3, 69 images, 145 trusted, 4
  truth-agreeing / 1, 30 images, 28 trusted, 6 corroborated. Street seeds are never `trusted()` and never corroborate
  (`tiers.INDEPENDENT`): their 372 images across all cities gave 0 trusted, 0 corroborated readings, 74 of 546 within 25 % of
  truth. Spheres cost about 14 images per trusted truth-agreeing reading (390 images, 28) in viewpoint class.
- **Cause:**
  - The planners already choose viewpoints: auto standoffs carry a x2 water bonus and drone candidates are ranked over water,
    so 38 of 47 locations are waterfront / sea-side / pier and the filter has little left to drop.
  - The two valuable locations it drops are high drone spheres whose recorded position sits on land above a service lane
    (San Juan d1, 145 trusted, 4 truth-agreeing; Cartagena seed_6, 28 trusted, 6 corroborated; viewed: both are 100 m+ aerial
    views). An OSM class of the ground position says nothing about a camera that is in the air.
- **Candidate screens** (not a mark): of 44 uncaptured drone / probable Cartagena candidates (near + more lists, deduplicated by
  pano), the filter drops 8 (6 footways / service lanes, 1 residential, 1 tertiary), saving 240-360 of 1,320-1,980 images
  (18 %); the 37 of the request count is not recoverable from the files, this is the nearest set. Granada: 37 of 45 user panos
  are footway / pedestrian classes, but Granada has no yield data, so the 82 % cannot be called saving.
- **Rejected:** the filter as registered; no module, flag or planner wiring was added. Not tried (new hypotheses, to pre-register):
  an elevation-aware class for aerial spheres, or cheap 1-image screening before any 30-45 image sphere (already the practice).
- Scratch: `cap/` (`prereg.txt`, `capture_filter.py` prototype, `classified.json`, `analyze.py`).

### 2026-10-14 — The satellite roof-shape GBM (flat vs pitched) does not rank LiDAR plane-fit roof classes: AUC 0.25-0.51 in five cities against a 0.75 mark, not used for panorama heights
- **Hypothesis (plan section (d), E-D1):** `city2stl/roof_model.py` (checkpoint `models/roof_shape_gbm.joblib`,
  threshold 0.90; z18 Esri crops + the OSM `building` / `building:levels` / `height` tags, no panorama) separates
  flat from pitched roofs on footprints that have LiDAR, so a panorama reading could be told "ridge, not eave".
  Until now it was checked only against OSM tags and the Cartagena share, never against LiDAR.
- **Pre-registered** (scratch `shape/roof/prereg.txt`, written before any cell was read):
  - Cells: the cached 1 m survey nDSM inside the footprint eroded 1 m (the survey's own polygon). Check: the
    cell count equals the stored `survey_cells` for 51 % of San Juan footprints and is within +-2 for 96 %;
    the cell p50 is within 0.05 m of the stored `roof_m` p50 for 98 %.
  - Label from a least-squares plane: flat = slope < 5 deg and RMS < 0.5 m; pitched = slope >= 15 deg, or a
    2-plane k-planes fit with RMS < 0.5 m, < 0.6 x the 1-plane RMS, each plane >= 25 % of the cells and the
    normals >= 10 deg apart; else ambiguous (excluded, counted).
  - Footprints: OSM, non-nested (no overlap above 50 % of the smaller footprint: building parts and their
    parents out), area >= 40 m2, `survey_cells` >= 20. Granada separately (REDIAM 1 m); Cartagena has no LiDAR.
  - Mark per city with >= 30 pitched labels: AUC >= 0.75 and false-pitched <= 0.05 at the stored 0.90 cut.
- **Measured** (CPU only, no Google request; 19,925 candidates, 9,527 ambiguous; false-pitched = flat-labelled
  called pitched, recall = pitched-labelled called pitched; model call = p > 0.90):

  | city | flat / pitched / ambiguous | scored | AUC | false-pitched | recall | pitched share: LiDAR label / model call | accuracy / always-flat |
  |---|---|---|---|---|---|---|---|
  | San Juan | 3,552 / 3,093 / 5,531 | 6,645 | **0.421** | 0.034 | 0.028 | 46.5 % / 3.1 % | 0.529 / 0.535 |
  | Honolulu | 179 / 1,098 / 1,041 | 1,277 | **0.508** | **0.061** | 0.088 | 86.0 % / 8.5 % | 0.208 / 0.140 |
  | Miami | 64 / 136 / 317 | 199 | **0.248** | 0.031 | 0.044 | 67.8 % / 4.0 % | 0.342 / 0.322 |
  | Fort Lauderdale | 994 / 648 / 2,488 | 1,640 | **0.473** | 0.028 | 0.070 | 39.5 % / 4.5 % | 0.616 / 0.605 |
  | Granada | 25 / 609 / 150 | 634 | **0.485** | **0.400** | 0.350 | 96.1 % / 35.2 % | 0.360 / 0.039 |

  (Scored = flat + pitched labels with a model row; 3 footprints had no crop. Granada has only 25 flat labels.)
  - Every city has >= 30 pitched labels, so every city is scored; none reaches AUC 0.75 (San Juan 95 %
    bootstrap interval 0.41-0.43). The false-pitched mark holds only where the model almost never says pitched
    (recall 2.8-8.8 % in the US cities), and there its accuracy is within 0.02 of always-flat except in
    Honolulu (+0.07, 14 % flat). At p > 0.5 false-pitched is 0.25-0.48 in the four US cities.
  - Prior shift: the model calls 3-9 % pitched in the four US cities, whose LiDAR pitched share runs 40-86 %;
    35 % in Granada (label 96 %); 3.9 % in Cartagena (2,907 non-nested footprints >= 40 m2 of the 3,021 in the
    cached OSM layer; the plan's 6.4 % was another set). The share the model reports does not follow the
    stock: it is set by the threshold, not by the roofs.
  - Post-hoc, not a change of verdict: AUC against only the cleaner pitched labels (two-plane fits, or slope
    15-50 deg with 1-plane RMS < 0.6 m), reported where >= 30 pitched and >= 30 flat: San Juan 0.45-0.48,
    Honolulu 0.53-0.66, Fort Lauderdale 0.53-0.54.
- **Cause (6 false-pitched and 6 missed-pitched, random draw with seed 7 from the four US cities, rendered with the
  LiDAR cells: `shape/roof/eye_false_pitched.png`, `eye_missed_pitched.png`; 162 flat-labelled footprints have p > 0.9):**
  - False-pitched (6 of 6 are flat by LiDAR: slope 1-3 deg, RMS 0.07-0.46 m): five are San Juan roofs surfaced or
    painted terracotta / red-brown, the colour the model learned as tile; the sixth, in Fort Lauderdale, looks
    like a low-pitch tile roof that the label calls flat (RMS 0.46 m, just under the 0.5 cut).
  - Missed-pitched: only 1 of 6 is a coherent pitched roof (Fort Lauderdale tile roof, one plane at 16 deg,
    p 0.89, a hair under the cut). The other five reach "pitched" through the slope arm with mixed levels inside
    the ring (slope 15-67 deg, RMS 0.8-8.2 m): a tower or a taller neighbour's cells in the footprint, or a ring
    across a tower and its lower roof. The pre-registered slope >= 15 arm is therefore contaminated: it gives
    2,580 of 3,093 San Juan and 986 of 1,098 Honolulu pitched labels, and only 144 and 54 of those have a 1-plane
    RMS < 0.6 m. That noise lowers every AUC but does not explain 0.25-0.42 (below chance); the flat labels
    (slope < 5 deg, RMS < 0.5 m) are clean, and the model still calls 3-6 % of them pitched at 0.90 and 25-48 % at 0.5.
  - Size of the "ridge, not eave" correction on the cleaner pitched set (two-plane, or one plane at 15-50 deg
    with RMS < 0.6 m; informational): ridge minus eave (p95 - p5 of the cells) has a median of 2.3-3.0 m for
    100-300 m2 roofs in San Juan, Honolulu, Miami and Fort Lauderdale (1.8-3.5 m for 300-1000 m2; Granada 3.1 m
    for 100-300 m2, n = 25), and p95 minus the median is 1.0-1.4 m (Granada 2.3 m). Not applied:
    the flag that would trigger it is not reliable.
- **Rejected:** the checkpoint as a flat-vs-pitched flag in LiDAR-truth cities; its per-footprint probability
  is not published, stored or used on panorama heights (the panorama side keeps assuming flat roofs, as
  `roof_fit` does now). The mark failed in all five cities. The "Cartagena 6.4 % pitched" symptom check says
  only that the model rarely answers pitched; it does not say it answers right.
  - Not tried (new hypotheses, pre-register first): a retrain on LiDAR plane-fit labels from these cities
    (the label cleaned of the slope-arm contamination, with terracotta-painted flat roofs as negatives); E-D2
    (a ridge-profile test on column tops) is not run, its mark needs this model to add AUC.
- Scratch: `shape/roof/` (`prereg.txt`, `step1_cells.py` .. `step7_sens.py`, `labels.py`, `result.json`,
  `sens.json`, the per-city `cells_*.pkl`, `labels_*.pkl`, `prob_*.pkl`, and the two PNGs).

### 2026-10-13 — Height steps inside a building (E-C): a two-plateau score from the drone column tops fails its pre-registered mark (AUC 0.52-0.58 in the three survey cities), not built
- **Hypothesis:** the per-column heights `roof_fit._fit` already computes (kept today only as the 35th
  percentile, median and IQR on `RoofMeasured`) show a podium + tower or a stepped roof as two plateaus; a
  score from them (publish-side use: flag a footprint as stepped, lead to parts) would rank stepped
  buildings above single-roof ones.
- **Pre-registered** (plan section (c), scratch `shape/plan.md`; details in `shape/steps/prereg.txt`,
  written before any AUC was computed):
  - Rows: the saved drone states' stored `RoofMeasured` readings (San Juan d1/d2, Honolulu d1/d2, Fort
    Lauderdale d1/d3, Miami martinez / seed_3 sphere / seed_4 sphere), `n_trusted >= 3`, one per footprint
    (most trusted edges). The column heights come from a capture copy of `_fit` (one extra tuple field); it
    reproduces every stored `height_m` exactly (1,818 readings, all equal).
  - Score: 1-D 2-means gap in metres of the column heights (both clusters >= 20 % and >= 3 columns, else 0).
    IQR reported, not for the mark.
  - Truth: positive = survey step (`roof_m` p95 - p50 >= 4 m and p95 / p50 >= 1.3, `survey_cells >= 20`) OR in
    `benchmark.nested_keys`; negative = the other labelled footprints. Miami has no `roof_m`: nested-only
    positives, so its negatives are unreliable.
  - Mark: AUC >= 0.70 with >= 30 positives in >= 2 cities; threshold = max F1 on San Juan, applied to the rest.
- **Measured:**

  | city | n | positives (step / nested only) | AUC gap | AUC IQR | AUC gap, step label only | precision / recall at 1.71 m |
  |---|---|---|---|---|---|---|
  | San Juan | 607 | 62 (49 / 13) | **0.576** | 0.591 | 0.542 | 0.129 / 0.726 |
  | Fort Lauderdale | 285 | 30 (16 / 14) | **0.556** | 0.572 | 0.650 | 0.109 / 0.667 |
  | Honolulu | 44 | 16 (9 / 7) | 0.525 | 0.511 | 0.540 | 0.385 / 0.625 |
  | Miami (nested only) | 96 | 11 (0 / 11) | 0.655 | 0.708 | n/a | 0.137 / 0.909 |

  The mark fails: no city reaches 0.70 with >= 30 positives (San Juan and Fort Lauderdale are the only
  ones with 30 or more). The threshold (1.71 m, F1 0.218 on San Juan) flags 350 of 607 San Juan and 183
  of 285 Fort Lauderdale buildings: a gap that small is the noise floor, not a step. Spearman of the gap
  against the survey p95 - p50: 0.12 (San Juan), 0.09 (Fort Lauderdale), 0.04 (Honolulu).
- **Cause (post-hoc; 6 false positives and 6 missed steps drawn as column-top profiles,
  `shape/steps/false_positives.png`, `missed_steps.png`):**
  - The columns are mostly not this building's roof. Only 45 of 607 San Juan, 73 of 285 Fort Lauderdale,
    18 of 44 Honolulu and 18 of 96 Miami readings are within max(25 %, 2 m) of the survey truth (median
    |reading / truth - 1| 3.1 in San Juan, 3.3 in Fort Lauderdale). On that right-reading subset the AUC
    is no better (0.58 San Juan, 0.49 Honolulu, 0.33 Fort Lauderdale; <= 14 positives each).
  - False positives (6 of 6): the column tops of a 0-34 m building (survey) jump between ~10 m and 70-175 m
    along the footprint (Fort Lauderdale b0351 175 m vs a 34 m truth, b0377 and b0378 at 125 m on 0-4 m
    ground; San Juan b20618 / b20352 / b20376 at 70 m on 7-13 m buildings): the column ray passes the
    footprint and the instance run ends on the tower behind it. A far tower over a low footprint makes a
    perfect "two plateaus", pose error (1-2 deg is 17-35 m at 1 km) making which columns belong to which
    footprint unreliable.
  - Missed steps (6 of 6): a single flat plateau, IQR 0.1-0.8 m, at the wrong height (Honolulu b1263 /
    b1262 at 135 m on a 112 m tower, nested podium; b1492 at 140 m on a 15 m building; San Juan b19992
    at 39 m on a 15 m / 7 m step, b19770 at 23 m on a 19 m / 7 m step). The instance is the block or the
    tower, the lower part is hidden behind it (a far podium behind a near tower, the risk stated in the
    plan) or merged into one MobileSAM instance, so the profile is flat. The nested ones read the
    tower's height (the pre-F-DET5 behaviour already noted for nested rows).
  - Trees were not the failure here: no profile among the 12 is a canopy.
- **Rejected:** the score as registered. A step score needs the part structure, not the column spread:
  per-instance column tops with a hidden-part test (the lower roof behind the higher), after pose and
  ownership are right. Not tried (new hypotheses, pre-register first): the same score only on readings
  that pass the ownership rule (keeps 7-41 % of the rows, too few positives); the container-versus-part
  test of the plan (section (c) item 2).
- Scratch: `shape/steps/` (`extract.py`, `analyze.py`, `diag.py`, `plots.py`, `result.json`, PNGs).

### 2026-10-12 — Coarse pose search for drone Photo Spheres (gravity fixed, cost volume over x/y/z/heading, outline refine from the top 4 minima): fails all three pre-registered marks, not kept
- **Hypothesis:** a drone sphere's local fit (waterline at the recorded position, +-600 m waterline scan,
  then `refine_on_outline` +-150 m) fails when the recorded position is the pilot's, 150-250 m from the
  drone (5 of 15 truth-city spheres, Cartagena near #4 / #18: outline move 171-212 m, misfit 4.6 deg). A
  LoD-Loc-style search would fix it: pitch fixed from the waterline fit (clamped to +-5 deg of the sea-horizon
  value), then a cost volume over x, y +-250 m / 10 m, z +-30 m / 10 m, heading +-15 deg / 1 deg scored on
  the waterline misfit (trimmed mean, pitch fixed) plus LAMBDA x (1 - edge score; MobileSAM instance edges
  against OSM footprint limbs, no OSM height), then `refine_on_outline` from the top 4 separated minima, the
  lowest refined misfit winning. Code (`_pano/pose_search.py`, hook `SKYLINE_POSE_SEARCH=1` in
  `elevated.measure_elevated_seed`) was written and run in the worktree; **not committed**.
- **Pre-registered marks** (scratch `pose/notes.md`, fixed before any test; LAMBDA chosen once from the
  known-good cost volumes): perturbation (Cartagena seed_6, Miami seed_4 p8m18, Honolulu d1 / d2; start =
  known pose moved 50 / 100 / 200 m and turned +-10 deg, 10 trials): recovered within 10 m / 0.5 deg in
  >= 90 % AND tagged-tower readings within 25 % of the known pose's reading for >= 80 %; the 7 failed
  spheres reported (gate verdict, tagged within 25 % of tag); no-harm on Cartagena seeds 1 / 4 / 5 / 6:
  pose within 5 m / 0.3 deg of the saved one, or the tagged within-25 % share improves.
- **LAMBDA** (grid 0 / 0.5 / 1 / 2 / 4 / 8; criterion fixed before looking: share of volume nodes costing less
  than the best node within 20 m / +-10 m height / +-2 deg of the known pose, mean over 7 known-good spheres:
  Cartagena seeds 1 / 4 / 5 / 6, Miami seed_4, Honolulu d1 / d2): means 0.0860 / 0.0855 / 0.0842 / 0.0819 /
  0.0789 / 0.0774, so 8.0 (the grid's top). Honolulu d1 sits at 0.53 for every LAMBDA: 53 % of the nodes
  beat the known pose, its waterline misfit there is 20 deg against 2-3 deg 100 m south of it (the waterline
  and the tower outline disagree by ~100 m), and the edge score at the known pose (0.36) equals the volume's
  median (0.35) with its heading profile peaking 5 deg off.
- **Measured** (4 runs in parallel, one core each):

  | test | result | mark |
  |---|---|---|
  | perturbation, recovered within 10 m / 0.5 deg | **1 of 99 trials** (Honolulu d2 at 200 m); Cartagena seed_6 stopped at 3 trials per offset | >= 90 %: **fail** |
  | perturbation, tagged readings within 25 % of the known pose's (median over trials, shared towers) | 0.50 overall (d1 0.50, d2 0.0-0.09, Miami 0.56, seed_6 0.60) | >= 0.80: **fail** |
  | failed spheres, outline gate | 1 of 7 passes (Sunny Isles d5, at a 11 m camera height); the 6 others still move the camera 184-244 m from the coarse candidate (FL d2 / d5 / d6, near #4 / #18) or fit 2.66 deg (FL d4) | not a mark: 0 of 6 failures cured |
  | failed spheres, tagged within 25 % of tag | FL d2 4 of 29, d4 2 of 6, d5 1 of 6, d6 0 of 1, Sunny Isles d5 2 of 9, near #4 2 of 6, near #18 0 of 5 | |
  | no-harm, Cartagena | seed_1 moved 184 m / -15.2 deg (share 0.67 -> 0.20); seed_4 31.6 m / -1.45 deg (0.71 -> 0.67); seed_5 gate rejects (was a fit); seed_6 unchanged (overhead branch, search not used) | **fail** (3 of 4 harmed) |

  Perturbation by sphere (n / recovered / median error m / median error deg, identical at 50 / 100 / 200 m
  unless noted): Honolulu d1 10 / 0 / 58 / 0.4; Honolulu d2 10 / 0 / 136 / 20.0 (200 m: 1 / 85 / 6.4); Miami
  seed_4 10 / 0 / 51 / 0.4; Cartagena seed_6 3 / 0 / 42 / 1.8. Runtime per search (coarse volume + 4
  refinements): Honolulu 335-415 s, Miami 335-355 s, Cartagena seed_6 1,570 s (1,470 s coarse), failed spheres 486-1,265 s
  (coarse 375-1,132 s).
- **Cause:**
  - The search is start-independent (the same pose from all 10 starts in 5 of 12 cells; the median error
    does not change with the offset except Honolulu d2 at 200 m) but it converges on a pose other than the old path's: the tower-outline
    misfit is flat and multi-modal. At the known poses it is 0.360 (Honolulu d1), 1.045 (d2), 0.546 (Miami);
    the search ends at 0.278 (58 m away), 0.049 and 0.562 (51 m away, a tie). Within 10 m / 0.5 deg of a
    pose the old path settled on is below the cue's resolution, so mark 1 cannot be met by this cue.
  - Ranking by refined outline misfit rewards degenerate fits: Honolulu d2 0.05 deg at 99 m up (known 49 m;
    a coarse candidate at 79 m times the 1.25 height factor), FL d2 0.08 at 133 m, near #18 0.05 over 71
    columns, near #4 0.12 over 88: few tower columns, camera lifted until the towers barely clear 3 deg
    (the Cartagena seed_7 overfit the 130 m overhead rule exists for).
  - The coarse cost does not choose: the edge cue is flat in a dense city (Honolulu: 2,600 footprints
    within 2.5 km, every instance matches some footprint), so the top 4 minima are the waterline's, and
    the tagged outline decides alone, as in the old path but with more places to overfit.
  - Cost: 6-21 min a search on one core (4 runs in parallel) against ~2 min for the old fit.
- **Rejected:** the search as registered. Not tried (would be new hypotheses, to pre-register first):
  ranking the refined candidates by outline misfit with a minimum column count and the waterline height
  as a bound; scoring the volume on the outline of all footprints (area >= 600 m2) instead of instance
  edges; a coarse-to-fine volume (the shore rays cost ~0.1 s a node, 2,601 nodes a search).
- Scratch: `pose/` (scripts, `results/*.jsonl`, `lambda_pick.txt`).

### 2026-10-12 — Soft tags: an implausibly low OSM tag as a soft ownership bound (the false flags in San Juan, Fort Lauderdale, Honolulu)
- **Hypothesis:** the ownership rule flags 26-38 % of right drone readings in the three truth cities
  (Miami 2.8 %) because an OSM tag far below the footprint's untagged prior (a storey count or a
  placeholder in `height`: Honolulu 3-15 m on 83-112 m towers) is taken as a hard cap on its own top;
  making such a tag a soft bound (`tag_soft`: occluder max(tag, p90), own claim max(1.25 x tag, p90 / q99 /
  open), `hard` False) would release the right readings and keep the catches.
- **Pre-registered marks** (scratch `ownfix/notes.md`, before any candidate ran): right-flagged <= 10 % per
  city, wrong-caught >= 50 % per city, Miami unchanged (catch 20 of 28, flag 1 of 36), Cartagena replay
  `Cartagena_v17_own` (7 towers 202/190/170/188/161/160/156 unchanged, non-PH 1-3-floor used <= 2, 10 of 10
  tag-correct kept).
- **Diagnosis** (the 16 right readings flagged in the base rule: San Juan 5 of 13, Fort Lauderdale 6 of 17,
  Honolulu 5 of 19; `ownfix/diag.py`, same walk as `seed_ownership`, trusted readings, survey
  p95 truth >= 20 m, wrong = more than 25 % off):
  - 12: F's own OSM tag far below truth (SJ Plaza Miramar 12.8 vs 73 m; FL b0463 25.6 vs 159, Diplomat 45
    vs 98; HNL b1263 3, b1262 10, b0972 8, b1135 3, Prince Waikiki 15 vs 83-112 m);
  - 3: a nearer claimant's tag far above truth (FL b0361 140.8 vs 86 m over b0342 / Beach Club II; b0214
    108.8 vs 4.8 m over Parker Plaza);
  - 1: F's p90 too low (SJ b21268, read 34.8, truth 44, p90 29; a nearer correct tag holds 64 % of the columns).
  - Tag quality (tagged footprints with truth >= 20 m; n / off > 25 % / tag < 0.5 x truth): Miami osm_tag
    194 / 13 / 1; San Juan osm_levels 203 / 74 / 25; Fort Lauderdale osm_levels 152 / 36 / 11 (all its tags);
    Honolulu osm_tag 532 / 188 / 173. The cause is wrong tags in the new cities, which Miami lacks.
  - Survey-truth counterfactual (survey p95 as a hard bound wherever truth exists): right flagged SJ 1 of
    13, FL 4 of 17 (23.5 %: b0432 / b0463 are right readings whose top a nearer, taller tower claims with
    its true height: geometry, not a bound), HNL 0 of 19; wrong caught 60 / 81 / 85 %. So even perfect
    heights leave Fort Lauderdale above 10 %.
- **Measured** (right flagged / wrong caught; San Juan n 13 / 40, Fort Lauderdale 17 / 16, Honolulu 19 / 20):

  | rule | San Juan | Fort Lauderdale | Honolulu |
  |---|---|---|---|
  | base | 5 (38 %) / 30 (75 %) | 6 (35 %) / 10 (62 %) | 5 (26 %) / 19 (95 %) |
  | soft tag < p10, own claim max(1.25 t, p90) | 5 / 30 | 6 / 8 (50 %) | 3 (16 %) / 19 |
  | same, below p50 | 5 / 29 | 6 / 8 | 3 / 17 (85 %) |
  | soft < p10, own claim q99 | 5 / 29 | 6 / 8 | 3 / 19 |
  | soft < p10, own claim open | 5 (38 %) / 29 (72 %) | 6 (35 %) / 8 (50 %) | 0 (0 %) / 17 (85 %) |
  | osm_levels tags also soft, own open | not run | 0 / 0 (no hard bound left) | 0 / 14 (70 %) |

  - The prior (trained on untagged footprints) has no tower in its upper tail (q99 79 m on a 112 m tower),
    so only an open own claim frees Honolulu. San Juan and Fort Lauderdale are unchanged: their wrong tags
    (12.8 m on 73 m, 25.6 m on 159 m) sit above the footprint's p10, so no prior quantile marks them.
  - Honolulu alone passes (0 / 19 right flagged, 85 % caught). Miami and the Cartagena replay were not run:
    the rule already fails in two cities.
- **Verdict:** refused; `ownership.py` unchanged (the edit was reverted). Marks failed: San Juan 38 % and
  Fort Lauderdale 35 % right flagged (> 10 %); Fort Lauderdale also falls to 50 % caught.
  Tags in these cities are wrong too often (and a levels-derived tag is all Fort Lauderdale has) for a
  tag-based bound to separate a right tower from one behind a low building without independent height
  evidence, which the rule refuses by design. Cartagena's flags: left as they are (validated on Miami and
  the 7 towers; its tag quality is unmeasured), and nothing is turned on for the truth cities. A next try
  needs height evidence independent of the tag (satellite lean / shadow per footprint) as F's own bound.
- **Source:** scratch `ownfix/` (`notes.md`, `diag.py`, `table.py`, `tagsrc.py`, `<city>_<variant>.json` / `.log`).

### 2026-10-09 — The own-corner rule: a trusted top shows a vertical edge at its footprint's projected corners
- **Hypothesis:** a right top shows a sky gap, a non-building label, a MobileSAM instance boundary
  or a depth step at the footprint's projected left or right corner column, at the row its roof
  has there (CBHE).
- **Measured** (Miami states, LiDAR; `ownership.Scene.corner_edges`: column +-3 px, row +-3 px,
  6 rows below, 60 % of rows differing, depth step 1.15):
  - current readings: no edge at 10 of 36 right readings (28 %); an edge at 18 of 28 wrong ones;
  - older reading set: 8 of 39 right flagged, 16 of 51 wrong caught;
  - all 4 known tower-behind cases show an edge (b6428's left corner shares Four Seasons' edge);
  - why: at 1-3 km OSM footprints include low wings (Villa Regina's corner 35 px right of its
    tower), MobileSAM merges a block into one instance, the spin panos give a building a few px.
- **Verdict:** refused as a requirement; kept as a diagnostic (`Scene(..., diagnose=True)`).

### 2026-10-09 — Claims as the run's start (occ[] from footprint geometry)
- **Hypothesis:** `measure_footprints` starts each run above the nearer footprints' claims at their
  upper plausible heights instead of above the nearer measured tops (review §3 item 3).
- **Measured** (Miami, scratch monkeypatch of `measure_footprints`, ownership rule after): trusted
  readings with truth 64 -> 67, within 25 % 0.56 -> 0.45; after the rule 0.74 kept (fails 0.80).
- **Verdict:** refused. The claims act in the ownership walk after the measurement;
  `footprint_detect.py` is unchanged.

### 2026-10-09 — A footprint's prior p90 as evidence that a top is not its own
- **Hypothesis:** an untagged footprint whose reading clears its prior p90 claim, with a farther
  footprint's p90 claim holding the top, read that farther footprint (and likewise a nearer
  untagged footprint's p90 claim hides a top).
- **Measured:** Miami scores the same with or without it (towers tagged). Cartagena, same
  satellite readings: it left out 150 of 400 trusted readings (45 with tags and cadastre only) and
  verified_2 fell 42 -> 34; the lost rows were mostly PH plots of 15-21 floors read 55-81 m with a
  stereo or lean reading agreeing (b0191, b0339, b0411, b0447, b1434, b1505, b0637).
- **Verdict:** refused: the p90 holds 0-22 % of untagged buildings over 60 m; only a tag or a
  cadastre count takes a top away. The p90 still names the re-credit target.

### 2026-10-08 — A render-fit height optimiser over the drone images
- **Hypothesis:** render every footprint as a prism from the saved drone cameras and move the
  heights (and each camera's heading, pitch and height) until the render matches the MobileSAM
  instances and the sky labels; a "handover" step (lower a footprint to its prior, raise the one
  behind it) fixes readings of the tower behind. It would replace per-footprint column readings
  plus fusion.
- **Measured** (scratch `renderfit/`, Cartagena seeds 1/4/5/6, spin and sphere, fit v3; truth = OSM
  height tags plus the published towers):
  - published towers within 25 %: 5 of 7;
  - on the footprints fusion also reads (n 12): within 25 % 0.58, fusion 0.75; median error 8.9 %
    vs 4.4 %; bias -0.34 vs +0.14 (log);
  - where only the fit reads (n 23): 0.52 vs 0.48 for its start heights;
  - as a checker of fused readings (`checker.py`): Miami (LiDAR, n 50, 27 wrong) handover gain
    > 50 flags 9 readings with truth, 7 of them wrong (20 wrong missed); any flag (sky, split,
    handover) 26 flagged, 16 wrong. Cartagena: 3 wrong of 12; the handover flags only wrong ones
    (2-3), but any flag also marks 6 right ones.
- **Verdict:** refused as a height source: worse than fusion where both read, and it loses 2
  published towers. Recommended as a checker only: the handover flag marks readings for review
  (mostly wrong ones in Miami), not an automatic dispute until it is measured on more truth.

### 2026-10-07 — A joint camera position / heading / height fit from feet, water edge and tower outline
- **Hypothesis:** fitting a drone seed's position, heading and height together (observed building
  feet, the water edge, the tower outline) makes the far and overhead seeds usable (the "Open"
  item of the far-drone entry above).
- **Measured** (scratch `jointfit/`; Cartagena seeds 4/6/7, Chicago chi1 and wickerSKY, Miami mia2
  and mia3, Benidorm izalko; held-out tag / survey folds):
  - none of the 6 user-kept seeds became usable;
  - seed_6, the control (its recorded position is right: tags exact), moved 42 m and read 2.2x
    the tags;
  - Benidorm izalko: a constant ~34 m camera-height error plus ~1 deg of pitch, not position.
- **Verdict:** refused. The cues cannot place a camera within the ~10 m these seeds need; a cue
  that could is footprint corners matched to MobileSAM instance edges.

### 2026-10-06 — Relaxing the trust gates to get more coverage
- **Hypothesis:** loosening the confidence and distance gates of `_pano/elevated.py::trusted` adds usable readings.
- **Measured:** untrusted readings are wrong: median error 200-400 % against tags.
- **Verdict:** refused; coverage must come from more images (more seeds, the satellite cross-check T44), not looser gates.

### 2026-10-06 — A shared-top-edge occlusion check
- **Hypothesis:** a trusted reading whose top row matches a farther footprint's top row is an occluder and wrong.
- **Measured:** caught none of the 5 wrong trusted readings and flagged 25 good ones.
- **Verdict:** refused.

### 2026-10-07 — Floor-implied distance alone as the tower-behind test
- **Hypothesis:** an accepted floor instance covering a reading's top, matched to a farther plot,
  shows the run climbed onto that plot (F-SKY26 step 6 design).
- **Measured:** 0 wrong readings matched, 3 right ones did (none of either with the instance change
  required); seeds have 0-38 accepted floor instances.
- **Verdict:** refused; the satellite + evidence test is used instead.

### 2026-10-06 — The "behind" flag for occluded tops
- **Hypothesis:** flag a tower top that lies behind a nearer surface.
- **Measured:** over-flagged (good readings marked).
- **Verdict:** removed.

### 2026-10-06 — Street-fit camera cost from SegFormer road labels
- **Hypothesis:** fit the drone camera to the street network using SegFormer road pixels.
- **Measured:** the cost surface is flat; the road mask is too patchy.
- **Verdict:** refused; the overhead roof fit (`_pano/elevated.py::overhead_pose`) is used instead.

### 2026-10-07 — Benchmark survey truth re-measured with the stat-2 survey p95
- **Decision:** every cached truth record's survey side is re-read with stat version 2
  (`benchmark.refresh_survey_truth`); the cached 3D Tiles side is kept and each record reclassified.
- **Why:** stat-1 survey values depended on the run's footprint grouping (tile bbox set the raster's
  cell size and phase), ČÚZK rasters were read 0.56 x their N/S offset off (Prague, up to 46 m),
  and the EPT project was chosen per tile (commit `3c1a017`). Re-reading only the survey side
  costs no 3D Tiles requests (~81 % of the monthly free cap used).
- **Measured:** confirmed records Prague 24 -> 30, Miami 763 -> 774, other cities -6 to +3;
  survey within 3 m of 3D Tiles Prague 19 -> 25 of 84. Headline: Miami MAE 5.83 -> 5.62 m, within
  25 % 0.556 -> 0.571 (n 162 -> 168); Prague MAE 31.0 -> 33.8 m (n 22 -> 28); other cities move
  under 1.3 m MAE. This is a truth correction, not a model change.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) Progress, 2026-10-07.

### 2026-10-07 — Low-rises: survey LiDAR where free, tiers everywhere, survey-blind benchmark
- **Decision:**
  - A region run publishes the per-footprint survey nDSM height (p95 inside the footprint shrunk
    by 1 m) ahead of the OSM tag, drone and satellite, unless it is suspected stale.
  - Every published row carries a tier: `survey`, `verified_2`, `tag`, `single` or `prior`.
    `single` rows are published and labelled unverified.
  - Near-field street views are studied as a second image for low-rises.
  - The benchmark headline scores the survey-blind answer (`no_survey_height_m`). Survey rows are
    scored only against 3D Tiles.
- **Why:** user choice (2026-10-07: all three options). Satellite data can't verify low-rises (the
  stereo entry above). Free survey LiDAR covers the US, Spain, France, Czechia and Andalusia.
  Scoring survey heights against survey-based truth would be circular.
- **Rejected:**
  - 3D Tiles as a run-time source: ~81 % of the monthly free cap was used by 2026-10-07;
  - counting correlated pairs as verified.
- **Supersedes / superseded by:** extends the 2026-10-03 truth entry.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) addendum.

### 2026-10-07 — Multi-date satellite stereo verifies mid- and high-rises, not low-rises
- **Decision:** stereo (roof shift between dated WorldView scenes) is dropped below 40 m. It is
  weighted 0.6 at 40-100 m and 0.4 above 100 m. A confident multiview peak (conf >= 0.3) and
  stereo + lean agreement get up to 1.0 / ~0.7. Satellite readings take a drone-equivalent
  distance `dist_m = max(100, ~3300 * sigma_log)` instead of a fixed 100 m. The 3300 constant is
  provisional until drone sigma is measured against distance.
- **Why:** Chicago, 882 LiDAR buildings, 5 scenes, 9 pairs.
  - Stereo is within 25 % for 10 % of buildings under 15 m and 34 % at 15-40 m. It reaches 59 % at
    40-100 m.
  - Multiview at conf >= 0.3 is within 25 % 84 % of the time above 40 m (sigma_log 0.08-0.12).
  - Multiview plus lean agreement: 93-100 %, but on only 28 buildings.
  - A 10 m roof shifts 3-9 m between scenes, and low roofs have too little texture to match.
- **Consequence:** satellite data cannot provide the second image for low-rises. Verifying
  low-rises needs near-field imagery (drone or street level) or survey LiDAR.
- **Also found:** the sun-time search started at ~04:00 UTC the next day (the time zone was
  subtracted twice). That, more than missing tiles, broke the Miami sun solve; it is fixed in the
  scratch `w3_geom.py`. Wayback release 2016 WV02 serves the 2017 image (duplicate).
- **Supersedes / superseded by:** extends the 2026-10-07 satellite weights entry.
- **Source:** scratchpad `satellite_cities/validation.md`, backed up to
  `Code/claude/scratch_backup_2026-10-07/`.

### 2026-10-07 — Far and overhead drone spheres are dropped from height readings
- **Decision:** only drone spheres close to their towers with a waterline fit are used (Cartagena
  seeds 1/4/5/6, Miami seed_4). Far drones (0.7-6.6 km) and overhead fits other than seed_6 are
  not read.
- **Why:** their failure is the camera position, which no cue we have resolves.
  - A Photo Sphere's recorded position can be 100 m+ off (a pilot or ground position). On seed_7,
    near towers and a far landmark need headings 18 deg apart (parallax).
  - Heading, height and position then trade off against the ground score, the lowest-foot residual
    and the tower outline. All gave flat or ambiguous minima (seed_7, Chicago wickerSKY, Miami
    Martinez).
  - Benidorm izalko reads 2-3x too tall from footprint distance, not from pitch or scale: the error
    shrinks as 0.96 deg + 1949/d, and the footprint bases project into the sea.
  - seed_6 reads well only because its recorded position happens to be right.
- **Rejected:** a tower-outline heading cue for overhead seeds (ambiguous minima, 90 tower columns
  on seed_6); a camera-height scan (entry below).
- **Open:** a joint position fit from near and far landmarks would be needed. The outline step
  should also reject fits at its search edge or on fewer than about 100 columns (Benidorm's
  150 m move passed a "> 150" check).
- **Source:** scratch `r8/` (2026-10-07).

### 2026-10-07 — Overhead drone camera height from a lowest-foot scan
- **Hypothesis:** scanning camera height (30-800 m) for the best match between predicted and observed
  building feet fixes the overhead fits that sat at the 260 m grid top (Chicago wickerSKY, Miami
  Martinez) without the bases fixed point locking onto nearby edges.
- **Measured:**
  - Cartagena seed_6: 189 -> 198 m, tagged within 25 % 1.00 -> 0.75.
  - Cartagena seed_7: gained a wrong trusted reading (5x its tag).
  - Chicago and Miami do move off the grid top (254 m, 362 m), but residuals are 2-4 deg, and
    height trades against heading on seed_7.
  - Benidorm izalko reads 1.9x too tall even at the scan's 35 m, so its error is the horizon, not
    the height.
- **Verdict:** refused (reverted; patch in scratch `r7/height_scan.patch`). It needs a sharper
  heading cue first (the tower outline); far drones stay unreliable.

### 2026-10-07 — Placing no-GPS Commons photos with trusted heights or LightGlue
- **Hypothesis:** with LiDAR heights (instead of OSM tags) the skyline outline search places Commons
  photos; or DISK+LightGlue matches to the Miami drone panoramas plus PnP on tower blocks does.
- **Measured** on 53-57 Miami photos with known locations (location hidden):
  - outline search with LiDAR heights and EXIF focal: 15 % within 300 m (OSM tags 11 %), true
    place first 5 of 27, median rank 145;
  - a score lead of >= 0.1 over the runner-up placed 5 photos, 4 correctly. That is a precise gate
    for about 15 % of photos;
  - LightGlue: 1 of 55 within 300 m. The references are 2026 drone views 86-260 m up at only 4
    places; the matches land on 2-4 towers, so PnP is degenerate.
- **Verdict:** refused for the pipeline. At most, keep the EXIF + 0.1-lead gate as a high-precision
  placer. Learned matching would need references like the photos (Street View or posed Commons
  photos), not drone panoramas. Labels plus EXIF remain the reliable route (±5 m).

### 2026-10-06 — Placing no-GPS Commons photos by outline search, SIFT, building_groups voting or photo_columns
- **Hypothesis:** a Commons photo with no GPS can be placed by searching its outline, by SIFT matches, by `building_groups` voting, or by `city2stl/skyline/photo_columns.py::photo_columns`.
- **Measured:** the oracle (best possible choice) got only 4/7 headings and 1/17 rank.
- **Verdict:** refused.

### 2026-09-04 — Satellite shadow heights work in Cartagena
- **Hypothesis:** the shadow pipeline fails in Cartagena because of a wrong shadow bearing.
- **Measured:** bearing swept 0-345° in 15° steps over the 400 largest footprints: `near_level` p50
  0.037-0.060 against a 0.17 gate at every bearing, `length_m` p50 168-254 m, 0-10 of ~330 accepted.
  No bearing shows short runs with high near_level.
- **Verdict:** refused; the cause is physical. The ESRI scene was taken at 59° sun elevation
  (10.4° N, 2026-02-10, ~11:00), so shadows are ~0.6 × height in a dark, wet, vegetated scene; the
  other acquisitions are no better. Do not use shadow heights as a reference at low latitude
  without checking the acquisition's sun elevation first.
- **Source:** decisions.md 2026-09-04 entry.

### 2026-08-30 — Granada's export is flat because it lacks height data
- **Hypothesis:** Granada exports flat because OSM has little height data there.
- **Measured:** OSM carries `building:levels` on 4272 of 4476 buildings in the window (95.4 %,
  median 4); only 0.5 % carry `height`. Overture covers 55.6 % of pixels, GBA 31.8 %. The shipped
  3MF had 1026 distinct building heights (1.50-32.50 model units), every level count 1-8 at its own
  height, nothing floored to the 10 m default.
- **Cleared on the way:** the empty 3.5-7.1 m band in the vertex histogram belongs to the terrain
  object (base underside to lowest terrain), not the buildings; the 3.9 m/level in the old
  buildings module sat in dead code (deleted 2026-09-01), so 3.2 m/level is the only rule.
- **Verdict:** refused. Whatever is wrong with a Granada export, missing heights are not it.
- **Also measured:** GBA does not help Granada. It adds 8276 pixels Overture lacks against 70 689
  the other way, and reads 7.5 m lower on the median (corr +0.646), the same downward bias as in
  Miami. nDSM's 100 % coverage there is ground at 30 m. GBA stays a Cartagena/Tunis source.
- **Source:** claude/memory-bank/activeContext.md, 2026-08-30 (moved 2026-09-28).

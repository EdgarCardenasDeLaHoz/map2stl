# Trained roof-shape model

_Last updated: 2026-09-28_

Scope: the flat-versus-pitched classifier that fills `roof:shape` for untagged
buildings, its checkpoint, and how it is wired into the city export. It does
not cover roof *height*, which is still the 30 %-of-building-height rule in
`city2stl/city_model.py::_roof` (when `roof:height` is untagged), nor the registration/plate work.

## Why it exists

The mesh extrudes an untagged building flat. That makes the baseline to beat
**always-flat**, not always-majority: a classifier that cannot beat always-flat
has no upside at all, because the only thing it can change is a correct flat
roof into a spike.

The hand-written signal cascade in `map2stl/city2stl/roof_classifier.py:920`
does not beat it. Measured against OSM tags it scored exactly its
always-majority baseline while never once answering "flat" — Salzburg came back
100 % pitched, Cartagena 99.6 % pyramidal. Two causes:

- **Resolution.** `fetch_region_satellite` caps a job at 400 tiles and walks
  the zoom down until the request fits, so a city-sized box arrives at 0.8 to
  1.2 m per pixel. A ten-metre house is then eight pixels across and no roof
  shape survives. The tier that answered was the one needing the least evidence.
- **Three numbers.** Gradient strength, gradient anisotropy and brightness
  ridge, thresholded by hand, cannot separate a flat concrete roof from a
  pyramid.

## What was trained

Labels are OSM `roof:shape` tags with their footprints, harvested per city.
The production classifier is satellite-based, so a usable training row is only
*(satellite crop, tag)* — any tagged city can contribute, which is what let the
set grow past the eight cities the plate work was stuck at.

Two rules decide which cities count.

**Hold out cities, not rows.** Neighbouring buildings share a block, a builder
and often a tagger, so a random split lets the model recognise the street. The
number reported is leave-one-city-out.

**Coverage, not count, decides whether labels are usable.** Tagged roofs
divided by buildings in the same box. Barcelona tags 189 of 18 674 buildings —
one per cent, and those are the cathedral, the market, the odd one out; a model
trained to score there learns what is remarkable in Barcelona. Brno tags 52 %,
so its labels are simply what Brno is built like. Barcelona and Amsterdam, at
1.0 % and 3.6 %, were exactly the two cities every earlier model scored *below
chance* on. Filtering to ≥ 10 % coverage moved leave-one-city-out AUC from
0.602 to 0.792.

**Train wide, score narrow.** Low-coverage cities cannot score anything, but
they are still real roofs on real imagery and they reach places the
high-coverage set does not (Athens, Lisbon, São Paulo, Tel Aviv). Training on
them and reporting only on high-coverage cities beat training narrow at every
threshold.

**Import stamps are excluded.** Jerusalem returns 1961 `hipped` of 1998, all on
`building=yes`, 98 % also carrying `roof:material` and almost none carrying
`building:levels`. That is one bulk edit stamping a default, not a city;
Jerusalem is built flat-roofed and domed. Left in, it was the only Middle
Eastern city the model saw, and Cairo next door came back 99 % wrongly pitched.

`abs_lat` was ablated and dropped: it contributed 0.004 AUC, and it is a city
label in disguise — a model that has learned what a latitude means cannot be
trusted at a new one, and Granada and Cartagena are the point of this.

## The threshold

Errors are not symmetric. A missed gable loses a ridge nobody notices; a
wrongly pitched concrete block gets a three-metre spike, which is the failure
that started this work. So the cut is chosen to hold the false-pitched rate
near 5 % rather than to maximise accuracy, and it is stored in the checkpoint
rather than rediscovered. It is **0.90**, not 0.5.

## Checkpoint

`map2stl/models/roof_shape_gbm.joblib` — a `HistGradientBoostingClassifier`
plus the metadata production needs:

| key | meaning |
| --- | --- |
| `features` | the 52 column names, in order; must match `roof_features.FEATURES` |
| `threshold` | 0.90, the one-sided cut described above |
| `classes` | `["flat", "pitched"]` |
| `trained_on` | the 45 cities fitted |
| `scored_on` | the 10 high-coverage cities the metrics come from |
| `metrics`, `per_city` | the held-out numbers below |

Held out by city: mean city AUC 0.790, recall 0.377, false-pitched 0.044,
accuracy 0.624 against an always-flat 0.428. Nine of the ten scored cities beat
always-flat; Cairo is the exception at −0.048 and remains the weakest fold.

The model answers flat or pitched only. It was never trained to tell a gable
from a hip, and footprint elongation separates the two at 0.505 AUC — that is,
not at all — so every pitched call is written as `gabled`, the majority pitched
tag (14 726 gabled against 6232 hipped).

## Wiring

- `map2stl/city2stl/roof_model.py` — loads the checkpoint, checks the feature
  order against the extractor, and classifies one ring. A missing checkpoint
  returns `None`, which is a normal state: the cascade still runs.
- `map2stl/city2stl/roof_features.py` — the 52 columns. **Reordering or
  renaming anything in `FEATURES` invalidates the checkpoint**, which will keep
  returning plausible probabilities off the wrong columns. `tests/test_roof_model.py`
  is the only place that catches it.
- `map2stl/city2stl/roof_tiles.py` — per-building zoom-18 crops (0.4 to
  0.6 m per pixel), cached under `map2stl/cache/roof_tiles/`. This is the fix
  for the resolution defect above; it is why the model does not read the
  city-wide image at all. `prefetch_bbox` fills that cache concurrently before
  a run starts: the per-building path is serial, and on a whole city it was
  returning about four tiles a minute against a few hundred distinct tiles, so
  Granada's 19 521 buildings turned an export that should take minutes into
  hours. Warming the cache first moved it to roughly forty tiles a minute, and
  `classify_roof_shapes` calls it automatically once a checkpoint is loaded. A
  failed prefetch is logged and ignored -- `crop_for_ring` still fetches what
  is missing.
- `classify_roof_shapes(..., use_model=True)` asks the model before the signal
  cascade and falls back to it for anything the model cannot measure.
  `satellite_rgb` may be `None` when the model is loaded, since it fetches its
  own tiles; buildings it declines are then reported skipped rather than handed
  to a cascade with nothing to look at. `_stats["by_model"]` counts the calls
  the model made.
- `TerrainSession.classify_roof_shapes(use_model=True)` is the default. Roof
  classification as a whole is still opt-in from the exporter
  (`export_city_3mf(classify_roofs=True)`).

Requires `scikit-learn` and `joblib`; both are in `requirements.txt`, and their
absence downgrades to the cascade rather than raising.

## Acceptance, on cities the checkpoint never read

The checkpoint is refit on every training city, so scoring it against those
same cities measures a model that has read the answers. These are absent from
`trained_on`, harvested fresh, and run through
`classify_roof_shapes(use_model=True)` — the production entry point, so the
numbers cover the wiring as well as the model.

| city | coverage | rows | pitched | accuracy | vs always-flat | recall | false-pitched |
| --- | --- | --- | --- | --- | --- | --- | --- |
| graz_at | 22.0 % | 481 | 73.8 % | 0.713 | +0.451 | 0.617 | 0.016 |
| dresden_de | 44.8 % | 454 | 71.8 % | 0.482 | +0.200 | 0.288 | 0.023 |
| bratislava_sk | 26.2 % | 491 | 41.8 % | 0.796 | +0.214 | 0.537 | 0.017 |
| ljubljana_si | 16.5 % | 486 | 83.1 % | 0.543 | +0.374 | 0.450 | 0.000 |

1912 rows, mean gain over always-flat +0.310, mean false-pitched 0.014, and no
building fell through to the cascade. The false-pitched rate is the number to
watch rather than the accuracy: it is the rate at which correct flat roofs get
a ridge they should not have, and it lands where the threshold was aimed.

**All four are Central European and pitched-heavy, which is the easy half of
the test, and that is not a choice.** A flat-roofed city does not tag its roofs
— why record the absence of a shape? Measured while looking for a control:
Tunis 0 tagged of 2272 buildings, Palermo 35 of 18 016 (0.2 %), Seville 87 of
13 500 (0.6 %), Naples 111 of 10 623 (1.0 %). Cairo, at 46.7 %, is the only
flat-dominant city in the world with usable coverage, and it is already in
training and already the weakest fold. More labels of this kind will not fix
it.

So the flat side is measured by the symptom instead: the share the model calls
pitched in cities with no labels at all. Cartagena is flat concrete and the old
cascade called it 99.6 % pyramidal.

| city | rows | called pitched | old cascade |
| --- | --- | --- | --- |
| cartagena_co | 250 | 6.4 % | 99.6 % pyramidal |
| granada_es | 247 | 32.0 % | — |

Cartagena is the fix, stated plainly: 6.4 % pitched where the cascade said
99.6 %. Granada's 32 % sits well below the 79.7 % the plate-fitted Iberian
curve assumed when it last re-ran, which is a discrepancy worth resolving
before the two are used together.

## Known weak spots

- **Cairo.** The only flat-dominant, high-coverage, non-European city in the
  set, and still slightly net-negative. Flat-roofed cities outside Europe are
  under-represented and it shows.
- **Salzburg** has a 20 % false-pitched rate against a global threshold tuned
  to 5 %.
- **Per-city recall runs 0.097 to 0.617** under one threshold. The failure mode
  is conservative — it misses ridges rather than inventing them — which is the
  right direction, but a per-city cut is unexplored.
- **No gable-versus-hip call.** Everything pitched is written `gabled`.

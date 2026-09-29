# A real-versus-generated critic on plate cities — and why it cannot tune the exporter

> Status: research record (2026-08/09). Outcome promoted as `city2stl/registration/critic.py`; decision: [decisions/plate-critic.md](../decisions/plate-critic.md).

Scope: the experiment that asked whether a network trained to tell a vendor
plate from our OSM extrusion can be used as a search objective for the
exporter's roof constants. It cannot, and the same measurement shows that no
objective built on these eight plates can. This page records the numbers so the
question is not reopened blind.

The experiment code lived in a session scratchpad (critic/: plate_pairs.py warps a plate
into the OSM frame and fits its metres-per-model-unit, patches.py cuts 32-cell
patches and synthesises a roofed variant, train_critic.py trains the critic
leave-one-city-out, sweep.py runs the decisive test) and did not survive. The
measured-error side was re-implemented and promoted as
`city2stl/registration/critic.py`; the learned critic was not (why:
[decisions/plate-critic.md](../decisions/plate-critic.md)).

## The setup

Eight cities have a vendor plate registered against OSM: Barcelona, Bilbao,
Lisbon, Miami, Paris, Prague, Salzburg, Valencia. For each, three rasters on the
same 512x512 OSM grid:

* **plate** — `stl_heightmap.npy` warped through the accepted placement affine
  and scaled by a median-ratio fit against OSM heights on shared built cells.
* **generated flat** — `osm_buildings.npy`, every building a box.
* **generated roofed** — the same, each footprint raised by a distance-transform
  ridge, which is what the mesh's 30 % roof rule does in two dimensions.

2842 patches per class. Cities are held out whole, because patches from one city
share a building stock, a plate batch and a registration.

## The critic separates perfectly and learns nothing useful

Leave-one-city-out, twelve epochs:

| mode | mean AUC | mean plate score | mean flat score | mean roof delta |
|---|---|---|---|---|
| raw | 0.999 | 0.973 | 0.078 | +0.094 |
| detrend (per-patch mean removed) | 0.999 | 0.983 | 0.065 | +0.073 |

Roofs moved the score toward the plate in 8 of 8 held-out cities in both modes,
which reads as success and is not. The movement is 0.005 to 0.021 in most
cities: the sigmoid is saturated, because the classes are separable on something
far stronger than roofs. That something is the renderer. The plate raster came
from `mesh_to_heightmap` sampling a mesh; the generated raster came from
rasterising polygons. Removing the per-patch mean does not touch it, which is
what the detrend row establishes.

## The decisive test: whose optimum wins

A saturated score can still be usable if its argmin lands where the truth's
does. Sweeping roof rise over {0, 0.10, 0.20, 0.30, 0.40, 0.55, 0.70} and
pitched fraction over {0, 0.25, 0.5, 0.75, 1.0}, and scoring each point both by
median absolute difference against the plate and by a critic that never saw the
city:

| city | truth argmin (rise/frac) | critic argmin |
|---|---|---|
| barcelona_spain | 0.10 / 0.25 | 0.70 / 0.75 |
| bilbao_spain | 0.20 / 0.75 | 0.70 / 1.00 |
| lisbon_portugal | 0.00 / 0.00 | 0.70 / 1.00 |
| miami_fl_usa | 0.20 / 0.50 | 0.70 / 1.00 |
| paris_france | 0.10 / 0.25 | 0.70 / 1.00 |
| prague_czech_republic | 0.00 / 0.00 | 0.70 / 1.00 |
| salzburg_austria | 0.00 / 0.00 | 0.70 / 1.00 |
| valencia_spain | 0.20 / 1.00 | 0.70 / 1.00 |

Rise agreed in 0 of 8. The critic's optimum is the largest rise and the largest
pitched fraction on the grid in every city, including the three whose truth
curve is exactly flat. It is monotone in "more relief", not in "correct relief":
any texture at all reads as less like a box, so a search against it would drive
every building to a 70 % roof.

This is the failure the AUC could not have revealed, and it is not a resolution
problem to be fixed with better renderer parity.

## The plates cannot referee roofs either

The truth column of the same sweep is the more important result. Median absolute
height error against the plate, by roof rise:

| city | r=0.00 | r=0.10 | r=0.30 | best |
|---|---|---|---|---|
| barcelona_spain | 5.08 | 5.06 | 5.07 | 5.06 |
| bilbao_spain | 2.90 | 2.77 | 2.76 | 2.73 |
| lisbon_portugal | 3.39 | 3.39 | 3.39 | 3.39 |
| miami_fl_usa | 11.29 | 11.08 | 11.25 | 10.89 |
| paris_france | 4.13 | 4.13 | 4.13 | 4.13 |
| prague_czech_republic | 3.86 | 3.86 | 3.86 | 3.86 |
| salzburg_austria | 3.33 | 3.33 | 3.33 | 3.33 |
| valencia_spain | 4.75 | 4.57 | 4.54 | 4.53 |

Roofs move the error by 0.02 to 0.4 m against a floor of 2.9 to 11.3 m, and
three cities do not move at all. **That floor is wrong — see the correction
below.** The conclusion it supports is not: the corrected floor is larger in six
of eight cities, so a roof sits even further below the noise than this table
suggests. At the 5 to 6 m cell size these grids carry, with a registration whose
own residual is several metres, a roof is smaller than the noise. The 30 %
constant is neither confirmed nor refuted here; it is invisible.

Any future attempt at fitting roof geometry needs a reference the plates cannot
provide: a source at roughly a metre per pixel, per building rather than per
cell. That is the same conclusion [roof-shape-model.md](../reference/roof-shape-model.md) reached from the
other direction, where per-building zoom-18 crops replaced the city-wide image.

## What was tried and rejected before writing this off

Rendering our own 3MF over each plate's exact bbox and putting it through
`mesh_to_heightmap` removes the renderer confound in principle. Measured on
Barcelona, it costs more than it buys: `terrain_residual` on our own mesh places
the local ground partway up the buildings in a dense city, and correlation with
the plate falls from 0.434 (polygon raster) to 0.210, with median error rising
from 5.08 m to 8.68 m. Matching the renderer while destroying the height signal
is not progress. The generated 3MFs were kept, since they are the coverage
deliverable in their own right.

## Correction (2026-08-30): the generated raster was stale

Every number above that involves the generated surface was measured against
`map2stl/tools/align_tool/data/<city>/osm_buildings.npy`, and that file predates both the OSM
height tagging and the skyline height enhancement. Checked against the current
cache it has a median of exactly 10.00 m in all three height-source groups and
not one cell at 3.0 m — it is a field of untagged 10 m boxes, not the exporter's
output. The "floor of 2.9 to 11.3 m" is therefore not a statement about the
plates or about our heights; it is what you get for comparing a vendor model
against a constant.

Rebuilt from today's cache with proper polygon fill (scratch critic/fresh_raster.py),
the same median absolute error is:

| city | archive raster | today's cache |
|---|---|---|
| barcelona_spain | 5.08 | 4.87 |
| bilbao_spain | 2.90 | 6.22 |
| lisbon_portugal | 3.39 | 4.49 |
| miami_fl_usa | 11.29 | 9.94 |
| paris_france | 4.13 | 4.85 |
| prague_czech_republic | 3.86 | 3.92 |
| salzburg_austria | 3.33 | 4.03 |
| valencia_spain | 4.75 | 8.18 |

The cities that got worse are exactly the ones with a large share of enhanced
(`merged`) heights — Bilbao 70.5 %, Valencia 56.5 %. That is a real production
defect, written up as bug 3 in `map2stl/docs/issues.md`; it is not an artefact
of the rebuild. Two things to know if the rebuild is repeated:

* **Row 0 is the south edge** in the align tool's grid, not the north. A
  north-up raster overlaps the archive's built mask at IoU 0.27; flipped, 0.89.
* Polygons must be filled, not bounding-boxed. The bounding-box shortcut is fine
  for labelling which source a cell came from and is not fine for measuring
  error, because it inflates every footprint into its neighbours.

What the correction does not change: the critic result. That sweep compared a
roofed variant against a flat variant of the *same* raster, so an offset shared
by both cancels. Rise still agreed in 0 of 8 cities, and the critic is still
monotone in "more relief".

What it does change: any analysis that split the archive raster's cells by
height source, or fitted a shrinkage factor to it. Those were labelling cells
from today's cache while reading heights from a surface that predates the
labels, and none of their conclusions stand.

## Where the generated model is actually weak: height-source coverage

The plates cannot referee roofs, but the exporter already records what it knew
about each building. Every feature carries a `height_source` with four values in
three tiers:

* `osm_tag`, `osm_levels` — a real OSM height, tagged or derived from storey
  count. Free and trustworthy.
* `merged` — no OSM height, so the skyline height estimator supplied one
  (`enhance_buildings_with_raster`, which only ever overwrites `default`).
* `default` — nothing at all: the 10 m constant, an identical box.

That is a coverage measurement needing no plate, and it localises the weakness
better than any error metric. Built area by source, area-weighted because a
thousand tagged garden sheds and one untagged tower block are not a 99.9 % success:

| city | osm | merged | default |
|---|---|---|---|
| cartagena_co | 7.6 % | 0.0 % | 92.4 % |
| granada_es | 95.6 % | 4.0 % | 0.4 % |
| barcelona_spain | 83.9 % | 7.0 % | 9.1 % |
| bilbao_spain | 17.3 % | 74.8 % | 7.9 % |
| lisbon_portugal | 34.1 % | 32.9 % | 33.1 % |
| miami_fl_usa | 73.3 % | 0.0 % | 26.7 % |
| paris_france | 64.5 % | 32.2 % | 3.3 % |
| prague_czech_republic | 86.9 % | 11.9 % | 1.3 % |
| salzburg_austria | 56.8 % | 35.0 % | 8.2 % |
| valencia_spain | 62.6 % | 32.9 % | 4.5 % |

Read together with the corrected error table above, this explains the pattern
exactly: the cities whose error rose when the stale raster was replaced are the
ones with the largest `merged` share. Miami has no `merged` cells at all because
US bboxes skip enhancement (`source_name: "osm_only_us"`), and its 26.7 % of
default area is the reason it carries the largest error in the set.

Cartagena is the case the coverage work was aimed at: 92.4 % of its built area
is a flat 10 m box, so no roof model, no scale fit and no critic can improve it.
Only a height source can.

Measured with the scratch critic/coverage.py, which reads the server's OSM cache off disk
and clips by centroid — the cache is keyed by hash rather than by city name, and
a payload can cover a larger box than the one asked for, so clipping rather than
whole-file tallying is what keeps a neighbouring district's tagging out of a
city's numbers.

# Roof shape research (shadow-heights notebook, part 2)

> Status: historical research notebook, Aug 2026 (moved from claude/memory-bank/shadow_heights.md, lines 478-787).
> Conclusions live in [../decisions/roofs-landmarks.md](../decisions/roofs-landmarks.md); shadow part in [shadow-heights.md](shadow-heights.md).

## Roof shape: what OSM can and cannot supervise — 2026-08-29

Asked whether the shadow pipeline could be fine-tuned for roof shape against
existing OSM heights. It cannot, for a reason worth recording rather than
rediscovering: an OSM height is one scalar per building and a roof shape is a
profile. The tag that would supervise shape is `roof:shape`, not `height`.

**Cartagena has none of the three inputs this would need.** Of its 40,917 OSM
buildings, 63 carry a real `height` tag, 10,144 carry `building:levels`, 30,710
carry nothing and are filled with a constant, and **zero** carry `roof:shape`.
Google's photorealistic 3D coverage there is not thin but absent:
`g3d_prod_cartagena.npy` and `g3d_cartagena.npy` are both 0.0% covered, and the
Bocagrande probe that does return data (94.6%) tops out at 4.7 m, which is
terrain with no buildings on it in a district of high-rises.

**`city2stl/roof_classifier.py` already exists and is degenerate.** It is 1215
lines, accepts a height raster, and claims 75 to 85 per cent accuracy with one
and 55 to 65 without. Nothing calls it — `terrain_session.classify_roof_shapes`
wraps it and has no callers — and `test_roof_classifier.py` is 714 lines of
synthetic smoke tests that never compare against a real tag. Run on Miami's 122
footprints for the first time:

| input | output |
| --- | --- |
| RGB only | 121 of 122 called `pyramidal`, 1 `gabled` |
| DSM + RGB | 60 hipped, 28 gabled, 22 pyramidal, 10 skillion, **2 flat** |

Two flat roofs in downtown Miami. Both runs scored flat-versus-pitched accuracy
0.745 against a DSM reference whose majority class is also 0.745: the classifier
never says flat, so it scores exactly the base rate and carries no information.
The claimed accuracy is the majority-class rate.

**The DSM does carry roof shape; a percentile spread is the wrong way to read
it.** Thresholding the 10th-to-90th percentile spread called 76 of 102 Miami
footprints pitched, which is wrong for a downtown. Fitting a plane to the
interior and reading its slope instead gives 39 of 82 flat below 5 degrees, and
median slope 5.91 degrees. The difference is clutter: 48 of 82 have a plane
residual above 40 per cent of their spread, which is a flat roof with mechanical
plant and a parapet, not a pitch. Use the plane fit.

**Resolution is the binding constraint in Cartagena, not the model.** Its median
building is 88 m² and two levels — roughly 9 by 10 m. At 2 m a pixel that is 4 by
5 pixels and no roof is recoverable at any accuracy. ESRI native imagery at about
0.5 m a pixel makes it 19 by 20, which is workable. Any roof-shape attempt there
has to start by fetching at native resolution rather than the 2 m grid the shadow
work standardised on.

**The one legitimate use of the OSM heights.** `building:levels` gives an eave
height and the shadow gives a total height, so their difference is roof height.
Per building this is useless — the shadow operator's error is about 40 m and a
roof is 3 to 8 m — but the median over thousands of buildings would set a
city-wide roof-height prior, which is more than the mesh generator's current
constant.

A registration check ran along the way and cleared: `shadow_azimuth.lonlat_to_px`
is north-up and `plate_height_truth.lonlat_to_px` is south-up, each correct for
its own raster. Comparing the Google DSM against the registered plate with the
right convention gives MAE 15.64 m and correlation +0.862 over 46 footprints,
consistent with the 14.02 m recorded for Google 3D Tiles, so the DSM is sound
where it exists.
### High-resolution imagery: the resolution limit lifts, the geometric cue does not

The roof-shape attempt died at 2 m a pixel because Cartagena's median building is
about 9 by 10 m. ESRI World Imagery reaches zoom 18 there, 0.587 m a pixel at
this latitude, and `satellite_image.py` already fetches it — `_MAX_ZOOM` is 18
and `_choose_zoom` walks down only to respect the 400-tile cap, so a window of
about 1.2 km comes back at full resolution without touching the module.

**The detail is real, not an upsample.** Fetching the same walled-centre window
at three targets and comparing Laplacian variance on a common grid gives 38.2 at
zoom 16, 380.7 at zoom 17 and 5059.7 at zoom 18 — roughly a decade per level.
Cubic interpolation of a coarser source would flatten instead.

At that scale 1,465 footprints fall wholly inside a 1.2 km window, median 616
interior pixels and a short side of 18.2 px (10.7 m). **84 per cent are at least
12 px across**, against 4 to 5 px at the 2 m grid the shadow work standardised
on. Ridge lines, two-tone roof faces, courtyard rings and rooftop plant are all
directly legible by eye.

**Scoring needed a reference, since Cartagena has no `roof:shape` tag.** Forty-two
individual footprints were sampled — filtered to a 10 to 40 px short side and 150
to 3000 px area, because OSM here often draws one polygon around a whole
courtyard block — and labelled by eye. To check that the labels were not simply
reading terracotta colour and calling it pitched, the same 42 were relabelled
from a greyscale histogram-equalised sheet. **The two label sets agree on 41 of
42**, so clay tile is separable from concrete on texture alone and the colour
labels are not circular. The greyscale set is the reference below.

| cue | flat | pitched | AUC | best accuracy |
| --- | --- | --- | --- | --- |
| redness, CIELAB a* | 0.00 | 13.50 | **0.958** | 0.952 |
| gradient coherence | 0.21 | 0.29 | 0.608 | 0.738 |
| ridge contrast | 0.51 | 0.48 | 0.494 | 0.738 |
| always flat | | | | 0.714 |

**Roof shape here is recoverable from material, not from geometry.** Redness
separates almost cleanly, and the physical reason is sound independently of the
labelling: clay barrel tile has to be laid on a pitch to shed water, and a
concrete slab is flat. Both geometric cues fail — ridge contrast at 0.494 is
chance, and gradient coherence scores exactly the majority rate. That is the
same machinery `roof_classifier.py` leans on, measured on imagery three times
finer than it was ever given, and it still carries nothing.

Two limits to carry forward. The 42 labels are one person's reading of imagery,
so the figures are a feasibility check rather than an accuracy, and the cut on
redness is fitted in sample. And the material-to-pitch implication is a fact
about this building stock: it should transfer to other colonial Caribbean and
Latin American centres, but it says nothing about a city roofed in dark membrane
or metal, and it needs checking once against a city that actually publishes
`roof:shape`.

Cached: `cartagena_centro_hires.npy` (2048x2051 at zoom 18) with
`cartagena_centro_hires.json`, sample indices in `cartagena_sample_idx.npy`,
scripts hires_probe.py, hires_foot.py, hires_sample.py, hires_score2.py.

## Roof pitch for Granada: the plates cannot teach it, Granada's own tags can — 2026-08-29

Asked to train on the cities that have STL plates, Barcelona among them, so that
roof pitch could be rendered on top of the base height in Granada. The plates
turn out to be the wrong teacher, and Granada turns out not to need one.

**The plates hold real surfaces, not extrusions.** Inside the built area a plate
takes 15,000 to 22,000 distinct height values with a median gap of 0.001 m, so
these are continuous meshes rather than footprints extruded to a storey count.
Within a 3x3 window lying wholly inside the built area the height spread is 1.3
to 2.2 m in the European cities, which is the right order for a pitch.

**But that relief is not roof shape.** Two plate cities publish `roof:shape` and
both say so directly. Fitting a plane inside each tagged footprint and reading
its slope:

| city | m/px | flat | pitched | slope, flat | slope, pitched | AUC |
| --- | --- | --- | --- | --- | --- | --- |
| salzburg_austria | 4.85 | 20 | 172 | 9.34 deg | 12.74 deg | 0.596 |
| prague_czech_republic | 5.88 | 20 | 38 | 11.59 deg | 13.50 deg | 0.528 |

Chance in Prague, near it in Salzburg, and in both the roofs tagged flat already
measure 9 to 12 degrees of slope, which no flat roof has. The cell size explains
it: a tagged footprint covers about sixteen pixels, four across, and a plane fit
over four pixels reads the bilinear warp smearing the step between neighbouring
buildings rather than any roof. The spread cue is worse than chance in Prague, at
0.339. **No source `.stl` exists anywhere in the repo**, so the 512x512 raster
cannot be re-cut finer — this is the ceiling, not a setting.

**Granada publishes its own labels.** Spain tags `roof:shape` on 0.1 to 1.0 per
cent of buildings in a 2 km box, against 9.9 per cent in Prague and 35.0 in
Salzburg. Over the whole metropolitan area that still comes to **957 tagged
roofs**, of which 748 fall in the flat-or-pitched classes and 640 sit inside
imagery that could be fetched. Granada also carries `building:levels` on 4,268 of
the 4,476 buildings in the old-town window, so the base height was never the
missing part. Only the roof was.

**Cues, on 640 real tags at zoom 18 (0.476 m/px).** Every cue on its own scores
at or below the majority rate of 0.806 — the same trap `roof_classifier.py` fell
into — so only the AUC is meaningful alone:

| cue | flat | pitched | AUC |
| --- | --- | --- | --- |
| footprint area, px | 1359 | 496 | 0.752 |
| redness, CIELAB a* | -2.50 | 4.00 | 0.724 |
| brightness spread | 33.68 | 23.92 | 0.718 |
| gradient magnitude | 100.02 | 75.79 | 0.684 |
| gradient coherence | 0.25 | 0.36 | 0.623 |
| two-face brightness split | 0.32 | 0.46 | 0.621 |

**Redness does not transfer at Cartagena's strength.** It scored AUC 0.958 there
against 42 hand labels and 0.724 here against 640 real tags. Granada roofs are
terracotta whether flat or pitched, so material stops implying pitch. Read the
Cartagena figure as a fact about that city's stock and its small hand-labelled
sample, not as a general result.

**Combined, under five folds grouped by imagery window** so no model can pass by
learning one neighbourhood and being tested on its neighbours:

| features | AUC | balanced accuracy | accuracy |
| --- | --- | --- | --- |
| footprint shape only | 0.833 | 0.711 | 0.836 |
| imagery only | 0.839 | 0.687 | 0.811 |
| both, gradient boosting | **0.913** | **0.783** | 0.872 |
| always pitched | 0.500 | 0.500 | 0.806 |

The two halves are worth about the same alone and clearly more together, so the
imagery earns its place rather than rediscovering that houses are small and
blocks are flat. Balanced accuracy 0.783 is the number that matters: unlike
`roof_classifier.py`, this model does say flat, and is usually right when it
does.

**The roof solid is the distance transform of the footprint interior times
tan(pitch).** Distance to the nearest wall is exactly the height of a hip roof
above its eave, so no ridge direction is needed. Take the transform over the
**union** of the pitched footprints rather than building by building: Granada's
old town is terraced, and a per-building transform gives every house its own
pyramid with a valley at each party wall, where the union lets one ridge run the
length of the row. Streets stay separate because footprints across one do not
touch.

Applied to the old-town window: 4,295 buildings, 3,421 called pitched (79.7 per
cent, against the 80.6 per cent tagged prior), mean base 15.4 m, roof mean 1.27 m
over the pitched area and capped at 6.0 m, adding 4.1 per cent to mean built
height.

**The one unmeasured parameter is the pitch angle**, set to 26 degrees from
Andalusian clay-tile practice. Nothing here measures it: the plates cannot
resolve it and OSM Granada has zero `roof:height`. A Google 3D Tiles probe over
the window was still fetching when this was written; if it returns coverage —
Cartagena's was 0.0 per cent, so that is not assured — a plane fit inside the
tagged footprints would measure the angle directly and replace the constant.

Scripts: plate_relief.py, plate_roofshape.py, granada_osm.py,
granada_cues.py, granada_cues2.py, roof_features.py, granada_ablate.py,
granada_roof_union.py. Outputs: `granada_base_m.npy`, `granada_roof_m.npy`,
`granada_total_m.npy`, `granada_roof_calls.json`, `granada_layer.json`,
`granada_roof_preview.png`.

### The pitch angle, measured

Google 3D Tiles does cover Granada, unlike Cartagena's 0.0 per cent: the probe
returned 81.0 per cent fill at 1.52 m/px after nineteen minutes. Ground-normalised
against a six-pixel skirt it tracks the OSM base at correlation +0.591, MAE 4.2 m,
which also fixes the row order -- the flipped orientation gives +0.123.

Regressing height on distance-to-wall inside 1,647 footprints gives a pitch, and
binning that by footprint half-width separates a real roof from photogrammetric
edge smear. Smear rounds the lip of a building over a fixed distance, so it holds
its rise and loses its angle as buildings widen; a roof holds its angle and gains
rise.

    half-width      n    pitch deg   ridge rise m
      3 - 4 m     130       18.9          1.12
      4 - 5 m     645       23.9          2.11
      5 - 6 m     254       26.6          2.74
      6 - 8 m     418       29.2          3.75
      8 - 12 m    166       26.1          4.54
     12 - up       34        9.4          2.01

The angle holds near 24 to 29 degrees while the rise grows fourfold. That is a
roof. The 12 m-and-wider bin falling to 9.4 degrees is consistent rather than
contrary -- those are the large blocks, which in Granada are flat-roofed.

So `PITCH_DEG = 26.0` is no longer an assumption. The layer keeps the number it
already had, now with a measurement behind it.

### The mesh cannot make the flat-versus-pitched call, and that is not a refutation

The same mesh scores AUC 0.488 against the classifier over all 1,647 footprints,
and 0.470 over the 759 where the fit is good enough to gate on. Model-flat roofs
measure 26.5 degrees in it, model-pitched 25.1.

That looked at first like the classifier failing an independent test. It is not,
for two reasons. The mesh has no ground truth behind it in this window -- only
nine tagged roofs fall inside the probe box -- whereas the classifier scores AUC
0.913 against 640 real `roof:shape` tags under folds grouped by imagery window.
And a per-building pitch at 1.52 m/px is a fit over a footprint about ten pixels
across, which is the same resolution problem that sank the STL plates, one order
milder. The width binning above works precisely because it aggregates; the
per-building number does not survive alone.

Granada's old town is also pitched almost throughout, so there is little for
either estimator to discriminate here. Both put the pitched share near 80 per
cent (mesh 74.2, classifier 82.3) and disagree on which buildings, agreeing 65.2
per cent of the time.

A build that let the mesh set each building's angle was written and run
(granada_layer_v2.py) and then abandoned: it changes the numbers (roof mean
1.42 m, +4.8 per cent) without any evidence it changes them for the better. The
mesh sets the city's angle; the classifier keeps the call. If this is revisited,
the missing piece is a probe over a metro area carrying enough `roof:shape` tags
to referee the two directly.

### Correction: Granada does have STL files, and they still carry no roofs

An earlier note in this file said no `.stl` exists anywhere in the repo. That was
wrong -- the search covered the `Code` tree and not the sibling data folders.
`..\Cities\granada_stl\` holds `Granada_buildings.stl`,
`Granada_buildings2.stl`, `granada_topo.stl` and a 91 MB
`granada_topo+osm.obj`, and the plate exports come from
`_align_tool/data/<city>/stl_heightmap.npy` for barcelona, bilbao, lisbon, miami,
paris, prague, salzburg and valencia.

The correction does not change the conclusion, and it improves the reason behind
it. Face normals settle what a mesh contains without any resampling: an extrusion
has walls at normal z = 0 and roofs at z = +-1 and nothing between, while a 26
degree roof puts faces at cos(26) = 0.90.

`Granada_buildings.stl` is georeferenced UTM, 2,482 by 2,520 m, 131,584
triangles, and **100 per cent of its upward area lies within 2 degrees of
horizontal**. A flat extrusion, exactly.

`Granada_buildings2.stl` looks more promising at first -- 678,466 triangles in
three print plates, the middle one holding 595,010, and 46 per cent of its upward
area sloped at a median tilt of 21.8 degrees, which is roughly roof pitch. It is
not roofs. Rendered top-down it is recognisably Granada, and the slope is the
hillside: splitting upward faces by height above the local surface gives 74.4 per
cent of the area level with the ground at a median tilt of 31.7 degrees, and only
3.0 per cent of it more than 1.5 units up, of which 47.4 per cent is flat and the
median tilt is 7.9 degrees. Buildings straddling a slope explain that residue.
Zoomed in, the blocks are visibly flat-topped with the topo triangulation showing
through between them.

So the STL route closes for a stronger reason than the five-metre raster ceiling
first blamed. The source city models are footprint extrusions, and re-cutting them
finer would never have produced a roof. This is measured for Granada only; the
plate cities come from a different vendor and were closed separately, on their own
evidence -- slope inside tagged footprints separating pitched from flat at AUC
0.596 in Salzburg and 0.528 in Prague.

**The STLs were never ground truth for the Granada roof layer.** They were the
candidate feature and lost. The labels behind the classifier are 640 OSM
`roof:shape` tags; the pitch angle behind the geometry is the width-binned Google
mesh measurement. Nothing in the delivered layer depends on a plate.

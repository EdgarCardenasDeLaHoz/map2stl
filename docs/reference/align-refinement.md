# Refining the drag tool's opening transform

_Last updated: 2026-09-28_

Scope: how `map2stl/tools/align_tool/refine_guess.py` corrects the geometric placement that
`export_align_data.py` builds, using only the rasters the export has already written. It
covers what is fitted, what is deliberately not, and the evidence each result carries. For
how the placement itself is derived see `numpy2stl/docs/registration.md`; for how the
plate's centre is solved see the module docstring of `tools/align_tool/locate.py`.

## What the export starts from

The OSM window is `osm_margin` (1.5) times the plate's footprint and both rasters are
rendered at 512 pixels, so the plate fills `1 / 1.5` of the frame and sits in the middle of
it. That placement is exactly as good as the two numbers behind it:

- the **centre**, solved by `locate` against OSM water under a confidence gate;
- the **span**, which the export only knows when a hand alignment measured it, and otherwise
  takes as nominal.

Everything else about the placement is arithmetic. The refinement corrects what those two
numbers leave behind.

## What it fits

| | Fitted | Why |
|---|---|---|
| Translation | always | A few pixels of centre error survive the solve, and the building masks pin them down. |
| Span | always, but a measured one is hard to overrule | The nominal span is wrong by 8-10% on Prague and Valencia, and the one measured span is wrong by 3.5%. |
| Rotation | never | Zero by construction; the window is built axis-aligned around the centre. |
| Anisotropic scale | never | Tried; made every city worse. |

### Deciding whether a span is really known

`locate.measured_span_m` returns a span whenever a hand alignment exists, which is a coarser
question than this one. The drag tool opens on the geometric placement, so a person who
drags the plate into position without resizing it saves the opening scale straight back out.
Of the six saved alignments only Salzburg's moved the scale, from 0.6667 to 0.5488; the
other five sit on the opening value to six decimal places. `span_is_measured` therefore
compares the saved alignment's scale against the guess recorded in the same file, which is
independent of what the current export happens to have been built at.

Where a span is measured, the export builds the OSM window at that size, so `f = 1` in the
sweep *is* the measurement. The sweep still runs. It costs one extra correlation pass and
it is the only way to discover that a measurement was wrong, which Salzburg's was: its
window was built at 1651 m and the sweep reads 1598 m, three and a half per cent smaller.
Overruling a person's work needs more than a higher score, so the sweep has to win on both
the correlation and the confidence of its peak -- `SPAN_OVERRIDE_R` and `SPAN_OVERRIDE_Z`.
Salzburg clears both by a wide margin, r 0.34 against 0.15 and z 12.0 against 5.5, and
warping the footprints into the plate frame to check confirms it: mask IoU 0.359 against
0.256, with precision 0.395 against 0.304 and recall 0.796 against 0.615. The other seven
cities are untouched by the change, because none of them has a measured span to overrule.

## How it fits

1. **Masked normalized cross-correlation.** The plain FFT correlation used by the water
   solver zero-means over the whole frame, which hands a larger template a higher score for
   free. `ncc_surface` takes the Pearson statistics inside the plate's own footprint only,
   so scores at different spans are comparable, which is what the span sweep needs.
2. **Span sweep**, 0.85 to 1.20 in steps of 0.005, on the building masks alone. Water is a
   ridge here -- a river scores nearly the same at every span once it can slide along
   itself -- so it settles nothing and is held back as an independent check.
3. **Continuous fit** on translation at the chosen span, Nelder-Mead from eight starting
   points up to twelve pixels apart, scoring buildings and (where both sides have water)
   water equally.

## What it has to prove before it is accepted

- coarse peak height `z >= 4` and margin over the runner-up `>= 1`;
- at least three of the eight starts landing in the same basin, and those starts scattering
  no more than 4 px;
- total movement under 25 px, about 150 m;
- water overlap not falling by more than 0.01, which for a buildings-driven fit is a cue the
  fit never saw.

A result that fails any of these is discarded and the geometric placement stands.

### Why the agreement test is what it is

The gate is looking for a *rival* optimum, not for a headcount. A start counts as a
contender when its score is within five per cent of the winner's, or within 0.01 absolute,
whichever is looser; the proportional term is what lets a city correlating at 0.34 be judged
on the same terms as one correlating at 0.80. Contenders then have to land together, and it
is that scatter -- not their number -- which says whether a second basin exists.

Dumping every start's landing point across the eight cities shows why nothing in between is
worth worrying about. A start that reaches the winner arrives within a thousandth of its
score and a fifth of a pixel of its position; a start that does not scores between 22 and 49
per cent worse and lands 6 to 40 px away. There is no populated middle. Requiring five
agreeing starts therefore measured how many descents happened not to stall, which is a fact
about Nelder-Mead rather than about the fit: it rejected Lisbon, whose four agreeing starts
sit 0.2 px apart under a peak of z 11.2 with a margin of 7.7 and whose water overlap
improves, because its other four simplices collapsed at r 0.43 to 0.50 against the winner's
0.636. Three is the floor because the fan seeds two starts on placements the export already
believes in, so three means one of the six blind offsets found the same answer on its own.

## Results

Run over the eight exported cities, all eight refinements accepted:

| city | span factor | moved | r | water IoU |
|---|---|---|---|---|
| Barcelona | x1.020 (2046 m) | 32 m | 0.769 | 0.794 -> 0.839 |
| Bilbao | x1.010 (2026 m) | 17 m | 0.662 | 0.471 -> 0.470 |
| Lisbon | x1.125 (2256 m) | 105 m | 0.638 | 0.651 -> 0.652 |
| Miami | x1.005 (2032 m) | 11 m | 0.806 | 0.802 -> 0.808 |
| Paris | x1.015 (2036 m) | 46 m | 0.584 | 0.319 -> 0.328 |
| Prague | x1.070 (2146 m) | 110 m | 0.507 | 0.343 -> 0.343 |
| Salzburg | x0.965 (1598 m) | 43 m | 0.428 | 0.325 -> 0.320 |
| Valencia | x1.085 (2176 m) | 122 m | 0.609 | no water |

Warping the OSM footprints into each plate's own frame and scoring the two building masks
against each other -- the only comparison in which the two coverages mean the same thing --
gives the placement's practical worth:

| city | plate cov | OSM cov | precision | recall | IoU |
|---|---|---|---|---|---|
| Barcelona | 0.609 | 0.464 | 0.686 | 0.901 | 0.638 |
| Bilbao | 0.535 | 0.429 | 0.727 | 0.906 | 0.676 |
| Lisbon | 0.505 | 0.446 | 0.635 | 0.719 | 0.509 |
| Miami | 0.315 | 0.239 | 0.593 | 0.781 | 0.509 |
| Paris | 0.634 | 0.484 | 0.700 | 0.916 | 0.658 |
| Prague | 0.594 | 0.425 | 0.664 | 0.929 | 0.632 |
| Salzburg | 0.513 | 0.255 | 0.395 | 0.796 | 0.359 |
| Valencia | 0.584 | 0.497 | 0.735 | 0.864 | 0.658 |

Salzburg remains the weakest and the reason is no longer the placement. Its plate carries
twice the building-like area OSM records for it, which is what an alpine city's forested
slopes look like to a morphological top-hat: a stand of trees is height above local ground
in exactly the way a building is. Local roughness does now separate the two -- roofs at a
median 0.075 against 0.174 for the false positives, where before the span was corrected the
signal ran the wrong way -- but dropping the roughest cells costs more recall than it buys
precision, and IoU falls at every cut. Telling a tree from a roof needs shape, not a
threshold.

Two independent corroborations:

- **`locate`'s own span search**, which uses water rather than buildings and a different
  window, reads 2160 m for Prague and Valencia and 2040 m for Barcelona. This sweep reads
  2156, 2196 and 2066. Two methods sharing no input agreeing to about a percent is the
  reason the span correction is trusted at all.
- **`eval_registration.py`** scores the refined placement above the hand alignment it is
  being compared against on every city and every mask metric -- overlap IoU Bilbao
  0.577/0.535, Miami 0.403/0.395, Paris 0.564/0.547, Salzburg 0.320/0.317, and height
  correlation Bilbao 0.358/0.216, Paris 0.319/0.174.

## Rejected: the satellite channel

Sobel edges from `sat.png` against blurred plate building edges, swept over span. It scores
an order of magnitude weaker than the building masks (r 0.10-0.30, every margin under 1.2)
and is only sane where the correlation is already strong. Paris lands 246 px out, Prague
233 px, Valencia 188 px. The imagery is not registered to OSM to the accuracy this needs.

## Using it

```
python map2stl/tools/align_tool/refine_guess.py                 # report over every exported city
python map2stl/tools/align_tool/refine_guess.py --apply         # write accepted results to disk
python map2stl/tools/align_tool/refine_guess.py paris_france    # one city
```

`--apply` rewrites `pipeline_guess` in each city's `meta.json` and in `tools/align_tool/data/align_data.js`,
moving the untouched placement to `geometric_guess` and recording the evidence under
`refinement`. Re-running is safe: the fit always starts from `geometric_guess`, so a second
pass lands in the same place rather than compounding.

`export_align_data.py` runs the same refinement inline, so a freshly exported city arrives
already corrected.

## Open follow-up

An accepted span is recorded as `refined_span_m` but is not fed back into the OSM window,
which is still built at the nominal span. Feeding it back would let the plate sit at the
geometric placement again with nothing left to correct, at the cost of a re-fetch.

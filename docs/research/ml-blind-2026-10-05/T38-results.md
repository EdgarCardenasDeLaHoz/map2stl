# T38 results: a height prior for untagged buildings (blind)

**Code:** [`tools/ml/t38_untagged_prior.py`](../../../tools/ml/t38_untagged_prior.py) (CPU, about 10 s).
Run `python tools/ml/t38_untagged_prior.py --out t38.json`.

**Data:** `buildings.csv`, untagged rows (`height_source == default`): 872 buildings in 8 cities.

**Protocol:** leave one city out. Every model and the constant are fitted only on the other cities.

Written blind: I did not read the local rules or scores before building this.

## Answer

- **Never publish the Street View height for an untagged building.** It is the worst predictor
  by a factor of 8–10 in every city: pooled MAE 91.2 m, against 11.3 m for a constant.
- **A constant of about 12 m is already a good prior:**
  - pooled MAE 11.3 m;
  - 27 % within 25 %.
  - The median of the training cities' untagged truth is 11.5–13.1 m, whichever city is held
    out, so it transfers.
- **A small gradient-boosting model on footprint geometry and OSM neighbourhood beats the
  constant in 6 of the 6 cities with at least 20 untagged buildings** (`gbm_geometry`):
  - pooled MAE 9.4 m, against 11.3 m for the constant;
  - 34 % within 25 %, against 27 %;
  - macro MAE over those 6 cities 9.6 m, against 11.7 m.
  - The gain is about 17 %: real, but modest.
- **Street View signals add nothing** (`gbm_geometry_sv` 9.5 m against 9.4 m without them).
  The model gives `sv_height_m` almost no weight, which is consistent with Street View's
  untagged errors being assignment errors, not noise.
- **Training on tagged buildings too** (`gbm_geometry_all_rows`) helps only towers over 60 m
  (MAE 51.8 m, against 68.6 m) and hurts the 0–30 m band (7.6 m, against 5.0 m), where 87 % of
  untagged buildings sit. Pooled it is worse (10.7 m). Tagged buildings are a taller
  population.

**Recommendation for T28's fallback:**
- **Ship now:** a 12 m constant, or the geometry model. 10 m, today's T28 default, is close; on
  this data a 12 m constant scores slightly better.
- **Next step:** the geometry model, after T29 compares it with Overture, GBA and the merged
  raster on the same buildings. A per-building source (Overture or GBA) may beat both where it
  has data.
- **Weak spot:** neither the prior nor the model finds untagged towers (60 m and up: MAE
  69–82 m). They need a measurement, not a prior.

## Per held-out city

MAE in m, with the share within 25 % after the slash:

| city | n | constant | Street View | log-linear on area | GBM shape only | **GBM geometry** | GBM + SV | GBM, all rows |
|---|---|---|---|---|---|---|---|---|
| Boston | 291 | 8.4 / 0.32 | 76.7 / 0.08 | 8.0 / 0.34 | 7.3 / 0.37 | **7.0 / 0.35** | 6.9 / 0.35 | 8.7 / 0.32 |
| Miami | 166 | 11.4 / 0.29 | 115.9 / 0.02 | 11.1 / 0.31 | 11.0 / 0.28 | **9.8 / 0.39** | 10.0 / 0.36 | 11.1 / 0.34 |
| Chicago | 135 | 18.7 / 0.27 | 86.3 / 0.09 | 17.3 / 0.28 | 17.6 / 0.27 | **16.8 / 0.28** | 16.9 / 0.32 | 18.5 / 0.26 |
| Seattle | 123 | 11.8 / 0.28 | 87.5 / 0.09 | 10.1 / 0.27 | 10.6 / 0.29 | **9.5 / 0.33** | 9.6 / 0.28 | 9.6 / 0.32 |
| La Défense | 89 | 8.0 / 0.14 | 109.2 / 0.02 | 5.6 / 0.35 | 5.5 / 0.35 | **5.7 / 0.28** | 5.7 / 0.32 | 6.6 / 0.26 |
| Madrid | 60 | 11.6 / 0.18 | 90.4 / 0.03 | 10.9 / 0.30 | 10.3 / 0.25 | **8.5 / 0.38** | 8.7 / 0.40 | 9.6 / 0.30 |
| Prague | 6 | 19.4 / 0.50 | 47.1 / 0.17 | 19.0 / 0.33 | 17.4 / 0.67 | 19.7 / 0.17 | 19.1 / 0.17 | 15.7 / 0.33 |
| Benidorm | 2 | 6.6 / 0.00 | 59.8 / 0.00 | 4.2 / 0.00 | 3.4 / 0.00 | 2.7 / 0.50 | 1.4 / 0.50 | 2.7 / 0.50 |
| **pooled** | 872 | 11.3 / 0.27 | 91.2 / 0.06 | 10.3 / 0.31 | 10.2 / 0.32 | **9.4 / 0.34** | 9.5 / 0.34 | 10.7 / 0.31 |

Median absolute error and the per-city JSON are in the script's `--out` file. Prague (6) and
Benidorm (2) are too small to read.

## By true height, pooled

| band | n | constant | Street View | GBM geometry | GBM, all rows |
|---|---|---|---|---|---|
| 0–30 m | 763 | 5.6 | 93.8 | **5.0** | 7.6 |
| 30–60 m | 65 | 29.9 | 85.2 | 21.0 | **18.7** |
| 60 m and up | 44 | 82.1 | 55.0 | 68.6 | **51.8** |

## Models

All the models predict log height and are fitted to minimise absolute error, so they predict
the median. Height errors are skewed, and MAE is the score.

- **constant:** the median untagged truth of the training cities.
- **log-linear on area:** median (quantile 0.5) regression, log height on log area.
- **GBM shape only:** `HistGradientBoostingRegressor`, absolute loss, depth 3, 200 iterations
  at rate 0.05, at least 20 rows per leaf. Inputs: log area, log perimeter, compactness
  (4πA/P²) and vertex count.
- **GBM geometry:** shape plus neighbourhood, computed from the other buildings of the same
  city in the table. Truth is never an input.
  - The count of buildings within 150 m and within 400 m.
  - The median **OSM height tag** of the tagged buildings within 150 m and within 400 m.
    Tags are known at run time. 69 % of untagged buildings have a tagged neighbour within
    400 m.
- **GBM + SV:** adds `sv_height_m`, `sv_n_views`, `sv_n_seeds`, `sv_mad_m` and `sv_confidence`.
- **GBM, all rows:** the geometry model trained on tagged and untagged training buildings,
  scored on untagged ones only.

Permutation importance of the geometry model (fitted on all untagged rows, MAE on log height),
in this order:
- log area (0.13);
- tag median within 150 m (0.07);
- vertex count (0.06);
- log perimeter (0.05);
- compactness and the 400 m features (0.04 each);
- count within 150 m (0.03).

## Caveats

- **The neighbourhood is limited to this table.** It holds only buildings the Street View
  pipeline measured, so at run time the neighbours should come from all OSM buildings in the
  region. That gives more neighbours and probably a better model; it is not tested here.
- **The truth is biased towards visible buildings.** It covers buildings the skyline pipeline
  estimated and both truth sources confirmed, so it over-represents buildings seen from the
  street. Hidden courtyard buildings and very small buildings may differ.
- **The 60 m+ band is small** (44 buildings), most of them in Chicago and Miami.

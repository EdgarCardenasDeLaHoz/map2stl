# T37 results: which photo tower readings are wrong? (blind)

**Code:** [`tools/ml/t37_photo_reading_errors.py`](../../../tools/ml/t37_photo_reading_errors.py) (CPU, a few seconds).
Run `python tools/ml/t37_photo_reading_errors.py --out t37.json`.

**Data:** `photo_readings.csv`: Miami 388 readings, Chicago 2,403.

**Wrong** means `|photo_m − truth_m| > 25 % of truth`. That is 24.7 % of Miami's readings and
23.0 % of Chicago's.

**Protocol:** train on one city, test on the other, in both directions. A model's threshold is
the one that maximises F1 of "wrong" on the training city.

Written blind: I did not read the local rules (`reading_flag`, `_cross_check`, the F-WEB2 notes)
before this.

## Answer

1. **The tower's own OSM tag is the signal, and a fixed rule is as good as any model.**
   - The rule: flag a reading that is more than 25 % away from the tower's OSM height tag.
   - Results:

     | Test city | AUC | Precision of "wrong" | Recall of "wrong" | Readings flagged | MAE of kept readings |
     |---|---|---|---|---|---|
     | Miami | 0.90 | 0.77 | 0.83 | 27 % | 23.8 → 14.0 m |
     | Chicago | 0.93 | 0.79 | 0.79 | 23 % | 24.3 → 14.5 m |

   - A gradient-boosting model on every feature (`gbm_all`) is no better out of city:
     - AUC 0.94 / 0.93;
     - kept MAE 14.9 / 15.0 m.
   - Logistic regression is unstable across the swap: recall 0.83 on Miami, 0.58 on Chicago.
   - What the boosting model adds over the rule is within the noise of one city swap.
2. **Without the tag, agreement with other photos is the next-best signal; geometry alone is
   weak.**

   | Model | AUC (Miami / Chicago) |
   |---|---|
   | No-tag model: other-photo agreement, distance, width, angles, prominence (`gbm_no_tag`) | 0.83 / 0.85 |
   | Geometry and view only (`gbm_geometry_only`) | 0.68 / 0.67 |

   The rule "more than 25 % from the median of the other photos" alone:
   - precision 0.54 / 0.65, recall 0.63 / 0.70;
   - kept MAE 16.7 / 16.3 m against 22.7 / 24.2 m for all readings.

   For untagged buildings, where there is no tag to compare with, this is the usable check.
3. **For a tagged tower, the photo rarely beats its tag.**
   - Using the OSM tag instead of the reading gives MAE 9.5 m in Miami and 8.5 m in Chicago,
     against 14 m for the best-filtered readings.
   - Only 4–5 % of tags are wrong by more than 25 %. Photos catch few of them:
     - of the 111 tag-wrong readings, the photo is right on 31;
     - of the 633 readings the OSM rule flags, the tag rather than the photo is the wrong one in
       17 (3 %).
   - So flagging by the tag is safe: it almost never throws away a reading that was right
     against a wrong tag.
4. **More photos per tower matter more than any filter.** Per tower, the median over its
   photos, against truth:

   | Photos of the tower | Miami MAE / wrong | Chicago MAE / wrong |
   |---|---|---|
   | 1 | 29.3 m / 44 % | 25.3 m / 43 % |
   | 2–3 | 23.3 m / 26 % | 20.5 m / 33 % |
   | 4+ | 11.1 m / 3 % | 11.3 m / 11 % |

5. **Wrong readings are mostly too tall:** 65 % sit above the truth. That fits a reading taken
   from the wrong roofline, for example a taller building behind.

**Recommendation:**
- **Tagged towers:** gate readings with the 25 % tag rule; no model is needed.
- **Untagged buildings:** gate with the 25 % rule against the other photos' median, and require
  3 or 4 photos or more.
- **Where photos earn their place on tagged towers:** relative order, and the few towers whose
  tags are wrong. Those cases are too rare here to learn a detector from.

## Full table

AUC; precision and recall of "wrong"; share flagged; MAE of all readings and of kept readings;
and the share of kept readings that are still wrong.

**Train Chicago → test Miami**

| method | AUC | prec | rec | flagged | MAE all | MAE kept | wrong kept |
|---|---|---|---|---|---|---|---|
| rule: OSM tag ±25 % | 0.904 | 0.769 | 0.833 | 0.268 | 23.75 | **14.03** | 0.056 |
| rule: tag or other photos ±25 % | – | 0.627 | 0.875 | 0.345 | 23.75 | 13.48 | 0.047 |
| logistic, all features | 0.921 | 0.833 | 0.833 | 0.247 | 23.75 | 15.15 | 0.055 |
| GBM, all features | 0.936 | 0.764 | 0.844 | 0.273 | 23.75 | 14.89 | 0.053 |
| GBM, no tag | 0.829 | 0.509 | 0.604 | 0.294 | 23.75 | 18.10 | 0.139 |
| GBM, geometry only | 0.679 | 0.360 | 0.667 | 0.459 | 23.75 | 19.40 | 0.152 |
| OSM tag instead of the reading | – | – | – | – | 9.47 | – | 0.049 |

**Train Miami → test Chicago**

| method | AUC | prec | rec | flagged | MAE all | MAE kept | wrong kept |
|---|---|---|---|---|---|---|---|
| rule: OSM tag ±25 % | 0.926 | 0.793 | 0.785 | 0.228 | 24.26 | **14.45** | 0.064 |
| rule: tag or other photos ±25 % | – | 0.665 | 0.843 | 0.292 | 24.26 | 13.96 | 0.051 |
| logistic, all features | 0.872 | 0.830 | 0.584 | 0.162 | 24.26 | 17.93 | 0.114 |
| GBM, all features | 0.930 | 0.837 | 0.759 | 0.209 | 24.26 | 14.97 | 0.070 |
| GBM, no tag | 0.848 | 0.643 | 0.450 | 0.161 | 24.26 | 19.41 | 0.151 |
| GBM, geometry only | 0.665 | 0.425 | 0.358 | 0.194 | 24.26 | 22.75 | 0.183 |
| OSM tag instead of the reading | – | – | – | – | 8.53 | – | 0.038 |

## Features (truth is never an input)

- **Tag agreement:** `log(photo_m / osm_m)` and its absolute value.
- **Other-photo agreement:** `|log(photo_m / other_photos_median_m)|`, with a has-others flag
  (8 % of readings have none) and `n_other_photos`.
- **View:** log distance, `width_deg`, `osm_angle_deg`, `prominence_deg`.
- **Photo quality:** the mean absolute log-ratio to the tag of the photo's *other* towers (leave
  one out), and the number of towers in the photo.

**Models:** the 25 % rules have no fitting.
- Logistic regression: standardised inputs.
- `HistGradientBoostingClassifier`: depth 3, 200 iterations at rate 0.05, at least 20 rows per
  leaf.

## Caveats

- **Two cities, one swap each.** The Miami test set is small: 388 readings from 25 photos.
- **Readings are not independent.** The same tower and the same photo appear many times, so the
  effective sample is closer to the number of towers (142 and 491) than of readings.
- **Every reading here is of a tagged tower.** The no-tag results are what carries over to
  untagged buildings, but that transfer is untested: the other-photo median here also comes
  from tagged towers.

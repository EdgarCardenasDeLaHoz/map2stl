# ML blind review data, 2026-10-05 (tasks T37, T38)

Two tables exported from the skyline runs (which are gitignored), so a cloud session can train
and score models without the run data, keys or a GPU. **Blind:** the local session's own rules
and their scores are deliberately not here; build your model from the data and the question,
and report held-out-city scores. The local session compares afterwards.

Truth everywhere = `truth_m`, the building's height where survey lidar and Google 3D Tiles
agree within max(3 m, 10 %) ("confirmed"). Only confirmed buildings are included.

## `photo_readings.csv` (T37): is a photo's reading of a tower right?

One row per tower reading in a kept Wikimedia Commons photo (Miami 388, Chicago 2,403). The
photo's camera was solved, each OSM-tagged tower predicted to form the skyline was identified,
and its height read from the photo's outline (tilt and camera height fitted on the photo's other
towers).

| column | meaning |
|---|---|
| city, photo, tower | ids (tower = index in that city's tower table) |
| dist_m | camera to the tower's nearest corner |
| width_deg | the tower's share of the photo's columns, degrees |
| osm_angle_deg | angle of the tower's OSM roof above the camera's horizon |
| prominence_deg | how far the tower's OSM roof rises above the next tower at those bearings (model) |
| photo_m | height read from the photo |
| osm_m | the tower's OSM height tag |
| n_other_photos, other_photos_median_m | other kept photos that read the same tower, and their median |
| truth_m | confirmed truth |

Question: predict whether `|photo_m - truth_m| > 0.25 * truth_m` (a wrong reading), or predict
the error. Train on one city, test on the other (both directions). Report precision/recall of
"wrong", and the MAE of the readings you would keep against all readings.

## `buildings.csv` (T38): a height for buildings without a trusted measurement

One row per building the Street View pipeline estimated, in the eight benchmark cities.

| column | meaning |
|---|---|
| city, key, lat, lon | ids and centroid |
| area_m2, perimeter_m, n_vertices | footprint geometry |
| osm_tag_m, height_source | OSM height (tag or levels) when there is one; `default` = untagged |
| sv_height_m, sv_n_views, sv_n_seeds, sv_mad_m, sv_confidence | the Street View estimate and its signals |
| truth_m | confirmed truth |

Question: predict `truth_m` for **untagged** buildings (`height_source` = `default`). Baselines
to beat: a constant (choose it on training cities only) and `sv_height_m`. Leave-one-city-out.
Report MAE, median error and the share within 25 %, per city.

# Skyline: building identity, heights from above, storeys (2026-10-06)

Three problems from the Cartagena work, each planned by one agent and reviewed by another
before building. Implementation in progress; results go to the task board (T43 and new rows).

## 1. Placing Commons photos without GPS (identify buildings by groups)

**Evidence.** On 17 photos with known cameras:
- Outline search (`skyline_match.locate`): 0/17 within 300 m.
- SIFT against the drone views: no real matches.
- `building_groups` voting: 0/16 within 1 km.
- At the true camera the model does not line up. Most model heights are fallbacks, MobileSAM
  splits towers differently from OSM, and trees cut skyline runs.

**Reviewed decisions.**
- Test the core hypothesis at the true camera before building search, zones or wildcards.
- Score per 0.1-degree column, not per segment. That removes the many-to-one segment problem.
- Trusted reference: OSM tags, the published heights and trusted drone readings. Other
  buildings are wildcards that can only cancel the "unexplained" penalty and never earn score.
- Asymmetric penalties: a photo building with no explanation costs a lot. A predicted tower
  missing from the photo costs little, because Commons photos date from 2005-2024.
- Exhaustive verify over about 2k cameras x 3600 headings x 23 scales is about 2.3 h per photo
  in Python. Vectorised sliding column scores bring it down to:
  - about 12 min per photo with no field of view;
  - about 90 s with the EXIF field of view;
  - seconds with a viewpoint zone.
- No hand annotation up front. Truth is the GPS position (300 m tolerance) plus the EXIF or
  refined heading.

**Key experiment and go/no-go.**
- (a) Best heading at the GPS position: within 3 degrees on at least 10/17.
- (b) The true camera ranks first among 500 distractor cameras on at least 7/17.
- If both pass: add viewpoint zones (title parsing: Popa, the baluartes, the bay, San Felipe),
  then landmark anchors (Estelar, the curved green tower), then refine with
  `camera_solver.solve_pose`.
- If (a) fails: geometry and segmentation cannot place these photos. Pivot to landmark anchors
  plus a quick click-to-label tool feeding `solve_from_labels`.
- Appearance galleries (DINOv2 on drone instances) and learned matchers are tie-breakers only.

## 2. Heights from a drone above the roofs (T43)

**Evidence.**
- seed_6 is about 185 m up over Bocagrande. Its camera comes from a full-circle ground fit
  (heading offset 3.6 degrees).
- `measure_footprints` on 9 tagged towers: median error 45 %, reading low (Portomarine 113 vs 188).
- The geometry cannot explain reading low: with the nearest-vertex distance the error would
  read high. The run stops early at roof or depth boundaries.

**Reviewed decisions.**
1. Diagnostic overlay first: the predicted base, crease and far-roof rows at the tag height,
   against where the run stops. This separates a pose problem from a boundary-detection problem.
2. Per-column closed form:
   - top = atan((H - h) / d_out), the far roof edge, when H < h;
   - top = atan((H - h) / d_in) otherwise;
   - inverted per column.
   A 1-D search is unnecessary.
3. Observed top only inside the band of rows a 3-260 m building can reach. Rank the edges:
   - sky or ground above: trusted;
   - another instance: depth;
   - a nearer occluder: censored (lower bound only).
4. Aggregate over columns with the 35th percentile, because penthouses raise the middle columns.
5. Per-building self-calibration from the base row does not measure camera height. Pool the
   base rows into one height-free check of the pose instead.
6. Never fit on the tags you evaluate on: use a ground- or waterline-only pose, or leave-one-out.
   `refine_on_outline` already leaks tags into the pose.

**Go/no-go.**
- After the diagnostic: the far-edge row at the tag height lands within 4 px of a visible
  boundary on at least 6 of 9 towers.
- Ship when all three hold:
  - synthetic prisms within max(2 m, 5 %);
  - seed_6 median error 15 % or less;
  - seeds 1/4/5 unchanged.

## 3. Storeys from facade floor bands (cross-validation)

**Evidence.** Prototype on hi-res seeds 4/5:
- 54 facades;
- floor period median 2.43 m (real floors are about 3 m);
- 48 % agreement with geometry within 25 %.

It locked onto the shortest lag (half periods) and used one distance for every column.

**Reviewed decisions.**
1. Rectify each column to metres:
   - z = d_col x tan(elevation);
   - d_col is the ray-footprint hit for that column, not the nearest vertex;
   - reject incidence above 70 degrees.
   `sphere_pano` makes each vertical world line one column.
2. Period from the normalised autocorrelation over 1.2-9 m:
   - take the shortest lag with ACF at least 0.85 of the maximum;
   - the 2.6-4.5 m floor prior is a check, not the search window.
3. The floor count (extent / period) does not depend on distance. It is a truly independent
   height check: floors x 3.0 m (residential), 3.5 m (hotel/office), 4.2 m (colonial).
   A period outside the prior points to a wrong distance or a wrong building.
4. Refuse when:
   - there are fewer than 5 px per floor (px/floor = f x P x cos^2(e) / (d x cos^2(e - pitch)));
   - fewer than 6 floors are visible;
   - strips disagree by more than 8 %.
5. Validate on floor counts: OSM `building:levels` on about 120 buildings, and the published
   floors of 5 towers. Do not use height / floors, which includes crowns and podiums.

**Go/no-go.** On held-out, hi-res, accepted facades, all three must hold:
- within 1 floor in at least 75 % of cases at 40 % coverage or more;
- MAE at least 20 % below the geometry / 3.1 m baseline;
- median period 2.9-3.4 m.

Otherwise floors are used only as an identity check.

## Related fixes the same day

- **Seams:** pinhole crops stitched side by side tore apart when pointed down.
  `elevated.sphere_pano` now reprojects the views exactly onto one sphere (d0ced7b).
- **seed_6 pointed down:** rows at -10, -36 and -62 degrees cover +5 to -77 degrees.

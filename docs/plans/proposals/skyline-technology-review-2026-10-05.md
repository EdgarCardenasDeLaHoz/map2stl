# Skyline heights — technology review: other approaches and photogrammetry

**Status:** proposal (follow-up to T20; no code changed). **Companion to:**
[skyline-improvement-plan-2026-10-05.md](skyline-improvement-plan-2026-10-05.md). That plan
ranks fixes inside the current pipeline. This one asks whether the pipeline uses the right
technology, and where photogrammetry fits.

**Question (user, 2026-10-05):** "Are we using the best technology for the panoramic tasks,
research other approaches, is photogrammetry applicable here."

## Short answer

- **The dominant error is not a technology gap.** It is roof-to-building assignment: untagged
  buildings read +65 to +113 m too tall, and pair order inside a view is near chance.
  - Photos using the same geometry with "the outline owner gets the roof" reach 91 % pair order
    (T20 plan, item 2).
  - A better depth model or segmenter does not change who gets the roof.
- **The skyline has never been compared with the height merge the app already ships.** The
  baseline it has to beat is `city2stl/height/__init__.py::merge_height_rasters`: Overture,
  GBA, WSF3D, GHSL and the rest.
  - That merge has been scored only against OSM tags (building-heights.md, 2026-08-30): Overture
    MAE 5.86 m; GBA 25.90 m, under-reading by 67.4 m above 50 m.
  - It has never been scored on the benchmark's confirmed truth, nor on untagged buildings.
- **Photogrammetry applies in one form: wide-baseline triangulation across photos or drone
  seeds.** Dense structure-from-motion from Street View sequences does not.
- **Where 3D Tiles has buildings, it already is the photogrammetric answer.** It is allowed for
  research use (building-heights.md, 2026-09-27). So the skyline's value is in places like
  Cartagena, where the 2026-10-04 probe found no buildings in 3D Tiles.

## Technology compared

| Step | Now | Alternative (2025–26) | Effect on our main error |
|---|---|---|---|
| Height for untagged buildings | Street View geometry. Miami: MAE 108.6 m; a constant 11 m scores 17.1 m. | The existing merge (Overture, GBA, WSF3D). Published GBA RMSE is 1.5–8.9 m by continent; ours on Miami tags is 25.9 m, low on towers. | Large for low buildings: this is the fallback of T20 item 1. |
| Depth | Depth Anything V2, relative; fitted as `a/d + b`; saturates past ~1.2 km | Metric3D v2, UniDepth or Depth Pro (metric, zero-shot). Survey: Metric3D v2 and UniDepth closest to lidar outdoors. | Moderate. A cleaner "surface nearer than the footprint" test for T20 item 2. Far towers stay hard. |
| Segmentation | SegFormer b1 | SAM-based building regions (2025 Baidu-panorama paper); b3 | Small. Order is wrong at any mask quality. |
| Pose of unlocated photos | Skyline outline against OSM; SIFT recognises only copies | Feed-forward multi-view reconstruction: VGGT (CVPR 2025), MASt3R; cameras and points from unposed photos in seconds | Large for the photo path. Groups photos and gives relative poses (F-WEB2 step G). |
| Heights for untagged buildings from photos | T16 (C2): agreement of implied heights across 2+ photos | Triangulating roof points across photos and seeds | Large. Removes "which building is on top" for any building seen from two wide-apart spots. |
| Occluded facades | — | Diffusion inpainting of occluders (2025): +10 % within 2 m on 1,000 buildings | Small. Close-range street scenes, not skylines at 0.5–2 km. |

## Is photogrammetry applicable? The baseline decides

Depth from two views: disparity = B·f/d. A 1 px error then gives a relative depth error of
d / (B·f).

For our 2688 px panoramas, f ≈ 2688 / 2π ≈ 428 px:

| Pair | Baseline B | Distance d | Disparity | Depth error per px |
|---|---|---|---|---|
| Consecutive Street View panoramas | 10 m | 1 km | 4.3 px | ~23 % |
| Street View panoramas one block apart | 100 m | 1 km | 43 px | ~2 % |
| Separate seeds; Commons photos from different spots | 300–1500 m | 1 km | 130–640 px | < 1 % |

**Dense SfM on Street View sequences:** not proposed.
- Towers are 0.5–2 km away and the baselines are about 10 m.
- Stitched panoramas are distorted, which the photogrammetry literature advises against.
- It would cost Street View requests per panorama.

**Wide-baseline triangulation:** proposed (P5).
- What we already hold has long baselines:
  - Commons skyline photos from Watson Island, Brickell, Vizcaya and the cruise port;
  - Cartagena drone seeds 0.5–1.5 km apart.
- T16 is already a constrained triangulation: the true roof gives one height from every
  photo.
- The full version triangulates a roof point, so no footprint has to be chosen first.

## Proposals

### P1. Score the existing height merge on the benchmark (do first)

**What**
- In `benchmark.py`, score each per-footprint source on confirmed truth: Overture, GBA, the
  merged raster sampled per footprint, and the OSM tag.
- Use the same bands and the same tagged/untagged split as the skyline.
- Add the rows to `10_benchmark` summaries next to the Street View and photo rows.

**Why**
- Every skyline number is meaningful only against this baseline, and it does not exist.
- STATUS says the merge "wins overall" on Miami tags. On untagged buildings, nothing is known.

**Gain:** decides where the skyline is worth running at all. If the merge is within ~10 m on
untagged low buildings, the skyline's job narrows to towers above ~50 m.

**Cost:** S–M. The scorer is cloud work, with synthetic tests. The provider reads need
network: the Overture and GBA parquet, run locally or in a cloud environment that allows those
hosts.

**Verify:** a per-city table, rows = sources, columns = MAE, bias, pair order, by band and by
tagged/untagged.

**Risk:** GBA and Overture may have learned from OSM tags, so their scores on tagged buildings
are optimistic. Report untagged buildings separately; they are the honest case.

### P2. Run the skyline only where it adds value

**What:** after P1, a rule (user decision) for when the skyline runs at all. For example:
- the city has no 3D Tiles buildings; or
- the merge has footprints above 50 m, where GBA reads 67 m low; or
- OSM tags are sparse.

The skyline's output is then a correction for towers, not the source for every footprint.

**Gain:** stops the skyline from degrading low buildings, which T20 item 1 also prevents.

**Cost:** S once P1 exists. **Risk:** a wrong rule hides real towers; keep the benchmark split by
band.

### P3. A metric depth model for the occlusion test

**What**
- Swap Depth Anything V2 for Metric3D v2 or UniDepth in `depth_estimation.py`, behind a flag,
  for the "surface nearer than the footprint" test (F-DET6, and T20 item 2).
- Keep heights from geometry; use depth only to accept or reject.

**Evidence**
- F-DET6 fits Depth Anything as `a/d + b` per pano, implied distance within 10–25 %, and it
  saturates past ~1.5 km.
- The 35 % "nearer surface" rule needs a ratio that is right to better than that.
- The survey cited below has Metric3D v2 and UniDepth closest to lidar point clouds outdoors.

**Gain:**
- Fewer wrong accepts in the 0.5–1.2 km band.
- No per-pano fit.
- Possibly drops the OSM-distance clamp beyond 1.2 km.

**Cost:** M; GPU, local.

**Verify:** implied distance against OSM footprint distance on the F-DET6 seeds (the
error-distribution plot), then the benchmark untagged bias with T20 item 2.

**Risk**
- Metric3D needs the camera intrinsics: tiles cut from the panorama have known intrinsics, so
  this is fine.
- Every metric model degrades past 1–2 km; this does not fix far towers.

### P4. Feed-forward multi-view reconstruction to pose and group photos

**What**
- Run VGGT, or MASt3R as a fallback, on candidate Commons photo groups.
- Keep groups whose relative poses are consistent.
- Fix scale and position with the located members (GPS or labels) or with OSM tower anchors,
  then refine with the existing outline fit.
- This replaces the outline-only grouping planned in F-WEB2 step G.

**Evidence**
- Search placement keeps 9 of 67 photos; only 1 passes the gate (`miami_photos.csv`).
- SIFT recognises only copies of the same shot.
- VGGT reports state-of-the-art camera and point estimates from unposed views in about a
  second; a follow-up reconstructs landmarks from tourist photos in under a minute.

**Gain:** kept photos 27 → 40+; more buildings with 3+ photos (17.5 m MAE, against 29.3 m for
one).

**Cost:** M; GPU, local. The cloud can build the group-and-anchor logic on mocked model outputs.

**Verify:** the located-photo validation (hide the location; target ≥ 70 % within 300 m and 3°),
then `17_photo_pipeline` kept counts and MAE.

**Risk**
- Skyline photos from 1–5 km with narrow views have little overlap; check on Miami before
  relying on it.
- Licences: VGGT's weights are released for research; check before shipping.

### P5. Wide-baseline triangulation of roof points (photogrammetry, scoped)

**What**
- For a group of posed photos or drone seeds, match roof-corner and roofline points along the
  skyline across views. Use the P4 correspondences, or epipolar search on the skyline profile.
- Triangulate them in the local metric frame (`skyline_match.Towers`).
- Assign each 3D roof point to the footprint it falls in.
- Height = point z minus terrain.

**Evidence**
- T16 already recovers untagged heights by cross-photo agreement, but it must test each
  footprint in turn and needs anchors for tilt and camera height.
- Drone seed disagreements are mostly the farther seed reading the tower behind (57–100 %).
  A triangulated point cannot be credited to the wrong footprint.

**Gain:**
- Heights for untagged buildings without anchors.
- A truth proxy for Cartagena, which has no survey.

**Cost:** M–L. The geometry core (triangulation, assignment, robust fit) is cloud work with
synthetic scenes; matching on real photos is local.

**Verify:**
- Miami confirmed truth on buildings in 2+ photos: compare with T16 and the merge (P1).
- Cartagena: agreement with T16 and with the nearest drone seed.

**Risk**
- Roofline points are low-texture; match on the skyline curve plus corners, not on SIFT.
- Photo dates differ, so construction changes give false points; prefer photos from 2015 on.

## Not proposed

- **Dense SfM or MVS on Street View sequences:** baselines are too short (see the table above),
  and it would mean Street View cost per panorama.
- **Diffusion inpainting of occluders:** helps close-range facades, not skylines.
- **A bigger segmenter as a general fix:** assignment, not masks, sets the order.
- **Building more on 3D Tiles where it has buildings:** it is already the truth source there.
  The skyline is for where it is missing.

## Where to build

| Proposal | Cloud | Local (GPU, caches, keys) |
|---|---|---|
| P1 merge on the benchmark | scorer and tests | provider reads, the run |
| P2 when to run the skyline | rule and tests | — (user decision) |
| P3 metric depth | flag plumbing, tests with a stub model | model runs, evaluation |
| P4 VGGT / MASt3R grouping | grouping and anchoring on mocked outputs | model runs |
| P5 triangulation | geometry core on synthetic scenes | matching on real photos and drone seeds |

**Order:** P1 → P2 → P5 (it extends T16) → P4 → P3. P3 can be done alongside T20 item 2 when
that starts.

## Sources

- [GlobalBuildingAtlas](https://arxiv.org/pdf/2506.04106) and
  [3D-GloBFP](https://essd.copernicus.org/preprints/essd-2024-217): global per-building heights.
- [Survey on monocular metric depth estimation](https://arxiv.org/pdf/2501.11841),
  [Depth Pro](https://arxiv.org/pdf/2410.02073),
  [UniDepth](https://openaccess.thecvf.com/content/CVPR2024/papers/Piccinelli_UniDepth_Universal_Monocular_Metric_Depth_Estimation_CVPR_2024_paper.pdf),
  [metric depth models for urban asset localization](https://arxiv.org/pdf/2509.14839).
- [VGGT (CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_VGGT_Visual_Geometry_Grounded_Transformer_CVPR_2025_paper.html),
  [large-scene feed-forward reconstruction from unposed views](https://arxiv.org/pdf/2602.23361).
- [Single-building height from Baidu panoramas with SAM (IJGI 2025)](https://www.mdpi.com/2220-9964/14/8/297).
- [From Google Street View to 3D city models (ICCVW 2009)](https://mlanthology.org/iccvw/2009/torii2009iccvw-google),
  [Street View photogrammetry limits (ISPRS 2017)](https://isprs-archives.copernicus.org/articles/XLII-2-W5/361/2017/).
- [Building height from Mapillary and OSM](https://arxiv.org/pdf/2307.02574).

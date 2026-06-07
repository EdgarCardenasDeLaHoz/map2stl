# F-SKY16 — Coastline-ICP heading registration

Status: **Phase A done (2026-05-28)** — measure-only. Lean plan;
updated once with results, not rewritten mid-flight.

## Why
- Pano-projected coastline (F-SKY13B): **bearings accurate, distances bad.**
  - Column→bearing = exact stitch geometry.
  - `camera_h / tan(elevation)` blows up near horizon → far coastline collapses radially toward seed.
- F-SKY11.1 per-keypoint sweep is distance-sensitive → drifts (seed_1 was 30° off until the `max_signal_dist_m` weighting patch).
- **Insight: don't trust distances — register the shapes.**
  - Match pano-coastline point set → OSM-coastline point set.
  - Solve for rotation (= heading offset) + optional radial scale (absorbs depth compression).
  - Shape-alignment is robust to the per-point distance errors that wreck per-keypoint scoring.

## Approach

1. **Inputs**: pano-projected coastline points (from
   `pano_water_top_to_lonlat`, already computed), OSM coastline points
   (from `osm_water.sample_coastline_points`, already computed). Both
   in lon/lat relative to the seed.
2. **Convert both to seed-relative polar** (bearing, range). Pano
   bearings are trusted; OSM is ground truth.
3. **1-D rotational ICP**: for a candidate heading offset, rotate the
   pano bearings, find each pano point's nearest OSM point, accumulate
   a robust (trimmed/median) alignment cost. Sweep offset coarse →
   fine; optionally solve a radial scale per offset.
4. **Robustness guards** (the hard part — see risks):
   - weight near points more (reuse `max_signal_dist_m` logic)
   - trimmed correspondence (drop worst X% — pano has noise OSM doesn't)
   - partial overlap (pano sees ~180° of coast, OSM 360°) — only score
     bearings the pano actually covers
   - multi-start / good initial guess to dodge the 180° bay symmetry
5. **Output**: recovered heading offset + an alignment confidence
   (so the existing σ/peak gate has an analogue). Feed into the joint
   anchor optimizer the same way F-SKY11.1's recovery does.

## Phasing

- **Phase A** (this iteration): standalone `coastline_icp_offset()` in
  `coastline_registration.py` + a comparison harness that runs it next
  to the current `sweep_pano_heading_offset` on Cartagena's 5 seeds and
  prints both recovered offsets vs the manual-override ground truth. NO
  production wiring — measure first.
- **Phase B** (later, gated on Phase A beating the keypoint sweep):
  wire as the primary heading source ahead of the joint optimizer;
  the recovered radial-scale becomes a per-seed depth calibration that
  could improve building `forward_m` estimates.

## Success criteria (Phase A)

On Cartagena's 5 seeds, recovered offset vs manual ground truth
(seed_1=135°, seed_4=-180°, seed_5=320°; seed_2/3 are
rejected/negative):
- ICP matches or beats the keypoint sweep on seed_1 (currently +7°
  after the weighting patch) and seed_5 (currently +19°).
- ICP gets seed_4 closer than the keypoint sweep's 76-136° miss
  (seed_4 is the standing failure — coastline-only may still not
  solve it, but ICP using full shape has the best shot).
- No new dependency; pure numpy.

## Known risks

1. **Local minima / 180° bay symmetry** — a peninsula seed has
   near-symmetric coastline on two sides. ICP needs a good init
   (URL heading) + possibly multi-start, else it locks onto the
   mirror. This is the same failure that has dogged every
   heading-recovery attempt.
2. **Partial + noisy correspondence** — pano coastline is a noisy
   ~180° arc; OSM is a clean 360° polyline. Naive nearest-neighbour
   ICP will mis-correspond. Needs trimming + bearing-coverage masking.
3. **Depth compression isn't a uniform scale** — a single radial-scale
   factor only approximates the `1/tan` compression. May need a
   monotonic radial warp rather than a scalar; start with scalar,
   measure residuals, escalate only if needed.

## What this does NOT do

- Does not touch the geometric water-top projection or Depth Anything
  V2 (F-SKY12). It sidesteps absolute depth rather than improving it.
- Does not change building matching directly — it improves the heading
  the matcher inherits.

## Phase A measured results (2026-05-28, Cartagena)

`coastline_icp_offset` shipped + validated:
- **Synthetic**: recovers known offsets (0/25/−40/135°) to ±2° under
  0.3× radial compression + 2° bearing noise; sharp ~0.4° cost min.
  → the bearings-only registration premise is sound.
- **Real Cartagena** (ICP vs keypoint sweep vs manual ground truth):

| Seed | Truth | Keypoint sweep | ICP | ICP cost |
|---|---|---|---|---|
| 1 | 135° | 142° (+7°) | 126° (−9°) | 7.0° |
| 4 | 180° | 44° (**off 136°**) | 162° (−18°) | 4.6° |
| 5 | 320° | 339° (+19°) | 177° (**off ~143°**) | 5.3° |
| 2 | (rejected) | 285° | 159° | 0.01° (degenerate) |

**Verdict: ICP is COMPLEMENTARY, not a replacement.**
- Fixes seed_4 — keypoint sweep's standing 136° miss → ICP −18°.
- Regresses seed_5 — locks onto the 180° bay-symmetry mirror (177°, low cost 5.3° because the mirrored shape aligns convincingly).
- `init_offset` = keypoint result didn't prevent the mirror; `search_range_deg=180` reaches it.

**Root finding (unchanged from every prior heading attempt): the 180°
bay symmetry is the real blocker.**
- Shape registration doesn't solve it — just fails on a different seed.
- A peninsula seed's coastline is near-symmetric front/back; both the keypoint peak and the ICP cost have a near-equal twin 180° away.

## Phase B — revised direction
- **Do NOT wire ICP as a drop-in primary** (would regress seed_5).
- Productive next step = **180°-symmetry disambiguation** (helps both methods):
  - **Consensus gate** (cheapest win): ICP + keypoint sweep agree within ~15° → auto-drive; disagree → manual override + flag. seed_1 (~130-142°) auto-drives; seed_4/seed_5 stay manual.
  - **Asymmetric tiebreaker**: building skyline is NOT 180°-symmetric even when coastline is (towers one side, open water the other) — combine coastline cost with a one-sided building-density / sky-fraction check.
  - **Narrowed search**: ±45-60° of a trustworthy prior instead of ±180°.

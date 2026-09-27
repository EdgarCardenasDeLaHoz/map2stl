"""Registration robustness test.

Apply a *known* transformation (rotation / scale / translation) to the STL
heightmap, re-register the perturbed model against the real OSM data, and check
that the recovered transform changes by the inverse of what was applied.  This
measures how capable the registration algorithm is, with a ground truth — it is
independent of the STL-vs-OSM data mismatch because we compare each perturbed
run to the unperturbed baseline run on the same data.

Usage
-----
python -m city2stl.registration.scripts.robustness_test \\
    --stl path/to/city.stl --region "Philadelphia, PA, USA" [--resolution 512]

Interpretation
--------------
For an applied rotation +r, a correct algorithm recovers a rotation that is
r degrees *less* than the baseline (it rotates the model back).  The reported
"error" is |recovered_delta − applied|; small errors across the sweep mean the
registration is robust.  Rotation is the headline metric (it now comes from the
translation-invariant line-angle histogram).
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np

logging.basicConfig(level=logging.WARNING)  # quiet — we print our own table


def _affine(cx, cy, scale, rot_deg, tx, ty):
    import cv2
    M = cv2.getRotationMatrix2D((cx, cy), rot_deg, scale)
    M[0, 2] += tx
    M[1, 2] += ty
    return M.astype(np.float64)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stl", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--resolution", type=int, default=512)
    p.add_argument("--z-axis", type=int, default=2)
    args = p.parse_args(argv)

    from numpy2stl.registration.align import _decompose_matrix, apply_transform, register

    from city2stl.registration import register_city_stl

    region = args.region
    parts = region.split(",")
    if len(parts) == 4:
        try:
            region = tuple(float(x) for x in parts)
        except ValueError:
            pass

    print("Baseline registration (unperturbed)…")
    base = register_city_stl(stl_file=args.stl, city_name=region,
                             resolution=args.resolution, stl_z_axis=args.z_axis,
                             out_dir=False)
    orig = base.stl_heightmap
    osm = base.osm_heightmap
    b_scale = base.registration.scale
    b_rot = base.registration.angle_deg
    h, w = orig.shape
    cx, cy = w / 2.0, h / 2.0
    print(f"  baseline: scale={b_scale:.4f}  rot={b_rot:+.2f}deg\n")

    # Known perturbations: (label, scale, rot_deg, tx_px, ty_px)
    cases = [
        ("rot +5",    1.0,  5.0,  0,   0),
        ("rot -5",    1.0, -5.0,  0,   0),
        ("rot +12",   1.0, 12.0,  0,   0),
        ("shift +30x",1.0,  0.0, 30,   0),
        ("shift -20y",1.0,  0.0,  0, -20),
        ("scale 1.1", 1.1,  0.0,  0,   0),
        ("scale 0.9", 0.9,  0.0,  0,   0),
        ("combo",     1.1,  8.0, 25, -15),
    ]

    print(f"{'case':<12} {'applied':<22} "
          f"{'rec d_scale':>16} {'d_rot':>8} {'rot_err':>8}")
    print("-" * 72)
    for label, s, r, tx, ty in cases:
        M_p = _affine(cx, cy, s, r, tx, ty)
        moved = apply_transform(orig, M_p, orig.shape)
        try:
            res = register(moved, osm, known_scale=None, scale_search=0.35)
            rec_scale, rec_rot = _decompose_matrix(res["transform"])
        except Exception as exc:
            print(f"{label:<12} FAILED: {exc}")
            continue
        # The recovered transform should change by the inverse of the applied one.
        d_scale = rec_scale / b_scale          # expected ≈ 1/s
        d_rot = rec_rot - b_rot                 # expected ≈ -r
        rot_err = abs(d_rot - (-r))
        print(f"{label:<12} s={s:.2f} r={r:+.0f} t=({tx:+d},{ty:+d})   "
              f"{d_scale:>10.3f} (exp {1/s:.3f})  {d_rot:>+7.2f} {rot_err:>7.2f}")

    print("\nSmall rot_err across cases = robust rotation recovery.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

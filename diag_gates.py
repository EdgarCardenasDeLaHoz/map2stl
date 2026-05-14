import numpy as np
import cv2
from city2stl.skyline_cv.pipeline import detect_skyline_contour
import sys
import pathlib
import glob
sys.path.insert(0, '.')

img_files = sorted(glob.glob('city2stl/skyline_cv/runs/image_cache/*.png'))
print(f'Testing {len(img_files)} images...')
results = []
for f in img_files:
    img_bgr = cv2.imread(f)
    if img_bgr is None:
        continue
    img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    top_h = max(1, int(h * 0.20))
    contour, sky_mask = detect_skyline_contour(img)
    sky_frac_top = float(sky_mask[:top_h].sum()) / max(top_h * w, 1)
    contour_valid = contour[np.isfinite(contour)]
    contour_range_frac = (
        float(np.nanmax(contour_valid) - np.nanmin(contour_valid)) / h
        if len(contour_valid) > 0 else 0.0
    )
    gate_b = contour_range_frac >= 0.10
    results.append((f, sky_frac_top, contour_range_frac, gate_b))

print(f'Passed Gate B: {sum(r[3] for r in results)}/{len(results)}')
print()
print('FAILING Gate B (range < 0.10) sorted by range:')
for f, sf, cr, gb in sorted(results, key=lambda x: x[2]):
    if not gb:
        print(f'  range={cr:.4f} sky={sf:.3f} {pathlib.Path(f).name}')
print()
print('Range distribution (all):')
ranges = sorted(r[2] for r in results)
buckets = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 1.0)]
for lo, hi in buckets:
    n = sum(1 for r in ranges if lo <= r < hi)
    print(f'  [{lo:.2f},{hi:.2f}): {n}')

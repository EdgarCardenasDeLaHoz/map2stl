"""Quick diagnostic: score distribution of cached images after neural sky."""
from city2stl.skyline_cv.pipeline import detect_skyline_contour
import sys
import json
import os
import base64
import numpy as np
import cv2

sys.path.insert(0, ".")

cache_dir = "city2stl/skyline_cv/runs/image_cache"
files = [f for f in os.listdir(cache_dir) if f.endswith(".png")]

print(f"Found {len(files)} cached PNGs. Loading model + scoring...")
scores = []
for fn in files:
    img = cv2.imread(os.path.join(cache_dir, fn))
    if img is None:
        continue
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    contour, sky_mask = detect_skyline_contour(img_rgb)
    h, w = img_rgb.shape[:2]
    top_h = max(1, int(h * 0.20))
    sky_frac_top = float(sky_mask[:top_h].sum()) / (top_h * w)
    contour_valid = contour[np.isfinite(contour)]
    if contour_valid.size == 0:
        continue
    rng = float(np.nanmax(contour_valid) - np.nanmin(contour_valid)) / h
    if sky_frac_top < 0.35 or rng < 0.05:
        continue
    var = float(np.std(contour))
    span = float(np.nanmax(contour) - np.nanmin(contour))
    score = float(np.clip((0.6 * var + 0.4 * span) / 80.0, 0.0, 1.0))
    scores.append((score, rng, var, span, fn))

scores.sort()
print(f"\nN passing both gates: {len(scores)}")
print(f"Scores < 0.20:       {sum(1 for s in scores if s[0] < 0.20)}")
print(f"Scores 0.20-0.30:    {sum(1 for s in scores if 0.20 <= s[0] < 0.30)}")
print(f"Scores 0.30-0.45:    {sum(1 for s in scores if 0.30 <= s[0] < 0.45)}")
print(f"Scores >= 0.45:      {sum(1 for s in scores if s[0] >= 0.45)}")
all_scores = [s[0] for s in scores]
print(f"Min: {min(all_scores):.4f}  Max: {max(all_scores):.4f}  Median: {float(np.median(all_scores)):.4f}")

print("\nBottom 15 scores (lowest quality that still pass both gates):")
for score, rng, var, span, fn in scores[:15]:
    print(
        f"  score={score:.4f}  range={rng:.4f}  std={var:.1f}  span={span:.1f}  {fn}")

print("\nTop 10 scores:")
for score, rng, var, span, fn in scores[-10:]:
    print(
        f"  score={score:.4f}  range={rng:.4f}  std={var:.1f}  span={span:.1f}  {fn}")

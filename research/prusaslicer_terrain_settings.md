# PrusaSlicer Settings Research — Terrain Puzzle Maps
**Date:** 2026-04-06  
**Context:** Ender-3 Pro, 0.4mm nozzle, PLA, puzzle-piece terrain maps with interlocking notches

---

## Confirmed: `bottom_solid_layers = 0` is correct
The current profile has this set. Terrain pieces sit flat on the print bed with the terrain face on top — no bottom skin is needed or desired (saves material, reduces print time, bottom face is never seen).

**Mitigation for adhesion risk with 0 bottom layers:**
```ini
first_layer_height = 0.2     # thicker squish for better bed grip
first_layer_speed = 20       # slower first layer
disable_fan_first_layers = 3 # no cooling until layer 4 (prevents warping)
elefant_foot_compensation = 0.2
```

---

## 1. Terrain Top Surface Quality

### `top_solid_layers = 5` (currently 3)
At 5% infill the top cap must bridge wide gaps. 3 layers at 0.32mm = 0.96mm — often not enough. 5 layers guarantees clean result.

### `top_fill_pattern = monotonic` (currently rectilinear)
Monotonic always prints lines in the same direction, eliminating the alternating-direction washboard artifact common on terrain tops. Default in PrusaSlicer 2.3+.

### `top_solid_infill_speed = 25` (currently 50)
Slower top surface pass → better adhesion, flatter finish.

### `infill_anchor = 2.5` and `infill_anchor_max = 12`
Connects infill lines to perimeter walls, reducing gaps at wall junctions that show through the top surface.

---

## 2. Puzzle Notch Dimensional Accuracy

### `perimeters = 3` (currently 2)
Three perimeters gives notch walls structural rigidity. With 2, the notch wall may flex slightly causing false-fit.

### `perimeter_generator = arachne`
Adapts extrusion width to actual wall thickness — avoids thin-wall gaps in notch geometry. Default in PrusaSlicer 2.5+.

### `external_perimeter_speed = 25` (currently 40)
Slow outer perimeter → better dimensional accuracy on notch curves. Inner perimeters can stay at 45mm/s.

### `elefant_foot_compensation = 0.2`
First layer squish causes notch bases to bind. 0.2mm trims the over-squish without removing thin lines.

### `resolution = 0.01`
Controls contour simplification (mm). `0` = no simplification → huge gcode + slow slice. `0.01` (10 microns) is below FDM dimensional repeatability — safe and dramatically faster. Do not go above `0.05` or notch curves may be rounded.

```ini
resolution = 0.01
gcode_resolution = 0.0125
```

---

## 3. Seam Position

### `seam_position = random` (currently aligned)
Terrain pieces have organic curved edges with no sharp corners. `aligned` creates a visible vertical ridge on curved silhouettes. `random` distributes seam blobs around the perimeter.

**Note:** For notch faces specifically, use **Seam Painting** in PrusaSlicer GUI (right-click → Paint-on seam) to force seam away from the joint face. Seam paint overrides global `seam_position`.

---

## 4. Ironing — Do Not Use

### `ironing = 0`
Ironing is designed for flat horizontal surfaces. Terrain tops are continuously sloped — ironing would drag marks across non-flat contours with no benefit and significant time cost. Per Prusa docs: "not useful for round objects, figures, and organic shapes."

**Exception:** If a model has genuinely flat plateau regions, use a Modifier Mesh to enable ironing only there.

---

## 5. Infill

### `infill_every_layers = 2` (already set)
At 0.32mm layer height, prints one infill layer per 2 perimeter layers (0.64mm combined). ~50% infill time savings, no visible quality difference. Correct for terrain.

### `fill_density = 5%` (keep as-is)
Fine for terrain — infill exists only to support top surface and give rigidity.

### `fill_pattern = rectilinear` or `line`
Both are fine for sparse infill. `rectilinear` is the PrusaSlicer INI key name.

---

## 6. Cooling (PLA)

```ini
fan_always_on = 1
min_fan_speed = 35
max_fan_speed = 100
disable_fan_first_layers = 3
full_fan_speed_layer = 4
slowdown_below_layer_time = 15    # up from 10 — thin border pieces need more time
min_print_speed = 10
bridges_fan_speed = 100           # full fan on bridging moves in terrain top layers
```

**Key:** `slowdown_below_layer_time = 15` is important for the 1mm hollow border/base pieces — their layer area is small and PLA must solidify before the next layer arrives.

---

## 7. Multi-Piece Print Strategy

### Option A — Simultaneous (default, safer for Ender-3 Pro)
```ini
avoid_crossing_perimeters = 1
avoid_crossing_perimeters_max_detour = 0
```
Tall travel moves over completed pieces can knock them off. Simultaneous printing avoids this.

### Option B — Sequential (PrusaSlicer 2.9.1+ only)
```ini
print_sequence = objects
```
Finishes each piece before starting the next. Eliminates inter-piece stringing. Requires careful arrangement to avoid nozzle collision. PrusaSlicer 2.9.1+ includes Smart Sequential Arrange.

---

## 8. Settings to Leave Unchanged

| Setting | Value | Reason |
|---|---|---|
| `layer_height` | 0.32 | Good terrain detail vs. speed balance |
| `support_material` | 0 | Flat bottom + organic top needs no support |
| `skirts` | 2 | Good for detecting clogs before pieces start |
| `brim_width` | 0 | Full flat bottoms don't need brim; brim would interfere with puzzle edges |
| `bottom_solid_layers` | 0 | Intentional — bottom face is never seen |

---

## Proposal: Quality-Optimized Profile (deferred — current profile is speed-optimized)

The `maps_2025_part2.ini` profile was deliberately tuned for speed. The settings below are a quality upgrade proposal for when print time is less constrained (e.g., final display pieces vs. prototypes). Do not apply until ready to trade speed for quality.

## Recommended INI Delta (changes from current maps_2025_part2.ini)

```ini
; Surface quality
top_solid_layers = 5          ; was 3
top_fill_pattern = monotonic  ; was rectilinear
top_solid_infill_speed = 25   ; was 50
infill_anchor = 2.5           ; new
infill_anchor_max = 12        ; new

; Puzzle accuracy  
perimeters = 3                ; was 2
perimeter_generator = arachne ; new (verify PrusaSlicer version supports it)
external_perimeter_speed = 25 ; was 40 (note: perimeter_speed for inner walls stays 45)
resolution = 0.01             ; was 0
gcode_resolution = 0.0125     ; new

; Seam
seam_position = random        ; was aligned

; Ironing
ironing = 0                   ; confirm off (should be default)

; Cooling
slowdown_below_layer_time = 15 ; was 10
bridges_fan_speed = 100        ; was not set
full_fan_speed_layer = 4       ; new
fan_always_on = 1              ; was 0

; First layer (mitigates 0 bottom_solid_layers risk)
first_layer_speed = 20         ; was 30
```

---

## Sources
- https://blog.prusa3d.com/how-to-print-maps-terrains-and-landscapes-on-a-3d-printer_29117/
- https://blog.prusa3d.com/make-top-surfaces-super-smooth-ironing-prusaslicer-2-3-beta_41506/
- https://help.prusa3d.com/article/ironing_177488
- https://help.prusa3d.com/article/elephant-foot-compensation_114487
- https://help.prusa3d.com/article/seam-position_151069
- https://help.prusa3d.com/article/arachne-perimeter-generator_352769
- https://help.prusa3d.com/article/cooling_127569
- https://forum.prusa3d.com/forum/how-do-i-print-this-printing-help/trying-to-force-monotonic-solid-layer-topographic-map-print/
- https://ansonliu.com/maps/print-settings/

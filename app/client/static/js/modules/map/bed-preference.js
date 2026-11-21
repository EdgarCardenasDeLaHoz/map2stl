/**
 * modules/map/bed-preference.js - the printer bed the new-region suggestion box is shaped for.
 *
 * Explore has no bed control (user decision, 2026-11-13): the dashed suggestion box takes
 * its aspect ratio from the bed chosen in Extrude, which ModelContainer.vue persists in
 * localStorage under BED_KEY as {value, w, h}. No saved choice means a square box.
 */
import { parseBedSize } from '../export/print-scale.js';

export const BED_KEY = 'map2stl_bed';

/** The persisted bed, or a 220 x 220 mm square when none is saved (or storage is unreadable). */
export function readBedPreference(storage) {
    let saved = null;
    try {
        const s = storage ?? (typeof localStorage === 'undefined' ? null : localStorage);
        saved = JSON.parse(s?.getItem(BED_KEY) || 'null');
    } catch { saved = null; }
    if (!saved || !saved.value) return { w: 220, h: 220, name: '220×220 mm bed' };
    const { w, h } = parseBedSize(saved.value, saved.w, saved.h);
    return { w, h, name: `${w}×${h} mm bed` };
}

/** Width over height of the bed; 1 (square) for anything unusable. */
export function bedAspect(bed) {
    if (!bed || !(bed.w > 0) || !(bed.h > 0)) return 1;
    return bed.w / bed.h;
}

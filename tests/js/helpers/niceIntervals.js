/**
 * Pure extraction of nicePixelInterval + niceGeoInterval from layers/stacked-layers.js.
 * Source: app/client/static/js/modules/layers/stacked-layers.js:135–160
 */
export function nicePixelInterval(totalPixels, targetLines) {
    const raw = totalPixels / targetLines;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const mult of [1, 2, 5, 10]) {
        const candidate = mag * mult;
        if (totalPixels / candidate <= targetLines) return candidate;
    }
    return mag * 10;
}

export function niceGeoInterval(rangeInPixels, pixelsPerDegree, targetLines) {
    const candidates = [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 45, 90];
    const totalRange = rangeInPixels / pixelsPerDegree;
    for (const c of candidates) {
        if (totalRange / c <= targetLines) return c;
    }
    return candidates[candidates.length - 1];
}

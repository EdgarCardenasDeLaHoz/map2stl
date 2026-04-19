/**
 * Pure extraction of hslToRgb + mapElevationToColor from dem/dem-loader.js.
 * Source: app/client/static/js/modules/dem/dem-loader.js:37–105
 */
function hslToRgb(h, s, l) {
    let r, g, b;
    if (s === 0) {
        r = g = b = l;
    } else {
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
    }
    return [r, g, b];
}

export function mapElevationToColor(t, cmap) {
    t = Math.max(0, Math.min(1, t));
    if (cmap === 'raindow') cmap = 'rainbow';
    if (cmap === 'jet') {
        const clip = x => Math.max(0, Math.min(1, x));
        const r = clip(1.5 - Math.abs(4 * t - 3));
        const g = clip(1.5 - Math.abs(4 * t - 2));
        const b = clip(1.5 - Math.abs(4 * t - 1));
        return [r, g, b];
    }
    if (cmap === 'rainbow') {
        const h = 0.66 * (1 - t);
        return hslToRgb(h, 1, 0.5);
    }
    if (cmap === 'viridis') {
        const h = 0.7 - 0.7 * t;
        const s = 0.9;
        const l = 0.5;
        return hslToRgb(h, s, l);
    } else if (cmap === 'hot') {
        const r = Math.min(1, 3 * t);
        const g = Math.min(1, Math.max(0, 3 * t - 1));
        const b = Math.min(1, Math.max(0, 3 * t - 2));
        return [r, g, b];
    } else if (cmap === 'gray') {
        return [t, t, t];
    }
    // default: terrain-like (green → brown → white)
    if (t < 0.4) {
        const tt = t / 0.4;
        return [0.0 * (1 - tt) + 0.4 * tt, 0.3 * (1 - tt) + 0.25 * tt + 0.45 * tt, 0.0 + 0.0 * tt];
    } else if (t < 0.8) {
        const tt = (t - 0.4) / 0.4;
        return [0.4 * (1 - tt) + 0.55 * tt, 0.6 * (1 - tt) + 0.45 * tt, 0.2 * (1 - tt) + 0.15 * tt];
    } else {
        const tt = (t - 0.8) / 0.2;
        return [0.55 * (1 - tt) + 0.9 * tt, 0.45 * (1 - tt) + 0.9 * tt, 0.15 * (1 - tt) + 0.9 * tt];
    }
}

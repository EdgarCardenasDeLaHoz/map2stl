/* DEAD — not loaded by app.js or index.html. Editing this file has no effect on the running app. */
/**
 * Color Utilities Module
 * Color mapping and conversion functions for DEM visualization
 */

/**
 * Convert HSL to RGB
 * @param {number} h - Hue (0-1)
 * @param {number} s - Saturation (0-1)
 * @param {number} l - Lightness (0-1)
 * @returns {Array<number>} [r, g, b] values (0-1)
 */
export function hslToRgb(h, s, l) {
    let r, g, b;
    
    if (s === 0) {
        r = g = b = l; // achromatic
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

/**
 * Map a normalized elevation value to a color based on colormap
 * @param {number} t - Normalized value (0-1)
 * @param {string} cmap - Colormap name
 * @returns {Array<number>} [r, g, b] values (0-1)
 */
export function mapElevationToColor(t, cmap) {
    // Clamp t to [0, 1]
    t = Math.max(0, Math.min(1, t));
    
    // Handle common typos
    if (cmap === 'raindow') cmap = 'rainbow';
    
    switch (cmap) {
        case 'jet':
            return jetColormap(t);
        case 'rainbow':
            return rainbowColormap(t);
        case 'viridis':
            return viridisColormap(t);
        case 'hot':
            return hotColormap(t);
        case 'gray':
        case 'grey':
            return [t, t, t];
        case 'terrain':
        default:
            return terrainColormap(t);
    }
}

/**
 * Jet colormap (blue → cyan → yellow → red)
 */
function jetColormap(t) {
    const clip = x => Math.max(0, Math.min(1, x));
    const r = clip(1.5 - Math.abs(4 * t - 3));
    const g = clip(1.5 - Math.abs(4 * t - 2));
    const b = clip(1.5 - Math.abs(4 * t - 1));
    return [r, g, b];
}

/**
 * Rainbow colormap (blue → red)
 */
function rainbowColormap(t) {
    // Map t to hue from blue (~0.66) to red (0)
    const h = 0.66 * (1 - t);
    return hslToRgb(h, 1, 0.5);
}

/**
 * Viridis-like colormap (purple → blue → green → yellow)
 */
function viridisColormap(t) {
    // Simple viridis approximation using HSL
    const h = 0.7 - 0.7 * t; // from purple to yellow
    const s = 0.9;
    const l = 0.5;
    return hslToRgb(h, s, l);
}

/**
 * Hot colormap (black → red → yellow → white)
 */
function hotColormap(t) {
    const r = Math.min(1, 3 * t);
    const g = Math.min(1, Math.max(0, 3 * t - 1));
    const b = Math.min(1, Math.max(0, 3 * t - 2));
    return [r, g, b];
}

/**
 * Terrain colormap (green → brown → white)
 */
function terrainColormap(t) {
    if (t < 0.4) {
        // Green shades (lowland)
        const tt = t / 0.4;
        return [
            0.0 * (1 - tt) + 0.4 * tt,
            0.3 * (1 - tt) + 0.7 * tt,
            0.0 + 0.0 * tt
        ];
    } else if (t < 0.8) {
        // Brown shades (highland)
        const tt = (t - 0.4) / 0.4;
        return [
            0.4 * (1 - tt) + 0.55 * tt,
            0.7 * (1 - tt) + 0.45 * tt,
            0.0 * (1 - tt) + 0.15 * tt
        ];
    } else {
        // White shades (peaks/snow)
        const tt = (t - 0.8) / 0.2;
        return [
            0.55 * (1 - tt) + 0.95 * tt,
            0.45 * (1 - tt) + 0.95 * tt,
            0.15 * (1 - tt) + 0.95 * tt
        ];
    }
}

/**
 * ESA WorldCover color scheme
 */
export const ESA_COLORS = {
    10: [0, 100, 0],       // Tree cover (green)
    20: [255, 187, 34],    // Shrubland (yellow)
    30: [255, 255, 76],    // Grassland (light yellow)
    40: [240, 150, 255],   // Cropland (pink)
    50: [250, 0, 0],       // Built-up (red)
    60: [180, 180, 180],   // Bare/sparse (gray)
    70: [240, 240, 240],   // Snow/ice (white)
    80: [0, 100, 200],     // Permanent water (blue)
    90: [0, 150, 160],     // Herbaceous wetland (teal)
    95: [0, 207, 117],     // Mangroves (cyan-green)
    100: [250, 230, 160]   // Moss and lichen (beige)
};

/**
 * Get ESA land cover color for a class value
 * @param {number} value - ESA WorldCover class value
 * @returns {Array<number>} [r, g, b] values (0-255)
 */
export function getEsaColor(value) {
    return ESA_COLORS[Math.round(value)] || [50, 50, 50];
}

/**
 * Available colormaps
 */
export const COLORMAPS = [
    { value: 'terrain', label: 'Terrain' },
    { value: 'viridis', label: 'Viridis' },
    { value: 'jet', label: 'Jet' },
    { value: 'rainbow', label: 'Rainbow' },
    { value: 'hot', label: 'Hot' },
    { value: 'gray', label: 'Gray' }
];

/**
 * Create a color gradient canvas for colorbar
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {string} colormap - Colormap name
 * @returns {HTMLCanvasElement}
 */
export function createColorGradient(width, height, colormap) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(width, height);
    
    for (let x = 0; x < width; x++) {
        const t = x / (width - 1);
        const [r, g, b] = mapElevationToColor(t, colormap);
        
        for (let y = 0; y < height; y++) {
            const idx = (y * width + x) * 4;
            imgData.data[idx] = Math.round(r * 255);
            imgData.data[idx + 1] = Math.round(g * 255);
            imgData.data[idx + 2] = Math.round(b * 255);
            imgData.data[idx + 3] = 255;
        }
    }
    
    ctx.putImageData(imgData, 0, 0);
    return canvas;
}

// Export default object
export default {
    hslToRgb,
    mapElevationToColor,
    ESA_COLORS,
    getEsaColor,
    COLORMAPS,
    createColorGradient
};
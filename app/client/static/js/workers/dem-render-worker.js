/**
 * workers/dem-render-worker.js
 * ============================
 * Off-thread DEM pixel-rendering worker.
 *
 * Receives a flat Float32Array of elevation values, a pre-built colour LUT
 * (Uint8Array, 1024 × 3 bytes), and the vmin/vmax range. Produces a
 * Uint8ClampedArray of RGBA pixels (ready for ImageData) and posts it back.
 *
 * The LUT is built on the main thread by buildColorLUT() (which calls
 * window.mapElevationToColor — a main-thread-only function) and is
 * zero-copy-transferred into the worker.
 *
 * Message protocol
 * ─────────────────
 * Incoming:
 *   { gen, values: Float32Array, width: number, height: number,
 *     lut: Uint8Array (1024 × 3), vmin: number, vmax: number }
 *   Transferables: [values.buffer, lut.buffer]
 *
 * Outgoing (success):
 *   { type: 'rendered', gen, pixels: Uint8ClampedArray, width, height }
 *   Transferables: [pixels.buffer]
 *
 * Outgoing (error):
 *   { type: 'error', gen, message: string }
 *
 * The main thread reconstructs the canvas with:
 *   ctx.putImageData(new ImageData(pixels, width, height), 0, 0)
 */

'use strict';

self.onmessage = function onmessage({ data }) {
    const { gen, values, width, height, lut, vmin, vmax } = data;

    try {
        const total = width * height;
        const pixels = new Uint8ClampedArray(total * 4);

        const len = values.length;
        const range = (vmax - vmin) || 1;
        const invRange = 1 / range;

        for (let i = 0; i < total; i++) {
            const val = (i < len) ? values[i] : NaN;
            const idx = i << 2;

            if (Number.isFinite(val)) {
                const t = (val - vmin) * invRange;
                const tClamped = t < 0 ? 0 : (t > 1 ? 1 : t);
                // LUT has 1024 entries × 3 bytes (R, G, B)
                const lutIdx = (tClamped * 1023 + 0.5 | 0) * 3;
                pixels[idx]     = lut[lutIdx];
                pixels[idx + 1] = lut[lutIdx + 1];
                pixels[idx + 2] = lut[lutIdx + 2];
                pixels[idx + 3] = 255;
            }
            // else: all bytes stay 0 (transparent — already initialised)
        }

        self.postMessage(
            { type: 'rendered', gen, pixels, width, height },
            [pixels.buffer],
        );
    } catch (err) {
        self.postMessage({ type: 'error', gen, message: String(err) });
    }
};

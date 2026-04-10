/**
 * city-worker.js — Off-thread city overlay renderer.
 *
 * Receives pre-baked Float32Array feature buffers from the main thread,
 * draws all city layers onto an OffscreenCanvas, and posts back an ImageBitmap.
 *
 * Message protocol (main → worker):
 *   {
 *     type: 'render',
 *     gen:  number,          // generation counter — stale replies are discarded
 *     W:    number,          // canvas pixel width
 *     H:    number,          // canvas pixel height
 *     tX, tY, tW, tH: number,  // letterbox draw rect
 *     invZ: number,          // 1 / stackZoom.scale
 *     layers: {
 *       waterways: LayerData | null,
 *       buildings: LayerData | null,
 *       walls:     LayerData | null,
 *       roads:     LayerData | null,
 *     },
 *     styles: {
 *       buildingsColor:  string,   // CSS color
 *       roadsColor:      string,
 *       waterwaysColor:  string,
 *       roadBaseWidth:   number,   // metres
 *       bboxLonM:        number,   // bbox longitude span in metres
 *     },
 *     toggles: {
 *       buildings:  boolean,
 *       roads:      boolean,
 *       waterways:  boolean,
 *     },
 *   }
 *
 * LayerData: { features: BakedFeature[] }
 * BakedFeature: {
 *   buf:    Float32Array,   // flat [x0,y0, x1,y1, ...] pixel coords
 *   counts: Uint16Array,    // vertex count per ring
 *   x0, y0, x1, y1: number, // pixel bounding box
 *   type: string,           // 'Polygon'|'MultiPolygon'|'LineString'|'MultiLineString'
 *   height_m: number,       // for buildings
 *   road_width_m: number,   // for roads
 * }
 *
 * Message protocol (worker → main):
 *   { type: 'bitmap', gen: number, bitmap: ImageBitmap }
 *   { type: 'error',  gen: number, message: string }
 */

'use strict';

const ALPHA_BUCKETS = 8;

// ---------------------------------------------------------------------------
// Draw helpers
// ---------------------------------------------------------------------------

function _drawFeatPath(ctx, feat, closePaths) {
    const { buf, counts } = feat;
    let i = 0;
    for (const count of counts) {
        ctx.moveTo(buf[i], buf[i + 1]); i += 2;
        for (let v = 1; v < count; v++, i += 2) ctx.lineTo(buf[i], buf[i + 1]);
        if (closePaths) ctx.closePath();
    }
}

function _culled(feat, x0, y0, x1, y1) {
    return feat.x1 < x0 || feat.x0 > x1 || feat.y1 < y0 || feat.y0 > y1;
}

function _renderLayer(ctx, msg, onlyLayer) {
    const { tX, tY, tW, tH, invZ, layers, styles, toggles } = msg;
    const { bboxLonM } = styles;
    const drawW = tW;
    const metrePerPx = bboxLonM / drawW;

    const clipX0 = tX, clipY0 = tY, clipX1 = tX + tW, clipY1 = tY + tH;

    function _shouldDraw(name) {
        return onlyLayer != null ? onlyLayer === name : !!toggles[name];
    }

    // ── Waterways ─────────────────────────────────────────────────────────────
    if (_shouldDraw('waterways') && layers.waterways?.features?.length) {
        const c = styles.waterwaysColor;
        ctx.globalAlpha = 0.65;

        // Polygons (lakes, ponds)
        ctx.fillStyle   = c + '88';
        ctx.strokeStyle = c;
        ctx.lineWidth   = 1 * invZ;
        for (const feat of layers.waterways.features) {
            if (_culled(feat, clipX0, clipY0, clipX1, clipY1)) continue;
            if (feat.type === 'Polygon' || feat.type === 'MultiPolygon') {
                ctx.beginPath();
                _drawFeatPath(ctx, feat, true);
                ctx.fill();
                ctx.stroke();
            }
        }

        // LineStrings (rivers, streams)
        ctx.strokeStyle = c;
        ctx.lineWidth   = 2 * invZ;
        ctx.beginPath();
        for (const feat of layers.waterways.features) {
            if (_culled(feat, clipX0, clipY0, clipX1, clipY1)) continue;
            if (feat.type === 'LineString' || feat.type === 'MultiLineString') {
                _drawFeatPath(ctx, feat, false);
            }
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    // ── Buildings — batched by opacity bucket ─────────────────────────────────
    if (_shouldDraw('buildings') && layers.buildings?.features?.length) {
        const baseC = styles.buildingsColor;
        ctx.strokeStyle = baseC;
        ctx.lineWidth   = 0.5 * invZ;
        ctx.fillStyle   = baseC;

        const buckets = Array.from({ length: ALPHA_BUCKETS }, () => []);
        for (const feat of layers.buildings.features) {
            if (feat.x1 - feat.x0 < 0.5 && feat.y1 - feat.y0 < 0.5) continue; // sub-pixel
            if (_culled(feat, clipX0, clipY0, clipX1, clipY1)) continue;
            const h  = feat.height_m || 10;
            const t  = Math.min(1, Math.max(0, (h - 3) / 77));
            const bi = Math.min(ALPHA_BUCKETS - 1, Math.floor(t * ALPHA_BUCKETS));
            buckets[bi].push(feat);
        }

        for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
            if (!buckets[bi].length) continue;
            ctx.globalAlpha = 0.40 + (bi / (ALPHA_BUCKETS - 1)) * 0.45;
            ctx.beginPath();
            for (const feat of buckets[bi]) _drawFeatPath(ctx, feat, true);
            ctx.fill();
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ── Roads — batched by lineWidth ──────────────────────────────────────────
    if (_shouldDraw('roads') && layers.roads?.features?.length) {
        const c = styles.roadsColor;
        ctx.strokeStyle = c;
        ctx.globalAlpha = 0.75;

        const groups = new Map();
        for (const feat of layers.roads.features) {
            if (_culled(feat, clipX0, clipY0, clipX1, clipY1)) continue;
            const widthM  = feat.road_width_m || styles.roadBaseWidth;
            const widthPx = Math.max(0.5, (widthM / metrePerPx) * invZ);
            const key     = Math.round(widthPx * 2) / 2;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(feat);
        }

        for (const [lineWidth, feats] of groups) {
            ctx.lineWidth = lineWidth;
            ctx.beginPath();
            for (const feat of feats) _drawFeatPath(ctx, feat, false);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }

    // ── Walls (drawn when buildings visible) ──────────────────────────────────
    if (_shouldDraw('buildings') && layers.walls?.features?.length) {
        const c = styles.buildingsColor;
        ctx.strokeStyle = c;
        ctx.lineWidth   = 3 * invZ;
        ctx.globalAlpha = 0.85;
        ctx.lineJoin    = 'round';
        ctx.lineCap     = 'round';

        for (const feat of layers.walls.features) {
            if (_culled(feat, clipX0, clipY0, clipX1, clipY1)) continue;
            if (feat.type === 'Polygon' || feat.type === 'MultiPolygon') {
                ctx.beginPath();
                _drawFeatPath(ctx, feat, true);
                ctx.stroke();
            }
        }

        ctx.beginPath();
        for (const feat of layers.walls.features) {
            if (_culled(feat, clipX0, clipY0, clipX1, clipY1)) continue;
            if (feat.type === 'LineString' || feat.type === 'MultiLineString') {
                _drawFeatPath(ctx, feat, false);
            }
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.lineJoin    = 'miter';
        ctx.lineCap     = 'butt';
    }
}

// ---------------------------------------------------------------------------
// Message handler
// ---------------------------------------------------------------------------

self.onmessage = function (e) {
    const msg = e.data;
    if (msg.type !== 'render') return;

    const { gen, W, H, tX, tY, tW, tH } = msg;

    try {
        const canvas = new OffscreenCanvas(W, H);
        const ctx    = canvas.getContext('2d');

        ctx.save();
        ctx.beginPath();
        ctx.rect(tX, tY, tW, tH);
        ctx.clip();
        _renderLayer(ctx, msg, null);  // draw all toggled layers
        ctx.restore();

        const bitmap = canvas.transferToImageBitmap();
        self.postMessage({ type: 'bitmap', gen, bitmap }, [bitmap]);
    } catch (err) {
        self.postMessage({ type: 'error', gen, message: err.message });
    }
};

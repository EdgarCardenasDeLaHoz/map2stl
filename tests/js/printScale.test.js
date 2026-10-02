/**
 * printScale.test.js — model scale, vertical exaggeration and bed/piece maths
 * (app/client/static/js/modules/export/print-scale.js).
 *
 * modelScale() must agree with city2stl/city_model.py:choose_scale and
 * piecesNeeded() with app/server/core/puzzle.py:plan_grid.
 */
import { describe, it, expect } from 'vitest';
import {
    fillBedMmPerPx, DEFAULT_BED, bedFitMm,
    AUTO_TRUE_SCALE_MAX_KM, bboxDiagonalKm, defaultPieceMm, formatGroundLength,
    modelScale, parseBedSize, piecesNeeded,
} from '../../app/client/static/js/modules/export/print-scale.js';

// 0.1° square on the equator: 11 132 m wide, 11 054 m tall, ~15.7 km diagonal.
const SMALL = { north: 0.1, south: 0, east: 0.1, west: 0 };
// 0.5° square: ~78 km diagonal, so Auto picks fit-to-height.
const LARGE = { north: 0.5, south: 0, east: 0.5, west: 0 };

describe('bboxDiagonalKm', () => {
    it('uses the flat-earth metres-per-degree constants', () => {
        expect(bboxDiagonalKm(SMALL)).toBeCloseTo(Math.hypot(11.132, 11.054), 3);
    });
});

describe('modelScale', () => {
    it('reports ground metres per model millimetre and the 1:N scale', () => {
        const s = modelScale({ bbox: SMALL, cols: 1000, rows: 1000, mmPerPx: 0.5 });
        const mPerPx = (11.132 + 11.054) / 2;
        expect(s.mPerPx).toBeCloseTo(mPerPx, 4);
        expect(s.mPerMm).toBeCloseTo(s.mPerPx / 0.5, 9);
        expect(s.scaleDenominator).toBe(Math.round(s.mPerPx * 1000 / 0.5));
        expect(s.widthMm).toBe(500);
        expect(s.depthMm).toBe(500);
    });

    it('auto is true scale under the diagonal limit, times the exaggeration', () => {
        expect(bboxDiagonalKm(SMALL)).toBeLessThan(AUTO_TRUE_SCALE_MAX_KM);
        const s = modelScale({ bbox: SMALL, cols: 1000, rows: 1000, mmPerPx: 0.5, exaggeration: 1.5 });
        expect(s.zMode).toBe('true');
        expect(s.verticalExaggeration).toBeCloseTo(1.5, 9);
        expect(s.zMmPerM).toBeCloseTo(0.5 / s.mPerPx * 1.5, 9);
    });

    it('auto fits to height over the diagonal limit', () => {
        const s = modelScale({
            bbox: LARGE, cols: 1000, rows: 1000, mmPerPx: 0.2,
            fitHeightMm: 30, elevMin: 0, elevMax: 2000,
        });
        expect(s.zMode).toBe('fit');
        expect(s.zMmPerM).toBeCloseTo(30 / 2000, 9);
        // vertical scale over horizontal scale
        expect(s.verticalExaggeration).toBeCloseTo((30 / 2000) / (0.2 / s.mPerPx), 9);
        expect(s.verticalExaggeration).toBeGreaterThan(4);
    });

    it('an explicit mode wins over the diagonal rule', () => {
        const s = modelScale({ bbox: LARGE, cols: 500, rows: 500, mmPerPx: 1, zMode: 'true', exaggeration: 2 });
        expect(s.zMode).toBe('true');
        expect(s.verticalExaggeration).toBeCloseTo(2, 9);
        const f = modelScale({ bbox: SMALL, cols: 500, rows: 500, mmPerPx: 1, zMode: 'fit', elevMin: 100, elevMax: 100 });
        expect(f.zMode).toBe('fit');
        expect(Number.isFinite(f.verticalExaggeration)).toBe(true);
        expect(f.flatRelief).toBe(true);
    });

    it('flags a flat DEM in fit mode instead of a huge exaggeration', () => {
        const flat = modelScale({ bbox: LARGE, cols: 500, rows: 500, mmPerPx: 1, elevMin: 0, elevMax: 0 });
        expect(flat.zMode).toBe('fit');
        expect(flat.flatRelief).toBe(true);
        const nan = modelScale({ bbox: LARGE, cols: 500, rows: 500, mmPerPx: 1, elevMin: NaN, elevMax: NaN });
        expect(nan.flatRelief).toBe(true);
        const hilly = modelScale({ bbox: LARGE, cols: 500, rows: 500, mmPerPx: 1, elevMin: 0, elevMax: 500 });
        expect(hilly.flatRelief).toBe(false);
        // True scale does not depend on relief, so a flat DEM is fine there.
        const t = modelScale({ bbox: SMALL, cols: 500, rows: 500, mmPerPx: 1, elevMin: 5, elevMax: 5 });
        expect(t.zMode).toBe('true');
        expect(t.flatRelief).toBe(false);
    });

    it('returns null without a usable bbox or grid', () => {
        expect(modelScale({ bbox: null, cols: 10, rows: 10, mmPerPx: 1 })).toBeNull();
        expect(modelScale({ bbox: SMALL, cols: 0, rows: 10, mmPerPx: 1 })).toBeNull();
        expect(modelScale({ bbox: SMALL, cols: 10, rows: 10, mmPerPx: 0 })).toBeNull();
    });
});

describe('formatGroundLength', () => {
    it('picks a readable unit', () => {
        expect(formatGroundLength(3.52)).toBe('3.5 m');
        expect(formatGroundLength(0.456)).toBe('0.46 m');
        expect(formatGroundLength(22.19)).toBe('22 m');
        expect(formatGroundLength(1500)).toBe('1.5 km');
        expect(formatGroundLength(NaN)).toBe('—');
    });
});

describe('parseBedSize / defaultPieceMm', () => {
    it('parses a preset and a custom bed', () => {
        expect(parseBedSize('250x210')).toEqual({ w: 250, h: 210 });
        expect(parseBedSize('custom', '300', '200')).toEqual({ w: 300, h: 200 });
        expect(parseBedSize('custom', '', 'x')).toEqual({ w: 220, h: 220 });
        expect(parseBedSize('garbage')).toEqual({ w: 220, h: 220 });   // default bed
    });

    it('defaults the piece to the shorter bed side less 10 mm', () => {
        expect(defaultPieceMm({ w: 250, h: 210 })).toBe(200);
        expect(defaultPieceMm({ w: 256, h: 256 })).toBe(246);
        expect(defaultPieceMm({ w: 20, h: 20 })).toBe(30);   // floor
    });
});

describe('piecesNeeded', () => {
    const bed = { w: 250, h: 210 };

    it('fits in either orientation', () => {
        expect(piecesNeeded(240, 200, bed, 200)).toEqual({ fits: true, cols: 1, rows: 1, pieces: 1 });
        expect(piecesNeeded(200, 240, bed, 200)).toEqual({ fits: true, cols: 1, rows: 1, pieces: 1 });
    });

    it('matches plan_grid: ceil(size / piece) per axis', () => {
        expect(piecesNeeded(600, 450, bed, 200)).toEqual({ fits: false, cols: 3, rows: 3, pieces: 9 });
        expect(piecesNeeded(401, 200.5, bed, 200)).toEqual({ fits: false, cols: 3, rows: 2, pieces: 6 });
    });

    it('falls back to the bed default piece', () => {
        expect(piecesNeeded(600, 300, bed, 0)).toEqual({ fits: false, cols: 3, rows: 2, pieces: 6 });
    });
});

describe('fillBedMmPerPx', () => {
    it('fills the default 220 x 220 bed less the margin', () => {
        expect(DEFAULT_BED).toBe('220x220');
        // 600 x 481 grid (Granada): 210 / 600 = 0.35 limits the width.
        expect(fillBedMmPerPx(600, 481, { w: 220, h: 220 })).toBe(0.35);
    });
    it('turns the model when that makes it bigger', () => {
        // Wide grid on a tall bed: turned, 240 / 400 = 0.6 instead of 200 / 400 = 0.5.
        expect(fillBedMmPerPx(400, 100, { w: 210, h: 250 }, 10)).toBe(0.6);
    });
    it('returns 0 for an empty grid', () => {
        expect(fillBedMmPerPx(0, 10, { w: 220, h: 220 })).toBe(0);
    });
});

describe('bedFitMm', () => {
    it('fills the 220 bed less the margin', () => {
        // Mont Blanc test box, 13.4 x 11.4 km: width limits, 210 x 178 mm.
        expect(bedFitMm(13.4, 11.4, { w: 220, h: 220 })).toEqual({ w: 210, h: 178 });
    });
    it('is zero for an empty box', () => {
        expect(bedFitMm(0, 5, { w: 220, h: 220 })).toEqual({ w: 0, h: 0 });
    });
});

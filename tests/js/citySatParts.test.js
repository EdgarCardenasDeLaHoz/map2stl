/**
 * citySatParts.test.js — satellite image on the finished model's parts (F-VIEW-SATPARTS):
 * planar UVs, the steep-face rule and the part-to-mode mapping
 * (app/client/static/js/modules/export/city-fullmodel.js).
 */
import { describe, it, expect } from 'vitest';
import {
    satelliteUVs, splitFacesBySlope, takesSatelliteImage, toViewerFrame,
} from '../../app/client/static/js/modules/export/city-fullmodel.js';

// A 101 x 51 px DEM at 2 mm/px: the box is 200 x 100 mm, drawn at scale 0.5.
const frame = { scale: 0.5, xOffset: 50, zOffset: 25, cols: 101, rows: 51, mmPerPx: 2 };

describe('satelliteUVs', () => {
    it('maps the four model corners to the image corners (north edge is v = 1)', () => {
        // model mm (x east, y north, z up): SW, SE, NW, NE
        const part = { positions: new Float32Array([0, 0, 0, 200, 0, 0, 0, 100, 0, 200, 100, 0]), indices: new Uint32Array() };
        toViewerFrame(part, frame);
        const uv = satelliteUVs(part.positions, frame);
        const want = [0, 0, 1, 0, 0, 1, 1, 1];
        uv.forEach((v, i) => expect(v).toBeCloseTo(want[i], 5));
    });
    it('ignores height', () => {
        const part = { positions: new Float32Array([100, 50, 0, 100, 50, 9]), indices: new Uint32Array() };
        toViewerFrame(part, frame);
        const uv = satelliteUVs(part.positions, frame);
        expect(uv[0]).toBeCloseTo(uv[2]); expect(uv[1]).toBeCloseTo(uv[3]);
        expect(uv[0]).toBeCloseTo(0.5); expect(uv[1]).toBeCloseTo(0.5);
    });
});

describe('splitFacesBySlope', () => {
    // viewer frame, y up. Top face (flat), side wall (vertical), 45 deg ramp, 70 deg slope, underside.
    const pos = new Float32Array([
        0, 0, 0, 1, 0, 0, 0, 0, 1,        // 0-2 flat, CCW seen from above: normal +y
        0, 0, 0, 0, 1, 0, 1, 0, 0,        // 3-5 vertical wall
    ]);
    it('sends flat faces to the image and vertical ones to the plain colour', () => {
        const r = splitFacesBySlope(pos, Uint32Array.from([0, 2, 1, 3, 4, 5]));
        expect([...r.flat]).toEqual([0, 2, 1]);
        expect([...r.steep]).toEqual([3, 4, 5]);
    });
    it('puts the cut at 60 degrees', () => {
        const tilt = (deg) => {
            const t = deg * Math.PI / 180;           // slope angle from horizontal, rising along x
            const p = new Float32Array([0, 0, 0, 0, 0, 1, Math.cos(t), Math.sin(t), 0]);
            return splitFacesBySlope(p, Uint32Array.from([0, 1, 2]));
        };
        expect(tilt(45).flat.length).toBe(3);
        expect(tilt(70).steep.length).toBe(3);
    });
    it('treats an underside as steep and a degenerate face as steep', () => {
        const down = splitFacesBySlope(pos, Uint32Array.from([0, 1, 2]));
        expect(down.steep.length).toBe(3);
        const zero = splitFacesBySlope(new Float32Array(9), Uint32Array.from([0, 1, 2]));
        expect(zero.steep.length).toBe(3);
    });
});

describe('takesSatelliteImage', () => {
    it('drapes ground, roads, railways, trails and green', () => {
        for (const n of ['terrain', 'roads', 'railways', 'trails', 'green']) expect(takesSatelliteImage(n)).toBe(true);
    });
    it('keeps buildings, water and trees plain unless roofs are on', () => {
        for (const n of ['buildings', 'churches', 'towers', 'walls', 'fortifications']) {
            expect(takesSatelliteImage(n)).toBe(false);
            expect(takesSatelliteImage(n, true)).toBe(true);
        }
        for (const n of ['waterways', 'trees', 'other']) expect(takesSatelliteImage(n, true)).toBe(false);
    });
});

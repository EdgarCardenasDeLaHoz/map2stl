/**
 * puzzleCuts.test.js — draggable puzzle cut positions
 * (app/client/static/js/modules/export/puzzle-cuts.js).
 *
 * The edges go to the server as col_edges_mm / row_edges_mm, which
 * numpy2stl's validate_edges requires to be [0, ..., size], strictly increasing.
 */
import { describe, it, expect } from 'vitest';
import {
    cutPolyline, evenEdges, gridKey, isCustom, jigsawCuts, knobFits, minPieceMm,
    moveEdge, nearestEdge, roundEdges, tonguePath,
} from '../../app/client/static/js/modules/export/puzzle-cuts.js';

describe('evenEdges', () => {
    it('splits the size into n equal pieces, ending exactly at the size', () => {
        expect(evenEdges(300, 3)).toEqual([0, 100, 200, 300]);
        expect(evenEdges(149, 1)).toEqual([0, 149]);
        const e = evenEdges(100, 3);
        expect(e[3]).toBe(100);
        expect(e).toHaveLength(4);
    });
});

describe('nearestEdge', () => {
    const e = [0, 100, 200, 300];
    it('finds the interior edge within the tolerance', () => {
        expect(nearestEdge(e, 104, 5)).toBe(1);
        expect(nearestEdge(e, 197, 5)).toBe(2);
    });
    it('never picks the model boundary, or anything out of reach', () => {
        expect(nearestEdge(e, 1, 5)).toBe(-1);
        expect(nearestEdge(e, 299, 5)).toBe(-1);
        expect(nearestEdge(e, 150, 5)).toBe(-1);
    });
});

describe('moveEdge', () => {
    const e = [0, 100, 200, 300];
    it('moves an interior edge and leaves the rest', () => {
        expect(moveEdge(e, 1, 130)).toEqual([0, 130, 200, 300]);
        expect(e).toEqual([0, 100, 200, 300]);
    });
    it('keeps the minimum piece size from both neighbours', () => {
        expect(moveEdge(e, 1, 190, 40)).toEqual([0, 160, 200, 300]);
        expect(moveEdge(e, 2, 5, 40)).toEqual([0, 100, 140, 300]);
    });
    it('ignores the boundary edges', () => {
        expect(moveEdge(e, 0, 50)).toEqual(e);
        expect(moveEdge(e, 3, 250)).toEqual(e);
    });
});

describe('helpers', () => {
    it('minPieceMm matches the server knob rule', () => {
        // knob 20 x 8, clearance 0.3: 8 + 10 + 0.6 < piece / 2  ->  piece > 37.2
        expect(minPieceMm(20, 8, 0.3)).toBeCloseTo(38.2);
    });
    it('isCustom only for moved edges', () => {
        expect(isCustom(evenEdges(300, 3))).toBe(false);
        expect(isCustom([0, 120, 200, 300])).toBe(true);
    });
    it('gridKey changes with the grid and size', () => {
        expect(gridKey(3, 2, 300, 200)).not.toBe(gridKey(3, 3, 300, 200));
        expect(gridKey(3, 2, 300, 200)).toBe('3x2@300.0x200.0');
    });
    it('roundEdges rounds to 0.1 mm', () => {
        expect(roundEdges([0, 33.333, 66.66, 100])).toEqual([0, 33.3, 66.7, 100]);
    });
});

// The outline the preview draws must be the one numpy2stl's jigsaw_outlines
// cuts: same tongue shapes, same fit rule, same checkerboard direction.
describe('tonguePath', () => {
    it('rectangular is the plain tab', () => {
        expect(tonguePath('rectangular', 10, 8)).toEqual([[0, -10], [8, -10], [8, 10], [0, 10]]);
    });
    it('dovetail widens from 0.6x at the cut to the full width at the tip', () => {
        expect(tonguePath('dovetail', 10, 8)).toEqual([[0, -6], [8, -6 / 0.6], [8, 10], [0, 6]]);
    });
    it('classic is a neck at half width and a head the full width', () => {
        const p = tonguePath('classic', 10, 8, 16);
        expect(p[0]).toEqual([0, -5]);
        expect(p[p.length - 1]).toEqual([0, 5]);
        expect(Math.max(...p.map(q => q[0]))).toBeCloseTo(8, 6);        // tip at u = depth
        const widest = Math.max(...p.map(q => Math.abs(q[1])));
        expect(widest).toBeLessThanOrEqual(10);        // sampled arc, so just short of k2
        expect(widest).toBeGreaterThan(9.9);
        expect(Math.min(...p.map(q => q[0]))).toBe(0);                 // never behind the cut
    });
    it('rejects an unknown shape', () => {
        expect(() => tonguePath('star', 10, 8)).toThrow(/unknown knob shape/);
    });
});

describe('knobFits', () => {
    it('is the server rule: depth + width / 2 + 2 * clearance < min piece / 2', () => {
        expect(knobFits(20, 8, 0.3, 40, 40)).toBe(true);     // 18.6 < 20
        expect(knobFits(20, 8, 0.3, 37, 40)).toBe(false);    // 18.6 > 18.5
        expect(knobFits(0, 8, 0.3, 100, 100)).toBe(false);   // no knob at all
    });
});

describe('cutPolyline', () => {
    it('runs along the edge and crosses it with the tongue', () => {
        const p = cutPolyline({ axis: 0, at: 100, lo: 0, hi: 60, centre: 30, sign: 1,
            shape: 'rectangular', halfWidth: 10, depth: 8 });
        expect(p[0]).toEqual([100, 0]);
        expect(p[p.length - 1]).toEqual([100, 60]);
        expect(p).toContainEqual([108, 20]);                 // tongue into the +x piece
        expect(Math.min(...p.map(q => q[0]))).toBe(100);
    });
    it('points the other way for sign -1, and across x on axis 1', () => {
        const v = cutPolyline({ axis: 0, at: 100, lo: 0, hi: 60, centre: 30, sign: -1,
            shape: 'rectangular', halfWidth: 10, depth: 8 });
        expect(Math.min(...v.map(q => q[0]))).toBe(92);
        const h = cutPolyline({ axis: 1, at: 50, lo: 0, hi: 60, centre: 30, sign: 1,
            shape: 'rectangular', halfWidth: 10, depth: 8 });
        expect(h[0]).toEqual([0, 50]);
        expect(h).toContainEqual([20, 58]);
    });
    it('is the straight edge without a knob', () => {
        expect(cutPolyline({ axis: 1, at: 50, lo: 0, hi: 60, centre: 30, sign: 1 }))
            .toEqual([[0, 50], [60, 50]]);
    });
});

describe('jigsawCuts', () => {
    const cols = evenEdges(300, 3);
    const rows = evenEdges(200, 2);
    const knob = { shape: 'rectangular', knobWidth: 20, knobDepth: 8, clearance: 0.3 };

    it('draws every interior cut once', () => {
        const { polylines, knobs } = jigsawCuts(cols, rows, knob);
        expect(knobs).toBe(true);
        expect(polylines).toHaveLength(2 * (3 - 1) + 3 * (2 - 1));   // 4 vertical, 3 horizontal
    });
    it('alternates the tongue like a checkerboard', () => {
        const { polylines } = jigsawCuts(cols, rows, knob);
        const vertical = polylines.slice(0, 4);                       // rows 0, 0, 1, 1
        const beyond = (p, at) => p.some(q => q[0] > at) && !p.some(q => q[0] < at);
        expect(beyond(vertical[0], 100)).toBe(true);                  // row 0: +x
        expect(beyond(vertical[2], 100)).toBe(false);                 // row 1: -x
        const horizontal = polylines.slice(4);                        // columns 0, 1, 2
        const above = (p, at) => p.some(q => q[1] > at) && !p.some(q => q[1] < at);
        expect(above(horizontal[0], 100)).toBe(true);                 // column 0: +y
        expect(above(horizontal[1], 100)).toBe(false);                // column 1: -y
    });
    it('falls back to straight cuts when the knob does not fit', () => {
        const { polylines, knobs } = jigsawCuts(evenEdges(60, 3), evenEdges(60, 3), knob);
        expect(knobs).toBe(false);
        expect(polylines.every(p => p.length === 2)).toBe(true);
    });
    it('follows dragged edges, measuring the knob against the smallest piece', () => {
        const { polylines, knobs } = jigsawCuts([0, 40, 300], rows, knob);
        expect(knobs).toBe(true);                                     // 40 mm piece > 38.2
        expect(polylines.slice(0, 2).every(p => p[0][0] === 40)).toBe(true);
        expect(jigsawCuts([0, 30, 300], rows, knob).knobs).toBe(false);
    });
});

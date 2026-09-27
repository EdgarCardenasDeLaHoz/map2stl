/**
 * puzzleCuts.test.js — draggable puzzle cut positions
 * (app/client/static/js/modules/export/puzzle-cuts.js).
 *
 * The edges go to the server as col_edges_mm / row_edges_mm, which
 * numpy2stl's validate_edges requires to be [0, ..., size], strictly increasing.
 */
import { describe, it, expect } from 'vitest';
import {
    evenEdges, gridKey, isCustom, minPieceMm, moveEdge, nearestEdge, roundEdges,
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

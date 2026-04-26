import { describe, it, expect } from 'vitest';
import { CurveEditorState } from './helpers/curveEditorState.js';

describe('CurveEditorState — initial state', () => {
    it('starts with two endpoints at (0,0) and (1,1)', () => {
        const s = new CurveEditorState();
        expect(s.getPoints()).toEqual([{ x: 0, y: 0 }, { x: 1, y: 1 }]);
    });

    it('starts with preset "linear"', () => {
        expect(new CurveEditorState().getPreset()).toBe('linear');
    });
});

describe('CurveEditorState — addPoint', () => {
    it('inserts a point and keeps the array sorted by x', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.5);
        const pts = s.getPoints();
        expect(pts.length).toBe(3);
        expect(pts[1]).toEqual({ x: 0.5, y: 0.5 });
    });

    it('clamps y between neighbouring points', () => {
        const s = new CurveEditorState();
        // With only endpoints y=0 and y=1, y=0.9 should be allowed unchanged.
        const added = s.addPoint(0.5, 0.9);
        expect(added).toBe(true);
        const mid = s.getPoints()[1];
        expect(mid.y).toBeCloseTo(0.9);
    });

    it('rejects a point within the snap threshold of an existing point', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.5);
        // 0.5 + 0.01 < threshold (0.08) → should be rejected
        const added = s.addPoint(0.51, 0.51);
        expect(added).toBe(false);
        expect(s.getPoints().length).toBe(3);
    });

    it('correctly constrains y when interior neighbours bound it', () => {
        const s = new CurveEditorState();
        s.addPoint(0.3, 0.3);
        // With left neighbour y=0.3 and right neighbour y=1.0,
        // adding y=0.1 (below left) should clamp to 0.3.
        s.addPoint(0.6, 0.1);
        const pts = s.getPoints();
        const pt = pts.find(p => Math.abs(p.x - 0.6) < 0.01);
        expect(pt.y).toBeGreaterThanOrEqual(0.3);
    });
});

describe('CurveEditorState — removePoint', () => {
    it('removes a middle point', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.5);
        const removed = s.removePoint(0.5, 0.5);
        expect(removed).toBe(true);
        expect(s.getPoints().length).toBe(2);
    });

    it('does not remove the first endpoint', () => {
        const s = new CurveEditorState();
        expect(s.removePoint(0, 0)).toBe(false);
        expect(s.getPoints().length).toBe(2);
    });

    it('does not remove the last endpoint', () => {
        const s = new CurveEditorState();
        expect(s.removePoint(1, 1)).toBe(false);
        expect(s.getPoints().length).toBe(2);
    });
});

describe('CurveEditorState — interpolate', () => {
    it('returns 0.5 at x=0.5 on a linear curve', () => {
        const s = new CurveEditorState();
        expect(s.interpolate(0.5)).toBeCloseTo(0.5);
    });

    it('returns 0 at x=0', () => {
        expect(new CurveEditorState().interpolate(0)).toBeCloseTo(0);
    });

    it('returns 1 at x=1', () => {
        expect(new CurveEditorState().interpolate(1)).toBeCloseTo(1);
    });

    it('interpolates between a custom midpoint', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.8);
        // Between x=0 (y=0) and x=0.5 (y=0.8): at x=0.25 → y=0.4
        expect(s.interpolate(0.25)).toBeCloseTo(0.4);
    });
});

describe('CurveEditorState — serialize / deserialize', () => {
    it('round-trips through serialize + deserialize', () => {
        const s = new CurveEditorState();
        s.addPoint(0.3, 0.4);
        s.addPoint(0.7, 0.8);
        const data = s.serialize();

        const s2 = new CurveEditorState();
        s2.deserialize(data);
        expect(s2.getPoints()).toEqual(s.getPoints());
        expect(s2.getPreset()).toBe(data.preset);
    });

    it('serializes the preset name', () => {
        const s = new CurveEditorState();
        s.setPoints([{ x: 0, y: 0 }, { x: 1, y: 1 }], 's-curve');
        expect(s.serialize().preset).toBe('s-curve');
    });
});

describe('CurveEditorState — undo / redo', () => {
    it('undo reverts an addPoint', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.5);
        expect(s.getPoints().length).toBe(3);
        const undid = s.undo();
        expect(undid).toBe(true);
        expect(s.getPoints().length).toBe(2);
    });

    it('undo returns false when already at initial state', () => {
        const s = new CurveEditorState();
        expect(s.undo()).toBe(false);
    });

    it('redo re-applies a reverted addPoint', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.5);
        s.undo();
        const redid = s.redo();
        expect(redid).toBe(true);
        expect(s.getPoints().length).toBe(3);
    });

    it('redo returns false when at the latest state', () => {
        const s = new CurveEditorState();
        expect(s.redo()).toBe(false);
    });

    it('undo reverts a removePoint', () => {
        const s = new CurveEditorState();
        s.addPoint(0.5, 0.5);
        s.removePoint(0.5, 0.5);
        expect(s.getPoints().length).toBe(2);
        s.undo();
        expect(s.getPoints().length).toBe(3);
    });
});

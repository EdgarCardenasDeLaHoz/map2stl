import { describe, it, expect } from 'vitest';
import {
    apply, invert2x3, decompose, plateCorners, plateCentre, translate, rotateAbout, scaleLocal,
    flipPlate, scaleAbout, rotatePlate, cornerScale, rotateDelta, rotateHandle, offsetMetres,
    offsetText, fmtIou, iouTone, fmtGap, parseCenter, windowCentreText, buildGroundTruth,
    placementFromSaved, fitPoints, toScreen, fromScreen, zoomView, hitPlate, refineText, keyOp,
} from '../../app/client/static/js/modules/ui/plate-align-format.js';

const near = (a, b, d = 1e-6) => expect(Math.abs(a - b)).toBeLessThan(d);
const M0 = [[0.7, 0, 80], [0, 0.7, 90]];

describe('matrices', () => {
    it('inverts and round-trips a point', () => {
        const M = [[0.6, -0.2, 10], [0.2, 0.6, -4]];
        const [x, y] = apply(invert2x3(M), ...apply(M, 33, 44));
        near(x, 33); near(y, 44);
        expect(() => invert2x3([[0, 0, 1], [0, 0, 2]])).toThrow();
    });

    it('decomposes scale, rotation and flip', () => {
        const r = Math.PI / 6;
        const M = [[Math.cos(r) * 2, -Math.sin(r) * 2, 5], [Math.sin(r) * 2, Math.cos(r) * 2, 6]];
        const p = decompose(M);
        near(p.scale, 2); near(p.rot_deg, 30); expect(p.flip).toBe(false);
        expect(decompose(flipPlate(M0, 512, 512)).flip).toBe(true);
    });
});

describe('manipulation keeps the plate centre', () => {
    it('rotate, scale and flip about the centre', () => {
        const c0 = plateCentre(M0, 512, 512);
        for (const M of [rotatePlate(M0, 512, 512, 12), scaleAbout(M0, 512, 512, 1.3),
            flipPlate(M0, 512, 512), scaleLocal(M0, 512, 512, 1.2, 0.8)]) {
            const c = plateCentre(M, 512, 512);
            near(c[0], c0[0]); near(c[1], c0[1]);
        }
    });

    it('rotateAbout a far point moves the centre; 360 degrees is the identity', () => {
        const R = rotateAbout(M0, 10, 20, 360);
        for (let i = 0; i < 2; i++) for (let j = 0; j < 3; j++) near(R[i][j], M0[i][j], 1e-9);
        const q = rotateAbout(M0, 0, 0, 90);
        const [x, y] = apply(q, 0, 0);
        near(x, -90); near(y, 80);
    });

    it('translate shifts only t', () => {
        expect(translate(M0, 2, -3)).toEqual([[0.7, 0, 82], [0, 0.7, 87]]);
    });
});

describe('handles', () => {
    it('corner drag scales about the centre, locked or per axis, with clamping', () => {
        const c = plateCentre(M0, 512, 512);
        const corner = apply(M0, 512, 512);
        const out = [c[0] + (corner[0] - c[0]) * 1.5, c[1] + (corner[1] - c[1]) * 1.5];
        near(decompose(cornerScale(M0, 512, 512, corner, out, true)).scale, 0.7 * 1.5);
        const wide = [c[0] + (corner[0] - c[0]) * 2, corner[1]];
        const U = cornerScale(M0, 512, 512, corner, wide, false);
        near(decompose(U).scale_x, 1.4); near(decompose(U).scale, 0.7);
        const huge = [c[0] + 1e6, c[1] + 1e6];
        near(decompose(cornerScale(M0, 512, 512, corner, huge, true)).scale, 0.7 * 5);
    });

    it('rotate drag measures the angle', () => {
        near(rotateDelta([0, 0], [10, 0], [0, 10]), 90);
        near(rotateDelta([0, 0], [-10, -1], [-10, 1]), -11.4212, 1e-3);
    });

    it('the rotate handle sits beyond the top edge', () => {
        const h = rotateHandle(M0, 512, 512, 20);
        const top = apply(M0, 256, 0);
        near(h[0], top[0]); near(h[1], top[1] - 20);
    });

    it('hit testing picks the rotate handle, a corner, the body, or nothing', () => {
        const view = { k: 1, vx: 0, vy: 0 };
        const c = plateCorners(M0, 512, 512);
        const rh = rotateHandle(M0, 512, 512, 28);
        expect(hitPlate(view, M0, 512, 512, rh[0], rh[1])).toBe('rotate');
        expect(hitPlate(view, M0, 512, 512, c[2][0] + 2, c[2][1])).toBe('c2');
        expect(hitPlate(view, M0, 512, 512, 200, 200)).toBe('body');
        expect(hitPlate(view, M0, 512, 512, 1000, 1000)).toBeNull();
    });
});

describe('texts', () => {
    it('offset against the reference, north up', () => {
        const o = offsetMetres(translate(M0, 2, -1), M0, 512, 512, 5);
        near(o.east, 10); near(o.north, 5);
        const t = offsetText(rotatePlate(translate(M0, 2, -1), 512, 512, -6), M0, 512, 512, 5);
        expect(t).toBe('Offset 10.0 m E, 5.0 m N · rotation −6.0° · scale 1.000');
        expect(offsetText(null, M0, 512, 512, 5)).toBe('');
    });

    it('overlap and gap', () => {
        expect(fmtIou(0.614)).toBe('0.61');
        expect(fmtIou(null)).toBe('—');
        expect(iouTone(0.6)).toBe('good'); expect(iouTone(0.3)).toBe('warn'); expect(iouTone(0.04)).toBe('bad');
        expect(fmtGap(2.34)).toBe('2.3 m'); expect(fmtGap(undefined)).toBe('—');
    });

    it('refine text', () => {
        expect(refineText({ improved: false, iou_before: 0.3 })).toContain('Nothing better');
        expect(refineText({ improved: true, iou_before: 0.3, iou_after: 0.5, delta_translation_px: 2, delta_rot_deg: 0.5 }))
            .toBe('Refined: overlap 0.30 → 0.50, moved 2.0 px, 0.50°.');
    });

    it('window centre and centre parsing', () => {
        expect(windowCentreText([42, 40, 3, 1])).toBe('41.00000, 2.00000');
        expect(windowCentreText(null)).toBe('');
        expect(parseCenter('')).toBeNull();
        expect(parseCenter('41.38, 2.17')).toEqual([41.38, 2.17]);
        expect(parseCenter('41.38 2.17')).toEqual([41.38, 2.17]);
        expect(() => parseCenter('abc')).toThrow(/lat, lon/);
        expect(() => parseCenter('95, 2')).toThrow(/out of range/);
    });
});

describe('saved placement', () => {
    const meta = { city: 'X', region: 'Y', slug: 'x', resolution: 512, cell_size_m: 5, bbox_nsew: [1, 0, 1, 0], guess: { matrix: M0 } };

    it('writes both forms and reads them back', () => {
        const gt = buildGroundTruth(meta, M0, 'osm', new Date(0));
        expect(gt.exported_at).toBe('1970-01-01T00:00:00.000Z');
        expect(gt.ui.flip).toBe(false);
        near(gt.ui.osm_to_stl.scale, 1 / 0.7);
        const back = placementFromSaved(gt);
        for (let i = 0; i < 2; i++) for (let j = 0; j < 3; j++) near(back[i][j], M0[i][j], 1e-9);
    });

    it('reads an old file that has only the UI form', () => {
        const N = invert2x3(M0), q = decompose(N);
        const back = placementFromSaved({ ui: { osm_to_stl: { scale: q.scale, rot_deg: q.rot_deg, tx: q.tx, ty: q.ty } } });
        near(back[0][2], 80, 1e-6);
        expect(placementFromSaved({})).toBeNull();
        expect(placementFromSaved(null)).toBeNull();
    });

    it('a mirrored plate keeps the matrix and drops the UI form', () => {
        const gt = buildGroundTruth(meta, flipPlate(M0, 512, 512), 'osm');
        expect(gt.ui.flip).toBe(true);
        expect(gt.ui.osm_to_stl).toBeUndefined();
        expect(decompose(placementFromSaved(gt)).flip).toBe(true);
    });
});

describe('view', () => {
    it('fits, converts and zooms about a point', () => {
        const v = fitPoints([[0, 0], [100, 50]], 200, 200, 1);
        near(v.k, 2);
        const [sx, sy] = toScreen(v, 10, 10);
        const [x, y] = fromScreen(v, sx, sy);
        near(x, 10); near(y, 10);
        const z = zoomView(v, 50, 60, 2);
        const [x2, y2] = fromScreen(z, 50, 60);
        const [x1, y1] = fromScreen(v, 50, 60);
        near(x1, x2); near(y1, y2);
        expect(zoomView(v, 0, 0, 1e9).k).toBe(80);
    });
});

describe('keys', () => {
    it('maps the nudge keys', () => {
        expect(keyOp('ArrowLeft', false)).toEqual({ op: 'move', dx: -0.5, dy: 0 });
        expect(keyOp('ArrowDown', true)).toEqual({ op: 'move', dx: 0, dy: 5 });
        expect(keyOp('q', true)).toEqual({ op: 'rotate', deg: -1 });
        expect(keyOp('+', false).op).toBe('scale');
        expect(keyOp('x', false)).toBeNull();
    });
});

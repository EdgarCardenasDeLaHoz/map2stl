import { describe, it, expect } from 'vitest';
import {
    stepHint, pairsText, rmsTone, fmtRms, compass, cameraRows, photoStatus, photoName, labelsPayload,
    fitRect, photoToScreen, screenToPhoto, insidePhoto, llToScreen, screenToLl, fitMapView, zoomMap,
    panMap, mapRadius, needsFootprints, fovWedge, cameraFromCandidate, candidateText, inRing,
    footprintAt, diffText, heightRows, keepText, savedHeightsText, commonsText,
} from '../../app/client/static/js/modules/ui/photo-solve-format.js';

const near = (a, b, d = 1e-6) => expect(Math.abs(a - b)).toBeLessThan(d);

describe('hints and texts', () => {
    it('guides the next step', () => {
        expect(stepHint(0, null, false)).toContain('You need 4 pairs');
        expect(stepHint(2, null, false)).toContain('Add 2 more');
        expect(stepHint(2, [1, 2], false)).toBe('Now click the same spot on the map.');
        expect(stepHint(4, null, false)).toContain('Solve camera');
        expect(stepHint(5, null, true)).toContain('Camera solved');
        expect(pairsText(1)).toBe('1 pair');
        expect(pairsText(3)).toBe('3 pairs');
    });

    it('colours the fit error', () => {
        expect(rmsTone(2)).toBe('good'); expect(rmsTone(7)).toBe('warn');
        expect(rmsTone(30)).toBe('bad'); expect(rmsTone(null)).toBe('dim');
        expect(fmtRms(5.504)).toBe('5.5 px'); expect(fmtRms(undefined)).toBe('—');
    });

    it('compass points', () => {
        expect(compass(0)).toBe('N'); expect(compass(336.6)).toBe('NNW'); expect(compass(-90)).toBe('W');
        expect(compass(null)).toBe('');
    });

    it('camera card rows', () => {
        const rows = cameraRows({ rms_px: 5.5, lat: 37.17729, lon: -3.59209, elev_m: 783.1, heading_deg: 336.6, pitch_deg: -14.5, hfov_deg: 71.07 });
        expect(rows.map((r) => r.label)).toEqual(['Fit error', 'Position', 'Height', 'Looking', 'Field of view']);
        expect(rows[0].tone).toBe('warn');
        expect(rows[1].value).toBe('37.17729, -3.59209');
        expect(rows[3].value).toBe('337° NNW · tilt -14.5°');
        expect(rows[4].value).toBe('71°');
        expect(cameraRows(null)).toEqual([]);
    });

    it('photo status and names', () => {
        expect(photoStatus({ pairs: 0 })).toBe('0 pairs');
        expect(photoStatus({ pairs: 5, solved: true, rms_px: 2.04 })).toBe('5 pairs · solved 2.0 px');
        expect(photoStatus({ pairs: 0, solved: true, camera_source: 'auto' })).toBe('0 pairs · auto start camera');
        expect(photoStatus({ bad: true })).toBe('Flagged unusable');
        expect(photoName('granada_albaicin/photo.png')).toBe('granada albaicin');
        expect(photoName('x.jpg')).toBe('x.jpg');
    });

    it('labels payload', () => {
        const b = labelsPayload('a/photo.png', [{ x: 1, y: 2, lat: 3, lon: 4, label: 'A' }], [800, 600], { lat: 1 }, 'solve');
        expect(b.camera_source).toBe('solve'); expect(b.labels[0].label).toBe('A');
        expect(labelsPayload('a', [], [1, 1], null).camera).toBeUndefined();
    });

    it('heights', () => {
        expect(diffText(12, 9)).toBe('+3.0 m'); expect(diffText(5, 9.5)).toBe('−4.5 m');
        expect(diffText(5, null)).toBe('no OSM height'); expect(diffText(NaN, 3)).toBe('');
        expect(heightRows([{ photo_m: 10, osm_m: 8 }, { photo_m: null }]).length).toBe(1);
        expect(keepText(0)).toBe('No heights to keep'); expect(keepText(3)).toBe('Use these heights (3)');
        expect(savedHeightsText({ ok: true, saved: 1 })).toContain('Kept 1 height from');
        expect(savedHeightsText(null)).toBe('');
    });

    it('commons text', () => {
        expect(commonsText({ title: 'File:Skyline.jpg', width: 4000, height: 3000 })).toBe('Skyline.jpg · 4000×3000');
    });
});

describe('photo view', () => {
    it('fits the picture and converts both ways', () => {
        const f = fitRect(800, 400, 408, 408, 4);
        near(f.k, 0.5); near(f.ox, 4); near(f.oy, 104);
        const [sx, sy] = photoToScreen(f, 100, 50);
        const [x, y] = screenToPhoto(f, sx, sy);
        near(x, 100); near(y, 50);
        expect(insidePhoto(10, 10, 800, 400)).toBe(true);
        expect(insidePhoto(-1, 10, 800, 400)).toBe(false);
        expect(fitRect(0, 0, 1, 1).k).toBe(1);
    });
});

describe('map view', () => {
    const v = { lat: 37.18, lon: -3.59, mpp: 2 };
    it('round-trips a point and puts north up', () => {
        const [sx, sy] = llToScreen(v, 800, 600, 37.181, -3.589);
        const [lat, lon] = screenToLl(v, 800, 600, sx, sy);
        near(lat, 37.181, 1e-9); near(lon, -3.589, 1e-9);
        expect(sy).toBeLessThan(300);
        expect(sx).toBeGreaterThan(400);
    });

    it('fits points, zooms about a point, pans', () => {
        const f = fitMapView([[37.1, -3.6], [37.2, -3.5]], 800, 600);
        near(f.lat, 37.15); expect(f.mpp).toBeGreaterThan(10);
        expect(fitMapView([], 800, 600)).toBeNull();
        const z = zoomMap(v, 800, 600, 600, 100, 2);
        near(z.mpp, 1);
        const a = screenToLl(v, 800, 600, 600, 100), b = screenToLl(z, 800, 600, 600, 100);
        near(a[0], b[0], 1e-6); near(a[1], b[1], 1e-6);
        expect(zoomMap(v, 800, 600, 0, 0, 1e9).mpp).toBe(0.1);
        const p = panMap(v, 10, 0);
        expect(p.lon).toBeLessThan(v.lon);
    });

    it('decides when to load footprints', () => {
        const r = mapRadius(v, 800, 600);
        expect(r).toBeGreaterThanOrEqual(200);
        expect(needsFootprints(null, v, r)).toBe(true);
        expect(needsFootprints({ lat: v.lat, lon: v.lon, radius: r }, v, r)).toBe(false);
        expect(needsFootprints({ lat: v.lat, lon: v.lon, radius: r }, { ...v, lat: v.lat + 0.01 }, r)).toBe(true);
        expect(needsFootprints({ lat: v.lat, lon: v.lon, radius: r }, v, r * 2)).toBe(true);
    });
});

describe('camera on the map', () => {
    it('wedge edges are half the field of view either side', () => {
        const w = fovWedge({ lat: 10, lon: 20, heading_deg: 90, hfov_deg: 60 }, 1000);
        near(w.centre[0], 10, 1e-6); expect(w.centre[1]).toBeGreaterThan(20);
        expect(w.left[0]).toBeGreaterThan(10); expect(w.right[0]).toBeLessThan(10);
        expect(fovWedge(null, 10)).toBeNull();
    });

    it('candidate camera and text', () => {
        const c = cameraFromCandidate({ lat: 1, lon: 2, elev_m: 3, heading_deg: 90, pitch_deg: -2, f_px: 500, width: 1000, label: 'Title: from X' });
        near(c.hfov_deg, 90, 1e-6); expect(c.roll_deg).toBe(0);
        expect(candidateText({ label: 'Title: from X', heading_deg: 90 }, 0)).toBe('1. Title: from X · looking 90° E');
    });
});

describe('footprints', () => {
    const sq = { id: 1, ring: [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]] };
    it('point in ring', () => {
        expect(inRing(0.5, 0.5, sq.ring)).toBe(true);
        expect(inRing(1.5, 0.5, sq.ring)).toBe(false);
        expect(footprintAt([sq], 0.2, 0.2).id).toBe(1);
        expect(footprintAt([sq], 5, 5)).toBeNull();
    });
});

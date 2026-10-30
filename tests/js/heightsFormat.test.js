import { describe, it, expect } from 'vitest';
import {
    fmtCount, reportSub, tierRows, measuredCount, capFraction, capText, fmtSharePct, confirmInfo,
    stepHeading, stepText, runPercent, gpuText, boundsOf, fitView, project, unproject, zoomAt,
    panBy, pointInRing, hitTest, hoverInfo,
} from '../../app/client/static/js/modules/ui/heights-format.js';

const sq = (x, y, s) => [[x, y], [x + s, y], [x + s, y + s], [x, y + s]];

describe('texts', () => {
    it('counts and report sub line', () => {
        expect(fmtCount(1840)).toBe('1,840');
        expect(fmtCount(null)).toBe('—');
        const t = new Date(2026, 9, 23, 12).getTime() / 1000;
        expect(reportSub({ mtime: t, n_buildings: 902 })).toBe('Oct 23 · 902 buildings');
        expect(reportSub({ mtime: t, n_buildings: 1 })).toBe('Oct 23 · 1 building');
    });

    it('tier rows keep zero rows and the API colour', () => {
        const rows = tierRows({ corroborated: 3 }, { corroborated: { color: '#009E73' } });
        expect(rows.map((r) => r.count)).toEqual([3, 0, 0, 0]);
        expect(rows[0].color).toBe('#009E73');
        expect(rows[2].label).toBe('OSM tag (kept)');
        const old = tierRows({ unlabelled: 5 }, { unlabelled: { color: '#CCCCCC' } });
        expect(old.map((r) => r.key)).toEqual(['corroborated', 'single', 'tag', 'prior', 'unlabelled']);
        expect(old[4].label).toBe('No tier recorded');
        expect(measuredCount({ corroborated: 3, single: 4, prior: 9 })).toBe(7);
    });

    it('cap bar maths', () => {
        expect(capFraction(1840, 10000)).toBeCloseTo(0.184);
        expect(capFraction(20000, 10000)).toBe(1);
        expect(capFraction(1, 0)).toBeNull();
        expect(capText(1840, 10000)).toBe('1,840 / 10,000');
        expect(fmtSharePct(0.0123)).toBe('1.2 %');
        expect(fmtSharePct(0.5)).toBe('50 %');
    });
});

describe('confirmInfo', () => {
    const est = { paid_fetches: 500, cap: 10000, month_count: 1840, over_warn: false };
    it('asks only when paid fetches are allowed and needed', () => {
        expect(confirmInfo(est, false)).toBeNull();
        expect(confirmInfo({ ...est, paid_fetches: 0 }, true)).toBeNull();
        const c = confirmInfo(est, true);
        expect(c.paid).toBe(500);
        expect(c.text).toContain('500');
        expect(c.text).toContain('5.0 %');
        expect(c.warn).toBe(false);
        expect(c.warnText).toBe('');
    });
    it('says "at least" for a rough estimate', () => {
        expect(confirmInfo({ ...est, rough: true }, true).text).toContain('at least 500');
        expect(confirmInfo(est, true).text).toContain('about 500');
    });
    it('flags the 80 % line', () => {
        const c = confirmInfo({ ...est, month_count: 7900, over_warn: true }, true);
        expect(c.warn).toBe(true);
        expect(c.warnText).toContain('84 %');
    });
});

describe('run text', () => {
    const job = { step: 3, n_steps: 4, step_label: 'Detecting rooflines', done: 128, total: 212,
                  report: 'Cartagena_v21_skyline_report' };
    it('heading, step line, percent, gpu', () => {
        expect(stepHeading(job, 'Cartagena')).toBe('Cartagena v21 · step 3 of 4');
        expect(stepText(job)).toBe('Detecting rooflines · 128 / 212 panoramas');
        expect(stepText({ step_label: 'Reading the map' })).toBe('Reading the map');
        expect(runPercent({ ...job, done: 0, total: 0 })).toBe(50);
        expect(runPercent(job)).toBe(65);
        expect(runPercent(null)).toBe(0);
        expect(gpuText('in use')).toBe('GPU: in use by this run');
        expect(gpuText('waiting')).toBe('GPU: waiting for GPU');
        expect(gpuText('idle')).toBe('');
    });
});

describe('map view', () => {
    const bs = [{ id: 'a', ring: sq(-75.5, 10.4, 0.01) }, { id: 'b', ring: sq(-75.49, 10.41, 0.002) }];
    it('bounds and fit keep everything inside the box', () => {
        const b = boundsOf(bs);
        expect(b.west).toBe(-75.5);
        expect(boundsOf([])).toBeNull();
        const v = fitView(b, 800, 600);
        for (const [lon, lat] of [[b.west, b.south], [b.east, b.north]]) {
            const [x, y] = project(v, lon, lat);
            expect(x).toBeGreaterThanOrEqual(0);
            expect(x).toBeLessThanOrEqual(800);
            expect(y).toBeGreaterThanOrEqual(0);
            expect(y).toBeLessThanOrEqual(600);
        }
    });
    it('project and unproject round-trip', () => {
        const v = fitView(boundsOf(bs), 800, 600);
        const [x, y] = project(v, -75.495, 10.405);
        const [lon, lat] = unproject(v, x, y);
        expect(lon).toBeCloseTo(-75.495, 9);
        expect(lat).toBeCloseTo(10.405, 9);
    });
    it('zoom keeps the point under the cursor, pan moves the centre', () => {
        const v = fitView(boundsOf(bs), 800, 600);
        const before = unproject(v, 300, 200);
        const z = zoomAt(v, 300, 200, 2, v.scale);
        const after = unproject(z, 300, 200);
        expect(after[0]).toBeCloseTo(before[0], 9);
        expect(after[1]).toBeCloseTo(before[1], 9);
        expect(z.scale).toBeCloseTo(v.scale * 2);
        expect(zoomAt(v, 0, 0, 1e9, v.scale).scale).toBeCloseTo(v.scale * 200);
        const p = panBy(v, 100, 0);
        const [x] = project(p, v.cx, v.cy);
        expect(x).toBeCloseTo(v.w / 2 + 100);
    });
});

describe('hit testing', () => {
    const big = { id: 'big', ring: sq(0, 0, 10) };
    const small = { id: 'small', ring: sq(4, 4, 1) };
    it('point in ring', () => {
        expect(pointInRing(5, 5, big.ring)).toBe(true);
        expect(pointInRing(11, 5, big.ring)).toBe(false);
    });
    it('smallest footprint wins; nothing outside; tolerance finds near vertices', () => {
        expect(hitTest([big, small], 4.5, 4.5).id).toBe('small');
        expect(hitTest([big, small], 8, 8).id).toBe('big');
        expect(hitTest([big, small], 20, 20)).toBeNull();
        expect(hitTest([big, small], 10.1, 10.1, 0.5).id).toBe('big');
    });
});

describe('hover card', () => {
    const tiers = { single: { label: 'Single reading' } };
    it('names, height, tier, panos and tag', () => {
        const h = hoverInfo({ id: 'w1', name: null, h: 31.46, tier: 'single', panos: 1, tag: null }, tiers);
        expect(h).toEqual({ title: 'Building w1', height: '31.5 m', tier: 'Single reading',
                            panos: '1 panorama', tag: 'OSM tag: none' });
        expect(hoverInfo({ id: 'x', name: 'Torre', h: 20, tier: 'prior', panos: 0, tag: 18 }, tiers).tag)
            .toBe('OSM tag: 18.0 m');
        expect(hoverInfo(null, tiers)).toBeNull();
    });
});

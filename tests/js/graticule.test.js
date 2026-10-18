// True grid math (modules/dem/graticule.js): where a lat/lon lands in the projected height map.
// The expected values restate geo2stl/projections.py, so a drift on either side fails here.
import { describe, it, expect } from 'vitest';
import {
    bendsMeridians, formatDegree, frameAspect, geoAtFrac, graticuleLines, niceDegreeStep, projectedFrac,
    projectedShape,
} from '../../app/client/static/js/modules/dem/graticule.js';

const EUROPA = { north: 68.65655, south: 27.83908, east: 47.28516, west: -16.34766 };
const rad = (d) => d * Math.PI / 180;

describe('projectedFrac', () => {
    it('puts the corners of the box on the corners of the raster (cylindrical)', () => {
        for (const p of ['none', 'cosine', 'equidistant', 'mercator', 'lambert', 'miller', 'gall']) {
            const at = projectedFrac(EUROPA, p);
            expect(at(EUROPA.north, EUROPA.west).x).toBeCloseTo(0, 9);
            expect(at(EUROPA.north, EUROPA.west).y).toBeCloseTo(0, 9);
            expect(at(EUROPA.south, EUROPA.east).x).toBeCloseTo(1, 9);
            expect(at(EUROPA.south, EUROPA.east).y).toBeCloseTo(1, 9);
        }
    });

    it('cosine with a true-shape canvas is an even resize: linear in both axes', () => {
        const at = projectedFrac(EUROPA, 'cosine', false);
        const p = at(50, 10);
        expect(p.x).toBeCloseTo((10 - EUROPA.west) / (EUROPA.east - EUROPA.west), 9);
        expect(p.y).toBeCloseTo((EUROPA.north - 50) / (EUROPA.north - EUROPA.south), 9);
    });

    it('cosine on a fixed canvas narrows each row by cos(lat)/mean cos, centred', () => {
        const at = projectedFrac(EUROPA, 'cosine', true);
        // The centre meridian stays put; the edges pull in at the north and push out at the south.
        const mid = (EUROPA.east + EUROPA.west) / 2;
        expect(at(60, mid).x).toBeCloseTo(0.5, 9);
        expect(at(EUROPA.north, EUROPA.west).x).toBeGreaterThan(0.05);
        expect(at(EUROPA.south, EUROPA.west).x).toBeLessThan(0);
        let sum = 0;
        for (let k = 0; k < 257; k++) sum += Math.cos(rad(EUROPA.north - (k / 256) * (EUROPA.north - EUROPA.south)));
        const avg = sum / 257;
        expect(at(45, EUROPA.east).x).toBeCloseTo(0.5 + 0.5 * Math.cos(rad(45)) / avg, 9);
        expect(projectedFrac(EUROPA, 'equidistant', true)(45, EUROPA.east).x).toBeCloseTo(at(45, EUROPA.east).x, 12);
    });

    it('mercator spaces parallels by ln tan(pi/4 + lat/2)', () => {
        const f = (l) => Math.log(Math.tan(Math.PI / 4 + rad(l) / 2));
        const y = projectedFrac(EUROPA, 'mercator')(50, 0).y;
        expect(y).toBeCloseTo((f(EUROPA.north) - f(50)) / (f(EUROPA.north) - f(EUROPA.south)), 9);
        // Stretched towards the pole: 50°N sits lower in the picture than its linear place.
        expect(y).not.toBeCloseTo((EUROPA.north - 50) / (EUROPA.north - EUROPA.south), 3);
    });

    it('lambert, miller and gall use their own y(lat)', () => {
        const box = { north: 60, south: 0, east: 10, west: 0 };
        const lam = (l) => Math.sin(rad(l));
        expect(projectedFrac(box, 'lambert')(30, 5).y).toBeCloseTo((lam(60) - lam(30)) / lam(60), 9);
        const mil = (l) => 1.25 * Math.log(Math.tan(Math.PI / 4 + 0.4 * rad(l)));
        expect(projectedFrac(box, 'miller')(30, 5).y).toBeCloseTo((mil(60) - mil(30)) / mil(60), 9);
        const gall = (l) => (1 + Math.SQRT2 / 2) * Math.tan(rad(l) / 2);
        expect(projectedFrac(box, 'gall')(30, 5).y).toBeCloseTo((gall(60) - gall(30)) / gall(60), 9);
    });

    it('sinusoidal uses the widest row, so the edge meridians touch the frame there', () => {
        const at = projectedFrac(EUROPA, 'sinusoidal');
        // The south edge is the widest row in Europa: its corners reach x = 0 and 1.
        expect(at(EUROPA.south, EUROPA.west).x).toBeCloseTo(0, 9);
        expect(at(EUROPA.south, EUROPA.east).x).toBeCloseTo(1, 9);
        // Further north the box narrows by cos(lat)/cos(south).
        const ratio = Math.cos(rad(EUROPA.north)) / Math.cos(rad(EUROPA.south));
        expect(at(EUROPA.north, EUROPA.east).x).toBeCloseTo(0.5 + 0.5 * ratio, 9);
        expect(at(50, 0).y).toBeCloseTo((EUROPA.north - 50) / (EUROPA.north - EUROPA.south), 9);
    });

    it('clamps mercator at 85° instead of going to infinity', () => {
        const box = { north: 89, south: 60, east: 10, west: 0 };
        const p = projectedFrac(box, 'mercator')(89, 5);
        expect(Number.isFinite(p.y)).toBe(true);
    });
});

describe('bendsMeridians', () => {
    it('is true only where a straight grid cannot follow the meridians', () => {
        expect(bendsMeridians('sinusoidal')).toBe(true);
        expect(bendsMeridians('cosine', true)).toBe(true);
        expect(bendsMeridians('cosine', false)).toBe(false);
        expect(bendsMeridians('mercator', true)).toBe(false);
        expect(bendsMeridians('none')).toBe(false);
    });
});

describe('niceDegreeStep', () => {
    it('picks about eight lines across the span', () => {
        expect(niceDegreeStep(63.6)).toBe(10);
        expect(niceDegreeStep(1.5)).toBe(0.2);
        expect(niceDegreeStep(0.03)).toBe(0.005);
        expect(niceDegreeStep(360)).toBe(45);
    });
    it('never returns a step that gives more than the target lines', () => {
        for (const span of [0.004, 0.5, 3, 17, 80, 170]) expect(span / niceDegreeStep(span, 6)).toBeLessThanOrEqual(6);
    });
});

describe('formatDegree', () => {
    it('labels hemispheres and drops them at zero', () => {
        expect(formatDegree(10, false, 10)).toBe('10°E');
        expect(formatDegree(-10, false, 10)).toBe('10°W');
        expect(formatDegree(50, true, 10)).toBe('50°N');
        expect(formatDegree(-33.25, true, 0.25)).toBe('33.25°S');
        expect(formatDegree(0, true, 5)).toBe('0°');
        expect(formatDegree(40.1, true, 0.1)).toBe('40.1°N');
    });
});

describe('graticuleLines', () => {
    it('draws every round meridian and parallel inside Europa at 10°', () => {
        const { step, lines } = graticuleLines(EUROPA, 'cosine');
        expect(step).toBe(10);
        expect(lines.filter((l) => l.kind === 'meridian').map((l) => l.value)).toEqual([-10, 0, 10, 20, 30, 40]);
        expect(lines.filter((l) => l.kind === 'parallel').map((l) => l.value)).toEqual([30, 40, 50, 60]);
        expect(lines.find((l) => l.value === -10 && l.kind === 'meridian').label).toBe('10°W');
    });

    it('meridians stay straight in cosine and bend in sinusoidal', () => {
        const xs = (proj) => graticuleLines(EUROPA, proj).lines
            .find((l) => l.kind === 'meridian' && l.value === 40).pts.map((p) => p.x);
        const cos = xs('cosine');
        expect(Math.max(...cos) - Math.min(...cos)).toBeCloseTo(0, 9);
        const sin = xs('sinusoidal');
        expect(Math.max(...sin) - Math.min(...sin)).toBeGreaterThan(0.1);
        // Parallels are always horizontal in these projections.
        for (const l of graticuleLines(EUROPA, 'sinusoidal').lines.filter((g) => g.kind === 'parallel')) {
            const ys = l.pts.map((p) => p.y);
            expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(0, 9);
        }
    });

    it('honours a forced step and the segment count', () => {
        const { step, lines } = graticuleLines({ north: 1, south: 0, east: 1, west: 0 }, 'none', { step: 0.5, segments: 4 });
        expect(step).toBe(0.5);
        expect(lines.filter((l) => l.kind === 'meridian').map((l) => l.value)).toEqual([0, 0.5, 1]);
        expect(lines[0].pts).toHaveLength(5);
    });
});

describe('frameAspect', () => {
    const europa = { north: 68.66, south: 27.84, east: 47.29, west: -16.35 };
    const cosineAspect = (63.64 * Math.cos((48.25 * Math.PI) / 180)) / 40.82;

    it('uses the projected raster shape when a projection is set', () => {
        expect(frameAspect(europa, { width: 573, height: 600 }, 'mercator')).toBeCloseTo(573 / 600, 6);
        expect(frameAspect(europa, { width: 600, height: 435 }, 'sinusoidal')).toBeCloseTo(600 / 435, 6);
    });

    it('keeps the cosine bbox shape without a projection or with a fixed canvas', () => {
        expect(frameAspect(europa, { width: 600, height: 385 }, 'none')).toBeCloseTo(cosineAspect, 3);
        expect(frameAspect(europa, { width: 600, height: 600 }, 'mercator', true)).toBeCloseTo(cosineAspect, 3);
    });

    it('falls back to the bbox shape before a height map is loaded', () => {
        expect(frameAspect(europa, null, 'mercator')).toBeCloseTo(cosineAspect, 3);
    });
});

describe('geoAtFrac', () => {
    const ALL = ['none', 'cosine', 'equidistant', 'mercator', 'lambert', 'miller', 'gall', 'sinusoidal', 'laea', 'lcc'];
    it('inverts projectedFrac for every projection, true and fixed shape', () => {
        for (const md of [false, true]) {
            for (const p of ALL) {
                const fwd = projectedFrac(EUROPA, p, md);
                const inv = geoAtFrac(EUROPA, p, md);
                for (const [lat, lon] of [[30, -10], [45.5, 3.2], [60, 40], [EUROPA.north, EUROPA.west]]) {
                    const f = fwd(lat, lon);
                    const g = inv(f.x, f.y);
                    expect(g.lat).toBeCloseTo(lat, 7);
                    expect(g.lon).toBeCloseTo(lon, 7);
                }
            }
        }
    });

    it('puts sinusoidal corner pixels outside the box (no data there)', () => {
        const g = geoAtFrac(EUROPA, 'sinusoidal')(0.01, 0.01);   // top-left, the narrow north row
        expect(g.lon).toBeLessThan(EUROPA.west);
    });
});

describe('projectedShape', () => {
    // geo2stl/projections.py::projected_shape, read off the server (width = cols, height = rows).
    it('matches the server for Europa at 600', () => {
        expect(projectedShape(EUROPA, 'cosine', 600)).toEqual({ width: 600, height: 578 });
        expect(projectedShape(EUROPA, 'mercator', 600)).toEqual({ width: 573, height: 600 });
        expect(projectedShape(EUROPA, 'sinusoidal', 600)).toEqual({ width: 600, height: 435 });
        expect(projectedShape(EUROPA, 'equidistant', 600)).toEqual({ width: 600, height: 578 });
        expect(projectedShape(EUROPA, 'lambert', 600)).toEqual({ width: 600, height: 251 });
        expect(projectedShape(EUROPA, 'miller', 600)).toEqual({ width: 600, height: 509 });
        expect(projectedShape(EUROPA, 'gall', 600)).toEqual({ width: 600, height: 567 });
        expect(projectedShape(EUROPA, 'none', 600)).toEqual({ width: 600, height: 385 });
    });

    it('keeps the plate-carree shape on a fixed canvas', () => {
        for (const p of ['cosine', 'mercator', 'sinusoidal']) {
            expect(projectedShape(EUROPA, p, 600, true)).toEqual({ width: 600, height: 385 });
        }
    });

    it('makes dim the longest side of a tall box', () => {
        const SF = { north: 37.82, south: 37.66, east: -122.35, west: -122.52 };
        expect(projectedShape(SF, 'cosine', 1000)).toEqual({ width: 840, height: 1000 });
        expect(projectedShape(SF, 'lambert', 1000)).toEqual({ width: 1000, height: 744 });
        expect(projectedShape(SF, 'gall', 1000)).toEqual({ width: 788, height: 1000 });
        expect(projectedShape(SF, 'none', 1000)).toEqual({ width: 1000, height: 941 });
    });
});

import { describe, it, expect } from 'vitest';
import {
    latLngToVec, yawToFace, rotateXY, facesCamera, clamp,
} from '../../app/client/static/js/vue/explore/globeMath.ts';

const near = (a, b) => expect(a).toBeCloseTo(b, 6);

describe('latLngToVec', () => {
    it('puts the poles on the y axis and lng 0 on +x', () => {
        const n = latLngToVec(90, 0, 5);
        near(n.y, 5); near(n.x, 0); near(n.z, 0);
        const e = latLngToVec(0, 0, 5);
        near(e.x, 5); near(e.y, 0); near(e.z, 0);
    });
    it('keeps the radius', () => {
        const v = latLngToVec(37, -122, 5);
        near(Math.hypot(v.x, v.y, v.z), 5);
    });
    it('puts lng 90E on -z (texture-consistent, not mirrored)', () => {
        const v = latLngToVec(0, 90, 5);
        near(v.z, -5);
    });
});

describe('yawToFace', () => {
    it('brings the chosen longitude to the camera (+z)', () => {
        for (const lng of [-170, -75, 0, 12, 140]) {
            const w = rotateXY(latLngToVec(0, lng, 5), 0, yawToFace(lng));
            near(w.z, 5); near(w.x, 0);
        }
    });
});

describe('facesCamera', () => {
    it('is true for the near side and false for the far side', () => {
        expect(facesCamera({ x: 0, y: 0, z: 5 }, 14, 5)).toBe(true);
        expect(facesCamera({ x: 0, y: 0, z: -5 }, 14, 5)).toBe(false);
        expect(facesCamera({ x: 5, y: 0, z: 0 }, 14, 5)).toBe(false);
    });
});

describe('clamp', () => {
    it('limits to the range', () => {
        expect(clamp(5, 0, 3)).toBe(3);
        expect(clamp(-1, 0, 3)).toBe(0);
        expect(clamp(2, 0, 3)).toBe(2);
    });
});

import { describe, it, expect, beforeAll } from 'vitest';

// The toast fade used to be a fixed CSS timer (gone after ~3 s whatever
// duration showToast was given). The fade timing now comes from
// window.toastAnimation(duration) so it ends when the toast is removed.
let toastAnimation;

beforeAll(async () => {
    globalThis.window = globalThis.window || {};
    await import('../../app/client/static/js/modules/core/ui-helpers.js');
    toastAnimation = globalThis.window.toastAnimation;
});

/** Parse "fadeOut <dur>s ease <delay>s" into ms. */
function fadeTiming(anim) {
    const m = anim.match(/fadeOut ([\d.]+)s ease ([\d.]+)s forwards/);
    expect(m).not.toBeNull();
    return { fade: Number(m[1]) * 1000, delay: Number(m[2]) * 1000 };
}

describe('toastAnimation', () => {
    it('keeps the default 3 s toast behaviour (fade from 2.7 s)', () => {
        const { fade, delay } = fadeTiming(toastAnimation(3000));
        expect(delay).toBeCloseTo(2700);
        expect(fade).toBeCloseTo(300);
    });

    it('holds an 8 s toast until just before 8 s', () => {
        const { fade, delay } = fadeTiming(toastAnimation(8000));
        expect(delay + fade).toBeCloseTo(8000);
        expect(delay).toBeGreaterThan(7000);
    });

    it('slides in first', () => {
        expect(toastAnimation(5000).startsWith('slideIn 0.3s ease')).toBe(true);
    });

    it('handles very short and invalid durations', () => {
        const short = fadeTiming(toastAnimation(200));
        expect(short.delay + short.fade).toBeCloseTo(200);
        const bad = fadeTiming(toastAnimation(undefined));
        expect(bad.delay + bad.fade).toBeCloseTo(3000);
    });
});

// Toast stack (UI audit 2026-09-30): at most 3 on screen; the oldest
// non-error toast is dropped first, errors only when all are errors.
describe('toastDropIndex', () => {
    const drop = (types) => globalThis.window.toastDropIndex(types, 3);

    it('drops nothing up to the limit', () => {
        expect(drop([])).toBe(-1);
        expect(drop(['info', 'error', 'success'])).toBe(-1);
    });

    it('drops the oldest non-error toast first', () => {
        expect(drop(['error', 'info', 'success', 'warning'])).toBe(1);
        expect(drop(['info', 'error', 'error', 'error'])).toBe(0);
    });

    it('drops the oldest error when all are errors', () => {
        expect(drop(['error', 'error', 'error', 'error'])).toBe(0);
    });
});

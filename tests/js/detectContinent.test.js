import { describe, it, expect } from 'vitest';
import { detectContinent } from './helpers/detectContinent.js';

describe('detectContinent', () => {
    describe('Antarctica', () => {
        it('detects lat < -60 as Antarctica', () => {
            expect(detectContinent(-70, 0)).toBe('Antarctica');
            expect(detectContinent(-61, 45)).toBe('Antarctica');
        });
    });

    describe('Oceania', () => {
        it('detects Australia (lat=-25, lon=135)', () => {
            expect(detectContinent(-25, 135)).toBe('Oceania');
        });

        it('detects New Zealand (lat=-41, lon=174)', () => {
            expect(detectContinent(-41, 174)).toBe('Oceania');
        });

        it('detects Papua New Guinea (lat=-5, lon=145)', () => {
            expect(detectContinent(-5, 145)).toBe('Oceania');
        });
    });

    describe('South America', () => {
        it('detects Brazil (lat=-15, lon=-47)', () => {
            expect(detectContinent(-15, -47)).toBe('South America');
        });

        it('detects Argentina (lat=-34, lon=-58)', () => {
            expect(detectContinent(-34, -58)).toBe('South America');
        });
    });

    describe('North America', () => {
        it('detects USA (lat=40, lon=-100)', () => {
            expect(detectContinent(40, -100)).toBe('North America');
        });

        it('detects Canada (lat=60, lon=-100)', () => {
            expect(detectContinent(60, -100)).toBe('North America');
        });

        it('detects Central America (lat=15, lon=-85)', () => {
            expect(detectContinent(15, -85)).toBe('North America');
        });
    });

    describe('Asia', () => {
        it('detects China (lat=35, lon=105)', () => {
            expect(detectContinent(35, 105)).toBe('Asia');
        });

        it('detects Japan (lat=36, lon=138)', () => {
            expect(detectContinent(36, 138)).toBe('Asia');
        });

        it('detects Siberia (lat=60, lon=80)', () => {
            expect(detectContinent(60, 80)).toBe('Asia');
        });

        it('detects Middle East (lat=30, lon=45)', () => {
            expect(detectContinent(30, 45)).toBe('Asia');
        });
    });

    describe('Africa', () => {
        it('detects Kenya (lat=0, lon=37)', () => {
            expect(detectContinent(0, 37)).toBe('Africa');
        });

        it('detects South Africa (lat=-30, lon=25)', () => {
            expect(detectContinent(-30, 25)).toBe('Africa');
        });

        it('detects Nigeria (lat=9, lon=8)', () => {
            expect(detectContinent(9, 8)).toBe('Africa');
        });
    });

    describe('Europe', () => {
        it('detects France (lat=46, lon=2)', () => {
            expect(detectContinent(46, 2)).toBe('Europe');
        });

        it('detects UK (lat=52, lon=-1)', () => {
            expect(detectContinent(52, -1)).toBe('Europe');
        });

        it('detects Norway (lat=60, lon=10)', () => {
            expect(detectContinent(60, 10)).toBe('Europe');
        });
    });

    describe('Other', () => {
        it('returns Other for mid-Atlantic (lat=0, lon=-30)', () => {
            // lon=-30 is outside Africa bbox (lon >= -18) and South America bbox (lon >= -34 would be SA)
            // lat=0, lon=-30: not in any continent bbox → Other
            expect(detectContinent(0, -30)).toBe('Other');
        });

        it('returns Other for Pacific (lat=0, lon=-160)', () => {
            expect(detectContinent(0, -160)).toBe('Other');
        });
    });
});

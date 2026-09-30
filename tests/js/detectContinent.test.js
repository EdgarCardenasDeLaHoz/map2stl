import { describe, it, expect } from 'vitest';
import { detectContinent } from '../../app/client/static/js/modules/regions/continent.js';

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

    // The Mediterranean, Red Sea and Bosphorus boundaries (continent.js
    // header). Istanbul's historic centre lies west of the Bosphorus, so it is
    // Europe; the Asian shore (Kadikoy, Uskudar) is Asia.
    describe('Mediterranean and Near East boundaries', () => {
        it.each([
            ['Granada', 37.18, -3.60, 'Europe'],
            ['Seville', 37.39, -5.98, 'Europe'],
            ['Almeria', 36.84, -2.46, 'Europe'],
            ['Palermo, Sicily', 38.12, 13.36, 'Europe'],
            ['Syracuse, Sicily', 37.07, 15.29, 'Europe'],
            ['Malta', 35.90, 14.51, 'Europe'],
            ['Heraklion, Crete', 35.34, 25.13, 'Europe'],
            ['Rhodes', 36.43, 28.22, 'Europe'],
            ['Athens', 37.98, 23.73, 'Europe'],
            ['Istanbul (historic centre)', 41.01, 28.98, 'Europe'],
            ['Moscow', 55.75, 37.62, 'Europe'],
            ['Grozny', 43.32, 45.69, 'Europe'],
            ['Tunis', 36.81, 10.18, 'Africa'],
            ['Tangier', 35.77, -5.80, 'Africa'],
            ['Algiers', 36.75, 3.06, 'Africa'],
            ['Tripoli', 32.89, 13.19, 'Africa'],
            ['Cairo', 30.04, 31.24, 'Africa'],
            ['Alexandria', 31.20, 29.92, 'Africa'],
            ['Port Sudan', 19.62, 37.22, 'Africa'],
            ['Tel Aviv', 32.08, 34.78, 'Asia'],
            ['Sharm el-Sheikh, Sinai', 27.92, 34.33, 'Asia'],
            ['Jeddah', 21.54, 39.17, 'Asia'],
            ['Riyadh', 24.71, 46.68, 'Asia'],
            ['Sanaa', 15.37, 44.19, 'Asia'],
            ['Kadikoy (Istanbul, Asian shore)', 40.99, 29.03, 'Asia'],
            ['Ankara', 39.93, 32.86, 'Asia'],
            ['Izmir', 38.42, 27.14, 'Asia'],
            ['Nicosia, Cyprus', 35.17, 33.36, 'Asia'],
            ['Tbilisi', 41.72, 44.79, 'Asia'],
            ['Baku', 40.41, 49.87, 'Asia'],
            ['Yekaterinburg', 56.84, 60.61, 'Asia'],
        ])('%s', (_name, lat, lon, expected) => {
            expect(detectContinent(lat, lon)).toBe(expected);
        });
    });

    describe('Other', () => {
        it('returns Other for mid-Atlantic (lat=0, lon=-30)', () => {
            // lon=-30 is west of Africa (lon >= -26) and east of South America (lon <= -34)
            // lat=0, lon=-30: not in any continent bbox → Other
            expect(detectContinent(0, -30)).toBe('Other');
        });

        it('returns Other for Pacific (lat=0, lon=-160)', () => {
            expect(detectContinent(0, -160)).toBe('Other');
        });
    });
});

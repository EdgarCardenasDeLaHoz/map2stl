/**
 * modules/dem/dem-stats.js — one-pass statistics over a DEM's elevation values.
 *
 * Range and histogram bins of a 1000 x 1000 DEM used to be filter + reduce + reduce + forEach
 * over 1M values, twice per load (dem-main.js _applyDemResult and dem-loader.js
 * drawHistogram): a copy of the array and four passes. These are single loops with no
 * allocation beyond the bins. Pure (tests/js/demStats.test.js).
 */

/**
 * Smallest and largest finite value, and how many values are finite.
 * @param {ArrayLike<number>} values  may hold NaN / Infinity (no data)
 * @returns {{min:number, max:number, count:number}}  min = max = NaN when none is finite
 */
export function finiteRange(values) {
    let min = Infinity, max = -Infinity, count = 0;
    for (let i = 0, n = values.length; i < n; i++) {
        const v = values[i];
        if (!Number.isFinite(v)) continue;   // also skips null / undefined in a plain array
        if (v < min) min = v;
        if (v > max) max = v;
        count++;
    }
    return count ? { min, max, count } : { min: NaN, max: NaN, count: 0 };
}

/**
 * Histogram counts of the finite values over [min, max] in `numBins` equal bins (the top
 * value goes in the last bin).
 * @param {ArrayLike<number>} values
 * @param {number} min
 * @param {number} max
 * @param {number} numBins
 * @returns {number[]}
 */
export function binCounts(values, min, max, numBins) {
    const bins = new Array(numBins).fill(0);
    const scale = numBins / ((max - min) || 1);
    const last = numBins - 1;
    for (let i = 0, n = values.length; i < n; i++) {
        const v = values[i];
        if (!Number.isFinite(v)) continue;
        const idx = Math.floor((v - min) * scale);
        bins[idx < 0 ? 0 : idx > last ? last : idx]++;
    }
    return bins;
}

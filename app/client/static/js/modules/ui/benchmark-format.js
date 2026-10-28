/**
 * benchmark-format.js — number and label formatting for the Benchmark page
 * (vue/components/views/BenchmarkPage.vue). Pure: no DOM, no fetch.
 *
 * Errors are metres. A lower MAE is better, so a negative delta is an improvement.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "5.6 m", or "—" when there is no number. */
export function fmtMeters(v, digits = 1) {
    return Number.isFinite(v) ? `${v.toFixed(digits)} m` : '—';
}

/** "+1.5 m" / "−0.4 m" (a real minus sign), or "—". */
export function fmtSignedMeters(v, digits = 1) {
    if (!Number.isFinite(v)) return '—';
    const s = Math.abs(v).toFixed(digits);
    if (Number(s) === 0) return `${s} m`;
    return `${v > 0 ? '+' : '−'}${s} m`;
}

/** Share 0..1 as a whole percent: "67 %", or "—". */
export function fmtShare(frac) {
    return Number.isFinite(frac) ? `${Math.round(frac * 100)} %` : '—';
}

/** "1,000" style thousands separators. */
export function fmtCount(n) {
    return Number.isFinite(n) ? Math.round(n).toLocaleString('en-US') : '—';
}

/** "2026-10-23_2145" -> "Oct 23"; anything else comes back unchanged ("—" for empty). */
export function fmtStamp(stamp) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(stamp || '');
    if (!m) return stamp || '—';
    return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}`;
}

/**
 * The change in MAE against the previous scoring.
 * @param {number|null} delta  new MAE minus previous MAE (metres)
 * @param {string|null} stamp  the previous scoring's stamp
 * @returns {{kind: 'better'|'worse'|'same'|'none', text: string}}
 */
export function deltaInfo(delta, stamp) {
    if (!Number.isFinite(delta)) return { kind: 'none', text: '' };
    const when = stamp ? ` vs ${fmtStamp(stamp)}` : '';
    const mag = Math.abs(delta).toFixed(1);
    if (Number(mag) === 0) return { kind: 'same', text: `no change${when}` };
    return delta < 0
        ? { kind: 'better', text: `▼ ${mag} m${when}` }
        : { kind: 'worse', text: `▲ ${mag} m${when}` };
}

/** What the "within" column means: new scores count |error| <= 3 m, old ones 25 % of height. */
export function withinLabel(kind) {
    return kind === '3m' ? 'Within 3 m' : 'Within 25 %';
}

/** Bar fill 0..1 for "root requests this month"; null when there is no counter. */
export function usageFraction(value, limit) {
    if (!Number.isFinite(value) || !Number.isFinite(limit) || limit <= 0) return null;
    return Math.min(1, Math.max(0, value / limit));
}

/** "Root requests 55 / 1,000". */
export function usageText(value, limit) {
    return `${fmtCount(value)} / ${fmtCount(limit)}`;
}

/** The sentence under the Score button when paid fetches are on and truth is missing. */
export function gapHint(gap) {
    if (!gap) return '';
    if (!gap.missing) return 'Every building has cached truth: nothing to fetch.';
    return `${fmtCount(gap.missing)} of ${fmtCount(gap.buildings)} buildings have no cached truth; `
        + `measuring them takes about ${fmtCount(gap.tile_fetches)} 3D Tiles fetches.`;
}

/** Sub-label of a region row, shortened for the narrow list. */
export function regionSub(r) {
    return r?.sub || '';
}

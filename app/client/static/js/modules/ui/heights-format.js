/**
 * heights-format.js — pure logic for the Street View heights page
 * (vue/components/views/HeightsPage.vue): map view maths, hit testing, and the texts.
 * No DOM, no fetch.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** The tiers in the order the report card lists them, with the label the card shows. */
export const TIER_ORDER = ['corroborated', 'single', 'tag', 'prior'];
export const TIER_CARD_LABEL = {
    corroborated: 'Corroborated',
    single: 'Single reading',
    tag: 'OSM tag (kept)',
    prior: 'Prior estimate',
    unlabelled: 'No tier recorded',
};

/** "1,840" style thousands separators, or "—". */
export function fmtCount(n) {
    return Number.isFinite(n) ? Math.round(n).toLocaleString('en-US') : '—';
}

/** "Oct 23" from an epoch-seconds modified time (local time), or "". */
export function fmtDay(epochSeconds) {
    if (!Number.isFinite(epochSeconds)) return '';
    const d = new Date(epochSeconds * 1000);
    return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

/** "Oct 23 · 902 buildings" (the report row's second line). */
export function reportSub(report) {
    const n = report?.n_buildings;
    const parts = [fmtDay(report?.mtime)];
    if (Number.isFinite(n)) parts.push(`${fmtCount(n)} building${n === 1 ? '' : 's'}`);
    return parts.filter(Boolean).join(' · ');
}

/** Tier rows for the selected-report card: [{key, label, color, count}], the four tiers always
 *  (zero rows kept), plus "No tier recorded" when a report written before tiers existed has rows. */
export function tierRows(tierCounts, tiers) {
    const keys = (tierCounts?.unlabelled ?? 0) > 0 ? [...TIER_ORDER, 'unlabelled'] : TIER_ORDER;
    return keys.map((key) => ({
        key,
        label: TIER_CARD_LABEL[key],
        color: tiers?.[key]?.color ?? '#999999',
        count: tierCounts?.[key] ?? 0,
    }));
}

/** Buildings with a Street View reading: corroborated plus single. */
export function measuredCount(tierCounts) {
    return (tierCounts?.corroborated ?? 0) + (tierCounts?.single ?? 0);
}

/** Share of the monthly cap used, 0..1 (clamped), or null when unknown. */
export function capFraction(count, cap) {
    if (!Number.isFinite(count) || !Number.isFinite(cap) || cap <= 0) return null;
    return Math.min(1, Math.max(0, count / cap));
}

/** "1,840 / 10,000". */
export function capText(count, cap) {
    return `${fmtCount(count)} / ${fmtCount(cap)}`;
}

/** "+12 % of the monthly cap" style share, to one decimal when under 10 %. */
export function fmtSharePct(frac) {
    if (!Number.isFinite(frac)) return '—';
    const pct = frac * 100;
    return `${pct < 10 && pct > 0 ? pct.toFixed(1) : Math.round(pct)} %`;
}

/**
 * What the paid-fetch confirm says, or null when no confirm is needed.
 * `estimate` is the /api/skyline/estimate reply. Cache-only runs never ask.
 */
export function confirmInfo(estimate, allowPaid) {
    if (!allowPaid) return null;
    const paid = estimate?.paid_fetches;
    if (!Number.isFinite(paid) || paid <= 0) return null;
    const cap = estimate.cap;
    const frac = capFraction(paid, cap);
    const after = capFraction((estimate.month_count ?? 0) + paid, cap);
    return {
        paid,
        cap,
        share: fmtSharePct(frac),
        warn: !!estimate.over_warn,
        text: `This run needs ${estimate.rough ? 'at least' : 'about'} ${fmtCount(paid)} paid Street View images, `
            + `${fmtSharePct(frac)} of the ${fmtCount(cap)} monthly cap.`,
        warnText: estimate.over_warn
            ? `That takes this month to ${fmtSharePct(after)} of the cap, past the 80 % warning line.`
            : '',
    };
}

/** "Cartagena v21 · step 3 of 5" from a run snapshot. */
export function stepHeading(job, region) {
    const m = /_v(\d+)_/.exec(job?.report || '');
    const name = m ? `${region} v${m[1]}` : region;
    return job?.step ? `${name} · step ${job.step} of ${job.n_steps}` : name;
}

/** "Detecting rooflines · 128 / 212 panoramas". */
export function stepText(job) {
    if (!job) return '';
    const label = job.step_label || job.message || '';
    if (Number.isFinite(job.done) && Number.isFinite(job.total) && job.total > 0) {
        return `${label} · ${fmtCount(job.done)} / ${fmtCount(job.total)} panoramas`;
    }
    return label;
}

/** Whole-run progress 0..100: finished steps plus the share through the current one. */
export function runPercent(job) {
    if (!job?.step || !job.n_steps) return 0;
    const within = job.total > 0 ? Math.min(1, job.done / job.total) : 0;
    return Math.round(100 * ((job.step - 1 + within) / job.n_steps));
}

/** "GPU: in use by this run" / "GPU: waiting for GPU" / "". */
export function gpuText(gpu) {
    if (gpu === 'in use') return 'GPU: in use by this run';
    if (gpu === 'waiting') return 'GPU: waiting for GPU';
    return '';
}

// ---------------------------------------------------------------------------
// Map view: equirectangular, longitude scaled by cos(latitude), y up.
// ---------------------------------------------------------------------------

/** Bounds {west, east, south, north} of every ring, or null when there are none. */
export function boundsOf(buildings) {
    let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity;
    for (const b of buildings || []) {
        for (const [x, y] of b.ring || []) {
            if (x < west) west = x;
            if (x > east) east = x;
            if (y < south) south = y;
            if (y > north) north = y;
        }
    }
    return Number.isFinite(west) ? { west, east, south, north } : null;
}

/**
 * Fit bounds into a width x height box with a margin. Returns the view
 * {scale, kx, cx, cy, w, h}: screen x = w/2 + (lon - cx) * kx * scale, y likewise upward.
 */
export function fitView(bounds, w, h, margin = 24) {
    const cy = bounds ? (bounds.north + bounds.south) / 2 : 0;
    const cx = bounds ? (bounds.west + bounds.east) / 2 : 0;
    const kx = Math.cos((cy * Math.PI) / 180) || 1;
    const spanX = bounds ? Math.max((bounds.east - bounds.west) * kx, 1e-9) : 1;
    const spanY = bounds ? Math.max(bounds.north - bounds.south, 1e-9) : 1;
    const scale = Math.max(1e-9, Math.min((w - 2 * margin) / spanX, (h - 2 * margin) / spanY));
    return { scale, kx, cx, cy, w, h };
}

export function project(view, lon, lat) {
    return [view.w / 2 + (lon - view.cx) * view.kx * view.scale,
            view.h / 2 - (lat - view.cy) * view.scale];
}

export function unproject(view, x, y) {
    return [view.cx + (x - view.w / 2) / (view.kx * view.scale),
            view.cy - (y - view.h / 2) / view.scale];
}

/** Zoom about a screen point; `factor` > 1 zooms in. Clamped to 0.5x..200x of the fit scale. */
export function zoomAt(view, x, y, factor, fitScale) {
    const next = Math.min(fitScale * 200, Math.max(fitScale * 0.5, view.scale * factor));
    const [lon, lat] = unproject(view, x, y);
    const out = { ...view, scale: next };
    // keep (lon, lat) under the cursor
    out.cx = lon - (x - view.w / 2) / (view.kx * next);
    out.cy = lat + (y - view.h / 2) / next;
    return out;
}

/** Pan by a screen delta. */
export function panBy(view, dx, dy) {
    return { ...view, cx: view.cx - dx / (view.kx * view.scale), cy: view.cy + dy / view.scale };
}

/** Even-odd point in polygon; ring is [[x, y], ...] (closed or open). */
export function pointInRing(x, y, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i];
        const [xj, yj] = ring[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}

/**
 * The building under a lon/lat point: the smallest-area hit wins, so a small building inside
 * a large footprint stays reachable. Falls back to the nearest ring vertex within `tol` degrees.
 */
export function hitTest(buildings, lon, lat, tol = 0) {
    let best = null;
    let bestArea = Infinity;
    for (const b of buildings || []) {
        const r = b.ring;
        if (!r || r.length < 3) continue;
        if (!pointInRing(lon, lat, r)) continue;
        const area = ringArea(r);
        if (area < bestArea) { best = b; bestArea = area; }
    }
    if (best || !(tol > 0)) return best;
    let nearest = null;
    let nd = tol * tol;
    for (const b of buildings || []) {
        for (const [x, y] of b.ring || []) {
            const d = (x - lon) ** 2 + (y - lat) ** 2;
            if (d <= nd) { nd = d; nearest = b; }
        }
    }
    return nearest;
}

export function ringArea(ring) {
    let s = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        s += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    }
    return Math.abs(s) / 2;
}

/** The hover card's lines for one building: {title, height, tier, tag}. */
export function hoverInfo(b, tiers) {
    if (!b) return null;
    const panos = Number.isFinite(b.panos) ? b.panos : 0;
    return {
        title: b.name || `Building ${b.id}`,
        height: Number.isFinite(b.h) ? `${b.h.toFixed(1)} m` : '—',
        tier: tiers?.[b.tier]?.label ?? b.tier,
        panos: `${panos} panorama${panos === 1 ? '' : 's'}`,
        tag: Number.isFinite(b.tag) ? `OSM tag: ${b.tag.toFixed(1)} m` : 'OSM tag: none',
    };
}

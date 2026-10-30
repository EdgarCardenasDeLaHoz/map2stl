// photo-solve-format.js — pure logic for the Photo solve page (PhotoSolvePage.vue): pair hints,
// camera card texts, the photo and map views, footprint picking, candidate cameras, the heights
// table. No DOM, no network: tested by tests/js/photoSolveFormat.test.js.

const M_PER_DEG_LAT = 111320;
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

export const MIN_PAIRS = 4;

const num = (v) => typeof v === 'number' && Number.isFinite(v);

/** What the user should do next, from the pairs so far and a pending photo point. */
export function stepHint(pairs, pending, solved) {
    if (pending) return 'Now click the same spot on the map.';
    if (pairs < MIN_PAIRS) {
        const need = MIN_PAIRS - pairs;
        return pairs === 0
            ? 'Click a corner or a landmark on the photo, then the same spot on the map. You need 4 pairs.'
            : `${pairs} of ${MIN_PAIRS} pairs. Add ${need} more: a spot on the photo, then the same spot on the map.`;
    }
    return solved ? 'Camera solved. Add more pairs to improve it, or save.' : 'Enough pairs. Press Solve camera.';
}

export function pairsText(n) {
    return n === 1 ? '1 pair' : `${n} pairs`;
}

/** Colour class for the fit error. */
export function rmsTone(rms) {
    if (!num(rms)) return 'dim';
    if (rms <= 4) return 'good';
    if (rms <= 10) return 'warn';
    return 'bad';
}

export function fmtRms(rms) {
    return num(rms) ? `${rms.toFixed(1)} px` : '—';
}

export function compass(deg) {
    if (!num(deg)) return '';
    const d = ((deg % 360) + 360) % 360;
    return COMPASS[Math.round(d / 22.5) % 16];
}

/** The rows of the Camera card. */
export function cameraRows(pose) {
    if (!pose) return [];
    const rows = [];
    rows.push({ label: 'Fit error', value: fmtRms(pose.rms_px), tone: rmsTone(pose.rms_px) });
    if (num(pose.lat) && num(pose.lon)) {
        rows.push({ label: 'Position', value: `${pose.lat.toFixed(5)}, ${pose.lon.toFixed(5)}` });
    }
    if (num(pose.elev_m)) rows.push({ label: 'Height', value: `${Math.round(pose.elev_m)} m above sea level` });
    if (num(pose.heading_deg)) {
        const h = ((pose.heading_deg % 360) + 360) % 360;
        const tilt = num(pose.pitch_deg) ? ` · tilt ${pose.pitch_deg.toFixed(1)}°` : '';
        rows.push({ label: 'Looking', value: `${Math.round(h)}° ${compass(h)}${tilt}` });
    }
    if (num(pose.hfov_deg)) rows.push({ label: 'Field of view', value: `${pose.hfov_deg.toFixed(0)}°` });
    return rows;
}

/** One line under a photo in the list. */
export function photoStatus(p) {
    if (p.bad) return 'Flagged unusable';
    const bits = [pairsText(p.pairs || 0)];
    if (p.solved) {
        bits.push(p.camera_source === 'auto' ? 'auto start camera' : `solved ${fmtRms(p.rms_px)}`);
    }
    return bits.join(' · ');
}

export function photoName(path) {
    const parts = String(path || '').split('/');
    const folder = parts.length > 1 ? parts[parts.length - 2] : parts[0];
    return folder.replace(/_/g, ' ');
}

/** The saved-labels request body. */
export function labelsPayload(photo, pairs, imagePx, camera, cameraSource) {
    const body = {
        photo,
        image_px: imagePx,
        labels: pairs.map((p) => ({ x: p.x, y: p.y, lat: p.lat, lon: p.lon, label: p.label || undefined, auto: p.auto || undefined })),
    };
    if (camera) {
        body.camera = camera;
        if (cameraSource) body.camera_source = cameraSource;
    }
    return body;
}

// ---------- photo view: the picture fitted into the canvas -------------------------------------

export function fitRect(imgW, imgH, boxW, boxH, pad = 8) {
    if (!(imgW > 0 && imgH > 0 && boxW > 0 && boxH > 0)) return { k: 1, ox: 0, oy: 0 };
    const k = Math.min((boxW - 2 * pad) / imgW, (boxH - 2 * pad) / imgH);
    return { k, ox: (boxW - imgW * k) / 2, oy: (boxH - imgH * k) / 2 };
}

export const photoToScreen = (f, x, y) => [f.ox + x * f.k, f.oy + y * f.k];
export const screenToPhoto = (f, sx, sy) => [(sx - f.ox) / f.k, (sy - f.oy) / f.k];

export function insidePhoto(x, y, w, h) {
    return x >= 0 && y >= 0 && x <= w && y <= h;
}

// ---------- map view: lat/lon on screen, north up ----------------------------------------------

/** View = { lat, lon, mpp } : the point at the canvas centre and metres per screen pixel. */
export function llToScreen(v, w, h, lat, lon) {
    const kx = M_PER_DEG_LAT * Math.cos((v.lat * Math.PI) / 180);
    return [w / 2 + ((lon - v.lon) * kx) / v.mpp, h / 2 - ((lat - v.lat) * M_PER_DEG_LAT) / v.mpp];
}

export function screenToLl(v, w, h, sx, sy) {
    const kx = M_PER_DEG_LAT * Math.cos((v.lat * Math.PI) / 180);
    return [v.lat - ((sy - h / 2) * v.mpp) / M_PER_DEG_LAT, v.lon + ((sx - w / 2) * v.mpp) / kx];
}

export function fitMapView(points, w, h, pad = 40) {
    const pts = points.filter((p) => num(p[0]) && num(p[1]));
    if (!pts.length || !(w > 0 && h > 0)) return null;
    const lats = pts.map((p) => p[0]);
    const lons = pts.map((p) => p[1]);
    const lat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const lon = (Math.min(...lons) + Math.max(...lons)) / 2;
    const kx = M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);
    const spanX = Math.max((Math.max(...lons) - Math.min(...lons)) * kx, 60);
    const spanY = Math.max((Math.max(...lats) - Math.min(...lats)) * M_PER_DEG_LAT, 60);
    return { lat, lon, mpp: Math.max(spanX / (w - 2 * pad), spanY / (h - 2 * pad)) };
}

export function zoomMap(v, w, h, sx, sy, factor) {
    const mpp = Math.min(Math.max(v.mpp / factor, 0.1), 40);
    const [lat, lon] = screenToLl(v, w, h, sx, sy);
    const next = { ...v, mpp };
    const [lat2, lon2] = screenToLl(next, w, h, sx, sy);
    return { lat: v.lat + (lat - lat2), lon: v.lon + (lon - lon2), mpp };
}

export function panMap(v, dx, dy) {
    const kx = M_PER_DEG_LAT * Math.cos((v.lat * Math.PI) / 180);
    return { ...v, lat: v.lat + (dy * v.mpp) / M_PER_DEG_LAT, lon: v.lon - (dx * v.mpp) / kx };
}

/** Window the footprint request should cover: radius in metres around the view centre. */
export function mapRadius(v, w, h) {
    return Math.min(6000, Math.max(200, 0.6 * Math.hypot(w, h) * v.mpp));
}

/** True when the view has moved far enough that footprints should be loaded again. */
export function needsFootprints(loaded, v, radius) {
    if (!loaded) return true;
    const kx = M_PER_DEG_LAT * Math.cos((v.lat * Math.PI) / 180);
    const d = Math.hypot((v.lon - loaded.lon) * kx, (v.lat - loaded.lat) * M_PER_DEG_LAT);
    return d > loaded.radius * 0.35 || radius > loaded.radius * 1.2;
}

// ---------- camera on the map ------------------------------------------------------------------

/** Triangle of the camera's field of view: the camera and the two far edge points, [lat, lon]. */
export function fovWedge(pose, lengthM) {
    if (!pose || !num(pose.lat) || !num(pose.heading_deg)) return null;
    const half = (num(pose.hfov_deg) ? pose.hfov_deg : 60) / 2;
    const kx = M_PER_DEG_LAT * Math.cos((pose.lat * Math.PI) / 180);
    const at = (d) => {
        const r = ((pose.heading_deg + d) * Math.PI) / 180;
        return [pose.lat + (Math.cos(r) * lengthM) / M_PER_DEG_LAT, pose.lon + (Math.sin(r) * lengthM) / kx];
    };
    return { apex: [pose.lat, pose.lon], left: at(-half), right: at(half), centre: at(0) };
}

/** A candidate from Auto register as a start camera (what Save labels keeps, source "auto"). */
export function cameraFromCandidate(c) {
    const cam = {
        lat: c.lat, lon: c.lon, elev_m: c.elev_m, heading_deg: c.heading_deg, pitch_deg: c.pitch_deg,
        roll_deg: c.roll_deg || 0, f_px: c.f_px,
    };
    cam.hfov_deg = num(c.hfov_deg) ? c.hfov_deg
        : (num(c.width) && num(c.f_px) ? (2 * Math.atan(c.width / 2 / c.f_px) * 180) / Math.PI : undefined);
    return cam;
}

export function candidateText(c, i) {
    const bits = [`${i + 1}. ${c.label || c.source || 'Start pose'}`];
    if (num(c.heading_deg)) bits.push(`looking ${Math.round(((c.heading_deg % 360) + 360) % 360)}° ${compass(c.heading_deg)}`);
    return bits.join(' · ');
}

// ---------- footprints -------------------------------------------------------------------------

/** Ray-casting point in ring ([lon, lat] pairs). */
export function inRing(lat, lon, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i];
        const [xj, yj] = ring[j];
        if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}

export function footprintAt(features, lat, lon) {
    return (features || []).find((f) => inRing(lat, lon, f.ring)) || null;
}

// ---------- heights ----------------------------------------------------------------------------

export function diffText(photo, osm) {
    if (!num(photo)) return '';
    if (!num(osm)) return 'no OSM height';
    const d = photo - osm;
    return `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)} m`;
}

/** Rows to show: finite photo heights only, with the difference to OSM. */
export function heightRows(rows) {
    return (rows || [])
        .filter((r) => num(r.photo_m))
        .map((r) => ({ ...r, diff: diffText(r.photo_m, r.osm_m) }));
}

export function keepText(n) {
    return n === 0 ? 'No heights to keep' : `Use these heights (${n})`;
}

export function savedHeightsText(res) {
    if (!res || !res.ok) return '';
    return `Kept ${res.saved} height${res.saved === 1 ? '' : 's'} from this photo. OSM tags, survey and LiDAR heights are not changed.`;
}

// ---------- jobs / screening -------------------------------------------------------------------

export function commonsText(p) {
    const bits = [String(p.title || '').replace(/^File:/, '')];
    if (num(p.width) && num(p.height)) bits.push(`${p.width}×${p.height}`);
    return bits.join(' · ');
}

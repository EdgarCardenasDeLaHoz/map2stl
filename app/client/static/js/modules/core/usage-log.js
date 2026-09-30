/**
 * usage-log.js — local usage log (F-USAGE): what the user clicks, changes and waits for.
 *
 * Records, one event each (all with `t` = ISO time and `dt` = ms since the previous event):
 *   click     buttons, links, tabs, <summary> (id, label, enclosing panel id)
 *   change    inputs / selects / textareas once a value is committed (id, label, value)
 *   bbox      EV.BBOX_CHANGED, debounced; region (EV.REGION_SELECTED); dem (EV.DEM_LOADED)
 *   api       every /api/ request: method, path, status, ms (polling routes only on failure)
 *   toast     window.showToast messages; error: window errors, rejections, console.error
 *   page      session start, tab hidden / visible
 *
 * Never recorded: password fields and any control whose id, name, label or placeholder
 * mentions a key, token, secret or password (the server scrubs those values again).
 *
 * Events are batched to POST /api/usage (app/server/routers/usage.py) every 5 s and on
 * page hide, which appends them to output/usage/<date>.jsonl on this machine only.
 * Pause with localStorage `map2stl_usage_log` = "off" or window.usageLog.pause().
 */

const STORAGE_KEY = 'map2stl_usage_log';
const FLUSH_MS = 5000;
const SECRET = /pass|key|token|secret|auth|credential/i;
const POLLING = /\/status(\/|$)|\/progress(\/|$)|\/api\/usage/;

const rawFetch = window.fetch.bind(window);
const session = (window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`);
let buffer = [];
let last = Date.now();

function isOn() {
    try { return window.localStorage.getItem(STORAGE_KEY) !== 'off'; } catch (_) { return true; }
}

function record(kind, fields) {
    if (!isOn()) return;
    const now = Date.now();
    buffer.push({ t: new Date(now).toISOString(), dt: now - last, kind, ...fields });
    last = now;
    if (buffer.length >= 200) flush();
}

function flush(beacon = false) {
    if (!buffer.length) return;
    const body = JSON.stringify({ session, events: buffer });
    buffer = [];
    try {
        if (beacon && navigator.sendBeacon) {
            navigator.sendBeacon('/api/usage', new Blob([body], { type: 'application/json' }));
        } else {
            rawFetch('/api/usage', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                     body, keepalive: true }).catch(() => {});
        }
    } catch (_) { /* logging must never break the app */ }
}

const clip = (s, n = 80) => (s == null ? '' : String(s).replace(/\s+/g, ' ').trim().slice(0, n));

function labelOf(el) {
    const aria = el.getAttribute?.('aria-label') || el.getAttribute?.('title');
    if (aria) return clip(aria);
    if (el.id) {
        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab) return clip(lab.textContent);
    }
    const wrap = el.closest?.('label');
    if (wrap) return clip(wrap.textContent);
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
        return clip(el.getAttribute('placeholder') || el.name || '');
    }
    return clip(el.textContent);
}

function describe(el) {
    const panel = el.parentElement?.closest?.('[id]');
    return { tag: el.tagName.toLowerCase(), id: el.id || undefined, name: el.name || undefined,
             label: labelOf(el), in: panel?.id || undefined };
}

function isSecret(el, d) {
    return el.type === 'password'
        || SECRET.test([d.id, d.name, d.label, el.getAttribute?.('placeholder'),
                        el.getAttribute?.('autocomplete')].filter(Boolean).join(' '));
}

function valueOf(el) {
    if (el.type === 'checkbox' || el.type === 'radio') return el.checked;
    if (el.tagName === 'SELECT') {
        const opt = el.options[el.selectedIndex];
        return opt ? clip(opt.textContent, 60) || el.value : el.value;
    }
    if (el.type === 'file') return `${el.files?.length || 0} file(s)`;
    return clip(el.value, 100);
}

const CLICKABLE = 'button, a, summary, [role="button"], [role="tab"], [role="menuitem"], .tab';

document.addEventListener('click', (e) => {
    const el = e.target?.closest?.(CLICKABLE);
    if (!el) return;
    record('click', describe(el));
}, { capture: true, passive: true });

document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
    const d = describe(el);
    if (isSecret(el, d)) { record('change', { ...d, value: '[redacted]' }); return; }
    record('change', { ...d, type: el.type || undefined, value: valueOf(el) });
}, { capture: true, passive: true });

function scrubUrl(url) {
    try {
        const u = new URL(url, window.location.origin);
        for (const k of [...u.searchParams.keys()]) if (SECRET.test(k)) u.searchParams.set(k, '[redacted]');
        return decodeURIComponent(u.pathname + u.search);
    } catch (_) { return clip(url, 200); }
}

window.fetch = function usageFetch(input, init) {
    const url = typeof input === 'string' ? input : (input?.url || String(input));
    const p = rawFetch(input, init);
    if (!url.includes('/api/') || !isOn()) return p;
    const t0 = performance.now();
    const method = (init?.method || input?.method || 'GET').toUpperCase();
    const path = scrubUrl(url);
    p.then((r) => {
        if (POLLING.test(path) && r.ok) return;
        record('api', { method, path: clip(path, 300), status: r.status,
                        ms: Math.round(performance.now() - t0) });
    }, (err) => {
        if (err?.name === 'AbortError') return;
        record('api', { method, path: clip(path, 300), status: 0, error: clip(err?.message, 120),
                        ms: Math.round(performance.now() - t0) });
    });
    return p;
};

window.addEventListener('error', (e) => record('error', {
    message: clip(e.message, 200), source: clip(e.filename, 120), line: e.lineno }));
window.addEventListener('unhandledrejection', (e) => record('error', {
    message: clip(e.reason?.message || e.reason, 200), rejection: true }));
const rawConsoleError = console.error.bind(console);
console.error = (...args) => {
    record('error', { message: clip(args.map((a) => a?.message || a).join(' '), 200), console: true });
    rawConsoleError(...args);
};

// showToast is defined by ui-helpers.js, imported before this module.
if (typeof window.showToast === 'function') {
    const rawToast = window.showToast;
    window.showToast = function (message, type = 'info', duration) {
        record('toast', { type, message: clip(String(message).replace(/<[^>]+>/g, ''), 200) });
        return rawToast.call(this, message, type, duration);
    };
}

let bboxTimer = null;
function onEvents() {
    const ev = window.events, EV = window.EV;
    if (!ev || !EV) return;
    ev.on(EV.BBOX_CHANGED, (b) => {
        clearTimeout(bboxTimer);
        bboxTimer = setTimeout(() => {
            if (!b) return;
            const r = (x) => Math.round(Number(x) * 1e5) / 1e5;
            record('bbox', { north: r(b.north), south: r(b.south), east: r(b.east), west: r(b.west) });
        }, 800);
    });
    ev.on(EV.REGION_SELECTED, (index) => {
        const reg = window.appState?.selectedRegion;
        record('region', { index, name: clip(reg?.name, 80) || undefined });
    });
    ev.on(EV.DEM_LOADED, (vmin, vmax) => record('dem', { vmin, vmax }));
}
onEvents();

document.addEventListener('visibilitychange', () => {
    record('page', { state: document.visibilityState });
    if (document.visibilityState === 'hidden') flush(true);
});
window.addEventListener('pagehide', () => flush(true));
setInterval(flush, FLUSH_MS);

record('page', { state: 'start', path: window.location.pathname,
                 viewport: `${window.innerWidth}x${window.innerHeight}` });

window.usageLog = {
    session,
    get enabled() { return isOn(); },
    pause() { try { window.localStorage.setItem(STORAGE_KEY, 'off'); } catch (_) { /* */ } buffer = []; },
    resume() { try { window.localStorage.removeItem(STORAGE_KEY); } catch (_) { /* */ } },
    flush: () => flush(),
};

/**
 * modules/regions/region-links.js
 * ===============================
 * Shortcut links to a saved region: `/#region=Granada&tab=edit` opens Granada on the
 * Edit tab. The address bar follows the selected region and the open tab, so the link
 * for any region is whatever the address bar shows once it is open.
 *
 * Tabs in the link: `explore` (the default, left out of the link), `globe`, `edit`,
 * `extrude`. A region name matches exactly first, then ignoring case, spaces and
 * punctuation (`#region=san-juan` finds "San Juan").
 *
 * Public API (exposed on window):
 *   window.openRegionFromLocation() → Promise<boolean>   (called from app.js after start-up)
 *
 * Dependencies (resolved at call-time via window):
 *   window.getCoordinatesData(), window.selectCoordinate(i), window.goToEdit(i),
 *   window.switchView(view), window.showToast?.(msg, kind),
 *   window.events / window.EV (REGION_SELECTED, VIEW_CHANGED)
 */

/** Link tab name → `switchView` view name. */
export const TAB_VIEWS = { explore: 'map', globe: 'globe', edit: 'dem', extrude: 'model' };
const VIEW_TABS = Object.fromEntries(Object.entries(TAB_VIEWS).map(([t, v]) => [v, t]));

/**
 * Read a location hash such as `#region=Miami%2C%20FL&tab=edit`.
 * @param {string} hash
 * @returns {{region: string, tab: string} | null} null when the hash names no region;
 *   an unknown tab falls back to `explore`.
 */
export function parseRegionHash(hash) {
    const params = new URLSearchParams(String(hash || '').replace(/^#/, ''));
    const region = (params.get('region') || '').trim();
    if (!region) return null;
    const tab = (params.get('tab') || '').toLowerCase();
    return { region, tab: tab in TAB_VIEWS ? tab : 'explore' };
}

/**
 * Build the hash for a region and tab. Explore is the default and is left out.
 * @param {string} name
 * @param {string} [tab]
 * @returns {string}
 */
export function regionHash(name, tab = 'explore') {
    const t = tab in TAB_VIEWS && tab !== 'explore' ? `&tab=${tab}` : '';
    return `#region=${encodeURIComponent(name)}${t}`;
}

const _loose = (s) => String(s || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');

/**
 * Index of the region a link names, or -1.
 * @param {Array<{name: string}>} regions
 * @param {string} name
 * @returns {number}
 */
export function findRegionIndex(regions, name) {
    const exact = regions.findIndex((r) => r?.name === name);
    if (exact >= 0) return exact;
    const key = _loose(name);
    return key ? regions.findIndex((r) => _loose(r?.name) === key) : -1;
}

let _opening = false;

/** Write the selected region and open tab into the address bar (no history entry). */
function _syncHash() {
    if (_opening) return;
    const region = window.appState?.selectedRegion;
    if (!region?.name) return;
    const tab = VIEW_TABS[window.appState?.activeView] || 'explore';
    const hash = regionHash(region.name, tab);
    if (location.hash !== hash) history.replaceState(null, '', location.pathname + location.search + hash);
}

/**
 * Open the region and tab the address bar names. Leaves the page alone when the hash
 * names no region; shows a toast when the region does not exist.
 * @returns {Promise<boolean>} true when a region was opened
 */
async function openRegionFromLocation() {
    const link = parseRegionHash(location.hash);
    if (!link) return false;
    const regions = window.getCoordinatesData?.() || [];
    const index = findRegionIndex(regions, link.region);
    if (index < 0) {
        window.showToast?.(`No saved region is called "${link.region}"`, 'error');
        _syncHash();   // put the open region's link back
        return false;
    }
    _opening = true;
    try {
        if (link.tab === 'edit') {
            await window.goToEdit(index);
        } else {
            await window.selectCoordinate(index);
            const view = TAB_VIEWS[link.tab];
            if (window.appState?.activeView !== view) window.switchView(view);
        }
    } finally {
        _opening = false;
    }
    _syncHash();
    return true;
}
if (typeof window !== 'undefined') {   // the pure helpers above are also imported by Node tests
    window.openRegionFromLocation = openRegionFromLocation;
    window.events?.on?.(window.EV?.REGION_SELECTED, _syncHash);
    window.events?.on?.(window.EV?.VIEW_CHANGED, _syncHash);
    // A link pasted into the address bar of an open page (replaceState does not fire this).
    window.addEventListener('hashchange', () => { openRegionFromLocation(); });
}

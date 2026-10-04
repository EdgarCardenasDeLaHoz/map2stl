/**
 * modules/regions/region-ui.js — Region list, table, notes, and thumbnail UI.
 *
 * The sidebar list shows the map's viewport set (region-boxes.js::getViewportRegionSet:
 * the ≤ 20 regions drawn on the map, plus the selected one) under a
 * "Showing N of M in view · Show all" line; a search, or "Show all", lists every
 * region instead. Each row selects on click / Enter and carries a ✎ button that
 * opens the region editor (region-editor.js::openRegionEditor). Row and map-box
 * hovers are linked (region-boxes.js::highlightRegionBox).
 *
 * Public API (all on window):
 *   detectContinent(lat, lon)            — heuristic continent name (continent.js)
 *   groupRegionsByContinent(regions)     — group array by continent
 *   resolveRegionContinent(region)       — label-or-detected continent (filter key)
 *   renderCoordinatesList()              — render sidebar list view
 *   populateRegionsTable()               — render sidebar table view
 *   loadRegionFromTable(index)           — navigate to Edit for region
 *   viewRegionOnMap(index)               — select region + switch to map
 *   setupRegionsTable()                  — wire table search + refresh
 *   initRegionNotes()                    — load notes from localStorage
 *   getRegionNote(name) / setRegionNote(name, text) — notes (edited in the region editor)
 *   renameRegionLocalData(old, new)      — move notes + thumbnail after a rename
 *   initRegionThumbnails()               — load thumbnails from localStorage
 *   saveRegionThumbnail(name, dataURL)   — persist a thumbnail
 *
 * External dependencies:
 *   window.getCoordinatesData()          — accessor for coordinatesData closure var
 *   window.getSidebarState()             — accessor for sidebarState closure var
 *   window.appState.selectedRegion
 *   window.appState.regionThumbnails    — set by initRegionThumbnails()
 *   window.selectCoordinate(index)      — from app.js
 *   window.goToEdit(index)              — from app.js
 *   window.switchView(view)             — from app.js
 *   window.renderSidebarTable()         — from app.js
 *   window.loadCoordinates()            — from app.js
 *   window.showToast(msg, type)                — file-top global in app.js
 *   window.getViewportRegionSet / refreshRegionViewSet / highlightRegionBox (region-boxes.js)
 *   window.openRegionEditor(index)      — region-editor.js
 */

import { detectContinent } from './continent.js';
import { formatBboxDims, regionColor } from './region-geometry.js';

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

const CONTINENT_HIDDEN = new Set();

let regionThumbnails = {};
let regionNotes = {};

// ── Sidebar list pagination ───────────────────────────────────────────────────
const LIST_PAGE_SIZE = 20;
let _listPage = 0;
let _lastListSearch = '';  // used to reset the page when search changes
let _lastContinentFilter = 'all';
// "Show all" lists every region instead of the map's viewport set. The map
// keeps drawing the viewport set either way (drawing all of them is the
// clutter the set exists to avoid).
let _listShowAll = false;

const KNOWN_CONTINENTS = ['North America', 'South America', 'Europe', 'Africa', 'Asia', 'Oceania', 'Antarctica', 'Other'];

function _normalizeContinentLabel(label) {
    const raw = String(label || '').trim();
    if (!raw) return '';
    const key = raw.toLowerCase();
    const alias = {
        'north america': 'North America',
        'south america': 'South America',
        'europe': 'Europe',
        'africa': 'Africa',
        'asia': 'Asia',
        'oceania': 'Oceania',
        'antarctica': 'Antarctica',
        'other': 'Other',
    };
    return alias[key] || '';
}

function _resolveRegionContinent(region) {
    const normalized = _normalizeContinentLabel(region.label);
    if (normalized) return normalized;
    const lat = (region.north + region.south) / 2;
    const lon = (region.east + region.west) / 2;
    return detectContinent(lat, lon);
}

function _syncContinentFilterOptions(coordinatesData) {
    const select = document.getElementById('coordContinentFilter');
    if (!select) return;

    const selected = select.value || 'all';
    const values = new Set();
    coordinatesData.forEach((r) => values.add(_resolveRegionContinent(r)));

    const known = KNOWN_CONTINENTS.filter((c) => values.has(c));
    const options = ['all', ...known];

    select.innerHTML = options.map((c) => {
        const label = c === 'all' ? 'All Continents' : c;
        return `<option value="${c}">${label}</option>`;
    }).join('');

    select.value = options.includes(selected) ? selected : 'all';
}

// ─────────────────────────────────────────────────────────────────────────────
// Continent detection + grouping
// ─────────────────────────────────────────────────────────────────────────────

function groupRegionsByContinent(regions) {
    const groups = {};
    const ORDER = ['North America', 'South America', 'Europe', 'Africa', 'Asia', 'Oceania', 'Antarctica', 'Other'];
    const normalize = (label) => {
        const key = String(label || '').trim().toLowerCase();
        const alias = {
            'north america': 'North America',
            'south america': 'South America',
            'europe': 'Europe',
            'africa': 'Africa',
            'asia': 'Asia',
            'oceania': 'Oceania',
            'antarctica': 'Antarctica',
            'other': 'Other',
        };
        return alias[key] || '';
    };
    regions.forEach(region => {
        const lat = (region.north + region.south) / 2;
        const lon = (region.east + region.west) / 2;
        const labelContinent = normalize(region.label);
        const continent = labelContinent || detectContinent(lat, lon);
        if (!groups[continent]) groups[continent] = [];
        groups[continent].push(region);
    });
    Object.values(groups).forEach(g => g.sort((a, b) => a.name.localeCompare(b.name)));
    const known = ORDER.filter(c => groups[c]).map(c => ({ continent: c, regions: groups[c] }));
    const custom = Object.keys(groups).filter(c => !ORDER.includes(c)).sort()
        .map(c => ({ continent: c, regions: groups[c] }));
    return [...known, ...custom];
}

// ─────────────────────────────────────────────────────────────────────────────
// Sidebar list view
// ─────────────────────────────────────────────────────────────────────────────

/**
 * The line above the list saying which regions it shows, with the toggle:
 * "Showing 20 of 71 in view · Show all" / "Showing all 125 · Only those in view".
 * @param {{inViewCount: number, shownInView: number, total: number}|null} viewSet
 *   The viewport set, or null in show-all mode.
 * @param {number} shownCount - Regions listed (show-all mode)
 */
function _renderListScope(viewSet, shownCount) {
    // A two-option segmented control (design guidelines §1.10): "In view (20)" | "All (125)".
    // In view lists the map's viewport set (≤ 20, largest first); All lists every region.
    const el = document.createElement('div');
    el.className = 'coord-list-scope';
    el.setAttribute('role', 'radiogroup');
    el.setAttribute('aria-label', 'Which regions to list');
    const total = viewSet ? viewSet.total : shownCount;
    const inView = viewSet ? `In view (${viewSet.shownInView})` : 'In view';
    const inViewTitle = viewSet && viewSet.inViewCount > viewSet.shownInView
        ? `The ${viewSet.shownInView} largest of the ${viewSet.inViewCount} regions in view, as drawn on the map`
        : 'The regions drawn on the map, largest first';
    for (const [label, showAll, title] of [
        [inView, false, inViewTitle],
        [`All (${total})`, true, `List all ${total} saved regions`],
    ]) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'coord-list-scope-btn';
        btn.setAttribute('role', 'radio');
        const on = showAll === !viewSet;
        btn.setAttribute('aria-checked', String(on));
        btn.classList.toggle('on', on);
        btn.textContent = label;
        btn.title = title;
        btn.addEventListener('click', () => {
            if (_listShowAll === showAll) return;
            _listShowAll = showAll;
            _listPage = 0;
            renderCoordinatesList();
            document.querySelector('#coordinatesList .coord-list-scope-btn.on')?.focus();
        });
        el.append(btn);
    }
    return el;
}

function renderCoordinatesList() {
    if (window.getSidebarState?.() === 'expanded') window.renderSidebarTable?.();

    const list = document.getElementById('coordinatesList');
    if (!list) return;
    // Re-rendered on map pans too: keep keyboard focus on the same row/button.
    const focused = list.contains(document.activeElement) ? document.activeElement : null;
    const focusName = focused?.closest('.coordinate-item')?.dataset.regionName;
    const focusEdit = !!focused?.classList.contains('coordinate-item-edit');
    list.innerHTML = '';

    const coordinatesData = window.getCoordinatesData?.() || [];
    if (coordinatesData.length === 0) {
        list.innerHTML = '<div class="loading sidebar-empty-state">' +
            '<span class="sidebar-empty-state-icon">🗺️</span>' +
            '<span class="sidebar-empty-state-title">Draw a region on the map to begin</span>' +
            '<span class="sidebar-empty-state-hint">Use the ✏️ draw button on the map to select an area</span>' +
            '</div>';
        return;
    }

    const searchVal = (document.getElementById('coordSearch')?.value || '').toLowerCase();
    _syncContinentFilterOptions(coordinatesData);
    const continentFilter = document.getElementById('coordContinentFilter')?.value || 'all';

    // Reset to page 0 whenever the search term changes.
    if (searchVal !== _lastListSearch) {
        _listPage = 0;
        _lastListSearch = searchVal;
    }

    if (continentFilter !== _lastContinentFilter) {
        _listPage = 0;
        _lastContinentFilter = continentFilter;
    }

    // Search finds any region; otherwise the list shows the map's viewport set
    // (region-boxes.js::getViewportRegionSet), or every region after "Show all".
    const viewportMode = !searchVal && !_listShowAll;
    let filtered;
    let viewSet = null;
    if (viewportMode && window.getViewportRegionSet) {
        viewSet = window.getViewportRegionSet();
        filtered = viewSet.regions;
    } else {
        filtered = coordinatesData.filter((r) => {
            const matchesSearch = !searchVal || r.name.toLowerCase().includes(searchVal);
            if (!matchesSearch) return false;
            if (continentFilter === 'all') return true;
            return _resolveRegionContinent(r) === continentFilter;
        });
    }

    // ── Pagination (search / show-all only; the viewport set is ≤ 21) ──────
    const totalPages = viewSet ? 1 : Math.max(1, Math.ceil(filtered.length / LIST_PAGE_SIZE));
    if (_listPage >= totalPages) _listPage = totalPages - 1;
    const pageStart = viewSet ? 0 : _listPage * LIST_PAGE_SIZE;
    const paginated = viewSet ? filtered : filtered.slice(pageStart, pageStart + LIST_PAGE_SIZE);
    // ────────────────────────────────────────────────────────────────────────

    if (!searchVal) list.appendChild(_renderListScope(viewSet, filtered.length));
    if (viewSet && filtered.length === 0) {
        const hint = document.createElement('div');
        hint.className = 'coord-list-empty-hint';
        hint.textContent = 'No saved regions fit this view. Zoom out, or use Show all.';
        list.appendChild(hint);
    }

    const groups = groupRegionsByContinent(paginated);
    const outerFrag = document.createDocumentFragment();
    const selected = window.appState?.selectedRegion;
    const indexByName = new Map(coordinatesData.map((r, i) => [r.name, i]));

    groups.forEach(({ continent, regions: groupRegions }) => {
        const isHidden = CONTINENT_HIDDEN.has(continent);

        const groupEl = document.createElement('div');
        groupEl.className = 'continent-group-sidebar';

        const header = document.createElement('div');
        header.className = 'continent-header-sidebar';
        header.innerHTML = `
            <span class="continent-arrow-sidebar">▾</span>
            <span class="continent-label-sidebar">${continent}</span>
            <span class="continent-count-sidebar">${groupRegions.length}</span>
        `;
        if (isHidden) header.classList.add('collapsed');
        header.addEventListener('click', () => {
            const nowCollapsed = header.classList.toggle('collapsed');
            body.classList.toggle('collapsed');
            if (nowCollapsed) CONTINENT_HIDDEN.add(continent);
            else CONTINENT_HIDDEN.delete(continent);
        });

        const body = document.createElement('div');
        body.className = 'continent-body-sidebar';
        if (isHidden) body.classList.add('collapsed');

        const itemFrag = document.createDocumentFragment();
        groupRegions.forEach(region => {
            const originalIndex = indexByName.get(region.name) ?? -1;
            const item = document.createElement('div');
            item.className = 'coordinate-item';
            item.dataset.regionName = region.name;
            if (selected && selected.name === region.name) item.classList.add('selected');
            const esc = window.escapeHtml;
            item.innerHTML = `
                <span class="coordinate-item-swatch" aria-hidden="true" style="background:${regionColor(region.name)}"></span>
                <span class="coordinate-item-text">
                    <span class="coordinate-item-name">${esc(region.name)}</span>
                    <span class="coordinate-item-meta">${esc([formatBboxDims(region), region.description]
                        .filter(Boolean).join(' · '))}</span>
                </span>
                <button type="button" class="coordinate-item-edit"
                        aria-label="Edit ${esc(region.name)}" title="Edit region">✎</button>
            `;
            const editBtn = item.querySelector('.coordinate-item-edit');
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                void window.openRegionEditor?.(originalIndex);
            });
            // Enter/Space on the button must not also select through the row.
            editBtn.addEventListener('keydown', (e) => e.stopPropagation());
            item.tabIndex = 0;
            item.setAttribute('role', 'option');
            item.onclick = () => window.selectCoordinate?.(originalIndex);
            item.addEventListener('mouseenter', () => window.highlightRegionBox?.(region.name, true));
            item.addEventListener('mouseleave', () => window.highlightRegionBox?.(region.name, false));
            item.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); window.selectCoordinate?.(originalIndex); }
                else if (e.key === 'ArrowDown') { e.preventDefault(); const next = item.nextElementSibling || item.parentElement.nextElementSibling?.querySelector('.coordinate-item'); if (next) next.focus(); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); const prev = item.previousElementSibling || item.parentElement.previousElementSibling?.querySelector('.coordinate-item:last-child'); if (prev) prev.focus(); }
            });
            itemFrag.appendChild(item);
        });
        body.appendChild(itemFrag);

        groupEl.appendChild(header);
        groupEl.appendChild(body);
        outerFrag.appendChild(groupEl);
    });

    list.appendChild(outerFrag);
    if (focusName) {
        const row = list.querySelector(`.coordinate-item[data-region-name="${CSS.escape(focusName)}"]`);
        (focusEdit ? row?.querySelector('.coordinate-item-edit') : row)?.focus();
    }

    // ── Pagination controls ─────────────────────────────────────────────────
    if (totalPages > 1) {
        const pag = document.createElement('div');
        pag.className = 'list-pagination';
        const start = pageStart + 1;
        const end = Math.min(pageStart + LIST_PAGE_SIZE, filtered.length);
        pag.innerHTML = `
            <button id="listPagePrev" ${_listPage === 0 ? 'disabled' : ''}>&#8249; Prev</button>
            <span>${start}–${end} of ${filtered.length}</span>
            <button id="listPageNext" ${_listPage >= totalPages - 1 ? 'disabled' : ''}>Next &#8250;</button>
        `;
        pag.querySelector('#listPagePrev')?.addEventListener('click', () => {
            _listPage--;
            renderCoordinatesList();
        });
        pag.querySelector('#listPageNext')?.addEventListener('click', () => {
            _listPage++;
            renderCoordinatesList();
        });
        list.appendChild(pag);
    }
    // ────────────────────────────────────────────────────────────────────────
}

// ─────────────────────────────────────────────────────────────────────────────
// Regions table view
// ─────────────────────────────────────────────────────────────────────────────

const TABLE_PAGE_SIZE = 20;
let _tablePage = 0;
let _tableSearch = '';

function populateRegionsTable() {
    const tbody = document.getElementById('regionsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const coordinatesData = window.getCoordinatesData?.() || [];
    if (coordinatesData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);">No regions loaded</td></tr>';
        _renderTablePagination(0, 0);
        return;
    }

    const q = _tableSearch.toLowerCase();
    const filtered = q
        ? coordinatesData.filter((r) => r.name.toLowerCase().includes(q))
        : coordinatesData;

    const totalPages = Math.max(1, Math.ceil(filtered.length / TABLE_PAGE_SIZE));
    if (_tablePage >= totalPages) _tablePage = totalPages - 1;

    const start = _tablePage * TABLE_PAGE_SIZE;
    const pageData = filtered.slice(start, start + TABLE_PAGE_SIZE);

    const selected = window.appState?.selectedRegion;
    const indexByName = new Map(coordinatesData.map((r, i) => [r.name, i]));

    pageData.forEach((region) => {
        const index = indexByName.get(region.name) ?? -1;
        const tr = document.createElement('tr');
        tr.dataset.regionIndex = index;
        if (selected && selected.name === region.name) tr.classList.add('selected');
        tr.innerHTML = `
            <td>${window.escapeHtml(region.name)}</td>
            <td>${region.north?.toFixed(5) || ''}</td>
            <td>${region.south?.toFixed(5) || ''}</td>
            <td>${region.east?.toFixed(5) || ''}</td>
            <td>${region.west?.toFixed(5) || ''}</td>
            <td class="actions-cell">
                <button class="action-btn load" onclick="loadRegionFromTable(${index})">Load</button>
                <button class="action-btn" onclick="viewRegionOnMap(${index})">📍 Map</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    _renderTablePagination(filtered.length, totalPages);
}

function _renderTablePagination(total, totalPages) {
    const el = document.getElementById('regionsPagination');
    if (!el) return;
    if (total <= TABLE_PAGE_SIZE) {
        el.innerHTML = '';
        return;
    }
    const start = _tablePage * TABLE_PAGE_SIZE + 1;
    const end = Math.min((_tablePage + 1) * TABLE_PAGE_SIZE, total);
    el.innerHTML = `
        <button id="regPagePrev" ${_tablePage === 0 ? 'disabled' : ''}>&#8249; Prev</button>
        <span>${start}–${end} of ${total}</span>
        <button id="regPageNext" ${_tablePage >= totalPages - 1 ? 'disabled' : ''}>Next &#8250;</button>
    `;
    el.querySelector('#regPagePrev')?.addEventListener('click', () => { _tablePage--; populateRegionsTable(); });
    el.querySelector('#regPageNext')?.addEventListener('click', () => { _tablePage++; populateRegionsTable(); });
}

function loadRegionFromTable(index) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    if (index >= 0 && index < coordinatesData.length) window.goToEdit?.(index);
}

function viewRegionOnMap(index) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    if (index >= 0 && index < coordinatesData.length) {
        window.selectCoordinate?.(index);
        window.switchView?.('map');
    }
}

function setupRegionsTable() {
    const searchInput = document.getElementById('regionsSearch');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            _tableSearch = e.target.value;
            _tablePage = 0;
            populateRegionsTable();
        });
    }

    document.getElementById('refreshRegionsBtn')?.addEventListener('click', async () => {
        await window.loadCoordinates?.();
        populateRegionsTable();
        window.showToast('Regions refreshed', 'success');
    });

    if (!window.__coordContinentFilterDelegated) {
        document.addEventListener('change', (e) => {
            const target = e.target;
            if (!(target instanceof HTMLElement)) return;
            if (target.id !== 'coordContinentFilter') return;
            _listPage = 0;
            // The filter narrows the map's boxes too, so recompute the shared set.
            if (window.refreshRegionViewSet) window.refreshRegionViewSet({ force: true });
            else renderCoordinatesList();
        });
        window.__coordContinentFilterDelegated = true;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Region thumbnails
// ─────────────────────────────────────────────────────────────────────────────

function initRegionThumbnails() {
    try {
        const saved = localStorage.getItem('map2stl_thumbs');
        if (saved) regionThumbnails = JSON.parse(saved);
    } catch (_) { /* best-effort; failure is non-fatal */ }
    window.appState.regionThumbnails = regionThumbnails;
}

function saveRegionThumbnail(name, dataURL) {
    regionThumbnails[name] = dataURL;
    try { localStorage.setItem('map2stl_thumbs', JSON.stringify(regionThumbnails)); } catch (_) { /* best-effort; failure is non-fatal */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// Region notes
// ─────────────────────────────────────────────────────────────────────────────

function initRegionNotes() {
    try {
        const saved = localStorage.getItem('map2stl_regionNotes');
        if (saved) regionNotes = JSON.parse(saved);
    } catch (e) {
        console.warn('Failed to load region notes:', e);
    }
}

function _persistNotes() {
    try { localStorage.setItem('map2stl_regionNotes', JSON.stringify(regionNotes)); }
    catch (_) { window.showToast('Could not save notes — storage full or unavailable', 'warning'); }
}

/** The saved note for a region ('' if none). Read by region-editor.js. */
function getRegionNote(name) {
    return regionNotes[name] || '';
}

/** Store (or, when blank, remove) a region's note. */
function setRegionNote(name, text) {
    const note = String(text || '').trim();
    if ((regionNotes[name] || '') === note) return;
    if (note) regionNotes[name] = note;
    else delete regionNotes[name];
    _persistNotes();
}

/**
 * Move the browser-side data keyed by region name (notes, thumbnails) after a
 * rename. The server moves its own (settings, landmarks) in PUT /api/regions/{name}.
 */
function renameRegionLocalData(oldName, newName) {
    if (oldName === newName) return;
    if (oldName in regionNotes) {
        regionNotes[newName] = regionNotes[oldName];
        delete regionNotes[oldName];
        _persistNotes();
    }
    if (oldName in regionThumbnails) {
        regionThumbnails[newName] = regionThumbnails[oldName];
        delete regionThumbnails[oldName];
        try { localStorage.setItem('map2stl_thumbs', JSON.stringify(regionThumbnails)); } catch (_) { /* best-effort */ }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.detectContinent = detectContinent;
window.groupRegionsByContinent = groupRegionsByContinent;
window.renderCoordinatesList = renderCoordinatesList;
window.populateRegionsTable = populateRegionsTable;
window.loadRegionFromTable = loadRegionFromTable;
window.viewRegionOnMap = viewRegionOnMap;
window.setupRegionsTable = setupRegionsTable;
window.initRegionThumbnails = initRegionThumbnails;
window.saveRegionThumbnail = saveRegionThumbnail;
window.initRegionNotes = initRegionNotes;
window.getRegionNote = getRegionNote;
window.setRegionNote = setRegionNote;
window.renameRegionLocalData = renameRegionLocalData;
window.resolveRegionContinent = _resolveRegionContinent;

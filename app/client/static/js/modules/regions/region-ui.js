/**
 * modules/region-ui.js — Region list, table, notes, and thumbnail UI.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   detectContinent(lat, lon)            — heuristic continent name
 *   groupRegionsByContinent(regions)     — group array by continent
 *   renderCoordinatesList()              — render sidebar list view
 *   populateRegionsTable()               — render sidebar table view
 *   loadRegionFromTable(index)           — navigate to Edit for region
 *   viewRegionOnMap(index)               — select region + switch to map
 *   setupRegionsTable()                  — wire table search + refresh
 *   initRegionNotes()                    — load notes from localStorage
 *   showNotesModal(regionName)           — open notes modal
 *   hideNotesModal()                     — close notes modal
 *   saveRegionNotes()                    — persist notes + close modal
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
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

const CONTINENT_HIDDEN = new Set();
const REGION_TABLE_FILTER_STORAGE_KEY = 'strm2stl_filterRegionsToViewport';

let regionThumbnails = {};
let regionNotes = {};
let currentNotesRegion = null;
let _filterRegionsToViewport = false;

// ── Sidebar list pagination ───────────────────────────────────────────────────
const LIST_PAGE_SIZE = 20;
let _listPage = 0;
let _lastListSearch = '';  // used to reset the page when search changes

try {
    _filterRegionsToViewport = localStorage.getItem(REGION_TABLE_FILTER_STORAGE_KEY) === 'true';
} catch (_) {}

function _regionIntersectsBounds(region, bounds) {
    if (!bounds || !region) return true;
    return !(
        region.east < bounds.getWest() ||
        region.west > bounds.getEast() ||
        region.north < bounds.getSouth() ||
        region.south > bounds.getNorth()
    );
}

window.getFilterRegionsToViewport = function getFilterRegionsToViewport() {
    return _filterRegionsToViewport;
};

window.setFilterRegionsToViewport = function setFilterRegionsToViewport(enabled) {
    _filterRegionsToViewport = Boolean(enabled);
    try {
        localStorage.setItem(REGION_TABLE_FILTER_STORAGE_KEY, String(_filterRegionsToViewport));
    } catch (_) {}
    window.renderSidebarTable?.();
    window.populateRegionsTable?.();
};

window.filterRegionsForMapViewport = function filterRegionsForMapViewport(regions) {
    if (!_filterRegionsToViewport) return regions;
    const bounds = window.getMap?.()?.getBounds?.();
    if (!bounds) return regions;
    return regions.filter(region => _regionIntersectsBounds(region, bounds));
};

// ─────────────────────────────────────────────────────────────────────────────
// Continent detection + grouping
// ─────────────────────────────────────────────────────────────────────────────

function detectContinent(lat, lon) {
    if (lat < -60) return 'Antarctica';
    if (lat >= -55 && lat <= -10 && lon >= 110 && lon <= 180) return 'Oceania';
    if (lat >= -10 && lat <= 0 && lon >= 130 && lon <= 180) return 'Oceania';
    if (lat >= -56 && lat <= 13 && lon >= -82 && lon <= -34) return 'South America';
    if (lat >= 13 && lat <= 75 && lon >= -168 && lon <= -52) return 'North America';
    if (lat >= 8 && lat <= 28 && lon >= -90 && lon <= -52) return 'North America';
    if (lat >= 55 && lon >= 26 && lon <= 180) return 'Asia';
    if (lat >= -11 && lat <= 55 && lon >= 60 && lon <= 145) return 'Asia';
    if (lat >= 25 && lat <= 43 && lon >= 35 && lon <= 60) return 'Asia';
    if (lat >= -37 && lat <= 38 && lon >= -18 && lon <= 52) return 'Africa';
    if (lat >= 35 && lat <= 72 && lon >= -25 && lon <= 45) return 'Europe';
    return 'Other';
}

function groupRegionsByContinent(regions) {
    const groups = {};
    const ORDER = ['North America','South America','Europe','Africa','Asia','Oceania','Antarctica','Other'];
    regions.forEach(region => {
        const lat = (region.north + region.south) / 2;
        const lon = (region.east + region.west) / 2;
        const continent = (region.label && region.label.trim()) ? region.label.trim() : detectContinent(lat, lon);
        if (!groups[continent]) groups[continent] = [];
        groups[continent].push(region);
    });
    Object.values(groups).forEach(g => g.sort((a, b) => a.name.localeCompare(b.name)));
    const known  = ORDER.filter(c => groups[c]).map(c => ({ continent: c, regions: groups[c] }));
    const custom = Object.keys(groups).filter(c => !ORDER.includes(c)).sort()
        .map(c => ({ continent: c, regions: groups[c] }));
    return [...known, ...custom];
}

// ─────────────────────────────────────────────────────────────────────────────
// Sidebar list view
// ─────────────────────────────────────────────────────────────────────────────

function renderCoordinatesList() {
    if (window.getSidebarState?.() === 'expanded') window.renderSidebarTable?.();

    const list = document.getElementById('coordinatesList');
    if (!list) return;
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

    // Reset to page 0 whenever the search term changes.
    if (searchVal !== _lastListSearch) {
        _listPage = 0;
        _lastListSearch = searchVal;
    }

    const filtered  = searchVal
        ? coordinatesData.filter(r => r.name.toLowerCase().includes(searchVal))
        : coordinatesData;

    // ── Pagination ──────────────────────────────────────────────────────────
    const totalPages = Math.max(1, Math.ceil(filtered.length / LIST_PAGE_SIZE));
    if (_listPage >= totalPages) _listPage = totalPages - 1;
    const pageStart  = _listPage * LIST_PAGE_SIZE;
    const paginated  = filtered.slice(pageStart, pageStart + LIST_PAGE_SIZE);
    // ────────────────────────────────────────────────────────────────────────

    const groups     = groupRegionsByContinent(paginated);
    const outerFrag  = document.createDocumentFragment();
    const selected   = window.appState?.selectedRegion;
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
            const hasNote = regionNotes[region.name] && regionNotes[region.name].trim() !== '';
            const item = document.createElement('div');
            item.className = 'coordinate-item';
            item.dataset.regionName = region.name;
            if (selected && selected.name === region.name) item.classList.add('selected');
            item.innerHTML = `
                <span class="coordinate-item-icon">📍</span>
                <span class="coordinate-item-name">${region.name}</span>
                <span class="coordinate-item-meta">${region.description || ''}</span>
                <span class="coordinate-item-notes ${hasNote ? 'has-note' : ''}"
                      onclick="event.stopPropagation(); showNotesModal('${region.name.replace(/'/g, "\\'")}')"
                      title="${hasNote ? 'View/edit notes' : 'Add notes'}">📝</span>
            `;
            item.tabIndex = 0;
            item.setAttribute('role', 'option');
            item.onclick = () => window.selectCoordinate?.(originalIndex);
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

    // ── Pagination controls ─────────────────────────────────────────────────
    if (totalPages > 1) {
        const pag = document.createElement('div');
        pag.className = 'list-pagination';
        const start = pageStart + 1;
        const end   = Math.min(pageStart + LIST_PAGE_SIZE, filtered.length);
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

/** Render tag chips for display. */
function _renderTagChips(tagsStr) {
    if (!tagsStr) return '<span class="tag-empty">—</span>';
    return tagsStr.split(',')
        .map(t => t.trim()).filter(Boolean)
        .map(t => `<span class="region-tag-chip">${t}</span>`)
        .join('');
}

/** Inline tag editor — replaces chips cell with an input; saves on blur/Enter. */
function _startTagEdit(td, region, index) {
    const current = (region.tags || '').trim();
    td.innerHTML = '';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = current;
    inp.className = 'tag-edit-input';
    inp.placeholder = 'comma-separated tags';
    td.appendChild(inp);
    inp.focus();
    inp.select();

    const save = async () => {
        const newTags = inp.value.split(',').map(t => t.trim()).filter(Boolean).join(', ');
        region.tags = newTags;
        td.innerHTML = _renderTagChips(newTags);
        td.classList.add('tag-cell');
        _bindTagCellClick(td, region, index);
        // Persist to server
        try {
            await window.api.regions.update(region.name, {
                name: region.name, north: region.north, south: region.south,
                east: region.east, west: region.west,
                label: region.label, description: region.description,
                tags: newTags,
            });
        } catch (e) {
            window.showToast?.('Failed to save tags', 'error');
        }
    };
    inp.addEventListener('blur', save);
    inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
        if (e.key === 'Escape') { td.innerHTML = _renderTagChips(current); td.classList.add('tag-cell'); _bindTagCellClick(td, region, index); }
    });
}

function _bindTagCellClick(td, region, index) {
    td.addEventListener('click', () => _startTagEdit(td, region, index), { once: true });
}

function populateRegionsTable() {
    const tbody = document.getElementById('regionsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const coordinatesData = window.getCoordinatesData?.() || [];
    if (coordinatesData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888;">No regions loaded</td></tr>';
        _renderTablePagination(0, 0);
        return;
    }

    const q = _tableSearch.toLowerCase();
    const filteredBySearch = q
        ? coordinatesData.filter((r, i) =>
            r.name.toLowerCase().includes(q) ||
            (r.tags || '').toLowerCase().includes(q))
        : coordinatesData;
    const filtered = window.filterRegionsForMapViewport?.(filteredBySearch) || filteredBySearch;

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

        // Static columns
        tr.innerHTML = `
            <td>${region.name}</td>
            <td>${region.north?.toFixed(5) || ''}</td>
            <td>${region.south?.toFixed(5) || ''}</td>
            <td>${region.east?.toFixed(5) || ''}</td>
            <td>${region.west?.toFixed(5) || ''}</td>
        `;

        // Tags cell (interactive — built in JS to avoid XSS in chip HTML)
        const tagsTd = document.createElement('td');
        tagsTd.className = 'tag-cell';
        tagsTd.title = 'Click to edit tags';
        tagsTd.innerHTML = _renderTagChips(region.tags);
        _bindTagCellClick(tagsTd, region, index);
        tr.appendChild(tagsTd);

        // Actions cell
        const actTd = document.createElement('td');
        actTd.className = 'actions-cell';
        actTd.innerHTML = `
            <button class="action-btn load" onclick="loadRegionFromTable(${index})">Load</button>
            <button class="action-btn" onclick="viewRegionOnMap(${index})">📍 Map</button>
            <button class="action-btn" onclick="toggleRegionBboxHidden('${region.name.replace(/'/g, "\\'")}')">${window.isRegionBboxHidden?.(region.name) ? 'Show' : 'Hide'}</button>
            <button class="action-btn" onclick="_clearRegionCache(${index})" title="Clear cached DEM/water/satellite data for this region">♻ Cache</button>
            <button class="action-btn danger" onclick="_deleteRegionFromTable(${index})">🗑</button>
        `;
        tr.appendChild(actTd);

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
    const end   = Math.min((_tablePage + 1) * TABLE_PAGE_SIZE, total);
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

async function _deleteRegionFromTable(index) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    if (index < 0 || index >= coordinatesData.length) return;
    const region = coordinatesData[index];
    const confirmed = window.confirm(`Delete region "${region.name}"?\nThis cannot be undone.`);
    if (!confirmed) return;
    try {
        await window.api.regions.delete(region.name);
        await window.loadCoordinates?.();
        populateRegionsTable();
        window.renderCoordinatesList?.();
        window.showToast?.(`Deleted "${region.name}"`, 'success');
    } catch (e) {
        window.showToast?.('Delete failed: ' + (e?.message || e), 'error');
    }
}
window._deleteRegionFromTable = _deleteRegionFromTable;

async function _clearRegionCache(index) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    if (index < 0 || index >= coordinatesData.length) return;
    const region = coordinatesData[index];
    try {
        const result = await window.api.cache.clearRegion({
            north: region.north, south: region.south,
            east: region.east, west: region.west,
        });
        const n = result?.files_deleted ?? '?';
        window.showToast?.(`Cache cleared for "${region.name}" (${n} files removed)`, 'success');
        window._clearDemResponseCache?.();
    } catch (e) {
        window.showToast?.('Cache clear failed: ' + (e?.message || e), 'error');
    }
}
window._clearRegionCache = _clearRegionCache;

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

    const vpBtn = document.getElementById('viewportFilterBtn');
    if (vpBtn) {
        const _updateVpBtn = () => {
            const active = window.getFilterRegionsToViewport?.();
            vpBtn.classList.toggle('active', Boolean(active));
            vpBtn.title = active ? 'Showing only regions visible on map (click to show all)' : 'Show only regions visible on map';
        };
        _updateVpBtn();
        vpBtn.addEventListener('click', () => {
            window.setFilterRegionsToViewport?.(!window.getFilterRegionsToViewport?.());
            _updateVpBtn();
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Region thumbnails
// ─────────────────────────────────────────────────────────────────────────────

function initRegionThumbnails() {
    try {
        const saved = localStorage.getItem('strm2stl_thumbs');
        if (saved) regionThumbnails = JSON.parse(saved);
    } catch (_) {}
    window.appState.regionThumbnails = regionThumbnails;
}

function saveRegionThumbnail(name, dataURL) {
    regionThumbnails[name] = dataURL;
    try { localStorage.setItem('strm2stl_thumbs', JSON.stringify(regionThumbnails)); } catch (_) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// Region notes
// ─────────────────────────────────────────────────────────────────────────────

function initRegionNotes() {
    try {
        const saved = localStorage.getItem('strm2stl_regionNotes');
        if (saved) regionNotes = JSON.parse(saved);
    } catch (e) {
        console.warn('Failed to load region notes:', e);
    }

    const modal = document.getElementById('regionNotesModal');
    if (modal) {
        modal.addEventListener('click', (e) => { if (e.target === modal) hideNotesModal(); });
        modal.querySelector('[data-action="notes-cancel"]')?.addEventListener('click', hideNotesModal);
        modal.querySelector('[data-action="notes-save"]')?.addEventListener('click', saveRegionNotes);
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) hideNotesModal();
    });
}

function showNotesModal(regionName) {
    currentNotesRegion = regionName;
    const modal    = document.getElementById('regionNotesModal');
    const nameSpan = document.getElementById('notesRegionName');
    const textarea = document.getElementById('notesTextarea');

    nameSpan.textContent = regionName;
    textarea.value = regionNotes[regionName] || '';
    modal.classList.remove('hidden');
    textarea.focus();
}

function hideNotesModal() {
    const modal = document.getElementById('regionNotesModal');
    modal.classList.add('hidden');
    currentNotesRegion = null;
}

function saveRegionNotes() {
    if (!currentNotesRegion) return;
    const textarea = document.getElementById('notesTextarea');
    const note = textarea.value.trim();
    if (note) {
        regionNotes[currentNotesRegion] = note;
    } else {
        delete regionNotes[currentNotesRegion];
    }
    try { localStorage.setItem('strm2stl_regionNotes', JSON.stringify(regionNotes)); }
    catch (_) { window.showToast('Could not save notes — storage full or unavailable', 'warning'); }
    hideNotesModal();
    renderCoordinatesList();
    window.showToast('Notes saved!', 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.CONTINENT_HIDDEN        = CONTINENT_HIDDEN;
window.detectContinent          = detectContinent;
window.groupRegionsByContinent  = groupRegionsByContinent;
window.renderCoordinatesList    = renderCoordinatesList;
window.populateRegionsTable     = populateRegionsTable;
window.loadRegionFromTable      = loadRegionFromTable;
window.viewRegionOnMap          = viewRegionOnMap;
window.setupRegionsTable        = setupRegionsTable;
window.initRegionThumbnails     = initRegionThumbnails;
window.saveRegionThumbnail      = saveRegionThumbnail;
window.initRegionNotes          = initRegionNotes;
window.showNotesModal           = showNotesModal;
window.hideNotesModal           = hideNotesModal;
window.saveRegionNotes          = saveRegionNotes;

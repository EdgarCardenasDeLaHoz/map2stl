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

let regionThumbnails = {};
let regionNotes = {};
let currentNotesRegion = null;

// ── Sidebar list pagination ───────────────────────────────────────────────────
const LIST_PAGE_SIZE = 20;
let _listPage = 0;
let _lastListSearch = '';  // used to reset the page when search changes

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
    const ORDER = ['North America', 'South America', 'Europe', 'Africa', 'Asia', 'Oceania', 'Antarctica', 'Other'];
    regions.forEach(region => {
        const lat = (region.north + region.south) / 2;
        const lon = (region.east + region.west) / 2;
        const continent = (region.label && region.label.trim()) ? region.label.trim() : detectContinent(lat, lon);
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

    const filtered = searchVal
        ? coordinatesData.filter(r => r.name.toLowerCase().includes(searchVal))
        : coordinatesData;

    // ── Pagination ──────────────────────────────────────────────────────────
    const totalPages = Math.max(1, Math.ceil(filtered.length / LIST_PAGE_SIZE));
    if (_listPage >= totalPages) _listPage = totalPages - 1;
    const pageStart = _listPage * LIST_PAGE_SIZE;
    const paginated = filtered.slice(pageStart, pageStart + LIST_PAGE_SIZE);
    // ────────────────────────────────────────────────────────────────────────

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
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;">No regions loaded</td></tr>';
        _renderTablePagination(0, 0);
        return;
    }

    const q = _tableSearch.toLowerCase();
    const filtered = q
        ? coordinatesData.filter((r, i) => r.name.toLowerCase().includes(q))
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
            <td>${region.name}</td>
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
}

// ─────────────────────────────────────────────────────────────────────────────
// Region thumbnails
// ─────────────────────────────────────────────────────────────────────────────

function initRegionThumbnails() {
    try {
        const saved = localStorage.getItem('strm2stl_thumbs');
        if (saved) regionThumbnails = JSON.parse(saved);
    } catch (_) { }
    window.appState.regionThumbnails = regionThumbnails;
}

function saveRegionThumbnail(name, dataURL) {
    regionThumbnails[name] = dataURL;
    try { localStorage.setItem('strm2stl_thumbs', JSON.stringify(regionThumbnails)); } catch (_) { }
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
    const modal = document.getElementById('regionNotesModal');
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

window.CONTINENT_HIDDEN = CONTINENT_HIDDEN;
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
window.showNotesModal = showNotesModal;
window.hideNotesModal = hideNotesModal;
window.saveRegionNotes = saveRegionNotes;

// ============================================================
// VIEW MANAGEMENT — modules/view-management.js
// Extracted from app.js (DOMContentLoaded closure).
// Handles top-level tab switching, sidebar state machine,
// sidebar table rendering, bbox layer visibility, status panel,
// region load/save/submit, and DEM sub-tab wiring.
//
// Loaded as a plain <script> before app.js.
// All functions exposed on window.*
// Closure vars accessed via window.appState.* or window.get*()/set*() getters.
// ============================================================

'use strict';

// ---------------------------------------------------------------------------
// switchView
// ---------------------------------------------------------------------------

/**
 * Switch the main view to the specified tab.
 * Hides all containers then shows the selected one.
 * @param {'map'|'globe'|'dem'|'model'|'regions'|'compare'} view
 */
window.switchView = function switchView(view) {
    const mapContainer     = document.getElementById('mapContainer');
    const globeContainer   = document.getElementById('globeContainer');
    const demContainer     = document.getElementById('demContainer');
    const modelContainer   = document.getElementById('modelContainer');
    const compareContainer = document.getElementById('compareContainer');
    const regionsContainer = document.getElementById('regionsContainer');
    const newRegionSection = document.getElementById('newRegionSection');
    const tabs             = document.querySelectorAll('.tab');

    // Restore sidebar visibility (may have been hidden in model view)
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.style.display = '';

    // Hide all
    mapContainer.classList.add('hidden');
    globeContainer.classList.add('hidden');
    demContainer.classList.add('hidden');
    if (modelContainer) {
        modelContainer.classList.add('hidden');
        modelContainer.style.display = 'none';
    }
    if (compareContainer) {
        compareContainer.classList.add('hidden');
    }
    if (regionsContainer) {
        regionsContainer.classList.add('hidden');
    }

    // Show/hide new region section (only visible in 2D Map view)
    if (newRegionSection) {
        newRegionSection.style.display = view === 'map' ? 'block' : 'none';
    }

    // Remove active from tabs
    tabs.forEach(tab => tab.classList.remove('active'));

    // Show selected
    if (view === 'map') {
        mapContainer.classList.remove('hidden');
        document.querySelector('[data-view="map"]').classList.add('active');
    } else if (view === 'globe') {
        globeContainer.classList.remove('hidden');
        document.querySelector('[data-view="globe"]').classList.add('active');
    } else if (view === 'dem') {
        demContainer.classList.remove('hidden');
        document.querySelector('[data-view="dem"]').classList.add('active');
        // Ensure sidebar shows the region list so the user can switch regions
        document.getElementById('sidebarListView')?.classList.remove('hidden');
        document.getElementById('sidebarTableView')?.classList.add('hidden');
        document.getElementById('sidebarEditView')?.classList.add('hidden');
        // Fill bbox inputs immediately if a region is selected (they're normally filled
        // after DEM loads, leaving them blank if the user arrives via the tab button)
        const selectedRegion = window.appState.selectedRegion;
        if (selectedRegion) {
            window.setBboxInputValues?.(selectedRegion.north, selectedRegion.south, selectedRegion.east, selectedRegion.west);
        }
        // Auto-load DEM if a region is selected but no DEM data is loaded yet
        if (selectedRegion && !window.appState.lastDemData) {
            window.loadDEM?.().then(() => {
                window.loadWaterMask?.();
                window.loadSatelliteImage?.();
            });
        }
    } else if (view === 'model') {
        if (modelContainer) {
            modelContainer.classList.remove('hidden');
            modelContainer.style.display = 'flex';
        }
        document.querySelector('[data-view="model"]').classList.add('active');
        // Auto-collapse sidebar so the 3D viewport gets full width
        const sidebarEl = document.querySelector('.sidebar');
        if (sidebarEl) sidebarEl.style.display = 'none';
    } else if (view === 'regions') {
        if (regionsContainer) {
            regionsContainer.classList.remove('hidden');
            window.populateRegionsTable?.();
        }
        document.querySelector('[data-view="regions"]').classList.add('active');
    } else if (view === 'compare') {
        if (compareContainer) {
            compareContainer.classList.remove('hidden');
            window.initCompareMode?.();
        }
        document.querySelector('[data-view="compare"]').classList.add('active');
    }
};

// ---------------------------------------------------------------------------
// _setSidebarViews
// ---------------------------------------------------------------------------

/**
 * Apply a sidebar state to the DOM: show/hide the list and table views.
 * @param {'normal'|'expanded'|'hidden'} state
 */
window._setSidebarViews = function _setSidebarViews(state) {
    const listView      = document.getElementById('sidebarListView');
    const tableView     = document.getElementById('sidebarTableView');
    const editView      = document.getElementById('sidebarEditView');
    const paramsSection = document.getElementById('regionParamsSection');
    editView?.classList.add('hidden');
    if (state === 'expanded') {
        listView?.classList.add('hidden');
        tableView?.classList.remove('hidden');
        paramsSection?.classList.add('hidden');
        window.renderSidebarTable?.();
    } else {
        listView?.classList.remove('hidden');
        tableView?.classList.add('hidden');
        paramsSection?.classList.add('hidden');
    }
};

// ---------------------------------------------------------------------------
// cycleSidebarState
// ---------------------------------------------------------------------------

/**
 * Cycle the sidebar through normal → expanded → hidden → normal.
 * Updates button icon/label and calls _setSidebarViews.
 */
window.cycleSidebarState = function cycleSidebarState() {
    const sidebar   = document.getElementById('sidebar');
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const openBtn   = document.getElementById('openSidebarBtn');
    const icon      = toggleBtn.querySelector('.state-icon');
    const label     = toggleBtn.querySelector('.state-label');

    let sidebarState = window.getSidebarState?.() || 'normal';

    if (sidebarState === 'normal') {
        sidebarState = 'expanded';
        sidebar.classList.remove('collapsed');
        sidebar.classList.add('expanded');
        openBtn.classList.add('hidden');
        icon.textContent  = '⇐';
        label.textContent = 'Hide';
    } else if (sidebarState === 'expanded') {
        sidebarState = 'hidden';
        sidebar.classList.remove('expanded');
        sidebar.classList.add('collapsed');
        openBtn.classList.remove('hidden');
        icon.textContent  = '▶';
        label.textContent = 'Show';
    } else {
        sidebarState = 'normal';
        sidebar.classList.remove('collapsed', 'expanded');
        openBtn.classList.add('hidden');
        icon.textContent  = '⇔';
        label.textContent = 'Expand';
    }

    window.setSidebarState?.(sidebarState);
    window._setSidebarViews?.(sidebarState);
};

// ---------------------------------------------------------------------------
// renderSidebarTable
// ---------------------------------------------------------------------------

/**
 * Render the compact sidebar table of all regions, grouped by continent.
 * @param {string} [filter] - Filter string; defaults to the sidebar search input value
 */
window.renderSidebarTable = function renderSidebarTable(filter) {
    const tbody = document.getElementById('sidebarRegionsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const q            = (filter || document.getElementById('sidebarTableSearch')?.value || '').toLowerCase();
    const coordinatesData = window.getCoordinatesData?.() || [];
    const list         = q ? coordinatesData.filter(r => r.name.toLowerCase().includes(q)) : coordinatesData;
    const groups       = window.groupRegionsByContinent?.(list) || [];
    const selectedRegion = window.appState.selectedRegion;

    groups.forEach(({ continent, regions: groupRegions }) => {
        // Group header row
        const headerTr        = document.createElement('tr');
        headerTr.className    = 'tbl-group-header';
        headerTr.innerHTML    = `<td colspan="7" class="tbl-group-label">${continent} <span class="tbl-group-count">${groupRegions.length}</span></td>`;
        let groupCollapsed    = false;
        headerTr.onclick      = () => {
            groupCollapsed = !groupCollapsed;
            headerTr.classList.toggle('collapsed', groupCollapsed);
            let sibling = headerTr.nextElementSibling;
            while (sibling && !sibling.classList.contains('tbl-group-header')) {
                sibling.style.display = groupCollapsed ? 'none' : '';
                sibling = sibling.nextElementSibling;
            }
        };
        tbody.appendChild(headerTr);

        groupRegions.forEach(region => {
            const originalIndex = coordinatesData.findIndex(r => r.name === region.name);
            const p   = region.parameters || {};
            const dim = p.dim || '—';
            const tr  = document.createElement('tr');
            if (selectedRegion && selectedRegion.name === region.name) tr.classList.add('selected');
            tr.innerHTML = `
                <td class="tbl-name" title="${region.name}">${region.name}</td>
                <td class="tbl-coord">${region.north?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${region.south?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${region.east?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${region.west?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${dim}</td>
                <td class="tbl-actions">
                    <button class="tbl-btn edit" onclick="goToEdit(${originalIndex})" title="Open in Edit view">✏ Edit</button>
                    <button class="tbl-btn" onclick="selectCoordinate(${originalIndex});switchView('map')" title="Fly to on map">📍</button>
                </td>
            `;
            tr.onclick = (e) => {
                if (e.target.tagName === 'BUTTON') return;
                window.selectCoordinate?.(originalIndex);
                tbody.querySelectorAll('tr:not(.tbl-group-header)').forEach(r => r.classList.remove('selected'));
                tr.classList.add('selected');
            };
            tbody.appendChild(tr);
        });
    });
};

// ---------------------------------------------------------------------------
// toggleBboxLayerVisibility
// ---------------------------------------------------------------------------

// Module-scoped visibility state (mirrors the old closure var in app.js)
let _bboxLayersVisible = true;

/**
 * Toggle visibility of the preloaded-region and edit-marker Leaflet layers.
 */
window.toggleBboxLayerVisibility = function toggleBboxLayerVisibility() {
    _bboxLayersVisible = !_bboxLayersVisible;
    const btn             = document.getElementById('bboxVisToggleBtn');
    const map             = window.getMap?.();
    const preloadedLayer  = window.getPreloadedLayer?.();
    const editMarkersLayer = window.getEditMarkersLayer?.();

    if (_bboxLayersVisible) {
        if (preloadedLayer  && map) preloadedLayer.addTo(map);
        if (editMarkersLayer && map) editMarkersLayer.addTo(map);
        if (btn) { btn.textContent = '👁'; btn.classList.remove('hidden-state'); btn.title = 'Hide region boxes on map'; }
    } else {
        if (preloadedLayer  && map) preloadedLayer.remove();
        if (editMarkersLayer && map) editMarkersLayer.remove();
        if (btn) { btn.textContent = '🙈'; btn.classList.add('hidden-state'); btn.title = 'Show region boxes on map'; }
    }
};

// ---------------------------------------------------------------------------
// toggleStatusPanel
// ---------------------------------------------------------------------------

/**
 * Toggle the small status/info panel on the right edge of the visualisation area.
 */
window.toggleStatusPanel = function toggleStatusPanel() {
    const panel = document.getElementById('statusPanel');
    const btn   = document.getElementById('statusToggleBtn');
    if (!panel) return;
    const collapsed = panel.classList.toggle('collapsed');
    panel.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
    if (btn) btn.textContent = collapsed ? '◀' : '▶';
};

// ---------------------------------------------------------------------------
// loadSelectedRegion
// ---------------------------------------------------------------------------

/**
 * Apply the currently selected region's bbox to the map.
 */
window.loadSelectedRegion = function loadSelectedRegion() {
    const selectedRegion = window.appState.selectedRegion;
    if (!selectedRegion) {
        window.showToast?.('Please select a region first.', 'warning');
        return;
    }
    window.showToast?.(`Region "${selectedRegion.name}" loaded!`, 'success');
};

// ---------------------------------------------------------------------------
// saveCurrentRegion
// ---------------------------------------------------------------------------

/**
 * Save the current bounding box as a new named region via POST /api/regions.
 * @returns {Promise<void>}
 */
window.saveCurrentRegion = async function saveCurrentRegion() {
    const boundingBox = window.getBoundingBox?.();
    if (!boundingBox) {
        window.showToast?.('Please draw a bounding box first!', 'warning');
        return;
    }

    const regionName = document.getElementById('regionName').value.trim();
    if (!regionName) {
        window.showToast?.('Please enter a name for the region!', 'warning');
        return;
    }

    const bounds          = boundingBox;
    const regionLabelInput = document.getElementById('regionLabel');
    const regionData      = {
        name:        regionName,
        label:       (regionLabelInput?.value || '').trim() || undefined,
        north:       bounds.getNorth(),
        south:       bounds.getSouth(),
        east:        bounds.getEast(),
        west:        bounds.getWest(),
        description: `Custom region: ${regionName}`,
        parameters:  {
            dim:            parseInt(document.getElementById('paramDim').value),
            depth_scale:    window.appState.demParams.depthScale,
            water_scale:    window.appState.demParams.waterScale,
            height:         window.appState.demParams.height,
            base:           window.appState.demParams.base,
            subtract_water: window.appState.demParams.subtractWater
        }
    };

    try {
        const { data: result, error } = await window.api.regions.create(regionData);

        if (!error) {
            window.showToast?.(`Region "${regionName}" saved successfully!`, 'success');
            window.loadCoordinates?.();
            document.getElementById('regionName').value = '';
            if (regionLabelInput) regionLabelInput.value = '';
        } else {
            window.showToast?.('Error saving region: ' + (result?.error || result?.detail || error), 'error');
        }
    } catch (err) {
        console.error('Error:', err);
        window.showToast?.('Failed to save region', 'error');
    }
};

// ---------------------------------------------------------------------------
// submitBoundingBox
// ---------------------------------------------------------------------------

/**
 * Submit the current bounding box: switches to Edit view and triggers DEM load.
 */
window.submitBoundingBox = function submitBoundingBox() {
    const boundingBox = window.getBoundingBox?.();
    if (!boundingBox) {
        window.showToast?.('Please draw a bounding box first!', 'warning');
        return;
    }
    window.switchView?.('dem');
    window.loadDEM?.();
};

// ---------------------------------------------------------------------------
// setupDemSubtabs
// ---------------------------------------------------------------------------

/**
 * Wire click listeners on the DEM strip sub-tab buttons.
 * Also handles the settings panel collapse/expand toggle.
 */
window.setupDemSubtabs = function setupDemSubtabs() {
    document.querySelectorAll('#demStrip [data-subtab]').forEach(btn => {
        btn.addEventListener('click', () => { if (!btn.disabled) window.switchDemSubtab?.(btn.dataset.subtab); });
    });

    /**
     * Collapse or expand the right settings panel and restore the terrain canvas.
     */
    function toggleSettingsPanel() {
        const wrapper = document.getElementById('demRightPanel');
        if (!wrapper) return;
        const collapsed = wrapper.classList.toggle('settings-collapsed');
        const stripBtn  = document.getElementById('settingsStripBtn');
        if (stripBtn) stripBtn.classList.toggle('active', !collapsed);
        document.getElementById('layersContainer')?.classList.remove('hidden');
        document.getElementById('citiesPanel')?.classList.add('hidden');
        document.getElementById('mergePanel')?.classList.add('hidden');
        document.getElementById('compareInlineContainer')?.classList.add('hidden');
        document.getElementById('combinedContainer')?.classList.add('hidden');
        document.getElementById('demControlsInner')?.classList.remove('hidden');
        window.events?.emit(window.EV?.STACKED_UPDATE);
    }

    const settingsBtn        = document.getElementById('settingsStripBtn');
    if (settingsBtn)        settingsBtn.addEventListener('click', toggleSettingsPanel);
    const settingsExtBtn     = document.getElementById('settingsExternalBtn');
    if (settingsExtBtn)     settingsExtBtn.addEventListener('click', toggleSettingsPanel);
    const settingsCollapsedTab = document.getElementById('settingsCollapsedTab');
    if (settingsCollapsedTab) settingsCollapsedTab.addEventListener('click', toggleSettingsPanel);
};

// ---------------------------------------------------------------------------
// switchDemSubtab
// ---------------------------------------------------------------------------

/**
 * Switch the active DEM sub-tab, showing/hiding the appropriate container.
 * @param {'dem'|'water'|'landcover'|'combined'|'satellite'|'cities'|'merge'|'compare'} subtab
 */
window.switchDemSubtab = function switchDemSubtab(subtab) {
    // For merge the right panel IS the content — expand it if collapsed
    if (subtab === 'merge') {
        const rightPanel = document.getElementById('demRightPanel');
        rightPanel?.classList.remove('settings-collapsed');
    }

    // Update active state on strip buttons
    document.querySelectorAll('#demStrip [data-subtab]').forEach(t => {
        t.classList.toggle('active', t.dataset.subtab === subtab);
    });

    // Hide all containers, restore settings form
    document.getElementById('layersContainer')?.classList.add('hidden');
    document.getElementById('compareInlineContainer')?.classList.add('hidden');
    document.getElementById('combinedContainer')?.classList.add('hidden');
    document.getElementById('mergePanel')?.classList.add('hidden');
    document.getElementById('demControlsInner')?.classList.remove('hidden');

    // Close JSON editor if open
    const jsonViewToggleBtn = document.getElementById('jsonViewToggleBtn');
    if (jsonViewToggleBtn?.classList.contains('active')) jsonViewToggleBtn.click();

    // Show selected container
    switch (subtab) {
        case 'dem':
            document.getElementById('layersContainer')?.classList.remove('hidden');
            window.events?.emit(window.EV?.STACKED_UPDATE);
            break;
        case 'layers':
            document.getElementById('layersContainer')?.classList.remove('hidden');
            window.events?.emit(window.EV?.STACKED_UPDATE);
            break;
        case 'combined':
            document.getElementById('combinedContainer')?.classList.remove('hidden');
            break;
        case 'compare':
            document.getElementById('compareInlineContainer')?.classList.remove('hidden');
            window.updateCompareCanvases?.();
            break;
        case 'merge':
            document.getElementById('mergePanel')?.classList.remove('hidden');
            document.getElementById('demControlsInner')?.classList.add('hidden');
            window._refreshPipelinePanel?.();
            break;
        default:
            // Default: show layers stack
            document.getElementById('layersContainer')?.classList.remove('hidden');
            window.events?.emit(window.EV?.STACKED_UPDATE);
            break;
    }
};

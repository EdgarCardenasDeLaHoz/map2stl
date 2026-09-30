// ============================================================
// VIEW MANAGEMENT — modules/ui/view-management.js
// Handles top-level tab switching, sidebar state machine,
// sidebar table rendering, bbox layer visibility, status panel,
// region load/save/submit/delete, and DEM sub-tab wiring.
//
// All functions exposed on window.*
// Closure vars accessed via window.appState.* or window.get*()/set*() getters.
// ============================================================

'use strict';

// ---------------------------------------------------------------------------
// switchView
// ---------------------------------------------------------------------------

// View name -> container id. 'globe' has no header tab (the globe is normally
// shown as an overlay by the floating Globe button); every lookup below is
// null-safe so a view without a tab or container cannot throw.
const VIEW_CONTAINERS = {
    map: 'mapContainer',
    globe: 'globeContainer',
    dem: 'demContainer',
    model: 'modelContainer',
    regions: 'regionsContainer',
    compare: 'compareContainer',
};

/**
 * Re-measure the Leaflet map after its container changed size or visibility.
 */
function _invalidateMapSize() {
    window.getMap?.()?.invalidateSize?.();
}

/**
 * Switch the main view to the specified tab.
 * Hides all containers then shows the selected one.
 * @param {'map'|'globe'|'dem'|'model'|'regions'|'compare'} view
 */
window.switchView = function switchView(view) {
    const containers = Object.fromEntries(
        Object.entries(VIEW_CONTAINERS).map(([name, id]) => [name, document.getElementById(id)]));
    const modelContainer = containers.model;
    const newRegionSection = document.getElementById('newRegionSection');

    // Restore sidebar visibility (may have been hidden in model view)
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.style.display = '';

    // Hide all
    Object.values(containers).forEach(el => el?.classList.add('hidden'));
    if (modelContainer) modelContainer.style.display = 'none';

    // Show/hide new region section (only visible in 2D Map view)
    if (newRegionSection) {
        newRegionSection.style.display = view === 'map' ? 'block' : 'none';
    }

    // Remove active from tabs, then mark the selected one (if it has a tab)
    document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
    document.querySelector(`[data-view="${view}"]`)?.classList.add('active');
    document.body.classList.toggle('dem-edit-mode', view === 'dem');

    containers[view]?.classList.remove('hidden');

    if (view === 'map') {
        // Force Leaflet to recalculate size after container becomes visible.
        // Two passes are used because panel transitions can lag one frame.
        requestAnimationFrame(_invalidateMapSize);
        setTimeout(() => {
            _invalidateMapSize();
            _syncBboxLayerVisibility();
        }, 120);
    } else if (view === 'globe') {
        window.initGlobe?.();
    } else if (view === 'dem') {
        // Re-bind DEM subtab handlers in case Vue components mounted after initial setup.
        window.setupDemSubtabs?.();
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
        // NOTE: Do NOT auto-load layers here. Layer loading is triggered by goToEdit()
        // or explicit user action. Auto-loading here causes duplicate requests when
        // goToEdit() calls switchView() and then loadDEM() itself.
    } else if (view === 'model') {
        if (modelContainer) modelContainer.style.display = 'flex';
        // Auto-collapse sidebar so the 3D viewport gets full width
        if (sidebar) sidebar.style.display = 'none';
    } else if (view === 'regions') {
        if (containers.regions) window.populateRegionsTable?.();
    } else if (view === 'compare') {
        if (containers.compare) window.initCompareMode?.();
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
    const listView = document.getElementById('sidebarListView');
    const tableView = document.getElementById('sidebarTableView');
    const editView = document.getElementById('sidebarEditView');
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
    const q = (filter || document.getElementById('sidebarTableSearch')?.value || '').toLowerCase();
    const coordinatesData = window.getCoordinatesData?.() || [];
    const list = q ? coordinatesData.filter(r => r.name.toLowerCase().includes(q)) : coordinatesData;
    const groups = window.groupRegionsByContinent?.(list) || [];
    const selectedRegion = window.appState.selectedRegion;

    groups.forEach(({ continent, regions: groupRegions }) => {
        // Group header row
        const headerTr = document.createElement('tr');
        headerTr.className = 'tbl-group-header';
        headerTr.innerHTML = `<td colspan="7" class="tbl-group-label">${continent} <span class="tbl-group-count">${groupRegions.length}</span></td>`;
        let groupCollapsed = false;
        headerTr.onclick = () => {
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
            let originalIndex = coordinatesData.indexOf(region);
            if (originalIndex < 0) {
                // Fallback for non-identical object refs: match full bbox first,
                // then fall back to name-only as a last resort.
                originalIndex = coordinatesData.findIndex(r =>
                    r.name === region.name
                    && Number(r.north) === Number(region.north)
                    && Number(r.south) === Number(region.south)
                    && Number(r.east) === Number(region.east)
                    && Number(r.west) === Number(region.west)
                );
            }
            if (originalIndex < 0) {
                originalIndex = coordinatesData.findIndex(r => r.name === region.name);
            }
            if (originalIndex < 0) return;
            const tr = document.createElement('tr');
            if (selectedRegion && selectedRegion.name === region.name) tr.classList.add('selected');
            tr.dataset.label = region.label || '';
            tr.dataset.category = (region.label && region.label.trim()) ? region.label.trim() : continent;
            const safeName = window.escapeHtml ? window.escapeHtml(region.name) : region.name;
            tr.innerHTML = `
                <td class="tbl-name" title="${safeName}">${safeName}</td>
                <td class="tbl-coord">${region.north?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${region.south?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${region.east?.toFixed(2) ?? ''}</td>
                <td class="tbl-coord">${region.west?.toFixed(2) ?? ''}</td>
                <td class="tbl-actions">
                    <button class="tbl-btn edit" onclick="goToEdit(${originalIndex})" title="Open in Edit view">✏ Edit</button>
                    <button class="tbl-btn danger" data-action="delete" title="Delete region">🗑</button>
                </td>
            `;
            tr.querySelector('[data-action="delete"]')
                ?.addEventListener('click', () => { void window.deleteRegion?.(originalIndex); });
            tr.onclick = (e) => {
                if (e.target.tagName === 'BUTTON') return;
                window.selectCoordinate?.(originalIndex);
                tbody.querySelectorAll('tr:not(.tbl-group-header)').forEach(r => r.classList.remove('selected'));
                tr.classList.add('selected');
            };
            tbody.appendChild(tr);
        });
    });
    // Re-apply label filter if any active (set by Vue RegionListTable component)
    window._applyRegionLabelFilter?.();
};

// ---------------------------------------------------------------------------
// toggleBboxLayerVisibility
// ---------------------------------------------------------------------------

// Module-scoped visibility state (mirrors the old closure var in app.js)
let _bboxLayersVisible = true;

/**
 * Add or remove the saved-region rectangles and their edit markers on the
 * Leaflet map to match `_bboxLayersVisible`.
 */
function _syncBboxLayerVisibility() {
    const map = window.getMap?.();
    if (!map) return;
    [window.getPreloadedLayer?.(), window.getEditMarkersLayer?.()].forEach(layer => {
        if (!layer) return;
        const onMap = map.hasLayer(layer);
        if (_bboxLayersVisible && !onMap) layer.addTo(map);
        else if (!_bboxLayersVisible && onMap) map.removeLayer(layer);
    });
}

/**
 * Toggle visibility of the preloaded-region and edit-marker Leaflet layers.
 */
window.toggleBboxLayerVisibility = function toggleBboxLayerVisibility() {
    _bboxLayersVisible = !_bboxLayersVisible;
    _syncBboxLayerVisibility();
    const btn = document.getElementById('bboxVisToggleBtn');
    if (!btn) return;
    if (_bboxLayersVisible) {
        btn.textContent = '👁'; btn.classList.remove('hidden-state'); btn.title = 'Hide region boxes on map';
    } else {
        btn.textContent = '🙈'; btn.classList.add('hidden-state'); btn.title = 'Show region boxes on map';
    }
};

// ---------------------------------------------------------------------------
// deleteRegion
// ---------------------------------------------------------------------------

/**
 * Delete a saved region (DELETE /api/regions/{name}) after a confirm prompt,
 * then reload the region list. Clears the selection if it was the deleted one.
 * @param {number} index - Index into getCoordinatesData()
 * @returns {Promise<boolean>} true when the region was deleted
 */
window.deleteRegion = async function deleteRegion(index) {
    const region = (window.getCoordinatesData?.() || [])[index];
    if (!region) return false;
    if (!confirm(`Delete region "${region.name}"? Its saved settings are deleted too.`)) return false;

    const { error } = await window.api.regions.delete(region.name);
    if (error) {
        window.showToast?.(`Failed to delete region: ${error}`, 'error');
        return false;
    }
    if (window.appState.selectedRegion?.name === region.name) {
        window.setSelectedRegion?.(null);
        window.appState.selectedRegion = null;
    }
    window.showToast?.(`Region "${region.name}" deleted`, 'success');
    await window.loadCoordinates?.();
    return true;
};

// ---------------------------------------------------------------------------
// toggleStatusPanel
// ---------------------------------------------------------------------------

/**
 * Toggle the small status/info panel on the right edge of the visualisation area.
 */
window.toggleStatusPanel = function toggleStatusPanel() {
    const panel = document.getElementById('statusPanel');
    const btn = document.getElementById('statusToggleBtn');
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
/**
 * Show or hide the "Save drawn region" form under the region list. Shown when a
 * new box is drawn (focus on the name), hidden after a save or when an existing
 * region is picked, so the list has the sidebar's height the rest of the time.
 * @param {boolean} show
 */
window.showNewRegionForm = function showNewRegionForm(show) {
    const section = document.getElementById('newRegionSection');
    if (!section) return;
    section.hidden = !show;
    if (show) document.getElementById('regionName')?.focus();
};
window.events?.on(window.EV?.REGION_SELECTED, () => window.showNewRegionForm(false));

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

    const bounds = typeof boundingBox.getBounds === 'function'
        ? boundingBox.getBounds()
        : boundingBox;
    const regionLabelInput = document.getElementById('regionLabel');
    const regionData = {
        name: regionName,
        label: (regionLabelInput?.value || '').trim() || undefined,
        north: bounds.getNorth(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        west: bounds.getWest(),
        description: `Custom region: ${regionName}`,
        parameters: {
            dim: parseInt(document.getElementById('paramDim').value),
            depth_scale: window.appState.demParams.depthScale,
            water_scale: window.appState.demParams.waterScale,
            height: window.appState.demParams.height,
            base: window.appState.demParams.base,
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
            window.showNewRegionForm(false);
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
    const rightPanel = document.getElementById('demRightPanel');
    // Delegate subtab clicks so late-mounted Vue children still work.
    if (rightPanel && rightPanel.dataset.subtabBound !== '1') {
        rightPanel.addEventListener('click', (event) => {
            const btn = event.target.closest?.('[data-subtab]');
            if (!btn || btn.disabled) return;
            window.switchDemSubtab?.(btn.dataset.subtab);
        });
        rightPanel.dataset.subtabBound = '1';
    }

    /**
     * Collapse or expand the right settings panel and restore the terrain canvas.
     */
    function toggleSettingsPanel(forceCollapsed = null) {
        const wrapper = document.getElementById('demRightPanel');
        if (!wrapper) return;
        const collapsed = (typeof forceCollapsed === 'boolean')
            ? forceCollapsed
            : !wrapper.classList.contains('settings-collapsed');
        wrapper.classList.toggle('settings-collapsed', collapsed);

        // A dragged, restored, or auto-clamped panel carries an inline width,
        // and an inline declaration outranks the collapsed rule's width:0, so
        // the panel would stay open as an empty gap with its contents hidden.
        // Stash the width on collapse and put it back on expand. The resize
        // handle goes with it; a 5px col-resize sliver against a closed panel
        // is dead weight.
        if (collapsed) {
            if (wrapper.style.width) wrapper.dataset.expandedWidth = wrapper.style.width;
            wrapper.style.width = '';
        } else if (wrapper.dataset.expandedWidth) {
            wrapper.style.width = wrapper.dataset.expandedWidth;
        }
        document.getElementById('settingsPanelResizeHandle')
            ?.classList.toggle('hidden', collapsed);

        const stripBtn = document.getElementById('settingsStripBtn');
        if (stripBtn) stripBtn.classList.toggle('active', !collapsed);
        document.getElementById('layersContainer')?.classList.remove('hidden');
        document.getElementById('citiesPanel')?.classList.add('hidden');
        document.getElementById('compareInlineContainer')?.classList.add('hidden');
        document.getElementById('combinedContainer')?.classList.add('hidden');
        document.getElementById('demControlsInner')?.classList.remove('hidden');
        window.events?.emit(window.EV?.STACKED_UPDATE);
        window.emitStackUpdate?.();
        _invalidateMapSize();
        window.dispatchEvent(new Event('resize'));
        requestAnimationFrame(() => window._ensureDemViewportSpace?.());
    }

    window.toggleDemSettingsPanel = toggleSettingsPanel;

    // setupDemSubtabs re-runs on every entry into the Edit view so that Vue
    // children mounted after the first pass still get wired. addEventListener
    // does not deduplicate a fresh closure, so binding here unguarded stacked
    // one handler per entry, and with an even number of them a single click
    // toggled the panel shut and open again: the reopen tab looked dead on a
    // fresh session and alive after a layer load, purely on parity. Guard each
    // element with a dataset flag, and give every button whose intent is fixed
    // an explicit boolean so it stays correct even if one ever slips through.
    const bindSettingsToggle = (id, handler) => {
        const el = document.getElementById(id);
        if (!el || el.dataset.settingsToggleBound === '1') return;
        el.addEventListener('click', handler);
        el.dataset.settingsToggleBound = '1';
    };

    // The bbox bar hover-reveals; clicking its handle pins it open so that a
    // user reading coordinates does not lose them to a stray mouse move.
    bindSettingsToggle('demInfoHandle', () => {
        const bar = document.getElementById('demInfoBar');
        if (!bar) return;
        const pinned = bar.classList.toggle('dem-info-pinned');
        document.getElementById('demInfoHandle')
            ?.setAttribute('aria-expanded', String(pinned));
    });

    bindSettingsToggle('settingsStripBtn', () => toggleSettingsPanel());
    bindSettingsToggle('settingsExternalBtn', () => toggleSettingsPanel());
    bindSettingsToggle('settingsHideBtn', () => toggleSettingsPanel(true));
    bindSettingsToggle('settingsCollapsedTab', () => toggleSettingsPanel(false));

    // Keep DEM viewport usable when side panels consume too much width.
    window._ensureDemViewportSpace = function _ensureDemViewportSpace() {
        const demContainer = document.getElementById('demContainer');
        const center = document.querySelector('.dem-image-section');
        if (!demContainer || !center) return;
        if (demContainer.classList.contains('hidden')) return;

        const minCenter = window.innerWidth <= 1200 ? 180 : 280;
        const centerW = center.getBoundingClientRect().width;
        if (centerW >= minCenter) return;

        // First recovery step: collapse the city buildings table if it is open.
        const cityCollapsed = window.isCityBuildingsPanelCollapsed?.() ?? true;
        if (!cityCollapsed && typeof window.toggleCityBuildingsPanel === 'function') {
            window.toggleCityBuildingsPanel();
        }

        const centerW2 = center.getBoundingClientRect().width;
        if (centerW2 >= minCenter) return;

        // Second recovery step: clamp settings width so center gets breathing room.
        const right = document.getElementById('demRightPanel');
        if (!right || right.classList.contains('settings-collapsed')) return;

        const cityCollapsedTabW = document.getElementById('cityTableCollapsedTab')?.getBoundingClientRect().width || 0;
        const settingsHandleW = document.getElementById('settingsPanelResizeHandle')?.getBoundingClientRect().width || 0;
        const targetMax = Math.max(220, demContainer.clientWidth - minCenter - cityCollapsedTabW - settingsHandleW);
        const currentW = right.getBoundingClientRect().width;
        right.style.width = `${Math.max(220, Math.min(currentW, targetMax))}px`;

        if (center.getBoundingClientRect().width < (minCenter - 20)) {
            window.toggleDemSettingsPanel?.(true);
        }
    };

    if (!window.appState._demViewportGuardBound) {
        window.addEventListener('resize', () => window._ensureDemViewportSpace?.());
        window.appState._demViewportGuardBound = true;
    }

    // Recovery guard: if all render panes are hidden, force a safe default subtab.
    requestAnimationFrame(() => {
        const layersHidden = document.getElementById('layersContainer')?.classList.contains('hidden') ?? true;
        const compareHidden = document.getElementById('compareInlineContainer')?.classList.contains('hidden') ?? true;
        const combinedHidden = document.getElementById('combinedContainer')?.classList.contains('hidden') ?? true;
        if (layersHidden && compareHidden && combinedHidden) {
            window.switchDemSubtab?.(window.appState?.activeDemSubtab || 'layers');
        }
        window._ensureDemViewportSpace?.();
    });
};

// ---------------------------------------------------------------------------
// switchDemSubtab
// ---------------------------------------------------------------------------

/**
 * Switch the active DEM sub-tab, showing/hiding the appropriate container.
 * @param {'dem'|'water'|'landcover'|'combined'|'satellite'|'cities'|'compare'} subtab
 */
window.switchDemSubtab = function switchDemSubtab(subtab) {
    window.appState.activeDemSubtab = subtab;

    // Update active state on strip buttons
    document.querySelectorAll('#demRightPanel [data-subtab]').forEach(t => {
        t.classList.toggle('active', t.dataset.subtab === subtab);
    });

    // Hide all containers, restore settings form
    document.getElementById('layersContainer')?.classList.add('hidden');
    document.getElementById('compareInlineContainer')?.classList.add('hidden');
    document.getElementById('combinedContainer')?.classList.add('hidden');
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
        default:
            // Default: show layers stack
            document.getElementById('layersContainer')?.classList.remove('hidden');
            window.events?.emit(window.EV?.STACKED_UPDATE);
            break;
    }

    requestAnimationFrame(() => window._ensureDemViewportSpace?.());
};

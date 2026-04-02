/**
 * modules/bbox-panel.js
 *
 * Exposed on window:
 *   setBboxInputValues, initBboxMiniMap, syncBboxMiniMap, toggleBboxMiniMap,
 *   setupGridToggle,
 *   populateRegionsPanelTable, closeRegionsPanel, toggleContinentVisibility
 *
 * Depends on:
 *   window.appState.selectedRegion, window.appState.currentDemBbox,
 *   window.appState.lastDemData, window.appState.layerStatus
 *   window.getBoundingBox?.(), window.getMap?.(),
 *   window.getCoordinatesData?.(), window.getWaterOpacity?.(),
 *   window.getSidebarState?.(), window.getPreloadedLayer?.()
 *   window.CONTINENT_HIDDEN
 *   window.drawGridlinesOverlay?.(), window.loadDEM?.(), window.loadWaterMask?.(),
 *   window.loadSatelliteImage?.() (alias loadSatelliteImage in app.js closure),
 *   window.clearLayerCache?.(), window.updateLayerStatusIndicators?.(),
 *   window.groupRegionsByContinent?.(), window.selectCoordinate?.(),
 *   window.goToEdit?.()
 */

// ─── Mini-map state ──────────────────────────────────────────────────────────
let _bboxMiniMapInstance = null;
let _bboxMiniRect = null;
let _bboxMiniMapInited = false;
let _bboxReloadTimeout = null;
let _bboxMiniDragging = false;

// ─── setBboxInputValues ───────────────────────────────────────────────────────

/**
 * Fill the N/S/E/W coordinate input fields with values rounded to 5 decimals.
 * @param {number} n - North latitude
 * @param {number} s - South latitude
 * @param {number} e - East longitude
 * @param {number} w - West longitude
 */
window.setBboxInputValues = function setBboxInputValues(n, s, e, w) {
    const decimals = 5;
    const bboxN = document.getElementById('bboxNorth');
    const bboxS = document.getElementById('bboxSouth');
    const bboxE = document.getElementById('bboxEast');
    const bboxW = document.getElementById('bboxWest');
    if (bboxN) bboxN.value = parseFloat(n).toFixed(decimals);
    if (bboxS) bboxS.value = parseFloat(s).toFixed(decimals);
    if (bboxE) bboxE.value = parseFloat(e).toFixed(decimals);
    if (bboxW) bboxW.value = parseFloat(w).toFixed(decimals);
};

// ─── initBboxMiniMap ──────────────────────────────────────────────────────────

/**
 * Initialise the inline bbox mini-map Leaflet instance.
 * Draws a draggable rectangle for the current bbox and listens for drag events.
 * Guards against double-initialisation with `_bboxMiniMapInited`.
 */
window.initBboxMiniMap = function initBboxMiniMap() {
    if (_bboxMiniMapInited) return;
    _bboxMiniMapInited = true;

    _bboxMiniMapInstance = L.map('bboxMiniMap', {
        zoomControl: true,
        attributionControl: false,
        scrollWheelZoom: true
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 18, opacity: 0.85
    }).addTo(_bboxMiniMapInstance);

    // Use current bbox or fall back to world view
    const currentDemBbox = window.appState.currentDemBbox;
    const selectedRegion  = window.appState.selectedRegion;
    const bbox = currentDemBbox || (selectedRegion ? {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west
    } : null);

    if (bbox) {
        _bboxMiniRect = L.rectangle(
            [[bbox.south, bbox.west], [bbox.north, bbox.east]],
            { color: '#ff9900', weight: 2, fillOpacity: 0.15 }
        ).addTo(_bboxMiniMapInstance);
        _bboxMiniMapInstance.fitBounds(_bboxMiniRect.getBounds(), { padding: [30, 30] });
        _bboxMiniRect.editing.enable();
    } else {
        _bboxMiniMapInstance.setView([20, 0], 2);
    }

    // Live-update inputs while dragging a handle
    const container = _bboxMiniMapInstance.getContainer();
    container.addEventListener('mousedown', () => { _bboxMiniDragging = true; });
    document.addEventListener('mouseup', window._onBboxMiniMouseUp);
    container.addEventListener('mousemove', window._onBboxMiniMouseMove);
};

// ─── _onBboxMiniMouseMove ─────────────────────────────────────────────────────

/**
 * Live-update bbox coordinate inputs while the mini-map rectangle is being dragged.
 */
window._onBboxMiniMouseMove = function _onBboxMiniMouseMove() {
    if (!_bboxMiniDragging || !_bboxMiniRect) return;
    const b = _bboxMiniRect.getBounds();
    window.setBboxInputValues(b.getNorth(), b.getSouth(), b.getEast(), b.getWest());
};

// ─── _onBboxMiniMouseUp ───────────────────────────────────────────────────────

/**
 * On drag end: update bbox inputs and debounce a DEM reload (400 ms).
 */
window._onBboxMiniMouseUp = function _onBboxMiniMouseUp() {
    if (!_bboxMiniDragging) return;
    _bboxMiniDragging = false;
    if (!_bboxMiniRect) return;

    const b = _bboxMiniRect.getBounds();
    const n = parseFloat(b.getNorth().toFixed(5));
    const s = parseFloat(b.getSouth().toFixed(5));
    const e = parseFloat(b.getEast().toFixed(5));
    const w = parseFloat(b.getWest().toFixed(5));

    window.setBboxInputValues(n, s, e, w);

    // Debounced reload after drag ends
    clearTimeout(_bboxReloadTimeout);
    _bboxReloadTimeout = setTimeout(() => {
        let selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion) selectedRegion = {};
        selectedRegion.north = n; selectedRegion.south = s;
        selectedRegion.east = e; selectedRegion.west = w;
        window.appState.selectedRegion = selectedRegion;
        window.setSelectedRegion?.(selectedRegion);

        const currentDemBbox = { north: n, south: s, east: e, west: w };
        window.appState.currentDemBbox = currentDemBbox;

        window.clearLayerCache?.();
        window.loadDEM?.().then(() => {
            window.loadWaterMask?.();
            window.loadSatelliteImage?.();
        });
    }, 400);
};

// ─── syncBboxMiniMap ──────────────────────────────────────────────────────────

/**
 * Sync the mini-map rectangle bounds to `currentDemBbox`.
 * Called after a DEM load or region change.
 */
window.syncBboxMiniMap = function syncBboxMiniMap() {
    if (!_bboxMiniMapInited || !_bboxMiniMapInstance) return;
    const currentDemBbox = window.appState.currentDemBbox;
    const selectedRegion  = window.appState.selectedRegion;
    const bbox = currentDemBbox || (selectedRegion ? {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west
    } : null);
    if (!bbox) return;

    if (_bboxMiniRect) {
        _bboxMiniRect.editing.disable();
        _bboxMiniRect.setBounds([[bbox.south, bbox.west], [bbox.north, bbox.east]]);
        _bboxMiniRect.editing.enable();
    } else {
        _bboxMiniRect = L.rectangle(
            [[bbox.south, bbox.west], [bbox.north, bbox.east]],
            { color: '#ff9900', weight: 2, fillOpacity: 0.15 }
        ).addTo(_bboxMiniMapInstance);
        _bboxMiniRect.editing.enable();
    }
    _bboxMiniMapInstance.fitBounds(_bboxMiniRect.getBounds(), { padding: [30, 30] });
};

// ─── toggleBboxMiniMap ────────────────────────────────────────────────────────

/**
 * Toggle the inline bbox mini-map panel open or closed.
 * Initialises the Leaflet map on first open.
 */
window.toggleBboxMiniMap = function toggleBboxMiniMap() {
    const container = document.getElementById('bboxMiniMap');
    const btn = document.getElementById('editBboxOnMapBtn');
    if (!container) return;

    const opening = container.classList.contains('hidden');
    container.classList.toggle('hidden');
    if (btn) btn.classList.toggle('mini-map-open', opening);

    if (opening) {
        // Wait one frame for the div to become visible before initialising Leaflet
        requestAnimationFrame(() => {
            if (!_bboxMiniMapInited) {
                window.initBboxMiniMap();
            } else {
                window.syncBboxMiniMap();
                _bboxMiniMapInstance.invalidateSize();
            }
        });
    }
};

// ─── setupGridToggle ──────────────────────────────────────────────────────────

/**
 * Wire the `#showGridlines` checkbox and `#gridlineCount` select to redraw
 * gridline overlays on the DEM and stacked layer canvases.
 */
window.setupGridToggle = function setupGridToggle() {
    const showGridlines = document.getElementById('showGridlines');
    const gridlineCount = document.getElementById('gridlineCount');

    const redrawAllGridlines = () => {
        window.drawGridlinesOverlay?.('demImage');
        window.drawGridlinesOverlay?.('inlineLayersCanvas');
    };

    if (showGridlines) {
        showGridlines.addEventListener('change', redrawAllGridlines);
    }

    if (gridlineCount) {
        gridlineCount.addEventListener('change', redrawAllGridlines);
    }

    // Redraw gridlines on window resize
    window.addEventListener('resize', () => {
        if (window.appState.currentDemBbox) {
            requestAnimationFrame(redrawAllGridlines);
        }
    });
};

// ─── populateRegionsPanelTable ────────────────────────────────────────────────

/**
 * Render the floating regions panel list, grouped by continent.
 * Supports search filtering via `#regionsPanelSearch`.
 */
window.populateRegionsPanelTable = function populateRegionsPanelTable() {
    const container = document.getElementById('regionsPanelList');
    const searchInput = document.getElementById('regionsPanelSearch');
    const searchTerm = searchInput ? searchInput.value.toLowerCase() : '';

    const coordinatesData = window.getCoordinatesData?.() ?? [];
    const selectedRegion  = window.appState.selectedRegion;

    if (!container || !coordinatesData) return;
    container.innerHTML = '';

    if (coordinatesData.length === 0) {
        container.innerHTML = '<div style="padding:16px;color:#666;font-size:12px;text-align:center;">No regions yet.<br>Draw a bounding box on the map to create one.</div>';
        return;
    }

    const filtered = searchTerm
        ? coordinatesData.filter(r => r.name.toLowerCase().includes(searchTerm))
        : coordinatesData;

    const groups = window.groupRegionsByContinent?.(filtered) ?? [];

    groups.forEach(({ continent, regions: groupRegions }) => {
        const isHidden = window.CONTINENT_HIDDEN.has(continent);

        const groupEl = document.createElement('div');

        const header = document.createElement('div');
        header.className = 'continent-header';
        header.innerHTML = `
            <span class="continent-toggle">▾</span>
            <span class="continent-name">${continent}</span>
            <span class="continent-count">${groupRegions.length}</span>
            <span class="continent-eye${isHidden ? ' hidden-continent' : ''}" title="Show/hide on map"
                  onclick="event.stopPropagation(); toggleContinentVisibility('${continent}', this)">👁</span>
        `;
        header.addEventListener('click', () => {
            header.classList.toggle('collapsed');
            body.classList.toggle('collapsed');
        });

        const body = document.createElement('div');
        body.className = 'continent-body';

        groupRegions.forEach(region => {
            const originalIndex = coordinatesData.findIndex(r => r.name === region.name);
            const row = document.createElement('div');
            row.className = 'panel-region-row';
            if (selectedRegion && selectedRegion.name === region.name) row.classList.add('selected');
            row.innerHTML = `
                <span class="panel-region-name" title="${region.name}">${region.name}</span>
                <span class="panel-region-edit" onclick="event.stopPropagation(); window.goToEdit?.(${originalIndex}); window.closeRegionsPanel?.();">✏️ Edit</span>
            `;
            row.addEventListener('click', () => {
                window.selectCoordinate?.(originalIndex);
                // Highlight in panel
                container.querySelectorAll('.panel-region-row').forEach(r => r.classList.remove('selected'));
                row.classList.add('selected');
            });
            body.appendChild(row);
        });

        groupEl.appendChild(header);
        groupEl.appendChild(body);
        container.appendChild(groupEl);
    });
};

// ─── closeRegionsPanel ────────────────────────────────────────────────────────

/**
 * Close the floating regions panel and deactivate the toggle button.
 */
window.closeRegionsPanel = function closeRegionsPanel() {
    document.getElementById('regionsPanel')?.classList.add('hidden');
    document.getElementById('floatingRegionsToggle')?.classList.remove('active');
};

// ─── toggleContinentVisibility ────────────────────────────────────────────────

/**
 * Toggle visibility of a continent group inside the floating regions panel.
 * Updates `window.CONTINENT_HIDDEN` and re-renders the panel.
 * @param {string} continent - Continent name key
 * @param {HTMLElement} eyeEl - The eye icon element to update visually
 */
window.toggleContinentVisibility = function toggleContinentVisibility(continent, eyeEl) {
    if (window.CONTINENT_HIDDEN.has(continent)) {
        window.CONTINENT_HIDDEN.delete(continent);
        eyeEl.classList.remove('hidden-continent');
    } else {
        window.CONTINENT_HIDDEN.add(continent);
        eyeEl.classList.add('hidden-continent');
    }
    // Toggle map rectangles for regions in this continent
    const preloadedLayer = window.getPreloadedLayer?.();
    if (preloadedLayer) {
        preloadedLayer.eachLayer(layer => {
            if (layer._continentName === continent) {
                if (window.CONTINENT_HIDDEN.has(continent)) {
                    layer.setStyle({ opacity: 0, fillOpacity: 0 });
                } else {
                    layer.setStyle({ opacity: 1, fillOpacity: 0.15 });
                }
            }
        });
    }
};

/**
 * modules/regions.js
 * ==================
 * Load, render, select, save, and delete geographic regions.
 *
 * Public API (exposed on window):
 *   window.loadCoordinates()       → Promise<void>
 *   window.selectCoordinate(index) → Promise<void>
 *   window.goToEdit(index)
 *
 * Dependencies (resolved at call-time via window):
 *   window.api.regions.list()
 *   window.getCoordinatesData()     / window.setCoordinatesData(data)
 *   window.setSelectedRegion(r)
 *   window.getPreloadedLayer()
 *   window.getEditMarkersLayer()
 *   window.getMap()
 *   window.getGlobeScene()
 *   window.BBOX_COLORS
 *   window.getSidebarState()        / window.setSidebarState(s)
 *   window.updateRegionParamsTable(region)
 *   window.clearCityOverlay?.()
 *   window.haversineDiagKm(n, s, e, w)
 *   window.loadDEM?.()
 *   window.loadWaterMask?.()
 *   window.loadSatelliteImage?.()   (or bare call — global in app.js)
 *   window.loadAndApplyRegionSettings?.(name)
 *   window.loadWaterMask?.()
 *   window._updateCitiesLoadButton?.(region)
 *   window.appState._updateWorkflowStepper?.()
 *   renderCoordinatesList()         (global from region-ui.js)
 *   detectContinent(lat, lon)       (global from region-ui.js)
 *   clearLayerCache()               (global function in app.js)
 *   clearLayerDisplays()            (global function in app.js)
 *   switchView(view)                (global function in app.js)
 */


// === Constants ===
const DEM_DIM_SMALL_KM = 10;
const DEM_DIM_MEDIUM_KM = 50;
const DEM_DIM_LARGE_KM = 200;
const DEM_DIM_SMALL = 600;
const DEM_DIM_MEDIUM = 500;
const DEM_DIM_LARGE = 300;
const DEM_DIM_DEFAULT = 600;
const REGION_BBOX_OPACITY_DEFAULT = 0.15;
const REGION_BBOX_HIDDEN_STORAGE_KEY = 'strm2stl_hiddenRegionBboxes';
const REGION_BBOX_OPACITY_STORAGE_KEY = 'strm2stl_regionBboxOpacity';

// Breakpoint tables used by selectCoordinate to auto-set DEM dim (and water/ESA output resolution)
const AUTO_SCALE = {
    dim: [
        { maxKm: DEM_DIM_SMALL_KM, dim: DEM_DIM_SMALL },
        { maxKm: DEM_DIM_MEDIUM_KM, dim: DEM_DIM_MEDIUM },
        { maxKm: DEM_DIM_LARGE_KM, dim: DEM_DIM_LARGE },
        { maxKm: Infinity, dim: DEM_DIM_DEFAULT },
    ],
};

let _hiddenRegionBboxes = new Set();
let _regionBboxOpacity = REGION_BBOX_OPACITY_DEFAULT;
let _regionLayerIndex = new Map();

function _loadRegionBboxPrefs() {
    try {
        const hiddenRaw = localStorage.getItem(REGION_BBOX_HIDDEN_STORAGE_KEY);
        if (hiddenRaw) {
            const parsed = JSON.parse(hiddenRaw);
            if (Array.isArray(parsed)) _hiddenRegionBboxes = new Set(parsed.filter(Boolean));
        }
    } catch (_) {}

    try {
        const opacityRaw = parseFloat(localStorage.getItem(REGION_BBOX_OPACITY_STORAGE_KEY) || '');
        if (Number.isFinite(opacityRaw)) {
            _regionBboxOpacity = Math.max(0, Math.min(1, opacityRaw));
        }
    } catch (_) {}
}

function _saveHiddenRegionBboxes() {
    try {
        localStorage.setItem(REGION_BBOX_HIDDEN_STORAGE_KEY, JSON.stringify([..._hiddenRegionBboxes]));
    } catch (_) {}
}

function _saveRegionBboxOpacity() {
    try {
        localStorage.setItem(REGION_BBOX_OPACITY_STORAGE_KEY, String(_regionBboxOpacity));
    } catch (_) {}
}

function _applyRegionBboxStyle(entry) {
    if (!entry?.rect) return;
    entry.rect.setStyle({
        color: entry.color,
        fillColor: entry.color,
        opacity: 1,
        fillOpacity: _regionBboxOpacity,
    });
}

function _shouldShowRegionBbox(name) {
    return (window.getBboxLayersVisible?.() ?? true) && !_hiddenRegionBboxes.has(name);
}

function syncRegionBboxVisibility() {
    const preloadedLayer = window.getPreloadedLayer?.();
    const editMarkersLayer = window.getEditMarkersLayer?.();
    if (!preloadedLayer) return;

    preloadedLayer.clearLayers();
    if (editMarkersLayer) editMarkersLayer.clearLayers();

    for (const [name, entry] of _regionLayerIndex.entries()) {
        _applyRegionBboxStyle(entry);
        if (!_shouldShowRegionBbox(name)) continue;
        preloadedLayer.addLayer(entry.rect);
        if (editMarkersLayer && entry.editMarker) editMarkersLayer.addLayer(entry.editMarker);
    }
}

window.syncRegionBboxVisibility = syncRegionBboxVisibility;
window.isRegionBboxHidden = function isRegionBboxHidden(name) {
    return _hiddenRegionBboxes.has(name);
};
window.setRegionBboxHidden = function setRegionBboxHidden(name, hidden) {
    if (!name) return;
    if (hidden) _hiddenRegionBboxes.add(name);
    else _hiddenRegionBboxes.delete(name);
    _saveHiddenRegionBboxes();
    syncRegionBboxVisibility();
    window.renderSidebarTable?.();
    window.populateRegionsTable?.();
};
window.toggleRegionBboxHidden = function toggleRegionBboxHidden(name) {
    window.setRegionBboxHidden?.(name, !_hiddenRegionBboxes.has(name));
};
window.getRegionBboxOpacity = function getRegionBboxOpacity() {
    return _regionBboxOpacity;
};
window.setRegionBboxOpacity = function setRegionBboxOpacity(opacity) {
    const next = Math.max(0, Math.min(1, Number(opacity)));
    _regionBboxOpacity = Number.isFinite(next) ? next : _regionBboxOpacity;
    _saveRegionBboxOpacity();
    for (const entry of _regionLayerIndex.values()) _applyRegionBboxStyle(entry);
};

_loadRegionBboxPrefs();

// ── loadCoordinates ─────────────────────────────────────────────────────────

/**
 * Fetch all saved regions from `/api/regions` and populate the UI.
 * Draws colour-coded rectangles on the map and updates the coordinates list.
 * @returns {Promise<void>}
 */
async function loadCoordinates() {
    const list = document.getElementById('coordinatesList');

    if (!list) {
        console.error('coordinatesList element not found!');
        return;
    }

    list.innerHTML = '<div class="loading"><span class="spinner"></span>Loading coordinates...</div>';

    try {
        const { data, error } = await window.api.regions.list();
        if (error) throw new Error(error);

        window.setCoordinatesData?.(data.regions || []);

        // Populate coordinates list with enhanced styling
        renderCoordinatesList();

        const coordinatesData = window.getCoordinatesData?.() || [];

        const preloadedLayer = window.getPreloadedLayer?.();
        const editMarkersLayer = window.getEditMarkersLayer?.();

        // Draw rectangles on map - sorted by size (largest first) so smaller ones are clickable
        if (preloadedLayer) {
            preloadedLayer.clearLayers();
            if (editMarkersLayer) editMarkersLayer.clearLayers();
            _regionLayerIndex = new Map();

            // Calculate area for each region and sort by size descending
            const sortedRegions = coordinatesData.map((region, originalIndex) => {
                const width = Math.abs(region.east - region.west);
                const height = Math.abs(region.north - region.south);
                const area = width * height;
                return { region, originalIndex, area };
            }).sort((a, b) => b.area - a.area); // Largest first

            const BBOX_COLORS = window.BBOX_COLORS || [];

            sortedRegions.forEach(({ region, originalIndex }) => {
                const bounds = [[region.south, region.west],
                [region.north, region.east]];
                const colorObj = BBOX_COLORS[originalIndex % BBOX_COLORS.length];
                const rect = L.rectangle(bounds, {
                    color: colorObj.color,
                    weight: 2,
                    fill: true,
                    fillColor: colorObj.color,
                    fillOpacity: _regionBboxOpacity
                });
                rect._regionName = region.name;

                // Tag rectangle with continent for visibility toggling
                const cLat = (region.north + region.south) / 2;
                const cLon = (region.east + region.west) / 2;
                rect._continentName = detectContinent(cLat, cLon);

                // Click selects the region (stays on Explore)
                rect.on('click', () => window.selectCoordinate(originalIndex));

                // Edit button pinned at the top-right corner of each bbox (hidden until hover)
                const editIcon = L.divIcon({
                    html: `<div class="bbox-edit-icon">✏️ Edit</div>`,
                    className: 'bbox-edit-marker',
                    iconSize: [56, 22],
                    iconAnchor: [56, 0]   // top-right corner of the icon aligns with [north, east]
                });
                const editMarker = L.marker([region.north, region.east], {
                    icon: editIcon,
                    interactive: true,
                    keyboard: false,
                    zIndexOffset: 500
                });
                editMarker.on('click', () => window.goToEdit(originalIndex));
                editMarker._regionBounds = L.latLngBounds(bounds[0], bounds[1]);
                editMarker._regionName = region.name;

                // Hover: show tooltip + reveal Edit button
                rect.on('mouseover', function (e) {
                    const rawLabel = (region.label || '').trim();
                    const label = (rawLabel && rawLabel.toLowerCase() !== 'coorlist')
                        ? rawLabel
                        : region.name;
                    rect.unbindTooltip();
                    rect.bindTooltip(label, { sticky: false, direction: 'top', offset: [0, -4] });
                    rect.openTooltip(e.latlng);
                    editMarker.getElement()?.querySelector('.bbox-edit-icon')?.classList.add('visible');
                });
                rect.on('mouseout', function () {
                    // Delay hiding so the user can move to the edit button
                    setTimeout(() => {
                        const icon = editMarker.getElement()?.querySelector('.bbox-edit-icon');
                        if (icon && !icon.matches(':hover')) icon.classList.remove('visible');
                    }, 300);
                });
                // Keep edit button visible while hovering it directly
                editMarker.on('mouseover', function () {
                    editMarker.getElement()?.querySelector('.bbox-edit-icon')?.classList.add('visible');
                });
                editMarker.on('mouseout', function () {
                    editMarker.getElement()?.querySelector('.bbox-edit-icon')?.classList.remove('visible');
                });

                _regionLayerIndex.set(region.name, {
                    rect,
                    editMarker,
                    color: colorObj.color,
                });
            });

            syncRegionBboxVisibility();
        }

        // Add markers to globe
        updateGlobeMarkers();

    } catch (error) {
        console.error('Error loading coordinates:', error);
        list.innerHTML = '<div class="loading" style="color:red;">Error loading coordinates: ' + error.message + '</div>';
    }
}
window.loadCoordinates = loadCoordinates;

// ── updateGlobeMarkers ──────────────────────────────────────────────────────

/**
 * Refresh all coordinate markers on the Three.js globe from `coordinatesData`.
 */
function updateGlobeMarkers() {
    const globeScene = window.getGlobeScene?.();

    // Check if globeScene exists and has the markers group
    if (!globeScene || !globeScene.children || globeScene.children.length < 3) {
        return; // Globe not initialized yet
    }

    // Clear existing markers
    const markersGroup = globeScene.children[2];
    if (!markersGroup || !markersGroup.children) {
        return;
    }

    while (markersGroup.children.length > 0) {
        markersGroup.remove(markersGroup.children[0]);
    }

    const coordinatesData = window.getCoordinatesData?.() || [];
    const BBOX_COLORS = window.BBOX_COLORS || [];

    // Add markers for each region
    coordinatesData.forEach((region, i) => {
        const centerLat = (region.north + region.south) / 2;
        const centerLng = (region.east + region.west) / 2;
        const cssColor = BBOX_COLORS[i % BBOX_COLORS.length]?.color;
        const color = cssColor ? parseInt(cssColor.slice(1), 16) : 0xff0000;

        const marker = createGlobeMarker(centerLat, centerLng, color);
        markersGroup.add(marker);
    });
}

// ── createGlobeMarker ───────────────────────────────────────────────────────

/**
 * Create a Three.js sprite marker positioned on the globe surface.
 * @param {number} lat   - Latitude in degrees
 * @param {number} lng   - Longitude in degrees
 * @param {number} color - Three.js integer color (default red 0xff0000)
 * @returns {THREE.Mesh} The created marker mesh
 */
function createGlobeMarker(lat, lng, color = 0xff0000) {
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lng + 180) * (Math.PI / 180);

    const geometry = new THREE.SphereGeometry(0.05, 8, 8);
    const material = new THREE.MeshBasicMaterial({ color });
    const marker = new THREE.Mesh(geometry, material);

    marker.position.x = 5 * Math.sin(phi) * Math.cos(theta);
    marker.position.y = 5 * Math.cos(phi);
    marker.position.z = 5 * Math.sin(phi) * Math.sin(theta);

    return marker;
}

// ── selectCoordinate ────────────────────────────────────────────────────────

/**
 * Select a region by index: sets `selectedRegion`, flies the map to it,
 * loads and applies region settings, and updates all list/table UIs.
 *
 * Side effects (in order):
 *  1. Sets `window.appState.selectedRegion` and calls `window.setSelectedRegion`.
 *  2. Calls `clearLayerCache()` and `clearLayerDisplays()` to flush stale layer data.
 *  3. Calls `clearCityOverlay()` if available so city auto-load triggers for the new region.
 *  4. Awaits `window.loadAndApplyRegionSettings(name)` — writes demParams, dim, sat_scale etc.
 *     Falls back to `selectedRegion.parameters` if no saved settings exist.
 *  5. Auto-sets `#waterResolution`, `#landCoverResolution`, and `#paramDim` via AUTO_SCALE
 *     breakpoints; never lowers a user's explicit dim choice.
 *  6. Calls `window.updateRegionParamsTable` if the sidebar is expanded.
 *  7. Calls `map.fitBounds` (wrapped in try/catch — fails silently if map is hidden).
 *  8. If the Edit (DEM) view is visible and `skipAutoLoad` is false: fires `loadDEM`
 *     → then `loadWaterMask`, `loadSatelliteImage`, `loadEsaLandCover`, `loadSatelliteRGBImage`,
 *     `loadHydrology` in parallel, and `loadCityRaster` / `loadCityData` if diagKm ≤ 10.
 *     goToEdit() passes `{ skipAutoLoad: true }` to prevent double loading.
 *  9. Calls `window.appState._updateWorkflowStepper`.
 *
 * @param {number} index - Index into `coordinatesData` (from `window.getCoordinatesData()`)
 * @param {{ skipAutoLoad?: boolean }} [opts] - `skipAutoLoad`: skip the DEM-view auto-reload.
 *   Pass `true` when the caller will load layers itself (e.g. goToEdit).
 * @returns {Promise<void>}
 */
async function selectCoordinate(index, { skipAutoLoad = false } = {}) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    const selectedRegion = coordinatesData[index];
    window.setSelectedRegion?.(selectedRegion);
    window.appState.selectedRegion = selectedRegion;

    // Populate bbox inputs immediately so they're never empty after selection
    window.setBboxInputValues?.(selectedRegion.north, selectedRegion.south,
        selectedRegion.east, selectedRegion.west);

    // CRITICAL: Abort any in-flight layer loads from the previous region before
    // clearing state, to prevent stale responses from rendering into the new region's
    // view after clearLayerDisplays() has already shown placeholder content.
    window.cancelDemLoads?.();
    window.cancelWaterLoads?.();
    window.cancelHydroLoad?.();
    window.cancelCityRasterLoad?.();
    window.cancelCityLoad?.();

    // Clear cached layer data and clear visual displays when region changes
    // Prevents stale water mask / land cover / DEM from showing with new region
    clearLayerCache();
    clearLayerDisplays();
    // Clear city overlay so auto-load triggers for the new region
    if (typeof clearCityOverlay === 'function') clearCityOverlay();

    // Highlight in sidebar list
    document.querySelectorAll('.coordinate-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.regionName === selectedRegion.name);
    });

    // Load parameters: try saved region_settings.json first, fall back to
    // legacy coordinates.json parameters, then hard-coded defaults.
    // Await so settings are applied before DEM is loaded below.
    const hasSaved = await window.loadAndApplyRegionSettings?.(selectedRegion.name);
    if (!hasSaved && selectedRegion.parameters) {
        const rp = selectedRegion.parameters;
        const _setEl = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
        const _setChk = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.checked = Boolean(v); };
        _setEl('paramDim', rp.dim || 600);
        _setEl('paramDepthScale', rp.depth_scale ?? 0.5);
        _setEl('paramWaterScale', rp.water_scale ?? 0.05);
        _setChk('paramSubtractWater', rp.subtract_water !== false);
        _setEl('waterResolution', rp.dim ?? 600);
        _setEl('exportModelHeight', rp.height ?? 10);
        _setEl('exportBaseHeight', rp.base ?? 2);
        if (window.appState?.demParams) {
            window.appState.demParams.depthScale = rp.depth_scale ?? 0.5;
            window.appState.demParams.waterScale = rp.water_scale ?? 0.05;
            window.appState.demParams.subtractWater = rp.subtract_water !== false;
            window.appState.demParams.dim = rp.dim ?? 600;
            window.appState.demParams.height = rp.height ?? 10;
            window.appState.demParams.base = rp.base ?? 2;
        }
    }

    // Populate label editor with selected region's current label
    const labelEditEl = document.getElementById('regionLabelEdit');
    if (labelEditEl) labelEditEl.value = selectedRegion.label || '';

    // Refresh datalist of existing labels from all regions
    const datalist = document.getElementById('regionLabelsList');
    if (datalist) {
        const labels = [...new Set(coordinatesData.map(r => r.label).filter(Boolean))].sort();
        datalist.innerHTML = labels.map(l => `<option value="${l}">`).join('');
    }

    // Show/hide Cities tab based on region diagonal
    window._updateCitiesLoadButton?.(selectedRegion);

    // Auto-select water/land cover resolution (sat_scale) and DEM dim based on region diagonal.
    // ESA WorldCover is 10m native; use that for city scale to avoid quality loss.
    // Also raise paramDim for small regions so the water/sat alignment target is high enough.
    {
        const diagKm = window.haversineDiagKm?.(
            selectedRegion.north, selectedRegion.south,
            selectedRegion.east, selectedRegion.west
        );
        // dim: output resolution in pixels — applies to DEM, water mask, and ESA land cover.
        // Use the same breakpoint table to auto-set waterResolution and esaResolution
        // (both now hold pixel counts, not m/px).
        const autoDim = AUTO_SCALE.dim.find(t => diagKm <= t.maxKm)?.dim ?? 600;
        const waterResEl = document.getElementById('waterResolution');
        if (waterResEl) waterResEl.value = String(autoDim);
        const esaResEl = document.getElementById('esaResolution');
        if (esaResEl) esaResEl.value = String(autoDim);

        // DEM dim: only raise if lower than the auto value and no saved settings loaded.
        const dimEl = document.getElementById('paramDim');
        if (dimEl) {
            const currentDim = parseInt(dimEl.value) || 600;
            // Raise dim if it is lower than what the region size warrants.
            // Never lower the user's explicit choice.
            // Skip if saved settings were loaded — respect the persisted dim.
            if (!hasSaved && autoDim > currentDim) dimEl.value = String(autoDim);
        }

        // Sync layer-view per-layer resolution selects from the persisted/auto-set values.
        // These are independent controls in LayerViewSection for one-shot loads at a
        // specific resolution, initialized to match the persisted settings on region change.
        // If the value doesn't match an option exactly, snap to the nearest valid option.
        const _snapSelectToNearest = (selectEl, val) => {
            if (!selectEl) return;
            const options = Array.from(selectEl.options).map(o => parseInt(o.value));
            if (!options.length) return;
            const num = parseInt(val) || options[0];
            const nearest = options.reduce((prev, curr) =>
                Math.abs(curr - num) < Math.abs(prev - num) ? curr : prev
            );
            selectEl.value = String(nearest);
        };
    }

    // Update region params table if sidebar is expanded
    if (document.getElementById('sidebar').classList.contains('expanded')) {
        window.updateRegionParamsTable?.(selectedRegion);
    }

    // Fly to region on map (if map is visible)
    const map = window.getMap?.();
    if (map) {
        const bounds = [[selectedRegion.south, selectedRegion.west],
        [selectedRegion.north, selectedRegion.east]];
        try { map.fitBounds(bounds, { padding: [20, 20] }); } catch (e) { }
    }

    window.appState._updateWorkflowStepper?.();

    // Emit on the event bus so any module can react to region selection
    // without needing a direct function reference.
    window.events?.emit(window.EV?.REGION_SELECTED, index, selectedRegion);

    // If the DEM view is already open and the caller is not about to load layers
    // itself (e.g. goToEdit passes skipAutoLoad:true), reload all layers now so
    // the user sees the new region without having to press Load DEM manually.
    if (!skipAutoLoad) {
        const demContainer = document.getElementById('demContainer');
        if (demContainer && !demContainer.classList.contains('hidden')) {
            const r = selectedRegion;
            window.loadDEM?.().then(() => {
                const tasks = [
                    window.loadWaterMask?.(),
                    window.loadSatelliteImage?.(),
                    window.loadEsaLandCover?.(),
                    window.loadSatelliteRGBImage?.(),
                    window.loadHydrology?.(),
                ];
                const diagKm = (r && window.haversineDiagKm)
                    ? window.haversineDiagKm(r.north, r.south, r.east, r.west)
                    : 0;
                if (diagKm > 0 && diagKm <= 10) {
                    const bldgToggle = document.getElementById('layerBuildingsToggle');
                    if (bldgToggle && !bldgToggle.checked) bldgToggle.checked = true;
                    if (window.loadCityRaster) tasks.push(window.loadCityRaster());
                    else if (window.loadCityData) tasks.push(window.loadCityData());
                }
                return Promise.all(tasks);
            }).catch(err => {
                console.error('Auto-reload error on region switch:', err);
            });
        }
    }
}
window.selectCoordinate = selectCoordinate;

// ── goToEdit ────────────────────────────────────────────────────────────────

/**
 * Select a region and immediately navigate to the Edit (DEM) tab,
 * triggering a full layer load (DEM + water mask + satellite).
 * @param {number} index - Index into `coordinatesData`
 */
async function goToEdit(index) {
    await window.selectCoordinate(index, { skipAutoLoad: true });
    switchView('dem');

    // Populate the compact sidebar edit panel
    const coordinatesData = window.getCoordinatesData?.() || [];
    const region = coordinatesData[index];
    if (region) {
        const nameEl = document.getElementById('sbRegionName');
        if (nameEl) nameEl.textContent = region.name;
        const dec = 5;
        const sbN = document.getElementById('sbNorth');
        const sbS = document.getElementById('sbSouth');
        const sbE = document.getElementById('sbEast');
        const sbW = document.getElementById('sbWest');
        if (sbN) sbN.value = parseFloat(region.north).toFixed(dec);
        if (sbS) sbS.value = parseFloat(region.south).toFixed(dec);
        if (sbE) sbE.value = parseFloat(region.east).toFixed(dec);
        if (sbW) sbW.value = parseFloat(region.west).toFixed(dec);
    }

    // Keep the region list visible so the user can switch to another region
    document.getElementById('sidebarListView')?.classList.remove('hidden');
    document.getElementById('sidebarTableView')?.classList.add('hidden');
    document.getElementById('sidebarEditView')?.classList.add('hidden');

    // Ensure sidebar is in normal mode (visible, not expanded/hidden)
    if (window.getSidebarState?.() !== 'normal') {
        const sidebar = document.getElementById('sidebar');
        const openBtn = document.getElementById('openSidebarBtn');
        const toggleBtn = document.getElementById('sidebarToggleBtn');
        sidebar?.classList.remove('collapsed', 'expanded');
        openBtn?.classList.add('hidden');
        const icon = toggleBtn?.querySelector('.state-icon');
        const label = toggleBtn?.querySelector('.state-label');
        if (icon) icon.textContent = '⇔';
        if (label) label.textContent = 'Expand';
        window.setSidebarState?.('normal');
    }

    window.loadDEM?.().then(() => {
        // Load secondary layers in parallel
        const tasks = [
            window.loadWaterMask?.(),
            window.loadSatelliteImage?.(),
            window.loadEsaLandCover?.(),
            window.loadSatelliteRGBImage?.(),
            window.loadHydrology?.(),
        ];
        const r = region || window.appState?.selectedRegion;
        const diagKm = (r && window.haversineDiagKm)
            ? window.haversineDiagKm(r.north, r.south, r.east, r.west)
            : 0;
        if (diagKm > 0 && diagKm <= 10) {
            // Ensure buildings toggle is on so loadCityData fetches polygon layers
            const bldgToggle = document.getElementById('layerBuildingsToggle');
            if (bldgToggle && !bldgToggle.checked) bldgToggle.checked = true;
            if (window.loadCityRaster) tasks.push(window.loadCityRaster());
            else if (window.loadCityData) tasks.push(window.loadCityData());
        }
        return Promise.all(tasks);
    }).catch(err => {
        console.error('Error loading layers in goToEdit:', err);
        window.showToast?.('Error loading layers: ' + (err?.message || err), 'error');
    });
}
window.goToEdit = goToEdit;

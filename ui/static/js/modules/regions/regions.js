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

        const preloadedLayer   = window.getPreloadedLayer?.();
        const editMarkersLayer = window.getEditMarkersLayer?.();

        // Draw rectangles on map - sorted by size (largest first) so smaller ones are clickable
        if (preloadedLayer) {
            preloadedLayer.clearLayers();
            if (editMarkersLayer) editMarkersLayer.clearLayers();

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
                    fillOpacity: 0.15
                });

                // Tag rectangle with continent for visibility toggling
                const cLat = (region.north + region.south) / 2;
                const cLon = (region.east + region.west) / 2;
                rect._continentName = detectContinent(cLat, cLon);

                // Click selects the region (stays on Explore)
                rect.on('click', () => window.selectCoordinate(originalIndex));

                // Edit button pinned at the top-right corner of each bbox (hidden until hover)
                const editIcon = L.divIcon({
                    html: `<div class="bbox-edit-icon" onclick="goToEdit(${originalIndex})">✏️ Edit</div>`,
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
                editMarker._regionBounds = L.latLngBounds(bounds[0], bounds[1]);
                if (editMarkersLayer) editMarkersLayer.addLayer(editMarker);

                // Hover: show tooltip + reveal Edit button
                rect.on('mouseover', function(e) {
                    const label = region.label || region.name;
                    rect.unbindTooltip();
                    rect.bindTooltip(label, { sticky: false, direction: 'top', offset: [0, -4] });
                    rect.openTooltip(e.latlng);
                    editMarker.getElement()?.querySelector('.bbox-edit-icon')?.classList.add('visible');
                });
                rect.on('mouseout', function() {
                    // Delay hiding so the user can move to the edit button
                    setTimeout(() => {
                        const icon = editMarker.getElement()?.querySelector('.bbox-edit-icon');
                        if (icon && !icon.matches(':hover')) icon.classList.remove('visible');
                    }, 300);
                });
                // Keep edit button visible while hovering it directly
                editMarker.on('mouseover', function() {
                    editMarker.getElement()?.querySelector('.bbox-edit-icon')?.classList.add('visible');
                });
                editMarker.on('mouseout', function() {
                    editMarker.getElement()?.querySelector('.bbox-edit-icon')?.classList.remove('visible');
                });

                preloadedLayer.addLayer(rect);
            });
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

    // Add markers for each region
    coordinatesData.forEach(region => {
        const centerLat = (region.north + region.south) / 2;
        const centerLng = (region.east + region.west) / 2;

        const marker = createGlobeMarker(centerLat, centerLng);
        markersGroup.add(marker);
    });
}

// ── createGlobeMarker ───────────────────────────────────────────────────────

/**
 * Create a Three.js sprite marker positioned on the globe surface.
 * @param {number} lat - Latitude in degrees
 * @param {number} lng - Longitude in degrees
 * @returns {THREE.Mesh} The created marker mesh
 */
function createGlobeMarker(lat, lng) {
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lng + 180) * (Math.PI / 180);

    const geometry = new THREE.SphereGeometry(0.05, 8, 8);
    const material = new THREE.MeshBasicMaterial({ color: 0xff0000 });
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
 * @param {number} index - Index into `coordinatesData`
 * @returns {Promise<void>}
 */
async function selectCoordinate(index) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    const selectedRegion = coordinatesData[index];
    window.setSelectedRegion?.(selectedRegion);
    window.appState.selectedRegion = selectedRegion;

    // CRITICAL: Clear cached layer data and clear visual displays when region changes
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
        document.getElementById('paramDim').value = rp.dim || 100;
        if (window.appState?.demParams) {
            window.appState.demParams.depthScale    = rp.depth_scale    ?? 0.5;
            window.appState.demParams.waterScale    = rp.water_scale    ?? 0.05;
            window.appState.demParams.subtractWater = rp.subtract_water !== false;
            window.appState.demParams.satScale      = rp.sat_scale      ?? 500;
            window.appState.demParams.height        = rp.height         ?? 10;
            window.appState.demParams.base          = rp.base           ?? 2;
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
        // sat_scale: ESA fetch resolution (m/px) — lower = finer, more pixels
        const autoSatScale = diagKm > 500 ? 1000
                           : diagKm > 100 ? 500
                           : diagKm > 30  ? 100
                           : diagKm > 10  ? 30
                           :                10;   // city scale → ESA native 10m
        const waterResEl = document.getElementById('waterResolution');
        const landResEl  = document.getElementById('landCoverResolution');
        if (waterResEl) waterResEl.value = String(autoSatScale);
        if (landResEl)  landResEl.value  = String(autoSatScale);

        // dim: DEM output pixel count — raise for city scale so the server-side
        // alignment target (target_width/height) is large enough to hold ESA 10m data.
        // Only auto-set if no settings were loaded from the DB (first-time or reset).
        const dimEl = document.getElementById('paramDim');
        if (dimEl) {
            const currentDim = parseInt(dimEl.value) || 200;
            const autoDim = diagKm > 200 ? 200
                          : diagKm > 50  ? 300
                          : diagKm > 10  ? 500
                          :                600;  // city → 600px holds ESA 10m detail
            // Raise dim if it is lower than what the region size warrants.
            // Never lower the user's explicit choice.
            if (autoDim > currentDim) dimEl.value = String(autoDim);
        }
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
        try { map.fitBounds(bounds, { padding: [20, 20] }); } catch(e) {}
    }

    // If Edit view is currently active, load all layers for the new region
    const demContainer = document.getElementById('demContainer');
    if (demContainer && !demContainer.classList.contains('hidden')) {
        window.loadDEM?.().then(() => {
            // Load secondary layers in parallel (no dependency between them)
            const tasks = [
                window.loadWaterMask?.(),
                window.loadSatelliteImage?.(),
            ];
            const diagKm = window.appState?.haversineDiagKm?.();
            if (diagKm && diagKm <= 15 && window.loadCityData) {
                tasks.push(window.loadCityData());
            }
            return Promise.all(tasks);
        });
    }

    window.appState._updateWorkflowStepper?.();
}
window.selectCoordinate = selectCoordinate;

// ── goToEdit ────────────────────────────────────────────────────────────────

/**
 * Select a region and immediately navigate to the Edit (DEM) tab,
 * triggering a full layer load (DEM + water mask + satellite).
 * @param {number} index - Index into `coordinatesData`
 */
function goToEdit(index) {
    window.selectCoordinate(index);
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
        const icon  = toggleBtn?.querySelector('.state-icon');
        const label = toggleBtn?.querySelector('.state-label');
        if (icon)  icon.textContent  = '⇔';
        if (label) label.textContent = 'Expand';
        window.setSidebarState?.('normal');
    }

    window.loadDEM?.().then(() => {
        // Load secondary layers in parallel
        const tasks = [
            window.loadWaterMask?.(),
            window.loadSatelliteImage?.(),
        ];
        const diagKm = window.appState?.haversineDiagKm?.();
        if (diagKm && diagKm <= 15 && window.loadCityData) {
            tasks.push(window.loadCityData());
        }
        return Promise.all(tasks);
    });
}
window.goToEdit = goToEdit;

        // ============================================================
        // FILE-TOP HELPERS (available before DOMContentLoaded)
        // ============================================================

        // ============================================================
        // GLOBAL STATE
        // All application state lives as closure variables inside
        // DOMContentLoaded (or at file-top scope for pre-init state).
        // Shared state is mirrored to window.appState (see modules/state.js).
        // ============================================================

        // Global variables
        let map;
        let globeScene, globeCamera, globeRenderer, globe;
        let drawnItems;
        let preloadedLayer;  // layer for rectangles loaded from saved coordinates
        let editMarkersLayer;  // permanent Edit buttons inside each bbox
        let boundingBox;
        let coordinatesData = [];
        let selectedRegion = null;

        // Shared state object — exposed on window so extracted modules can read/write it.
        // Updated wherever these variables change throughout this file.
        // Initialise state keys on the reactive Proxy created by modules/state.js.
        // Do NOT reassign window.appState — that would destroy the Proxy and its listeners.
        if (!window.appState?.set) window.appState = {};   // fallback if state.js not loaded
        window.appState.selectedRegion    = null;
        window.appState.currentDemBbox    = null;
        window.appState.osmCityData       = null;
        window.appState.lastDemData       = null;
        window.appState.lastWaterMaskData = null;
        window.appState.showToast         = null;
        window.appState.haversineDiagKm   = null;
        let lastDemData = null;
        let lastEsaData = null;
        let lastRawDemData = null;

        // Land cover configuration — owned by water-mask.js; exposed on window.appState.
        window.appState.landCoverConfig = {
            10: { name: 'Tree Cover', color: [0, 100, 0], elevation: 0.1 },
            20: { name: 'Shrubland', color: [255, 187, 34], elevation: 0.05 },
            30: { name: 'Grassland', color: [255, 255, 76], elevation: 0.02 },
            40: { name: 'Cropland', color: [240, 150, 255], elevation: 0.0 },
            50: { name: 'Built-up', color: [250, 0, 0], elevation: 0.15 },
            60: { name: 'Bare/Sparse', color: [180, 180, 180], elevation: 0.0 },
            70: { name: 'Snow/Ice', color: [240, 240, 240], elevation: 0.0 },
            80: { name: 'Water', color: [0, 100, 200], elevation: -0.1 },
            90: { name: 'Wetland', color: [0, 150, 160], elevation: -0.02 },
            95: { name: 'Mangroves', color: [0, 207, 117], elevation: 0.0 },
            100: { name: 'Moss/Lichen', color: [250, 230, 160], elevation: 0.0 },
            0: { name: 'No Data/Ocean', color: [0, 50, 150], elevation: -0.15 }
        };
        window.appState.landCoverConfigDefaults = JSON.parse(JSON.stringify(window.appState.landCoverConfig));

        // Track the bbox that each layer was loaded for
        let layerBboxes = {
            dem: null,
            water: null,
            landCover: null
        };

        // Layer loading status: 'empty' | 'loading' | 'loaded' | 'error'
        let layerStatus = {
            dem: 'empty',
            water: 'empty',
            landCover: 'empty'
        };

        // Mirror layerBboxes and layerStatus to window.appState (shared references; property
        // mutations are auto-visible to modules).  Full-object reassignments (clearLayerCache)
        // must re-sync window.appState manually — see clearLayerCache() below.
        window.appState.layerBboxes = layerBboxes;
        window.appState.layerStatus = layerStatus;

        // (lastAppliedPresetName moved to modules/presets.js)

        /**
         * Clear all cached layer data
         * Call this when changing regions to prevent stale data
         */
        function clearLayerCache() {
            lastDemData = null;
            window.clearLastWaterMaskData?.();
            lastEsaData = null;
            lastRawDemData = null;
            currentDemBbox = null;
            window.appState.currentDemBbox = null;
            window.appState.lastDemData = null;
            _setDemEmptyState(true);
            originalDemValues = null;  // Reset so next Apply uses new region's data
            window.appState.originalDemValues = null;
            curveDataVmin = null;      // Reset stable curve coordinate system
            window.appState.curveDataVmin = null;
            curveDataVmax = null;
            window.appState.curveDataVmax = null;

            // Reset layer tracking
            layerBboxes = { dem: null, water: null, landCover: null };
            layerStatus = { dem: 'empty', water: 'empty', landCover: 'empty' };
            window.appState.layerBboxes = layerBboxes;
            window.appState.layerStatus = layerStatus;
            window._clearCityRasterCache?.();
            window.appState.cityRasterSourceCanvas = null;

            // Update status indicators
            updateLayerStatusIndicators();
        }

        /**
         * Clear any visual layer displays from the UI (DEM, water, satellite, combined)
         * Call this when changing regions so previously-loaded images/overlays are removed.
         */
        function clearLayerDisplays() {
            const placeholders = {
                demImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view DEM</p>',
                waterMaskImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view water mask</p>',
                satelliteImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view land cover</p>',
                combinedImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view combined layers</p>'
            };

            Object.keys(placeholders).forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    // remove any attached canvases or images
                    el.innerHTML = placeholders[id];
                }
            });

            // remove any gridline overlays
            document.querySelectorAll('.dem-gridlines-overlay').forEach(n => n.remove());
        }

        // updateLayerStatusIndicators — moved to modules/ui-helpers.js

        /**
         * Get current bounding box as object
         */
        function getCurrentBboxObject() {
            let bounds;
            if (boundingBox) {
                bounds = boundingBox;
            } else if (selectedRegion) {
                return {
                    north: selectedRegion.north,
                    south: selectedRegion.south,
                    east: selectedRegion.east,
                    west: selectedRegion.west
                };
            } else {
                return null;
            }

            return {
                north: bounds.getNorth(),
                south: bounds.getSouth(),
                east: bounds.getEast(),
                west: bounds.getWest()
            };
        }

        /**
         * Check if a layer's bbox matches current bbox
         */
        function isLayerCurrent(layerName) {
            const currentBbox = getCurrentBboxObject();
            const layerBbox = layerBboxes[layerName];

            if (!currentBbox || !layerBbox) return false;

            const epsilon = 0.0001;
            return Math.abs(currentBbox.north - layerBbox.north) < epsilon &&
                Math.abs(currentBbox.south - layerBbox.south) < epsilon &&
                Math.abs(currentBbox.east - layerBbox.east) < epsilon &&
                Math.abs(currentBbox.west - layerBbox.west) < epsilon;
        }

        // Color palette for different bounding boxes (distinct, easy to tell apart)
        const BBOX_COLORS = [
            { color: '#ff4444', name: 'Red' },
            { color: '#44aaff', name: 'Blue' },
            { color: '#44ff44', name: 'Green' },
            { color: '#ffaa00', name: 'Orange' },
            { color: '#ff44ff', name: 'Magenta' },
            { color: '#00ffff', name: 'Cyan' },
            { color: '#ffff44', name: 'Yellow' },
            { color: '#aa44ff', name: 'Purple' },
        ];
        let currentBboxColorIndex = 0;

        // ============================================================
        // UI UTILITIES — moved to modules/ui-helpers.js
        // showToast, toggleCollapsible, setupCoordinateSearch,
        // setLayerStatus, updateLayerStatusUI, showLoading, hideLoading
        // ============================================================

        // ============================================================
        // Cache Management — moved to modules/cache.js
        // updateCacheStatusUI, fetchServerCacheStatus, preloadAllRegions,
        // clearClientCache, clearServerCache, setupCacheManagement
        // ============================================================

        // ============================================================
        // INITIALIZATION
        // Entry point: DOMContentLoaded bootstraps the whole app.
        // ============================================================

        document.addEventListener('DOMContentLoaded', async function () {
            console.log('DOM loaded, initializing app...');

            // Check if required libraries are loaded
            if (typeof L === 'undefined') {
                console.error('Leaflet library not loaded!');
                document.getElementById('coordinatesList').innerHTML = '<div class="loading" style="color:red">Error: Leaflet library failed to load. Try refreshing or use a different browser.</div>';
                // Still try to load coordinates without map
                await loadCoordinates();
                return;
            }

            console.log('Leaflet loaded:', typeof L);
            console.log('Three.js loaded:', typeof THREE);

            try {
                initMap();
                console.log('Map initialized');
            } catch (e) {
                console.error('Error initializing map:', e);
            }

            try {
                initGlobe();
                console.log('Globe initialized');
            } catch (e) {
                console.error('Error initializing globe:', e);
            }

            await loadCoordinates();
            console.log('Coordinates loaded');

            setupEventListeners();
            setupDemSubtabs();
            window.setupWaterMaskListeners?.();
            setupGridToggle();
            setupCacheManagement();

            // Start in expanded sidebar state by default
            sidebarState = 'expanded';
            const _sidebar = document.getElementById('sidebar');
            if (_sidebar) { _sidebar.classList.remove('collapsed'); _sidebar.classList.add('expanded'); }
            const _toggleBtn = document.getElementById('sidebarToggleBtn');
            if (_toggleBtn) {
                const _icon = _toggleBtn.querySelector('.state-icon');
                const _lbl = _toggleBtn.querySelector('.state-label');
                if (_icon) _icon.textContent = '⇐';
                if (_lbl) _lbl.textContent = 'Hide';
            }
            _setSidebarViews('expanded');

            // Load available DEM sources and show API key warning if needed
            window._initDemSources?.();

            // Initialize merge panel
            window.setupMergePanel?.();


            console.log('App initialization complete');
        });

        // ============================================================
        // MAP & GLOBE
        // Leaflet 2D map initialisation, tile layers, grid overlay,
        // bounding box drawing, and Three.js globe.
        // ============================================================

        let currentTileLayer = null;

        const TILE_LAYERS = {
            'osm': {
                url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            },
            'osm-topo': {
                url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
                attribution: '&copy; OpenTopoMap contributors'
            },
            'esri-world': {
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                attribution: '&copy; Esri'
            },
            'esri-topo': {
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
                attribution: '&copy; Esri'
            },
            'carto-light': {
                url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                attribution: '&copy; CartoDB'
            },
            'carto-dark': {
                url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                attribution: '&copy; CartoDB'
            },
            'stamen-terrain': {
                url: 'https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}{r}.png',
                attribution: '&copy; Stadia Maps, Stamen Design'
            }
        };

        /**
         * Switch the active Leaflet tile layer.
         * Removes the previous tile layer and adds the new one.
         * @param {string} layerKey - Key from the `TILE_LAYERS` object (e.g. 'osm', 'esri-world')
         */
        function setTileLayer(layerKey) {
            const layerConfig = TILE_LAYERS[layerKey];
            if (!layerConfig || !map) return;

            // Remove current tile layer
            if (currentTileLayer) {
                map.removeLayer(currentTileLayer);
            }

            // Add new tile layer
            currentTileLayer = L.tileLayer(layerConfig.url, {
                attribution: layerConfig.attribution
            }).addTo(map);
        }

        // DEM overlay on map (actual elevation data rendered as colored image)
        let demOverlayLayer = null;
        let demOverlayLoading = false;

        /**
         * Toggle the DEM terrain overlay on the Leaflet map.
         * Strategy 1: uses a pre-generated global PNG if available.
         * Strategy 2: generates a DEM image for the current viewport on demand.
         * @param {boolean} show - true to enable the overlay, false to remove it
         * @returns {Promise<boolean>} Resolves to the new overlay state
         */
        async function toggleDemOverlay(show) {
            if (!map) return;

            if (show) {
                if (demOverlayLoading) return true;
                demOverlayLoading = true;

                if (demOverlayLayer) { map.removeLayer(demOverlayLayer); demOverlayLayer = null; }

                try {
                    showToast('Loading terrain overlay...', 'info');

                    // ── Strategy 1: pre-cached global DEM PNG ──────────────────
                    // Generated at server startup; always covers the full globe.
                    let usedGlobal = false;
                    try {
                        const metaResp = await fetch('/static/global_dem_meta.json');
                        if (metaResp.ok) {
                            // meta is only used to confirm the file exists; overlay is always full-globe
                            demOverlayLayer = L.imageOverlay(
                                `/static/global_dem.png?_=${Date.now()}`,
                                [[-90, -180], [90, 180]],
                                { opacity: 0.7, interactive: false }
                            ).addTo(map);
                            showToast('Terrain overlay loaded', 'success');
                            usedGlobal = true;
                        }
                    } catch (_) {}

                    // ── Strategy 2: generate on demand via preview_dem ─────────
                    if (!usedGlobal) {
                        const bounds = map.getBounds();
                        const north = bounds.getNorth(), south = bounds.getSouth();
                        const east = bounds.getEast(), west = bounds.getWest();
                        const colormap = document.getElementById('demColormap')?.value || 'terrain';
                        const { data } = await api.dem.load(
                            `north=${north}&south=${south}&east=${east}&west=${west}&dim=150&colormap=${colormap}&projection=none&subtract_water=false&depth_scale=1`
                        );
                        if (data.dem_values && data.dimensions) {
                            let demVals = data.dem_values;
                            let h = Number(data.dimensions[0]);
                            let w = Number(data.dimensions[1]);
                            if (Array.isArray(demVals) && demVals.length && Array.isArray(demVals[0])) {
                                h = demVals.length; w = demVals[0].length; demVals = demVals.flat();
                            }
                            const vmin = data.min_elevation ?? 0;
                            const vmax = data.max_elevation ?? 1;
                            const canvas = document.createElement('canvas');
                            canvas.width = w; canvas.height = h;
                            const ctx = canvas.getContext('2d');
                            const imgData = ctx.createImageData(w, h);
                            for (let i = 0; i < demVals.length; i++) {
                                let t = (demVals[i] - vmin) / ((vmax - vmin) || 1);
                                t = Math.max(0, Math.min(1, t));
                                const [r, g, b] = mapElevationToColor(t, colormap);
                                imgData.data[i*4]=Math.round(r*255); imgData.data[i*4+1]=Math.round(g*255);
                                imgData.data[i*4+2]=Math.round(b*255); imgData.data[i*4+3]=200;
                            }
                            ctx.putImageData(imgData, 0, 0);
                            // Re-project to Web Mercator so the canvas aligns correctly with map tiles
                            const ob = data.bbox;
                            const _bN = ob ? ob[3] : north, _bS = ob ? ob[1] : south;
                            const _bE = ob ? ob[2] : east, _bW = ob ? ob[0] : west;
                            const _toRad = d => d * Math.PI / 180;
                            const _mercY = l => Math.log(Math.tan(Math.PI/4 + _toRad(Math.max(-85,Math.min(85,l)))/2));
                            const _mN = _mercY(_bN), _mS = _mercY(_bS), _mRange = _mN - _mS;
                            const _latRange = _bN - _bS;
                            const projCanvas = document.createElement('canvas');
                            projCanvas.width = w; projCanvas.height = h;
                            const projCtx = projCanvas.getContext('2d');
                            const projImg = projCtx.createImageData(w, h);
                            const srcData = imgData.data;
                            for (let dstY = 0; dstY < h; dstY++) {
                                const mv = _mN - (dstY / (h - 1)) * _mRange;
                                const lat = (2 * Math.atan(Math.exp(mv)) - Math.PI / 2) * 180 / Math.PI;
                                const srcY = Math.round((_bN - lat) / _latRange * (h - 1));
                                if (srcY < 0 || srcY >= h) continue;
                                projImg.data.set(srcData.subarray(srcY * w * 4, (srcY + 1) * w * 4), dstY * w * 4);
                            }
                            projCtx.putImageData(projImg, 0, 0);
                            demOverlayLayer = L.imageOverlay(projCanvas.toDataURL('image/png'),
                                [[_bS, _bW], [_bN, _bE]],
                                { opacity: 0.7, interactive: false }
                            ).addTo(map);
                            showToast('Terrain overlay loaded', 'success');
                        }
                    }
                } catch (err) {
                    console.error('DEM overlay error:', err);
                    showToast('Failed to load terrain overlay', 'error');
                } finally {
                    demOverlayLoading = false;
                }

                // Show opacity control
                document.getElementById('terrainOpacityGroup').style.display = 'flex';
                document.getElementById('terrainOpacityLabel').style.display = '';
                return true;
            } else {
                if (demOverlayLayer) {
                    map.removeLayer(demOverlayLayer);
                    demOverlayLayer = null;
                }
                // Hide opacity control
                document.getElementById('terrainOpacityGroup').style.display = 'none';
                document.getElementById('terrainOpacityLabel').style.display = 'none';
                return false;
            }
        }

        // Fallback: terrain relief overlay (hillshade tiles)
        let terrainOverlayLayer = null;

        /**
         * Toggle the terrain overlay (delegates to toggleDemOverlay).
         * @param {boolean} show - true to show, false to hide
         */
        function toggleTerrainOverlay(show) {
            if (!map) return;
            // Always use DEM overlay from current map viewport
            toggleDemOverlay(show);
        }

        /**
         * Set opacity of the active DEM/terrain overlay layer.
         * @param {number} opacity - Opacity value 0–100 (converted to 0–1 internally)
         */
        function setTerrainOverlayOpacity(opacity) {
            if (demOverlayLayer) {
                demOverlayLayer.setOpacity(opacity / 100);
            }
            if (terrainOverlayLayer) {
                terrainOverlayLayer.setOpacity(opacity / 100);
            }
        }

        /**
         * Update the floating terrain toggle button appearance to reflect active state.
         * @param {boolean} active - true if terrain overlay is currently on
         */
        function updateFloatingTerrainButton(active) {
            const btn = document.getElementById('floatingTerrainToggle');
            if (btn) {
                if (active) {
                    btn.classList.add('active');
                    btn.title = 'DEM overlay ON (click to disable)';
                } else {
                    btn.classList.remove('active');
                    btn.title = 'Toggle DEM overlay';
                }
            }
        }

        /**
         * Initialise the Leaflet map: tile layer, draw controls, feature groups,
         * bounding box draw event, and map grid.
         */
        function initMap() {
            map = L.map('map', {
                minZoom: 2,
                maxZoom: 18,
                worldCopyJump: true
            }).setView([20, 0], 2);

            // Use tile layer system
            setTileLayer('osm');

            preloadedLayer = new L.FeatureGroup().addTo(map);
            editMarkersLayer = new L.FeatureGroup().addTo(map);
            drawnItems = new L.FeatureGroup().addTo(map);

            // Hide edit markers when their bbox is too small on screen (< 40px diagonal)
            function _updateEditMarkerVisibility() {
                if (!editMarkersLayer) return;
                editMarkersLayer.eachLayer(marker => {
                    if (!marker._regionBounds) return;
                    const ne = map.latLngToContainerPoint(marker._regionBounds.getNorthEast());
                    const sw = map.latLngToContainerPoint(marker._regionBounds.getSouthWest());
                    const pxDiag = Math.sqrt(Math.pow(ne.x - sw.x, 2) + Math.pow(ne.y - sw.y, 2));
                    const el = marker.getElement?.();
                    if (el) el.style.display = pxDiag < 40 ? 'none' : '';
                });
            }
            map.on('zoomend', _updateEditMarkerVisibility);
            map.on('moveend', _updateEditMarkerVisibility);

            // Get next color for drawn rectangles
            /**
             * Return the next colour string from `BBOX_COLORS` in round-robin order.
             * @returns {string} CSS colour string
             */
            function getNextBboxColor() {
                const colorObj = BBOX_COLORS[currentBboxColorIndex % BBOX_COLORS.length];
                currentBboxColorIndex++;
                return colorObj.color;
            }

            const drawControl = new L.Control.Draw({
                draw: {
                    rectangle: {
                        shapeOptions: {
                            color: '#ff4444',
                            weight: 3,
                            fillOpacity: 0.2
                        }
                    },
                    polygon: false,
                    circle: false,
                    marker: false,
                    polyline: false
                },
                edit: {
                    featureGroup: drawnItems
                }
            });
            map.addControl(drawControl);

            map.on(L.Draw.Event.CREATED, function (event) {
                // Don't clear - keep old boxes visible with different colors
                const layer = event.layer;
                const bboxColor = getNextBboxColor();
                layer.setStyle({
                    color: bboxColor,
                    weight: 3,
                    fillColor: bboxColor,
                    fillOpacity: 0.15
                });
                drawnItems.addLayer(layer);
                boundingBox = layer.getBounds();

                // Update the current box indicator
                updateBboxIndicator(bboxColor);
            });

            // Initialize map grid
            initMapGrid();
        }

        // Map grid layer (graticule)
        let mapGridLayer = null;
        let mapGridEnabled = false;

        /**
         * Initialise the Leaflet layer group used for the map graticule (grid).
         */
        function initMapGrid() {
            // Create a custom graticule layer
            mapGridLayer = L.layerGroup();
        }

        /**
         * Redraw the map graticule (grid) for the current viewport and zoom level.
         * Called on map moveend events when the grid is enabled.
         */
        function updateMapGrid() {
            if (!map || !mapGridLayer) return;

            mapGridLayer.clearLayers();

            if (!mapGridEnabled) return;

            const bounds = map.getBounds();
            const zoom = map.getZoom();

            // Calculate grid interval based on zoom level
            let interval;
            if (zoom <= 3) interval = 30;
            else if (zoom <= 5) interval = 10;
            else if (zoom <= 7) interval = 5;
            else if (zoom <= 9) interval = 1;
            else if (zoom <= 11) interval = 0.5;
            else if (zoom <= 13) interval = 0.1;
            else interval = 0.05;

            const west = Math.floor(bounds.getWest() / interval) * interval;
            const east = Math.ceil(bounds.getEast() / interval) * interval;
            const south = Math.floor(bounds.getSouth() / interval) * interval;
            const north = Math.ceil(bounds.getNorth() / interval) * interval;

            const gridStyle = {
                color: 'rgba(255, 255, 255, 0.4)',
                weight: 1,
                dashArray: '4, 4'
            };

            const labelStyle = {
                className: 'grid-label',
                permanent: true,
                direction: 'center'
            };

            // Draw latitude lines
            for (let lat = south; lat <= north; lat += interval) {
                const line = L.polyline([[lat, west], [lat, east]], gridStyle);
                mapGridLayer.addLayer(line);

                // Add label
                if (zoom >= 5) {
                    const label = L.marker([lat, bounds.getCenter().lng], {
                        icon: L.divIcon({
                            className: 'grid-label',
                            html: `<span style="background:rgba(0,0,0,0.6);color:#fff;padding:1px 3px;font-size:9px;border-radius:2px;">${lat.toFixed(lat % 1 === 0 ? 0 : 2)}°</span>`,
                            iconSize: [40, 12]
                        })
                    });
                    mapGridLayer.addLayer(label);
                }
            }

            // Draw longitude lines
            for (let lng = west; lng <= east; lng += interval) {
                const line = L.polyline([[south, lng], [north, lng]], gridStyle);
                mapGridLayer.addLayer(line);

                // Add label
                if (zoom >= 5) {
                    const label = L.marker([bounds.getCenter().lat, lng], {
                        icon: L.divIcon({
                            className: 'grid-label',
                            html: `<span style="background:rgba(0,0,0,0.6);color:#fff;padding:1px 3px;font-size:9px;border-radius:2px;">${lng.toFixed(lng % 1 === 0 ? 0 : 2)}°</span>`,
                            iconSize: [40, 12]
                        })
                    });
                    mapGridLayer.addLayer(label);
                }
            }
        }

        /**
         * Show or hide the map graticule overlay.
         * @param {boolean} show - true to enable the grid, false to remove it
         */
        function toggleMapGrid(show) {
            mapGridEnabled = show;

            if (show) {
                if (mapGridLayer) {
                    mapGridLayer.addTo(map);
                    updateMapGrid();

                    // Update grid on zoom/pan
                    map.on('moveend', updateMapGrid);
                }
            } else {
                if (mapGridLayer) {
                    map.removeLayer(mapGridLayer);
                    map.off('moveend', updateMapGrid);
                }
            }

            // Update button state
            const btn = document.getElementById('floatingGridToggle');
            if (btn) {
                btn.classList.toggle('active', show);
                btn.title = show ? 'Grid ON (click to hide)' : 'Toggle grid lines';
            }
        }

        /**
         * Update the bounding box colour indicator element in the UI.
         * @param {string} color - CSS colour string (e.g. '#ff4444')
         */
        function updateBboxIndicator(color) {
            const indicator = document.getElementById('bboxColorIndicator');
            if (indicator) {
                indicator.style.backgroundColor = color;
                indicator.title = `Current selection: ${color}`;
            }
        }

        /**
         * Initialise the Three.js globe: scene, camera, renderer, sphere geometry,
         * lighting, and the RAF animation loop.
         */
        function initGlobe() {
            try {
                const container = document.getElementById('globe');
                if (!container || container.clientWidth === 0 || container.clientHeight === 0) {
                    console.warn('Globe container not ready, skipping init');
                    return;
                }

                globeScene = new THREE.Scene();
                globeCamera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
                globeRenderer = new THREE.WebGLRenderer({ antialias: true });
                globeRenderer.setSize(container.clientWidth, container.clientHeight);
                container.appendChild(globeRenderer.domElement);

                // Create globe geometry
                const geometry = new THREE.SphereGeometry(5, 64, 64);
                const material = new THREE.MeshPhongMaterial({
                    color: 0x2233ff,
                    transparent: true,
                    opacity: 0.8
                });
                globe = new THREE.Mesh(geometry, material);
                globeScene.add(globe);

                // Add lighting
                const ambientLight = new THREE.AmbientLight(0x404040);
                globeScene.add(ambientLight);

                const directionalLight = new THREE.DirectionalLight(0xffffff, 0.5);
                directionalLight.position.set(1, 1, 1);
                globeScene.add(directionalLight);

                globeCamera.position.z = 10;

                // Add coordinate markers group
                globeScene.add(new THREE.Group()); // Index 2 will be for markers

                animateGlobe();
            } catch (error) {
                console.error('Error initializing globe:', error);
                // Hide globe toggle if WebGL is unavailable
                const globeToggle = document.getElementById('floatingGlobeToggle');
                if (globeToggle) {
                    globeToggle.style.display = 'none';
                    globeToggle.title = 'Globe unavailable (WebGL not supported)';
                }
            }
        }

        /**
         * RAF animation loop for the Three.js globe.
         * Rotates the globe on the Y axis each frame.
         */
        function animateGlobe() {
            requestAnimationFrame(animateGlobe);
            globe.rotation.y += 0.005;
            globeRenderer.render(globeScene, globeCamera);
        }

        // ============================================================
        // REGION MANAGEMENT
        // Load, render, select, save, and delete geographic regions.
        // ============================================================

        /**
         * Fetch all saved regions from `/api/regions` and populate the UI.
         * Draws colour-coded rectangles on the map and updates the coordinates list.
         * @returns {Promise<void>}
         */
        async function loadCoordinates() {
            console.log('loadCoordinates() called');
            const list = document.getElementById('coordinatesList');

            if (!list) {
                console.error('coordinatesList element not found!');
                return;
            }

            list.innerHTML = '<div class="loading"><span class="spinner"></span>Loading coordinates...</div>';

            try {
                const { data, error } = await api.regions.list();
                if (error) throw new Error(error);
                console.log('Got data, regions count:', data.regions?.length || 0);

                coordinatesData = data.regions || [];

                // Populate coordinates list with enhanced styling
                renderCoordinatesList();

                console.log('Populated', coordinatesData.length, 'regions');

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
                        rect.on('click', () => selectCoordinate(originalIndex));

                        // Hover thumbnail preview (P12)
                        rect.on('mouseover', function(e) {
                            const thumb = window.appState.regionThumbnails?.[region.name];
                            const label = region.label || region.name;
                            const html = thumb
                                ? `<img src="${thumb}" style="display:block;width:96px;height:60px;object-fit:cover;border-radius:3px;"><div style="text-align:center;font-size:11px;color:#ccc;margin-top:3px;max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${label}</div>`
                                : `<div style="font-size:12px;padding:3px 6px;">${label}</div>`;
                            rect.unbindTooltip();
                            rect.bindTooltip(html, { sticky: false, direction: 'top', className: 'region-thumb-tooltip', offset: [0, -4] });
                            rect.openTooltip(e.latlng);
                        });

                        // Permanent Edit button pinned at the top-right corner of each bbox
                        const editIcon = L.divIcon({
                            html: `<div class="bbox-edit-icon" onclick="goToEdit(${originalIndex})">✏️ Edit</div>`,
                            className: '',
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

        // Update globe markers
        /**
         * Refresh all coordinate markers on the Three.js globe from `coordinatesData`.
         */
        function updateGlobeMarkers() {
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

            // Add markers for each region
            coordinatesData.forEach(region => {
                const centerLat = (region.north + region.south) / 2;
                const centerLng = (region.east + region.west) / 2;

                const marker = createGlobeMarker(centerLat, centerLng);
                markersGroup.add(marker);
            });
        }

        // Create a marker on the globe
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

        // Select a coordinate — stays on Explore. Use goToEdit() to switch to Edit view.
        /**
         * Select a region by index: sets `selectedRegion`, flies the map to it,
         * loads and applies region settings, and updates all list/table UIs.
         * @param {number} index - Index into `coordinatesData`
         * @returns {Promise<void>}
         */
        async function selectCoordinate(index) {
            selectedRegion = coordinatesData[index];
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
                document.getElementById('paramDim').value = selectedRegion.parameters.dim || 100;
                document.getElementById('paramDepthScale').value = selectedRegion.parameters.depth_scale || 0.5;
                document.getElementById('paramWaterScale').value = selectedRegion.parameters.water_scale || 0.05;
                document.getElementById('paramHeight').value = selectedRegion.parameters.height || 10;
                document.getElementById('paramBase').value = selectedRegion.parameters.base || 2;
                document.getElementById('paramSubtractWater').checked = selectedRegion.parameters.subtract_water !== false;
                document.getElementById('paramSatScale').value = selectedRegion.parameters.sat_scale || 500;
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
                const diagKm = haversineDiagKm(
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
                updateRegionParamsTable(selectedRegion);
            }

            // Fly to region on map (if map is visible)
            if (map) {
                const bounds = [[selectedRegion.south, selectedRegion.west],
                [selectedRegion.north, selectedRegion.east]];
                try { map.fitBounds(bounds, { padding: [20, 20] }); } catch(e) {}
            }

            // If Edit view is currently active, load all layers for the new region
            const demContainer = document.getElementById('demContainer');
            if (demContainer && !demContainer.classList.contains('hidden')) {
                loadDEM().then(() => {
                    window.loadWaterMask?.();
                    loadSatelliteImage();
                });
            }

            _updateWorkflowStepper();
        }

        /**
         * Select a region and immediately navigate to the Edit (DEM) tab,
         * triggering a full layer load (DEM + water mask + satellite).
         * @param {number} index - Index into `coordinatesData`
         */
        function goToEdit(index) {
            selectCoordinate(index);
            switchView('dem');

            // Populate the compact sidebar edit panel
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
            if (sidebarState !== 'normal') {
                const sidebar = document.getElementById('sidebar');
                const openBtn = document.getElementById('openSidebarBtn');
                const toggleBtn = document.getElementById('sidebarToggleBtn');
                sidebar?.classList.remove('collapsed', 'expanded');
                openBtn?.classList.add('hidden');
                const icon  = toggleBtn?.querySelector('.state-icon');
                const label = toggleBtn?.querySelector('.state-label');
                if (icon)  icon.textContent  = '⇔';
                if (label) label.textContent = 'Expand';
                sidebarState = 'normal';
            }

            loadDEM().then(() => {
                window.loadWaterMask?.();
                loadSatelliteImage();
            });
        }

        // ============================================================
        // EVENT LISTENERS
        // Central event wiring for the entire application UI.
        // ============================================================

        /**
         * Wire all major UI event listeners (tabs, buttons, sliders, dropdowns).
         * Called once during DOMContentLoaded initialisation.
         */
        function setupEventListeners() {
            // Tab buttons
            document.querySelectorAll('.tab').forEach(tab => {
                tab.addEventListener('click', () => switchView(tab.dataset.view));
            });

            // Control buttons
            document.getElementById('loadRegionBtn').onclick = loadSelectedRegion;
            document.getElementById('saveRegionBtn').onclick = saveCurrentRegion;
            document.getElementById('submitBtn').onclick = submitBoundingBox;

            _setupBboxListeners();

            // 3-state sidebar toggle button
            const sidebarToggleBtn = document.getElementById('sidebarToggleBtn');
            if (sidebarToggleBtn) sidebarToggleBtn.onclick = cycleSidebarState;

            // Bbox layer visibility toggle
            const bboxVisToggleBtn = document.getElementById('bboxVisToggleBtn');
            if (bboxVisToggleBtn) bboxVisToggleBtn.onclick = toggleBboxLayerVisibility;

            // Sidebar expanded table search
            const sidebarTableSearch = document.getElementById('sidebarTableSearch');
            if (sidebarTableSearch) {
                sidebarTableSearch.addEventListener('input', () => renderSidebarTable(sidebarTableSearch.value));
            }
            // Status panel toggle
            const statusToggleBtn = document.getElementById('statusToggleBtn');
            if (statusToggleBtn) statusToggleBtn.onclick = toggleStatusPanel;

            // Apply region parameters button
            const applyParamsBtn = document.getElementById('applyParamsBtn');
            if (applyParamsBtn) applyParamsBtn.onclick = applyRegionParams;

            // Clear bounding boxes button
            const clearBboxBtn = document.getElementById('clearBboxBtn');
            if (clearBboxBtn) clearBboxBtn.onclick = clearAllBoundingBoxes;

            _setupModelExportListeners();

            _setupMapAndDemListeners();

            // Layer opacity controls
            setupOpacityControls();

            // Auto-reload on settings changes
            setupAutoReload();

            // Stacked layers view
            setupStackedLayers();

            // Setup coordinate search filter
            setupCoordinateSearch();

            // Setup regions table
            setupRegionsTable();

            // Setup keyboard shortcuts
            setupKeyboardShortcuts();

            _setupResizablePanel();

            // Initialize elevation curve editor
            initCurveEditor();

            // Initialize preset profiles
            initPresetProfiles();

            // Initialize region notes and thumbnails
            initRegionNotes();
            initRegionThumbnails();

            // Initialize stacked layers zoom/pan (once only)
            enableStackedZoomPan();

            _setupSettingsJsonToggle();
            _setupCityAndExportListeners();

            _setupSidebarEditView();

            // ── Draw tool helper ─────────────────────────────────────────────────────
            /**
             * Programmatically enable the Leaflet rectangle draw mode and show a guide toast.
             */
            function activateDrawTool() {
                if (drawControl && drawControl._toolbars?.draw) {
                    try { drawControl._toolbars.draw._modes.rectangle.handler.enable(); } catch(e) {}
                }
                const btn = document.getElementById('floatingDrawBtn');
                if (btn) btn.classList.add('drawing');
                showToast('Draw a rectangle on the map, then enter a name and click Save Region', 'info');
            }

            // ── Sidebar compact edit view (sbBackBtn / sbReloadBtn) ────────────────
            function _setupSidebarEditView() {
                document.getElementById('sbBackBtn')?.addEventListener('click', () => {
                    document.getElementById('sidebarEditView')?.classList.add('hidden');
                    _setSidebarViews(sidebarState);
                });
                document.getElementById('sbReloadBtn')?.addEventListener('click', () => {
                    const n = document.getElementById('sbNorth')?.value;
                    const s = document.getElementById('sbSouth')?.value;
                    const e = document.getElementById('sbEast')?.value;
                    const w = document.getElementById('sbWest')?.value;
                    if (n != null) setBboxInputValues(n, s, e, w);
                    const nf = parseFloat(n), sf = parseFloat(s),
                          ef = parseFloat(e), wf = parseFloat(w);
                    if (!isNaN(nf) && !isNaN(sf) && !isNaN(ef) && !isNaN(wf)) {
                        if (boundingBox) map.removeLayer(boundingBox);
                        boundingBox = L.rectangle([[sf, wf], [nf, ef]],
                            { color: '#e74c3c', weight: 2, fillOpacity: 0.05 });
                        boundingBox.addTo(map);
                    }
                    loadAllLayers();
                });
            }

            // ── DEM display, map overlays, draw tool, grid, opacity, warnings ────────
            function _setupMapAndDemListeners() {
                // Colormap change — recolor without refetching
                document.getElementById('demColormap').onchange = recolorDEM;

                // Projection change triggers client-side re-render only
                const projSelect = document.getElementById('paramProjection');
                if (projSelect) {
                    const projDescriptions = {
                        'none':        'No correction — raw lat/lon grid displayed as-is.',
                        'cosine':      'Horizontal scaling by cos(latitude). Correct east-west distances.',
                        'mercator':    'Web Mercator — vertical stretching increases towards poles.',
                        'lambert':     'Lambert Cylindrical Equal-Area — preserves area at the cost of shape.',
                        'sinusoidal':  'Sinusoidal — each row scaled by cos(lat), centred on meridian.',
                    };
                    projSelect.addEventListener('change', () => {
                        const desc = document.getElementById('projectionDescription');
                        if (desc) desc.textContent = projDescriptions[projSelect.value] || '';
                        recolorDEM();
                        const _wmd = window.appState.lastWaterMaskData;
                        if (_wmd) {
                            window.renderWaterMask?.(_wmd);
                            window.renderEsaLandCover?.(_wmd);
                        }
                    });
                }

                // Rescale buttons
                document.getElementById('applyRescaleBtn')?.addEventListener('click', () => {
                    const minVal = parseFloat(document.getElementById('rescaleMin').value);
                    const maxVal = parseFloat(document.getElementById('rescaleMax').value);
                    if (isNaN(minVal) || isNaN(maxVal)) { showToast('Enter valid min and max values', 'warning'); return; }
                    if (minVal >= maxVal) { showToast('Min must be less than max', 'warning'); return; }
                    rescaleDEM(minVal, maxVal);
                });
                document.getElementById('resetRescaleBtn')?.addEventListener('click', resetRescale);

                // Map tile layer selector (Edit panel)
                const tileLayerSelect = document.getElementById('mapTileLayer');
                if (tileLayerSelect) {
                    tileLayerSelect.onchange = e => {
                        setTileLayer(e.target.value);
                        showToast(`Map style: ${e.target.options[e.target.selectedIndex].text}`, 'info');
                    };
                }

                // Terrain relief overlay toggle (Edit panel)
                document.getElementById('showTerrainOverlay')?.addEventListener('change', e => {
                    toggleTerrainOverlay(e.target.checked);
                    updateFloatingTerrainButton(e.target.checked);
                    showToast(e.target.checked ? 'Terrain relief enabled' : 'Terrain relief disabled', 'info');
                });
                document.getElementById('floatingTerrainToggle')?.addEventListener('click', () => {
                    const cb = document.getElementById('showTerrainOverlay');
                    if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
                });

                // Generate global DEM terrain cache
                const genGlobalDemBtn = document.getElementById('genGlobalDemBtn');
                if (genGlobalDemBtn) {
                    genGlobalDemBtn.onclick = async () => {
                        const status = document.getElementById('genGlobalDemStatus');
                        genGlobalDemBtn.disabled = true;
                        if (status) status.textContent = 'Generating…';
                        showToast('Generating terrain cache — this runs once and may take a minute', 'info', 5000);
                        try {
                            const { error } = await api.misc.globalDemOverview(true);
                            if (!error) {
                                if (status) status.textContent = '✓ Done';
                                showToast('Terrain cache generated', 'success');
                            } else {
                                if (status) status.textContent = '✗ Failed';
                                showToast('Failed: ' + error, 'error');
                            }
                        } catch (e) {
                            if (status) status.textContent = '✗ Error';
                            showToast('Error generating cache', 'error');
                        } finally {
                            genGlobalDemBtn.disabled = false;
                        }
                    };
                }

                // Floating grid toggle
                document.getElementById('floatingGridToggle')?.addEventListener('click', () => {
                    mapGridEnabled = !mapGridEnabled;
                    toggleMapGrid(mapGridEnabled);
                    showToast(mapGridEnabled ? 'Grid enabled' : 'Grid disabled', 'info');
                });

                // Floating globe toggle
                document.getElementById('floatingGlobeToggle')?.addEventListener('click', () => {
                    const gc  = document.getElementById('globeContainer');
                    const btn = document.getElementById('floatingGlobeToggle');
                    if (gc.classList.contains('hidden')) {
                        Object.assign(gc.style, { position: 'absolute', top: '0', left: '0',
                                                   width: '100%', height: '100%', zIndex: '500' });
                        gc.classList.remove('hidden');
                        btn?.classList.add('active');
                        initGlobe();
                    } else {
                        gc.classList.add('hidden');
                        gc.style.position = '';
                        btn?.classList.remove('active');
                    }
                });

                // Floating regions panel toggle + close
                const floatingRegionsBtn = document.getElementById('floatingRegionsToggle');
                const regionsPanel       = document.getElementById('regionsPanel');
                if (floatingRegionsBtn && regionsPanel) {
                    floatingRegionsBtn.onclick = () => {
                        regionsPanel.classList.toggle('hidden');
                        floatingRegionsBtn.classList.toggle('active', !regionsPanel.classList.contains('hidden'));
                        if (!regionsPanel.classList.contains('hidden')) populateRegionsPanelTable();
                    };
                }
                document.getElementById('closeRegionsPanel')?.addEventListener('click', () => {
                    regionsPanel?.classList.add('hidden');
                    floatingRegionsBtn?.classList.remove('active');
                });

                // Map settings panel (⚙️ button)
                const mapSettingsBtn   = document.getElementById('floatingMapSettingsBtn');
                const mapSettingsPanel = document.getElementById('mapSettingsPanel');
                mapSettingsBtn?.addEventListener('click', () => {
                    mapSettingsPanel?.classList.toggle('hidden');
                    mapSettingsBtn.classList.toggle('active', !mapSettingsPanel?.classList.contains('hidden'));
                });
                document.getElementById('closeMapSettingsBtn')?.addEventListener('click', () => {
                    mapSettingsPanel?.classList.add('hidden');
                    mapSettingsBtn?.classList.remove('active');
                });

                // Tile layer selector in Explore panel (mirrors Edit)
                const mapTileLayerExplore = document.getElementById('mapTileLayerExplore');
                const mapTileLayerEdit    = document.getElementById('mapTileLayer');
                mapTileLayerExplore?.addEventListener('change', () => {
                    setTileLayer(mapTileLayerExplore.value);
                    if (mapTileLayerEdit) mapTileLayerEdit.value = mapTileLayerExplore.value;
                });
                mapTileLayerEdit?.addEventListener('change', () => {
                    if (mapTileLayerExplore) mapTileLayerExplore.value = mapTileLayerEdit.value;
                });

                // Terrain overlay controls in Explore panel
                const terrainCheckboxExplore     = document.getElementById('showTerrainOverlayExplore');
                const terrainRowExplore          = document.getElementById('terrainOpacityRowExplore');
                const terrainOpacityExplore      = document.getElementById('terrainOverlayOpacityExplore');
                const terrainOpacityLabelExplore = document.getElementById('terrainOpacityValueExplore');
                terrainCheckboxExplore?.addEventListener('change', () => {
                    const on = terrainCheckboxExplore.checked;
                    toggleTerrainOverlay(on);
                    if (terrainRowExplore) terrainRowExplore.style.display = on ? 'flex' : 'none';
                    const editCb = document.getElementById('showTerrainOverlay');
                    if (editCb) editCb.checked = on;
                });
                terrainOpacityExplore?.addEventListener('input', () => {
                    const val = parseInt(terrainOpacityExplore.value);
                    setTerrainOverlayOpacity(val);
                    if (terrainOpacityLabelExplore) terrainOpacityLabelExplore.textContent = val + '%';
                    const editSlider = document.getElementById('terrainOverlayOpacity');
                    if (editSlider) {
                        editSlider.value = val;
                        document.getElementById('terrainOpacityValue').textContent = val + '%';
                    }
                });

                // Grid lines toggle in Explore panel
                document.getElementById('showGridlinesExplore')?.addEventListener('change', e => {
                    mapGridEnabled = e.target.checked;
                    toggleMapGrid(mapGridEnabled);
                });

                // Regions panel search
                document.getElementById('regionsPanelSearch')
                    ?.addEventListener('input', () => populateRegionsPanelTable());

                // Regions panel "New" button
                document.getElementById('regionsPanelNewBtn')?.addEventListener('click', () => {
                    closeRegionsPanel();
                    if (drawControl && drawControl._toolbars?.draw) {
                        try { drawControl._toolbars.draw._modes.rectangle.handler.enable(); } catch(e) {}
                    }
                    showToast('Draw a rectangle on the map to create a new region', 'info');
                });

                // Draw tool wiring (activateDrawTool is a hoisted fn in setupEventListeners scope)
                document.getElementById('floatingDrawBtn')?.addEventListener('click', activateDrawTool);
                document.getElementById('startDrawBtn')?.addEventListener('click', () => {
                    activateDrawTool();
                    switchView('map');
                });
                if (map) {
                    map.on(L.Draw.Event.CREATED, () => {
                        document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
                    });
                    map.on(L.Draw.Event.DRAWSTOP, () => {
                        document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
                    });
                }

                // Layer grid (stacked layers view)
                const gridVisibleCb = document.getElementById('layerGridVisible');
                document.getElementById('layerGridVisible')?.addEventListener('change', () => {
                    const gc = document.getElementById('layerGridCanvas');
                    if (gc) {
                        gc.style.display = gridVisibleCb.checked ? 'block' : 'none';
                        if (gridVisibleCb.checked) drawLayerGrid();
                    }
                });
                document.getElementById('layerGridDensity')?.addEventListener('change', () => {
                    if (gridVisibleCb?.checked) drawLayerGrid();
                });

                // Terrain overlay opacity slider (Edit panel)
                document.getElementById('terrainOverlayOpacity')?.addEventListener('input', e => {
                    const val = e.target.value;
                    document.getElementById('terrainOpacityValue').textContent = `${val}%`;
                    setTerrainOverlayOpacity(val);
                });

                // Resolution warnings
                document.getElementById('paramDim')?.addEventListener('input', () => {
                    const val = parseInt(document.getElementById('paramDim').value);
                    const w   = document.getElementById('demResWarning');
                    if (w) w.style.display = val > 500 ? 'block' : 'none';
                });
                document.getElementById('paramSatScale')?.addEventListener('input', () => {
                    const val = parseInt(document.getElementById('paramSatScale').value);
                    const w   = document.getElementById('satResWarning');
                    if (w) w.style.display = val < 100 ? 'block' : 'none';
                });
                document.getElementById('waterResolution')?.addEventListener('change', () => {
                    const val = parseInt(document.getElementById('waterResolution').value);
                    const w   = document.getElementById('waterResWarning');
                    if (w) w.style.display = val >= 500 ? 'block' : 'none';
                    window.loadWaterMask?.();
                });
            }

            // ── Model tab: generate, download (STL/OBJ/3MF), cross-section ─────────
            function _setupModelExportListeners() {
                document.getElementById('generateModelBtn2')?.addEventListener('click', generateModelFromTab);
                document.getElementById('downloadSTLBtn')?.addEventListener('click', downloadSTL);
                document.getElementById('downloadOBJBtn')?.addEventListener('click', () => downloadModel('obj'));
                document.getElementById('download3MFBtn')?.addEventListener('click', () => downloadModel('3mf'));
                document.getElementById('previewModelBtn')?.addEventListener('click', previewModelIn3D);

                // Physical dimensions + bed optimizer
                ['modelResolution', 'modelBaseHeight'].forEach(id => {
                    document.getElementById(id)?.addEventListener('change', updatePrintDimensions);
                });
                const bedSel = document.getElementById('bedSizeSelect');
                if (bedSel) {
                    bedSel.addEventListener('change', () => {
                        const customRow = document.getElementById('bedCustomRow');
                        if (customRow) customRow.style.display = bedSel.value === 'custom' ? 'flex' : 'none';
                        updatePrintDimensions();
                    });
                }
                ['bedCustomW', 'bedCustomH'].forEach(id => {
                    document.getElementById(id)?.addEventListener('input', updatePrintDimensions);
                });

                // Contour lines toggle
                const contoursChk = document.getElementById('modelContours');
                if (contoursChk) {
                    contoursChk.addEventListener('change', () => {
                        const p = document.getElementById('modelContoursParams');
                        if (p) p.style.display = contoursChk.checked ? 'block' : 'none';
                    });
                }

                // Cross-section export
                const _setMidVal = () => {
                    const axis = document.getElementById('crossSectionAxis')?.value || 'lat';
                    const r = selectedRegion || window.appState?.selectedRegion;
                    if (!r) { showToast('Select a region first', 'warning'); return; }
                    const mid = axis === 'lat'
                        ? ((r.north + r.south) / 2).toFixed(4)
                        : ((r.east + r.west) / 2).toFixed(4);
                    const el = document.getElementById('crossSectionValue');
                    if (el) el.value = mid;
                };
                document.getElementById('crossSectionMidBtn')?.addEventListener('click', _setMidVal);
                document.getElementById('crossSectionAxis')?.addEventListener('change', () => {
                    const el = document.getElementById('crossSectionValue');
                    if (el && !el.value) _setMidVal();
                });
                document.getElementById('downloadCrossSectionBtn')
                    ?.addEventListener('click', downloadCrossSection);
            }

            // ── Resizable settings panel (drag-to-resize + ResizeObserver) ────────
            function _setupResizablePanel() {
                const resizeHandle = document.getElementById('settingsPanelResizeHandle');
                const rightPanel   = document.getElementById('demRightPanel');
                if (!resizeHandle || !rightPanel) return;

                let resizing = false, startX, startW, rafPending = false;
                resizeHandle.addEventListener('mousedown', e => {
                    resizing = true;
                    startX   = e.clientX;
                    startW   = rightPanel.offsetWidth;
                    resizeHandle.classList.add('dragging');
                    document.body.style.cursor     = 'col-resize';
                    document.body.style.userSelect = 'none';
                    e.preventDefault();
                });
                document.addEventListener('mousemove', e => {
                    if (!resizing) return;
                    const newW = Math.max(280, Math.min(900, startW + (startX - e.clientX)));
                    rightPanel.style.width = newW + 'px';
                    if (!rafPending) {
                        rafPending = true;
                        requestAnimationFrame(() => {
                            updateStackedLayers();
                            if (lastDemData?.values?.length) recolorDEM();
                            rafPending = false;
                        });
                    }
                });
                document.addEventListener('mouseup', () => {
                    if (!resizing) return;
                    resizing = false;
                    resizeHandle.classList.remove('dragging');
                    document.body.style.cursor     = '';
                    document.body.style.userSelect = '';
                    try { localStorage.setItem('strm2stl_settingsPanelWidth', rightPanel.offsetWidth); } catch (_) {}
                    requestAnimationFrame(() => updateStackedLayers());
                });
                // Restore saved width
                try {
                    const savedW = localStorage.getItem('strm2stl_settingsPanelWidth');
                    if (savedW) rightPanel.style.width = parseInt(savedW) + 'px';
                } catch (_) {}

                // ResizeObserver: reflow canvases on any panel size change
                let _raf = null;
                new ResizeObserver(() => {
                    if (_raf) return;
                    _raf = requestAnimationFrame(() => {
                        _raf = null;
                        const cc = rightPanel.querySelector('#curveCanvas');
                        if (cc) {
                            const cont = cc.parentElement;
                            if (cont.clientWidth > 0 && cont.clientHeight > 0) {
                                cc.width  = cont.clientWidth;
                                cc.height = cont.clientHeight;
                                window.drawCurve?.();
                            }
                        }
                        if (lastDemData?.values?.length) recolorDEM();
                    });
                }).observe(rightPanel);
            }

            // ── Settings save + JSON view toggle ─────────────────────────────────
            function _setupSettingsJsonToggle() {
                const saveSettingsBtn = document.getElementById('saveRegionSettingsBtn');
                if (saveSettingsBtn) saveSettingsBtn.onclick = () => window.saveRegionSettings?.();

                const jsonToggleBtn    = document.getElementById('jsonViewToggleBtn');
                const jsonView         = document.getElementById('settingsJsonView');
                const demControlsInner = document.getElementById('demControlsInner');
                const settingsSaveRow  = document.getElementById('settingsSaveRow');

                if (jsonToggleBtn && jsonView) {
                    let jsonViewOpen = false;
                    jsonToggleBtn.addEventListener('click', () => {
                        jsonViewOpen = !jsonViewOpen;
                        jsonToggleBtn.classList.toggle('active', jsonViewOpen);
                        if (jsonViewOpen) {
                            const editor = document.getElementById('settingsJsonEditor');
                            if (editor) editor.value = JSON.stringify(window.collectAllSettings?.() ?? {}, null, 2);
                            jsonView.classList.remove('hidden');
                            if (demControlsInner) demControlsInner.classList.add('hidden');
                            if (settingsSaveRow) settingsSaveRow.style.display = 'none';
                        } else {
                            jsonView.classList.add('hidden');
                            if (demControlsInner) demControlsInner.classList.remove('hidden');
                            if (settingsSaveRow) settingsSaveRow.style.display = '';
                            document.getElementById('settingsJsonError')?.classList.add('hidden');
                        }
                    });
                }

                document.getElementById('applyJsonSettingsBtn')?.addEventListener('click', () => {
                    const editor  = document.getElementById('settingsJsonEditor');
                    const errorEl = document.getElementById('settingsJsonError');
                    try {
                        window.applyAllSettings?.(JSON.parse(editor.value));
                        document.getElementById('jsonViewToggleBtn')?.click();
                        showToast('Settings applied from JSON', 'success');
                    } catch (e) {
                        if (errorEl) {
                            errorEl.textContent = 'Invalid JSON: ' + e.message;
                            errorEl.classList.remove('hidden');
                        }
                    }
                });

                document.getElementById('cancelJsonSettingsBtn')?.addEventListener('click', () => {
                    document.getElementById('jsonViewToggleBtn')?.click();
                });
            }

            // ── City overlay + puzzle + viewer export listeners ──────────────────
            function _setupCityAndExportListeners() {
                // Cities tab: load / clear
                const loadCityBtn = document.getElementById('loadCityDataBtn');
                if (loadCityBtn) loadCityBtn.onclick = loadCityData;
                const clearCityBtn = document.getElementById('clearCityDataBtn');
                if (clearCityBtn) clearCityBtn.onclick = clearCityOverlay;

                // Layer toggle handlers — invalidate offscreen cache before re-render
                ['Buildings', 'Roads', 'Waterways', 'Pois'].forEach(layer => {
                    const toggle = document.getElementById(`layer${layer}Toggle`);
                    const swatch = document.getElementById(`layer${layer}Color`);
                    if (toggle) toggle.addEventListener('change', () => {
                        window._invalidateCityCache?.();
                        renderCityOverlay();
                    });
                    if (swatch) swatch.addEventListener('input', () => {
                        window._invalidateCityCache?.();
                        renderCityOverlay();
                    });
                });
                // Road width re-renders live; tolerance/minArea require a new fetch
                document.getElementById('cityRoadWidth')
                    ?.addEventListener('input', () => renderCityOverlay());

                // Export city + terrain as 3MF
                document.getElementById('exportCityBtn')?.addEventListener('click', async () => {
                    const buildings = window.appState?.osmCityData?.buildings;
                    const demData   = lastDemData;
                    if (!buildings?.features?.length) {
                        showToast('Load city data first', 'warning'); return;
                    }
                    if (!demData?.values?.length) {
                        showToast('Load DEM first', 'warning'); return;
                    }
                    const bbox = currentDemBbox || selectedRegion;
                    if (!bbox) { showToast('No bounding box', 'warning'); return; }

                    const btn = document.getElementById('exportCityBtn');
                    if (btn) { btn.disabled = true; btn.textContent = '⏳ Exporting…'; }
                    try {
                        const payload = {
                            north: bbox.north, south: bbox.south,
                            east:  bbox.east,  west:  bbox.west,
                            dem_values:  Array.from(demData.values),
                            dem_width:   demData.width,
                            dem_height:  demData.height,
                            buildings:   buildings,
                            model_height_mm: parseFloat(document.getElementById('modelHeight')?.value) || 20,
                            base_mm:         parseFloat(document.getElementById('baseHeight')?.value)  || 5,
                            building_z_scale: parseFloat(document.getElementById('buildingZScale')?.value) || 0.5,
                            simplify_terrain: document.getElementById('citySimplifyMesh')?.checked ?? true,
                            name: (selectedRegion?.name || 'city').replace(/[^a-z0-9_-]/gi, '_'),
                        };
                        const { data: blob, error: exportErr } = await api.cities.export3mf(payload);
                        if (exportErr) throw new Error(exportErr);
                        const url  = URL.createObjectURL(blob);
                        const a    = document.createElement('a');
                        a.href     = url;
                        a.download = payload.name + '_city.3mf';
                        a.click();
                        URL.revokeObjectURL(url);
                        showToast('City 3MF exported', 'success');
                    } catch (e) {
                        showToast('Export failed: ' + e.message, 'error');
                    } finally {
                        if (btn) {
                            btn.disabled = false;
                            btn.innerHTML = '<span class="btn-icon">🏙️</span> 3MF + Buildings';
                        }
                    }
                });

                // Puzzle controls
                const puzzleEnabledChk = document.getElementById('puzzleEnabled');
                const puzzleParams = document.getElementById('puzzleParams');
                if (puzzleEnabledChk && puzzleParams) {
                    puzzleEnabledChk.addEventListener('change', () => {
                        puzzleParams.style.display = puzzleEnabledChk.checked ? '' : 'none';
                        updatePuzzlePreview();
                    });
                }
                ['puzzlePiecesX', 'puzzlePiecesY', 'puzzleNotchDepth', 'puzzleMargin'].forEach(id => {
                    document.getElementById(id)?.addEventListener('input', updatePuzzlePreview);
                });

                // 3D viewer toggles
                document.getElementById('viewerWireframe')?.addEventListener('change', e => {
                    if (window.appState.terrainMesh) window.appState.terrainMesh.material.wireframe = e.target.checked;
                });
                document.getElementById('viewerAutoRotate')?.addEventListener('change', e => {
                    window.setViewerAutoRotate?.(e.target.checked);
                });

                document.getElementById('exportPuzzle3MFBtn')
                    ?.addEventListener('click', exportPuzzle3MF);
            }

            // ── Bbox fine-tune listeners (extracted helper) ─────────────────────
            function _setupBboxListeners() {
                const bboxReloadBtn = document.getElementById('bboxReloadBtn');
                if (bboxReloadBtn) {
                    bboxReloadBtn.onclick = () => {
                        const n = parseFloat(document.getElementById('bboxNorth')?.value);
                        const s = parseFloat(document.getElementById('bboxSouth')?.value);
                        const e = parseFloat(document.getElementById('bboxEast')?.value);
                        const w = parseFloat(document.getElementById('bboxWest')?.value);
                        if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
                            showToast('Invalid coordinates', 'error'); return;
                        }
                        const nc = Math.max(-90,  Math.min(90,  n));
                        const sc = Math.max(-90,  Math.min(90,  s));
                        const ec = Math.max(-180, Math.min(180, e));
                        const wc = Math.max(-180, Math.min(180, w));
                        setBboxInputValues(nc, sc, ec, wc);
                        if (!selectedRegion) selectedRegion = {};
                        selectedRegion.north = nc; selectedRegion.south = sc;
                        selectedRegion.east  = ec; selectedRegion.west  = wc;
                        window.appState.selectedRegion = selectedRegion;
                        currentDemBbox = { north: nc, south: sc, east: ec, west: wc };
                        window.appState.currentDemBbox = currentDemBbox;
                        if (boundingBox) map.removeLayer(boundingBox);
                        boundingBox = L.rectangle([[sc, wc], [nc, ec]],
                            { color: '#e74c3c', weight: 2, fillOpacity: 0.05 });
                        boundingBox.addTo(map);
                        clearLayerCache();
                        loadAllLayers();
                    };
                }

                // Enter key on any bbox input triggers reload
                ['bboxNorth', 'bboxSouth', 'bboxEast', 'bboxWest'].forEach(id => {
                    document.getElementById(id)?.addEventListener('keydown', ev => {
                        if (ev.key === 'Enter') bboxReloadBtn?.click();
                    });
                });

                // Mini-map toggle
                document.getElementById('editBboxOnMapBtn')
                    ?.addEventListener('click', toggleBboxMiniMap);

                // Save bbox coordinates back to the selected region
                document.getElementById('saveBboxBtn')?.addEventListener('click', async () => {
                    if (!selectedRegion?.name) { showToast('No region selected', 'error'); return; }
                    const n = parseFloat(document.getElementById('bboxNorth')?.value);
                    const s = parseFloat(document.getElementById('bboxSouth')?.value);
                    const e = parseFloat(document.getElementById('bboxEast')?.value);
                    const w = parseFloat(document.getElementById('bboxWest')?.value);
                    if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
                        showToast('Invalid coordinates', 'error'); return;
                    }
                    try {
                        const { error } = await api.regions.update(selectedRegion.name, {
                            name:  selectedRegion.name,
                            label: selectedRegion.label || '',
                            north: n, south: s, east: e, west: w,
                        });
                        if (error) throw new Error(error);
                        selectedRegion.north = n; selectedRegion.south = s;
                        selectedRegion.east  = e; selectedRegion.west  = w;
                        showToast('Bbox saved', 'success');
                        await loadCoordinates();
                    } catch (err) {
                        showToast('Save failed: ' + err.message, 'error');
                    }
                });

                // Save group label for selected region
                document.getElementById('saveRegionLabelBtn')?.addEventListener('click', async () => {
                    if (!selectedRegion?.name) { showToast('No region selected', 'error'); return; }
                    const label = document.getElementById('regionLabelEdit')?.value.trim() ?? '';
                    try {
                        const { error } = await api.regions.update(selectedRegion.name, {
                            name:  selectedRegion.name,
                            label,
                            north: selectedRegion.north, south: selectedRegion.south,
                            east:  selectedRegion.east,  west:  selectedRegion.west,
                        });
                        if (error) throw new Error(error);
                        selectedRegion.label = label;
                        window.appState.selectedRegion = selectedRegion;
                        showToast('Label saved', 'success');
                        await loadCoordinates();
                    } catch (err) {
                        showToast('Save failed: ' + err.message, 'error');
                    }
                });
            }
        }

        /**
         * Register global keyboard shortcuts:
         * - Ctrl+1/2/3/4: switch views
         * - Ctrl+S: save region
         * - Ctrl+R: reload layers
         * - Escape: clear all bounding boxes
         * - ArrowUp/Down: navigate region list
         */
        function setupKeyboardShortcuts() {
            document.addEventListener('keydown', (e) => {
                // Skip if typing in an input field
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                    return;
                }

                // Ctrl/Cmd + Number: Switch tabs
                if (e.ctrlKey || e.metaKey) {
                    switch (e.key) {
                        case '1':
                            e.preventDefault();
                            switchView('map');
                            showToast('Map View (Ctrl+1)', 'info');
                            break;
                        case '2':
                            e.preventDefault();
                            switchView('globe');
                            showToast('Globe View (Ctrl+2)', 'info');
                            break;
                        case '3':
                            e.preventDefault();
                            switchView('dem');
                            showToast('Layers View (Ctrl+3)', 'info');
                            break;
                        case '4':
                            e.preventDefault();
                            switchView('model');
                            showToast('Model View (Ctrl+4)', 'info');
                            break;
                        case 's':
                        case 'S':
                            e.preventDefault();
                            saveCurrentRegion();
                            break;
                        case 'r':
                        case 'R':
                            e.preventDefault();
                            if (selectedRegion) loadAllLayers();
                            break;
                        case 'z':
                        case 'Z':
                            e.preventDefault();
                            undoCurve();
                            break;
                        case 'y':
                        case 'Y':
                            e.preventDefault();
                            redoCurve();
                            break;
                    }
                }

                // Escape: Clear selections
                if (e.key === 'Escape') {
                    clearAllBoundingBoxes();
                }

                // Arrow keys: Navigate regions
                if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                    e.preventDefault();
                    const regionList = document.getElementById('coordinateList');
                    const items = regionList.querySelectorAll('li');
                    if (items.length === 0) return;

                    const activeItem = regionList.querySelector('li.active');
                    let currentIndex = activeItem ? Array.from(items).indexOf(activeItem) : -1;

                    if (e.key === 'ArrowUp') {
                        currentIndex = Math.max(0, currentIndex - 1);
                    } else {
                        currentIndex = Math.min(items.length - 1, currentIndex + 1);
                    }

                    items[currentIndex].click();
                }
            });
        }

        // ============================================================
        // CURVE EDITOR — moved to modules/curve-editor.js
        // ============================================================
        // Stub closure vars (referenced later in renderDEMCanvas / clearLayerCache)
        let curveDataVmin = null, curveDataVmax = null;
        let originalDemValues = null;

        // initCurveEditor, setCurvePreset, addCurvePoint, drawCurve, applyCurveTodem,
        // applyCurveTodemSilent, undoCurve, redoCurve, resetDemToOriginal, interpolateCurve
        // — all moved to modules/curve-editor.js (exposed on window).
        function initCurveEditor() { window.initCurveEditor?.(); }
        function undoCurve()       { window.undoCurve?.(); }
        function redoCurve()       { window.redoCurve?.(); }


        // ============================================================
        // PRESET MANAGEMENT
        // Built-in and user-defined parameter preset profiles.
        // Stored in localStorage under the key 'userPresets'.
        // ============================================================
        // builtInPresets, userPresets, initPresetProfiles, setupPresetEventListeners,
        // updatePresetSelect, loadSelectedPreset, applyPreset, getCurrentSettings,
        // collectAllSettings, applyAllSettings, saveRegionSettings,
        // loadAndApplyRegionSettings, showSavePresetDialog, hideSavePresetDialog,
        // saveNewPreset, deleteSelectedPreset — moved to modules/presets.js

        // initPresetProfiles — in modules/presets.js; called via window.initPresetProfiles()
        function initPresetProfiles() { window.initPresetProfiles?.(); }

        // ── window.CONTINENT_HIDDEN, regionNotes, currentNotesRegion, regionThumbnails ─────
        // ── detectContinent, groupRegionsByContinent, renderCoordinatesList ──────────
        // ── populateRegionsTable, loadRegionFromTable, viewRegionOnMap ───────────────
        // ── setupRegionsTable, initRegionNotes, showNotesModal, hideNotesModal ───────
        // ── saveRegionNotes, initRegionThumbnails, saveRegionThumbnail ───────────────
        // Extracted to ui/static/js/modules/region-ui.js.
        // All functions are on window; coordinatesData via window.getCoordinatesData(),
        // sidebarState via window.getSidebarState().
        // ────────────────────────────────────────────────────────────────────────────

        // ── compareData, initCompareMode … applyRegionParams ────────────────────────
        // Extracted to ui/static/js/modules/compare-view.js.
        // All functions are on window; coordinatesData via window.getCoordinatesData().
        // ────────────────────────────────────────────────────────────────────────────

        // ============================================================
        // STACKED LAYERS VIEW
        // Napari-style layered canvas view with zoom/pan and grid overlay.
        // ============================================================
        let stackedLayerData = {
            dem: null,
            water: null,
            sat: null
        };

        /**
         * Initialise stacked layers: wire visibility checkboxes and opacity sliders
         * for DEM, water, and satellite layer canvases.
         */
        function setupStackedLayers() {
            // Layer visibility toggles
            ['Dem', 'Water', 'Sat'].forEach(layer => {
                const checkbox = document.getElementById(`layer${layer}Visible`);
                const slider = document.getElementById(`layer${layer}Opacity`);
                const canvas = document.getElementById(`layer${layer}Canvas`);

                if (checkbox) {
                    checkbox.onchange = () => {
                        if (canvas) {
                            canvas.style.display = checkbox.checked ? 'block' : 'none';
                        }
                    };
                }

                if (slider) {
                    slider.oninput = (e) => {
                        const val = e.target.value;
                        if (canvas) {
                            canvas.style.opacity = val / 100;
                        }
                        // Update label
                        const label = slider.nextElementSibling;
                        if (label) label.textContent = `${val}%`;
                    };
                }
            });
        }

        // ── updateStackedLayers, applyStackedTransform, enableStackedZoomPan, drawLayerGrid ─
        // Extracted to ui/static/js/modules/stacked-layers.js (TODO item 16).
        // Functions are defined on window by that script, loaded in index.html before app.js.
        // window.appState.(currentDemBbox, selectedRegion, lastDemData) used for shared state.

        /**
         * Render the floating regions panel list, grouped by continent.
         * Supports search filtering via `#regionsPanelSearch`.
         */
        function populateRegionsPanelTable() {
            const container = document.getElementById('regionsPanelList');
            const searchInput = document.getElementById('regionsPanelSearch');
            const searchTerm = searchInput ? searchInput.value.toLowerCase() : '';

            if (!container || !coordinatesData) return;
            container.innerHTML = '';

            if (coordinatesData.length === 0) {
                container.innerHTML = '<div style="padding:16px;color:#666;font-size:12px;text-align:center;">No regions yet.<br>Draw a bounding box on the map to create one.</div>';
                return;
            }

            const filtered = searchTerm
                ? coordinatesData.filter(r => r.name.toLowerCase().includes(searchTerm))
                : coordinatesData;

            const groups = groupRegionsByContinent(filtered);

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
                        <span class="panel-region-edit" onclick="event.stopPropagation(); goToEdit(${originalIndex}); closeRegionsPanel();">✏️ Edit</span>
                    `;
                    row.addEventListener('click', () => {
                        selectCoordinate(originalIndex);
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
        }

        /**
         * Close the floating regions panel and deactivate the toggle button.
         */
        function closeRegionsPanel() {
            document.getElementById('regionsPanel')?.classList.add('hidden');
            document.getElementById('floatingRegionsToggle')?.classList.remove('active');
        }

        /**
         * Toggle visibility of a continent group inside the floating regions panel.
         * Updates `window.CONTINENT_HIDDEN` and re-renders the panel.
         * @param {string} continent - Continent name key
         * @param {HTMLElement} eyeEl - The eye icon element to update visually
         */
        function toggleContinentVisibility(continent, eyeEl) {
            if (window.CONTINENT_HIDDEN.has(continent)) {
                window.CONTINENT_HIDDEN.delete(continent);
                eyeEl.classList.remove('hidden-continent');
            } else {
                window.CONTINENT_HIDDEN.add(continent);
                eyeEl.classList.add('hidden-continent');
            }
            // Toggle map rectangles for regions in this continent
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
        }

        /**
         * Watch settings inputs and auto-reload all layers when they change
         * (only if the `#autoReloadLayers` checkbox is checked).
         */
        function setupAutoReload() {
            const autoReloadCheckbox = document.getElementById('autoReloadLayers');
            const settingsToWatch = ['paramDim', 'paramDepthScale', 'paramWaterScale', 'paramSatScale', 'paramSubtractWater'];

            settingsToWatch.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.addEventListener('change', () => {
                        if (autoReloadCheckbox && autoReloadCheckbox.checked && selectedRegion) {
                            showToast('Settings changed - reloading layers...', 'info');
                            loadAllLayers();
                        }
                    });
                }
            });
        }

        /**
         * Remove all drawn and preloaded bounding box layers from the map,
         * reset selection state, and clear cached layer data.
         */
        function clearAllBoundingBoxes() {
            if (drawnItems) {
                drawnItems.clearLayers();
            }
            if (preloadedLayer) {
                preloadedLayer.clearLayers();
            }
            if (editMarkersLayer) {
                editMarkersLayer.clearLayers();
            }
            boundingBox = null;
            selectedRegion = null;
            window.appState.selectedRegion = null;
            currentBboxColorIndex = 0;
            updateBboxIndicator(BBOX_COLORS[0].color);

            // Clear all cached layer data
            clearLayerCache();

            // Clear layer displays
            document.getElementById('demImage').innerHTML = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view DEM</p>';
            document.getElementById('waterMaskImage').innerHTML = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view water mask</p>';
            document.getElementById('satelliteImage').innerHTML = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view land cover</p>';
            document.getElementById('combinedImage').innerHTML = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view combined layers</p>';

            showToast('All selections cleared', 'info');
        }

        /**
         * Load DEM, water mask, and land cover in sequence for the current region.
         * Switches to the Edit view first.
         * @returns {Promise<void>}
         */
        async function loadAllLayers() {
            if (!boundingBox && !selectedRegion) {
                showToast('Please select a region or draw a bounding box first.', 'warning');
                return;
            }

            // Switch to DEM view
            switchView('dem');

            // Show loading state
            document.getElementById('demImage').innerHTML = '<p style="text-align:center;padding:50px;">Loading all layers...</p>';

            try {
                // Load DEM first
                await loadDEM();

                // Load water mask in parallel
                await window.loadWaterMask?.();

                // Render combined view automatically
                switchDemSubtab('combined');
                window.renderCombinedView?.();

            } catch (error) {
                console.error('Error loading layers:', error);
                showToast('Error loading layers: ' + error.message, 'error');
            }
        }
        // Expose for modules/presets.js (applyPreset triggers layer reload).
        window.loadAllLayers = loadAllLayers;

        // Expose closure vars + functions needed by extracted modules.
        window.getCoordinatesData = () => coordinatesData;
        window.getBoundingBox     = () => boundingBox;
        window.selectCoordinate   = selectCoordinate;
        window.goToEdit           = goToEdit;
        window.loadCoordinates    = loadCoordinates;

        // Unified opacity values (used by both stacked and combined views)
        let waterOpacity = 0.7;
        let satOpacity = 0.5;
        window.getWaterOpacity = () => waterOpacity;

        /**
         * Wire layer opacity slider events. Updates `waterOpacity`/`satOpacity` globals
         * and triggers stacked/combined view redraws.
         */
        function setupOpacityControls() {
            // Layer opacity controls (unified for stacked and combined views)
            const layers = [
                { key: 'Dem', varUpdate: null },
                { key: 'Water', varUpdate: (val) => { waterOpacity = val; } },
                { key: 'Sat', varUpdate: (val) => { satOpacity = val; } }
            ];

            layers.forEach(({ key, varUpdate }) => {
                const slider = document.getElementById(`layer${key}Opacity`);
                const label = document.getElementById(`layer${key}OpacityLabel`);
                if (slider) {
                    slider.addEventListener('input', () => {
                        const val = parseInt(slider.value) / 100;
                        if (label) label.textContent = slider.value + '%';
                        if (varUpdate) varUpdate(val);

                        // Update stacked view
                        updateStackedLayers();

                        // Re-render combined view if visible
                        if (document.getElementById('combined')?.classList.contains('active')) {
                            window.renderCombinedView?.();
                        }
                    });
                }
            });
        }

        /**
         * Re-render the DEM canvas from cached `lastDemData` using the current colormap.
         * No server request is made. Redraws colorbar, histogram, and gridlines.
         */
        // recolorDEM, rescaleDEM, resetRescale — moved to modules/dem-loader.js

        // ============================================================
        // DEM LOADING & RENDERING
        // Fetch DEM data from server, render to canvas, apply projection,
        // curve editor, colormap, rescale, histogram, colorbar.
        // ============================================================

        /**
         * Switch the main view to the specified tab.
         * Hides all containers then shows the selected one.
         * @param {'map'|'globe'|'dem'|'model'|'regions'|'compare'} view - Target view name
         */
        function switchView(view) {
            const mapContainer = document.getElementById('mapContainer');
            const globeContainer = document.getElementById('globeContainer');
            const demContainer = document.getElementById('demContainer');
            const modelContainer = document.getElementById('modelContainer');
            const compareContainer = document.getElementById('compareContainer');
            const regionsContainer = document.getElementById('regionsContainer');
            const newRegionSection = document.getElementById('newRegionSection');
            const tabs = document.querySelectorAll('.tab');

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
                if (selectedRegion) {
                    setBboxInputValues(selectedRegion.north, selectedRegion.south, selectedRegion.east, selectedRegion.west);
                }
                // Don't auto-load here - let the caller decide when to load
                // This prevents infinite recursion when selectRegion calls switchView then loadDEM
            } else if (view === 'model') {
                if (modelContainer) {
                    modelContainer.classList.remove('hidden');
                    modelContainer.style.display = 'flex';
                }
                document.querySelector('[data-view="model"]').classList.add('active');
                // Auto-collapse sidebar so the 3D viewport gets full width
                const sidebar = document.querySelector('.sidebar');
                if (sidebar) sidebar.style.display = 'none';
            } else if (view === 'regions') {
                if (regionsContainer) {
                    regionsContainer.classList.remove('hidden');
                    populateRegionsTable();
                }
                document.querySelector('[data-view="regions"]').classList.add('active');
            } else if (view === 'compare') {
                if (compareContainer) {
                    compareContainer.classList.remove('hidden');
                    initCompareMode();
                }
                document.querySelector('[data-view="compare"]').classList.add('active');
            }
        }

        /**
         * Apply the currently selected region's bbox to the map.
         * Shows a warning if no region is selected.
         */
        function loadSelectedRegion() {
            if (!selectedRegion) {
                showToast('Please select a region first.', 'warning');
                return;
            }

            // Region is already loaded when selected
            showToast(`Region "${selectedRegion.name}" loaded!`, 'success');
        }

        /**
         * Save the current bounding box as a new named region via `POST /api/regions`.
         * Reads name from `#regionName`, label from `#regionLabel`, and current DEM params.
         * Refreshes the coordinates list on success.
         * @returns {Promise<void>}
         */
        async function saveCurrentRegion() {
            if (!boundingBox) {
                showToast('Please draw a bounding box first!', 'warning');
                return;
            }

            const regionName = document.getElementById('regionName').value.trim();
            if (!regionName) {
                showToast('Please enter a name for the region!', 'warning');
                return;
            }

            const bounds = boundingBox;
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
                    depth_scale: parseFloat(document.getElementById('paramDepthScale').value),
                    water_scale: parseFloat(document.getElementById('paramWaterScale').value),
                    height: parseInt(document.getElementById('paramHeight').value),
                    base: parseInt(document.getElementById('paramBase').value),
                    subtract_water: document.getElementById('paramSubtractWater').checked
                }
            };

            try {
                const { data: result, error } = await api.regions.create(regionData);

                if (!error) {
                    showToast(`Region "${regionName}" saved successfully!`, 'success');
                    loadCoordinates(); // Reload coordinates
                    document.getElementById('regionName').value = '';
                    if (regionLabelInput) regionLabelInput.value = '';
                } else {
                    showToast('Error saving region: ' + (result?.error || result?.detail || error), 'error');
                }

            } catch (error) {
                console.error('Error:', error);
                showToast('Failed to save region', 'error');
            }
        }

        /**
         * Submit the current bounding box: switches to Edit view and triggers DEM load.
         * Shows a warning if no bounding box has been drawn.
         */
        function submitBoundingBox() {
            if (!boundingBox) {
                showToast('Please draw a bounding box first!', 'warning');
                return;
            }
            switchView('dem');
            loadDEM();
        }

        /**
         * Main DEM loader. Fetches DEM data from `/api/preview_dem` for the current bbox,
         * renders to canvas with colormap and projection, draws histogram and colorbar,
         * stores result in `lastDemData`, and updates stacked layers.
         * Exposed as `window.loadDEM` for HTML onclick access.
         * @param {boolean} [highRes=false] - Use 400px dim instead of the form value
         * @returns {Promise<void>}
         */
        window.loadDEM = async function loadDEM(highRes = false) {
            // Abort any in-flight DEM request before starting a new one
            if (window.loadDEM._controller) {
                window.loadDEM._controller.abort();
            }
            window.loadDEM._controller = new AbortController();
            const signal = window.loadDEM._controller.signal;

            if (!boundingBox && !selectedRegion) {
                document.getElementById('demImage').innerHTML = '<p>Please select a region or draw a bounding box first.</p>';
                showToast('Please select a region first', 'warning');
                return;
            }

            let bounds;
            if (boundingBox) {
                bounds = boundingBox;
            } else if (selectedRegion) {
                bounds = L.latLngBounds(
                    [selectedRegion.south, selectedRegion.west],
                    [selectedRegion.north, selectedRegion.east]
                );
            }

            const north = bounds.getNorth();
            const south = bounds.getSouth();
            const east = bounds.getEast();
            const west = bounds.getWest();

            const dataset = document.getElementById('demDataset').value;
            const showLandUse = document.getElementById('paramLandUse').checked;

            const demSource = document.getElementById('paramDemSource')?.value || 'local';
            const params = new URLSearchParams({
                north, south, east, west,
                dim: highRes ? 400 : document.getElementById('paramDim').value,
                depth_scale: document.getElementById('paramDepthScale').value,
                water_scale: document.getElementById('paramWaterScale').value,
                height: document.getElementById('paramHeight').value,
                base: document.getElementById('paramBase').value,
                subtract_water: document.getElementById('paramSubtractWater').checked,
                dataset: dataset,
                show_landuse: showLandUse,
                dem_source: demSource,
            });

            // Clear DEM cache before loading new DEM
            clearLayerCache();
            // Update layer status
            layerStatus.dem = 'loading';
            updateLayerStatusIndicators();

            // Show loading overlay on stacked layers view
            const stackContainer = document.getElementById('dem-image-section');
            if (stackContainer) showLoading(stackContainer, 'Loading DEM...');

            // Show loading indicator and clear old DEM
            const demImageContainer = document.getElementById('demImage');
            demImageContainer.innerHTML = `<div class="loading"><span class="spinner"></span>Loading DEM... <button onclick="window.loadDEM._controller&&window.loadDEM._controller.abort()" style="margin-left:10px;padding:2px 8px;background:#c0392b;color:#fff;border:none;border-radius:3px;cursor:pointer;font-size:11px;">✕ Cancel</button></div>`;
            showToast('Loading DEM data...', 'info');
            // Optionally, show a progress bar
            let progressBar = document.createElement('div');
            progressBar.className = 'dem-progress-bar';
            progressBar.style.width = '100%';
            progressBar.style.height = '6px';
            progressBar.style.background = '#eee';
            progressBar.innerHTML = '<div style="width:0%;height:100%;background:#4a90e2;transition:width 0.3s;" id="demProgress"></div>';
            demImageContainer.appendChild(progressBar);


            try {
                const { data, error: loadErr } = await api.dem.load(params, signal);
                if (loadErr) {
                    console.error('Failed to load /api/terrain/dem:', loadErr);
                    { const _p = document.createElement('p'); _p.textContent = `Error: ${loadErr}`; document.getElementById('demImage').replaceChildren(_p); }
                    layerStatus.dem = 'error';
                    updateLayerStatusIndicators();
                    showToast('Failed to load DEM: ' + loadErr, 'error');
                    return;
                }

                if (data.error) {
                    { const _p = document.createElement('p'); _p.textContent = `Error: ${data.error}`; document.getElementById('demImage').replaceChildren(_p); }
                    layerStatus.dem = 'error';
                    updateLayerStatusIndicators();
                    showToast('Failed to load DEM: ' + data.error, 'error');
                    return;
                }

                // Track bbox and update status
                layerBboxes.dem = { north, south, east, west };
                layerStatus.dem = 'loaded';
                updateLayerStatusIndicators();
                // Remove loading overlay from stacked layers
                const stackC = document.getElementById('dem-image-section');
                if (stackC) hideLoading(stackC);

                // Client-side rendering of DEM data
                if (data.dem_values && data.dimensions) {
                    let demVals = data.dem_values;
                    let h = Number(data.dimensions[0]);
                    let w = Number(data.dimensions[1]);

                    // Handle nested arrays
                    if (Array.isArray(demVals) && demVals.length && Array.isArray(demVals[0])) {
                        h = demVals.length;
                        w = demVals[0].length;
                        demVals = demVals.flat();
                    }

                    const colormap = document.getElementById('demColormap').value;
                    // Use reduce to avoid stack overflow with large arrays
                    const finiteVals = demVals.filter(Number.isFinite);
                    const calcMin = finiteVals.length ? finiteVals.reduce((a, b) => a < b ? a : b, finiteVals[0]) : 0;
                    const calcMax = finiteVals.length ? finiteVals.reduce((a, b) => a > b ? a : b, finiteVals[0]) : 1;
                    const vmin = data.min_elevation !== undefined ? data.min_elevation : calcMin;
                    const vmax = data.max_elevation !== undefined ? data.max_elevation : calcMax;

                    // Store bounding box for gridlines
                    currentDemBbox = { north, south, east, west };
                    window.appState.currentDemBbox = currentDemBbox;

                    // Render DEM canvas
                    const rawCanvas = renderDEMCanvas(demVals, w, h, colormap, vmin, vmax);
                    const canvas = applyProjection(rawCanvas, { north, south, east, west });
                    const container = document.getElementById('demImage');
                    container.innerHTML = '';
                    container.appendChild(canvas);
                    // Fill container width, preserve aspect ratio
                    canvas.style.width = '100%';
                    canvas.style.height = 'auto';
                    container.style.position = 'relative';

                    // Update overlays
                    updateAxesOverlay(currentDemBbox);
                    drawColorbar(vmin, vmax, colormap);
                    drawHistogram(demVals);

                    // Draw gridlines after canvas is appended and sized
                    requestAnimationFrame(() => drawGridlinesOverlay('demImage'));

                    // Update stacked layers view
                    requestAnimationFrame(() => updateStackedLayers());

                    // Populate bbox fine-tune inputs
                    setBboxInputValues(north, south, east, west);
                    const elevRange = document.getElementById('bboxElevRange');
                    if (elevRange) elevRange.textContent = `Elevation: ${vmin.toFixed(1)}m — ${vmax.toFixed(1)}m`;

                    // Sync mini-map rectangle to new bbox
                    syncBboxMiniMap();

                    // Update rescale inputs with current values
                    document.getElementById('rescaleMin').value = Math.floor(vmin);
                    document.getElementById('rescaleMax').value = Math.ceil(vmax);

                    // Handle landuse/satellite data if available
                    const landuseContainer = document.getElementById('demLanduse');
                    const landuseWrapper = document.querySelector('.dem-landuse-container');
                    if (data.sat_values && data.sat_dimensions && data.sat_available) {
                        const sat_h = data.sat_dimensions[0];
                        const sat_w = data.sat_dimensions[1];
                        const satCanvas = renderSatelliteCanvas(data.sat_values, sat_w, sat_h);
                        landuseContainer.innerHTML = '';
                        landuseContainer.appendChild(satCanvas);
                        landuseWrapper.classList.remove('hidden');
                    } else {
                        landuseWrapper.classList.add('hidden');
                    }

                    // Enable zoom/pan on new canvas
                    enableZoomAndPan(canvas);

                    // Capture a small thumbnail for the sidebar
                    if (selectedRegion?.name) {
                        try {
                            const thumbCanvas = document.createElement('canvas');
                            thumbCanvas.width = 48; thumbCanvas.height = 30;
                            thumbCanvas.getContext('2d').drawImage(canvas, 0, 0, 48, 30);
                            saveRegionThumbnail(selectedRegion.name, thumbCanvas.toDataURL('image/jpeg', 0.6));
                            renderCoordinatesList();
                        } catch (_) {}
                    }

                    // Store bbox on lastDemData for physical dimensions calculation
                    if (lastDemData) lastDemData.bbox = { north, south, east, west };

                    // Cities 8: refresh city overlay on DEM canvas after reload
                    if (window.appState?.osmCityData) requestAnimationFrame(() => window.renderCityOnDEM?.());

                    // Auto-load city data if any city layer toggle is enabled and region is small enough
                    const _anyLayerOn = ['layerBuildingsToggle','layerRoadsToggle','layerWaterwaysToggle']
                        .some(id => document.getElementById(id)?.checked);
                    if (_anyLayerOn && !window.appState?.osmCityData && typeof loadCityData === 'function') {
                        loadCityData();
                    }

                    // Update print dimensions panel (Extrude tab)
                    updatePrintDimensions();

                    showToast(`DEM loaded (${vmin.toFixed(0)}m - ${vmax.toFixed(0)}m)`, 'success');
                } else {
                    document.getElementById('demImage').innerHTML = '<p>No DEM data available</p>';
                    layerStatus.dem = 'error';
                    updateLayerStatusIndicators();
                    showToast('No DEM data available', 'warning');
                }
            } catch (error) {
                if (error.name === 'AbortError') {
                    document.getElementById('demImage').innerHTML = '<p>DEM load cancelled.</p>';
                    layerStatus.dem = 'empty';
                    updateLayerStatusIndicators();
                    return;
                }
                console.error('Error loading DEM:', error);
                console.error('Error stack:', error.stack);
                { const _p = document.createElement('p'); _p.textContent = `Failed to load DEM: ${error.message || error}`; document.getElementById('demImage').replaceChildren(_p); }
                layerStatus.dem = 'error';
                updateLayerStatusIndicators();
                showToast('Failed to load DEM', 'error');
            } finally {
                const stackF = document.getElementById('dem-image-section');
                if (stackF) hideLoading(stackF);
            }
        }

        // Current bounding box for gridlines (updated when DEM loads)
        let currentDemBbox = null;

        // Rendering helpers
        // lastDemData is declared at top of script

        // drawGridlinesOverlay, applyProjection — moved to modules/dem-loader.js

        /**
         * Render elevation values to a canvas element using a colour lookup table.
         * Stores data in `lastDemData` and `layerBboxes.dem`, then updates layer status.
         * @param {number[]} values - Flat array of elevation values (row-major)
         * @param {number} width - Canvas width in pixels
         * @param {number} height - Canvas height in pixels
         * @param {string} colormap - Colormap name ('terrain','viridis','jet','rainbow','hot','gray')
         * @param {number} [vmin] - Minimum value for colour mapping (auto-calculated if omitted)
         * @param {number} [vmax] - Maximum value for colour mapping (auto-calculated if omitted)
         * @returns {HTMLCanvasElement} The rendered canvas element
         */
        function renderDEMCanvas(values, width, height, colormap, vmin, vmax) {
            // store last DEM
            lastDemData = { values: (Array.isArray(values) ? values.slice() : []), width, height, colormap, vmin, vmax };
            window.appState.lastDemData = lastDemData;
            _setDemEmptyState(false);
            _updateWorkflowStepper();

            // Let curve-editor.js re-normalize control points and insert sea-level marker.
            // _onDemLoaded reads old curveDataVmin/Vmax from appState (still old at this point).
            window.appState._onDemLoaded?.(vmin, vmax);
            curveDataVmin = vmin;
            window.appState.curveDataVmin = vmin;
            curveDataVmax = vmax;
            window.appState.curveDataVmax = vmax;

            // Track DEM layer bbox (use currentDemBbox which is set before renderDEMCanvas is called)
            if (currentDemBbox) {
                layerBboxes.dem = { ...currentDemBbox };
                layerStatus.dem = 'loaded';
                updateLayerStatusIndicators();
            }

            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, width, height);
            const img = ctx.createImageData(width, height);

            // OPTIMIZATION: Use typed arrays and minimize function calls
            const data = img.data;
            const flat = Array.isArray(values) ? values : [];
            const len = flat.length;

            // Find min/max efficiently with typed arrays
            let calcMin = Infinity, calcMax = -Infinity;
            for (let i = 0; i < len; i++) {
                const v = flat[i];
                if (Number.isFinite(v)) {
                    if (v < calcMin) calcMin = v;
                    if (v > calcMax) calcMax = v;
                }
            }
            if (calcMin === Infinity) calcMin = 0;
            if (calcMax === -Infinity) calcMax = 1;

            const min = (typeof vmin === 'number') ? vmin : calcMin;
            const max = (typeof vmax === 'number') ? vmax : calcMax;
            const range = (max - min) || 1;
            const invRange = 1 / range; // Pre-compute inverse for multiplication instead of division

            // OPTIMIZATION: Pre-compute color lookup table for all colormaps
            const colorLUT = new Uint8Array(1024 * 3);
            for (let i = 0; i < 1024; i++) {
                const t = i / 1023;
                const [r, g, b] = mapElevationToColor(t, colormap);
                colorLUT[i * 3]     = Math.round((r || 0) * 255);
                colorLUT[i * 3 + 1] = Math.round((g || 0) * 255);
                colorLUT[i * 3 + 2] = Math.round((b || 0) * 255);
            }

            const total = width * height;
            for (let i = 0; i < total; i++) {
                const val = (i < len) ? flat[i] : NaN;
                const idx = i << 2; // i * 4 using bit shift

                if (Number.isFinite(val)) {
                    const t = (val - min) * invRange;
                    const tClamped = t < 0 ? 0 : (t > 1 ? 1 : t);

                    const lutIdx = (tClamped * 1023 + 0.5 | 0) * 3;
                    data[idx]     = colorLUT[lutIdx];
                    data[idx + 1] = colorLUT[lutIdx + 1];
                    data[idx + 2] = colorLUT[lutIdx + 2];
                    data[idx + 3] = 255;
                } else {
                    // transparent for invalid
                    data[idx] = data[idx + 1] = data[idx + 2] = data[idx + 3] = 0;
                }
            }
            ctx.putImageData(img, 0, 0);
            // scale to container width while preserving resolution
            canvas.style.maxWidth = '100%';
            canvas.style.height = 'auto';
            canvas.style.display = 'block';

            // Grid lines are optional - controlled by toggle checkbox
            // Do not add them automatically here

            return canvas;
        }
        // Expose on window so dem-loader.js (plain script loaded before app.js) can call it.
        // renderSatelliteCanvas, mapElevationToColor, updateAxesOverlay, hslToRgb, drawColorbar,
        // drawHistogram are in modules/dem-loader.js. applyProjection, enableZoomAndPan,
        // drawGridlinesOverlay, recolorDEM, rescaleDEM, resetRescale are also in dem-loader.js.
        window.renderDEMCanvas = renderDEMCanvas;

        // Resize handler: ensure canvas scales to container; redraw if desired
        window.addEventListener('resize', () => {
            const container = document.getElementById('demImage');
            if (!container) return;
            const canvas = container.querySelector('canvas');
            if (canvas) {
                canvas.style.width = '100%';
                canvas.style.height = '100%';
            }
        });

        // hslToRgb, drawColorbar, drawHistogram — moved to modules/dem-loader.js

        // ============================================================
        // SIDEBAR
        // 3-state sidebar toggle and table/list views.
        // ============================================================

        // 3-state sidebar toggle: normal → expanded → hidden → normal
        let sidebarState = 'normal'; // 'normal', 'expanded', 'hidden'
        window.getSidebarState = () => sidebarState;

        /**
         * Apply a sidebar state to the DOM: show/hide the list and table views.
         * @param {'normal'|'expanded'|'hidden'} state - Target sidebar state
         */
        function _setSidebarViews(state) {
            const listView   = document.getElementById('sidebarListView');
            const tableView  = document.getElementById('sidebarTableView');
            const editView   = document.getElementById('sidebarEditView');
            const paramsSection = document.getElementById('regionParamsSection');
            editView?.classList.add('hidden');
            if (state === 'expanded') {
                listView?.classList.add('hidden');
                tableView?.classList.remove('hidden');
                paramsSection?.classList.add('hidden'); // table replaces params panel
                renderSidebarTable();
            } else {
                listView?.classList.remove('hidden');
                tableView?.classList.add('hidden');
                paramsSection?.classList.add('hidden');
            }
        }

        /**
         * Cycle the sidebar through normal → expanded → hidden → normal.
         * Updates button icon and label, and calls `_setSidebarViews`.
         */
        function cycleSidebarState() {
            const sidebar = document.getElementById('sidebar');
            const toggleBtn = document.getElementById('sidebarToggleBtn');
            const openBtn = document.getElementById('openSidebarBtn');
            const icon = toggleBtn.querySelector('.state-icon');
            const label = toggleBtn.querySelector('.state-label');

            if (sidebarState === 'normal') {
                sidebarState = 'expanded';
                sidebar.classList.remove('collapsed');
                sidebar.classList.add('expanded');
                openBtn.classList.add('hidden');
                icon.textContent = '⇐';
                label.textContent = 'Hide';
            } else if (sidebarState === 'expanded') {
                sidebarState = 'hidden';
                sidebar.classList.remove('expanded');
                sidebar.classList.add('collapsed');
                openBtn.classList.remove('hidden');
                icon.textContent = '▶';
                label.textContent = 'Show';
            } else {
                sidebarState = 'normal';
                sidebar.classList.remove('collapsed', 'expanded');
                openBtn.classList.add('hidden');
                icon.textContent = '⇔';
                label.textContent = 'Expand';
            }
            _setSidebarViews(sidebarState);
        }

        /**
         * Render the compact sidebar table of all regions, grouped by continent.
         * Supports an optional search filter string.
         * @param {string} [filter] - Filter string; defaults to the sidebar search input value
         */
        function renderSidebarTable(filter) {
            const tbody = document.getElementById('sidebarRegionsTableBody');
            if (!tbody) return;
            tbody.innerHTML = '';
            const q = (filter || document.getElementById('sidebarTableSearch')?.value || '').toLowerCase();
            const list = q ? coordinatesData.filter(r => r.name.toLowerCase().includes(q)) : coordinatesData;
            const groups = groupRegionsByContinent(list);

            groups.forEach(({ continent, regions: groupRegions }) => {
                // Group header row
                const headerTr = document.createElement('tr');
                headerTr.className = 'tbl-group-header';
                headerTr.innerHTML = `<td colspan="7" class="tbl-group-label">${continent} <span class="tbl-group-count">${groupRegions.length}</span></td>`;
                let groupCollapsed = false;
                headerTr.onclick = () => {
                    groupCollapsed = !groupCollapsed;
                    headerTr.classList.toggle('collapsed', groupCollapsed);
                    // Toggle all rows in this group
                    let sibling = headerTr.nextElementSibling;
                    while (sibling && !sibling.classList.contains('tbl-group-header')) {
                        sibling.style.display = groupCollapsed ? 'none' : '';
                        sibling = sibling.nextElementSibling;
                    }
                };
                tbody.appendChild(headerTr);

                groupRegions.forEach(region => {
                    const originalIndex = coordinatesData.findIndex(r => r.name === region.name);
                    const p = region.parameters || {};
                    const dim = p.dim || '—';
                    const tr = document.createElement('tr');
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
                        selectCoordinate(originalIndex);
                        tbody.querySelectorAll('tr:not(.tbl-group-header)').forEach(r => r.classList.remove('selected'));
                        tr.classList.add('selected');
                    };
                    tbody.appendChild(tr);
                });
            });
        }

        // Expose for modules/region-ui.js
        window.switchView       = switchView;
        window.renderSidebarTable = renderSidebarTable;

        // Bbox layer visibility toggle (👁 button)
        let bboxLayersVisible = true;

        /**
         * Toggle the visibility of the preloaded-region and edit-marker Leaflet layers.
         * Updates the `#bboxVisToggleBtn` icon to reflect the current state.
         */
        function toggleBboxLayerVisibility() {
            bboxLayersVisible = !bboxLayersVisible;
            const btn = document.getElementById('bboxVisToggleBtn');
            if (bboxLayersVisible) {
                if (preloadedLayer && map) preloadedLayer.addTo(map);
                if (editMarkersLayer && map) editMarkersLayer.addTo(map);
                if (btn) { btn.textContent = '👁'; btn.classList.remove('hidden-state'); btn.title = 'Hide region boxes on map'; }
            } else {
                if (preloadedLayer && map) preloadedLayer.remove();
                if (editMarkersLayer && map) editMarkersLayer.remove();
                if (btn) { btn.textContent = '🙈'; btn.classList.add('hidden-state'); btn.title = 'Show region boxes on map'; }
            }
        }

        /**
         * Toggle the small status/info panel on the right edge of the visualisation area.
         * Updates the `#statusToggleBtn` arrow direction.
         */
        function toggleStatusPanel() {
            const panel = document.getElementById('statusPanel');
            const btn = document.getElementById('statusToggleBtn');
            if (!panel) return;
            const collapsed = panel.classList.toggle('collapsed');
            panel.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
            if (btn) btn.textContent = collapsed ? '◀' : '▶';
        }

        // Open sidebar from floating button (goes to normal state)
        document.getElementById('openSidebarBtn').addEventListener('click', () => {
            const sidebar = document.getElementById('sidebar');
            const toggleBtn = document.getElementById('sidebarToggleBtn');
            const icon = toggleBtn.querySelector('.state-icon');
            const label = toggleBtn.querySelector('.state-label');

            sidebarState = 'normal';
            sidebar.classList.remove('collapsed', 'expanded');
            document.getElementById('regionParamsSection').classList.add('hidden');
            document.getElementById('openSidebarBtn').classList.add('hidden');
            icon.textContent = '⇔';
            label.textContent = 'Expand';
        });
        // ============================================================
        // SATELLITE / LAND COVER
        // Load ESA land cover or satellite imagery for the current bbox.
        // ============================================================

        /**
         * Load satellite/land cover imagery from `/api/preview_dem` with `show_sat=true`.
         * Renders the result to the `#satelliteImage` container via `renderSatelliteCanvas`.
         * @returns {Promise<void>}
         */
        let _satelliteAbortController = null;
        async function loadSatelliteImage() {
            if (_satelliteAbortController) _satelliteAbortController.abort();
            _satelliteAbortController = new AbortController();
            const signal = _satelliteAbortController.signal;

            if (!boundingBox && !selectedRegion) {
                document.getElementById('satelliteImage').innerHTML = '<p>Please select a region or draw a bounding box first.</p>';
                return;
            }

            let bounds;
            if (boundingBox) {
                bounds = boundingBox;
            } else if (selectedRegion) {
                bounds = L.latLngBounds(
                    [selectedRegion.south, selectedRegion.west],
                    [selectedRegion.north, selectedRegion.east]
                );
            }

            const north = bounds.getNorth();
            const south = bounds.getSouth();
            const east = bounds.getEast();
            const west = bounds.getWest();
            const resolution = document.getElementById('satResolution').value;
            const dataset = document.getElementById('satDataset').value;

            const params = new URLSearchParams({
                north, south, east, west,
                dim: resolution,
                show_sat: true,
                dataset
            });

            // Show loading indicator
            document.getElementById('satelliteImage').innerHTML = '<p style="text-align:center;padding:20px;">Loading satellite data...</p>';

            try {
                const { data, error: satErr } = await api.dem.load(params, signal);
                if (satErr) {
                    { const _p = document.createElement('p'); _p.textContent = `Error: ${satErr}`; document.getElementById('satelliteImage').replaceChildren(_p); }
                    return;
                }

                if (data.error) {
                    { const _p = document.createElement('p'); _p.textContent = `Error: ${data.error}`; document.getElementById('satelliteImage').replaceChildren(_p); }
                    return;
                }

                // Client-side rendering of satellite data
                if (data.sat_values && data.sat_dimensions && data.sat_available) {
                    const sat_h = data.sat_dimensions[0];
                    const sat_w = data.sat_dimensions[1];
                    const canvas = renderSatelliteCanvas(data.sat_values, sat_w, sat_h);
                    canvas.style.width = '100%';
                    canvas.style.height = 'auto';
                    document.getElementById('satelliteImage').innerHTML = '';
                    document.getElementById('satelliteImage').appendChild(canvas);
                    // Ensure stacked view updates when satellite preview is ready
                    requestAnimationFrame(() => updateStackedLayers());
                } else {
                    // Show placeholder when satellite data not available
                    document.getElementById('satelliteImage').innerHTML = `
                        <div style="background:#333;padding:30px;text-align:center;border-radius:4px;">
                            <p style="color:#888;margin:0;">Satellite data not available</p>
                            <p style="color:#666;font-size:12px;margin-top:5px;">Earth Engine module required</p>
                        </div>
                    `;
                }
            } catch (error) {
                if (error.name === 'AbortError') return;
                console.error('Error loading satellite image:', error);
                document.getElementById('satelliteImage').innerHTML = '<p>Failed to load satellite image.</p>';
            }
        }

        // ============================================================
        // 3D MODEL VIEWER & EXPORT
        // Three.js terrain preview, STL/OBJ/3MF export, puzzle slicer.
        // ============================================================

        // generatedModelData and _setExportButtonsEnabled moved to modules/export-handlers.js.
        // _setExportButtonsEnabled is on window; generatedModelData is on window.appState.
        window.appState.generatedModelData = null;
        /** Show or hide the DEM empty state and layers container. */
        function _setDemEmptyState(isEmpty) {
            const emptyEl = document.getElementById('demEmptyState');
            const layersEl = document.getElementById('layersContainer');
            if (emptyEl) emptyEl.style.display = isEmpty ? 'flex' : 'none';
            if (layersEl) layersEl.style.display = isEmpty ? 'none' : '';
        }

        /**
         * UX10: Update the workflow stepper in the header.
         *
         * Three steps: (1) region selected, (2) DEM loaded, (3) model generated.
         * Marks tab step badges with ✓ when complete and shows a hint bar
         * pointing the user to the next action. The hint bar hides once all
         * three steps are done.
         */
        function _updateWorkflowStepper() {
            const step1Done = !!selectedRegion;
            const step2Done = !!lastDemData;
            const step3Done = !!window.appState.generatedModelData;

            // Update tab badges
            document.getElementById('tabExplore')?.classList.toggle('step-done', step1Done);
            document.getElementById('tabEdit')?.classList.toggle('step-done', step2Done);
            document.getElementById('tabExtrude')?.classList.toggle('step-done', step3Done);

            // Build hint bar content
            const hint = document.getElementById('workflowHint');
            const hintText = document.getElementById('workflowHintText');
            if (!hint || !hintText) return;

            if (step1Done && step2Done && step3Done) {
                hint.hidden = true;
                return;
            }
            hint.hidden = false;

            function _stepEl(n, label, state) {
                // state: 'done' | 'active' | 'pending'
                const icon = state === 'done' ? '✓' : String(n);
                return `<span class="workflow-hint-step ${state}">${icon} ${label}</span>`;
            }

            const s1 = _stepEl(1, 'Select region', step1Done ? 'done' : 'active');
            const s2 = _stepEl(2, 'Load DEM',      step2Done ? 'done' : (step1Done ? 'active' : 'pending'));
            const s3 = _stepEl(3, 'Generate model', step3Done ? 'done' : (step2Done ? 'active' : 'pending'));

            let nextAction = '';
            if (!step1Done)      nextAction = '— select or draw a region in Explore';
            else if (!step2Done) nextAction = '— click Load DEM in the Edit tab';
            else                 nextAction = '— click Generate Model in the Extrude tab';

            hintText.innerHTML = `${s1} <span class="workflow-hint-sep">›</span> ${s2} <span class="workflow-hint-sep">›</span> ${s3} <span style="color:#555;margin-left:6px;">${nextAction}</span>`;
        }

        // Expose callbacks on window.appState so extracted modules can call them.
        window.appState._setDemEmptyState = _setDemEmptyState;
        window.appState._updateWorkflowStepper = _updateWorkflowStepper;
        // Called by modules/presets.js applyAllSettings to restore curve state.
        // Delegates to modules/curve-editor.js (curve-editor.js registers window.applyCurveSettings).
        window.appState._applyCurveSettings = function(points, presetName) {
            window.applyCurveSettings?.(points, presetName);
        };

        // Disable on load — enabled after generateModelFromTab succeeds
        document.addEventListener('DOMContentLoaded', () => {
            window._setExportButtonsEnabled?.(false);
            _setDemEmptyState(true);
            _updateWorkflowStepper();
        });

        /**
         * Update the physical dimensions panel in the Extrude tab.
         * Calculates real-world bbox area, print footprint in mm, map scale,
         * model height, and whether the footprint fits standard printer beds.
         * Pure JS — no backend call needed.
         */
        function updatePrintDimensions() {
            const panel = document.getElementById('printDimensions');
            if (!lastDemData || !lastDemData.width || !lastDemData.height) {
                panel.style.display = 'none';
                return;
            }

            // Grid footprint: numpy2stl maps pixel indices directly to mm units
            const gridW = lastDemData.width;
            const gridH = lastDemData.height;

            // Model height: resolution input is sent as model_height to the backend
            const modelH = parseFloat(document.getElementById('modelResolution').value) || 200;
            const baseH = parseFloat(document.getElementById('modelBaseHeight').value) || 0;
            const totalH = modelH + baseH;

            document.getElementById('dimFootprint').textContent = `${gridW} × ${gridH} mm`;
            document.getElementById('dimHeight').textContent = `${totalH} mm (${modelH} terrain + ${baseH} base)`;

            // Real-world area from bbox
            const bbox = lastDemData.bbox || (selectedRegion ? {
                north: selectedRegion.north, south: selectedRegion.south,
                east: selectedRegion.east, west: selectedRegion.west
            } : null);

            if (bbox) {
                const midLat = (bbox.north + bbox.south) / 2;
                const latCos = Math.cos(midLat * Math.PI / 180);
                const realW_m = Math.abs(bbox.east - bbox.west) * 111320 * latCos;
                const realH_m = Math.abs(bbox.north - bbox.south) * 110540;
                const realW_km = realW_m / 1000;
                const realH_km = realH_m / 1000;

                document.getElementById('dimRealArea').textContent =
                    `${realW_km.toFixed(1)} × ${realH_km.toFixed(1)} km`;

                // Scale: real width in mm / print width in mm
                const scale = Math.round(realW_m / (gridW / 1000));
                document.getElementById('dimScale').textContent = `1 : ${scale.toLocaleString()}`;

                // Bed fit check
                const beds = [
                    { name: 'Ender 220', w: 220, h: 220 },
                    { name: 'Prusa 250', w: 250, h: 210 },
                    { name: 'Bambu 256', w: 256, h: 256 },
                    { name: 'Bambu 350', w: 350, h: 350 },
                ];
                const fitting = beds.filter(b => gridW <= b.w && gridH <= b.h);
                const fitRow = document.getElementById('dimBedFitRow');
                const fitText = document.getElementById('dimBedFitText');
                if (fitting.length > 0) {
                    fitText.textContent = '✓ ' + fitting.map(b => b.name).join(', ');
                    fitRow.style.color = '#52b788';
                } else {
                    fitText.textContent = `⚠ exceeds standard beds`;
                    fitRow.style.color = '#e67e22';
                }
            } else {
                document.getElementById('dimRealArea').textContent = '—';
                document.getElementById('dimScale').textContent = '—';
                document.getElementById('dimBedFitText').textContent = '—';
            }

            panel.style.display = 'block';

            // Bed optimizer — compute recommended resolution and scale
            _updateBedOptimizer(bbox);
        }

        /**
         * Compute the recommended resolution and print scale for the selected printer bed.
         * Reads bedSizeSelect and (if custom) bedCustomW/H.
         * @param {Object|null} bbox - {north, south, east, west} or null
         */
        function _updateBedOptimizer(bbox) {
            const resultEl = document.getElementById('bedOptimizerResult');
            if (!resultEl || !bbox) return;

            const sel = document.getElementById('bedSizeSelect')?.value || '250x210';
            let bedW, bedH;
            if (sel === 'custom') {
                bedW = parseFloat(document.getElementById('bedCustomW')?.value) || 220;
                bedH = parseFloat(document.getElementById('bedCustomH')?.value) || 220;
            } else {
                [bedW, bedH] = sel.split('x').map(Number);
            }

            const midLat = (bbox.north + bbox.south) / 2;
            const latCos = Math.cos(midLat * Math.PI / 180);
            const realW_m = Math.abs(bbox.east - bbox.west) * 111320 * latCos;
            const realH_m = Math.abs(bbox.north - bbox.south) * 110540;

            // Fit to bed: scale so the longer axis fills the bed, preserve aspect ratio
            const aspectRatio = realW_m / realH_m;
            let printW, printH;
            if (aspectRatio >= bedW / bedH) {
                printW = bedW; printH = bedW / aspectRatio;
            } else {
                printH = bedH; printW = bedH * aspectRatio;
            }

            const scale = Math.round(realW_m / (printW / 1000));
            const pieces = (printW > bedW || printH > bedH) ? Math.ceil(printW / bedW) * Math.ceil(printH / bedH) : 1;

            // Recommend a resolution that gives ~0.5mm/pixel
            const recRes = Math.min(600, Math.max(100, Math.round(printW / 0.5 / 100) * 100));

            let html = `<b>Fit to ${bedW}×${bedH} mm bed:</b><br>`;
            html += `Print size: ${printW.toFixed(0)} × ${printH.toFixed(0)} mm<br>`;
            html += `Scale: 1 : ${scale.toLocaleString()}<br>`;
            html += `Recommended resolution: ${recRes}×${recRes}<br>`;
            if (pieces > 1) {
                html += `<span style="color:#e67e22;">⚠ ${printW.toFixed(0)}×${printH.toFixed(0)} mm exceeds bed — needs ${pieces}-piece puzzle</span>`;
            } else {
                html += `<span style="color:#52b788;">✓ Fits bed with ${(bedW - printW).toFixed(0)}×${(bedH - printH).toFixed(0)} mm margin</span>`;
            }
            resultEl.innerHTML = html;
        }

        // ── generateModelFromTab, downloadSTL, downloadModel, downloadCrossSection ────
        // Extracted to ui/static/js/modules/export-handlers.js.
        // Functions are on window; generatedModelData is on window.appState.
        // ────────────────────────────────────────────────────────────────────────────


        // ── initModelViewer, createTerrainMesh, previewModelIn3D, haversineDiagKm ─────
        // Extracted to ui/static/js/modules/model-viewer.js.
        // Functions are on window; terrainMesh is on window.appState.terrainMesh.
        // ────────────────────────────────────────────────────────────────────────────

        // ================================================================
        // Feature 2 — Cities / OSM layer
        // ================================================================

        // ============================================================
        // CITY OVERLAY
        // OpenStreetMap / Overpass API city data rendered as a canvas
        // overlay on the stacked layers view.
        // To be extracted to modules/city-overlay.js (Task 3).
        // ============================================================

        // haversineDiagKm — extracted to modules/model-viewer.js (window.haversineDiagKm).
        // window.appState.haversineDiagKm set by model-viewer.js initModelViewer().
        window.appState.haversineDiagKm = (...args) => window.haversineDiagKm?.(...args);

        // ── _updateCitiesLoadButton, loadCityRaster, _setupCityRasterLayer ──────────
        // Extracted to ui/static/js/modules/city-overlay.js.
        // Functions are defined on window by that script, loaded in index.html before app.js.
        // _setupCityRasterLayer is called from city-overlay.js DOMContentLoaded.
        // ────────────────────────────────────────────────────────────────────────────

        // ── loadCityData, _updateCityLayerCount, clearCityOverlay, renderCityOverlay ─────
        // Also in modules/city-overlay.js.

        // ── updatePuzzlePreview, exportPuzzle3MF, terrainMesh, viewerAutoRotate ────────
        // Extracted to ui/static/js/modules/model-viewer.js.
        // updatePuzzlePreview and exportPuzzle3MF are on window; terrainMesh is on
        // window.appState.terrainMesh; viewerAutoRotate via window.setViewerAutoRotate().
        // ────────────────────────────────────────────────────────────────────────────

        // ── _initDemSources, _mergeSourceOptions, _createMergeLayerObj, _renderMergeLayerCard ───
        // ── _renderMergePanel, _mergeLayerToSpec, runMerge, _syncMergeFromCurrentLayers ────────
        // ── _refreshPipelinePanel, setupMergePanel ───────────────────────────────────────────
        // Extracted to ui/static/js/modules/dem-merge.js. On window: _initDemSources, _refreshPipelinePanel, setupMergePanel.
        // ────────────────────────────────────────────────────────────────────────────

        // ============================================================
        // DEM SUB-TABS
        // Strip navigation (dem / water / landcover / combined / satellite
        // / cities / merge / compare).
        // ============================================================

        /**
         * Wire click listeners on the DEM strip sub-tab buttons.
         * Also handles the settings panel collapse/expand toggle.
         */
        function setupDemSubtabs() {
            // Subtab buttons in the top bar
            document.querySelectorAll('#demStrip [data-subtab]').forEach(btn => {
                btn.addEventListener('click', () => { if (!btn.disabled) switchDemSubtab(btn.dataset.subtab); });
            });

            // Settings toggle — collapses/expands the right panel completely
            /**
             * Collapse or expand the right settings panel and restore the terrain canvas.
             */
            function toggleSettingsPanel() {
                const wrapper = document.getElementById('demRightPanel');
                if (!wrapper) return;
                const collapsed = wrapper.classList.toggle('settings-collapsed');
                const stripBtn = document.getElementById('settingsStripBtn');
                if (stripBtn) stripBtn.classList.toggle('active', !collapsed);
                // Ensure the terrain canvas is visible whenever Settings is toggled
                document.getElementById('layersContainer')?.classList.remove('hidden');
                document.getElementById('citiesPanel')?.classList.add('hidden');
                document.getElementById('mergePanel')?.classList.add('hidden');
                document.getElementById('compareInlineContainer')?.classList.add('hidden');
                document.getElementById('combinedContainer')?.classList.add('hidden');
                document.getElementById('demControlsInner')?.classList.remove('hidden');
                updateStackedLayers();
            }
            const settingsBtn = document.getElementById('settingsStripBtn');
            if (settingsBtn) settingsBtn.addEventListener('click', toggleSettingsPanel);
            const settingsExtBtn = document.getElementById('settingsExternalBtn');
            if (settingsExtBtn) settingsExtBtn.addEventListener('click', toggleSettingsPanel);
            const settingsCollapsedTab = document.getElementById('settingsCollapsedTab');
            if (settingsCollapsedTab) settingsCollapsedTab.addEventListener('click', toggleSettingsPanel);
        }

        /**
         * Switch the active DEM sub-tab, showing/hiding the appropriate container.
         * @param {'dem'|'water'|'landcover'|'combined'|'satellite'|'cities'|'merge'|'compare'} subtab
         */
        function switchDemSubtab(subtab) {
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
                    updateStackedLayers();
                    break;
                case 'layers':
                    document.getElementById('layersContainer')?.classList.remove('hidden');
                    updateStackedLayers();
                    break;
                case 'combined':
                    document.getElementById('combinedContainer')?.classList.remove('hidden');
                    break;
                case 'compare':
                    document.getElementById('compareInlineContainer')?.classList.remove('hidden');
                    updateCompareCanvases();
                    break;
                case 'merge':
                    document.getElementById('mergePanel')?.classList.remove('hidden');
                    document.getElementById('demControlsInner')?.classList.add('hidden');
                    window._refreshPipelinePanel?.();
                    break;
                default:
                    // Default: show layers stack
                    document.getElementById('layersContainer')?.classList.remove('hidden');
                    updateStackedLayers();
                    break;
            }
        }
        window.switchDemSubtab = switchDemSubtab;

        // ── loadWaterMask, renderWaterMask, renderEsaLandCover, renderCombinedView ────────────
        // ── loadSatelliteForTab, previewWaterSubtract, applyWaterSubtract ───────────────────
        // ── renderLandCoverLegend, setupLandCoverEditor, setupWaterMaskListeners ─────────────
        // Extracted to ui/static/js/modules/water-mask.js.
        // loadWaterMask, renderWaterMask, renderEsaLandCover, renderCombinedView, loadSatelliteForTab,
        // previewWaterSubtract, applyWaterSubtract, renderLandCoverLegend, setupLandCoverEditor,
        // setupWaterMaskListeners are on window.
        // ────────────────────────────────────────────────────────────────────────────

        // ============================================================
        // BBOX MINI-MAP
        // Inline Leaflet mini-map for interactive bounding box editing.
        // ============================================================
        let bboxMiniMapInstance = null;
        let bboxMiniRect = null;
        let bboxMiniMapInited = false;
        let bboxReloadTimeout = null;
        let bboxMiniDragging = false;

        /**
         * Fill the N/S/E/W coordinate input fields with values rounded to 5 decimals.
         * @param {number} n - North latitude
         * @param {number} s - South latitude
         * @param {number} e - East longitude
         * @param {number} w - West longitude
         */
        function setBboxInputValues(n, s, e, w) {
            const decimals = 5;
            const bboxN = document.getElementById('bboxNorth');
            const bboxS = document.getElementById('bboxSouth');
            const bboxE = document.getElementById('bboxEast');
            const bboxW = document.getElementById('bboxWest');
            if (bboxN) bboxN.value = parseFloat(n).toFixed(decimals);
            if (bboxS) bboxS.value = parseFloat(s).toFixed(decimals);
            if (bboxE) bboxE.value = parseFloat(e).toFixed(decimals);
            if (bboxW) bboxW.value = parseFloat(w).toFixed(decimals);
        }

        /**
         * Initialise the inline bbox mini-map Leaflet instance.
         * Draws a draggable rectangle for the current bbox and listens for drag events.
         * Guards against double-initialisation with `bboxMiniMapInited`.
         */
        function initBboxMiniMap() {
            if (bboxMiniMapInited) return;
            bboxMiniMapInited = true;

            bboxMiniMapInstance = L.map('bboxMiniMap', {
                zoomControl: true,
                attributionControl: false,
                scrollWheelZoom: true
            });

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 18, opacity: 0.85
            }).addTo(bboxMiniMapInstance);

            // Use current bbox or fall back to world view
            const bbox = currentDemBbox || (selectedRegion ? {
                north: selectedRegion.north, south: selectedRegion.south,
                east: selectedRegion.east, west: selectedRegion.west
            } : null);

            if (bbox) {
                bboxMiniRect = L.rectangle(
                    [[bbox.south, bbox.west], [bbox.north, bbox.east]],
                    { color: '#ff9900', weight: 2, fillOpacity: 0.15 }
                ).addTo(bboxMiniMapInstance);
                bboxMiniMapInstance.fitBounds(bboxMiniRect.getBounds(), { padding: [30, 30] });
                bboxMiniRect.editing.enable();
            } else {
                bboxMiniMapInstance.setView([20, 0], 2);
            }

            // Live-update inputs while dragging a handle
            const container = bboxMiniMapInstance.getContainer();
            container.addEventListener('mousedown', () => { bboxMiniDragging = true; });
            document.addEventListener('mouseup', _onBboxMiniMouseUp);
            container.addEventListener('mousemove', _onBboxMiniMouseMove);
        }

        /**
         * Live-update bbox coordinate inputs while the mini-map rectangle is being dragged.
         */
        function _onBboxMiniMouseMove() {
            if (!bboxMiniDragging || !bboxMiniRect) return;
            const b = bboxMiniRect.getBounds();
            setBboxInputValues(b.getNorth(), b.getSouth(), b.getEast(), b.getWest());
        }

        /**
         * On drag end: update bbox inputs and debounce a DEM reload (400 ms).
         */
        function _onBboxMiniMouseUp() {
            if (!bboxMiniDragging) return;
            bboxMiniDragging = false;
            if (!bboxMiniRect) return;

            const b = bboxMiniRect.getBounds();
            const n = parseFloat(b.getNorth().toFixed(5));
            const s = parseFloat(b.getSouth().toFixed(5));
            const e = parseFloat(b.getEast().toFixed(5));
            const w = parseFloat(b.getWest().toFixed(5));

            setBboxInputValues(n, s, e, w);

            // Debounced reload after drag ends
            clearTimeout(bboxReloadTimeout);
            bboxReloadTimeout = setTimeout(() => {
                if (!selectedRegion) selectedRegion = {};
                selectedRegion.north = n; selectedRegion.south = s;
                selectedRegion.east = e; selectedRegion.west = w;
                window.appState.selectedRegion = selectedRegion;
                currentDemBbox = { north: n, south: s, east: e, west: w };
                window.appState.currentDemBbox = currentDemBbox;
                clearLayerCache();
                loadDEM().then(() => { window.loadWaterMask?.(); loadSatelliteImage(); });
            }, 400);
        }

        /**
         * Sync the mini-map rectangle bounds to `currentDemBbox`.
         * Called after a DEM load or region change.
         */
        function syncBboxMiniMap() {
            if (!bboxMiniMapInited || !bboxMiniMapInstance) return;
            const bbox = currentDemBbox || (selectedRegion ? {
                north: selectedRegion.north, south: selectedRegion.south,
                east: selectedRegion.east, west: selectedRegion.west
            } : null);
            if (!bbox) return;

            if (bboxMiniRect) {
                bboxMiniRect.editing.disable();
                bboxMiniRect.setBounds([[bbox.south, bbox.west], [bbox.north, bbox.east]]);
                bboxMiniRect.editing.enable();
            } else {
                bboxMiniRect = L.rectangle(
                    [[bbox.south, bbox.west], [bbox.north, bbox.east]],
                    { color: '#ff9900', weight: 2, fillOpacity: 0.15 }
                ).addTo(bboxMiniMapInstance);
                bboxMiniRect.editing.enable();
            }
            bboxMiniMapInstance.fitBounds(bboxMiniRect.getBounds(), { padding: [30, 30] });
        }

        /**
         * Toggle the inline bbox mini-map panel open or closed.
         * Initialises the Leaflet map on first open.
         */
        function toggleBboxMiniMap() {
            const container = document.getElementById('bboxMiniMap');
            const btn = document.getElementById('editBboxOnMapBtn');
            if (!container) return;

            const opening = container.classList.contains('hidden');
            container.classList.toggle('hidden');
            if (btn) btn.classList.toggle('mini-map-open', opening);

            if (opening) {
                // Wait one frame for the div to become visible before initialising Leaflet
                requestAnimationFrame(() => {
                    if (!bboxMiniMapInited) {
                        initBboxMiniMap();
                    } else {
                        syncBboxMiniMap();
                        bboxMiniMapInstance.invalidateSize();
                    }
                });
            }
        }
        // ─────────────────────────────────────────────────────────────────────

        /**
         * Wire the `#showGridlines` checkbox and `#gridlineCount` select to redraw
         * gridline overlays on the DEM and stacked layer canvases.
         */
        function setupGridToggle() {
            const showGridlines = document.getElementById('showGridlines');
            const gridlineCount = document.getElementById('gridlineCount');

            const redrawAllGridlines = () => {
                drawGridlinesOverlay('demImage');
                drawGridlinesOverlay('inlineLayersCanvas');
            };

            if (showGridlines) {
                showGridlines.addEventListener('change', redrawAllGridlines);
            }

            if (gridlineCount) {
                gridlineCount.addEventListener('change', redrawAllGridlines);
            }

            // Redraw gridlines on window resize
            window.addEventListener('resize', () => {
                if (currentDemBbox) {
                    requestAnimationFrame(redrawAllGridlines);
                }
            });
        }

        // enableZoomAndPan — moved to modules/dem-loader.js

        /**
         * Attach a mouse-move hover tooltip to a DEM canvas that shows elevation (m)
         * and geographic coordinates for the pixel under the cursor.
         * @param {HTMLCanvasElement} canvas - The DEM canvas element
         */
        function setupHoverTooltip(canvas) {
            // Create tooltip element if it doesn't exist
            let tooltip = document.getElementById('demTooltip');
            if (!tooltip) {
                tooltip = document.createElement('div');
                tooltip.id = 'demTooltip';
                tooltip.style.cssText = `
                    position: fixed;
                    background: rgba(0,0,0,0.85);
                    color: #fff;
                    padding: 6px 10px;
                    border-radius: 4px;
                    font-size: 12px;
                    pointer-events: none;
                    z-index: 10000;
                    display: none;
                    white-space: nowrap;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
                `;
                document.body.appendChild(tooltip);
            }

            canvas.addEventListener('mousemove', (e) => {
                if (!lastDemData || !currentDemBbox) {
                    tooltip.style.display = 'none';
                    return;
                }

                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;

                // Convert to data coordinates
                const { values, width, height, vmin, vmax } = lastDemData;
                const { north, south, east, west } = currentDemBbox;

                // Normalize to 0-1 range in canvas space
                const canvasX = x / rect.width;
                const canvasY = y / rect.height;

                // Convert to data indices
                const dataX = Math.floor(canvasX * width);
                const dataY = Math.floor(canvasY * height);

                if (dataX >= 0 && dataX < width && dataY >= 0 && dataY < height) {
                    const idx = dataY * width + dataX;
                    const elevation = values[idx];

                    // Calculate lat/lon
                    const lat = north - (canvasY * (north - south));
                    const lon = west + (canvasX * (east - west));

                    if (Number.isFinite(elevation)) {
                        tooltip.innerHTML = `
                            <div><strong>Elevation:</strong> ${elevation.toFixed(1)} m</div>
                            <div><strong>Lat:</strong> ${lat.toFixed(5)}°</div>
                            <div><strong>Lon:</strong> ${lon.toFixed(5)}°</div>
                        `;
                        tooltip.style.display = 'block';
                        tooltip.style.left = (e.clientX + 15) + 'px';
                        tooltip.style.top = (e.clientY + 15) + 'px';
                    } else {
                        tooltip.innerHTML = `
                            <div><strong>No data</strong></div>
                            <div><strong>Lat:</strong> ${lat.toFixed(5)}°</div>
                            <div><strong>Lon:</strong> ${lon.toFixed(5)}°</div>
                        `;
                        tooltip.style.display = 'block';
                        tooltip.style.left = (e.clientX + 15) + 'px';
                        tooltip.style.top = (e.clientY + 15) + 'px';
                    }
                } else {
                    tooltip.style.display = 'none';
                }
            });

            canvas.addEventListener('mouseleave', () => {
                tooltip.style.display = 'none';
            });
        }

        // enableZoomAndPan and drawGridlinesOverlay are in modules/dem-loader.js.


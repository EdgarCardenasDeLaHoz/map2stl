        // ============================================================
        // FILE-TOP HELPERS (available before DOMContentLoaded)
        // ============================================================

        /**
         * Fetch a URL with basic error handling.
         * Returns parsed JSON on success, or null on network/HTTP error (and shows a toast).
         * @param {string} url - The endpoint URL
         * @param {Object} [options={}] - fetch() options
         * @returns {Promise<Object|null>} Parsed JSON response, or null on failure
         */
        async function fetchWithErrorHandling(url, options = {}) {
            try {
                const response = await fetch(url, options);
                if (!response.ok) {
                    const errorMessage = `Error: ${response.status} ${response.statusText}`;
                    console.error(errorMessage);
                    showToast(errorMessage, 'error');
                    return null;
                }
                return await response.json();
            } catch (error) {
                console.error('Network error:', error);
                showToast('Network error. Please try again later.', 'error');
                return null;
            }
        }

        // ============================================================
        // GLOBAL STATE
        // All application state lives as closure variables inside
        // DOMContentLoaded (or at file-top scope for pre-init state).
        // There is no central state object — see state.js for the
        // planned replacement (currently unused by this file).
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
        window.appState.selectedRegion  = null;
        window.appState.currentDemBbox  = null;
        window.appState.osmCityData     = null;
        window.appState.lastDemData     = null;
        window.appState.showToast       = null;
        window.appState.haversineDiagKm = null;
        let lastDemData = null;
        let lastWaterMaskData = null;
        let lastEsaData = null;
        let lastRawDemData = null;

        // Land cover configuration - colors and elevation values for each ESA WorldCover type
        const landCoverConfig = {
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

        // Store original config for reset
        const landCoverConfigDefaults = JSON.parse(JSON.stringify(landCoverConfig));

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

        // Track the name of the last applied preset (used to trigger cache-only behavior)
        let lastAppliedPresetName = null;

        /**
         * Clear all cached layer data
         * Call this when changing regions to prevent stale data
         */
        function clearLayerCache() {
            lastDemData = null;
            lastWaterMaskData = null;
            lastEsaData = null;
            lastRawDemData = null;
            currentDemBbox = null;
            window.appState.currentDemBbox = null;
            window.appState.lastDemData = null;
            _setDemEmptyState(true);
            originalDemValues = null;  // Reset so next Apply uses new region's data
            curveDataVmin = null;      // Reset stable curve coordinate system
            curveDataVmax = null;

            // Reset layer tracking
            layerBboxes = { dem: null, water: null, landCover: null };
            layerStatus = { dem: 'empty', water: 'empty', landCover: 'empty' };
            if (typeof lastCityRasterData !== 'undefined') lastCityRasterData = null;
            if (window.appState) window.appState.cityRasterSourceCanvas = null;

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

        /**
         * Update layer status indicator UI
         */
        function updateLayerStatusIndicators() {
            // Update the new layer-tab-status indicators
            updateLayerStatusUI();

            // Also update any legacy badge system
            const statusIcons = {
                'empty': '○',
                'loading': '◐',
                'loaded': '●',
                'error': '⚠️',
                'stale': '◔'
            };

            // Update tab badges (if they exist)
            document.querySelectorAll('.layer-tab').forEach(tab => {
                const subtab = tab.dataset.subtab;
                let layerName = subtab;
                if (subtab === 'satellite') layerName = 'landCover';
                if (subtab === 'combined') return; // Combined is computed

                const status = layerStatus[layerName] || 'empty';
                let badge = tab.querySelector('.layer-badge');

                if (!badge) {
                    badge = document.createElement('span');
                    badge.className = 'layer-badge';
                    badge.style.cssText = 'margin-left:4px;font-size:10px;';
                    tab.appendChild(badge);
                }

                badge.textContent = statusIcons[status];
                badge.title = status;
            });
        }

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
        // UI UTILITIES (toast, collapsibles, loading overlay, search)
        // ============================================================

        /**
         * Show a brief toast notification in the top-right corner.
         * @param {string} message - Message text to display
         * @param {'success'|'error'|'warning'|'info'} [type='info'] - Visual style
         * @param {number} [duration=3000] - Auto-dismiss delay in milliseconds
         */
        function showToast(message, type = 'info', duration = 3000) {
            const container = document.getElementById('toastContainer');
            if (!container) return;

            const icons = {
                success: '✓',
                error: '✕',
                warning: '⚠',
                info: 'ℹ'
            };

            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `
                <span class="toast-icon">${icons[type] || icons.info}</span>
                <span class="toast-message">${message}</span>
            `;

            container.appendChild(toast);

            setTimeout(() => {
                toast.remove();
            }, duration);
        }
        window.appState.showToast = showToast;

        /**
         * Toggle a collapsible section open or closed.
         * Also reinitialises the curve canvas if it was hidden.
         * @param {HTMLElement} header - The collapsible section header element
         */
        function toggleCollapsible(header) {
            const section = header.closest('.collapsible-section');
            if (section) {
                const wasCollapsed = section.classList.contains('collapsed');
                section.classList.toggle('collapsed');

                // If section is being expanded, reinitialise any canvases inside it
                if (wasCollapsed) {
                    setTimeout(() => {
                        // Curve editor canvas
                        const cc = section.querySelector('#curveCanvas');
                        if (cc) {
                            const container = cc.parentElement;
                            if (container.clientWidth > 0 && container.clientHeight > 0) {
                                cc.width  = container.clientWidth;
                                cc.height = container.clientHeight;
                                drawCurve();
                            }
                        }
                        // Histogram + colorbar — redraw at current panel width
                        if (section.querySelector('#histogram') && lastDemData?.values?.length) {
                            recolorDEM();
                        }
                    }, 50);
                }
            }
        }

        /**
         * Wire the coordinate search input to filter the list by name.
         * Hides list items whose text does not match the query.
         */
        function setupCoordinateSearch() {
            const searchInput = document.getElementById('coordSearch');
            if (!searchInput || searchInput._searchWired) return;
            searchInput._searchWired = true;

            searchInput.addEventListener('input', function () {
                const query = this.value.toLowerCase();
                const items = document.querySelectorAll('.coordinate-item');

                items.forEach(item => {
                    const name = item.textContent.toLowerCase();
                    item.style.display = name.includes(query) ? '' : 'none';
                });
            });
        }

        // ============================================================
        // LAYER STATUS
        // ============================================================

        /**
         * Update a single layer's status and refresh the status UI.
         * @param {'dem'|'water'|'landCover'} layer - Layer identifier
         * @param {'empty'|'loading'|'loaded'|'error'} status - New status value
         */
        function setLayerStatus(layer, status) {
            layerStatus[layer] = status;
            updateLayerStatusUI();
        }

        /**
         * Sync all layer status badge elements in the DOM from the `layerStatus` object.
         */
        function updateLayerStatusUI() {
            const statusMap = {
                'dem': 'status-dem',
                'water': 'status-water',
                'landCover': 'status-satellite',
                'combined': 'status-combined'
            };

            const layerStatusMap = {
                'dem': 'dem',
                'water': 'water',
                'satellite': 'landCover',
                'combined': 'combined'
            };

            Object.entries(statusMap).forEach(([layer, elementId]) => {
                const element = document.getElementById(elementId);
                if (element) {
                    // Remove all status classes
                    element.classList.remove('empty', 'loading', 'loaded', 'error');
                    // Add current status class
                    const status = layerStatus[layerStatusMap[layer] || layer] || 'empty';
                    element.classList.add(status);
                }
            });

            // Update strip button status dots
            const stripDotMap = { 'dem': 'stripDotDem', 'water': 'stripDotWater', 'landCover': 'stripDotLandCover' };
            Object.entries(stripDotMap).forEach(([layer, dotId]) => {
                const dot = document.getElementById(dotId);
                if (dot) {
                    dot.classList.remove('loaded', 'loading', 'error');
                    const s = layerStatus[layer] || 'empty';
                    if (s !== 'empty') dot.classList.add(s);
                }
            });
        }

        /**
         * Show a spinner loading overlay on a container element.
         * Removes any existing overlay first.
         * @param {HTMLElement|string} container - DOM element or element ID
         * @param {string} [message='Loading...'] - Text shown below the spinner
         */
        function showLoading(container, message = 'Loading...') {
            // Remove any existing overlay
            hideLoading(container);

            const overlay = document.createElement('div');
            overlay.className = 'loading-overlay';
            overlay.innerHTML = `
                <span class="spinner"></span>
                <p>${message}</p>
            `;

            if (typeof container === 'string') {
                container = document.getElementById(container);
            }

            if (container) {
                container.style.position = 'relative';
                container.appendChild(overlay);
            }
        }

        /**
         * Remove the loading overlay from a container element.
         * @param {HTMLElement|string} container - DOM element or element ID
         */
        function hideLoading(container) {
            if (typeof container === 'string') {
                container = document.getElementById(container);
            }

            if (container) {
                const overlay = container.querySelector('.loading-overlay');
                if (overlay) overlay.remove();
            }
        }

        // ============================================
        // Cache Management System
        // ============================================

        // Client-side water mask cache
        const waterMaskCache = {
            memory: new Map(),
            maxSize: 50,
            stats: { hits: 0, misses: 0, preloaded: 0 },

            // Generate cache key from bbox and sat_scale (ESA fetch resolution in m/px)
            generateKey(bbox) {
                // sat_scale controls ESA data quality; demWidth/demHeight ensure alignment
                const sc = bbox.sat_scale || bbox.resolution || 0;
                const demW = bbox.demWidth || 0;
                const demH = bbox.demHeight || 0;
                return `${bbox.north.toFixed(4)}_${bbox.south.toFixed(4)}_${bbox.east.toFixed(4)}_${bbox.west.toFixed(4)}_sc${sc}_${demW}x${demH}`;
            },

            // Get cached data
            get(bbox) {
                const key = this.generateKey(bbox);
                if (this.memory.has(key)) {
                    this.stats.hits++;
                    return this.memory.get(key).data;
                }
                this.stats.misses++;
                return null;
            },

            // Store data in cache
            set(bbox, data) {
                const key = this.generateKey(bbox);
                this.memory.set(key, { data, timestamp: Date.now() });

                // Evict oldest if over limit
                if (this.memory.size > this.maxSize) {
                    const oldest = Array.from(this.memory.entries())
                        .sort((a, b) => a[1].timestamp - b[1].timestamp)[0];
                    this.memory.delete(oldest[0]);
                }
            },

            // Check if bbox is cached
            has(bbox) {
                return this.memory.has(this.generateKey(bbox));
            },

            // Get cache statistics
            getStats() {
                const hitRate = this.stats.hits + this.stats.misses > 0
                    ? ((this.stats.hits / (this.stats.hits + this.stats.misses)) * 100).toFixed(1)
                    : 0;
                return {
                    ...this.stats,
                    memorySize: this.memory.size,
                    hitRate: hitRate
                };
            },

            // Clear all cache
            clear() {
                this.memory.clear();
                this.stats = { hits: 0, misses: 0, preloaded: 0 };
            }
        };

        /**
         * Refresh the cache count / hit-rate elements in the UI from `waterMaskCache.getStats()`.
         */
        function updateCacheStatusUI() {
            const stats = waterMaskCache.getStats();

            const memoryCount = document.getElementById('memoryCacheCount');
            const hitRate = document.getElementById('cacheHitRate');
            const preloadedCount = document.getElementById('preloadedCount');

            if (memoryCount) memoryCount.textContent = `${stats.memorySize} items`;
            if (hitRate) hitRate.textContent = `${stats.hitRate}%`;
            if (preloadedCount) preloadedCount.textContent = `${stats.preloaded} regions`;
        }

        /**
         * Fetch server-side cache stats from `/api/cache` and update the DOM counter.
         * @returns {Promise<void>}
         */
        async function fetchServerCacheStatus() {
            try {
                const serverCacheCount = document.getElementById('serverCacheCount');
                if (serverCacheCount) serverCacheCount.textContent = 'Checking...';

                const response = await fetch('/api/cache');
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();

                if (serverCacheCount) {
                    serverCacheCount.textContent = `${data.total_cached_files} files (${data.total_size_mb} MB)`;
                }
            } catch (e) {
                console.warn('Could not fetch server cache status:', e);
                const serverCacheCount = document.getElementById('serverCacheCount');
                if (serverCacheCount) serverCacheCount.textContent = 'Error';
            }
        }

        /**
         * Preload water mask data for every stored region into `waterMaskCache`.
         * Shows a progress bar in the UI while loading.
         * @returns {Promise<void>}
         */
        async function preloadAllRegions() {
            if (!coordinatesData || coordinatesData.length === 0) {
                showToast('No regions to preload', 'warning');
                return;
            }

            const progressContainer = document.getElementById('preloadProgress');
            const progressFill = document.getElementById('preloadFill');
            const progressText = document.getElementById('preloadText');
            const preloadBtn = document.getElementById('preloadRegionsBtn');

            progressContainer.classList.remove('hidden');
            preloadBtn.disabled = true;
            preloadBtn.innerHTML = '<span class="btn-icon">⏳</span> Preloading...';

            let loaded = 0;
            let skipped = 0;
            const total = coordinatesData.length;

            showToast(`Starting preload of ${total} regions...`, 'info');

            for (const region of coordinatesData) {
                const bbox = {
                    north: region.north,
                    south: region.south,
                    east: region.east,
                    west: region.west
                };

                // Check if already cached
                if (waterMaskCache.has(bbox)) {
                    skipped++;
                    console.log(`[Preload] Skipping cached: ${region.name}`);
                } else {
                    try {
                        console.log(`[Preload] Loading: ${region.name}`);
                        const params = new URLSearchParams({
                            north: bbox.north,
                            south: bbox.south,
                            east: bbox.east,
                            west: bbox.west,
                            sat_scale: region.parameters?.sat_scale || 500,
                            dim: region.parameters?.dim || 200
                        });

                        const response = await fetch(`/api/terrain/water-mask?${params}`);
                        const data = await response.json();

                        if (!data.error) {
                            waterMaskCache.set(bbox, data);
                            waterMaskCache.stats.preloaded++;
                            loaded++;
                        }

                        // Small delay to not overwhelm server
                        await new Promise(resolve => setTimeout(resolve, 300));
                    } catch (e) {
                        console.warn(`[Preload] Failed for ${region.name}:`, e);
                    }
                }

                // Update progress
                const progress = ((loaded + skipped) / total * 100);
                progressFill.style.width = `${progress}%`;
                progressText.textContent = `${loaded + skipped} / ${total} (${loaded} loaded, ${skipped} cached)`;
                updateCacheStatusUI();
            }

            preloadBtn.disabled = false;
            preloadBtn.innerHTML = '<span class="btn-icon">⚡</span> Preload All Regions';

            showToast(`Preload complete: ${loaded} loaded, ${skipped} already cached`, 'success');

            // Hide progress after a delay
            setTimeout(() => {
                progressContainer.classList.add('hidden');
            }, 3000);
        }

        /**
         * Clear client-side water mask cache and all layer data, then refresh the status UI.
         */
        function clearClientCache() {
            waterMaskCache.clear();
            clearLayerCache();
            updateCacheStatusUI();
            showToast('Client cache cleared', 'success');
        }

        /**
         * Delete all server-side cached DEM files via `DELETE /api/cache`.
         * @returns {Promise<void>}
         */
        async function clearServerCache() {
            try {
                const response = await fetch('/api/cache', { method: 'DELETE' });
                const data = await response.json();

                if (data.status === 'success') {
                    showToast(`Server cache cleared (${data.cleared?.[0]?.files_deleted ?? 0} files)`, 'success');
                    fetchServerCacheStatus();
                } else {
                    showToast('Failed to clear server cache', 'error');
                }
            } catch (e) {
                showToast('Error clearing server cache: ' + e.message, 'error');
            }
        }

        /**
         * Wire cache management button click handlers and start the status refresh interval.
         */
        function setupCacheManagement() {
            const preloadBtn = document.getElementById('preloadRegionsBtn');
            const clearClientBtn = document.getElementById('clearClientCacheBtn');
            const clearServerBtn = document.getElementById('clearServerCacheBtn');

            if (preloadBtn) preloadBtn.onclick = preloadAllRegions;
            if (clearClientBtn) clearClientBtn.onclick = clearClientCache;
            if (clearServerBtn) clearServerBtn.onclick = clearServerCache;

            // Update cache status periodically
            updateCacheStatusUI();
            fetchServerCacheStatus();
            setInterval(updateCacheStatusUI, 5000);
        }

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
            setupWaterMaskListeners();
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
            _initDemSources();

            // Initialize merge panel
            setupMergePanel();

            // Initialize city raster layer (City Heights toggle + appState listener)
            _setupCityRasterLayer();

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
                        const response = await fetch(
                            `/api/terrain/dem?north=${north}&south=${south}&east=${east}&west=${west}&dim=150&colormap=${colormap}&projection=none&subtract_water=false&depth_scale=1`
                        );
                        const data = await response.json();
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
                console.log('Fetching /api/regions...');
                const response = await fetch('/api/regions');
                console.log('Response status:', response.status);
                const data = await response.json();
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
            const hasSaved = await loadAndApplyRegionSettings(selectedRegion.name);
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
            _updateCitiesLoadButton(selectedRegion);

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
                    loadWaterMask();
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
                loadWaterMask();
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

            // Initialize region notes
            initRegionNotes();

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
                        if (lastWaterMaskData) {
                            renderWaterMask(lastWaterMaskData);
                            renderEsaLandCover(lastWaterMaskData);
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
                            const resp = await fetch('/api/global_dem_overview?regen=true');
                            if (resp.ok) {
                                if (status) status.textContent = '✓ Done';
                                showToast('Terrain cache generated', 'success');
                            } else {
                                const d = await resp.json().catch(() => ({}));
                                if (status) status.textContent = '✗ Failed';
                                showToast('Failed: ' + (d.error || resp.statusText), 'error');
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
                    loadWaterMask();
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
                                if (typeof drawCurve === 'function') drawCurve();
                            }
                        }
                        if (lastDemData?.values?.length) recolorDEM();
                    });
                }).observe(rightPanel);
            }

            // ── Settings save + JSON view toggle ─────────────────────────────────
            function _setupSettingsJsonToggle() {
                const saveSettingsBtn = document.getElementById('saveRegionSettingsBtn');
                if (saveSettingsBtn) saveSettingsBtn.onclick = saveRegionSettings;

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
                            if (editor) editor.value = JSON.stringify(collectAllSettings(), null, 2);
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
                        applyAllSettings(JSON.parse(editor.value));
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
                        const resp = await fetch('/api/cities/export3mf', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload),
                        });
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({}));
                            throw new Error(err.error || resp.status);
                        }
                        const blob = await resp.blob();
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
                    if (terrainMesh) terrainMesh.material.wireframe = e.target.checked;
                });
                document.getElementById('viewerAutoRotate')?.addEventListener('change', e => {
                    viewerAutoRotate = e.target.checked;
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
                        const resp = await fetch(
                            `/api/regions/${encodeURIComponent(selectedRegion.name)}`,
                            {
                                method: 'PUT',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    name:  selectedRegion.name,
                                    label: selectedRegion.label || '',
                                    north: n, south: s, east: e, west: w,
                                }),
                            }
                        );
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({}));
                            throw new Error(err.error || resp.status);
                        }
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
                        const resp = await fetch(
                            `/api/regions/${encodeURIComponent(selectedRegion.name)}`,
                            {
                                method: 'PUT',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    name:  selectedRegion.name,
                                    label,
                                    north: selectedRegion.north, south: selectedRegion.south,
                                    east:  selectedRegion.east,  west:  selectedRegion.west,
                                }),
                            }
                        );
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({}));
                            throw new Error(err.error || resp.status);
                        }
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
        // CURVE EDITOR
        // Interactive elevation curve editor using Canvas API.
        // Supports monotone cubic spline interpolation and named presets.
        // ============================================================
        let curvePoints = [];  // Array of {x, y} normalized [0,1] — x=0 means curveDataVmin, x=1 means curveDataVmax
        let curveCanvas, curveCtx;
        let activeCurvePreset = 'linear';
        let originalDemValues = null;  // Store original DEM values before curve application
        let curveDataVmin = null, curveDataVmax = null;  // Stable reference range for curve coordinate system (set at load, never changed by rescale)
        let _curveLUT = null;  // 1024-point float LUT, invalidated when curvePoints changes

        const curvePresets = {
            'linear': [[0, 0], [1, 1]],
            'enhance-peaks': [[0, 0], [0.3, 0.2], [0.5, 0.4], [0.7, 0.7], [0.85, 0.9], [1, 1]],
            'compress-depths': [[0, 0.2], [0.2, 0.3], [0.4, 0.45], [0.6, 0.6], [0.8, 0.8], [1, 1]],
            's-curve': [[0, 0], [0.25, 0.1], [0.5, 0.5], [0.75, 0.9], [1, 1]]
        };

        /**
         * Initialise the elevation curve editor: get canvas reference, set up
         * event listeners, and draw the initial linear curve.
         */
        function initCurveEditor() {
            curveCanvas = document.getElementById('curveCanvas');
            if (!curveCanvas) return;

            curveCtx = curveCanvas.getContext('2d');

            // Set canvas size - use fixed size if container is collapsed
            const container = curveCanvas.parentElement;
            const containerWidth = container.clientWidth || 200;
            const containerHeight = container.clientHeight || 150;
            curveCanvas.width = Math.max(containerWidth, 150);
            curveCanvas.height = Math.max(containerHeight, 100);

            // Initialize with linear preset
            setCurvePreset('linear');

            // Setup event listeners
            setupCurveEventListeners();

            // Observe for resize when section is expanded; debounce via RAF and fire once on init
            let _curveResizeRaf = null;
            const _applyCurveResize = () => {
                if (container.clientWidth > 0 && container.clientHeight > 0) {
                    curveCanvas.width = container.clientWidth;
                    curveCanvas.height = container.clientHeight;
                    drawCurve();
                }
            };
            const resizeObserver = new ResizeObserver(() => {
                if (_curveResizeRaf) return;
                _curveResizeRaf = requestAnimationFrame(() => { _curveResizeRaf = null; _applyCurveResize(); });
            });
            resizeObserver.observe(container);
            // Fire immediately in case container already has its final size
            _applyCurveResize();
        }

        /**
         * Wire all mouse events on the curve canvas: click to add, double-click to remove,
         * drag to move control points, and button handlers (Apply, Reset, Sea Level).
         */
        function setupCurveEventListeners() {
            if (!curveCanvas || curveCanvas._curveWired) return;
            curveCanvas._curveWired = true;

            // Preset buttons
            document.querySelectorAll('.curve-presets button').forEach(btn => {
                btn.addEventListener('click', () => {
                    const preset = btn.dataset.preset;
                    setCurvePreset(preset);
                    document.querySelectorAll('.curve-presets button').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    // Auto-apply on preset change
                    applyCurveTodemSilent();
                });
            });

            // Track drag state to suppress accidental point-adds after dragging
            let draggingPoint = null;
            let didDrag = false;

            // Left-click to add point (only if no drag just happened and no existing point nearby)
            curveCanvas.addEventListener('click', (e) => {
                if (didDrag) { didDrag = false; return; }
                const rect = curveCanvas.getBoundingClientRect();
                const x = (e.clientX - rect.left) / rect.width;
                const y = 1 - (e.clientY - rect.top) / rect.height;
                // Only add if not clicking near an existing point
                if (!findCurvePointNear(x, y)) addCurvePoint(x, y);
            });

            // Right-click to delete a point
            curveCanvas.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                const rect = curveCanvas.getBoundingClientRect();
                const x = (e.clientX - rect.left) / rect.width;
                const y = 1 - (e.clientY - rect.top) / rect.height;
                removeCurvePointNear(x, y);
            });

            // Drag points
            curveCanvas.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return; // left button only
                const rect = curveCanvas.getBoundingClientRect();
                const x = (e.clientX - rect.left) / rect.width;
                const y = 1 - (e.clientY - rect.top) / rect.height;
                draggingPoint = findCurvePointNear(x, y);
                didDrag = false;
            });

            curveCanvas.addEventListener('mousemove', (e) => {
                if (!draggingPoint) return;
                didDrag = true;
                const rect = curveCanvas.getBoundingClientRect();
                const rawX = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                const rawY = Math.max(0, Math.min(1, 1 - (e.clientY - rect.top) / rect.height));

                // Find index of dragged point; lock endpoints x to 0/1
                const idx = curvePoints.indexOf(draggingPoint);
                const isFirst = idx === 0;
                const isLast = idx === curvePoints.length - 1;

                // X: first/last are locked; interior clamped between neighbours
                let newX = rawX;
                if (isFirst) newX = 0;
                else if (isLast) newX = 1;
                else {
                    const xMin = curvePoints[idx - 1].x + 0.005;
                    const xMax = curvePoints[idx + 1].x - 0.005;
                    newX = Math.max(xMin, Math.min(xMax, rawX));
                }

                // Y: clamp between neighbours' Y to enforce monotonicity
                const prevY = isFirst ? 0 : curvePoints[idx - 1].y;
                const nextY = isLast ? 1 : curvePoints[idx + 1].y;
                const newY = Math.max(prevY, Math.min(nextY, rawY));

                draggingPoint.x = newX;
                draggingPoint.y = newY;
                drawCurve();
            });

            curveCanvas.addEventListener('mouseup', () => {
                if (draggingPoint) {
                    draggingPoint = null;
                    // Auto-apply curve changes
                    applyCurveTodemSilent();
                }
            });
            curveCanvas.addEventListener('mouseleave', () => {
                if (draggingPoint) {
                    draggingPoint = null;
                    applyCurveTodemSilent();
                }
            });

            // Apply button
            const applyBtn = document.getElementById('applyCurveBtn');
            if (applyBtn) {
                applyBtn.addEventListener('click', applyCurveTodem);
            }

            // Reset button
            const resetBtn = document.getElementById('resetCurveBtn');
            if (resetBtn) {
                resetBtn.addEventListener('click', () => {
                    setCurvePreset('linear');
                    document.querySelectorAll('.curve-presets button').forEach(b => b.classList.remove('active'));
                    document.querySelector('.curve-presets button[data-preset="linear"]')?.classList.add('active');
                    resetDemToOriginal();
                });
            }

            // Sea Level Buffer button
            const seaLvlBtn = document.getElementById('seaLevelBufferBtn');
            if (seaLvlBtn) {
                seaLvlBtn.addEventListener('click', () => {
                    if (curveDataVmin === null) {
                        showToast('Load DEM data first', 'warning');
                        return;
                    }
                    // Use the stable curve coordinate system (not affected by display rescale)
                    const vmin = curveDataVmin, vmax = curveDataVmax;
                    if (vmin >= 0) {
                        showToast('No sub-sea-level data in this region', 'info');
                        return;
                    }
                    // Normalised sea-level position on X axis
                    const slX = Math.max(0.01, Math.min(0.98, (0 - vmin) / ((vmax - vmin) || 1)));
                    const depthScale = 0.3;  // compress ocean depths to 30 % of their range

                    // Rebuild curve: compress ocean; keep land pts, add shelf step at sea level
                    // Filter out existing interior points in the ocean zone
                    curvePoints = curvePoints.filter(p => p === curvePoints[0] || p === curvePoints[curvePoints.length - 1] || p.x > slX + 0.02);

                    // Ensure endpoints
                    curvePoints[0] = { x: 0, y: 0 };
                    if (curvePoints[curvePoints.length - 1].x < 1) curvePoints.push({ x: 1, y: 1 });

                    // Ocean compression: just before sea level, output is slX * depthScale
                    const shelfY = slX * depthScale;
                    curvePoints.push({ x: slX - 0.005, y: shelfY });
                    // Shelf step: sharp rise at sea level
                    curvePoints.push({ x: slX, y: shelfY + 0.015 });
                    // First land point continues linearly up from there
                    curvePoints.push({ x: slX + 0.02, y: shelfY + 0.04 });

                    // Re-sort and enforce monotonicity
                    curvePoints.sort((a, b) => a.x - b.x);
                    for (let i = 1; i < curvePoints.length; i++) {
                        if (curvePoints[i].y < curvePoints[i - 1].y)
                            curvePoints[i].y = curvePoints[i - 1].y;
                    }
                    drawCurve();
                    applyCurveTodemSilent();
                    showToast('Sea level shelf applied', 'success');
                });
            }
        }

        /**
         * Load a named curve preset into `curvePoints` and redraw the canvas.
         * @param {string} presetName - Key in `curvePresets` (e.g. 'linear', 's-curve')
         */
        function setCurvePreset(presetName) {
            activeCurvePreset = presetName;
            const preset = curvePresets[presetName];
            if (!preset) return;

            curvePoints = preset.map(p => ({ x: p[0], y: p[1] }));
            drawCurve();
        }

        /**
         * Add a control point to the curve, enforcing monotonicity.
         * Skips if a nearby point already exists (threshold 0.05).
         * @param {number} x - Normalised x coordinate [0, 1]
         * @param {number} y - Normalised y coordinate [0, 1]
         */
        function addCurvePoint(x, y) {
            // Don't add if too close to existing point
            const threshold = 0.08;
            for (const p of curvePoints) {
                if (Math.abs(p.x - x) < threshold && Math.abs(p.y - y) < threshold) return;
            }

            // Enforce monotonicity: clamp y between left and right neighbours
            curvePoints.sort((a, b) => a.x - b.x);
            let prevY = 0, nextY = 1;
            for (let i = 0; i < curvePoints.length; i++) {
                if (curvePoints[i].x <= x) prevY = curvePoints[i].y;
                else { nextY = curvePoints[i].y; break; }
            }
            y = Math.max(prevY, Math.min(nextY, y));

            curvePoints.push({ x, y });
            curvePoints.sort((a, b) => a.x - b.x);
            drawCurve();
        }

        /**
         * Remove the control point nearest to the given position (threshold 0.08).
         * First and last endpoints are protected and cannot be removed.
         * @param {number} x - Normalised x coordinate [0, 1]
         * @param {number} y - Normalised y coordinate [0, 1]
         */
        function removeCurvePointNear(x, y) {
            const threshold = 0.12;
            const index = curvePoints.findIndex(p =>
                Math.abs(p.x - x) < threshold && Math.abs(p.y - y) < threshold
            );
            if (index !== -1 && index !== 0 && index !== curvePoints.length - 1) {
                // Don't remove first or last point
                curvePoints.splice(index, 1);
                drawCurve();
            }
        }

        /**
         * Find and return the first control point within threshold of (x, y).
         * @param {number} x - Normalised x [0, 1]
         * @param {number} y - Normalised y [0, 1]
         * @returns {{x: number, y: number}|undefined} Matching point or undefined
         */
        function findCurvePointNear(x, y) {
            const threshold = 0.12;
            return curvePoints.find(p =>
                Math.abs(p.x - x) < threshold && Math.abs(p.y - y) < threshold
            );
        }

        /**
         * Re-render the curve editor canvas: grid, reference line, sea-level marker,
         * the spline curve, and all control point handles.
         */
        function drawCurve() {
            _curveLUT = null;  // Invalidate LUT whenever curve control points change
            if (!curveCtx || !curveCanvas) return;

            const w = curveCanvas.width;
            const h = curveCanvas.height;

            // Clear
            curveCtx.fillStyle = '#252525';
            curveCtx.fillRect(0, 0, w, h);

            // Draw grid
            curveCtx.strokeStyle = '#3a3a3a';
            curveCtx.lineWidth = 1;
            for (let i = 1; i < 4; i++) {
                const x = w * i / 4;
                const y = h * i / 4;
                curveCtx.beginPath();
                curveCtx.moveTo(x, 0);
                curveCtx.lineTo(x, h);
                curveCtx.stroke();
                curveCtx.beginPath();
                curveCtx.moveTo(0, y);
                curveCtx.lineTo(w, y);
                curveCtx.stroke();
            }

            // Draw diagonal reference line
            curveCtx.strokeStyle = '#555';
            curveCtx.setLineDash([5, 5]);
            curveCtx.beginPath();
            curveCtx.moveTo(0, h);
            curveCtx.lineTo(w, 0);
            curveCtx.stroke();
            curveCtx.setLineDash([]);

            // Draw sea level marker (vertical dashed line at normalized 0m position)
            // Uses curveDataVmin/Vmax (stable reference range from load time) so the line
            // stays fixed relative to control points even if the user changes rescale min/max.
            if (curveDataVmin !== null && curveDataVmax !== null) {
                const slX = (0 - curveDataVmin) / ((curveDataVmax - curveDataVmin) || 1);
                if (slX > 0.01 && slX < 0.99) {
                    const px = slX * w;
                    curveCtx.strokeStyle = 'rgba(64,180,255,0.6)';
                    curveCtx.lineWidth = 1;
                    curveCtx.setLineDash([3, 3]);
                    curveCtx.beginPath();
                    curveCtx.moveTo(px, 0);
                    curveCtx.lineTo(px, h);
                    curveCtx.stroke();
                    curveCtx.setLineDash([]);
                    curveCtx.fillStyle = 'rgba(64,180,255,0.8)';
                    curveCtx.font = '9px monospace';
                    curveCtx.fillText('0m', px + 2, 10);
                }
            }

            // Draw curve
            if (curvePoints.length >= 2) {
                curveCtx.strokeStyle = '#00aaff';
                curveCtx.lineWidth = 2;
                curveCtx.beginPath();
                curveCtx.moveTo(curvePoints[0].x * w, (1 - curvePoints[0].y) * h);
                for (let i = 1; i < curvePoints.length; i++) {
                    curveCtx.lineTo(curvePoints[i].x * w, (1 - curvePoints[i].y) * h);
                }
                curveCtx.stroke();
            }

            // Draw points — larger for easier clicking, orange for endpoints, blue for interior
            curvePoints.forEach((p, i) => {
                const isEndpoint = (i === 0 || i === curvePoints.length - 1);
                const px = p.x * w, py = (1 - p.y) * h;
                const radius = isEndpoint ? 7 : 8;
                curveCtx.beginPath();
                curveCtx.arc(px, py, radius, 0, Math.PI * 2);
                curveCtx.fillStyle = isEndpoint ? '#ff6600' : '#00aaff';
                curveCtx.fill();
                curveCtx.strokeStyle = 'rgba(255,255,255,0.9)';
                curveCtx.lineWidth = 2;
                curveCtx.stroke();
                // Show delete hint on non-endpoint points
                if (!isEndpoint) {
                    curveCtx.fillStyle = 'rgba(255,255,255,0.5)';
                    curveCtx.font = '9px sans-serif';
                    curveCtx.textAlign = 'center';
                    curveCtx.fillText('×', px, py + 3);
                    curveCtx.textAlign = 'left';
                }
            });

            // Draw labels
            curveCtx.fillStyle = '#888';
            curveCtx.font = '10px sans-serif';
            curveCtx.fillText('Low', 5, h - 5);
            curveCtx.fillText('High', w - 25, 12);
            curveCtx.fillText('In', w / 2 - 5, h - 5);
            curveCtx.save();
            curveCtx.translate(12, h / 2);
            curveCtx.rotate(-Math.PI / 2);
            curveCtx.fillText('Out', 0, 0);
            curveCtx.restore();
        }

        /**
         * Apply the current curve to the DEM values and re-render.
         * Shows a success toast. Stores `originalDemValues` if not already saved.
         */
        function applyCurveTodem() {
            if (!lastDemData || !lastDemData.values || curvePoints.length < 2) {
                showToast('Load a DEM first', 'warning');
                return;
            }

            // Store original if not already stored
            if (!originalDemValues) {
                originalDemValues = [...lastDemData.values];
            }

            const values = [...originalDemValues];

            // Use the stable curve coordinate system (set at DEM load time, not affected by rescale)
            const vmin = curveDataVmin !== null ? curveDataVmin : (() => {
                let mn = Infinity;
                for (let i = 0; i < values.length; i++) if (values[i] < mn) mn = values[i];
                return mn;
            })();
            const vmax = curveDataVmax !== null ? curveDataVmax : (() => {
                let mx = -Infinity;
                for (let i = 0; i < values.length; i++) if (values[i] > mx) mx = values[i];
                return mx;
            })();
            const range = vmax - vmin || 1;

            // Build 1024-point LUT once (reused across all pixels)
            if (!_curveLUT) {
                _curveLUT = new Float32Array(1024);
                for (let i = 0; i < 1024; i++) _curveLUT[i] = interpolateCurve(i / 1023);
            }

            // Apply curve mapping via LUT (avoids per-pixel spline eval)
            const remappedValues = values.map(v => {
                const normalized = Math.max(0, Math.min(1, (v - vmin) / range));
                return vmin + _curveLUT[Math.round(normalized * 1023)] * range;
            });

            // Update lastDemData
            lastDemData.values = remappedValues;

            // If auto-rescale is enabled, recompute vmin/vmax from the remapped values
            if (document.getElementById('autoRescale')?.checked) {
                let newMin = Infinity, newMax = -Infinity;
                for (let i = 0; i < remappedValues.length; i++) {
                    const v = remappedValues[i];
                    if (isFinite(v)) {
                        if (v < newMin) newMin = v;
                        if (v > newMax) newMax = v;
                    }
                }
                if (isFinite(newMin) && isFinite(newMax)) {
                    lastDemData.vmin = newMin;
                    lastDemData.vmax = newMax;
                    document.getElementById('rescaleMin').value = Math.floor(newMin);
                    document.getElementById('rescaleMax').value = Math.ceil(newMax);
                }
            }

            // Redraw DEM
            recolorDEM();
            showToast('Elevation curve applied!', 'success');
        }

        /**
         * Apply the current curve silently (no toast). Used for real-time drag updates.
         * Reads from `originalDemValues` so repeated drags don't compound.
         */
        function applyCurveTodemSilent() {
            if (!lastDemData || !lastDemData.values || curvePoints.length < 2) {
                return;
            }

            // Store original if not already stored
            if (!originalDemValues) {
                originalDemValues = [...lastDemData.values];
            }

            const values = [...originalDemValues];

            // Use the stable curve coordinate system (set at DEM load time, not affected by rescale)
            const vmin = curveDataVmin !== null ? curveDataVmin : (() => {
                let mn = Infinity;
                for (let i = 0; i < values.length; i++) if (values[i] < mn) mn = values[i];
                return mn;
            })();
            const vmax = curveDataVmax !== null ? curveDataVmax : (() => {
                let mx = -Infinity;
                for (let i = 0; i < values.length; i++) if (values[i] > mx) mx = values[i];
                return mx;
            })();
            const range = vmax - vmin || 1;

            // Build 1024-point LUT once (reused across all pixels)
            if (!_curveLUT) {
                _curveLUT = new Float32Array(1024);
                for (let i = 0; i < 1024; i++) _curveLUT[i] = interpolateCurve(i / 1023);
            }

            // Apply curve mapping via LUT (avoids per-pixel spline eval)
            const remappedValues = values.map(v => {
                const normalized = Math.max(0, Math.min(1, (v - vmin) / range));
                return vmin + _curveLUT[Math.round(normalized * 1023)] * range;
            });

            // Update lastDemData
            lastDemData.values = remappedValues;

            // Redraw DEM (which updates layers too)
            recolorDEM();
        }

        /**
         * Evaluate the curve at a given normalised input x using monotone cubic spline.
         * Falls back to linear interpolation between the nearest two control points.
         * @param {number} x - Input value [0, 1]
         * @returns {number} Output value [0, 1]
         */
        function interpolateCurve(x) {
            // Linear interpolation between curve points
            if (curvePoints.length < 2) return x;

            // Find surrounding points
            let left = curvePoints[0];
            let right = curvePoints[curvePoints.length - 1];

            for (let i = 0; i < curvePoints.length - 1; i++) {
                if (curvePoints[i].x <= x && curvePoints[i + 1].x >= x) {
                    left = curvePoints[i];
                    right = curvePoints[i + 1];
                    break;
                }
            }

            // Interpolate
            const t = (x - left.x) / (right.x - left.x || 1);
            return left.y + t * (right.y - left.y);
        }

        /**
         * Restore `lastDemData.values` from `originalDemValues` and redraw the canvas.
         */
        function resetDemToOriginal() {
            if (originalDemValues && lastDemData) {
                lastDemData.values = [...originalDemValues];
                recolorDEM();
                showToast('DEM reset to original', 'info');
            }
        }

        // ============================================================
        // PRESET MANAGEMENT
        // Built-in and user-defined parameter preset profiles.
        // Stored in localStorage under the key 'userPresets'.
        // ============================================================
        const builtInPresets = {
            'default': {
                dim: 200,
                depthScale: 0.5,
                waterScale: 0.05,
                colormap: 'terrain',
                subtractWater: true,
                satScale: 500,
                elevationCurve: 'linear'
            },
            'high-detail': {
                dim: 600,
                depthScale: 0.3,
                waterScale: 0.03,
                colormap: 'terrain',
                subtractWater: true,
                satScale: 250,
                elevationCurve: 'linear'
            },
            'print-ready': {
                dim: 400,
                depthScale: 0.8,
                waterScale: 0.1,
                colormap: 'gray',
                subtractWater: true,
                satScale: 500,
                elevationCurve: 's-curve'
            },
            'mountain': {
                dim: 400,
                depthScale: 0.2,
                waterScale: 0.02,
                colormap: 'terrain',
                subtractWater: false,
                satScale: 500,
                elevationCurve: 'enhance-peaks'
            },
            'coastal': {
                dim: 300,
                depthScale: 0.7,
                waterScale: 0.15,
                colormap: 'viridis',
                subtractWater: true,
                satScale: 300,
                elevationCurve: 'compress-depths'
            }
        };

        let userPresets = {};

        /**
         * Load user presets from localStorage and wire preset UI event listeners.
         * Called once during DOMContentLoaded.
         */
        function initPresetProfiles() {
            // Load user presets from localStorage
            let saved = null;
            try { saved = localStorage.getItem('strm2stl_userPresets'); } catch (_) {}
            if (saved) {
                try {
                    userPresets = JSON.parse(saved);
                    updatePresetSelect();
                } catch (e) {
                    console.warn('Failed to load user presets:', e);
                }
            }

            // Setup event listeners
            setupPresetEventListeners();
        }

        /**
         * Wire click/dblclick handlers for Load, Save, Delete, Confirm, Cancel
         * preset buttons and the preset select dropdown.
         */
        function setupPresetEventListeners() {
            const loadBtn = document.getElementById('loadPresetBtn');
            const saveBtn = document.getElementById('savePresetBtn');
            const deleteBtn = document.getElementById('deletePresetBtn');
            const confirmBtn = document.getElementById('confirmSavePresetBtn');
            const cancelBtn = document.getElementById('cancelSavePresetBtn');
            const presetSelect = document.getElementById('presetSelect');

            if (loadBtn) loadBtn.addEventListener('click', loadSelectedPreset);
            if (saveBtn) saveBtn.addEventListener('click', showSavePresetDialog);
            if (deleteBtn) deleteBtn.addEventListener('click', deleteSelectedPreset);
            if (confirmBtn) confirmBtn.addEventListener('click', saveNewPreset);
            if (cancelBtn) cancelBtn.addEventListener('click', hideSavePresetDialog);

            // Double-click to quick-load
            if (presetSelect) {
                presetSelect.addEventListener('dblclick', loadSelectedPreset);
            }
        }

        /**
         * Rebuild the preset `<select>` with built-in options and user presets grouped
         * under a `My Presets` optgroup.
         */
        function updatePresetSelect() {
            const select = document.getElementById('presetSelect');
            if (!select) return;

            // Keep built-in options
            const builtInOptions = ['', 'default', 'high-detail', 'print-ready', 'mountain', 'coastal'];

            // Clear and rebuild
            select.innerHTML = '<option value="">-- Select Preset --</option>';

            // Add built-in presets
            Object.keys(builtInPresets).forEach(name => {
                const option = document.createElement('option');
                option.value = name;
                option.textContent = name.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                select.appendChild(option);
            });

            // Add user presets
            if (Object.keys(userPresets).length > 0) {
                const optgroup = document.createElement('optgroup');
                optgroup.label = '📁 My Presets';
                Object.keys(userPresets).forEach(name => {
                    const option = document.createElement('option');
                    option.value = 'user:' + name;
                    option.textContent = name;
                    optgroup.appendChild(option);
                });
                select.appendChild(optgroup);
            }
        }

        /**
         * Read the currently selected preset from the dropdown and apply it.
         * Looks up built-in presets or user presets by the select value.
         */
        function loadSelectedPreset() {
            const select = document.getElementById('presetSelect');
            if (!select || !select.value) {
                showToast('Select a preset first', 'warning');
                return;
            }

            let preset;
            // Remember which preset was selected
            lastAppliedPresetName = select.value;
            if (select.value.startsWith('user:')) {
                const name = select.value.substring(5);
                preset = userPresets[name];
            } else {
                preset = builtInPresets[select.value];
            }

            if (!preset) {
                showToast('Preset not found', 'error');
                return;
            }

            applyPreset(preset);
            showToast('Preset loaded!', 'success');
        }

        /**
         * Apply a preset object to the settings form controls and optionally reload layers.
         * @param {Object} preset - Preset object with dim, depthScale, colormap, etc.
         */
        function applyPreset(preset) {
            // Apply settings
            if (preset.dim) document.getElementById('paramDim').value = preset.dim;
            if (preset.depthScale !== undefined) document.getElementById('paramDepthScale').value = preset.depthScale;
            if (preset.waterScale !== undefined) document.getElementById('paramWaterScale').value = preset.waterScale;
            if (preset.colormap) document.getElementById('demColormap').value = preset.colormap;
            if (preset.subtractWater !== undefined) document.getElementById('paramSubtractWater').checked = preset.subtractWater;
            if (preset.satScale) document.getElementById('paramSatScale').value = preset.satScale;

            // Apply elevation curve preset
            if (preset.elevationCurve && curvePresets[preset.elevationCurve]) {
                setCurvePreset(preset.elevationCurve);
                document.querySelectorAll('.curve-presets button').forEach(b => {
                    b.classList.toggle('active', b.dataset.preset === preset.elevationCurve);
                });
            }

            // If auto-reload is on, reload layers
            if (document.getElementById('autoReloadLayers')?.checked && selectedRegion) {
                loadAllLayers();
            }
        }

        /**
         * Return a partial settings object with the most commonly preset-ed fields.
         * @returns {{dim, depthScale, waterScale, colormap, subtractWater, satScale, elevationCurve}}
         */
        function getCurrentSettings() {
            return {
                dim: parseInt(document.getElementById('paramDim')?.value) || 200,
                depthScale: parseFloat(document.getElementById('paramDepthScale')?.value) || 0.5,
                waterScale: parseFloat(document.getElementById('paramWaterScale')?.value) || 0.05,
                colormap: document.getElementById('demColormap')?.value || 'terrain',
                subtractWater: document.getElementById('paramSubtractWater')?.checked ?? true,
                satScale: parseInt(document.getElementById('paramSatScale')?.value) || 500,
                elevationCurve: activeCurvePreset || 'linear'
            };
        }

        /**
         * Collect all editable settings panel values into a flat object for persistence.
         * Includes DEM params, projection, rescale range, gridlines, curve, and DEM source.
         * @returns {Object} Full settings snapshot
         */
        function collectAllSettings() {
            const rescaleMin = document.getElementById('rescaleMin')?.value;
            const rescaleMax = document.getElementById('rescaleMax')?.value;
            return {
                dim:          parseInt(document.getElementById('paramDim')?.value) || 200,
                depth_scale:  parseFloat(document.getElementById('paramDepthScale')?.value) || 0.5,
                water_scale:  parseFloat(document.getElementById('paramWaterScale')?.value) || 0.05,
                height:       parseFloat(document.getElementById('paramHeight')?.value) || 10,
                base:         parseFloat(document.getElementById('paramBase')?.value) || 2,
                subtract_water: document.getElementById('paramSubtractWater')?.checked ?? true,
                sat_scale:    parseInt(document.getElementById('paramSatScale')?.value) || 500,
                colormap:     document.getElementById('demColormap')?.value || 'terrain',
                projection:   document.getElementById('paramProjection')?.value || 'none',
                rescale_min:  rescaleMin && rescaleMin !== '' ? parseFloat(rescaleMin) : null,
                rescale_max:  rescaleMax && rescaleMax !== '' ? parseFloat(rescaleMax) : null,
                gridlines_show:  document.getElementById('showGridlines')?.checked ?? false,
                gridlines_count: parseInt(document.getElementById('gridlineCount')?.value) || 5,
                elevation_curve: activeCurvePreset || 'linear',
                elevation_curve_points: curvePoints.map(p => [p.x, p.y]),
                dem_source:      document.getElementById('paramDemSource')?.value || 'local',
            };
        }

        /**
         * Apply a saved settings object back to all form controls, then redraw the curve.
         * @param {Object} s - Settings object as returned by `collectAllSettings()`
         */
        function applyAllSettings(s) {
            if (!s) return;
            const set = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };
            const setChk = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.checked = val; };

            set('paramDim', s.dim);
            set('paramDepthScale', s.depth_scale);
            set('paramWaterScale', s.water_scale);
            set('paramHeight', s.height);
            set('paramBase', s.base);
            setChk('paramSubtractWater', s.subtract_water);
            set('paramSatScale', s.sat_scale);
            set('demColormap', s.colormap);
            set('paramProjection', s.projection);
            if (s.rescale_min != null) set('rescaleMin', s.rescale_min);
            if (s.rescale_max != null) set('rescaleMax', s.rescale_max);
            setChk('showGridlines', s.gridlines_show);
            set('gridlineCount', s.gridlines_count);
            set('paramDemSource', s.dem_source);
            // Restore curve
            if (s.elevation_curve_points && Array.isArray(s.elevation_curve_points) && s.elevation_curve_points.length >= 2) {
                curvePoints = s.elevation_curve_points.map(p => ({ x: p[0], y: p[1] }));
                activeCurvePreset = s.elevation_curve || 'custom';
                if (typeof drawCurve === 'function') drawCurve();
            }
            // Update projection description
            const projSelect = document.getElementById('paramProjection');
            const projDesc = document.getElementById('projectionDescription');
            if (projSelect && projDesc) {
                const descs = {
                    'none': 'No correction — raw lat/lon grid displayed as-is.',
                    'cosine': 'Horizontal scaling by cos(latitude). Correct east-west distances.',
                    'mercator': 'Web Mercator — vertical stretching increases towards poles.',
                    'lambert': 'Lambert Cylindrical Equal-Area — preserves area at the cost of shape.',
                    'sinusoidal': 'Sinusoidal — each row scaled by cos(lat), centred on meridian.'
                };
                projDesc.textContent = descs[projSelect.value] || '';
            }
        }

        /**
         * Save all current DEM/viewer settings for the selected region to the server
         * via `POST /api/regions/{name}/settings`.
         * @returns {Promise<void>}
         */
        async function saveRegionSettings() {
            if (!selectedRegion) { showToast('Select a region first', 'warning'); return; }
            const settings = collectAllSettings();
            const statusEl = document.getElementById('saveSettingsStatus');
            if (statusEl) statusEl.textContent = 'Saving…';
            try {
                const resp = await fetch(`/api/regions/${encodeURIComponent(selectedRegion.name)}/settings`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings)
                });
                if (resp.ok) {
                    if (statusEl) { statusEl.textContent = 'Saved ✓'; setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000); }
                    showToast('Settings saved for ' + selectedRegion.name, 'success');
                } else {
                    const err = await resp.json();
                    showToast('Save failed: ' + (err.error || resp.status), 'error');
                    if (statusEl) statusEl.textContent = 'Error';
                }
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
                if (statusEl) statusEl.textContent = 'Error';
            }
        }

        /**
         * Fetch saved settings for a region via `GET /api/regions/{name}/settings` and apply them.
         * Falls back to default settings on 404 (no saved settings yet).
         * @param {string} regionName - Name of the region whose settings to load
         * @returns {Promise<void>}
         */
        async function loadAndApplyRegionSettings(regionName) {
            try {
                const resp = await fetch(`/api/regions/${encodeURIComponent(regionName)}/settings`);
                if (resp.ok) {
                    const data = await resp.json();
                    applyAllSettings(data.settings);
                    return true;
                }
            } catch (e) { /* network error — use defaults */ }
            return false;
        }

        /**
         * Show the save-preset dialog and focus its name input.
         */
        function showSavePresetDialog() {
            const dialog = document.getElementById('presetSaveDialog');
            const input = document.getElementById('newPresetName');
            if (dialog) {
                dialog.classList.remove('hidden');
                if (input) {
                    input.value = '';
                    input.focus();
                }
            }
        }

        /**
         * Hide the save-preset dialog.
         */
        function hideSavePresetDialog() {
            const dialog = document.getElementById('presetSaveDialog');
            if (dialog) dialog.classList.add('hidden');
        }

        /**
         * Read the name from `#newPresetName`, validate it, save current settings as a
         * user preset in `localStorage`, and close the dialog.
         */
        function saveNewPreset() {
            const input = document.getElementById('newPresetName');
            const name = input?.value?.trim();

            if (!name) {
                showToast('Enter a preset name', 'warning');
                return;
            }

            // Check if name already exists
            if (builtInPresets[name.toLowerCase()]) {
                showToast('Cannot overwrite built-in preset', 'error');
                return;
            }

            // Save preset
            userPresets[name] = getCurrentSettings();

            // Persist to localStorage
            try { localStorage.setItem('strm2stl_userPresets', JSON.stringify(userPresets)); } catch (_) { showToast('Could not save preset — storage full or unavailable', 'warning'); }

            // Update select
            updatePresetSelect();

            // Select the new preset
            const select = document.getElementById('presetSelect');
            if (select) select.value = 'user:' + name;

            hideSavePresetDialog();
            showToast(`Preset "${name}" saved!`, 'success');
        }

        /**
         * Delete the currently selected user preset from `localStorage` after confirmation.
         * Refuses to delete built-in presets.
         */
        function deleteSelectedPreset() {
            const select = document.getElementById('presetSelect');
            if (!select || !select.value) {
                showToast('Select a preset to delete', 'warning');
                return;
            }

            if (!select.value.startsWith('user:')) {
                showToast('Cannot delete built-in presets', 'warning');
                return;
            }

            const name = select.value.substring(5);
            if (confirm(`Delete preset "${name}"?`)) {
                delete userPresets[name];
                try { localStorage.setItem('strm2stl_userPresets', JSON.stringify(userPresets)); } catch (_) { showToast('Could not save preset — storage full or unavailable', 'warning'); }
                updatePresetSelect();
                showToast(`Preset "${name}" deleted`, 'info');
            }
        }

        // ============================================================
        // REGION RENDERING & NOTES
        // List/table views, continent grouping, notes modal.
        // ============================================================
        const CONTINENT_HIDDEN = new Set(); // continent names hidden by user

        /**
         * Heuristically determine which continent a lat/lon point falls on.
         * @param {number} lat - Latitude in degrees
         * @param {number} lon - Longitude in degrees
         * @returns {string} Continent name
         */
        function detectContinent(lat, lon) {
            if (lat < -60) return 'Antarctica';
            // Oceania first (avoids Asia overlap)
            if (lat >= -55 && lat <= -10 && lon >= 110 && lon <= 180) return 'Oceania';
            if (lat >= -10 && lat <= 0 && lon >= 130 && lon <= 180) return 'Oceania';
            // South America
            if (lat >= -56 && lat <= 13 && lon >= -82 && lon <= -34) return 'South America';
            // North America (includes Caribbean, Central America)
            if (lat >= 13 && lat <= 75 && lon >= -168 && lon <= -52) return 'North America';
            if (lat >= 8 && lat <= 28 && lon >= -90 && lon <= -52) return 'North America'; // Caribbean
            // Russia / North Asia
            if (lat >= 55 && lon >= 26 && lon <= 180) return 'Asia';
            // Asia (before Europe/Africa to handle Middle East correctly)
            if (lat >= -11 && lat <= 55 && lon >= 60 && lon <= 145) return 'Asia';
            if (lat >= 25 && lat <= 43 && lon >= 35 && lon <= 60) return 'Asia'; // Middle East
            // Africa
            if (lat >= -37 && lat <= 38 && lon >= -18 && lon <= 52) return 'Africa';
            // Europe
            if (lat >= 35 && lat <= 72 && lon >= -25 && lon <= 45) return 'Europe';
            return 'Other';
        }

        /**
         * Group an array of regions by continent using `detectContinent()`.
         * Regions with an explicit `label` field use that as the continent name.
         * Returned array is sorted in a fixed continent order.
         * @param {Array} regions - Array of region objects
         * @returns {Array<{continent: string, regions: Array}>}
         */
        function groupRegionsByContinent(regions) {
            const groups = {};
            const ORDER = ['North America','South America','Europe','Africa','Asia','Oceania','Antarctica','Other'];
            regions.forEach(region => {
                const lat = (region.north + region.south) / 2;
                const lon = (region.east + region.west) / 2;
                // Use explicit label if set, otherwise auto-detect
                const continent = (region.label && region.label.trim()) ? region.label.trim() : detectContinent(lat, lon);
                if (!groups[continent]) groups[continent] = [];
                groups[continent].push(region);
            });
            // Sort within each group alphabetically
            Object.values(groups).forEach(g => g.sort((a, b) => a.name.localeCompare(b.name)));
            // Return known groups in fixed order, then any custom labels alphabetically after
            const known = ORDER.filter(c => groups[c]).map(c => ({ continent: c, regions: groups[c] }));
            const custom = Object.keys(groups).filter(c => !ORDER.includes(c)).sort()
                .map(c => ({ continent: c, regions: groups[c] }));
            return [...known, ...custom];
        }

        /**
         * Render the sidebar coordinates list view, grouped by continent.
         * Also refreshes the expanded sidebar table if it is visible.
         */
        function renderCoordinatesList() {
            // If expanded table is visible, refresh it too
            if (sidebarState === 'expanded') renderSidebarTable();

            const list = document.getElementById('coordinatesList');
            if (!list) return;
            list.innerHTML = '';

            if (coordinatesData.length === 0) {
                list.innerHTML = '<div class="loading">No regions found. Draw a bbox on the map to create one.</div>';
                return;
            }

            const searchVal = (document.getElementById('coordSearch')?.value || '').toLowerCase();
            const filtered = searchVal
                ? coordinatesData.filter(r => r.name.toLowerCase().includes(searchVal))
                : coordinatesData;

            const groups = groupRegionsByContinent(filtered);

            const outerFrag = document.createDocumentFragment();

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
                    header.classList.toggle('collapsed');
                    body.classList.toggle('collapsed');
                });

                const body = document.createElement('div');
                body.className = 'continent-body-sidebar';
                if (isHidden) body.classList.add('collapsed');

                // Batch item nodes via DocumentFragment to avoid per-item layout thrash
                const itemFrag = document.createDocumentFragment();
                groupRegions.forEach(region => {
                    const originalIndex = coordinatesData.findIndex(r => r.name === region.name);
                    const hasNote = regionNotes[region.name] && regionNotes[region.name].trim() !== '';
                    const item = document.createElement('div');
                    item.className = 'coordinate-item';
                    item.dataset.regionName = region.name;
                    if (selectedRegion && selectedRegion.name === region.name) item.classList.add('selected');
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
                    item.onclick = () => selectCoordinate(originalIndex);
                    item.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectCoordinate(originalIndex); }
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
        }

        // =====================================================
        // REGIONS TABLE
        // =====================================================

        /**
         * Render `coordinatesData` into the `#regionsTableBody` table element.
         * Highlights the currently selected region row. Called after load or refresh.
         */
        function populateRegionsTable() {
            const tbody = document.getElementById('regionsTableBody');
            if (!tbody) return;

            tbody.innerHTML = '';

            if (!coordinatesData || coordinatesData.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;">No regions loaded</td></tr>';
                return;
            }

            coordinatesData.forEach((region, index) => {
                const tr = document.createElement('tr');
                tr.dataset.regionIndex = index;
                if (selectedRegion && selectedRegion.name === region.name) {
                    tr.classList.add('selected');
                }

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
        }

        /**
         * Navigate to the Edit view for the region at the given index in `coordinatesData`.
         * Called from inline `onclick` handlers in the regions table.
         * @param {number} index - Index into `coordinatesData`
         */
        function loadRegionFromTable(index) {
            if (index >= 0 && index < coordinatesData.length) {
                goToEdit(index);
            }
        }

        /**
         * Select a region and switch the main view to the map tab.
         * Called from inline `onclick` handlers in the regions table.
         * @param {number} index - Index into `coordinatesData`
         */
        function viewRegionOnMap(index) {
            if (index >= 0 && index < coordinatesData.length) {
                selectCoordinate(index);
                switchView('map');
            }
        }

        /**
         * Wire interactive behaviours for the regions table:
         * – live search filtering via `#regionsSearch`
         * – refresh button via `#refreshRegionsBtn`
         */
        function setupRegionsTable() {
            // Search filter
            const searchInput = document.getElementById('regionsSearch');
            if (searchInput) {
                searchInput.oninput = (e) => {
                    const query = e.target.value.toLowerCase();
                    const rows = document.querySelectorAll('#regionsTableBody tr');
                    rows.forEach(row => {
                        const text = row.textContent.toLowerCase();
                        row.style.display = text.includes(query) ? '' : 'none';
                    });
                };
            }

            // Refresh button
            const refreshBtn = document.getElementById('refreshRegionsBtn');
            if (refreshBtn) {
                refreshBtn.onclick = async () => {
                    await loadCoordinates();
                    populateRegionsTable();
                    showToast('Regions refreshed', 'success');
                };
            }
        }

        // =====================================================
        // REGION NOTES
        // =====================================================
        let regionNotes = {};
        let currentNotesRegion = null;

        /**
         * Load region notes from `localStorage` into `regionNotes` and wire
         * the notes modal close handlers (outside click and Escape key).
         */
        function initRegionNotes() {
            // Load notes from localStorage
            let saved = null;
            try { saved = localStorage.getItem('strm2stl_regionNotes'); } catch (_) {}
            if (saved) {
                try {
                    regionNotes = JSON.parse(saved);
                } catch (e) {
                    console.warn('Failed to load region notes:', e);
                }
            }

            // Close modal on outside click
            const modal = document.getElementById('regionNotesModal');
            if (modal) {
                modal.addEventListener('click', (e) => {
                    if (e.target === modal) {
                        hideNotesModal();
                    }
                });
            }

            // Close on Escape key
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                    hideNotesModal();
                }
            });
        }

        /**
         * Open the region notes modal pre-populated with any saved note for `regionName`.
         * Sets `currentNotesRegion` for use by `saveRegionNotes`.
         * @param {string} regionName - Name of the region to show notes for
         */
        function showNotesModal(regionName) {
            currentNotesRegion = regionName;
            const modal = document.getElementById('regionNotesModal');
            const nameSpan = document.getElementById('notesRegionName');
            const textarea = document.getElementById('notesTextarea');

            nameSpan.textContent = regionName;
            textarea.value = regionNotes[regionName] || '';
            modal.classList.remove('hidden');
            textarea.focus();

            // Escape key closes the modal; listener removed when modal hides
            if (!showNotesModal._escHandler) {
                showNotesModal._escHandler = (e) => {
                    if (e.key === 'Escape') hideNotesModal();
                };
            }
            document.addEventListener('keydown', showNotesModal._escHandler);
        }

        /**
         * Close the region notes modal and clear `currentNotesRegion`.
         */
        function hideNotesModal() {
            const modal = document.getElementById('regionNotesModal');
            modal.classList.add('hidden');
            currentNotesRegion = null;
            if (showNotesModal._escHandler) {
                document.removeEventListener('keydown', showNotesModal._escHandler);
            }
        }

        /**
         * Persist the note text from the notes modal textarea to `localStorage`.
         * Deletes the entry if the textarea is empty. Closes the modal and refreshes
         * the coordinates list.
         */
        function saveRegionNotes() {
            if (!currentNotesRegion) return;

            const textarea = document.getElementById('notesTextarea');
            const note = textarea.value.trim();

            if (note) {
                regionNotes[currentNotesRegion] = note;
            } else {
                delete regionNotes[currentNotesRegion];
            }

            try { localStorage.setItem('strm2stl_regionNotes', JSON.stringify(regionNotes)); } catch (_) { showToast('Could not save notes — storage full or unavailable', 'warning'); }
            hideNotesModal();
            renderCoordinatesList();
            showToast('Notes saved!', 'success');
        }

        // ============================================================
        // COMPARE VIEW
        // Side-by-side comparison of two regions with aligned DEM renders.
        // ============================================================
        let compareData = {
            left: { region: null, image: null },
            right: { region: null, image: null }
        };

        /**
         * Initialise the compare mode panel: populate left/right region selects
         * from `coordinatesData`, preserving any existing selections.
         */
        function initCompareMode() {
            // Wire inline layer selects once
            const leftSel  = document.getElementById('compareInlineLeft');
            const rightSel = document.getElementById('compareInlineRight');
            if (leftSel && !leftSel._wired) {
                leftSel._wired = true;
                leftSel.onchange  = () => renderCompareLayer('left');
                rightSel.onchange = () => renderCompareLayer('right');
            }
        }

        /**
         * Render the selected layer into one of the inline compare canvases.
         * Copies the already-loaded layer source canvas (DEM, water, sat, or combined).
         * @param {'left'|'right'} side
         */
        function renderCompareLayer(side) {
            const cap    = side.charAt(0).toUpperCase() + side.slice(1);
            const select = document.getElementById(`compareInline${cap}`);
            const canvas = document.getElementById(`compareInline${cap}Canvas`);
            if (!select || !canvas) return;

            const layer = select.value;

            // Map layer name → source canvas selector
            const sourceSelectors = {
                dem:      '#demImage canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay)',
                water:    '#waterMaskImage canvas',
                sat:      '#satelliteImage canvas',
                combined: '#combinedImage canvas',
            };
            const srcSelector = sourceSelectors[layer];
            const srcCanvas = srcSelector ? document.querySelector(srcSelector) : null;

            const ctx = canvas.getContext('2d');
            if (!srcCanvas || !srcCanvas.width || !srcCanvas.height) {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.fillStyle = '#444';
                ctx.font = '13px sans-serif';
                ctx.textAlign = 'center';
                canvas.width  = 300;
                canvas.height = 150;
                ctx.fillText('Load this layer first', 150, 80);
                return;
            }

            canvas.width  = srcCanvas.width;
            canvas.height = srcCanvas.height;
            ctx.drawImage(srcCanvas, 0, 0);
        }

        /**
         * Called when switching to the compare sub-tab — wire selects and render both sides.
         */
        function updateCompareCanvases() {
            initCompareMode();
            renderCompareLayer('left');
            renderCompareLayer('right');
        }

        /**
         * Load and render the DEM for one side of the compare panel.
         * @param {'left'|'right'} side - Which panel side to load
         * @returns {Promise<void>}
         */
        async function loadCompareRegion(side) {
            const cap = side.charAt(0).toUpperCase() + side.slice(1);
            const select = document.getElementById(`compare${cap}Region`);
            const nameSpan = document.getElementById(`compare${cap}Name`);
            const imageEl = document.getElementById(`compare${cap}Image`);
            const empty = document.getElementById(`compare${cap}Empty`);

            if (!select || !select.value) {
                if (nameSpan) nameSpan.textContent = '--';
                if (imageEl) imageEl.style.display = 'none';
                if (empty) { empty.textContent = 'Select a region to compare'; empty.style.display = 'block'; }
                compareData[side].region = null;
                return;
            }

            const regionIndex = parseInt(select.value);
            const region = coordinatesData[regionIndex];
            if (!region) return;

            if (nameSpan) nameSpan.textContent = region.name;
            if (empty) { empty.textContent = 'Loading…'; empty.style.display = 'block'; }
            if (imageEl) imageEl.style.display = 'none';

            try {
                const colormap = document.getElementById(`compare${cap}Colormap`)?.value || 'terrain';

                const params = new URLSearchParams({
                    north: region.north,
                    south: region.south,
                    east: region.east,
                    west: region.west,
                    dim: 200
                });

                const response = await fetch(`/api/terrain/dem?${params}`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                if (!data.dem_values || !data.dimensions) {
                    throw new Error(data.error || 'No DEM data returned');
                }

                let demVals = data.dem_values;
                let h = Number(data.dimensions[0]);
                let w = Number(data.dimensions[1]);
                if (Array.isArray(demVals[0])) {
                    h = demVals.length; w = demVals[0].length;
                    demVals = demVals.flat();
                }

                // Render client-side without touching global state
                const vmin = data.min_elevation !== undefined ? data.min_elevation :
                    demVals.filter(Number.isFinite).reduce((a, b) => a < b ? a : b, Infinity);
                const vmax = data.max_elevation !== undefined ? data.max_elevation :
                    demVals.filter(Number.isFinite).reduce((a, b) => a > b ? a : b, -Infinity);

                const offscreen = document.createElement('canvas');
                offscreen.width = w; offscreen.height = h;
                const ctx = offscreen.getContext('2d');
                const imgData = ctx.createImageData(w, h);
                const range = (vmax - vmin) || 1;
                for (let i = 0; i < w * h; i++) {
                    const t = Math.max(0, Math.min(1, (demVals[i] - vmin) / range));
                    const [r, g, b] = mapElevationToColor(t, colormap);
                    imgData.data[i * 4]     = Math.round((r || 0) * 255);
                    imgData.data[i * 4 + 1] = Math.round((g || 0) * 255);
                    imgData.data[i * 4 + 2] = Math.round((b || 0) * 255);
                    imgData.data[i * 4 + 3] = 255;
                }
                ctx.putImageData(imgData, 0, 0);

                if (imageEl) {
                    imageEl.src = offscreen.toDataURL();
                    imageEl.style.display = 'block';
                }
                if (empty) empty.style.display = 'none';
                compareData[side].region = region;
                compareData[side].image = data;
            } catch (error) {
                console.error('Compare load error:', error);
                if (empty) { empty.textContent = 'Error: ' + error.message; empty.style.display = 'block'; }
                if (imageEl) imageEl.style.display = 'none';
            }
        }

        /**
         * Apply the selected colormap to a compare panel and reload its DEM.
         * @param {'left'|'right'} side - Which compare panel side to update
         */
        function applyCompareColormap(side) {
            // Reload with new colormap
            loadCompareRegion(side);
        }

        /**
         * Update the exaggeration label text for a compare panel side and reload the DEM.
         * @param {'left'|'right'} side - Which compare panel side to update
         */
        function updateCompareExagLabel(side) {
            const exagInput = document.getElementById(`compare${side.charAt(0).toUpperCase() + side.slice(1)}Exag`);
            const exagLabel = document.getElementById(`compare${side.charAt(0).toUpperCase() + side.slice(1)}ExagLabel`);
            exagLabel.textContent = parseFloat(exagInput.value).toFixed(1) + 'x';
            // Reload with new exaggeration
            loadCompareRegion(side);
        }

        /**
         * Populate the region parameters table (`#regionParamsBody`) with editable
         * fields for the given region object and current DEM settings.
         * Renders a placeholder row when no region is provided.
         * @param {Object|null} region - Region object with north/south/east/west/name, or null
         */
        function updateRegionParamsTable(region) {
            const tbody = document.getElementById('regionParamsBody');
            if (!region) {
                tbody.innerHTML = '<tr><td colspan="2" style="color:#888;text-align:center;">Select a region</td></tr>';
                return;
            }

            const params = [
                { key: 'name', label: 'Name', value: region.name || '', type: 'text', readonly: true },
                { key: 'north', label: 'North', value: region.north || '', type: 'number', step: '0.0001' },
                { key: 'south', label: 'South', value: region.south || '', type: 'number', step: '0.0001' },
                { key: 'east', label: 'East', value: region.east || '', type: 'number', step: '0.0001' },
                { key: 'west', label: 'West', value: region.west || '', type: 'number', step: '0.0001' },
                { key: 'dim', label: 'Dimension', value: document.getElementById('paramDim').value || 200, type: 'number', min: 50, max: 1000 },
                { key: 'depth_scale', label: 'Depth Scale', value: document.getElementById('paramDepthScale').value || 0.5, type: 'number', step: '0.1' },
                { key: 'water_scale', label: 'Water Scale', value: document.getElementById('paramWaterScale').value || 0.05, type: 'number', step: '0.01' },
                { key: 'sat_scale', label: 'Satellite Scale', value: document.getElementById('paramSatScale').value || 500, type: 'number', min: 100, max: 5000 }
            ];

            tbody.innerHTML = params.map(p => `
                <tr>
                    <td>${p.label}</td>
                    <td>
                        <input type="${p.type}" 
                               data-param="${p.key}" 
                               value="${p.value}" 
                               ${p.readonly ? 'readonly' : ''}
                               ${p.step ? `step="${p.step}"` : ''}
                               ${p.min !== undefined ? `min="${p.min}"` : ''}
                               ${p.max !== undefined ? `max="${p.max}"` : ''}
                               style="width:100%;background:#404040;color:#fff;border:1px solid #555;padding:4px;border-radius:3px;">
                    </td>
                </tr>
            `).join('');
        }

        /**
         * Read all `data-param` inputs from `#regionParamsBody` and sync their values
         * back to the main DEM control fields and `selectedRegion` coordinates.
         * Called when the user clicks the Apply button in the compare/region params panel.
         */
        function applyRegionParams() {
            const tbody = document.getElementById('regionParamsBody');
            const inputs = tbody.querySelectorAll('input[data-param]');

            inputs.forEach(input => {
                const param = input.dataset.param;
                const value = input.value;

                // Sync to main controls
                switch (param) {
                    case 'dim':
                        document.getElementById('paramDim').value = value;
                        break;
                    case 'depth_scale':
                        document.getElementById('paramDepthScale').value = value;
                        break;
                    case 'water_scale':
                        document.getElementById('paramWaterScale').value = value;
                        break;
                    case 'sat_scale':
                        document.getElementById('paramSatScale').value = value;
                        break;
                    case 'north':
                    case 'south':
                    case 'east':
                    case 'west':
                        // Update the selected region coordinates
                        if (selectedRegion) {
                            selectedRegion[param] = parseFloat(value);
                        }
                        break;
                }
            });

            showToast('Parameters applied! Loading layers...', 'success');
            loadAllLayers();
        }

        // =====================================================
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
                const isHidden = CONTINENT_HIDDEN.has(continent);

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
         * Updates `CONTINENT_HIDDEN` and re-renders the panel.
         * @param {string} continent - Continent name key
         * @param {HTMLElement} eyeEl - The eye icon element to update visually
         */
        function toggleContinentVisibility(continent, eyeEl) {
            if (CONTINENT_HIDDEN.has(continent)) {
                CONTINENT_HIDDEN.delete(continent);
                eyeEl.classList.remove('hidden-continent');
            } else {
                CONTINENT_HIDDEN.add(continent);
                eyeEl.classList.add('hidden-continent');
            }
            // Toggle map rectangles for regions in this continent
            if (preloadedLayer) {
                preloadedLayer.eachLayer(layer => {
                    if (layer._continentName === continent) {
                        if (CONTINENT_HIDDEN.has(continent)) {
                            layer.setStyle({ opacity: 0, fillOpacity: 0 });
                        } else {
                            layer.setStyle({ opacity: 1, fillOpacity: 0.15 });
                        }
                    }
                });
            }
        }

        /**
         * Make a regions panel table cell inline-editable.
         * Creates an input inside the cell; saves on blur or Enter, reverts on Escape.
         * @param {HTMLTableCellElement} td - The table cell to make editable
         */
        function editRegionCell(td) {
            if (td.querySelector('input')) return; // Already editing

            const originalValue = td.textContent;
            const field = td.dataset.field;
            const row = td.closest('tr');
            const regionIdx = parseInt(row.dataset.regionIdx);

            const input = document.createElement('input');
            input.type = field === 'name' ? 'text' : 'number';
            input.step = '0.0001';
            input.value = originalValue;

            td.textContent = '';
            td.appendChild(input);
            input.focus();
            input.select();

            const finishEdit = () => {
                const newValue = input.value;
                td.textContent = newValue;

                // Update the data
                if (coordinatesData[regionIdx]) {
                    if (field === 'name') {
                        coordinatesData[regionIdx].name = newValue;
                    } else {
                        coordinatesData[regionIdx][field] = parseFloat(newValue);
                    }
                    showToast(`Updated ${field} for ${coordinatesData[regionIdx].name}`, 'success');
                }
            };

            input.onblur = finishEdit;
            input.onkeydown = (e) => {
                if (e.key === 'Enter') finishEdit();
                if (e.key === 'Escape') {
                    td.textContent = originalValue;
                }
            };
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
                await loadWaterMask();

                // Render combined view automatically
                switchDemSubtab('combined');
                renderCombinedView();

            } catch (error) {
                console.error('Error loading layers:', error);
                showToast('Error loading layers: ' + error.message, 'error');
            }
        }

        // Unified opacity values (used by both stacked and combined views)
        let waterOpacity = 0.7;
        let satOpacity = 0.5;

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
                            renderCombinedView();
                        }
                    });
                }
            });
        }

        /**
         * Re-render the DEM canvas from cached `lastDemData` using the current colormap.
         * No server request is made. Redraws colorbar, histogram, and gridlines.
         */
        function recolorDEM() {
            if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
                console.log('No DEM data cached, cannot recolor');
                return;
            }
            const colormap = document.getElementById('demColormap').value;
            // If auto-rescale is enabled, reset vmin/vmax to the actual data range
            if (document.getElementById('autoRescale')?.checked) {
                let calcMin = Infinity, calcMax = -Infinity;
                for (const v of lastDemData.values) {
                    if (isFinite(v)) { if (v < calcMin) calcMin = v; if (v > calcMax) calcMax = v; }
                }
                if (isFinite(calcMin) && isFinite(calcMax)) {
                    lastDemData.vmin = calcMin;
                    lastDemData.vmax = calcMax;
                    document.getElementById('rescaleMin').value = Math.floor(calcMin);
                    document.getElementById('rescaleMax').value = Math.ceil(calcMax);
                }
            }
            const { values, width, height, vmin, vmax } = lastDemData;

            const rawCanvas = renderDEMCanvas(values, width, height, colormap, vmin, vmax);
            const canvas = applyProjection(rawCanvas, currentDemBbox);
            const container = document.getElementById('demImage');
            container.innerHTML = '';
            container.appendChild(canvas);
            canvas.style.width = '100%';
            canvas.style.height = 'auto';

            // Redraw colorbar with new colormap
            drawColorbar(vmin, vmax, colormap);

            // Redraw histogram with new colormap colors
            drawHistogram(values);

            // Re-enable zoom/pan
            enableZoomAndPan(canvas);

            // Redraw gridlines and stacked layers after canvas is laid out
            requestAnimationFrame(() => {
                drawGridlinesOverlay('demImage');
                drawGridlinesOverlay('inlineLayersCanvas');
                updateStackedLayers();
            });
        }

        /**
         * Rescale DEM display range client-side (no server request).
         * Updates `lastDemData.vmin/vmax` and redraws the canvas, colorbar, and histogram.
         * @param {number} newVmin - New minimum elevation value
         * @param {number} newVmax - New maximum elevation value
         */
        function rescaleDEM(newVmin, newVmax) {
            if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
                showToast('No DEM data loaded', 'warning');
                return;
            }

            const colormap = document.getElementById('demColormap').value;
            const { values, width, height } = lastDemData;

            // Use custom vmin/vmax
            const vmin = newVmin;
            const vmax = newVmax;

            // Update lastDemData with new range
            lastDemData.vmin = vmin;
            lastDemData.vmax = vmax;

            const rawCanvas = renderDEMCanvas(values, width, height, colormap, vmin, vmax);
            const canvas = applyProjection(rawCanvas, currentDemBbox);
            const container = document.getElementById('demImage');
            container.innerHTML = '';
            container.appendChild(canvas);
            canvas.style.width = '100%';
            canvas.style.height = 'auto';

            // Redraw colorbar with new range
            drawColorbar(vmin, vmax, colormap);

            // Redraw histogram
            drawHistogram(values);

            // Re-enable zoom/pan
            enableZoomAndPan(canvas);

            // Update stacked layers
            requestAnimationFrame(() => updateStackedLayers());

            showToast(`Rescaled to ${vmin.toFixed(0)}m - ${vmax.toFixed(0)}m`, 'success');
        }

        /**
         * Reset the DEM display range to the auto-computed min/max from the data.
         * Updates the rescale input fields and calls `rescaleDEM`.
         */
        function resetRescale() {
            if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
                showToast('No DEM data loaded', 'warning');
                return;
            }

            // Recalculate auto vmin/vmax from values
            const values = lastDemData.values;
            let calcMin = Infinity, calcMax = -Infinity;
            for (const v of values) {
                if (isFinite(v)) {
                    if (v < calcMin) calcMin = v;
                    if (v > calcMax) calcMax = v;
                }
            }

            // Update inputs
            document.getElementById('rescaleMin').value = Math.floor(calcMin);
            document.getElementById('rescaleMax').value = Math.ceil(calcMax);

            // Apply
            rescaleDEM(calcMin, calcMax);
            showToast('Reset to auto range', 'info');
        }

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
                const response = await fetch('/api/regions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(regionData)
                });

                const result = await response.json();

                if (response.ok) {
                    showToast(`Region "${regionName}" saved successfully!`, 'success');
                    loadCoordinates(); // Reload coordinates
                    document.getElementById('regionName').value = '';
                    if (regionLabelInput) regionLabelInput.value = '';
                } else {
                    showToast('Error saving region: ' + (result.error || result.detail), 'error');
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
                let data = null;

                {
                    const response = await fetch(`/api/terrain/dem?${params}`, { signal });
                    const rawText = await response.text();
                    try {
                        data = rawText ? JSON.parse(rawText) : {};
                    } catch (parseErr) {
                        console.error('Failed to parse /api/terrain/dem response as JSON:', parseErr, rawText);
                        document.getElementById('demImage').innerHTML = `<p>Failed to load DEM: invalid server response</p>`;
                        layerStatus.dem = 'error';
                        updateLayerStatusIndicators();
                        showToast('Failed to load DEM: invalid server response', 'error');
                        return;
                    }
                    if (!response.ok) {
                        const errMsg = data && data.error ? data.error : `HTTP ${response.status}: ${response.statusText}`;
                        console.error('Server returned error for /api/terrain/dem:', response.status, response.statusText, data);
                        { const _p = document.createElement('p'); _p.textContent = `Error: ${errMsg}`; document.getElementById('demImage').replaceChildren(_p); }
                        layerStatus.dem = 'error';
                        updateLayerStatusIndicators();
                        showToast('Failed to load DEM: ' + errMsg, 'error');
                        return;
                    }
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

        /**
         * Reload the DEM at 400px resolution (high detail).
         * Delegates to `loadDEM(true)`.
         * @returns {Promise<void>}
         */
        async function loadHighResDEM() {
            await loadDEM(true);
        }

        // Current bounding box for gridlines (updated when DEM loads)
        let currentDemBbox = null;

        // Rendering helpers
        // lastDemData is declared at top of script

        /**
         * Draw lat/lon gridlines with axis tick labels on a DEM canvas.
         * Respects the current projection (none/mercator/cosine/sinusoidal/lambert).
         * Removes the overlay if the `#showGridlines` checkbox is unchecked.
         * @param {string} [containerId='demImage'] - ID of the container holding the canvas
         */
        function drawGridlinesOverlay(containerId = 'demImage') {
            const container = document.getElementById(containerId);
            if (!container || !currentDemBbox) return;

            const canvas = container.querySelector('canvas:not(.dem-gridlines-overlay)');
            if (!canvas) return;

            const showGridlines = document.getElementById('showGridlines');
            if (!showGridlines || !showGridlines.checked) {
                const existing = container.querySelector('.dem-gridlines-overlay');
                if (existing) existing.remove();
                return;
            }

            const { north, south, east, west } = currentDemBbox;
            const latRange = north - south;
            const lonRange = east - west;

            // Get current projection
            const projection = document.getElementById('paramProjection')?.value || 'none';
            const toRad = d => d * Math.PI / 180;
            const mercY = l => Math.log(Math.tan(Math.PI / 4 + toRad(Math.max(-85, Math.min(85, l))) / 2));
            const mercN = mercY(Math.min(85, north));
            const mercS = mercY(Math.max(-85, south));
            const mercRange = mercN - mercS;

            // Map geographic lat/lon to projected canvas pixel fraction [0..1]
            /**
             * Convert lat/lon to canvas fractions [0,1] under the active projection.
             * @param {number} lat @param {number} lon
             * @returns {{xFrac: number|null, yFrac: number}} Fractions; xFrac null for sinusoidal
             */
            function geoToFrac(lat, lon) {
                let xFrac = (lon - west) / lonRange;

                let yFrac;
                switch (projection) {
                    case 'mercator': {
                        const my = mercY(lat);
                        yFrac = (mercN - my) / mercRange;
                        break;
                    }
                    case 'sinusoidal': {
                        // Sinusoidal: x is scaled per-row by cos(lat). For gridlines we can't draw
                        // straight vertical lines (they curve), so just draw horizontal lines.
                        yFrac = (north - lat) / latRange;
                        // xFrac remains linear — sinusoidal has curved meridians, label lat only
                        xFrac = null; // signal: skip vertical lines
                        break;
                    }
                    default:
                        yFrac = (north - lat) / latRange;
                }

                return { xFrac, yFrac };
            }

            // Create or get overlay canvas
            let overlay = container.querySelector('.dem-gridlines-overlay');
            if (!overlay) {
                overlay = document.createElement('canvas');
                overlay.className = 'dem-gridlines-overlay';
                container.appendChild(overlay);
            }

            container.style.position = 'relative';

            const canvasRect = canvas.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            const offsetLeft = canvasRect.left - containerRect.left;
            const offsetTop = canvasRect.top - containerRect.top;

            overlay.width = canvasRect.width;
            overlay.height = canvasRect.height;
            overlay.style.position = 'absolute';
            overlay.style.left = offsetLeft + 'px';
            overlay.style.top = offsetTop + 'px';
            overlay.style.width = canvasRect.width + 'px';
            overlay.style.height = canvasRect.height + 'px';
            overlay.style.pointerEvents = 'none';

            const ctx = overlay.getContext('2d');
            ctx.clearRect(0, 0, overlay.width, overlay.height);

            const gridCount = parseInt(document.getElementById('gridlineCount')?.value || '5');
            const W = overlay.width, H = overlay.height;

            // For cosine/lambert: content is centred in the full-width canvas with horizontal padding.
            // Compute the same xOffset/contentW used by applyProjection so gridlines align.
            const midLat = ((north + south) / 2) * Math.PI / 180;
            let xOffset = 0, contentW = W;
            if (projection === 'cosine') {
                contentW = Math.max(1, Math.round(W * Math.cos(midLat)));
                xOffset = Math.floor((W - contentW) / 2);
            } else if (projection === 'lambert') {
                contentW = Math.max(1, Math.round(W * Math.cos(midLat)));
                xOffset = Math.floor((W - contentW) / 2);
            }

            ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
            ctx.lineWidth = 1;
            ctx.font = '11px sans-serif';
            ctx.fillStyle = '#fff';
            ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
            ctx.shadowBlur = 2;

            // Vertical gridlines (longitude) — straight vertical lines for all projections except sinusoidal
            if (projection !== 'sinusoidal') {
                for (let i = 0; i <= gridCount; i++) {
                    const lon = west + (i / gridCount) * lonRange;
                    const xFrac = i / gridCount;
                    // Map into the content region [xOffset .. xOffset+contentW]
                    const x = xOffset + xFrac * contentW;

                    ctx.beginPath();
                    ctx.setLineDash([4, 4]);
                    ctx.moveTo(x, 0);
                    ctx.lineTo(x, H);
                    ctx.stroke();
                    ctx.setLineDash([]);

                    const label = lon.toFixed(2) + '°';
                    const textWidth = ctx.measureText(label).width;
                    const labelX = Math.max(textWidth / 2, Math.min(x, W - textWidth / 2));
                    ctx.fillText(label, labelX - textWidth / 2, H - 5);
                }
            } else {
                // Sinusoidal: draw curved meridians (polyline per longitude)
                for (let i = 0; i <= gridCount; i++) {
                    const lon = west + (i / gridCount) * lonRange;
                    ctx.beginPath();
                    ctx.setLineDash([4, 4]);
                    let first = true;
                    for (let row = 0; row <= H; row++) {
                        const lat = north - (row / H) * latRange;
                        const scale = Math.cos(toRad(lat));
                        const xFrac = 0.5 + (lon - (west + lonRange / 2)) / lonRange * scale;
                        const x = xFrac * W;
                        const y = row;
                        if (first) { ctx.moveTo(x, y); first = false; }
                        else ctx.lineTo(x, y);
                    }
                    ctx.stroke();
                    ctx.setLineDash([]);
                }
            }

            // Horizontal gridlines (latitude) — y-position depends on projection.
            // For sinusoidal the line width tapers with cos(lat) to match the projection boundary.
            for (let i = 0; i <= gridCount; i++) {
                const lat = north - (i / gridCount) * latRange;
                const { yFrac } = geoToFrac(lat, west);
                if (yFrac < 0 || yFrac > 1) continue;
                const y = yFrac * H;

                let lineX0, lineX1;
                if (projection === 'sinusoidal') {
                    const cosLat = Math.cos(toRad(lat));
                    lineX0 = W * (0.5 - 0.5 * cosLat);
                    lineX1 = W * (0.5 + 0.5 * cosLat);
                } else {
                    lineX0 = xOffset;
                    lineX1 = xOffset + contentW;
                }

                ctx.beginPath();
                ctx.setLineDash([4, 4]);
                ctx.moveTo(lineX0, y);
                ctx.lineTo(lineX1, y);
                ctx.stroke();
                ctx.setLineDash([]);

                const label = lat.toFixed(2) + '°';
                ctx.fillText(label, lineX0 + 5, y + 4);
            }

            // Border — curved outline for sinusoidal, rectangle for others
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.6)';
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            if (projection === 'sinusoidal') {
                // Trace left edge top→bottom, then right edge bottom→top
                ctx.beginPath();
                for (let row = 0; row <= H; row++) {
                    const lat = north - (row / H) * latRange;
                    const x = W * (0.5 - 0.5 * Math.cos(toRad(lat)));
                    if (row === 0) ctx.moveTo(x, 0); else ctx.lineTo(x, row);
                }
                for (let row = H; row >= 0; row--) {
                    const lat = north - (row / H) * latRange;
                    const x = W * (0.5 + 0.5 * Math.cos(toRad(lat)));
                    ctx.lineTo(x, row);
                }
                ctx.closePath();
                ctx.stroke();
            } else {
                ctx.strokeRect(xOffset, 0, contentW, H);
            }
        }

        /**
         * Apply a client-side map projection to a rendered canvas.
         * Returns a new (or the same) canvas transformed for the chosen projection.
         */
        function applyProjection(srcCanvas, bbox) {
            const projection = document.getElementById('paramProjection')?.value || 'none';
            if (!projection || projection === 'none') return srcCanvas;
            if (!bbox) return srcCanvas;

            const W = srcCanvas.width, H = srcCanvas.height;
            const { north, south, east, west } = bbox;
            const latRange = north - south;
            const lonRange = east - west;
            if (!latRange || !lonRange) return srcCanvas;
            const midLat = ((north + south) / 2) * Math.PI / 180;

            if (projection === 'cosine') {
                // Scale width by cos(midLat) to correct east-west distances.
                // Pad to full W so CSS width:100% doesn't stretch it back.
                const newW = Math.max(1, Math.round(W * Math.cos(midLat)));
                const dst = document.createElement('canvas');
                dst.width = W; dst.height = H;
                const offsetX = Math.floor((W - newW) / 2);
                dst.getContext('2d').drawImage(srcCanvas, 0, 0, W, H, offsetX, 0, newW, H);
                return dst;
            }

            if (projection === 'mercator') {
                const toRad = d => d * Math.PI / 180;
                const mercY = l => Math.log(Math.tan(Math.PI / 4 + toRad(l) / 2));
                const yN = mercY(Math.min(85, north)), yS = mercY(Math.max(-85, south));
                const yRange = yN - yS;
                if (Math.abs(yRange) < 1e-10) return srcCanvas;
                const srcCtx = srcCanvas.getContext('2d');
                const srcImg = srcCtx.getImageData(0, 0, W, H);
                const dst = document.createElement('canvas');
                dst.width = W; dst.height = H;
                const dstCtx = dst.getContext('2d');
                const dstImg = dstCtx.createImageData(W, H);
                for (let dstY = 0; dstY < H; dstY++) {
                    const t = dstY / (H - 1);
                    const mv = yN - t * yRange;
                    const lat = (2 * Math.atan(Math.exp(mv)) - Math.PI / 2) * 180 / Math.PI;
                    const srcY = Math.round((north - lat) / latRange * (H - 1));
                    if (srcY < 0 || srcY >= H) continue;
                    const dstBase = dstY * W * 4, srcBase = srcY * W * 4;
                    dstImg.data.set(srcImg.data.subarray(srcBase, srcBase + W * 4), dstBase);
                }
                dstCtx.putImageData(dstImg, 0, 0);
                return dst;
            }

            if (projection === 'lambert') {
                // Lambert cylindrical equal-area: scale height by 1/cos(midLat), width by cos(midLat).
                // Pad to full W so CSS width:100% doesn't stretch it back.
                const cosLat = Math.cos(midLat);
                const newW = Math.max(1, Math.round(W * cosLat));
                const newH = Math.max(1, Math.round(H / cosLat));
                const dst = document.createElement('canvas');
                dst.width = W; dst.height = newH;
                const offsetX = Math.floor((W - newW) / 2);
                dst.getContext('2d').drawImage(srcCanvas, 0, 0, W, H, offsetX, 0, newW, newH);
                return dst;
            }

            if (projection === 'sinusoidal') {
                // Sinusoidal: each row is scaled by cos(lat_row) and centred
                const srcCtx = srcCanvas.getContext('2d');
                const srcImg = srcCtx.getImageData(0, 0, W, H);
                const dst = document.createElement('canvas');
                dst.width = W; dst.height = H;
                const dstCtx = dst.getContext('2d');
                const dstImg = dstCtx.createImageData(W, H);
                for (let y = 0; y < H; y++) {
                    const lat = north - (y / (H - 1)) * latRange;
                    const scale = Math.cos(lat * Math.PI / 180);
                    const rowW = Math.max(1, Math.round(W * scale));
                    const offset = Math.round((W - rowW) / 2);
                    const srcBase = y * W * 4;
                    const dstBase = y * W * 4;
                    // Sample src row at scaled positions
                    for (let dstX = offset; dstX < offset + rowW && dstX < W; dstX++) {
                        const srcX = Math.round((dstX - offset) / rowW * (W - 1));
                        const si = srcBase + srcX * 4, di = dstBase + dstX * 4;
                        dstImg.data[di] = srcImg.data[si];
                        dstImg.data[di+1] = srcImg.data[si+1];
                        dstImg.data[di+2] = srcImg.data[si+2];
                        dstImg.data[di+3] = srcImg.data[si+3];
                    }
                }
                dstCtx.putImageData(dstImg, 0, 0);
                return dst;
            }

            return srcCanvas;
        }

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

            // Lock in the stable coordinate system for the curve editor.
            // If the curve already has a coordinate system (same region, different dim), re-normalize
            // the existing control points so they stay at the same absolute elevation positions.
            if (curveDataVmin !== null && curveDataVmax !== null &&
                (curveDataVmin !== vmin || curveDataVmax !== vmax)) {
                const oldMin = curveDataVmin, oldRange = (curveDataVmax - curveDataVmin) || 1;
                const newRange = (vmax - vmin) || 1;
                curvePoints = curvePoints.map(p => {
                    const absElev = oldMin + p.x * oldRange;
                    const newX = Math.max(0, Math.min(1, (absElev - vmin) / newRange));
                    return { x: newX, y: p.y };
                });
            }
            curveDataVmin = vmin;
            curveDataVmax = vmax;

            // Auto-insert a sea level control point if the region has sub-zero elevations
            if (vmin < 0 && vmax > 0) {
                const slX = Math.max(0.02, Math.min(0.98, (0 - vmin) / ((vmax - vmin) || 1)));
                // Only add if no existing point is near sea level
                const nearSL = curvePoints.some(p => Math.abs(p.x - slX) < 0.04);
                if (!nearSL) {
                    // Interpolate Y from current curve at sea level position
                    const slY = slX; // linear default
                    addCurvePoint(slX, slY);
                }
            }
            // Redraw curve to update sea level marker line
            if (typeof drawCurve === 'function') drawCurve();

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

        /**
         * Render a satellite or land-use pixel array to a canvas using the viridis colormap.
         * Uses a pre-computed 256-entry LUT for performance.
         * @param {number[]} values - Flat array of pixel intensity values (row-major)
         * @param {number} width - Canvas width in pixels
         * @param {number} height - Canvas height in pixels
         * @returns {HTMLCanvasElement} The rendered canvas element
         */
        function renderSatelliteCanvas(values, width, height) {
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, width, height);
            const img = ctx.createImageData(width, height);
            const data = img.data;
            const flat = Array.isArray(values) ? values : [];
            const len = flat.length;

            // OPTIMIZATION: Find min/max efficiently with single pass
            let vmin = Infinity, vmax = -Infinity;
            for (let i = 0; i < len; i++) {
                const v = flat[i];
                if (Number.isFinite(v)) {
                    if (v < vmin) vmin = v;
                    if (v > vmax) vmax = v;
                }
            }
            if (vmin === Infinity) vmin = 0;
            if (vmax === -Infinity) vmax = 1;

            const range = (vmax - vmin) || 1;
            const invRange = 1 / range;

            // OPTIMIZATION: Pre-compute viridis color lookup table
            const colorLUT = new Uint8Array(256 * 3);
            for (let i = 0; i < 256; i++) {
                const t = i / 255;
                const [r, g, b] = mapElevationToColor(t, 'viridis');
                colorLUT[i * 3] = Math.round(r * 255);
                colorLUT[i * 3 + 1] = Math.round(g * 255);
                colorLUT[i * 3 + 2] = Math.round(b * 255);
            }

            const total = width * height;
            for (let i = 0; i < total; i++) {
                const v = (i < len && Number.isFinite(flat[i])) ? flat[i] : vmin;
                const t = (v - vmin) * invRange;
                const tClamped = t < 0 ? 0 : (t > 1 ? 1 : t);
                const lutIdx = Math.round(tClamped * 255) * 3;
                const idx = i << 2;
                data[idx] = colorLUT[lutIdx];
                data[idx + 1] = colorLUT[lutIdx + 1];
                data[idx + 2] = colorLUT[lutIdx + 2];
                data[idx + 3] = 255;
            }
            ctx.putImageData(img, 0, 0);
            canvas.style.maxWidth = '100%';
            canvas.style.height = 'auto';
            return canvas;
        }

        /**
         * Map a normalised elevation value (0–1) to an RGB triple using a named colormap.
         * Supports 'jet', 'rainbow', 'viridis', 'hot', 'gray', and the default terrain scheme.
         * This is a local duplicate of the same function in colors.js; this copy is the one used.
         * @param {number} t - Normalised elevation in [0, 1]
         * @param {string} cmap - Colormap name
         * @returns {[number, number, number]} RGB values each in [0, 1]
         */
        function mapElevationToColor(t, cmap) {
            // t expected in [0,1]
            t = Math.max(0, Math.min(1, t));
            // alias common misspelling
            if (cmap === 'raindow') cmap = 'rainbow';
            if (cmap === 'jet') {
                const clip = x => Math.max(0, Math.min(1, x));
                const r = clip(1.5 - Math.abs(4 * t - 3));
                const g = clip(1.5 - Math.abs(4 * t - 2));
                const b = clip(1.5 - Math.abs(4 * t - 1));
                return [r, g, b];
            }
            if (cmap === 'rainbow') {
                // map t to hue from blue (~0.66) to red (0)
                const h = 0.66 * (1 - t);
                return hslToRgb(h, 1, 0.5);
            }
            if (cmap === 'viridis') {
                // simple viridis-ish approximation using HSL
                const h = 0.7 - 0.7 * t; // from purple to yellow
                const s = 0.9;
                const l = 0.5;
                return hslToRgb(h, s, l);
            } else if (cmap === 'hot') {
                // black -> red -> yellow -> white
                const r = Math.min(1, 3 * t);
                const g = Math.min(1, Math.max(0, 3 * t - 1));
                const b = Math.min(1, Math.max(0, 3 * t - 2));
                return [r, g, b];
            } else if (cmap === 'gray') {
                return [t, t, t];
            }
            // default: terrain-like (green -> brown -> white)
            if (t < 0.4) {
                // green shades
                const tt = t / 0.4;
                return [0.0 * (1 - tt) + 0.4 * tt, 0.3 * (1 - tt) + 0.25 * tt + 0.45 * tt, 0.0 + 0.0 * tt];
            } else if (t < 0.8) {
                const tt = (t - 0.4) / 0.4;
                return [0.4 * (1 - tt) + 0.55 * tt, 0.6 * (1 - tt) + 0.45 * tt, 0.2 * (1 - tt) + 0.15 * tt];
            } else {
                const tt = (t - 0.8) / 0.2;
                return [0.55 * (1 - tt) + 0.9 * tt, 0.45 * (1 - tt) + 0.9 * tt, 0.15 * (1 - tt) + 0.9 * tt];
            }
        }

        /**
         * Draw N/S/E/W coordinate labels on the axes overlay element inside `#demImage`.
         * Creates the overlay element if it does not yet exist.
         * Accepts either four separate arguments or a single bounding-box object with
         * `north`/`south`/`east`/`west` properties.
         * @param {number|Object} north - Northern latitude, or a bbox object
         * @param {number} [south] - Southern latitude
         * @param {number} [east] - Eastern longitude
         * @param {number} [west] - Western longitude
         */
        function updateAxesOverlay(north, south, east, west) {
            // Accept either (north, south, east, west) or a single bounds object
            let N, S, E, W;
            if (north && typeof north === 'object') {
                const b = north;
                N = b.north ?? b.N ?? b.n ?? null;
                S = b.south ?? b.S ?? b.s ?? null;
                E = b.east ?? b.E ?? b.e ?? null;
                W = b.west ?? b.W ?? b.w ?? null;
            } else {
                N = north; S = south; E = east; W = west;
            }

            const container = document.getElementById('demImage');
            if (!container) return;
            let overlay = container.querySelector('.dem-axes-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'dem-axes-overlay';
                overlay.innerHTML = `
                    <div class="top"></div>
                    <div class="bottom"></div>
                    <div class="left"></div>
                    <div class="right"></div>
                `;
                container.appendChild(overlay);
            }

            const fmt = (v) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(5);
            overlay.querySelector('.top').textContent = `N: ${fmt(N)}`;
            overlay.querySelector('.bottom').textContent = `S: ${fmt(S)}`;
            overlay.querySelector('.left').textContent = `W: ${fmt(W)}`;
            overlay.querySelector('.right').textContent = `E: ${fmt(E)}`;
        }

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

        /**
         * Convert HSL colour values to an RGB triple.
         * All parameters and return values are normalised to [0, 1].
         * This is a local duplicate of the same function in colors.js; this copy is the one used.
         * @param {number} h - Hue in [0, 1]
         * @param {number} s - Saturation in [0, 1]
         * @param {number} l - Lightness in [0, 1]
         * @returns {[number, number, number]} RGB values each in [0, 1]
         */
        function hslToRgb(h, s, l) {
            // h in [0,1], s,l in [0,1]
            let r, g, b;
            if (s === 0) {
                r = g = b = l; // achromatic
            } else {
                const hue2rgb = (p, q, t) => {
                    if (t < 0) t += 1;
                    if (t > 1) t -= 1;
                    if (t < 1 / 6) return p + (q - p) * 6 * t;
                    if (t < 1 / 2) return q;
                    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
                    return p;
                };
                const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
                const p = 2 * l - q;
                r = hue2rgb(p, q, h + 1 / 3);
                g = hue2rgb(p, q, h);
                b = hue2rgb(p, q, h - 1 / 3);
            }
            return [r, g, b];
        }

        /**
         * Render a compact colour-gradient bar into the `#colorbar` element.
         * The bar is 256 × 18 px and covers the full colormap range from `min` to `max`.
         * Sets a `title` attribute describing the elevation range.
         * @param {number} min - Minimum elevation value (metres)
         * @param {number} max - Maximum elevation value (metres)
         * @param {string} colormap - Colormap name used for gradient colours
         */
        function drawColorbar(min, max, colormap) {
            const bar = document.getElementById('colorbar');
            if (!bar) return;
            bar.innerHTML = '';
            const canvas = document.createElement('canvas');
            canvas.width  = Math.max(64, bar.clientWidth  || 256);
            canvas.height = 18;
            const ctx = canvas.getContext('2d');
            const img = ctx.createImageData(256, 18);
            for (let x = 0; x < 256; x++) {
                const t = x / 255;
                const [r, g, b] = mapElevationToColor(t, colormap);
                for (let y = 0; y < 18; y++) {
                    const idx = (y * 256 + x) * 4;
                    img.data[idx]     = Math.round(r * 255);
                    img.data[idx + 1] = Math.round(g * 255);
                    img.data[idx + 2] = Math.round(b * 255);
                    img.data[idx + 3] = 255;
                }
            }
            ctx.putImageData(img, 0, 0);
            canvas.style.width = '100%';
            canvas.style.height = '18px';
            // Update title with range
            bar.title = `Colorbar: ${Math.round(min)} m (left) → ${Math.round(max)} m (right) — ${colormap}`;
            bar.appendChild(canvas);
        }

        /**
         * Render an elevation histogram with a cumulative distribution curve into `#histogram`.
         * Bars are coloured using the currently selected colormap. A red dashed line marks
         * sea level when the data range spans negative values.
         * Also triggers `updateStackedLayers()` after rendering via `requestAnimationFrame`.
         * @param {number[]} values - Flat array of elevation values (may contain NaN/Infinity)
         */
        function drawHistogram(values) {
            const container = document.getElementById('histogram');
            if (!container) return;
            container.innerHTML = '';

            const canvas = document.createElement('canvas');
            canvas.width  = Math.max(100, container.clientWidth || 280);
            canvas.height = 200; // Increased for more square cumulative
            const ctx = canvas.getContext('2d');
            const colormap = document.getElementById('demColormap').value;

            // Filter valid values
            const valid = values.filter(v => Number.isFinite(v));
            if (valid.length === 0) {
                ctx.fillStyle = '#888';
                ctx.fillText('No data', 100, 60);
                container.appendChild(canvas);
                return;
            }

            // Use reduce instead of spread to avoid stack overflow with large arrays
            const min = valid.reduce((a, b) => a < b ? a : b, valid[0]);
            const max = valid.reduce((a, b) => a > b ? a : b, valid[0]);
            const range = (max - min) || 1;
            const numBins = 40;
            const bins = new Array(numBins).fill(0);

            valid.forEach(v => {
                const idx = Math.min(numBins - 1, Math.floor(((v - min) / range) * numBins));
                bins[idx]++;
            });

            const maxBin = Math.max(...bins);
            const barWidth = canvas.width / numBins;
            const histTop = 8;
            const histHeight = 70; // Regular histogram height
            const cumulTop = histTop + histHeight + 12; // Cumulative histogram starts below
            const cumulHeight = 80; // More square cumulative histogram

            // Background
            ctx.fillStyle = '#252525';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            // Draw grid lines for main histogram
            ctx.strokeStyle = 'rgba(100, 100, 100, 0.3)';
            ctx.lineWidth = 1;
            for (let i = 1; i <= 4; i++) {
                const y = histTop + (histHeight / 5) * i;
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(canvas.width, y);
                ctx.stroke();
            }
            for (let i = 1; i <= 4; i++) {
                const x = (canvas.width / 5) * i;
                ctx.beginPath();
                ctx.moveTo(x, histTop);
                ctx.lineTo(x, histTop + histHeight);
                ctx.stroke();
            }

            // Draw each bar with its corresponding colormap color
            bins.forEach((count, i) => {
                const t = i / (numBins - 1);
                const [r, g, b] = mapElevationToColor(t, colormap);
                ctx.fillStyle = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
                const barHeight = (count / maxBin) * histHeight;
                ctx.fillRect(i * barWidth, histTop + histHeight - barHeight, barWidth - 1, barHeight);
            });

            // Calculate and draw cumulative histogram
            const cumulative = [];
            let sum = 0;
            bins.forEach(count => {
                sum += count;
                cumulative.push(sum);
            });
            const totalCount = sum;

            // Cumulative area fill
            ctx.fillStyle = 'rgba(80, 160, 255, 0.3)';
            ctx.beginPath();
            ctx.moveTo(0, cumulTop + cumulHeight);
            cumulative.forEach((csum, i) => {
                const x = (i + 0.5) * barWidth;
                const y = cumulTop + cumulHeight - (csum / totalCount) * cumulHeight;
                ctx.lineTo(x, y);
            });
            ctx.lineTo(canvas.width, cumulTop);
            ctx.lineTo(canvas.width, cumulTop + cumulHeight);
            ctx.closePath();
            ctx.fill();

            // Cumulative line
            ctx.strokeStyle = '#50a0ff';
            ctx.lineWidth = 2;
            ctx.beginPath();
            cumulative.forEach((csum, i) => {
                const x = (i + 0.5) * barWidth;
                const y = cumulTop + cumulHeight - (csum / totalCount) * cumulHeight;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.stroke();

            // Cumulative labels
            ctx.fillStyle = '#888';
            ctx.font = '9px sans-serif';
            ctx.fillText('0%', 2, cumulTop + cumulHeight - 2);
            ctx.fillText('100%', 2, cumulTop + 8);
            ctx.fillText('Cumulative', canvas.width / 2 - 25, cumulTop + cumulHeight + 10);

            // Draw zero elevation line if it's within range
            if (min < 0 && max > 0) {
                const zeroX = ((0 - min) / range) * canvas.width;
                ctx.strokeStyle = '#ff4444';
                ctx.lineWidth = 2;
                ctx.setLineDash([4, 2]);
                ctx.beginPath();
                ctx.moveTo(zeroX, histTop);
                ctx.lineTo(zeroX, histTop + histHeight);
                ctx.stroke();
                ctx.setLineDash([]);

                ctx.fillStyle = '#ff4444';
                ctx.font = 'bold 10px sans-serif';
                ctx.fillText('0', zeroX - 3, histTop - 2);
            }

            // Axis labels for main histogram
            ctx.fillStyle = '#aaa';
            ctx.font = '10px sans-serif';
            ctx.fillText(min.toFixed(0) + 'm', 2, histTop + histHeight + 12);
            ctx.fillText(max.toFixed(0) + 'm', canvas.width - 35, histTop + histHeight + 12);

            canvas.style.width = '100%';
            canvas.style.height = 'auto';
            container.appendChild(canvas);

            // Update layers view when histogram is drawn (DEM data is available)
            requestAnimationFrame(() => updateStackedLayers());
        }

        // ============================================================
        // SIDEBAR
        // 3-state sidebar toggle and table/list views.
        // ============================================================

        // 3-state sidebar toggle: normal → expanded → hidden → normal
        let sidebarState = 'normal'; // 'normal', 'expanded', 'hidden'

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
                const response = await fetch(`/api/terrain/dem?${params}`, { signal });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                const data = await response.json();

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

        let generatedModelData = null;

        /** Enable or disable all model export buttons, and show/hide the model empty state. */
        function _setExportButtonsEnabled(enabled) {
            const ids = ['downloadSTLBtn', 'downloadOBJBtn', 'download3MFBtn',
                         'exportCityBtn', 'exportCrossSectionBtn', 'exportPuzzleBtn'];
            for (const id of ids) {
                const el = document.getElementById(id);
                if (!el) continue;
                el.disabled = !enabled;
                el.style.opacity = enabled ? '' : '0.4';
                el.style.cursor  = enabled ? '' : 'not-allowed';
            }
            const emptyEl = document.getElementById('modelEmptyState');
            if (emptyEl) emptyEl.style.display = enabled ? 'none' : 'flex';
        }
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
            const step3Done = !!generatedModelData;

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

        // Disable on load — enabled after generateModelFromTab succeeds
        document.addEventListener('DOMContentLoaded', () => {
            _setExportButtonsEnabled(false);
            _setDemEmptyState(true);
            _updateWorkflowStepper();
        });
        let modelScene, modelCamera, modelRenderer, modelMesh, modelControls;

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

        /**
         * Trigger server-side 3D model generation from the current DEM data.
         * Opens the model viewer tab and shows download/preview options.
         */
        function generateModelFromTab() {
            if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
                showToast('Please load a DEM first by selecting a region on the map.', 'warning');
                return;
            }

            const resolution = parseInt(document.getElementById('modelResolution').value);
            const exaggeration = parseFloat(document.getElementById('modelExaggeration').value);
            const baseHeight = parseFloat(document.getElementById('modelBaseHeight').value);
            const simplify = document.getElementById('modelSimplify').checked;

            if (!resolution || resolution < 1 || resolution > 2000) {
                showToast('Resolution must be between 1 and 2000.', 'warning'); return;
            }
            if (!exaggeration || exaggeration <= 0 || exaggeration > 100) {
                showToast('Exaggeration must be between 0 and 100.', 'warning'); return;
            }
            if (isNaN(baseHeight) || baseHeight < 0 || baseHeight > 100) {
                showToast('Base height must be between 0 and 100 mm.', 'warning'); return;
            }

            const progress = document.getElementById('modelProgress');
            const progressBar = document.getElementById('modelProgressBar');
            const progressText = document.getElementById('modelProgressText');
            const status = document.getElementById('modelStatus');

            // Show loading overlay on the viewport
            const viewportEl = document.querySelector('.model-viewport');
            if (viewportEl) showLoading(viewportEl, 'Generating model...');

            progress.style.display = 'block';
            progressBar.style.width = '0%';
            progressText.textContent = 'Preparing data...';

            // Simulate progress (actual generation would be server-side)
            setTimeout(() => {
                progressBar.style.width = '30%';
                progressText.textContent = 'Generating mesh...';
            }, 200);

            setTimeout(() => {
                progressBar.style.width = '70%';
                progressText.textContent = 'Applying parameters...';
            }, 500);

            setTimeout(() => {
                progressBar.style.width = '100%';
                progressText.textContent = 'Complete!';
                generatedModelData = {
                    values: lastDemData.values,
                    width: lastDemData.width,
                    height: lastDemData.height,
                    resolution: resolution,
                    exaggeration: exaggeration,
                    baseHeight: baseHeight,
                    vmin: lastDemData.vmin,
                    vmax: lastDemData.vmax
                };
                _setExportButtonsEnabled(true);
                _updateWorkflowStepper();
                status.textContent = `Model ready (${resolution}x${resolution}, ${exaggeration}x exaggeration)`;
                // Remove loading overlay from viewport
                const vp = document.querySelector('.model-viewport');
                if (vp) hideLoading(vp);

                setTimeout(() => {
                    progress.style.display = 'none';
                }, 1000);
            }, 800);
        }

        /**
         * Download the current model as an STL file.
         * Posts DEM data to `/api/export/stl` and triggers a file download.
         */
        function downloadSTL() {
            if (!generatedModelData) {
                showToast('Please generate a model first.', 'warning');
                return;
            }

            const progress = document.getElementById('modelProgress');
            const progressBar = document.getElementById('modelProgressBar');
            const progressText = document.getElementById('modelProgressText');

            progress.style.display = 'block';
            progressBar.style.width = '20%';
            progressText.textContent = 'Preparing STL export...';

            // Get region name for filename
            let regionName = 'terrain';
            if (selectedRegion && selectedRegion.name) {
                regionName = selectedRegion.name.replace(/[^a-zA-Z0-9]/g, '_');
            }

            const seaLevelCap = document.getElementById('modelSeaLevelCap')?.checked || false;
            const engraveLabel = document.getElementById('modelEngraveLabel')?.checked || false;
            const contours = document.getElementById('modelContours')?.checked || false;
            const contourInterval = parseInt(document.getElementById('modelContourInterval')?.value) || 100;
            const contourStyle = document.getElementById('modelContourStyle')?.value || 'engraved';

            // Send to backend for STL generation
            fetch('/api/export/stl', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    dem_values: generatedModelData.values,
                    height: generatedModelData.height,
                    width: generatedModelData.width,
                    model_height: generatedModelData.resolution,
                    base_height: generatedModelData.baseHeight,
                    exaggeration: generatedModelData.exaggeration,
                    sea_level_cap: seaLevelCap,
                    engrave_label: engraveLabel,
                    label_text: selectedRegion?.name || regionName,
                    contours,
                    contour_interval: contourInterval,
                    contour_style: contourStyle,
                    name: regionName
                })
            })
                .then(response => {
                    progressBar.style.width = '80%';
                    progressText.textContent = 'Downloading STL...';

                    if (!response.ok) {
                        return response.json().then(err => { throw new Error(err.error || 'STL generation failed'); });
                    }

                    // Read mesh quality headers before consuming body
                    const isWatertight = response.headers.get('X-Watertight') === 'true';
                    const faceCount = response.headers.get('X-Face-Count');
                    return response.blob().then(blob => ({ blob, isWatertight, faceCount }));
                })
                .then(({ blob, isWatertight, faceCount }) => {
                    progressBar.style.width = '100%';
                    progressText.textContent = 'Complete!';

                    // Trigger download
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `${regionName}.stl`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    a.remove();

                    // Show mesh quality feedback
                    const faces = faceCount ? `${parseInt(faceCount).toLocaleString()} faces` : '';
                    const quality = isWatertight ? '✓ watertight' : '⚠ not watertight';
                    showToast(`STL ready — ${faces} ${quality}`, isWatertight ? 'success' : 'info', 4000);

                    setTimeout(() => { progress.style.display = 'none'; }, 1000);
                })
                .catch(error => {
                    console.error('STL download error:', error);
                    progressText.textContent = 'Error: ' + error.message;
                    progressBar.style.backgroundColor = '#e74c3c';
                    setTimeout(() => {
                        progress.style.display = 'none';
                        progressBar.style.backgroundColor = '';
                    }, 2000);
                });
        }

        /**
         * Download the current model in a specified format (OBJ, 3MF).
         * Posts DEM data to `/api/export/{format}` and triggers a file download.
         * @param {string} format - Export format identifier ('obj' or '3mf')
         */
        function downloadModel(format) {
            if (!generatedModelData) {
                showToast('Please generate a model first.', 'warning');
                return;
            }

            const progress = document.getElementById('modelProgress');
            const progressBar = document.getElementById('modelProgressBar');
            const progressText = document.getElementById('modelProgressText');

            progress.style.display = 'block';
            progressBar.style.width = '20%';
            progressText.textContent = `Preparing ${format.toUpperCase()} export...`;

            // Get region name for filename
            let regionName = 'terrain';
            if (selectedRegion && selectedRegion.name) {
                regionName = selectedRegion.name.replace(/[^a-zA-Z0-9]/g, '_');
            }

            const seaLevelCap = document.getElementById('modelSeaLevelCap')?.checked || false;
            const engraveLabel = document.getElementById('modelEngraveLabel')?.checked || false;
            const contours = document.getElementById('modelContours')?.checked || false;
            const contourInterval = parseInt(document.getElementById('modelContourInterval')?.value) || 100;
            const contourStyle = document.getElementById('modelContourStyle')?.value || 'engraved';

            // Send to backend for model generation
            fetch(`/api/export/${format}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    dem_values: generatedModelData.values,
                    height: generatedModelData.height,
                    width: generatedModelData.width,
                    model_height: generatedModelData.resolution,
                    base_height: generatedModelData.baseHeight,
                    exaggeration: generatedModelData.exaggeration,
                    sea_level_cap: seaLevelCap,
                    engrave_label: engraveLabel,
                    label_text: selectedRegion?.name || regionName,
                    contours,
                    contour_interval: contourInterval,
                    contour_style: contourStyle,
                    name: regionName
                })
            })
                .then(response => {
                    progressBar.style.width = '80%';
                    progressText.textContent = `Downloading ${format.toUpperCase()}...`;

                    if (!response.ok) {
                        return response.json().then(err => { throw new Error(err.error || `${format.toUpperCase()} generation failed`); });
                    }
                    return response.blob();
                })
                .then(blob => {
                    progressBar.style.width = '100%';
                    progressText.textContent = 'Complete!';

                    // Trigger download
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `${regionName}.${format}`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    a.remove();

                    setTimeout(() => { progress.style.display = 'none'; }, 1000);
                })
                .catch(error => {
                    console.error(`${format.toUpperCase()} download error:`, error);
                    progressText.textContent = 'Error: ' + error.message;
                    progressBar.style.backgroundColor = '#e74c3c';
                    setTimeout(() => {
                        progress.style.display = 'none';
                        progressBar.style.backgroundColor = '';
                    }, 2000);
                });
        }

        /**
         * Download a cross-section STL: a thin wall showing the terrain elevation profile
         * along the user-specified latitude or longitude cut line.
         */
        function downloadCrossSection() {
            if (!generatedModelData) {
                showToast('Please generate a model first.', 'warning');
                return;
            }
            const cutAxis = document.getElementById('crossSectionAxis')?.value || 'lat';
            const cutValueStr = document.getElementById('crossSectionValue')?.value;
            const cutValue = parseFloat(cutValueStr);
            if (isNaN(cutValue)) {
                showToast('Enter a cut coordinate first', 'warning');
                return;
            }
            const thicknessMm = parseFloat(document.getElementById('crossSectionThickness')?.value) || 5;
            const statusEl = document.getElementById('crossSectionStatus');
            if (statusEl) statusEl.textContent = 'Generating…';

            const r = selectedRegion || window.appState?.selectedRegion || {};
            let regionName = 'terrain';
            if (selectedRegion && selectedRegion.name) {
                regionName = selectedRegion.name.replace(/[^a-zA-Z0-9]/g, '_');
            }

            fetch('/api/export/crosssection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    dem_values: generatedModelData.values,
                    height: generatedModelData.height,
                    width: generatedModelData.width,
                    north: r.north ?? 0, south: r.south ?? 0,
                    east: r.east ?? 0,   west: r.west ?? 0,
                    cut_axis: cutAxis,
                    cut_value: cutValue,
                    model_height: generatedModelData.resolution,
                    base_height: generatedModelData.baseHeight,
                    exaggeration: generatedModelData.exaggeration,
                    thickness_mm: thicknessMm,
                    name: regionName
                })
            })
                .then(response => {
                    if (!response.ok) {
                        return response.json().then(err => { throw new Error(err.error || 'Cross-section failed'); });
                    }
                    return response.blob();
                })
                .then(blob => {
                    const axis = cutAxis === 'lat' ? `lat${cutValue.toFixed(4)}` : `lon${cutValue.toFixed(4)}`;
                    const filename = `${regionName}_cross_${axis}.stl`;
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url; a.download = filename;
                    document.body.appendChild(a); a.click();
                    window.URL.revokeObjectURL(url); a.remove();
                    if (statusEl) statusEl.textContent = 'Downloaded.';
                    showToast('Cross-section STL ready', 'success');
                })
                .catch(error => {
                    console.error('Cross-section error:', error);
                    if (statusEl) statusEl.textContent = 'Error: ' + error.message;
                    showToast('Cross-section error: ' + error.message, 'error');
                });
        }

        /**
         * Initialise the Three.js 3D model viewer inside `#modelViewer`.
         * Creates the scene, camera, renderer, orbit controls, lighting, and a grid helper.
         * Starts the render loop.
         */
        function initModelViewer() {
            const container = document.getElementById('modelViewer');
            if (!container) return;

            // Clear any existing content
            container.innerHTML = '';

            // Create scene
            modelScene = new THREE.Scene();
            modelScene.background = new THREE.Color(0x1a1a1a);

            // Create camera
            const aspect = container.clientWidth / container.clientHeight;
            modelCamera = new THREE.PerspectiveCamera(60, aspect, 0.1, 1000);
            modelCamera.position.set(0, 100, 150);
            modelCamera.lookAt(0, 0, 0);

            // Create renderer
            try {
                modelRenderer = new THREE.WebGLRenderer({ antialias: true });
            } catch (webglErr) {
                console.error('WebGL unavailable for 3D viewer:', webglErr);
                container.innerHTML = '<div style="padding:20px;color:#888;text-align:center;">3D preview unavailable (WebGL not supported by this browser/GPU)</div>';
                return;
            }
            modelRenderer.setSize(container.clientWidth, container.clientHeight);
            container.appendChild(modelRenderer.domElement);

            // Add lights
            const ambientLight = new THREE.AmbientLight(0x404040, 0.6);
            modelScene.add(ambientLight);

            const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
            directionalLight.position.set(50, 100, 50);
            modelScene.add(directionalLight);

            const directionalLight2 = new THREE.DirectionalLight(0xffffff, 0.3);
            directionalLight2.position.set(-50, 50, -50);
            modelScene.add(directionalLight2);

            // Add grid helper
            const gridHelper = new THREE.GridHelper(200, 20, 0x444444, 0x333333);
            modelScene.add(gridHelper);

            // Simple orbit controls (mouse interaction)
            let isDragging = false;
            let previousMousePosition = { x: 0, y: 0 };

            modelRenderer.domElement.addEventListener('mousedown', (e) => {
                isDragging = true;
                previousMousePosition = { x: e.clientX, y: e.clientY };
            });

            modelRenderer.domElement.addEventListener('mousemove', (e) => {
                if (!isDragging) return;

                const deltaX = e.clientX - previousMousePosition.x;
                const deltaY = e.clientY - previousMousePosition.y;

                // Rotate camera around the center
                const spherical = new THREE.Spherical();
                spherical.setFromVector3(modelCamera.position);
                spherical.theta -= deltaX * 0.01;
                spherical.phi -= deltaY * 0.01;
                spherical.phi = Math.max(0.1, Math.min(Math.PI - 0.1, spherical.phi));
                modelCamera.position.setFromSpherical(spherical);
                modelCamera.lookAt(0, 0, 0);

                previousMousePosition = { x: e.clientX, y: e.clientY };
            });

            modelRenderer.domElement.addEventListener('mouseup', () => { isDragging = false; });
            modelRenderer.domElement.addEventListener('mouseleave', () => { isDragging = false; });

            // Zoom with scroll wheel
            modelRenderer.domElement.addEventListener('wheel', (e) => {
                e.preventDefault();
                const zoomSpeed = 0.1;
                const direction = e.deltaY > 0 ? 1 : -1;
                const distance = modelCamera.position.length();
                const newDistance = distance * (1 + direction * zoomSpeed);
                modelCamera.position.setLength(Math.max(20, Math.min(500, newDistance)));
            });

            // Handle resize
            window.addEventListener('resize', () => {
                if (!container.offsetParent) return; // Container not visible
                const width = container.clientWidth;
                const height = container.clientHeight;
                modelCamera.aspect = width / height;
                modelCamera.updateProjectionMatrix();
                modelRenderer.setSize(width, height);
            });

            // Expose for Feature 3
            viewerScene = modelScene;
            viewerRenderer = modelRenderer;
            viewerCamera = modelCamera;

            // Animation loop — supports auto-rotate
            /** RAF loop: rotates terrain mesh if auto-rotate is on, then renders the scene. */
            function animate() {
                requestAnimationFrame(animate);
                if (viewerAutoRotate && terrainMesh) {
                    terrainMesh.rotation.y += 0.005;
                }
                modelRenderer.render(modelScene, modelCamera);
            }
            animate();
        }

        /**
         * Create a Three.js terrain mesh from DEM elevation values.
         * Uses a PlaneGeometry with vertex Y positions mapped from elevation data.
         * Applies a terrain colour gradient (blue → green → brown → snow).
         * @param {number[]} demValues - Flat array of elevation values
         * @param {number} width - DEM grid width (columns)
         * @param {number} height - DEM grid height (rows)
         * @param {number} exaggeration - Vertical exaggeration multiplier
         * @returns {THREE.Mesh} The created terrain mesh
         */
        function createTerrainMesh(demValues, width, height, exaggeration) {
            // Create geometry
            const geometry = new THREE.PlaneGeometry(100, 100, width - 1, height - 1);
            geometry.rotateX(-Math.PI / 2); // Lay flat

            // Apply height values (use reduce to avoid spread stack overflow for large arrays)
            const positions = geometry.attributes.position.array;
            const vmin = demValues.reduce((a, b) => Math.min(a, b), Infinity);
            const vmax = demValues.reduce((a, b) => Math.max(a, b), -Infinity);
            const range = vmax - vmin || 1;

            for (let i = 0; i < demValues.length && i * 3 < positions.length; i++) {
                const normalizedHeight = (demValues[i] - vmin) / range;
                const y = normalizedHeight * 30 * exaggeration; // Scale height
                positions[i * 3 + 1] = y;
            }

            geometry.computeVertexNormals();

            // Create terrain-colored material
            const material = new THREE.MeshStandardMaterial({
                color: 0x8fbc8f,
                flatShading: false,
                side: THREE.DoubleSide,
                wireframe: false
            });

            // Add vertex colors based on height
            const colors = [];
            for (let i = 0; i < demValues.length; i++) {
                const normalizedHeight = (demValues[i] - vmin) / range;
                // Terrain color gradient: blue (low) -> green -> brown -> white (high)
                let r, g, b;
                if (normalizedHeight < 0.2) {
                    // Water/low areas - blue to green
                    r = 0.2; g = 0.4 + normalizedHeight * 2; b = 0.6 - normalizedHeight;
                } else if (normalizedHeight < 0.5) {
                    // Lowlands - green
                    r = 0.3 + normalizedHeight * 0.4; g = 0.6; b = 0.3;
                } else if (normalizedHeight < 0.8) {
                    // Mountains - brown/gray
                    r = 0.5 + normalizedHeight * 0.3; g = 0.4 + normalizedHeight * 0.2; b = 0.3;
                } else {
                    // Peaks - white/snow
                    const t = (normalizedHeight - 0.8) / 0.2;
                    r = 0.8 + t * 0.2; g = 0.8 + t * 0.2; b = 0.8 + t * 0.2;
                }
                colors.push(r, g, b);
            }
            geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
            material.vertexColors = true;

            return new THREE.Mesh(geometry, material);
        }

        /**
         * Build or replace the 3D terrain mesh in the model viewer using the current
         * DEM data. Initialises the viewer if needed, applies wireframe and puzzle preview.
         */
        function previewModelIn3D() {
            // Use generatedModelData if available, else fall back to lastDemData
            const source = generatedModelData || (lastDemData ? {
                values: lastDemData.values,
                width: lastDemData.width,
                height: lastDemData.height,
                exaggeration: parseFloat(document.getElementById('modelExaggeration')?.value) || 1.5
            } : null);

            if (!source || !source.values || !source.values.length) {
                showToast('Load a DEM first (Edit tab → Reload).', 'warning');
                return;
            }

            if (!modelRenderer) initModelViewer();

            // Remove old terrain mesh
            if (terrainMesh) {
                modelScene.remove(terrainMesh);
                terrainMesh.geometry.dispose();
                terrainMesh.material.dispose();
                terrainMesh = null;
            }
            if (modelMesh) {
                modelScene.remove(modelMesh);
                modelMesh.geometry.dispose();
                modelMesh.material.dispose();
            }

            terrainMesh = createTerrainMesh(source.values, source.width, source.height, source.exaggeration);
            modelMesh = terrainMesh;
            terrainMesh.position.set(0, 0, 0);
            modelScene.add(terrainMesh);

            // Apply wireframe state
            terrainMesh.material.wireframe = document.getElementById('viewerWireframe')?.checked ?? false;

            // Draw puzzle cuts if enabled
            updatePuzzlePreview();

            document.getElementById('modelStatus').textContent =
                `Preview: ${source.width}×${source.height}, ${source.exaggeration}× exag.`;

            showToast('3D preview loaded! Drag to rotate, scroll to zoom.', 'success');
        }

        /**
         * Draw a pixel grid overlay on an existing canvas element.
         * Lines are drawn every `gridSpacing` pixels in both axes using a dark stroke.
         * @param {HTMLCanvasElement} canvas - Target canvas to draw on
         * @param {number} width - Canvas width in pixels
         * @param {number} height - Canvas height in pixels
         * @param {number} gridSpacing - Spacing between grid lines in pixels
         */
        function addGridLines(canvas, width, height, gridSpacing) {
            const ctx = canvas.getContext('2d');
            ctx.strokeStyle = '#444';
            ctx.lineWidth = 0.5;

            for (let x = gridSpacing; x < width; x += gridSpacing) {
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, height);
                ctx.stroke();
            }

            for (let y = gridSpacing; y < height; y += gridSpacing) {
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(width, y);
                ctx.stroke();
            }
        }

        /**
         * Toggle pixel grid lines on the DEM canvas based on the `#gridToggle` checkbox state.
         * Redraws the DEM via `recolorDEM()` when the toggle is turned off.
         */
        function toggleGridLines() {
            const gridToggle = document.getElementById('gridToggle');
            const canvas = document.querySelector('#demImage canvas');
            if (!canvas) return;

            if (gridToggle.checked) {
                addGridLines(canvas, canvas.width, canvas.height, 20);
            } else {
                const ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                recolorDEM(); // Redraw DEM without grid lines
            }
        }

        // Water mask data storage (declared at top of script)
        // lastWaterMaskData already declared
        // lastRawDemData already declared at top

        // ================================================================
        // Feature 2 — Cities / OSM layer
        // ================================================================

        // ============================================================
        // CITY OVERLAY
        // OpenStreetMap / Overpass API city data rendered as a canvas
        // overlay on the stacked layers view.
        // To be extracted to modules/city-overlay.js (Task 3).
        // ============================================================

        /**
         * Compute the haversine diagonal distance of a bounding box in kilometres.
         * @param {number} north - North latitude
         * @param {number} south - South latitude
         * @param {number} east - East longitude
         * @param {number} west - West longitude
         * @returns {number} Diagonal distance in km
         */
        function haversineDiagKm(north, south, east, west) {
            const R = 6371;
            const dLat = (north - south) * Math.PI / 180;
            const midLat = ((north + south) / 2) * Math.PI / 180;
            const dLon = (east - west) * Math.PI / 180;
            const dy = R * dLat, dx = R * Math.cos(midLat) * dLon;
            return Math.sqrt(dx * dx + dy * dy);
        }
        window.appState.haversineDiagKm = haversineDiagKm;

        /**
         * Show or hide the cities tab button based on whether the region is small enough
         * for Overpass API queries (max diagonal 10 km).
         * @param {Object} region - Region object with north/south/east/west
         */
        function _updateCitiesLoadButton(region) {
            const loadBtn = document.getElementById('loadCityDataBtn');
            const infoRow = document.getElementById('cityInfoRow');
            if (!loadBtn || !region) return;
            const diagKm = haversineDiagKm(region.north, region.south, region.east, region.west);
            const available = diagKm <= 10;
            loadBtn.disabled = !available;
            loadBtn.style.opacity = available ? '' : '0.4';
            loadBtn.style.cursor = available ? '' : 'not-allowed';
            loadBtn.title = available
                ? `Fetch OSM data for this region (${diagKm.toFixed(1)} km)`
                : `Region too large (${diagKm.toFixed(1)} km — max 10 km)`;
            if (infoRow) {
                infoRow.textContent = available
                    ? `Region diagonal: ${diagKm.toFixed(1)} km — OSM data available.`
                    : `Region too large (${diagKm.toFixed(1)} km). Max 10 km for city data.`;
            }
        }

        // ── loadCityData, _updateCityLayerCount, clearCityOverlay, renderCityOverlay ─────
        // Extracted to ui/static/js/modules/city-overlay.js (TODO item 15).
        // Functions are defined on window by that script, loaded in index.html before app.js.
        // window.appState.osmCityData is the shared state used by both sides.

        let lastCityRasterData = null;

        /**
         * Fetch the City Heights raster from /api/cities/raster using the already-loaded
         * osmCityData GeoJSON. Renders the result into #layerCityRasterCanvas.
         * Called automatically when the "City Heights" layer toggle is turned on and
         * osmCityData is available.
         */
        async function loadCityRaster() {
            const cityData = window.appState?.osmCityData;
            const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion;
            if (!cityData || !bbox) return;

            const dim = parseInt(document.getElementById('paramDim')?.value) || 200;
            const buildingScale = parseFloat(document.getElementById('cityBuildingScale')?.value) || 1.0;
            const waterOffset   = parseFloat(document.getElementById('cityWaterOffset')?.value) ?? -2.0;

            setLayerStatus('cityRaster', 'loading');
            try {
                const resp = await fetch('/api/cities/raster', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        north: bbox.north, south: bbox.south,
                        east: bbox.east,   west: bbox.west,
                        dim,
                        buildings:  cityData.buildings  || { type: 'FeatureCollection', features: [] },
                        roads:      cityData.roads       || { type: 'FeatureCollection', features: [] },
                        waterways:  cityData.waterways   || { type: 'FeatureCollection', features: [] },
                        building_scale: buildingScale,
                        road_depression_m: 0,
                        water_depression_m: waterOffset,
                    }),
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                lastCityRasterData = data;

                // Render to an offscreen canvas; store it so updateStackedLayers() can
                // composite it into the stack at the correct letterbox position.
                const colormap = document.getElementById('demColormap')?.value || 'terrain';
                const canvas = renderDEMCanvas(
                    data.values, data.width, data.height, colormap, data.vmin, data.vmax
                );
                if (canvas && window.appState) {
                    window.appState.cityRasterSourceCanvas = canvas;
                }
                setLayerStatus('cityRaster', 'ready');
                updateStackedLayers();
            } catch (e) {
                setLayerStatus('cityRaster', 'error');
                showToast('City raster failed: ' + e.message, 'error');
            }
        }

        /** Wire the City Heights visibility toggle and opacity slider. */
        function _setupCityRasterLayer() {
            const toggle  = document.getElementById('layerCityRasterVisible');
            const opacity = document.getElementById('layerCityRasterOpacity');
            const label   = document.getElementById('layerCityRasterOpacityLabel');
            const canvas  = document.getElementById('layerCityRasterCanvas');
            if (!toggle || !canvas) return;

            toggle.addEventListener('change', () => {
                if (toggle.checked) {
                    canvas.style.display = '';
                    if (!lastCityRasterData && window.appState?.osmCityData) loadCityRaster();
                } else {
                    canvas.style.display = 'none';
                }
                updateStackedLayers();
            });

            if (opacity && label) {
                opacity.addEventListener('input', () => {
                    const pct = opacity.value;
                    label.textContent = pct + '%';
                    canvas.style.opacity = pct / 100;
                });
            }

            // Auto-trigger when city data loads
            if (window.appState?.on) {
                window.appState.on('osmCityData', (data) => {
                    // Update badge in Cities section header
                    const badge = document.getElementById('citiesSettingsBadge');
                    if (badge) {
                        if (data) {
                            const nb = data.buildings?.features?.length || 0;
                            const nr = data.roads?.features?.length || 0;
                            badge.textContent = `${nb} buildings · ${nr} roads`;
                            badge.style.color = '#4a9';
                        } else {
                            badge.textContent = '';
                        }
                    }
                    // Auto-expand the Cities section once when data first loads
                    if (data) {
                        const sec = document.getElementById('citiesSettingsSection');
                        if (sec?.classList.contains('collapsed')) sec.classList.remove('collapsed');
                    }
                    // Invalidate raster cache and reload if toggle is on
                    lastCityRasterData = null;
                    if (document.getElementById('layerCityRasterVisible')?.checked) loadCityRaster();
                });
            }
        }

        // ================================================================
        // Feature 3 — 3D Terrain Viewer + Puzzle Controls
        // ================================================================

        let terrainMesh = null;
        let viewerAutoRotate = false;
        let viewerScene = null, viewerRenderer = null, viewerCamera = null;

        /**
         * Draw cut-line geometry over the 3D terrain showing puzzle piece boundaries.
         * Reads puzzle X/Y piece counts from `#puzzlePiecesX` / `#puzzlePiecesY`.
         */
        function updatePuzzlePreview() {
            if (!terrainMesh || !viewerScene) return;
            // Remove old cut lines
            const old = viewerScene.getObjectByName('puzzleCuts');
            if (old) viewerScene.remove(old);

            if (!document.getElementById('puzzleEnabled')?.checked) return;

            const pX = parseInt(document.getElementById('puzzlePiecesX')?.value) || 3;
            const pY = parseInt(document.getElementById('puzzlePiecesY')?.value) || 3;

            const geo = new THREE.BufferGeometry();
            const verts = [];
            const w = 100, h = 100;  // Viewer uses 100x100 unit terrain
            // Vertical cut lines
            for (let i = 1; i < pX; i++) {
                const x = (i / pX) * w - w / 2;
                verts.push(x, 0, -h / 2, x, 0, h / 2);
            }
            // Horizontal cut lines
            for (let j = 1; j < pY; j++) {
                const z = (j / pY) * h - h / 2;
                verts.push(-w / 2, 0, z, w / 2, 0, z);
            }
            geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
            const mat = new THREE.LineBasicMaterial({ color: 0xff2222, depthTest: false });
            const lines = new THREE.LineSegments(geo, mat);
            lines.name = 'puzzleCuts';
            lines.position.y = 6;  // Slightly above terrain surface
            viewerScene.add(lines);
        }

        /**
         * Export the current terrain as a puzzle-sliced 3MF file.
         * Backend implementation is pending; currently shows a placeholder toast.
         * @returns {Promise<void>}
         */
        async function exportPuzzle3MF() {
            if (!selectedRegion) { showToast('Select a region first', 'warning'); return; }
            const pX = parseInt(document.getElementById('puzzlePiecesX')?.value) || 3;
            const pY = parseInt(document.getElementById('puzzlePiecesY')?.value) || 3;
            if (pX * pY > 64) { showToast('Too many pieces (max 64 total)', 'warning'); return; }

            showToast('Generating puzzle 3MF…', 'info');
            // Re-use the regular 3MF export with puzzle parameters passed as query params
            // (backend puzzle support requires further implementation — show intent)
            showToast('Puzzle 3MF export: backend implementation pending', 'warning');
        }

        // ============================================================
        // MERGE PANEL
        // Multi-layer DEM compositor allowing blending of multiple data
        // sources (local SRTM, OpenTopography, ESA water mask).
        // ============================================================

        /**
         * Fetch available DEM sources from `/api/terrain/sources` and populate
         * the `#paramDemSource` dropdown and API key warning.
         * @returns {Promise<void>}
         */
        async function _initDemSources() {
            try {
                const resp = await fetch('/api/terrain/sources');
                if (!resp.ok) return;
                const data = await resp.json();
                const select = document.getElementById('paramDemSource');
                const warning = document.getElementById('demSourceApiKeyWarning');
                if (!select) return;

                // Rebuild options from server response
                select.innerHTML = '';
                for (const src of data.sources) {
                    const opt = document.createElement('option');
                    opt.value = src.id;
                    opt.textContent = src.label + (src.resolution_m ? ` (${src.resolution_m}m)` : '');
                    if (!src.available) opt.disabled = true;
                    select.appendChild(opt);
                }

                // Also update merge source list
                _mergeSources = [
                    { id: 'local',     label: 'Local SRTM Tiles' },
                    { id: 'water_esa', label: 'Water Mask (ESA WorldCover)' },
                    ...data.sources
                        .filter(s => s.id !== 'local')
                        .map(s => ({ id: s.id, label: s.label + (s.resolution_m ? ` (${s.resolution_m}m)` : '') })),
                ];
                // Re-render merge panel if it has been initialised
                _renderMergePanel();

                // Show warning when an OpenTopography source is selected but no key configured
                const checkWarning = () => {
                    const val = select.value;
                    const needsKey = val !== 'local' && !data.opentopo_api_key_configured;
                    if (warning) warning.style.display = needsKey ? 'block' : 'none';
                };
                select.addEventListener('change', checkWarning);
                checkWarning();
            } catch (_) { /* non-fatal */ }
        }

        // =====================================================
        // DEM Merge Tool
        // =====================================================

        // Available DEM sources for merge layers (populated from /api/terrain/sources)
        let _mergeSources = [
            { id: 'local',     label: 'Local SRTM Tiles' },
            { id: 'water_esa', label: 'Water Mask (ESA)' },
        ];

        // Merge layer state — array of plain objects
        let _mergeLayers = [];
        let _mergeLayerSeq = 0;

        /**
         * Build HTML `<option>` elements for all available merge sources.
         * @param {string} selectedId - The source id to pre-select
         * @returns {string} HTML string of `<option>` elements
         */
        function _mergeSourceOptions(selectedId) {
            return _mergeSources.map(s =>
                `<option value="${s.id}"${s.id === selectedId ? ' selected' : ''}>${s.label}</option>`
            ).join('');
        }

        /**
         * Create a new merge layer descriptor object with default values.
         * @param {Object} [overrides={}] - Property overrides
         * @returns {Object} New merge layer object
         */
        function _createMergeLayerObj(overrides = {}) {
            return {
                id: ++_mergeLayerSeq,
                source: 'local',
                dim: 300,
                blend_mode: 'base',
                weight: 1.0,
                smooth_sigma: 0,
                clip_min: '',
                clip_max: '',
                extract_rivers: false,
                river_max_width_px: 8,
                normalize: false,
                invert: false,
                sharpen: false,
                processingOpen: false,
                ...overrides,
            };
        }

        /**
         * Generate HTML for a single merge layer card including source select,
         * blend mode, weight, and processing options.
         * @param {Object} layer - Merge layer descriptor object
         * @param {number} idx - Zero-based index of this layer in the stack
         * @returns {string} HTML string for the card
         */
        function _renderMergeLayerCard(layer, idx) {
            const isBase = idx === 0;
            const blendOptions = [
                ['base',    'Base (first layer)'],
                ['blend',   'Blend (weighted)'],
                ['rivers',  'Carve Rivers / Water'],
                ['replace', 'Replace'],
                ['max',     'Max (higher wins)'],
                ['min',     'Min (lower wins)'],
            ].map(([v, l]) =>
                `<option value="${v}"${v === layer.blend_mode ? ' selected' : ''}>${l}</option>`
            ).join('');

            const procDisplay = layer.processingOpen ? '' : 'display:none;';

            return `
<div class="merge-layer-card" data-layer-id="${layer.id}">
  <div class="merge-layer-header">
    <span class="merge-layer-num">${idx + 1}</span>
    <select class="merge-src" title="Elevation or mask source">
      ${_mergeSourceOptions(layer.source)}
    </select>
    <div class="merge-layer-actions">
      <button class="merge-up" title="Move up">↑</button>
      <button class="merge-dn" title="Move down">↓</button>
      <button class="merge-rm" title="Remove layer">✕</button>
    </div>
  </div>
  <div class="merge-layer-body">
    <div class="param-group">
      <label>Resolution (px):</label>
      <input type="number" class="merge-dim" value="${layer.dim}" min="50" max="2000" step="50">
    </div>
    <div class="param-group" ${isBase ? 'style="opacity:0.4;pointer-events:none;"' : ''}>
      <label>Blend mode:</label>
      <select class="merge-mode">${blendOptions}</select>
    </div>
    <div class="param-group merge-weight-row" ${isBase || layer.blend_mode === 'base' ? 'style="opacity:0.4;pointer-events:none;"' : ''}>
      <label>Weight:</label>
      <input type="range" class="merge-weight" min="0" max="3" step="0.05" value="${layer.weight}">
      <span class="val-label merge-weight-val">${layer.weight.toFixed(2)}</span>
    </div>

    <button class="merge-processing-toggle">⚙ Processing ${layer.processingOpen ? '▲' : '▼'}</button>
    <div class="merge-processing-body" style="${procDisplay}">
      <div class="param-group">
        <label>Smooth σ:</label>
        <input type="range" class="merge-smooth" min="0" max="15" step="0.5" value="${layer.smooth_sigma}">
        <span class="val-label merge-smooth-val">${layer.smooth_sigma}</span>
      </div>
      <div class="param-group">
        <label>Clip min (m):</label>
        <input type="number" class="merge-clip-min" placeholder="none" value="${layer.clip_min}" style="width:70px;">
      </div>
      <div class="param-group">
        <label>Clip max (m):</label>
        <input type="number" class="merge-clip-max" placeholder="none" value="${layer.clip_max}" style="width:70px;">
      </div>
      <div class="checkbox-row">
        <label><input type="checkbox" class="merge-extract-rivers"${layer.extract_rivers ? ' checked' : ''}> Rivers only</label>
        <label><input type="checkbox" class="merge-sharpen"${layer.sharpen ? ' checked' : ''}> Sharpen</label>
        <label><input type="checkbox" class="merge-normalize"${layer.normalize ? ' checked' : ''}> Normalize</label>
        <label><input type="checkbox" class="merge-invert"${layer.invert ? ' checked' : ''}> Invert</label>
      </div>
      <div class="param-group merge-river-width-row" style="${layer.extract_rivers ? '' : 'display:none;'}">
        <label>Max river width (px):</label>
        <input type="number" class="merge-river-width" value="${layer.river_max_width_px}" min="1" max="100">
      </div>
    </div>
  </div>
</div>`;
        }

        /**
         * Re-render the entire merge panel HTML from `_mergeLayers` state
         * and re-wire all card event listeners.
         */
        function _renderMergePanel() {
            const list = document.getElementById('mergeLayerList');
            if (!list) return;
            list.innerHTML = _mergeLayers.map((l, i) => _renderMergeLayerCard(l, i)).join('');

            // Wire events for each card
            list.querySelectorAll('.merge-layer-card').forEach(card => {
                const id = parseInt(card.dataset.layerId);
                const layer = _mergeLayers.find(l => l.id === id);
                if (!layer) return;
                const idx = _mergeLayers.indexOf(layer);

                // Source change
                card.querySelector('.merge-src').addEventListener('change', e => {
                    layer.source = e.target.value;
                    _renderMergePanel();
                });

                // Dim
                card.querySelector('.merge-dim').addEventListener('change', e => {
                    layer.dim = parseInt(e.target.value) || 300;
                });

                // Blend mode
                card.querySelector('.merge-mode').addEventListener('change', e => {
                    layer.blend_mode = e.target.value;
                    _renderMergePanel();
                });

                // Weight slider
                const wSlider = card.querySelector('.merge-weight');
                const wVal = card.querySelector('.merge-weight-val');
                wSlider.addEventListener('input', e => {
                    layer.weight = parseFloat(e.target.value);
                    if (wVal) wVal.textContent = layer.weight.toFixed(2);
                });

                // Processing toggle
                card.querySelector('.merge-processing-toggle').addEventListener('click', () => {
                    layer.processingOpen = !layer.processingOpen;
                    _renderMergePanel();
                });

                // Smooth slider
                const smSlider = card.querySelector('.merge-smooth');
                const smVal = card.querySelector('.merge-smooth-val');
                smSlider.addEventListener('input', e => {
                    layer.smooth_sigma = parseFloat(e.target.value);
                    if (smVal) smVal.textContent = layer.smooth_sigma;
                });

                // Clip
                card.querySelector('.merge-clip-min').addEventListener('change', e => {
                    layer.clip_min = e.target.value;
                });
                card.querySelector('.merge-clip-max').addEventListener('change', e => {
                    layer.clip_max = e.target.value;
                });

                // Checkboxes
                card.querySelector('.merge-extract-rivers').addEventListener('change', e => {
                    layer.extract_rivers = e.target.checked;
                    _renderMergePanel();
                });
                card.querySelector('.merge-sharpen').addEventListener('change', e => {
                    layer.sharpen = e.target.checked;
                });
                card.querySelector('.merge-normalize').addEventListener('change', e => {
                    layer.normalize = e.target.checked;
                });
                card.querySelector('.merge-invert').addEventListener('change', e => {
                    layer.invert = e.target.checked;
                });
                card.querySelector('.merge-river-width').addEventListener('change', e => {
                    layer.river_max_width_px = parseInt(e.target.value) || 8;
                });

                // Move up/down/remove
                card.querySelector('.merge-up').addEventListener('click', () => {
                    if (idx > 0) {
                        [_mergeLayers[idx - 1], _mergeLayers[idx]] = [_mergeLayers[idx], _mergeLayers[idx - 1]];
                        _renderMergePanel();
                    }
                });
                card.querySelector('.merge-dn').addEventListener('click', () => {
                    if (idx < _mergeLayers.length - 1) {
                        [_mergeLayers[idx + 1], _mergeLayers[idx]] = [_mergeLayers[idx], _mergeLayers[idx + 1]];
                        _renderMergePanel();
                    }
                });
                card.querySelector('.merge-rm').addEventListener('click', () => {
                    _mergeLayers.splice(idx, 1);
                    _renderMergePanel();
                });
            });
        }

        /**
         * Convert an internal merge layer descriptor object to the API `MergeLayerSpec` format.
         * @param {Object} layer - Internal merge layer object
         * @returns {Object} API-compatible layer spec
         */
        function _mergeLayerToSpec(layer) {
            return {
                source: layer.source,
                dim: layer.dim,
                blend_mode: layer.blend_mode,
                weight: layer.weight,
                label: layer.source,
                processing: {
                    smooth_sigma: layer.smooth_sigma,
                    sharpen: layer.sharpen,
                    clip_min: layer.clip_min !== '' ? parseFloat(layer.clip_min) : null,
                    clip_max: layer.clip_max !== '' ? parseFloat(layer.clip_max) : null,
                    normalize: layer.normalize,
                    invert: layer.invert,
                    extract_rivers: layer.extract_rivers,
                    river_max_width_px: layer.river_max_width_px,
                },
            };
        }

        /**
         * Execute the DEM merge by POSTing to `/api/dem/merge`.
         * Previews or permanently applies the result based on the `apply` flag.
         * @param {boolean} [apply=false] - true to replace `lastDemData`, false for preview only
         * @returns {Promise<void>}
         */
        async function runMerge(apply = false) {
            if (!_mergeLayers.length) {
                showToast('Add at least one layer first', 'warning');
                return;
            }
            if (!currentDemBbox && !selectedRegion) {
                showToast('Load a region first', 'warning');
                return;
            }

            const bbox = currentDemBbox || {
                north: selectedRegion.north, south: selectedRegion.south,
                east: selectedRegion.east,  west: selectedRegion.west,
            };

            const outDim = parseInt(document.getElementById('paramDim')?.value) || 300;

            const status = document.getElementById('mergeStatus');
            if (status) status.textContent = '⏳ Merging…';

            const previewBtn = document.getElementById('mergePreviewBtn');
            const applyBtn  = document.getElementById('mergeApplyBtn');
            if (previewBtn) previewBtn.disabled = true;
            if (applyBtn)  applyBtn.disabled  = true;

            try {
                const body = {
                    bbox,
                    dim: outDim,
                    layers: _mergeLayers.map(_mergeLayerToSpec),
                };
                // First layer blend_mode must be "base"
                if (body.layers.length > 0) body.layers[0].blend_mode = 'base';

                const resp = await fetch('/api/dem/merge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await resp.json();

                if (!resp.ok) {
                    if (status) status.textContent = '❌ ' + (data.error || 'Merge failed');
                    showToast('Merge failed: ' + (data.error || resp.status), 'error');
                    return;
                }

                if (status) {
                    const src = data.layer_count + ' layer(s) merged';
                    status.textContent = `✓ Done — ${data.dimensions?.[1]}×${data.dimensions?.[0]}px, `
                        + `${data.min_elevation?.toFixed(1)}–${data.max_elevation?.toFixed(1)} m`;
                }

                // Render the merged result client-side (same path as main DEM load)
                let demVals = data.dem_values;
                let h = Number(data.dimensions[0]);
                let w = Number(data.dimensions[1]);
                if (Array.isArray(demVals[0])) { h = demVals.length; w = demVals[0].length; demVals = demVals.flat(); }
                const vmin = data.min_elevation ?? 0;
                const vmax = data.max_elevation ?? 1;
                const colormap = document.getElementById('demColormap')?.value || 'terrain';

                currentDemBbox = { north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west };
                window.appState.currentDemBbox = currentDemBbox;
                const rawCanvas = renderDEMCanvas(demVals, w, h, colormap, vmin, vmax);
                const canvas = applyProjection(rawCanvas, currentDemBbox);
                const container = document.getElementById('demImage');
                container.innerHTML = '';
                container.appendChild(canvas);
                canvas.style.width = '100%'; canvas.style.height = 'auto';
                drawColorbar(vmin, vmax, colormap);
                drawHistogram(demVals);
                requestAnimationFrame(() => { drawGridlinesOverlay('demImage'); updateStackedLayers(); });

                if (apply) {
                    // Replace lastDemData and also originalDemValues so curve/reset work correctly
                    originalDemValues = [...demVals];
                    document.getElementById('rescaleMin').value = Math.floor(vmin);
                    document.getElementById('rescaleMax').value = Math.ceil(vmax);
                    showToast('Merged DEM applied', 'success');
                } else {
                    showToast('Merge preview rendered', 'info');
                }
            } catch (e) {
                if (status) status.textContent = '❌ ' + e.message;
                showToast('Merge error: ' + e.message, 'error');
            } finally {
                if (previewBtn) previewBtn.disabled = false;
                if (applyBtn)  applyBtn.disabled  = false;
            }
        }

        /**
         * Initialise the merge panel: seed with one base layer and wire the
         * Add Layer, Preview, and Apply button events.
         */
        /**
         * Populate merge layers from the currently active DEM source + water mask settings.
         * Called automatically on first open and via the "Sync from layers" button.
         */
        function _syncMergeFromCurrentLayers() {
            const source = document.getElementById('paramDemSource')?.value || 'local';
            const dim    = parseInt(document.getElementById('paramDim')?.value) || 300;
            _mergeLayers = [_createMergeLayerObj({ source, dim, blend_mode: 'base' })];
            if (lastWaterMaskData) {
                _mergeLayers.push(_createMergeLayerObj({ source: 'water_esa', dim, blend_mode: 'rivers' }));
            }
            _renderMergePanel();
        }

        /**
         * Read current param* hidden input values into the pipeline quick-settings panel.
         * Called each time the merge subtab is opened so the panel stays current.
         */
        function _refreshPipelinePanel() {
            const get = id => document.getElementById(id);
            const pairs = [
                ['pipelineDim',         'paramDim'],
                ['pipelineDepthScale',  'paramDepthScale'],
                ['pipelineWaterScale',  'paramWaterScale'],
                ['pipelineHeight',      'paramHeight'],
                ['pipelineBase',        'paramBase'],
                ['pipelineSatScale',    'paramSatScale'],
            ];
            for (const [pipeId, paramId] of pairs) {
                const pEl = get(pipeId); const hEl = get(paramId);
                if (pEl && hEl && hEl.value) pEl.value = hEl.value;
            }
            const swPipe  = get('pipelineSubtractWater');
            const swParam = get('paramSubtractWater');
            if (swPipe && swParam) swPipe.checked = swParam.value !== 'false';
            // Also mirror paramDemSource → pipelineSource
            const srcPipe  = get('pipelineSource');
            const srcParam = get('paramDemSource');
            if (srcPipe && srcParam) srcPipe.value = srcParam.value;
        }

        function setupMergePanel() {
            // Wire pipeline quick-settings inputs → hidden param inputs (and Extrude tab mirrors)
            const get = id => document.getElementById(id);
            const pipelineBindings = [
                ['pipelineDim',        'paramDim',        null],
                ['pipelineDepthScale', 'paramDepthScale', 'modelDepthScale'],
                ['pipelineWaterScale', 'paramWaterScale', 'modelWaterScale'],
                ['pipelineHeight',     'paramHeight',     null],
                ['pipelineBase',       'paramBase',       'modelBaseHeight'],
                ['pipelineSatScale',   'paramSatScale',   null],
            ];
            for (const [pipeId, paramId, mirrorId] of pipelineBindings) {
                const pEl = get(pipeId);
                if (!pEl) continue;
                pEl.addEventListener('change', () => {
                    const h = get(paramId); if (h) h.value = pEl.value;
                    if (mirrorId) { const m = get(mirrorId); if (m) m.value = pEl.value; }
                });
            }
            const swPipe = get('pipelineSubtractWater');
            if (swPipe) {
                swPipe.addEventListener('change', () => {
                    const h = get('paramSubtractWater'); if (h) h.value = String(swPipe.checked);
                    const m = get('modelSubtractWater'); if (m) m.checked = swPipe.checked;
                });
            }
            const srcPipe = get('pipelineSource');
            if (srcPipe) {
                srcPipe.addEventListener('change', () => {
                    const h = get('paramDemSource'); if (h) h.value = srcPipe.value;
                });
            }

            // "Reload DEM" button — syncs dim/source then re-fetches
            get('pipelineReloadBtn')?.addEventListener('click', () => {
                const pDim = get('pipelineDim'); const hDim = get('paramDim');
                if (pDim && hDim) hDim.value = pDim.value;
                const pSrc = get('pipelineSource'); const hSrc = get('paramDemSource');
                if (pSrc && hSrc) hSrc.value = pSrc.value;
                if (typeof window.loadDEM === 'function') window.loadDEM();
            });

            // "Sync from layers" button — rebuilds layer stack from current settings
            get('mergeSyncBtn')?.addEventListener('click', _syncMergeFromCurrentLayers);

            // Seed layer stack from current settings on first open
            if (_mergeLayers.length === 0) _syncMergeFromCurrentLayers();

            get('mergeAddLayerBtn')?.addEventListener('click', () => {
                const mode = _mergeLayers.length === 0 ? 'base' : 'blend';
                _mergeLayers.push(_createMergeLayerObj({ blend_mode: mode }));
                _renderMergePanel();
            });

            get('mergePreviewBtn')?.addEventListener('click', () => runMerge(false));
            get('mergeApplyBtn')?.addEventListener('click', () => runMerge(true));
        }

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
                    _refreshPipelinePanel();
                    break;
                default:
                    // Default: show layers stack
                    document.getElementById('layersContainer')?.classList.remove('hidden');
                    updateStackedLayers();
                    break;
            }
        }

        // ============================================================
        // WATER MASK
        // ESA/GEE water mask fetching, caching, rendering, and subtraction.
        // ============================================================

        /**
         * Fetch water mask data from `/api/water_mask` for the current bbox.
         * Uses `waterMaskCache` to avoid redundant requests. Stores result
         * in `lastWaterMaskData` and renders both water and land cover canvases.
         * @returns {Promise<void>}
         */
        let _waterMaskAbortController = null;
        async function loadWaterMask() {
            if (_waterMaskAbortController) _waterMaskAbortController.abort();
            _waterMaskAbortController = new AbortController();
            const signal = _waterMaskAbortController.signal;

            if (!boundingBox && !selectedRegion) {
                document.getElementById('waterMaskImage').innerHTML = '<p>Please select a region first.</p>';
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
            const bbox = { north, south, east, west };

            // Use water/land cover resolution dropdown as sat_scale (ESA fetch resolution in m/px).
            // Lower value = finer detail (100m = Sentinel native, 500m = fast/large regions).
            // The dropdown directly controls ESA data quality; output is always aligned to the DEM.
            const waterRes = parseInt(document.getElementById('waterResolution')?.value || '200');
            const landCoverRes = parseInt(document.getElementById('landCoverResolution')?.value || '200');
            const satScale = Math.min(waterRes, landCoverRes);  // finer wins
            const dim = lastDemData ? Math.max(lastDemData.width, lastDemData.height) : 400;

            // Build cache key including sat_scale and dataset so changing either busts the cache
            const waterDataset = document.getElementById('waterDataset')?.value || 'esa';
            let cacheKey = { ...bbox, sat_scale: satScale, dataset: waterDataset };
            if (lastDemData && lastDemData.width && lastDemData.height) {
                cacheKey.demWidth = lastDemData.width;
                cacheKey.demHeight = lastDemData.height;
            }

            // Check cache first
            const cachedData = waterMaskCache.get(cacheKey);
            if (cachedData) {
                // Verify cached dimensions match current DEM
                const dimsMatch = !lastDemData ||
                    (cachedData.water_mask_dimensions &&
                        cachedData.water_mask_dimensions[0] === lastDemData.height &&
                        cachedData.water_mask_dimensions[1] === lastDemData.width);

                if (dimsMatch) {
                    lastWaterMaskData = cachedData;
                    layerBboxes.water = bbox;
                    layerBboxes.landCover = bbox;
                    layerStatus.water = 'loaded';
                    layerStatus.landCover = 'loaded';
                    updateLayerStatusIndicators();
                    updateCacheStatusUI();

                    renderWaterMask(cachedData);
                    renderEsaLandCover(cachedData);  // Also render ESA land cover
                    // Ensure stacked view is updated when cached canvases are rendered
                    requestAnimationFrame(() => updateStackedLayers());
                    document.getElementById('waterMaskStats').innerHTML =
                        `Water pixels: ${cachedData.water_pixels} / ${cachedData.total_pixels} (${cachedData.water_percentage.toFixed(1)}%) <span style="color:#4CAF50;font-size:10px;">[CACHED]</span>`;
                    showToast(`Water & land cover loaded from cache`, 'success');
                    return;
                }
            }

            const params = new URLSearchParams({
                north, south, east, west,
                sat_scale: satScale,
                dim: dim,
                dataset: waterDataset
            });

            // If DEM is already loaded, pass its exact dimensions to ensure alignment
            if (lastDemData && lastDemData.width && lastDemData.height) {
                params.set('target_width', lastDemData.width);
                params.set('target_height', lastDemData.height);
                console.log(`[loadWaterMask] Using DEM dimensions for alignment: ${lastDemData.width}x${lastDemData.height}`);
            }

            // Update status to loading
            layerStatus.water = 'loading';
            layerStatus.landCover = 'loading';
            updateLayerStatusIndicators();

            document.getElementById('waterMaskImage').innerHTML = '<div class="loading"><span class="spinner"></span>Loading water mask from Earth Engine...</div>';
            showToast('Loading water mask from Earth Engine...', 'info');

            try {
                const response = await fetch(`/api/terrain/water-mask?${params}`, { signal });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                const data = await response.json();

                if (data.error) {
                    { const _p = document.createElement('p'); _p.textContent = `Error: ${data.error}`; document.getElementById('waterMaskImage').replaceChildren(_p); }
                    layerStatus.water = 'error';
                    layerStatus.landCover = 'error';
                    updateLayerStatusIndicators();
                    showToast('Failed to load water mask: ' + data.error, 'error');
                    return;
                }

                // Cache the result (use cacheKey which includes DEM dimensions)
                waterMaskCache.set(cacheKey, data);
                updateCacheStatusUI();

                lastWaterMaskData = data;

                // Track what bbox this data is for
                layerBboxes.water = bbox;
                layerBboxes.landCover = bbox;
                layerStatus.water = 'loaded';
                layerStatus.landCover = 'loaded';
                updateLayerStatusIndicators();

                // Render the water mask
                renderWaterMask(data);

                // Also render ESA land cover to satellite tab
                renderEsaLandCover(data);

                // Update stacked layers view
                requestAnimationFrame(() => updateStackedLayers());

                // Update stats
                document.getElementById('waterMaskStats').innerHTML =
                    `Water pixels: ${data.water_pixels} / ${data.total_pixels} (${data.water_percentage.toFixed(1)}%)`;

                showToast(`Water & land cover loaded`, 'success');

            } catch (error) {
                if (error.name === 'AbortError') return;
                console.error('Error loading water mask:', error);
                { const _p = document.createElement('p'); _p.textContent = `Error: ${error.message}`; document.getElementById('waterMaskImage').replaceChildren(_p); }
                layerStatus.water = 'error';
                layerStatus.landCover = 'error';
                updateLayerStatusIndicators();
                showToast('Failed to load water mask', 'error');
            }
        }

        /**
         * Render the water mask array to the `#waterMaskImage` canvas.
         * Water pixels are blue, land pixels are brown.
         * Applies the current map projection before display.
         * @param {Object} data - Water mask response `{water_mask_values, water_mask_dimensions}`
         */
        function renderWaterMask(data) {
            const container = document.getElementById('waterMaskImage');
            const values = data.water_mask_values;
            const h = data.water_mask_dimensions[0];
            const w = data.water_mask_dimensions[1];

            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            const imgData = ctx.createImageData(w, h);

            for (let i = 0; i < values.length; i++) {
                const val = values[i];
                const idx = i * 4;
                // Blue for water, transparent for land
                if (val > 0.5) {
                    imgData.data[idx] = 0;      // R
                    imgData.data[idx + 1] = 100; // G
                    imgData.data[idx + 2] = 255; // B
                    imgData.data[idx + 3] = 200; // A
                } else {
                    imgData.data[idx] = 100;    // R
                    imgData.data[idx + 1] = 80; // G
                    imgData.data[idx + 2] = 60; // B
                    imgData.data[idx + 3] = 255; // A
                }
            }

            ctx.putImageData(imgData, 0, 0);
            const projectedCanvas = currentDemBbox ? applyProjection(canvas, currentDemBbox) : canvas;
            container.innerHTML = '';
            container.appendChild(projectedCanvas);
            projectedCanvas.style.width = '100%';
            projectedCanvas.style.height = 'auto';
            // Trigger stacked view redraw when water mask canvas is ready
            requestAnimationFrame(() => updateStackedLayers());
        }

        /**
         * Render ESA WorldCover land cover classes to the `#satelliteImage` canvas.
         * Uses colours from `landCoverConfig` and applies the current projection.
         * @param {Object} data - ESA response `{esa_values, esa_dimensions}`
         */
        function renderEsaLandCover(data) {
            const container = document.getElementById('satelliteImage');
            const values = data.esa_values;
            const h = data.esa_dimensions[0];
            const w = data.esa_dimensions[1];

            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            const imgData = ctx.createImageData(w, h);

            // Use global landCoverConfig for colors
            const defaultColor = landCoverConfig[0]?.color || [0, 50, 150]; // No data/ocean color

            for (let i = 0; i < values.length; i++) {
                const raw = values[i];
                const val = Math.round(raw);
                const idx = i * 4;

                // Look up color from landCoverConfig, fallback to no-data color
                const config = landCoverConfig[val];
                const color = config ? config.color : defaultColor;

                imgData.data[idx] = color[0];
                imgData.data[idx + 1] = color[1];
                imgData.data[idx + 2] = color[2];
                imgData.data[idx + 3] = 255;
            }

            ctx.putImageData(imgData, 0, 0);
            const projLandCanvas = currentDemBbox ? applyProjection(canvas, currentDemBbox) : canvas;
            container.innerHTML = '';
            container.appendChild(projLandCanvas);
            projLandCanvas.style.width = '100%';
            projLandCanvas.style.height = 'auto';

            // Also render in satImage preview (properly copy canvas content)
            const previewContainer = document.getElementById('satelliteImage');
            if (previewContainer) {
                const previewCanvas = document.createElement('canvas');
                previewCanvas.width = projLandCanvas.width;
                previewCanvas.height = projLandCanvas.height;
                const previewCtx = previewCanvas.getContext('2d');
                previewCtx.drawImage(projLandCanvas, 0, 0);
                previewContainer.innerHTML = '';
                previewContainer.appendChild(previewCanvas);
                previewCanvas.style.width = '100%';
                previewCanvas.style.height = 'auto';
            }

            // Trigger stacked view redraw when ESA landcover canvas is ready
            requestAnimationFrame(() => updateStackedLayers());
        }

        // ============================================================
        // COMBINED VIEW
        // Composite of DEM colourmap + water overlay in one canvas.
        // ============================================================

        /**
         * Render the combined view: DEM colourmap with water overlay blended on top.
         * Auto-loads the water mask if not yet available for the current bbox.
         * @returns {Promise<void>}
         */
        async function renderCombinedView() {
            const container = document.getElementById('combinedImage');

            if (!lastDemData || !isLayerCurrent('dem')) {
                container.innerHTML = '<p style="text-align:center;padding:20px;">Load DEM first.</p>';
                return;
            }

            // Check if water mask needs loading
            if (!lastWaterMaskData || !isLayerCurrent('water')) {
                container.innerHTML = '<p style="text-align:center;padding:20px;">Loading water mask for combined view...</p>';
                await loadWaterMask();
            }

            // Verify dimensions match
            if (lastWaterMaskData && lastDemData) {
                const demSize = lastDemData.width * lastDemData.height;
                const waterSize = lastWaterMaskData.water_mask_values?.length ?? 0;

                if (demSize !== waterSize) {
                    console.warn('DEM and water mask dimension mismatch - reloading water mask');
                    await loadWaterMask();
                }
            }

            const colormap = document.getElementById('demColormap').value;
            const { values, width, height, vmin, vmax } = lastDemData;

            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            const imgData = ctx.createImageData(width, height);

            // Get water scale and opacity
            const waterScale = parseFloat(document.getElementById('waterScaleSlider')?.value || 0.05);
            const opacityVal = waterOpacity; // Use global waterOpacity from slider
            const waterVals = lastWaterMaskData?.water_mask_values || [];
            const ptp = vmax - vmin;

            for (let i = 0; i < values.length; i++) {
                // Apply water subtraction if water mask available
                let val = values[i];
                if (waterVals[i] && waterVals[i] > 0.5) {
                    val = val - (waterVals[i] * ptp * waterScale);
                }

                const t = Math.max(0, Math.min(1, (val - vmin) / (ptp || 1)));
                const [r, g, b] = mapElevationToColor(t, colormap);
                const idx = i * 4;

                // Blend with water overlay for visualization
                if (waterVals[i] && waterVals[i] > 0.5 && opacityVal > 0) {
                    imgData.data[idx] = Math.round((r * 255) * (1 - opacityVal) + 30 * opacityVal);
                    imgData.data[idx + 1] = Math.round((g * 255) * (1 - opacityVal) + 100 * opacityVal);
                    imgData.data[idx + 2] = Math.round((b * 255) * (1 - opacityVal) + 220 * opacityVal);
                } else {
                    imgData.data[idx] = Math.round(r * 255);
                    imgData.data[idx + 1] = Math.round(g * 255);
                    imgData.data[idx + 2] = Math.round(b * 255);
                }
                imgData.data[idx + 3] = 255;
            }

            ctx.putImageData(imgData, 0, 0);
            container.innerHTML = '';
            container.appendChild(canvas);
            canvas.style.width = '100%';
            canvas.style.height = 'auto';
            enableZoomAndPan(canvas);
        }

        /**
         * Load ESA land cover data for the satellite sub-tab.
         * Uses cached `lastWaterMaskData` if the layer is current; otherwise re-fetches.
         * @returns {Promise<void>}
         */
        async function loadSatelliteForTab() {
            const container = document.getElementById('satelliteImage');

            // Check if we have current data (matching bbox)
            if (lastWaterMaskData && lastWaterMaskData.esa_values && isLayerCurrent('landCover')) {
                renderEsaLandCover(lastWaterMaskData);
                return;
            }

            // Data is stale or missing - reload
            container.innerHTML = '<p style="text-align:center;padding:50px;">Loading land cover data...</p>';
            await loadWaterMask();

            if (lastWaterMaskData && lastWaterMaskData.esa_values) {
                renderEsaLandCover(lastWaterMaskData);
            } else {
                container.innerHTML = '<p style="text-align:center;padding:50px;">No land cover data available. Please select a region first.</p>';
            }
        }

        /**
         * Render a preview of the DEM with water pixels lowered by `#waterScaleSlider` and
         * blended with a blue water-colour overlay into `#combinedImage`.
         * Requires both `lastDemData` and `lastWaterMaskData` to be loaded.
         * @returns {Promise<void>}
         */
        async function previewWaterSubtract() {
            if (!lastDemData || !lastWaterMaskData) {
                document.getElementById('combinedImage').innerHTML = '<p>Load DEM and Water Mask first.</p>';
                return;
            }

            const waterScale = parseFloat(document.getElementById('waterScaleSlider').value);
            const opacityVal = waterOpacity; // Use global waterOpacity

            const demVals = lastDemData.values;
            const waterVals = lastWaterMaskData.water_mask_values;
            const w = lastDemData.width;
            const h = lastDemData.height;

            // Apply water subtraction
            const ptp = lastDemData.vmax - lastDemData.vmin;
            const adjustedDem = demVals.map((v, i) => {
                const waterVal = waterVals[i] ?? 0;
                return v - (waterVal * ptp * waterScale);
            });

            // Render combined view
            const colormap = document.getElementById('demColormap').value;
            const finiteVals = adjustedDem.filter(Number.isFinite);
            const vmin = finiteVals.reduce((a, b) => a < b ? a : b, finiteVals[0]);
            const vmax = finiteVals.reduce((a, b) => a > b ? a : b, finiteVals[0]);

            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            const imgData = ctx.createImageData(w, h);

            for (let i = 0; i < adjustedDem.length; i++) {
                const t = (adjustedDem[i] - vmin) / (vmax - vmin);
                const [r, g, b] = mapElevationToColor(t, colormap);
                const idx = i * 4;

                // Blend with water overlay
                const waterVal = waterVals[i] ?? 0;
                if (waterVal > 0.5 && opacityVal > 0) {
                    imgData.data[idx] = Math.round((r * 255) * (1 - opacityVal) + 0 * opacityVal);
                    imgData.data[idx + 1] = Math.round((g * 255) * (1 - opacityVal) + 100 * opacityVal);
                    imgData.data[idx + 2] = Math.round((b * 255) * (1 - opacityVal) + 255 * opacityVal);
                } else {
                    imgData.data[idx] = Math.round(r * 255);
                    imgData.data[idx + 1] = Math.round(g * 255);
                    imgData.data[idx + 2] = Math.round(b * 255);
                }
                imgData.data[idx + 3] = 255;
            }

            ctx.putImageData(imgData, 0, 0);
            const container = document.getElementById('combinedImage');
            container.innerHTML = '';
            container.appendChild(canvas);
            canvas.style.width = '100%';
            canvas.style.height = 'auto';
        }

        /**
         * Permanently apply water subtraction to `lastDemData.values` using
         * `#paramWaterScale` and the current water mask. Re-renders the DEM and
         * switches back to the `dem` sub-tab.
         */
        function applyWaterSubtract() {
            if (!lastDemData || !lastWaterMaskData) {
                showToast('Please load both DEM and Water Mask first.', 'warning');
                return;
            }

            const waterScale = parseFloat(document.getElementById('paramWaterScale').value);
            const demVals = lastDemData.values;
            const waterVals = lastWaterMaskData.water_mask_values;
            const ptp = lastDemData.vmax - lastDemData.vmin;

            // Apply water subtraction
            const adjustedDem = demVals.map((v, i) => {
                const waterVal = waterVals[i] ?? 0;
                return v - (waterVal * ptp * waterScale);
            });

            // Update lastDemData with adjusted values
            lastDemData.values = adjustedDem;
            const finiteVals = adjustedDem.filter(Number.isFinite);
            lastDemData.vmin = finiteVals.reduce((a, b) => a < b ? a : b, finiteVals[0]);
            lastDemData.vmax = finiteVals.reduce((a, b) => a > b ? a : b, finiteVals[0]);

            // Re-render DEM
            recolorDEM();
            switchDemSubtab('dem');

            showToast('Water subtraction applied to DEM.', 'success');
        }

        /**
         * Render the land cover legend as a colour-picker / elevation grid inside
         * `#landCoverLegend`. Wires change events to update `landCoverConfig`.
         */
        function renderLandCoverLegend() {
            const container = document.getElementById('landCoverLegend');
            if (!container) return;

            // Card-grid layout: each row has a color swatch, name, and elevation input
            const sortedKeys = Object.keys(landCoverConfig).map(Number).sort((a, b) => a - b);

            let html = '<div style="display:grid;grid-template-columns:28px 1fr 52px;gap:3px 6px;align-items:center;">';
            html += '<div style="font-size:9px;color:#666;grid-column:1">Color</div>';
            html += '<div style="font-size:9px;color:#666;">Type</div>';
            html += '<div style="font-size:9px;color:#666;">Elev</div>';

            for (const val of sortedKeys) {
                const config = landCoverConfig[val];
                const colorHex = '#' + config.color.map(c => c.toString(16).padStart(2, '0')).join('');
                html += `<input type="color" value="${colorHex}" data-lc-color="${val}"
                    title="${config.name}" style="width:26px;height:22px;border:1px solid #555;padding:1px;cursor:pointer;border-radius:3px;background:none;">`;
                html += `<span style="font-size:11px;color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${config.name}">${config.name}</span>`;
                html += `<input type="number" value="${config.elevation}" data-lc-elev="${val}"
                    step="0.01" min="-1" max="1"
                    style="width:100%;background:#3a3a3a;color:#ccc;border:1px solid #444;padding:2px;border-radius:3px;font-size:10px;">`;
            }
            html += '</div>';
            container.innerHTML = html;

            // Add event listeners
            container.querySelectorAll('input[data-lc-color]').forEach(input => {
                input.addEventListener('change', (e) => {
                    const val = parseInt(e.target.dataset.lcColor);
                    const hex = e.target.value;
                    // Convert hex to RGB
                    const r = parseInt(hex.substr(1, 2), 16);
                    const g = parseInt(hex.substr(3, 2), 16);
                    const b = parseInt(hex.substr(5, 2), 16);
                    landCoverConfig[val].color = [r, g, b];
                });
            });

            container.querySelectorAll('input[data-lc-elev]').forEach(input => {
                input.addEventListener('change', (e) => {
                    const val = parseInt(e.target.dataset.lcElev);
                    landCoverConfig[val].elevation = parseFloat(e.target.value) || 0;
                });
            });
        }

        /**
         * Wire land cover editor event handlers: render the legend and attach
         * Apply and Reset button listeners.
         */
        function setupLandCoverEditor() {
            // Render the legend
            renderLandCoverLegend();

            // Apply button - re-render with current settings
            const applyBtn = document.getElementById('applyLandCoverMapping');
            if (applyBtn) {
                applyBtn.onclick = () => {
                    if (lastWaterMaskData && lastWaterMaskData.esa_values) {
                        renderEsaLandCover(lastWaterMaskData);
                        showToast('Land cover colors applied', 'success');
                    } else {
                        showToast('No land cover data loaded', 'warning');
                    }
                };
            }

            // Reset button - restore default colors
            const resetBtn = document.getElementById('resetLandCoverMapping');
            if (resetBtn) {
                resetBtn.onclick = () => {
                    // Restore defaults
                    for (const key of Object.keys(landCoverConfigDefaults)) {
                        landCoverConfig[key] = JSON.parse(JSON.stringify(landCoverConfigDefaults[key]));
                    }
                    renderLandCoverLegend();
                    if (lastWaterMaskData && lastWaterMaskData.esa_values) {
                        renderEsaLandCover(lastWaterMaskData);
                    }
                    showToast('Land cover colors reset to defaults', 'info');
                };
            }

            // Resolution dropdown - reload on change
            const resolutionDropdown = document.getElementById('landCoverResolution');
            if (resolutionDropdown) {
                resolutionDropdown.onchange = () => {
                    loadWaterMask();
                };
            }
        }

        /**
         * Wire water mask tab event listeners: land cover editor, apply/preview water subtract,
         * and slider display updates.
         */
        function setupWaterMaskListeners() {
            // Water mask now loads automatically when region is selected

            // Setup land cover editor
            setupLandCoverEditor();

            const applyWaterSubtractBtn = document.getElementById('applyWaterSubtractBtn');
            if (applyWaterSubtractBtn) applyWaterSubtractBtn.onclick = applyWaterSubtract;

            const previewWaterSubtractBtn = document.getElementById('previewWaterSubtractBtn');
            if (previewWaterSubtractBtn) previewWaterSubtractBtn.onclick = previewWaterSubtract;

            // Slider value display updates
            const waterScaleSlider = document.getElementById('waterScaleSlider');
            if (waterScaleSlider) {
                waterScaleSlider.oninput = () => {
                    document.getElementById('waterScaleValue').textContent = waterScaleSlider.value;
                };
            }

            const waterOpacity = document.getElementById('waterOpacity');
            if (waterOpacity) {
                waterOpacity.oninput = () => {
                    document.getElementById('waterOpacityValue').textContent = waterOpacity.value;
                };
            }

            const waterThreshold = document.getElementById('waterThreshold');
            if (waterThreshold) {
                waterThreshold.oninput = () => {
                    document.getElementById('waterThresholdValue').textContent = waterThreshold.value;
                };
            }

            // (satellite sub-tab was removed; satellite loads automatically)
        }

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
                loadDEM().then(() => { loadWaterMask(); loadSatelliteImage(); });
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

        /**
         * Attach mouse-wheel zoom and drag-pan to a canvas element using CSS transforms.
         * Each call is independent — state is scoped to the closure.
         * Double-click resets to the original view.
         * @param {HTMLCanvasElement} canvas
         */
        function enableZoomAndPan(canvas) {
            if (!canvas) return;
            // Guard: remove previous listeners if canvas is reused
            if (canvas._zoomPanInited) return;
            canvas._zoomPanInited = true;

            let scale = 1, tx = 0, ty = 0;
            let dragging = false, lastX = 0, lastY = 0;

            function applyTransform() {
                canvas.style.transformOrigin = '0 0';
                canvas.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
                canvas.style.cursor = dragging ? 'grabbing' : (scale > 1 ? 'grab' : 'default');
            }

            canvas.addEventListener('wheel', e => {
                e.preventDefault();
                const rect   = canvas.getBoundingClientRect();
                const mouseX = e.clientX - rect.left;
                const mouseY = e.clientY - rect.top;
                const delta  = e.deltaY < 0 ? 1.15 : (1 / 1.15);
                const newScale = Math.max(1, Math.min(10, scale * delta));
                // Zoom towards the cursor
                tx = mouseX - (mouseX - tx) * (newScale / scale);
                ty = mouseY - (mouseY - ty) * (newScale / scale);
                scale = newScale;
                if (scale === 1) { tx = 0; ty = 0; }
                applyTransform();
            }, { passive: false });

            canvas.addEventListener('mousedown', e => {
                if (scale <= 1) return;
                dragging = true;
                lastX = e.clientX;
                lastY = e.clientY;
                e.preventDefault();
            });

            document.addEventListener('mousemove', e => {
                if (!dragging) return;
                tx += e.clientX - lastX;
                ty += e.clientY - lastY;
                lastX = e.clientX;
                lastY = e.clientY;
                applyTransform();
            });

            document.addEventListener('mouseup', () => {
                dragging = false;
                if (scale > 1) canvas.style.cursor = 'grab';
            });

            canvas.addEventListener('dblclick', () => {
                scale = 1; tx = 0; ty = 0;
                applyTransform();
            });
        }

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

        // DEM zoom/pan and visible-bounds calculation are provided by the
        // modular dem-viewer component (static/js/components/dem-viewer.js).
        // Legacy inline implementations removed to avoid duplication.


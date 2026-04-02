/**
 * modules/map-globe.js
 * ====================
 * Leaflet 2D map initialisation, tile layers, grid overlay,
 * bounding box drawing tools, and Three.js globe.
 *
 * Public API (exposed on window):
 *   window.resetBboxColorIndex()
 *   window.setTileLayer(layerKey)
 *   window.toggleMapLabels(show)
 *   window.toggleDemOverlay(show)        → Promise<boolean>
 *   window.toggleTerrainOverlay(show)
 *   window.setTerrainOverlayOpacity(opacity)
 *   window.updateFloatingTerrainButton(active)
 *   window.initMap()
 *   window.initMapGrid()
 *   window.updateMapGrid()
 *   window.toggleMapGrid(show)
 *   window.updateBboxIndicator(color)
 *   window.initGlobe()
 *   window.animateGlobe()
 *   window.BBOX_COLORS        (array)
 *   window.getDrawControl()
 *   window.getMapGridEnabled()
 *   window.setMapGridEnabled(v)
 *
 * Dependencies (resolved at call-time via window):
 *   window.api.dem.load(...)
 *   window.showToast(...)
 *   window.mapElevationToColor(...)   (from dem-loader.js)
 *   window.setBoundingBox(bounds)     (setter in app.js)
 *   window.setMap(map)
 *   window.setPreloadedLayer(layer)
 *   window.setEditMarkersLayer(layer)
 *   window.setDrawnItems(items)
 *   window.setGlobeScene(scene)
 *   window.setGlobeCamera(camera)
 *   window.setGlobeRenderer(renderer)
 *   window.setGlobe(globe)
 */

// ── Module-scope state ──────────────────────────────────────────────────────

let _map             = null;
let _preloadedLayer  = null;
let _editMarkersLayer = null;
let _drawnItems      = null;
let _globeScene      = null;
let _globeCamera     = null;
let _globeRenderer   = null;
let _globe           = null;

let currentTileLayer     = null;
let demOverlayLayer      = null;
let demOverlayLoading    = false;
let terrainOverlayLayer  = null;
let mapGridLayer         = null;
let mapGridEnabled       = false;
let _drawControl         = null;

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

// ── Public: bbox colour index reset ────────────────────────────────────────

window.resetBboxColorIndex = () => { currentBboxColorIndex = 0; };

// ── Tile layer ──────────────────────────────────────────────────────────────

/**
 * Switch the active Leaflet tile layer.
 * Removes the previous tile layer and adds the new one.
 * @param {string} layerKey - Key from the `TILE_LAYERS` object (e.g. 'osm', 'esri-world')
 */
function setTileLayer(layerKey) {
    const layerConfig = TILE_LAYERS[layerKey];
    if (!layerConfig || !_map) return;

    // Remove current tile layer
    if (currentTileLayer) {
        _map.removeLayer(currentTileLayer);
    }

    // Add new tile layer
    currentTileLayer = L.tileLayer(layerConfig.url, {
        attribution: layerConfig.attribution
    }).addTo(_map);
}
window.setTileLayer = setTileLayer;

// ── Labels overlay ───────────────────────────────────────────────────────────

let _labelsLayer = null;

/**
 * Add or remove a labels-only tile overlay on the map.
 * Uses CartoDB Voyager labels (transparent background, works on any base layer).
 * @param {boolean} show
 */
window.toggleMapLabels = function toggleMapLabels(show) {
    if (!_map) return;
    if (show && !_labelsLayer) {
        _labelsLayer = L.tileLayer(
            'https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png',
            { attribution: '&copy; CartoDB', opacity: 1 }
        ).addTo(_map);
    } else if (!show && _labelsLayer) {
        _map.removeLayer(_labelsLayer);
        _labelsLayer = null;
    }
};

// ── DEM overlay ─────────────────────────────────────────────────────────────

/**
 * Toggle the DEM terrain overlay on the Leaflet map.
 * Strategy 1: uses a pre-generated global PNG if available.
 * Strategy 2: generates a DEM image for the current viewport on demand.
 * @param {boolean} show - true to enable the overlay, false to remove it
 * @returns {Promise<boolean>} Resolves to the new overlay state
 */
async function toggleDemOverlay(show) {
    if (!_map) return;

    if (show) {
        if (demOverlayLoading) return true;
        demOverlayLoading = true;

        if (demOverlayLayer) { _map.removeLayer(demOverlayLayer); demOverlayLayer = null; }

        try {
            window.showToast('Loading terrain overlay...', 'info');

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
                    ).addTo(_map);
                    window.showToast('Terrain overlay loaded', 'success');
                    usedGlobal = true;
                }
            } catch (_) {}

            // ── Strategy 2: generate on demand via preview_dem ─────────
            if (!usedGlobal) {
                const bounds = _map.getBounds();
                const north = bounds.getNorth(), south = bounds.getSouth();
                const east = bounds.getEast(), west = bounds.getWest();
                const colormap = document.getElementById('demColormap')?.value || 'terrain';
                const { data, error: demErr } = await window.api.dem.load(
                    `north=${north}&south=${south}&east=${east}&west=${west}&dim=150&colormap=${colormap}&projection=none&subtract_water=false&depth_scale=1`
                );
                if (demErr) throw new Error(demErr);
                if (data && data.dem_values && data.dimensions) {
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
                        const [r, g, b] = window.mapElevationToColor(t, colormap);
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
                    ).addTo(_map);
                    window.showToast('Terrain overlay loaded', 'success');
                }
            }
        } catch (err) {
            console.error('DEM overlay error:', err);
            window.showToast('Failed to load terrain overlay', 'error');
        } finally {
            demOverlayLoading = false;
        }

        // Show opacity control
        document.getElementById('terrainOpacityGroup').style.display = 'flex';
        document.getElementById('terrainOpacityLabel').style.display = '';
        return true;
    } else {
        if (demOverlayLayer) {
            _map.removeLayer(demOverlayLayer);
            demOverlayLayer = null;
        }
        // Hide opacity control
        document.getElementById('terrainOpacityGroup').style.display = 'none';
        document.getElementById('terrainOpacityLabel').style.display = 'none';
        return false;
    }
}
window.toggleDemOverlay = toggleDemOverlay;

// Fallback: terrain relief overlay (hillshade tiles)

/**
 * Toggle the terrain overlay (delegates to toggleDemOverlay).
 * @param {boolean} show - true to show, false to hide
 */
function toggleTerrainOverlay(show) {
    if (!_map) return;
    // Always use DEM overlay from current map viewport
    toggleDemOverlay(show);
}
window.toggleTerrainOverlay = toggleTerrainOverlay;

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
window.setTerrainOverlayOpacity = setTerrainOverlayOpacity;

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
window.updateFloatingTerrainButton = updateFloatingTerrainButton;

// ── Map initialisation ──────────────────────────────────────────────────────

/**
 * Initialise the Leaflet map: tile layer, draw controls, feature groups,
 * bounding box draw event, and map grid.
 */
function initMap() {
    _map = L.map('map', {
        minZoom: 2,
        maxZoom: 18,
        worldCopyJump: true
    }).setView([20, 0], 2);

    window.setMap?.(_map);

    // Use tile layer system
    setTileLayer('osm');

    _preloadedLayer   = new L.FeatureGroup().addTo(_map);
    _editMarkersLayer = new L.FeatureGroup().addTo(_map);
    _drawnItems       = new L.FeatureGroup().addTo(_map);

    window.setPreloadedLayer?.(_preloadedLayer);
    window.setEditMarkersLayer?.(_editMarkersLayer);
    window.setDrawnItems?.(_drawnItems);

    // Hide edit markers when their bbox is too small on screen (< 40px diagonal)
    function _updateEditMarkerVisibility() {
        if (!_editMarkersLayer) return;
        _editMarkersLayer.eachLayer(marker => {
            if (!marker._regionBounds) return;
            const ne = _map.latLngToContainerPoint(marker._regionBounds.getNorthEast());
            const sw = _map.latLngToContainerPoint(marker._regionBounds.getSouthWest());
            const pxDiag = Math.sqrt(Math.pow(ne.x - sw.x, 2) + Math.pow(ne.y - sw.y, 2));
            const el = marker.getElement?.();
            if (el) el.style.display = pxDiag < 40 ? 'none' : '';
        });
    }
    _map.on('zoomend', _updateEditMarkerVisibility);
    _map.on('moveend', _updateEditMarkerVisibility);

    // Get next color for drawn rectangles
    /**
     * Return the next colour string from `BBOX_COLORS` in round-robin order.
     * @returns {string} CSS colour string
     */
    function getNextBboxColor() {
        const colorObj = (window.BBOX_COLORS || BBOX_COLORS)[currentBboxColorIndex % (window.BBOX_COLORS || BBOX_COLORS).length];
        currentBboxColorIndex++;
        return colorObj.color;
    }

    _drawControl = new L.Control.Draw({
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
            featureGroup: _drawnItems
        }
    });
    _map.addControl(_drawControl);

    _map.on(L.Draw.Event.CREATED, function (event) {
        // Don't clear - keep old boxes visible with different colors
        const layer = event.layer;
        const bboxColor = getNextBboxColor();
        layer.setStyle({
            color: bboxColor,
            weight: 3,
            fillColor: bboxColor,
            fillOpacity: 0.15
        });
        _drawnItems.addLayer(layer);
        window.setBoundingBox?.(layer.getBounds());

        // Update the current box indicator
        updateBboxIndicator(bboxColor);
    });

    // Initialize map grid
    initMapGrid();
}
window.initMap = initMap;

// ── Map grid (graticule) ────────────────────────────────────────────────────

/**
 * Initialise the Leaflet layer group used for the map graticule (grid).
 */
function initMapGrid() {
    // Create a custom graticule layer
    mapGridLayer = L.layerGroup();
}
window.initMapGrid = initMapGrid;

/**
 * Redraw the map graticule (grid) for the current viewport and zoom level.
 * Called on map moveend events when the grid is enabled.
 */
function updateMapGrid() {
    if (!_map || !mapGridLayer) return;

    mapGridLayer.clearLayers();

    if (!mapGridEnabled) return;

    const bounds = _map.getBounds();
    const zoom = _map.getZoom();

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
window.updateMapGrid = updateMapGrid;

/**
 * Show or hide the map graticule overlay.
 * @param {boolean} show - true to enable the grid, false to remove it
 */
function toggleMapGrid(show) {
    mapGridEnabled = show;

    if (show) {
        if (mapGridLayer) {
            mapGridLayer.addTo(_map);
            updateMapGrid();

            // Update grid on zoom/pan
            _map.on('moveend', updateMapGrid);
        }
    } else {
        if (mapGridLayer) {
            _map.removeLayer(mapGridLayer);
            _map.off('moveend', updateMapGrid);
        }
    }

    // Update button state
    const btn = document.getElementById('floatingGridToggle');
    if (btn) {
        btn.classList.toggle('active', show);
        btn.title = show ? 'Grid ON (click to hide)' : 'Toggle grid lines';
    }
}
window.toggleMapGrid = toggleMapGrid;

// ── BBox colour indicator ───────────────────────────────────────────────────

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
window.updateBboxIndicator = updateBboxIndicator;

// ── Globe ───────────────────────────────────────────────────────────────────

/**
 * Initialise the Three.js globe: scene, camera, renderer, sphere geometry,
 * lighting, and the RAF animation loop.
 */
function initGlobe() {
    try {
        const container = document.getElementById('globe');
        if (!container || container.clientWidth === 0 || container.clientHeight === 0) {
            return; // Globe tab not yet visible — init deferred until first activation
        }

        _globeScene    = new THREE.Scene();
        _globeCamera   = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
        _globeRenderer = new THREE.WebGLRenderer({ antialias: true });
        _globeRenderer.setSize(container.clientWidth, container.clientHeight);
        container.appendChild(_globeRenderer.domElement);

        window.setGlobeScene?.(_globeScene);
        window.setGlobeCamera?.(_globeCamera);
        window.setGlobeRenderer?.(_globeRenderer);

        // Create globe geometry
        const geometry = new THREE.SphereGeometry(5, 64, 64);
        const material = new THREE.MeshPhongMaterial({
            color: 0x2233ff,
            transparent: true,
            opacity: 0.8
        });
        _globe = new THREE.Mesh(geometry, material);
        _globeScene.add(_globe);

        window.setGlobe?.(_globe);

        // Add lighting
        const ambientLight = new THREE.AmbientLight(0x404040);
        _globeScene.add(ambientLight);

        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.5);
        directionalLight.position.set(1, 1, 1);
        _globeScene.add(directionalLight);

        _globeCamera.position.z = 10;

        // Add coordinate markers group
        _globeScene.add(new THREE.Group()); // Index 2 will be for markers

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
window.initGlobe = initGlobe;

/**
 * RAF animation loop for the Three.js globe.
 * Rotates the globe on the Y axis each frame.
 */
function animateGlobe() {
    requestAnimationFrame(animateGlobe);
    _globe.rotation.y += 0.005;
    _globeRenderer.render(_globeScene, _globeCamera);
}
window.animateGlobe = animateGlobe;

// ── Expose BBOX_COLORS and other constants ──────────────────────────────────

window.BBOX_COLORS = BBOX_COLORS;

// ── Expose drawControl and mapGridEnabled accessors ─────────────────────────

window.getDrawControl    = () => _drawControl;
window.getMapGridEnabled = () => mapGridEnabled;
window.setMapGridEnabled = (v) => { mapGridEnabled = v; };

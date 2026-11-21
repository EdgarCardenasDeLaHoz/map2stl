/**
 * explore/leafletMap.ts - the Explore page's Leaflet map: base map, labels, terrain relief
 * overlay, grid and the rectangle draw tool (F-UNIFY Phase 6; was modules/map/map-globe.js).
 *
 * ExploreMap.vue calls initMap() once the app-state bridge is ready. The map and its feature
 * groups are registered through the legacy window accessors (setMap, setPreloadedLayer, ...),
 * so Edit, Extrude and new-region.js keep reading `window.getMap()` as before. Every layer
 * change is mirrored into mapLayers.ts, which the Map menu renders.
 *
 * Depends on globals: L (Leaflet + Leaflet.draw), window.showToast, window.api.dem,
 * window.decodeDemValues, window.mapElevationToColor, window.newRegion.
 */
import { DEFAULT_STYLE, isTileStyle, mapLayers, STYLE_KEY } from './mapLayers';

declare const L: any;

let _map: any = null;
let _preloadedLayer: any = null;
let _editMarkersLayer: any = null;
let _drawnItems: any = null;

let currentTileLayer: any = null;
let demOverlayLayer: any = null;
let demOverlayLoading = false;
let terrainOverlayLayer: any = null;
let mapGridLayer: any = null;
let mapGridEnabled = true;  // every grid starts on (2026-10-14)
let _drawControl: any = null;

const TILE_LAYERS: Record<string, { url: string; attribution: string }> = {
    'osm': {
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    },
    'osm-topo': {
        url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
        attribution: 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | '
            + 'Map style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'
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

// ── Tile layer ──────────────────────────────────────────────────────────────

/**
 * Switch the active Leaflet tile layer.
 * Removes the previous tile layer and adds the new one.
 * @param layerKey - Key from the `TILE_LAYERS` object (e.g. 'osm', 'esri-world')
 */
export function setTileLayer(layerKey: string): void {
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
    mapLayers.style = layerKey;
    try { localStorage.setItem(STYLE_KEY, layerKey); } catch (_) { /* per-browser preference */ }
}

// ── Labels overlay ───────────────────────────────────────────────────────────

let _labelsLayer: any = null;

/**
 * Add or remove a labels-only tile overlay on the map.
 * Uses CartoDB Voyager labels (transparent background, works on any base layer).
 * @param {boolean} show
 */
export function toggleMapLabels(show: boolean): void {
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
    mapLayers.labels = !!show;
}

// ── DEM overlay ─────────────────────────────────────────────────────────────

/** Leaflet's own latitude clamp for EPSG:3857. */
const MERCATOR_MAX_LAT = 85.0511287798;

const DEM_OVERLAY_PANE = 'demOverlayPane';

/** Web Mercator y for a latitude in degrees, in radians of projected space. */
function _mercatorY(latDeg: number): number {
    const lat = Math.max(-MERCATOR_MAX_LAT, Math.min(MERCATOR_MAX_LAT, latDeg)) * Math.PI / 180;
    return Math.log(Math.tan(Math.PI / 4 + lat / 2));
}

/**
 * Own pane for the terrain overlay, sitting above the basemap tiles (z-index
 * 200) but below the vector overlay pane (400). Added to the default overlay
 * pane, the image is appended after the vector <svg> and so paints over every
 * region rectangle and drawn shape on the map.
 * @returns {string} The pane name, for passing to a layer's options.
 */
function ensureDemOverlayPane() {
    if (_map && !_map.getPane(DEM_OVERLAY_PANE)) {
        _map.createPane(DEM_OVERLAY_PANE).style.zIndex = 250;
    }
    return DEM_OVERLAY_PANE;
}

/**
 * Load the pre-rendered global DEM PNG and resample it from equirectangular
 * (plate carree, the projection the file is written in) to Web Mercator.
 *
 * Handing the source image straight to Leaflet with its own bounds is what
 * this used to do, and it misregisters badly: Leaflet clamps the bounds to
 * +/-85.05 and then stretches a linear-latitude image across the nonlinear
 * Mercator axis. Measured against the basemap, 45N was drawn at 66.5N and 30N
 * at 51.3N - only the equator landed in the right place. The on-demand path in
 * toggleDemOverlay already resamples this way; this makes the cached path
 * agree with it.
 *
 * @param {object|null} meta - Parsed global_dem_meta.json, for the source bounds.
 * @returns {Promise<L.ImageOverlay|null>} null if the image or its pixels are unreadable.
 */
function buildGlobalDemOverlay(meta: any): Promise<any> {
    return new Promise((resolve) => {
        const img = new Image();
        img.onerror = () => resolve(null);
        img.onload = () => {
            const north = Number(meta?.north ?? 90);
            const south = Number(meta?.south ?? -90);
            const east = Number(meta?.east ?? 180);
            const west = Number(meta?.west ?? -180);
            const w = img.naturalWidth, h = img.naturalHeight;
            const latRange = north - south;
            const lonSpanRad = (east - west) * Math.PI / 180;
            if (!w || !h || latRange <= 0 || lonSpanRad <= 0) return resolve(null);

            const src = document.createElement('canvas');
            src.width = w; src.height = h;
            const srcCtx = src.getContext('2d')!;
            srcCtx.drawImage(img, 0, 0);
            let srcData: Uint8ClampedArray;
            try {
                srcData = srcCtx.getImageData(0, 0, w, h).data;
            } catch (_) {
                // Tainted canvas. Better no overlay than one in the wrong place.
                return resolve(null);
            }

            const outN = Math.min(north, MERCATOR_MAX_LAT);
            const outS = Math.max(south, -MERCATOR_MAX_LAT);
            const mercN = _mercatorY(outN), mercRange = mercN - _mercatorY(outS);
            // A full-globe Mercator square is as tall as it is wide, so the
            // output needs more rows than the 2:1 source to keep detail at high
            // latitude, where Mercator spreads a few source rows over many.
            const outH = Math.max(2, Math.min(4096, Math.round(w * mercRange / lonSpanRad)));

            const dst = document.createElement('canvas');
            dst.width = w; dst.height = outH;
            const dstCtx = dst.getContext('2d')!;
            const dstImg = dstCtx.createImageData(w, outH);
            for (let y = 0; y < outH; y++) {
                const mv = mercN - (y / (outH - 1)) * mercRange;
                const lat = (2 * Math.atan(Math.exp(mv)) - Math.PI / 2) * 180 / Math.PI;
                const srcY = Math.round((north - lat) / latRange * (h - 1));
                if (srcY < 0 || srcY >= h) continue;
                dstImg.data.set(srcData.subarray(srcY * w * 4, (srcY + 1) * w * 4), y * w * 4);
            }
            dstCtx.putImageData(dstImg, 0, 0);

            resolve(L.imageOverlay(
                dst.toDataURL('image/png'),
                [[outS, west], [outN, east]],
                { opacity: 0.7, interactive: false, pane: ensureDemOverlayPane() }
            ));
        };
        // Same-origin, so the canvas stays untainted and toDataURL works.
        img.src = `/static/global_dem.png?_=${Date.now()}`;
    });
}

/**
 * Toggle the DEM terrain overlay on the Leaflet map.
 * Strategy 1: uses a pre-generated global PNG if available.
 * Strategy 2: generates a DEM image for the current viewport on demand.
 * @param {boolean} show - true to enable the overlay, false to remove it
 * @returns {Promise<boolean>} Resolves to the new overlay state
 */
async function toggleDemOverlay(show: boolean): Promise<boolean | undefined> {
    if (!_map) return;

    if (show) {
        if (demOverlayLoading) return true;
        demOverlayLoading = true;

        if (demOverlayLayer) { _map.removeLayer(demOverlayLayer); demOverlayLayer = null; }

        try {
            (window as any).showToast('Loading terrain overlay...', 'info');

            // ── Strategy 1: pre-cached global DEM PNG ──────────────────
            // Generated at server startup; always covers the full globe.
            let usedGlobal = false;
            try {
                const metaResp = await fetch('/static/global_dem_meta.json');
                if (metaResp.ok) {
                    // The meta's bounds describe the PNG's own extent, in the
                    // equirectangular projection it was written in. They are what
                    // the resample reads; discarding them and assuming a full
                    // globe is how the overlay came to be drawn 20 degrees north
                    // of where it belongs.
                    const meta = await metaResp.json().catch(() => null);
                    const layer = await buildGlobalDemOverlay(meta);
                    if (layer) {
                        demOverlayLayer = layer.addTo(_map);
                        (window as any).showToast('Terrain overlay loaded', 'success');
                        usedGlobal = true;
                    }
                }
            } catch (_) { /* best-effort; failure is non-fatal */ }

            // ── Strategy 2: generate on demand via preview_dem ─────────
            if (!usedGlobal) {
                const bounds = _map.getBounds();
                const north = bounds.getNorth(), south = bounds.getSouth();
                const east = bounds.getEast(), west = bounds.getWest();
                const colormap = (document.getElementById('demColormap') as HTMLSelectElement | null)?.value || 'terrain';
                const { data, error: demErr } = await (window as any).api.dem.load(
                    `north=${north}&south=${south}&east=${east}&west=${west}&dim=150&colormap=${colormap}&projection=none&subtract_water=false&depth_scale=1`
                );
                if (demErr) throw new Error(demErr);
                if (data && (data.dem_values || data.dem_values_b64) && data.dimensions) {
                    let demVals: any = (window as any).decodeDemValues(data);
                    let h = Number(data.dimensions[0]);
                    let w = Number(data.dimensions[1]);
                    if (Array.isArray(demVals) && demVals.length && Array.isArray(demVals[0])) {
                        h = demVals.length; w = demVals[0].length; demVals = demVals.flat();
                    }
                    const vmin = data.min_elevation ?? 0;
                    const vmax = data.max_elevation ?? 1;
                    const canvas = document.createElement('canvas');
                    canvas.width = w; canvas.height = h;
                    const ctx = canvas.getContext('2d')!;
                    const imgData = ctx.createImageData(w, h);
                    for (let i = 0; i < demVals.length; i++) {
                        let t = (demVals[i] - vmin) / ((vmax - vmin) || 1);
                        t = Math.max(0, Math.min(1, t));
                        const [r, g, b] = (window as any).mapElevationToColor(t, colormap);
                        imgData.data[i * 4] = Math.round(r * 255); imgData.data[i * 4 + 1] = Math.round(g * 255);
                        imgData.data[i * 4 + 2] = Math.round(b * 255); imgData.data[i * 4 + 3] = 200;
                    }
                    ctx.putImageData(imgData, 0, 0);
                    // Re-project to Web Mercator so the canvas aligns correctly with map tiles
                    const ob = data.bbox;
                    const _bN = ob ? ob[3] : north, _bS = ob ? ob[1] : south;
                    const _bE = ob ? ob[2] : east, _bW = ob ? ob[0] : west;
                    const _toRad = (d: number) => d * Math.PI / 180;
                    const _mercY = (l: number) => Math.log(Math.tan(Math.PI / 4 + _toRad(Math.max(-85, Math.min(85, l))) / 2));
                    const _mN = _mercY(_bN), _mS = _mercY(_bS), _mRange = _mN - _mS;
                    const _latRange = _bN - _bS;
                    const projCanvas = document.createElement('canvas');
                    projCanvas.width = w; projCanvas.height = h;
                    const projCtx = projCanvas.getContext('2d')!;
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
                        { opacity: 0.7, interactive: false, pane: ensureDemOverlayPane() }
                    ).addTo(_map);
                    (window as any).showToast('Terrain overlay loaded', 'success');
                }
            }
        } catch (err) {
            console.error('DEM overlay error:', err);
            (window as any).showToast('Failed to load terrain overlay', 'error');
        } finally {
            demOverlayLoading = false;
        }


        // The overlay was drawn at a fixed 70%: apply the slider's value.
        demOverlayLayer?.setOpacity?.(mapLayers.terrainOpacity / 100);
        const on = !!demOverlayLayer;
        mapLayers.terrain = on;
        document.getElementById('terrainOpacityGroup')?.classList.toggle('hidden', !on);
        document.getElementById('terrainOpacityLabel')?.classList.toggle('hidden', !on);
        return on;
    }
    if (demOverlayLayer) {
        _map.removeLayer(demOverlayLayer);
        demOverlayLayer = null;
    }
    mapLayers.terrain = false;
    document.getElementById('terrainOpacityGroup')?.classList.add('hidden');
    document.getElementById('terrainOpacityLabel')?.classList.add('hidden');
    return false;
}

/**
 * Toggle the terrain relief overlay (the DEM of the current viewport, or the global one).
 * @param show - true to show, false to hide
 */
export function toggleTerrainOverlay(show: boolean): void {
    if (!_map) return;
    mapLayers.terrain = !!show;   // the switch moves at once; it falls back if the overlay fails
    void toggleDemOverlay(show);
}

/**
 * Set opacity of the active DEM/terrain overlay layer.
 * @param opacity - Opacity value 0-100 (converted to 0-1 internally)
 */
export function setTerrainOverlayOpacity(opacity: number): void {
    const pct = Number(opacity);
    mapLayers.terrainOpacity = pct;
    if (demOverlayLayer) demOverlayLayer.setOpacity(pct / 100);
    if (terrainOverlayLayer) terrainOverlayLayer.setOpacity(pct / 100);
}

// ── Map initialisation ──────────────────────────────────────────────────────

/** Hide edit markers when their bbox is too small on screen (< 40px diagonal). */
function _updateEditMarkerVisibility(): void {
    if (!_editMarkersLayer || !_map) return;
    _editMarkersLayer.eachLayer((marker: any) => {
        if (!marker._regionBounds) return;
        const ne = _map.latLngToContainerPoint(marker._regionBounds.getNorthEast());
        const sw = _map.latLngToContainerPoint(marker._regionBounds.getSouthWest());
        const pxDiag = Math.hypot(ne.x - sw.x, ne.y - sw.y);
        const el = marker.getElement?.();
        if (el) el.style.display = pxDiag < 40 ? 'none' : '';
    });
}

/**
 * Initialise the Leaflet map on `el`: tile layer, draw controls, feature groups, the draw
 * event and the grid. Registers the map and its layers with the legacy window accessors
 * (`getMap`, `getPreloadedLayer`, ...) that Edit, Extrude and new-region.js read.
 */
export function initMap(el: string | HTMLElement = 'map'): any {
    if (_map) return _map;
    _map = L.map(el, {
        minZoom: 2,
        maxZoom: 18,
        worldCopyJump: true,
    }).setView([20, 0], 2);

    const w = window as any;
    w.setMap?.(_map);

    // Relief (OpenTopoMap) by default, so mountains and valleys show while choosing an area to
    // print (F-DESIGN new-region flow, 2026-10-02); the Map menu's choice is remembered.
    let style = DEFAULT_STYLE;
    try { style = localStorage.getItem(STYLE_KEY) || style; } catch (_) { /* default */ }
    if (!isTileStyle(style)) style = DEFAULT_STYLE;
    setTileLayer(style);

    _preloadedLayer = new L.FeatureGroup().addTo(_map);
    _editMarkersLayer = new L.FeatureGroup().addTo(_map);
    _drawnItems = new L.FeatureGroup().addTo(_map);
    w.setPreloadedLayer?.(_preloadedLayer);
    w.setEditMarkersLayer?.(_editMarkersLayer);
    w.setDrawnItems?.(_drawnItems);

    _map.on('zoomend', _updateEditMarkerVisibility);
    _map.on('moveend', _updateEditMarkerVisibility);

    _drawControl = new L.Control.Draw({
        draw: {
            rectangle: {
                shapeOptions: {
                    color: '#0a84ff',   // the selection accent (F-DESIGN)
                    weight: 2,
                    fillOpacity: 0.1,
                },
            },
            polygon: false,
            circle: false,
            marker: false,
            polyline: false,
        },
        edit: { featureGroup: _drawnItems },
    });
    _map.addControl(_drawControl);

    // A drawn box becomes the pending new region (new-region.js), named in NewRegionCard.vue.
    _map.on(L.Draw.Event.CREATED, (event: any) => {
        _drawnItems.addLayer(event.layer);
        w.newRegion?.onCreated(event.layer);
    });

    mapGridLayer = L.layerGroup();
    toggleMapGrid(mapGridEnabled);
    return _map;
}

/** Remove the map (the Explore page is gone), so a remount starts clean. */
export function destroyMap(): void {
    if (!_map) return;
    const w = window as any;
    _map.remove();
    _map = null;
    _preloadedLayer = _editMarkersLayer = _drawnItems = _drawControl = null;
    currentTileLayer = demOverlayLayer = _labelsLayer = mapGridLayer = null;
    w.setMap?.(null);
    w.setPreloadedLayer?.(null);
    w.setEditMarkersLayer?.(null);
    w.setDrawnItems?.(null);
}

// ── Map grid (graticule) ────────────────────────────────────────────────────

/**
 * Redraw the map graticule (grid) for the current viewport and zoom level.
 * Called on map moveend events when the grid is enabled.
 */
function updateMapGrid(): void {
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


    // Draw latitude lines
    for (let lat = south; lat <= north; lat += interval) {
        const line = L.polyline([[lat, west], [lat, east]], gridStyle);
        mapGridLayer.addLayer(line);

        // Add label
        if (zoom >= 5) {
            const label = L.marker([lat, bounds.getCenter().lng], {
                icon: L.divIcon({
                    className: 'grid-label',
                    html: `<span style="background:rgba(0,0,0,0.6);color:#fff;padding:1px 3px;font-size:11px;border-radius:2px;">${lat.toFixed(lat % 1 === 0 ? 0 : 2)}°</span>`,
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
                    html: `<span style="background:rgba(0,0,0,0.6);color:#fff;padding:1px 3px;font-size:11px;border-radius:2px;">${lng.toFixed(lng % 1 === 0 ? 0 : 2)}°</span>`,
                    iconSize: [40, 12]
                })
            });
            mapGridLayer.addLayer(label);
        }
    }
}


/**
 * Show or hide the map graticule overlay.
 * @param show - true to enable the grid, false to remove it
 */
export function toggleMapGrid(show: boolean): void {
    mapGridEnabled = show;
    mapLayers.grid = show;
    if (!_map || !mapGridLayer) return;
    if (show) {
        mapGridLayer.addTo(_map);
        updateMapGrid();
        _map.on('moveend', updateMapGrid);   // Leaflet ignores a listener it already has
    } else {
        _map.removeLayer(mapGridLayer);
        _map.off('moveend', updateMapGrid);
    }
}

export const getMap = (): any => _map;
export const getDrawControl = (): any => _drawControl;
export const getMapGridEnabled = (): boolean => mapGridEnabled;
export const setMapGridEnabled = (v: boolean): void => { mapGridEnabled = v; mapLayers.grid = v; };

/** The legacy window names other modules call (Edit's map controls, new-region.js). */
export function installLeafletGlobals(): void {
    const w = window as any;
    w.setTileLayer = setTileLayer;
    w.toggleMapLabels = toggleMapLabels;
    w.toggleTerrainOverlay = toggleTerrainOverlay;
    w.setTerrainOverlayOpacity = setTerrainOverlayOpacity;
    w.toggleMapGrid = toggleMapGrid;
    w.getDrawControl = getDrawControl;
    w.getMapGridEnabled = getMapGridEnabled;
    w.setMapGridEnabled = setMapGridEnabled;
}

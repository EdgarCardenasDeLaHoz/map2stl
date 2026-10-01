/**
 * modules/regions/region-boxes.js — saved-region boxes on the Explore (Leaflet) map,
 * and the viewport set the sidebar list shares with it.
 *
 * - Boxes are outlines over a dark halo, not filled tiles, so overlapping regions
 *   do not tint the map. Selected = accent colour; hovered = brighter + name tooltip.
 *   Styles: region-geometry.js::regionBoxStyle / regionHaloStyle.
 * - Only the viewport set is drawn: viewport-regions.js::selectViewportRegions
 *   (≤ 20 regions that intersect and fit the view, largest first, plus the
 *   selected one), recomputed on moveend/zoomend/resize (debounced 150 ms), on region
 *   load, on selection and on a continent-filter change. The sidebar list
 *   (region-ui.js::renderCoordinatesList) reads the same set through
 *   getViewportRegionSet(), so map and list show the same regions.
 * - Hover is linked both ways: a box hover marks its list row, a row hover
 *   highlights its box (drawing it for the moment if it is not in the set).
 *
 * Public API (on window):
 *   drawRegionBoxes(regions)        — rebuild the boxes, then refreshRegionViewSet
 *   refreshRegionViewSet({force})   — recompute the set, redraw, re-render the list if it changed
 *   getViewportRegionSet()          — {regions, inViewCount, total} last computed
 *   highlightRegionBox(name, on)    — row-hover hook used by region-ui.js
 *
 * Dependencies (resolved at call time):
 *   window.getMap(), window.getPreloadedLayer(), window.getEditMarkersLayer()
 *   window.getCoordinatesData(), window.appState.selectedRegion
 *   window.resolveRegionContinent(r), window.renderCoordinatesList()
 *   window.selectCoordinate(i), window.goToEdit(i), window.events / window.EV
 */

import { regionBoxStyle, regionHaloStyle } from './region-geometry.js';
import { selectViewportRegions } from './viewport-regions.js';

const VIEW_DEBOUNCE_MS = 150;

/** name → {region, index, rect, halo}; every saved region, drawn or not. */
let _boxes = new Map();
/** Names currently on the map, in draw order. */
let _drawn = [];
let _viewSet = null;
let _listKey = '';
let _hoveredName = null;
let _mapHooked = false;
let _debounce = null;

function _viewBox(map) {
    // A map not laid out yet (hidden container) has zero size and a zero-area
    // view in which nothing fits; treat it as the whole world instead.
    const size = map.getSize?.();
    if (!size || !size.x || !size.y) return null;
    try {
        const b = map.getBounds();
        return { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() };
    } catch (_) {
        return null;   // map not laid out yet
    }
}

function _stateFor(name) {
    if (window.appState?.selectedRegion?.name === name) return 'selected';
    if (_hoveredName === name) return 'hover';
    return 'normal';
}

function _style(box) {
    const state = _stateFor(box.region.name);
    box.rect.setStyle(regionBoxStyle(state));
    box.halo.setStyle(regionHaloStyle(state));
    if (state !== 'normal') { box.halo.bringToFront(); box.rect.bringToFront(); }
}

function _markRow(name, on) {
    const list = document.getElementById('coordinatesList');
    if (!list || !name) return;
    const row = list.querySelector(`.coordinate-item[data-region-name="${CSS.escape(name)}"]`);
    row?.classList.toggle('map-hover', on);
}

/** Put exactly the view set on the map, largest first so small boxes sit on top. */
function _applyMapSet() {
    const layer = window.getPreloadedLayer?.();
    if (!layer || !_viewSet) return;
    layer.clearLayers();
    _drawn = [];
    // The set is largest first, except the selected region appended at the end.
    for (const region of _viewSet.regions) {
        const box = _boxes.get(region.name);
        if (!box) continue;
        layer.addLayer(box.halo);
        layer.addLayer(box.rect);
        _drawn.push(region.name);
        _style(box);
    }
}

function _currentPool() {
    const all = window.getCoordinatesData?.() || [];
    const continent = document.getElementById('coordContinentFilter')?.value || 'all';
    if (continent === 'all' || !window.resolveRegionContinent) return all;
    return all.filter((r) => window.resolveRegionContinent(r) === continent);
}

/**
 * Recompute the viewport set, redraw the boxes, and re-render the sidebar list
 * when the set changed (or `force`), so panning does not rebuild an unchanged list.
 * @param {{force?: boolean}} [opts]
 */
function refreshRegionViewSet({ force = false } = {}) {
    const map = window.getMap?.();
    const view = map ? _viewBox(map) : null;
    _viewSet = selectViewportRegions(_currentPool(), view, window.appState?.selectedRegion?.name ?? null);
    _applyMapSet();
    const key = `${_viewSet.inViewCount}|${_viewSet.total}|${_viewSet.regions.map((r) => r.name).join('\u0001')}`;
    if (force || key !== _listKey) {
        _listKey = key;
        window.renderCoordinatesList?.();
    }
}

/** The set last computed by refreshRegionViewSet (computed now if there is none yet). */
function getViewportRegionSet() {
    if (!_viewSet) {
        const map = window.getMap?.();
        _viewSet = selectViewportRegions(_currentPool(), map ? _viewBox(map) : null,
            window.appState?.selectedRegion?.name ?? null);
    }
    return _viewSet;
}

/**
 * Highlight a region's box while its list row is hovered. A region outside the
 * view set is drawn for the duration of the hover and removed after.
 * @param {string} name
 * @param {boolean} on
 */
function highlightRegionBox(name, on) {
    const box = _boxes.get(name);
    const layer = window.getPreloadedLayer?.();
    if (!box || !layer) return;
    _hoveredName = on ? name : (_hoveredName === name ? null : _hoveredName);
    if (!_drawn.includes(name)) {
        if (on) { layer.addLayer(box.halo); layer.addLayer(box.rect); }
        else { layer.removeLayer(box.halo); layer.removeLayer(box.rect); }
    }
    _style(box);
}

function _hookMap(map) {
    if (_mapHooked || !map) return;
    map.on('zoomend moveend resize', () => {
        clearTimeout(_debounce);
        _debounce = setTimeout(() => refreshRegionViewSet(), VIEW_DEBOUNCE_MS);
    });
    window.events?.on?.(window.EV?.REGION_SELECTED, () => refreshRegionViewSet());
    _mapHooked = true;
}

/**
 * The hover "✏️ Edit" marker at a box's top-right corner, built on first hover:
 * it is invisible until then, and building one per region up front cost every
 * page load all of them to show at most one.
 */
function _editMarkerFor(bounds, index) {
    let marker = null;
    return function ensure() {
        if (marker) return marker;
        const icon = L.divIcon({
            html: '<div class="bbox-edit-icon">✏️ Edit</div>',
            className: 'bbox-edit-marker',
            iconSize: [56, 22],
            iconAnchor: [56, 0],   // top-right corner of the icon aligns with [north, east]
        });
        marker = L.marker(bounds.getNorthEast(), { icon, interactive: true, keyboard: false, zIndexOffset: 500 });
        marker.on('click', () => window.goToEdit(index));
        // Read by map-globe.js::initMap (_updateEditMarkerVisibility) to hide the
        // button when its bbox is smaller than ~40px on screen.
        marker._regionBounds = bounds;
        const iconEl = () => marker.getElement()?.querySelector('.bbox-edit-icon');
        marker.on('mouseover', () => iconEl()?.classList.add('visible'));
        marker.on('mouseout', () => iconEl()?.classList.remove('visible'));
        window.getEditMarkersLayer?.()?.addLayer(marker);
        return marker;
    };
}

function _buildBox(region, index) {
    const bounds = L.latLngBounds([region.south, region.west], [region.north, region.east]);
    const halo = L.rectangle(bounds, regionHaloStyle('normal'));
    const rect = L.rectangle(bounds, regionBoxStyle('normal'));
    const ensureEditMarker = _editMarkerFor(bounds, index);
    const box = { region, index, rect, halo };

    rect.on('click', () => window.selectCoordinate(index));
    rect.on('mouseover', (e) => {
        _hoveredName = region.name;
        _style(box);
        _markRow(region.name, true);
        // The label is the import batch tag ("coorlist" for the bulk CSV
        // import), not a name, so the tooltip shows the name.
        rect.unbindTooltip();
        rect.bindTooltip(region.name || region.label, { sticky: false, direction: 'top', offset: [0, -4] });
        rect.openTooltip(e.latlng);
        ensureEditMarker().getElement()?.querySelector('.bbox-edit-icon')?.classList.add('visible');
    });
    rect.on('mouseout', () => {
        if (_hoveredName === region.name) _hoveredName = null;
        _style(box);
        _markRow(region.name, false);
        // Delay hiding so the pointer can reach the edit button.
        setTimeout(() => {
            const icon = ensureEditMarker().getElement()?.querySelector('.bbox-edit-icon');
            if (icon && !icon.matches(':hover')) icon.classList.remove('visible');
        }, 300);
    });
    return box;
}

/**
 * Rebuild one box per saved region (drawn later only if in the view set), then
 * recompute the set and re-render the list. Renders the list even when there is
 * no map yet.
 * @param {Array<{name:string, north:number, south:number, east:number, west:number}>} regions
 */
function drawRegionBoxes(regions) {
    _boxes = new Map();
    _drawn = [];
    const layer = window.getPreloadedLayer?.();
    if (layer && typeof L !== 'undefined') {
        layer.clearLayers();
        window.getEditMarkersLayer?.()?.clearLayers();
        _hookMap(window.getMap?.());
        regions.forEach((region, index) => _boxes.set(region.name, _buildBox(region, index)));
    }
    refreshRegionViewSet({ force: true });
}

window.drawRegionBoxes = drawRegionBoxes;
window.refreshRegionViewSet = refreshRegionViewSet;
window.getViewportRegionSet = getViewportRegionSet;
window.highlightRegionBox = highlightRegionBox;

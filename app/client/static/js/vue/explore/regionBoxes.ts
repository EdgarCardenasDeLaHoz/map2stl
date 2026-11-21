/**
 * explore/regionBoxes.ts - saved-region boxes on the Explore (Leaflet) map
 * (F-UNIFY Phase 6; was modules/regions/region-boxes.js).
 *
 * - One outline per region in its own colour (region-geometry.js::regionColor) over a dark
 *   halo; only the selected one is tinted, a hovered one is thicker and shows its name.
 * - Drawn largest first (regionDrawOrder), so a small box sits on top of a large one and takes
 *   the click where they overlap. Hover and selection never reorder them.
 * - Only `store.mapRegions` is drawn (the viewport set: <= 20 that fit the view, plus the
 *   selected one). The map pushes its bounds into `store.view`; the sidebar list reads the same
 *   set, so the map and the list always show the same regions.
 * - Hover is shared through `store.hoverName`: a box hover marks its list row, a row hover
 *   highlights its box (drawn for the moment when it is not in the set).
 */
import { watch } from 'vue';
import {
    regionBoxStyle, regionColor, regionDrawOrder, regionHaloStyle,
} from '../../modules/regions/region-geometry.js';
import type { Region } from '../stores/types';
import type { RegionsStore } from '../stores/regions';

declare const L: any;

const VIEW_DEBOUNCE_MS = 150;

interface Box {
    sig: string;
    rect: any;
    halo: any;
    color: string;
    ensureMarker: () => any;
}

/** The map's visible bounds, or null while it has no size (hidden container, not laid out). */
export function viewBoxOf(map: any): { north: number; south: number; east: number; west: number } | null {
    const size = map?.getSize?.();
    if (!size || !size.x || !size.y) return null;
    try {
        const b = map.getBounds();
        return { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() };
    } catch (_) {
        return null;
    }
}

/**
 * Keep the map's boxes in step with the store. Returns the function that stops it and removes
 * the boxes.
 */
export function startRegionBoxes(map: any, store: RegionsStore): () => void {
    const boxes = new Map<string, Box>();
    let drawn: string[] = [];
    let extra: string | null = null;   // a hovered region drawn although it is outside the set
    let timer: ReturnType<typeof setTimeout> | null = null;

    const layer = () => (window as any).getPreloadedLayer?.();
    const markers = () => (window as any).getEditMarkersLayer?.();
    const sigOf = (r: Region) => `${r.north},${r.south},${r.east},${r.west}`;

    function stateFor(name: string): 'selected' | 'hover' | 'normal' {
        if (store.selectedName === name) return 'selected';
        if (store.hoverName === name) return 'hover';
        return 'normal';
    }

    function style(name: string) {
        const box = boxes.get(name);
        if (!box) return;
        const state = stateFor(name);
        box.rect.setStyle(regionBoxStyle(state, box.color));
        box.halo.setStyle(regionHaloStyle(state));
    }

    /** The hover "Edit" marker at a box's top-right corner, built on first hover. */
    function markerFor(region: Region, bounds: any) {
        let marker: any = null;
        return () => {
            if (marker) return marker;
            const icon = L.divIcon({
                html: '<div class="bbox-edit-icon">✏️ Edit</div>',
                className: 'bbox-edit-marker',
                iconSize: [56, 22],
                iconAnchor: [56, 0],   // the icon's top-right corner sits on [north, east]
            });
            marker = L.marker(bounds.getNorthEast(), { icon, interactive: true, keyboard: false, zIndexOffset: 500 });
            marker.on('click', () => void store.goToEdit(region.name));
            // Read by leafletMap.ts::_updateEditMarkerVisibility to hide it on a tiny box.
            marker._regionBounds = bounds;
            const iconEl = () => marker.getElement()?.querySelector('.bbox-edit-icon');
            marker.on('mouseover', () => iconEl()?.classList.add('visible'));
            marker.on('mouseout', () => iconEl()?.classList.remove('visible'));
            markers()?.addLayer(marker);
            return marker;
        };
    }

    function build(region: Region): Box {
        const bounds = L.latLngBounds([region.south, region.west], [region.north, region.east]);
        const halo = L.rectangle(bounds, regionHaloStyle('normal'));
        const color = regionColor(region.name);
        const rect = L.rectangle(bounds, regionBoxStyle('normal', color));
        const ensureMarker = markerFor(region, bounds);
        const name = region.name;

        rect.on('click', () => void store.select(name));
        rect.on('mouseover', (e: any) => {
            store.hoverName = name;
            // The label is the import batch tag, not a name: the tooltip shows the name.
            rect.unbindTooltip();
            rect.bindTooltip(region.name || region.label, { sticky: false, direction: 'top', offset: [0, -4] });
            rect.openTooltip(e.latlng);
            ensureMarker().getElement()?.querySelector('.bbox-edit-icon')?.classList.add('visible');
        });
        rect.on('mouseout', () => {
            if (store.hoverName === name) store.hoverName = null;
            // Delay hiding so the pointer can reach the edit button.
            setTimeout(() => {
                const icon = ensureMarker().getElement()?.querySelector('.bbox-edit-icon');
                if (icon && !icon.matches(':hover')) icon.classList.remove('visible');
            }, 300);
        });
        return { sig: sigOf(region), rect, halo, color, ensureMarker };
    }

    function boxFor(region: Region): Box {
        let box = boxes.get(region.name);
        if (!box || box.sig !== sigOf(region)) {
            if (box) { layer()?.removeLayer(box.halo); layer()?.removeLayer(box.rect); }
            box = build(region);
            boxes.set(region.name, box);
        }
        return box;
    }

    /** Put exactly the viewport set on the map, largest first so small boxes sit on top. */
    function applySet() {
        const group = layer();
        if (!group) return;
        group.clearLayers();
        drawn = [];
        extra = null;
        for (const region of regionDrawOrder(store.mapRegions as any[]) as Region[]) {
            const box = boxFor(region);
            group.addLayer(box.halo);
            group.addLayer(box.rect);
            drawn.push(region.name);
            style(region.name);
        }
        applyHover();
    }

    /** A hovered list row whose region is outside the set: draw it while hovered. */
    function applyHover() {
        const group = layer();
        if (!group) return;
        if (extra && extra !== store.hoverName) {
            const old = boxes.get(extra);
            if (old) { group.removeLayer(old.halo); group.removeLayer(old.rect); }
            extra = null;
        }
        const name = store.hoverName;
        if (name && !drawn.includes(name) && store.boxesVisible) {
            const region = store.regions.find((r) => r.name === name);
            if (region) {
                const box = boxFor(region);
                group.addLayer(box.halo);
                group.addLayer(box.rect);
                extra = name;
            }
        }
        for (const n of [...drawn, ...(extra ? [extra] : [])]) style(n);
    }

    function reset() {
        boxes.clear();
        layer()?.clearLayers();
        markers()?.clearLayers();
        drawn = [];
        extra = null;
    }

    function pushView() {
        store.view = viewBoxOf(map);
    }
    function onMove() {
        if (timer) clearTimeout(timer);
        timer = setTimeout(pushView, VIEW_DEBOUNCE_MS);
    }
    map.on('zoomend moveend resize', onMove);
    pushView();

    const stops = [
        watch(() => store.drawVersion, () => { reset(); applySet(); }),
        watch(() => store.mapRegions, applySet),
        watch(() => [store.hoverName, store.selectedName], applyHover),
    ];
    applySet();

    return () => {
        map.off('zoomend moveend resize', onMove);
        if (timer) clearTimeout(timer);
        stops.forEach((s) => s());
        reset();
    };
}

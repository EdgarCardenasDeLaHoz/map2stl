/**
 * explore/mapLayers.ts - the Explore map's layer choices (style, terrain relief, grid, labels),
 * as one reactive object.
 *
 * leafletMap.ts writes it whenever a layer changes, from any caller (the Map menu, Edit's
 * hidden mirrors, a link), and MapMenu.vue reads it, so the menu can never disagree with the
 * map (F-UNIFY Phase 6, M6-M9). Pure data: no Leaflet, no DOM.
 */
import { reactive } from 'vue';

export interface TileStyle { key: string; label: string; }

/** The seven base maps, in the order the Map menu lists them. */
export const TILE_STYLES: TileStyle[] = [
    { key: 'osm-topo', label: 'OpenTopoMap' },
    { key: 'osm', label: 'OpenStreetMap' },
    { key: 'esri-world', label: 'Satellite (Esri)' },
    { key: 'esri-topo', label: 'Esri Topo' },
    { key: 'carto-light', label: 'CartoDB light' },
    { key: 'carto-dark', label: 'CartoDB dark' },
    { key: 'stamen-terrain', label: 'Terrain (Stamen)' },
];

export const DEFAULT_STYLE = 'osm-topo';
export const STYLE_KEY = 'map2stl_mapStyle';

export function isTileStyle(key: unknown): key is string {
    return typeof key === 'string' && TILE_STYLES.some((s) => s.key === key);
}

export const mapLayers = reactive({
    style: DEFAULT_STYLE,
    terrain: false,
    /** 0-100 */
    terrainOpacity: 70,
    grid: true,
    labels: false,
});

export type MapLayers = typeof mapLayers;

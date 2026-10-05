/**
 * What the Edit panel shows for each layer (F-EDITPANEL, mockup
 * claude/mockups/2026-10-04-edit-panel/panel.png): FETCH, VIEW and COMPOSITE groups, then the
 * CANVAS group under every layer.
 *
 * One row per setting. A row writes the existing control by id (dom-fields.ts), so the modules,
 * autosave and presets see an ordinary edit; the old section components stay mounted (hidden)
 * as the holders of those ids, and the richer ones open as sub-pages (`sub`). Where the old
 * panel had the same setting twice (layer card + Fetch / Display / Composite sections), the row
 * here is the only visible control left.
 */
import { checked, num, setChecked, setField, val } from '../../../dom-fields';

export type RowKind = 'switch' | 'slider' | 'seg' | 'number' | 'select' | 'sub' | 'button' | 'res' | 'color';

export interface Opt { value: string; label: string; disabled?: boolean }

export interface Row {
  kind: RowKind;
  label: string;
  icon: string;
  color: string;
  /** Control id this row reads and writes. */
  id?: string;
  /** One plain line under the name; a function re-reads the page. */
  hint?: string | (() => string);
  min?: number; max?: number; step?: number; unit?: string;
  /** Value shown on the right of a slider. */
  fmt?: (v: number) => string;
  /** seg / select options; omitted for a select = the control's own options. */
  options?: Opt[];
  /** Hidden until "Show advanced" in its group. */
  adv?: boolean;
  /** Sub-page id (CollapsibleSection `sub`) for kind 'sub'. */
  sub?: string;
  /** Button id to click (kind 'button'), or after a change (e.g. a reload). */
  click?: string;
  /** Custom write instead of setField / setChecked. */
  set?: (v: string | boolean) => void;
  /** Custom read. */
  get?: () => string | boolean;
  /** Shown only when this returns true. */
  show?: () => boolean;
}

export interface LayerGroups {
  fetch: Row[];
  /** The group's main action, under the FETCH rows. */
  reload?: { label: string; click: string; primary?: boolean };
  /** Small links under the reload button. */
  links?: { label: string; click: string }[];
  view: Row[];
  composite: Row[];
  /** One plain line under the COMPOSITE group. */
  note?: string;
}

const w = () => window as any;
const click = (id: string) => (document.getElementById(id) as HTMLElement | null)?.click();
const f1 = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

/** The layer's "On the map" opacity (also the layer-order sub-page's slider). */
function opacityRow(stack: string): Row {
  return {
    kind: 'slider', label: 'On the map', icon: '◐', color: '#636366', id: `layerOpacity_${stack}`,
    min: 0, max: 100, step: 5, fmt: (v) => `${v}% opaque`,
    set: (v) => { setField(`layerOpacity_${stack}`, String(v)); w().setLayerOpacity?.(stack, Number(v) / 100); },
  };
}

/** Per-layer resolution: follows the terrain's Detail unless "Own resolution" is ticked. */
function resRow(id: string, what: string): Row {
  return { kind: 'res', label: 'Resolution', icon: '▦', color: '#636366', id, hint: `Grid of the ${what}` };
}

/** The ids that follow #paramDim when not overridden (resolution model, user 2026-10-04). */
export const RES_IDS = ['waterResolution', 'esaResolution', 'satImgResolution', 'cityRasterDim', 'trailsDim'];

const COLORMAPS: Opt[] = [
  { value: 'terrain', label: 'Terrain' }, { value: 'viridis', label: 'Viridis' },
  { value: 'rainbow', label: 'Rainbow' }, { value: 'gray', label: 'Gray' },
];

export const GROUPS: Record<string, LayerGroups> = {
  terrain: {
    fetch: [
      { kind: 'seg', label: 'Style', icon: '✨', color: '#5e5ce6', hint: 'Sets sources, layers and size in one tap',
        options: [{ value: 'city', label: 'City' }, { value: 'mountain', label: 'Mountain' },
                  { value: 'region', label: 'Region' }, { value: 'coast', label: 'Coast' }],
        get: () => '', set: (v) => w().applyWorkflowPreset?.(String(v)) },
      { kind: 'select', label: 'Elevation source', icon: '⛰', color: '#8e6e4e', id: 'paramDemSource',
        hint: 'Where the heights come from' },
      { kind: 'number', label: 'Detail', icon: '▦', color: '#30a46c', id: 'paramDim', unit: 'px',
        min: 50, max: 2000, step: 50, hint: 'Points across; layers follow it' },
      { kind: 'select', label: 'Projection', icon: '🌐', color: '#0a84ff', id: 'paramProjection',
        hint: 'For a large area' },
      { kind: 'switch', label: 'Trim empty edges', icon: '✂', color: '#636366', id: 'paramClipNans', adv: true,
        hint: 'Drop the blank border a projection leaves' },
      { kind: 'switch', label: 'Keep the grid size', icon: '⤢', color: '#636366', id: 'paramMaintainDimensions', adv: true,
        hint: 'Stretch back to the full size after projecting' },
      { kind: 'switch', label: 'Reload when the box moves', icon: '↻', color: '#636366', id: 'autoReloadLayers',
        hint: 'Fetch every layer again after a box edit' },
      { kind: 'sub', label: 'Landmarks', icon: '🏛', color: '#ff9f0a', sub: 'landmarks',
        hint: 'Notable buildings in the box, their heights' },
      { kind: 'sub', label: 'Saved presets', icon: '💾', color: '#636366', sub: 'presets', hint: 'Load, save or delete a setup' },
    ],
    reload: { label: '↺ Reload terrain', click: 'loadDemBtn', primary: true },
    links: [{ label: 'Clear cached downloads', click: 'clearRegionCacheBtn' }, { label: 'Settings as JSON', click: 'jsonViewToggleBtn' }],
    view: [
      { kind: 'seg', label: 'Colours', icon: '🎨', color: '#30a46c', id: 'demColormap', options: COLORMAPS },
      { kind: 'switch', label: 'Fit colours to the heights', icon: '↕', color: '#8e6e4e', id: 'autoRescale',
        hint: 'Off: set the range yourself' },
      { kind: 'number', label: 'Lowest colour at', icon: '↧', color: '#8e6e4e', id: 'rescaleMin', unit: 'm', adv: true,
        click: 'applyRescaleBtn' },
      { kind: 'number', label: 'Highest colour at', icon: '↥', color: '#8e6e4e', id: 'rescaleMax', unit: 'm', adv: true,
        click: 'applyRescaleBtn' },
      { kind: 'sub', label: 'Height curve', icon: '📈', color: '#bf5af2', sub: 'curve', hint: 'Reshape heights · histogram · sea level' },
      { kind: 'select', label: 'Composite colours', icon: '🎨', color: '#636366', id: 'compositeColormap', adv: true,
        hint: 'Colours of the combined height map' },
    ],
    composite: [
      { kind: 'number', label: 'Height', icon: '⛰', color: '#8e6e4e', id: 'paramDepthScale', unit: '×',
        min: 0, max: 10, step: 0.1, hint: 'Vertical scale of the ground' },
      { kind: 'switch', label: 'Lower the sea and lakes', icon: '🌊', color: '#0a84ff', id: 'paramSubtractWater',
        hint: 'Water sits below the shore' },
      { kind: 'number', label: 'Water lowered by', icon: '↧', color: '#0a84ff', id: 'paramWaterScale', unit: '× height',
        min: 0, max: 1, step: 0.01, adv: true },
      { kind: 'switch', label: 'Combine layers', icon: '⊕', color: '#5e5ce6', id: 'compositeEnabled', adv: true,
        hint: 'Carve rivers, raise land cover into the terrain' },
      { kind: 'switch', label: 'Include the terrain', icon: '⛰', color: '#8e6e4e', id: 'compositeDemEnabled', adv: true },
      { kind: 'slider', label: 'Terrain weight', icon: '⚖', color: '#8e6e4e', id: 'compositeDemWeight', adv: true,
        min: 0, max: 2, step: 0.1, fmt: (v) => `× ${f1(v)}` },
      { kind: 'button', label: 'Preview the combined map', icon: '👁', color: '#636366', click: 'previewCompositeBtn', adv: true },
      { kind: 'button', label: 'Apply to the terrain', icon: '✓', color: '#0a84ff', click: 'applyCompositeToDemBtn', adv: true,
        hint: 'Replace the terrain with the combined heights' },
    ],
  },

  water: {
    fetch: [
      { kind: 'seg', label: 'Rivers', icon: '〰', color: '#0a84ff', id: 'hydroMinOrder',
        hint: () => `${val('hydroSource') === 'natural_earth' ? 'Natural Earth' : 'HydroRIVERS'} · size ${val('hydroMinOrder')} and up`,
        options: [{ value: '5', label: 'Big only' }, { value: '3', label: 'Most' }, { value: '1', label: 'All' }],
        click: 'loadWaterHydrologyBtn' },
      { kind: 'select', label: 'River data', icon: '⛁', color: '#636366', id: 'hydroSource',
        options: [{ value: 'hydrorivers', label: 'HydroRIVERS (detailed)' }, { value: 'natural_earth', label: 'Natural Earth (coarse)' }] },
      { kind: 'select', label: 'Water areas from', icon: '💧', color: '#30a46c', id: 'waterDataset',
        options: [{ value: 'esa', label: 'ESA land cover' }, { value: 'jrc', label: 'JRC surface water' }] },
      resRow('waterResolution', 'water areas'),
    ],
    reload: { label: '↺ Reload rivers & lakes', click: 'loadWaterHydrologyBtn' },
    links: [{ label: 'Clear rivers & lakes', click: 'clearWaterHydrologyBtn' }],
    view: [
      opacityRow('WaterHydrology'),
      { kind: 'seg', label: 'Colour rivers by', icon: '🎨', color: '#bf5af2', id: 'hydroColorMode',
        options: [{ value: 'depth', label: 'Depth' }, { value: 'order', label: 'Size' }] },
    ],
    composite: [
      { kind: 'slider', label: 'River depth', icon: '↧', color: '#0a84ff', id: 'compositeRiverDepthMm',
        min: 0.1, max: 2, step: 0.1, fmt: (v) => `${f1(v)} mm`, hint: 'The biggest river; the rest in proportion' },
      { kind: 'slider', label: 'River width', icon: '↔', color: '#0a84ff', id: 'hydroWidthFactor',
        min: 0.5, max: 10, step: 0.5, fmt: (v) => `× ${f1(v)}`, hint: "× the width from the river's flow",
        // The map's rivers are the print's carve: reload them too (the composite recomputes on
        // its own; the server builds the shared carve once, perf audit 2026-10-04).
        click: 'loadWaterHydrologyBtn' },
      { kind: 'switch', label: 'Rivers', icon: '〰', color: '#0a84ff', id: 'compositeRiversEnabled', adv: true },
      { kind: 'slider', label: 'Depth from flow', icon: '≋', color: '#0a84ff', id: 'compositeRiverDepthScale', adv: true,
        min: 0, max: 20, step: 0.5, fmt: (v) => `× ${f1(v)}`, hint: 'How much deeper big rivers are than small' },
      { kind: 'switch', label: 'Lakes', icon: '◍', color: '#30a46c', id: 'compositeLakesEnabled',
        hint: () => `Larger than ${f1(num('compositeLakeMinAreaHa', 1))} ha`,
        set: (v) => { if (v) setChecked('compositeEnabled', true); setChecked('compositeLakesEnabled', !!v); } },
      { kind: 'slider', label: 'Lake depth', icon: '↧', color: '#30a46c', id: 'compositeLakeDepth',
        min: 0.5, max: 50, step: 0.5, fmt: (v) => `${f1(v)} m`, hint: 'Below the shore', show: () => checked('compositeLakesEnabled') },
      { kind: 'slider', label: 'Smallest lake', icon: '▢', color: '#30a46c', id: 'compositeLakeMinAreaHa', adv: true,
        min: 0.5, max: 100, step: 0.5, fmt: (v) => `${f1(v)} ha` },
      { kind: 'switch', label: 'Sea and lakes from ESA', icon: '≈', color: '#0a84ff', id: 'compositeWaterEnabled', adv: true,
        hint: 'Open water from the land-cover map' },
      { kind: 'slider', label: 'ESA water depth', icon: '↧', color: '#0a84ff', id: 'compositeWaterDepth', adv: true,
        min: 0, max: 50, step: 0.5, fmt: (v) => `${f1(v)} m` },
      { kind: 'slider', label: 'ESA water weight', icon: '⚖', color: '#0a84ff', id: 'compositeWaterWeight', adv: true,
        min: 0, max: 5, step: 0.1, fmt: (v) => `× ${f1(v)}` },
    ],
    note: 'The map shows the rivers exactly as they are carved.',
  },

  city: {
    fetch: [
      resRow('cityRasterDim', 'buildings height map'),
      { kind: 'number', label: 'Outline tolerance', icon: '◇', color: '#636366', id: 'citySimplifyTolerance', unit: 'm',
        min: 0, max: 50, step: 0.5, hint: 'How closely outlines follow OSM' },
      { kind: 'number', label: 'Smallest building', icon: '▢', color: '#636366', id: 'cityMinArea', unit: 'm²',
        min: 0, max: 5000, step: 5 },
      { kind: 'number', label: 'Floor height', icon: '☰', color: '#636366', id: 'cityMPerLevel', unit: 'm', adv: true,
        min: 2, max: 6, step: 0.05, hint: 'When OSM has floors, no height' },
      { kind: 'button', label: 'Better heights', icon: '🏢', color: '#ff9f0a', click: 'enhanceHeightsBtn',
        hint: 'Google 3D tiles where OSM has none',
        show: () => (document.getElementById('enhanceHeightsSection')?.style.display ?? 'none') !== 'none' },
      { kind: 'button', label: 'Building heights table', icon: '☰', color: '#ff9f0a', click: 'openCityTablePanelBtn',
        hint: "Set any building's height" },
    ],
    reload: { label: '↺ Reload buildings & roads', click: 'loadCityDataBtn' },
    links: [{ label: 'Clear buildings & roads', click: 'clearCityDataBtn' }],
    view: [
      opacityRow('CityOverlay'),
      { kind: 'select', label: 'Colour buildings by', icon: '🎨', color: '#bf5af2', id: 'cityColormap', hint: 'Height colours' },
      { kind: 'switch', label: 'Show buildings', icon: '🏠', color: '#ff9f0a', id: 'cityLayerBuildings', adv: true },
      { kind: 'switch', label: 'Show roads', icon: '🛣', color: '#636366', id: 'cityLayerRoads', adv: true },
      { kind: 'switch', label: 'Show water', icon: '〰', color: '#0a84ff', id: 'cityLayerWaterways', adv: true },
      { kind: 'color', label: 'Building colour', icon: '■', color: '#ff9f0a', id: 'layerBuildingsColor', adv: true },
      { kind: 'color', label: 'Road colour', icon: '■', color: '#636366', id: 'layerRoadsColor', adv: true },
      { kind: 'color', label: 'Water colour', icon: '■', color: '#0a84ff', id: 'layerWaterwaysColor', adv: true },
    ],
    composite: [
      { kind: 'switch', label: 'Buildings', icon: '🏠', color: '#ff9f0a', id: 'cityLayer_buildings_enabled' },
      { kind: 'slider', label: 'Building height', icon: '↥', color: '#ff9f0a', id: 'cityLayer_buildings_value',
        min: 0.5, max: 3, step: 0.1, hint: 'One scale for the map preview and the print',
        fmt: (v) => (Math.abs(v - 1) < 0.05 ? 'true height' : `× ${v.toFixed(1)}`) },
      { kind: 'switch', label: 'Roads', icon: '🛣', color: '#636366', id: 'cityLayer_roads_enabled' },
      { kind: 'seg', label: 'Roads are', icon: '⇵', color: '#636366', id: 'cityLayer_roads_mode',
        options: [{ value: 'raised', label: 'Raised' }, { value: 'engraved', label: 'Engraved' }] },
      { kind: 'switch', label: 'Water', icon: '〰', color: '#0a84ff', id: 'cityLayer_waterways_enabled' },
      { kind: 'switch', label: 'Rail', icon: '🚆', color: '#8e8e93', id: 'cityLayer_railways_enabled' },
      { kind: 'switch', label: 'Parks', icon: '🌳', color: '#30a46c', id: 'cityLayer_green_enabled' },
      { kind: 'switch', label: 'Walls', icon: '🧱', color: '#8e8e93', id: 'cityLayer_walls_enabled', adv: true },
      { kind: 'switch', label: 'Sloped roofs', icon: '⌂', color: '#ff9f0a', id: 'cityRoofShapes', adv: true,
        hint: 'Roof shapes from OpenStreetMap' },
      { kind: 'number', label: 'Road cut on the map', icon: '⇣', color: '#636366', id: 'compositeRoadCut', unit: 'm', adv: true,
        min: 0, max: 5, step: 0.1, hint: 'In the combined height map' },
      { kind: 'number', label: 'Water cut on the map', icon: '⇣', color: '#0a84ff', id: 'compositeRiverDepth', unit: 'm', adv: true,
        min: 0, max: 20, step: 0.5 },
      { kind: 'number', label: 'Wall height on the map', icon: '↥', color: '#8e8e93', id: 'compositeWallScale', unit: '×', adv: true,
        min: 0, max: 5, step: 0.1 },
      { kind: 'number', label: '3D city: building scale', icon: '↥', color: '#ff9f0a', id: 'cityBuildingScale', unit: 'mm/m', adv: true,
        min: 0, max: 10, step: 0.1, hint: 'Edit-view 3D city only' },
      { kind: 'number', label: '3D city: road depth', icon: '⇣', color: '#636366', id: 'cityRoadDepression', unit: 'm', adv: true,
        min: -10, max: 2, step: 0.5 },
      { kind: 'number', label: '3D city: water offset', icon: '⇣', color: '#0a84ff', id: 'cityWaterOffset', unit: 'm', adv: true,
        min: -20, max: 0, step: 0.5 },
    ],
    note: 'Heights come from OpenStreetMap; set any building in the table.',
  },

  satellite: {
    fetch: [resRow('satImgResolution', 'satellite image')],
    reload: { label: '↺ Reload satellite image', click: 'loadSatImgBtn' },
    links: [{ label: 'Clear satellite image', click: 'clearSatImgBtn' }],
    view: [opacityRow('SatImg')],
    composite: [
      { kind: 'switch', label: 'Colour the 3D model', icon: '🛰', color: '#30a46c', id: 'viewerColormap',
        hint: 'Drapes the image over the preview and the 3MF',
        get: () => val('viewerColormap') === 'satellite', set: (v) => setField('viewerColormap', v ? 'satellite' : 'terrain') },
      { kind: 'switch', label: 'Trees from the image', icon: '🌳', color: '#30a46c', id: 'compositeSatEnabled', adv: true },
      { kind: 'slider', label: 'Tree height', icon: '↥', color: '#30a46c', id: 'compositeVegHeight', adv: true,
        min: 0, max: 30, step: 0.5, fmt: (v) => `${f1(v)} m` },
      { kind: 'slider', label: 'Tree weight', icon: '⚖', color: '#30a46c', id: 'compositeSatWeight', adv: true,
        min: 0, max: 5, step: 0.1, fmt: (v) => `× ${f1(v)}` },
    ],
  },

  trails: {
    fetch: [
      { kind: 'select', label: 'Trail data', icon: '⛁', color: '#636366', id: 'trailsSource',
        options: [{ value: 'all', label: 'All sources' }, { value: 'osm', label: 'OpenStreetMap' }, { value: 'usfs', label: 'US Forest Service' }] },
      resRow('trailsDim', 'trails'),
      { kind: 'number', label: 'Trail width', icon: '↔', color: '#636366', id: 'trailsWidthM', unit: 'm',
        min: 1, max: 500, step: 1, hint: 'At least two pixels wide' },
    ],
    reload: { label: '↺ Reload trails', click: 'loadTrailsBtn' },
    links: [{ label: 'Clear trails', click: 'clearTrailsBtn' }],
    view: [
      opacityRow('Trails'),
      { kind: 'switch', label: 'Ski pistes', icon: '⛷', color: '#0a84ff', id: 'trailsShowSki' },
      { kind: 'switch', label: 'Hiking paths', icon: '🥾', color: '#ff9f0a', id: 'trailsShowHiking' },
      { kind: 'switch', label: 'Ski areas', icon: '▧', color: '#0a84ff', id: 'trailsShowAreas', adv: true },
      { kind: 'switch', label: 'Colour pistes by difficulty', icon: '🎨', color: '#bf5af2', id: 'trailsColorByDifficulty', adv: true },
      { kind: 'color', label: 'Piste colour', icon: '■', color: '#0a84ff', id: 'trailsSkiColor', adv: true },
      { kind: 'color', label: 'Path colour', icon: '■', color: '#ff9f0a', id: 'trailsHikingColor', adv: true },
    ],
    composite: [
      { kind: 'slider', label: 'Raised by', icon: '↥', color: '#ff9f0a', id: 'cityLayer_trails_value',
        min: 0.1, max: 2, step: 0.1, fmt: (v) => `${f1(v)} mm` },
      { kind: 'number', label: 'Relief on the map', icon: '⇵', color: '#636366', id: 'trailsReliefM', unit: 'm', adv: true,
        min: -100, max: 100, step: 0.5, hint: 'Negative engraves' },
      { kind: 'switch', label: 'Combine trails', icon: '⊕', color: '#5e5ce6', id: 'compositeTrailsEnabled', adv: true },
      { kind: 'switch', label: 'Pistes in the combined map', icon: '⛷', color: '#0a84ff', id: 'compositeTrailsSkiEnabled', adv: true },
      { kind: 'switch', label: 'Paths in the combined map', icon: '🥾', color: '#ff9f0a', id: 'compositeTrailsHikingEnabled', adv: true },
      { kind: 'slider', label: 'Trails weight', icon: '⚖', color: '#636366', id: 'compositeTrailsWeight', adv: true,
        min: 0, max: 5, step: 0.1, fmt: (v) => `× ${f1(v)}` },
    ],
  },

  landcover: {
    fetch: [resRow('esaResolution', 'land cover')],
    reload: { label: '↺ Reload land cover', click: 'loadEsaBtn' },
    view: [
      opacityRow('Sat'),
      { kind: 'sub', label: 'Land-use colours', icon: '🎨', color: '#30a46c', sub: 'landuse', hint: 'Colour per class' },
    ],
    composite: [
      { kind: 'switch', label: 'Raise trees and built-up', icon: '🌳', color: '#30a46c', id: 'compositeLandcoverEnabled' },
      { kind: 'slider', label: 'Tree height', icon: '↥', color: '#30a46c', id: 'compositeTreeHeight',
        min: 0, max: 40, step: 0.5, fmt: (v) => `${f1(v)} m` },
      { kind: 'slider', label: 'Land cover weight', icon: '⚖', color: '#30a46c', id: 'compositeLandcoverWeight',
        min: 0, max: 5, step: 0.1, fmt: (v) => `× ${f1(v)}` },
    ],
  },

  borders: {
    fetch: [],
    reload: { label: '↺ Reload borders', click: 'reloadBordersBtn' },
    view: [
      opacityRow('Borders'),
      { kind: 'switch', label: 'Countries', icon: '🏳', color: '#ff453a', id: 'bordersShowCountries',
        hint: 'Borders between countries' },
      { kind: 'switch', label: 'States and provinces', icon: '▦', color: '#ffd60a', id: 'bordersShowStates',
        hint: 'First-level divisions' },
      { kind: 'color', label: 'Country colour', icon: '■', color: '#ff453a', id: 'bordersCountryColor' },
      { kind: 'color', label: 'State colour', icon: '■', color: '#ffd60a', id: 'bordersStateColor' },
    ],
    composite: [],
    note: 'View only: borders are drawn on the map, not printed. Natural Earth 1:10m.',
  },

  mesh: {
    fetch: [{ kind: 'sub', label: 'Import a mesh', icon: '📐', color: '#5e5ce6', sub: 'mesh', hint: 'STL or OBJ: upload, library, convert' }],
    view: [opacityRow('MeshImport')],
    composite: [{ kind: 'sub', label: 'Plate registration', icon: '🧭', color: '#ff9f0a', sub: 'plate', hint: 'Place a printed plate on the map' }],
  },
};

/** The map view itself: the same group under every layer (user 2026-10-04). */
export const CANVAS: Row[] = [
  { kind: 'switch', label: 'Grid', icon: '#', color: '#636366', id: 'showGridlines', hint: 'Lat/lon lines' },
  { kind: 'sub', label: 'Grid & map options', icon: '🗺', color: '#0a84ff', sub: 'grid', hint: 'Spacing, pixel grid, map under the terrain' },
  { kind: 'sub', label: 'Layer order', icon: '☰', color: '#5e5ce6', sub: 'layers', hint: "What's drawn on top, on the map only" },
  { kind: 'button', label: 'Compare with satellite', icon: '◫', color: '#636366', click: 'splitViewToggleBtn', hint: 'Split view on the map' },
];

/** Sub-page titles. */
export const SUB_TITLES: Record<string, string> = {
  landmarks: 'Landmarks', presets: 'Saved presets', curve: 'Height curve', landuse: 'Land-use colours',
  mesh: 'Import a mesh', plate: 'Plate registration', grid: 'Grid & map options', layers: 'Layer order',
};

/** Every control id the rows write: the inventory test checks each exists once in the page. */
export function rowIds(): string[] {
  const all = [...Object.values(GROUPS).flatMap((g) => [...g.fetch, ...g.view, ...g.composite]), ...CANVAS];
  return [...new Set(all.map((r) => r.id || r.click).filter(Boolean) as string[])];
}

export { click };

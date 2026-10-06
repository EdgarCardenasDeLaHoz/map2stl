/**
 * The Edit page's layers (F-DESIGN, mockup claude/mockups/2026-10-01/edit.html).
 *
 * A layer here is what the user thinks of as one thing in the print: "Rivers & lakes" is
 * the hydrology preview *and* the composite carve, "Buildings & roads" the city overlay
 * *and* the City model layers. Its switch sets what prints (the model flags) and shows the
 * matching preview layer, so the picture and the model agree (guidelines §1.6). The panels
 * read and write the existing controls by id (dom-fields.ts); stacked-layers.js stays the
 * owner of what the canvas shows.
 */
import { defineStore } from 'pinia';
import { checked, num, setChecked, setField, val } from '../dom-fields';

export type EditLayerId = 'canvas' | 'terrain' | 'water' | 'city' | 'satellite' | 'trails' | 'landcover' | 'mesh' | 'borders';

export interface EditLayer {
  id: EditLayerId;
  name: string;
  icon: string;
  /** stacked-layers.js key of the preview layer ('' for Canvas, which has none). */
  stack: string;
  /** Always in the model (no switch). */
  fixed?: boolean;
  /** Only with this ⚙ tool (or Everything). */
  tool?: string;
  /** Whether it is in the model now. */
  inModel: () => boolean;
  /** Put it in the model or take it out (writes the existing controls). */
  setInModel: (on: boolean) => void;
  /** Canvases that hold a picture of it, best first (thumbnails). */
  pictures: () => (HTMLCanvasElement | null | undefined)[];
}

const w = () => window as any;

const WATER_IDS = ['compositeRiversEnabled', 'compositeLakesEnabled', 'compositeWaterEnabled'];
/** Other channels that carve, as [switch, weight]: they keep the composite on while they contribute. */
const OTHER_CARVED: [string, string][] = [
  ['compositeLandcoverEnabled', 'compositeLandcoverWeight'], ['compositeSatEnabled', 'compositeSatWeight'],
];
const CITY_IDS = ['buildings', 'fortifications', 'walls', 'towers', 'churches', 'roads', 'railways', 'green', 'waterways'];

/** In the panel's order (user 2026-10-05), all listed from the start. */
export const EDIT_LAYERS: EditLayer[] = [
  {
    // Not a layer of the print but the whole of it: projection, detail, the map view and
    // the combined height map (user 2026-10-05: Canvas as its own row, first).
    id: 'canvas', name: 'Canvas', icon: '▦', stack: '', fixed: true,
    inModel: () => true,
    setInModel: () => {},
    pictures: () => [],
  },
  {
    id: 'terrain', name: 'Terrain', icon: '⛰', stack: 'Dem', fixed: true,
    inModel: () => true,
    setInModel: () => {},
    pictures: () => [
      document.querySelector(`#demImage ${w().DEM_CANVAS_SELECTOR || 'canvas'}`) as HTMLCanvasElement | null,
      document.getElementById('layerDemCanvas') as HTMLCanvasElement | null,
    ],
  },
  {
    id: 'landcover', name: 'Land cover', icon: '🌿', stack: 'Sat',
    // Preview only: land cover has no server-side channel yet (composite-spec.js).
    inModel: () => !!(w().getActiveLayers?.() as Set<string> | undefined)?.has('Sat'),
    setInModel: () => {},
    pictures: () => [document.querySelector('#satelliteImage canvas') as HTMLCanvasElement | null],
  },
  {
    id: 'satellite', name: 'Satellite', icon: '🛰', stack: 'SatImg',
    inModel: () => val('viewerColormap') === 'satellite',
    setInModel: (on) => setField('viewerColormap', on ? 'satellite' : 'terrain'),
    pictures: () => [w().appState?.satImgSourceCanvas],
  },
  {
    id: 'water', name: 'Rivers & lakes', icon: '💧', stack: 'WaterHydrology',
    // Rivers, lakes and open water (ESA) are one thing to the user: carved water.
    inModel: () => checked('compositeEnabled')
      && (checked('compositeRiversEnabled') || checked('compositeLakesEnabled') || checked('compositeWaterEnabled')),
    setInModel: (on) => {
      if (on) setChecked('compositeEnabled', true);
      for (const id of WATER_IDS) setChecked(id, on);
      // Off: the composite goes too unless a channel that still needs it is on
      // (land cover, vegetation), so nothing carved is left behind unseen.
      if (!on && !OTHER_CARVED.some(([id, weight]) => checked(id) && num(weight, 0) > 0)) setChecked('compositeEnabled', false);
    },
    pictures: () => [w().appState?.waterHydrologyCanvas],
  },
  {
    id: 'borders', name: 'Borders', icon: '🗺', stack: 'Borders',
    // View only (user 2026-10-05): country and state lines on the map, never in the print.
    inModel: () => !!(w().getActiveLayers?.() as Set<string> | undefined)?.has('Borders'),
    setInModel: () => {},
    pictures: () => [w().appState?.bordersSourceCanvas],
  },
  {
    id: 'trails', name: 'Trails', icon: '🥾', stack: 'Trails',
    inModel: () => checked('cityLayer_trails_enabled'),
    setInModel: (on) => setChecked('cityLayer_trails_enabled', on),
    pictures: () => [document.getElementById('layerTrailsCanvas') as HTMLCanvasElement | null],
  },
  {
    id: 'city', name: 'Buildings & roads', icon: '🏙', stack: 'CityOverlay',
    inModel: () => checked('cityLayer_buildings_enabled') || checked('cityLayer_roads_enabled'),
    setInModel: (on) => { for (const id of CITY_IDS) setChecked(`cityLayer_${id}_enabled`, on); },
    pictures: () => [w().appState?.cityRasterSourceCanvas, document.getElementById('layerCityRasterCanvas') as HTMLCanvasElement | null],
  },
  {
    id: 'mesh', name: 'Import layer', icon: '📐', stack: 'MeshImport',
    inModel: () => !!(w().getActiveLayers?.() as Set<string> | undefined)?.has('MeshImport'),
    setInModel: () => {},
    pictures: () => [],
  },
];

export const useEditLayersStore = defineStore('editLayers', {
  state: () => ({
    selected: 'terrain' as EditLayerId,
    /** Bumped whenever a control, a load or the layer stack changes; computeds re-read the DOM. */
    tick: 0,
  }),
  actions: {
    select(id: EditLayerId) { this.selected = id; },
    bump() { this.tick++; },
  },
});

/**
 * Whether a layer's data is needed: it is in the model, or its preview is on the canvas.
 * modules/ui/app-setup.js::loadAllLayers asks before fetching the costly layers: the Amazon's
 * switched-off rivers were carved for 200 s after every terrain load (perf audit 2026-10-04).
 */
export function isEditLayerWanted(id: EditLayerId): boolean {
  const l = EDIT_LAYERS.find((x) => x.id === id);
  if (!l) return true;
  const active = w().getActiveLayers?.() as Set<string> | undefined;
  return l.inModel() || !!active?.has(l.stack);
}
w().isEditLayerWanted = isEditLayerWanted;

/** Show or hide a layer's preview on the canvas (loads it the first time). */
export function setPreviewVisible(stack: string, on: boolean): void {
  const active = w().getActiveLayers?.() as Set<string> | undefined;
  if (!active) return;
  if (active.has(stack) !== on) w().setStackMode?.(stack);
}

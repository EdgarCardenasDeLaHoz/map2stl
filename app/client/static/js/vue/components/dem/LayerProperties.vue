<template>
  <!-- Edit page, right: the selected layer's few settings that matter (design guidelines §1.7).
       Each control writes an existing control by id (dom-fields.ts), so autosave, reloads and
       the composite run exactly as for an edit in the full panels, which stay available as
       ⚙ tools (Data sources, Display, Composite & imports). -->
  <section id="layerProperties" class="lp" :aria-label="`${layer.name} settings`">
    <h2 class="lp-title">{{ layer.name }}</h2>
    <div class="lp-sub">{{ SUBTITLES[layer.id] }}</div>

    <!-- ── Terrain ── -->
    <template v-if="layer.id === 'terrain'">
      <div class="lp-ctl">
        <div class="lp-lbl"><b>Style</b><span class="lp-hint-inline">sets sources, layers and size</span></div>
        <div class="lp-seg" role="group" aria-label="Preset">
          <button v-for="p in PRESETS" :key="p.id" type="button" :title="p.title" @click="applyPreset(p.id)">{{ p.label }}</button>
        </div>
      </div>
      <div class="lp-ctl">
        <label class="lp-lbl" for="lpDemSource"><b>Elevation source</b></label>
        <select id="lpDemSource" class="lp-select" :value="pick('paramDemSource')"
                @change="setField('paramDemSource', ($event.target as HTMLSelectElement).value)">
          <option v-for="o in demSources" :key="o.value" :value="o.value" :disabled="o.disabled">{{ o.label }}</option>
        </select>
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><label for="lpDetail"><b>Detail</b></label><span>{{ detail }} points across</span></div>
        <input id="lpDetail" type="range" class="lp-range" min="200" max="2000" step="100" :value="detail"
               @input="detailDraft = Number(($event.target as HTMLInputElement).value)"
               @change="commitDetail">
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><b>Colours</b></div>
        <div class="lp-seg" role="radiogroup" aria-label="Height map colours">
          <button v-for="c in COLOURS" :key="c.value" type="button" role="radio"
                  :aria-checked="pick('demColormap') === c.value" :class="{ on: pick('demColormap') === c.value }"
                  @click="setField('demColormap', c.value)">{{ c.label }}</button>
        </div>
      </div>
      <button type="button" class="lp-btn" @click="click('loadDemBtn')">↺ Reload terrain</button>
      <div class="lp-hint">A new source or detail reloads the terrain and every layer on it.</div>
    </template>

    <!-- ── Rivers & lakes ── -->
    <template v-else-if="layer.id === 'water'">
      <div class="lp-ctl">
        <div class="lp-lbl"><b>Rivers</b></div>
        <div class="lp-seg" role="radiogroup" aria-label="Which rivers">
          <button v-for="r in RIVER_SETS" :key="r.order" type="button" role="radio" :title="r.title"
                  :aria-checked="minOrder === r.order" :class="{ on: minOrder === r.order }"
                  @click="setMinOrder(r.order)">{{ r.label }}</button>
        </div>
        <div class="lp-hint">{{ riverSourceLabel }} · Strahler order {{ minOrder }} and up</div>
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><label for="lpRiverDepth"><b>River depth</b></label><span>× {{ fmt(pickNum('compositeRiverDepthScale', 1)) }}</span></div>
        <input id="lpRiverDepth" type="range" class="lp-range" min="0.5" max="10" step="0.5"
               :value="pickNum('compositeRiverDepthScale', 1)"
               @change="setField('compositeRiverDepthScale', ($event.target as HTMLInputElement).value)">
        <div class="lp-hint">× the depth HydroRIVERS gives each river from its flow</div>
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><label for="lpRiverWidth"><b>River width</b></label><span>× {{ fmt(pickNum('hydroWidthFactor', 1)) }}</span></div>
        <input id="lpRiverWidth" type="range" class="lp-range" min="0.5" max="5" step="0.5"
               :value="pickNum('hydroWidthFactor', 1)"
               @change="setField('hydroWidthFactor', ($event.target as HTMLInputElement).value)">
      </div>
      <div class="lp-row">
        <div><div class="lp-name">Lakes</div><div class="lp-hint">Larger than {{ fmt(pickNum('compositeLakeMinAreaHa', 1)) }} ha</div></div>
        <input type="checkbox" role="switch" class="lp-switch" aria-label="Lakes"
               :checked="pickChecked('compositeLakesEnabled')"
               @change="setLakes(($event.target as HTMLInputElement).checked)">
      </div>
      <div v-if="pickChecked('compositeLakesEnabled')" class="lp-ctl">
        <div class="lp-lbl"><label for="lpLakeDepth"><b>Lake depth</b></label><span>{{ fmt(pickNum('compositeLakeDepth', 2)) }} m below the shore</span></div>
        <input id="lpLakeDepth" type="range" class="lp-range" min="0.5" max="50" step="0.5"
               :value="pickNum('compositeLakeDepth', 2)"
               @change="setField('compositeLakeDepth', ($event.target as HTMLInputElement).value)">
      </div>
      <div class="lp-hint">The map shows the rivers exactly as they are carved into the print.</div>
    </template>

    <!-- ── Buildings & roads ── -->
    <template v-else-if="layer.id === 'city'">
      <div v-for="c in CITY_PARTS" :key="c.id" class="lp-row">
        <div class="lp-name">{{ c.label }}</div>
        <input type="checkbox" role="switch" class="lp-switch" :aria-label="c.label"
               :checked="pickChecked(`cityLayer_${c.id}_enabled`)"
               @change="setChecked(`cityLayer_${c.id}_enabled`, ($event.target as HTMLInputElement).checked)">
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><label for="lpBuildingH"><b>Building height</b></label><span>{{ heightText }}</span></div>
        <input id="lpBuildingH" type="range" class="lp-range" min="0.5" max="3" step="0.1"
               :value="pickNum('cityLayer_buildings_value', 1)"
               @change="setField('cityLayer_buildings_value', ($event.target as HTMLInputElement).value)">
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><b>Roads</b></div>
        <div class="lp-seg" role="radiogroup" aria-label="Roads">
          <button v-for="m in ROAD_MODES" :key="m.value" type="button" role="radio"
                  :aria-checked="pick('cityLayer_roads_mode') === m.value" :class="{ on: pick('cityLayer_roads_mode') === m.value }"
                  @click="setField('cityLayer_roads_mode', m.value)">{{ m.label }}</button>
        </div>
      </div>
      <button type="button" class="lp-btn" @click="click('openCityTablePanelBtn')">Building heights table ›</button>
      <div class="lp-hint">Heights come from OpenStreetMap; set any building's height in the table.</div>
    </template>

    <!-- ── Satellite colour ── -->
    <template v-else-if="layer.id === 'satellite'">
      <div class="lp-row">
        <div><div class="lp-name">Colour the 3D model</div><div class="lp-hint">Drapes the image over the preview and the 3MF</div></div>
        <input type="checkbox" role="switch" class="lp-switch" aria-label="Colour the 3D model"
               :checked="pick('viewerColormap') === 'satellite'"
               @change="setField('viewerColormap', ($event.target as HTMLInputElement).checked ? 'satellite' : 'terrain')">
      </div>
      <div class="lp-ctl">
        <div class="lp-lbl"><b>Image detail</b></div>
        <div class="lp-seg" role="radiogroup" aria-label="Image detail">
          <button v-for="r in SAT_RES" :key="r" type="button" role="radio"
                  :aria-checked="pick('satImgResolution') === r" :class="{ on: pick('satImgResolution') === r }"
                  @click="setSatRes(r)">{{ r }} px</button>
        </div>
      </div>
    </template>

    <!-- ── Trails ── -->
    <template v-else-if="layer.id === 'trails'">
      <div class="lp-row"><div class="lp-name">Ski pistes</div>
        <input type="checkbox" role="switch" class="lp-switch" aria-label="Ski pistes"
               :checked="pickChecked('trailsShowSki')" @change="setChecked('trailsShowSki', ($event.target as HTMLInputElement).checked)"></div>
      <div class="lp-row"><div class="lp-name">Hiking paths</div>
        <input type="checkbox" role="switch" class="lp-switch" aria-label="Hiking paths"
               :checked="pickChecked('trailsShowHiking')" @change="setChecked('trailsShowHiking', ($event.target as HTMLInputElement).checked)"></div>
      <div class="lp-ctl">
        <div class="lp-lbl"><label for="lpTrailH"><b>Raised by</b></label><span>{{ fmt(pickNum('cityLayer_trails_value', 0.3)) }} mm</span></div>
        <input id="lpTrailH" type="range" class="lp-range" min="0.1" max="2" step="0.1"
               :value="pickNum('cityLayer_trails_value', 0.3)"
               @change="setField('cityLayer_trails_value', ($event.target as HTMLInputElement).value)">
      </div>
    </template>

    <!-- ── Land cover / imported mesh ── -->
    <template v-else>
      <div class="lp-hint">{{ layer.id === 'landcover'
        ? 'Shown on the map only. Its heights (trees, built-up) are a Composite & imports tool in ⚙ Settings.'
        : 'Import, place and blend a mesh with the Composite & imports tool in ⚙ Settings.' }}</div>
    </template>

    <!-- Opacity on the map, for every layer but the terrain -->
    <div v-if="layer.id !== 'terrain'" class="lp-ctl lp-opacity">
      <div class="lp-lbl"><label for="lpOpacity"><b>On the map</b></label><span>{{ opacity }}% opaque</span></div>
      <input id="lpOpacity" type="range" class="lp-range" min="0" max="100" step="5" :value="opacity"
             @input="setOpacity(Number(($event.target as HTMLInputElement).value))">
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { EDIT_LAYERS, useEditLayersStore, type EditLayerId } from '../../stores/editLayers';
import { checked, num, options, setChecked, setField, val } from '../../dom-fields';

const store = useEditLayersStore();
const w = () => window as any;
const layer = computed(() => EDIT_LAYERS.find((l) => l.id === store.selected) || EDIT_LAYERS[0]);

const SUBTITLES: Record<EditLayerId, string> = {
  terrain: 'The ground every other layer sits on',
  water: 'Carved into the model, as printed',
  city: 'Raised from the terrain in the 3D model',
  satellite: 'Colour only: no change to the shape',
  trails: 'Ski pistes and hiking paths',
  landcover: 'Forest, fields, built-up areas',
  mesh: 'A model you imported, placed on the terrain',
};
const PRESETS = [
  { id: 'city', label: 'City', title: 'Every city layer on, true-scale height, pieces sized to the bed' },
  { id: 'mountain', label: 'Mountain', title: '1.5× height, trails and water on, buildings off' },
  { id: 'region', label: 'Region', title: 'Large area: terrain only, rivers and lakes carved' },
  { id: 'coast', label: 'Coast', title: 'Sea-level cap, water engraved, buildings on' },
];
const COLOURS = [
  { value: 'terrain', label: 'Terrain' }, { value: 'viridis', label: 'Viridis' },
  { value: 'rainbow', label: 'Rainbow' }, { value: 'gray', label: 'Gray' },
];
const RIVER_SETS = [
  { order: 5, label: 'Big only', title: 'Strahler order 5 and up: main rivers' },
  { order: 3, label: 'Most', title: 'Order 3 and up' },
  { order: 1, label: 'All', title: 'Every river and stream' },
];
const CITY_PARTS = [
  { id: 'buildings', label: 'Buildings' }, { id: 'roads', label: 'Roads' },
  { id: 'waterways', label: 'Water' }, { id: 'railways', label: 'Rail' }, { id: 'green', label: 'Parks' },
];
const ROAD_MODES = [{ value: 'raised', label: 'Raised' }, { value: 'engraved', label: 'Engraved' }];
const SAT_RES = ['400', '600', '1000'];

// Every read goes through store.tick so the panel follows edits made anywhere.
function pick(id: string) { void store.tick; return val(id); }
function pickNum(id: string, d: number) { void store.tick; return num(id, d); }
function pickChecked(id: string) { void store.tick; return checked(id); }
function click(id: string) { (document.getElementById(id) as HTMLButtonElement | null)?.click(); }
function fmt(v: number) { return Number.isInteger(v) ? String(v) : v.toFixed(1); }

const demSources = computed(() => { void store.tick; return options('paramDemSource'); });

const detailDraft = ref<number | null>(null);
const detail = computed(() => detailDraft.value ?? pickNum('paramDim', 600));
function commitDetail(e: Event) {
  detailDraft.value = null;
  setField('paramDim', (e.target as HTMLInputElement).value);
}

function applyPreset(id: string) {
  w().applyWorkflowPreset?.(id);
  store.bump();
}

const minOrder = computed(() => pickNum('hydroMinOrder', 3));
const riverSourceLabel = computed(() => (pick('hydroSource') === 'natural_earth' ? 'Natural Earth' : 'HydroRIVERS'));
function setMinOrder(o: number) {
  setField('hydroMinOrder', String(o));
  // The hydrology preview reloads on its own load button; min order switches are cached.
  click('loadWaterHydrologyBtn');
  store.bump();
}
function setLakes(on: boolean) {
  if (on) setChecked('compositeEnabled', true);
  setChecked('compositeLakesEnabled', on);
  store.bump();
}

const heightText = computed(() => {
  const v = pickNum('cityLayer_buildings_value', 1);
  return Math.abs(v - 1) < 0.05 ? 'true height' : `× ${v.toFixed(1)}`;
});

function setSatRes(r: string) {
  setField('satImgResolution', r);
  click('loadSatImgBtn');
  store.bump();
}

const opacity = computed(() => pickNum(`layerOpacity_${layer.value.stack}`, 100));
function setOpacity(v: number) {
  setField(`layerOpacity_${layer.value.stack}`, String(v));
  w().setLayerOpacity?.(layer.value.stack, v / 100);
  store.bump();
}
</script>

<style scoped>
.lp { background: #1c1c1e; border-radius: 18px; padding: 16px; margin-bottom: 12px; color: #f5f5f7; }
.lp-title { margin: 0; font-size: 20px; font-weight: 700; }
.lp-sub { color: var(--text-muted); font-size: 13px; margin: 2px 0 14px; }
.lp-ctl { margin: 14px 0; }
.lp-lbl { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 13px; margin-bottom: 6px; }
.lp-lbl span { font-variant-numeric: tabular-nums; color: #f5f5f7; }
.lp-hint-inline { color: var(--text-muted) !important; font-size: 12px; }
.lp-seg { display: flex; background: #2c2c2e; border-radius: 10px; padding: 3px; gap: 2px; }
.lp-seg button {
  flex: 1; border: 0; border-radius: 8px; padding: 5px 0; background: none; color: var(--text-muted);
  font-size: 12px; cursor: pointer;
}
.lp-seg button:hover { color: #f5f5f7; }
.lp-seg button.on { background: #3a3a3c; color: #f5f5f7; font-weight: 600; }
.lp-select { width: 100%; background: #2c2c2e; color: inherit; border: 0; border-radius: 10px; padding: 7px 10px; font-size: 13px; }
.lp-range { width: 100%; accent-color: #0a84ff; }
.lp-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 0; }
.lp-name { font-weight: 600; font-size: 13px; }
.lp-hint { color: var(--text-muted); font-size: 12px; line-height: 1.4; margin-top: 4px; }
.lp-btn {
  width: 100%; margin-top: 6px; padding: 8px 0; border: 0; border-radius: 10px; cursor: pointer;
  background: #2c2c2e; color: #f5f5f7; font-size: 13px; font-weight: 600;
}
.lp-btn:hover { background: #3a3a3c; }
.lp-opacity { border-top: 1px solid #2f2f31; padding-top: 12px; }
.lp-switch {
  appearance: none; -webkit-appearance: none; flex: none; position: relative; cursor: pointer;
  width: 40px; height: 24px; border-radius: 999px; background: #3a3a3c; margin: 0; transition: background .15s;
}
.lp-switch::after {
  content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
  border-radius: 50%; background: #fff; transition: left .15s;
}
.lp-switch:checked { background: #30d158; }
.lp-switch:checked::after { left: 18px; }
.lp-switch:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
</style>

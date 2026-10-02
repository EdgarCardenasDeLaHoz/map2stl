<template>
  <!-- One rack, one row per layer, in _layerOrder (stacked-layers.js) — which is
       the render order and the order every other panel now lists layers in.

       Every row carries the same three properties: visibility, opacity, and
       z-order. Class-specific extras (a resolution readout, the per-layer
       display sections in LayerDisplaySections.vue) hang off that common base
       rather than replacing it. Before this, the visible rack offered opacity
       alone and the reorder arrows lived in a rack rendered into a hidden div,
       so z-order worked in the engine and was unreachable from the UI. -->
  <CollapsibleSection title="🗺️ Layers" :start-open="false" wrap-style="">

    <!-- Kept as the quick all-layers toggle strip. trails-overlay.js:327 and
         app-setup.js:31 both query these buttons by selector, and the row
         checkboxes below drive the same setStackMode, so the two stay in sync
         through the layer-stack-changed event. -->
    <div class="layer-mode-wrap">
      <div class="layer-mode-title">Active Layers</div>
      <div id="layerModeSelector" class="layer-mode-row">
        <button v-for="l in LAYERS" :key="l.key" class="layer-mode-btn"
                :class="{ active: active.has(l.key) }"
                :data-mode="l.key" :title="l.hint">{{ l.icon }} {{ l.short }}</button>
      </div>
    </div>

    <div id="layerRows" style="display:flex;flex-direction:column;gap:2px;">
      <div v-for="(l, i) in orderedLayers" :key="l.key" class="layer-row"
           :class="{ 'layer-row-off': !active.has(l.key) }">

        <!-- Visibility. Switching a layer on also fetches it when it has no
             data yet — see LAYER_AUTOLOAD in stacked-layers.js. -->
        <input type="checkbox" class="layer-row-vis" :id="`layerVis_${l.key}`"
               :checked="active.has(l.key)" :title="`Show ${l.label}`"
               :aria-label="`Show ${l.label}`"
               @change="toggleLayer(l.key)">

        <span class="layer-row-icon" :title="l.hint">{{ l.icon }}</span>
        <!-- Name with its kind (Terrain, Imagery...) underneath: as a column of its own the
             kind and resolution ("Terrain · 600 px") as columns pushed the row past the
             ~250 px panel once text was 11 px. -->
        <span class="layer-row-name" :title="`${l.label} (${l.cls})`">
          <span class="layer-row-label">{{ l.label }}</span>
          <span class="layer-row-class">{{ l.cls }}<span class="layer-row-res" :id="`layerRes_${l.key}`"></span></span>
        </span>

        <input type="range" class="layer-row-opacity" :id="`layerOpacity_${l.key}`"
               min="0" max="100" :value="l.opacity"
               :title="`${l.label} opacity`" :aria-label="`${l.label} opacity`"
               @input="onOpacity(l.key, $event)">
        <span class="layer-row-pct" :id="`layerOpacityPct_${l.key}`">{{ l.opacity }}%</span>

        <!-- z-order. Bottom of the list renders first, so "up" is later/on top. -->
        <span class="layer-row-move">
          <button type="button" class="layer-arrow-btn"
                  :disabled="i === orderedLayers.length - 1"
                  :title="`Move ${l.label} up (renders on top)`"
                  :aria-label="`Move ${l.label} up`"
                  @click="move(l.key, 1)">▲</button>
          <button type="button" class="layer-arrow-btn"
                  :disabled="i === 0"
                  :title="`Move ${l.label} down (renders behind)`"
                  :aria-label="`Move ${l.label} down`"
                  @click="move(l.key, -1)">▼</button>
        </span>
      </div>
    </div>

  </CollapsibleSection>

</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

/**
 * The nine layers, keyed as in _layerOrder. `cls` groups them the way the user
 * thinks about them; it is a per-row badge rather than a group heading because
 * the reorder arrows mean no fixed grouping survives.
 *
 * `res` names the Fetch-tab input whose value the row echoes. Layers without
 * one (city polygons, mesh import, composite) simply show nothing there.
 */
const LAYERS = [
  { key: 'Dem',             icon: '🏔', short: 'DEM',       label: 'DEM',         cls: 'Terrain',  hint: 'Base elevation',              res: 'paramDim',        def: 100 },
  { key: 'WaterHydrology',  icon: '🌊', short: 'Hydrology', label: 'Hydrology',   cls: 'Terrain',  hint: 'Merged hydrology overlay',    res: 'waterResolution', def: 75 },
  { key: 'Sat',             icon: '🌿', short: 'ESA',       label: 'ESA',         cls: 'Imagery',  hint: 'ESA land cover',              res: 'esaResolution',   def: 70 },
  { key: 'SatImg',          icon: '🛰', short: 'Sat',       label: 'Sat Img',     cls: 'Imagery',  hint: 'Satellite imagery',           res: 'satImgResolution', def: 80 },
  { key: 'CityRaster',      icon: '🏙', short: 'City',      label: 'City ↑',      cls: 'City',     hint: 'City heights raster',         res: 'cityRasterDim',   def: 70 },
  { key: 'CityOverlay',     icon: '⬡',  short: 'City Poly', label: 'City ⬡',      cls: 'City',     hint: 'City vector polygons',        res: null,              def: 85 },
  { key: 'Trails',          icon: '🥾', short: 'Trails',    label: 'Trails',      cls: 'Trails',   hint: 'Ski and hiking trails',       res: 'trailsDim',       def: 90 },
  { key: 'MeshImport',      icon: '📐', short: 'Mesh',      label: 'Mesh Import', cls: 'Imported', hint: 'Imported STL/OBJ mesh layer', res: null,              def: 80 },
  { key: 'CompositeDem',    icon: '★',  short: 'Composite', label: 'Composite',   cls: 'Composite', hint: 'Composite DEM',              res: null,              def: 100 },
];

const order = ref<string[]>(LAYERS.map(l => l.key));
const active = ref<Set<string>>(new Set(['Dem', 'CityOverlay']));
const opacity = ref<Record<string, number>>(
  Object.fromEntries(LAYERS.map(l => [l.key, l.def])));

const byKey = Object.fromEntries(LAYERS.map(l => [l.key, l]));

/** Rows in render order, bottom first, each carrying its live opacity. */
const orderedLayers = computed(() =>
  order.value
    .filter(k => byKey[k])
    .map(k => ({ ...byKey[k], opacity: opacity.value[k] ?? byKey[k].def })));

function toggleLayer(key: string) {
  // setStackMode refuses to switch off the last remaining layer, so the
  // checkbox is re-synced from the engine rather than assumed.
  window.setStackMode?.(key);
  _syncFromEngine();
}

function move(key: string, delta: number) {
  window.moveLayer?.(key, delta);
  _syncFromEngine();
}

function onOpacity(key: string, evt: Event) {
  const v = Number((evt.target as HTMLInputElement).value);
  opacity.value = { ...opacity.value, [key]: v };
  window.setLayerOpacity?.(key, v / 100);
}

/** Pull order and active set back from stacked-layers, the owner of both. */
function _syncFromEngine() {
  const o = (window as any).getLayerOrder?.();
  if (Array.isArray(o) && o.length) order.value = o;
  const a = (window as any).getActiveLayers?.();
  if (a instanceof Set) active.value = new Set(a);
}

/**
 * Echo each layer's fetch resolution next to its name. The inputs live in the
 * Fetch tab, so this runs after a short delay and again whenever one changes.
 */
function _syncResSpans() {
  for (const l of LAYERS) {
    if (!l.res) continue;
    const inp = document.getElementById(l.res) as HTMLInputElement | null;
    const span = document.getElementById(`layerRes_${l.key}`);
    if (!inp || !span) continue;
    const paint = () => { span.textContent = inp.value ? inp.value + ' px' : ''; };
    paint();
    inp.addEventListener('change', paint);
    inp.addEventListener('input', paint);
  }
}

let _onStackChanged: (() => void) | null = null;

onMounted(() => {
  _syncFromEngine();
  _onStackChanged = () => _syncFromEngine();
  window.addEventListener('layer-stack-changed', _onStackChanged);
  // Deferred so the Fetch tab's inputs exist to read from.
  setTimeout(_syncResSpans, 200);
});

onBeforeUnmount(() => {
  if (_onStackChanged) {
    window.removeEventListener('layer-stack-changed', _onStackChanged);
    _onStackChanged = null;
  }
});
</script>
<style scoped>
.layer-mode-wrap {
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid #333;
}

.layer-mode-title {
  font-size: 11px;
  color: #aaa;
  margin-bottom: 6px;
}

.layer-mode-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.layer-mode-row .layer-mode-btn {
  padding: 3px 7px;
  min-width: 78px;
  font-size: 11px;
}

.layer-row {
  display: grid;
  grid-template-columns: 14px 18px minmax(0, 1fr) minmax(40px, 54px) 32px 14px;
  gap: 0 5px;
  align-items: center;
  min-height: 28px;
}

/* A layer that is switched off keeps its row, so its opacity and position stay
   visible and adjustable, but reads as inactive. */
.layer-row-off .layer-row-label,
.layer-row-off .layer-row-icon,
.layer-row-off .layer-row-class {
  opacity: 0.45;
}

.layer-row-vis {
  width: 12px;
  height: 12px;
  margin: 0;
  cursor: pointer;
}

.layer-row-icon {
  font-size: 13px;
  text-align: center;
  opacity: 0.9;
}
.layer-row-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.15;
}
.layer-row-label {
  font-size: 11px;
  color: #aaa;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.layer-row-class {
  font-size: 11px;
  color: #8ea6bf;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.layer-row-res {
  color: var(--text-dim);
}
.layer-row-res:not(:empty)::before {
  content: " · ";
}
.layer-row-opacity {
  width: 100%;
}
.layer-row-pct {
  font-size: 11px;
  color: var(--text-dim);
  text-align: right;
  white-space: nowrap;
}
.layer-row-move {
  display: flex;
  flex-direction: column;
  line-height: 1;
  gap: 0;
}
.layer-arrow-btn {
  background: none;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  padding: 0;
  font-size: 11px;
  line-height: 1;
}
.layer-arrow-btn:disabled {
  opacity: 0.35;
  cursor: default;
}
</style>

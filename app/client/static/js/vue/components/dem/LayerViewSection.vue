<template>
  <!-- The all-layers toggle strip, kept hidden in #legacyControls. Visibility, opacity and
       order live in the Edit page's Layers rail (EditLayersPanel.vue, 2026-10-14); this strip
       stays because app-setup.js::setupStackedLayers, trails-overlay.js and the mesh-import
       e2e test click its buttons by selector. stacked-layers.js::setStackMode keeps their
       `active` class in sync. -->
  <CollapsibleSection title="🗺️ Layers" :start-open="false" wrap-style="">
    <div class="layer-mode-wrap">
      <div class="layer-mode-title">Active Layers</div>
      <div id="layerModeSelector" class="layer-mode-row">
        <button v-for="l in LAYERS" :key="l.key" class="layer-mode-btn"
                :class="{ active: active.has(l.key) }"
                :data-mode="l.key" :title="l.hint">{{ l.icon }} {{ l.short }}</button>
      </div>
    </div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { onBeforeUnmount, onMounted, ref } from 'vue';

/** Every stacked-layers.js key, one button each. */
const LAYERS = [
  { key: 'Dem',            icon: '🏔', short: 'DEM',       hint: 'Base elevation' },
  { key: 'WaterHydrology', icon: '🌊', short: 'Hydrology', hint: 'Merged hydrology overlay' },
  { key: 'Sat',            icon: '🌿', short: 'ESA',       hint: 'ESA land cover' },
  { key: 'SatImg',         icon: '🛰', short: 'Sat',       hint: 'Satellite imagery' },
  { key: 'CityRaster',     icon: '🏙', short: 'City',      hint: 'City heights raster' },
  { key: 'CityOverlay',    icon: '⬡',  short: 'City Poly', hint: 'City vector polygons' },
  { key: 'Trails',         icon: '🥾', short: 'Trails',    hint: 'Ski and hiking trails' },
  { key: 'MeshImport',     icon: '📐', short: 'Mesh',      hint: 'Imported STL/OBJ mesh layer' },
  { key: 'Borders',        icon: '🗺', short: 'Borders',   hint: 'Country and state borders' },
  { key: 'CompositeDem',   icon: '★',  short: 'Composite', hint: 'Composite DEM' },
];

const active = ref<Set<string>>(new Set(['Dem', 'CityOverlay']));

function sync() {
  const a = (window as any).getActiveLayers?.();
  if (a instanceof Set) active.value = new Set(a);
}

onMounted(() => {
  sync();
  window.addEventListener('layer-stack-changed', sync);
});
onBeforeUnmount(() => window.removeEventListener('layer-stack-changed', sync));
</script>
<style scoped>
.layer-mode-wrap { margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #333; }
.layer-mode-title { font-size: 11px; color: #aaa; margin-bottom: 6px; }
.layer-mode-row { display: flex; flex-wrap: wrap; gap: 4px; }
.layer-mode-row .layer-mode-btn { padding: 3px 7px; min-width: 78px; font-size: 11px; }
</style>

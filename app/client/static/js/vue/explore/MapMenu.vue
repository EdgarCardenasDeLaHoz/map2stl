<template>
  <div class="mm" ref="root">
    <button id="floatingMapSettingsBtn" type="button" class="map-floating-btn" :class="{ active: open }"
            aria-haspopup="dialog" :aria-expanded="open" title="Map style, terrain relief, grid and labels"
            aria-label="Map style and layers" @click="open = !open">
      🗺 <span class="btn-label">Map ▾</span>
    </button>

    <div v-if="open" id="mapSettingsPanel" class="mm-pop" role="dialog" aria-label="Map layers"
         @keydown.esc.stop.prevent="close">
      <div class="mm-head">
        <span class="mm-title">Map</span>
        <button id="closeMapSettingsBtn" type="button" class="mm-x" aria-label="Close map menu" title="Close"
                @click="close">✕</button>
      </div>

      <div class="mm-card">
        <label class="mm-row">
          <span>Style</span>
          <select id="mapTileLayerExplore" class="mm-select" :value="layers.style" aria-label="Map style"
                  @change="onStyle">
            <option v-for="s in TILE_STYLES" :key="s.key" :value="s.key">{{ s.label }}</option>
          </select>
        </label>

        <div class="mm-row">
          <span id="mmTerrainLbl">Terrain relief</span>
          <button id="showTerrainOverlayExplore" type="button" role="switch" class="mm-sw" :class="{ on: layers.terrain }"
                  :aria-checked="layers.terrain" aria-labelledby="mmTerrainLbl" @click="toggleTerrain"></button>
        </div>

        <div v-if="layers.terrain" id="terrainOpacityRowExplore" class="mm-row">
          <span>Opacity</span>
          <span class="mm-op">
            <input id="terrainOverlayOpacityExplore" type="range" min="0" max="100" :value="layers.terrainOpacity"
                   aria-label="Terrain relief opacity" @input="onOpacity">
            <output id="terrainOpacityValueExplore" class="mm-dim">{{ layers.terrainOpacity }}%</output>
          </span>
        </div>

        <div class="mm-row">
          <span id="mmGridLbl">Grid lines</span>
          <button id="showGridlinesExplore" type="button" role="switch" class="mm-sw grid" :class="{ on: layers.grid }"
                  :aria-checked="layers.grid" aria-labelledby="mmGridLbl" @click="toggleGrid"></button>
        </div>

        <div class="mm-row">
          <span id="mmLabelsLbl">Place names</span>
          <button id="showLabelsExplore" type="button" role="switch" class="mm-sw" :class="{ on: layers.labels }"
                  :aria-checked="layers.labels" aria-labelledby="mmLabelsLbl" @click="toggleLabels"></button>
        </div>
      </div>
      <p class="mm-foot">Seven base maps. Relief and place names draw on top. Remembered in this browser.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { mapLayers as layers, TILE_STYLES } from './mapLayers';

const open = ref(false);
const root = ref<HTMLElement | null>(null);
const w = () => window as any;

function close() { open.value = false; }

function onStyle(e: Event) {
  w().setTileLayer?.((e.target as HTMLSelectElement).value);
}
function toggleTerrain() {
  w().toggleTerrainOverlay?.(!layers.terrain);
}
function onOpacity(e: Event) {
  w().setTerrainOverlayOpacity?.(Number((e.target as HTMLInputElement).value));
}
function toggleGrid() {
  w().toggleMapGrid?.(!layers.grid);
}
function toggleLabels() {
  w().toggleMapLabels?.(!layers.labels);
}

function onDocDown(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) close();
}
onMounted(() => document.addEventListener('mousedown', onDocDown));
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown));
</script>

<style scoped>
.mm { position: relative; }
.mm-pop {
  position: absolute; top: calc(100% + 8px); right: 0; width: 300px; z-index: 1100;
  background: rgba(28, 28, 30, 0.97); border-radius: 14px; padding: 12px 12px 10px; color: var(--text, #f2f2f7);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55);
}
.mm-head { display: flex; align-items: center; justify-content: space-between; padding: 0 4px 8px; }
.mm-title { font-size: 15px; font-weight: 700; }
.mm-x { background: none; border: 0; color: var(--dim, #98989f); font-size: 14px; cursor: pointer; padding: 4px 6px; }
.mm-x:hover { color: #fff; }
.mm-card { background: var(--card, #2c2c2e); border-radius: 12px; }
.mm-row {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 40px;
  padding: 6px 14px; border-bottom: 1px solid var(--line, #3a3a3c); font-size: 14px;
}
.mm-row:last-child { border-bottom: 0; }
.mm-select {
  background: none; border: 0; color: var(--accent, #0a84ff); font-size: 14px; text-align: right; cursor: pointer; max-width: 160px;
}
.mm-select option { background: #2c2c2e; color: #f2f2f7; }
.mm-op { display: flex; align-items: center; gap: 8px; }
.mm-op input[type='range'] { width: 110px; accent-color: var(--accent, #0a84ff); }
.mm-dim { color: var(--dim, #98989f); font-size: 12px; min-width: 32px; text-align: right; }
.mm-sw {
  position: relative; width: 44px; height: 26px; border: 0; border-radius: 13px; padding: 0; cursor: pointer;
  background: #48484a; transition: background 0.15s;
}
.mm-sw::after {
  content: ''; position: absolute; top: 2px; left: 2px; width: 22px; height: 22px; border-radius: 50%;
  background: #fff; transition: transform 0.15s;
}
.mm-sw.on { background: var(--accent, #0a84ff); }
.mm-sw.on.grid { background: var(--corr, #30d158); }
.mm-sw.on::after { transform: translateX(18px); }
.mm-sw:focus-visible, .mm-select:focus-visible, .mm-x:focus-visible { outline: 2px solid var(--accent, #0a84ff); outline-offset: 2px; }
.mm-foot { margin: 8px 6px 0; font-size: 11px; color: var(--dim, #98989f); line-height: 1.4; }
</style>

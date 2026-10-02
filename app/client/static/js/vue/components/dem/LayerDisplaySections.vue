<template>
  <!-- Per-layer display controls, split out of LayerViewSection so that the View
       tab can read global chrome first and then one section per layer in the
       render-stack order. Nothing here refetches; every control repaints a
       payload that is already loaded, which is why these live in View rather
       than in Fetch. -->

  <!-- Moved from Fetch → Hydrology: colouring the loaded hydrology layer is a
       repaint (water-hydrology-combined.js listens for #hydroColorMode changes). -->
  <CollapsibleSection title="🌊 Hydrology Display" :start-open="false">
    <div class="param-group">
      <label for="hydroColorMode" title="How rivers are drawn in the hydrology layer. Changing it redraws without reloading.">Colour:</label>
      <select id="hydroColorMode">
        <option value="depth" selected>Depth (blue)</option>
        <option value="order">Strahler order</option>
      </select>
    </div>
    <div id="hydroOrderLegend" class="hydro-order-legend" hidden></div>
  </CollapsibleSection>

  <CollapsibleSection title="🏙 City Display" :start-open="false">
    <!-- Moved from Fetch → Cities. city-raster.js reads it when it paints the
         heights raster. -->
    <div class="param-group">
      <label for="cityColormap" title="Colour scheme for the city heights raster. 'Same as DEM' follows the main DEM colormap.">Colormap:</label>
      <select id="cityColormap" aria-label="City layer colormap">
        <option value="inherit" selected>Same as DEM</option>
        <option value="rainbow">Rainbow</option>
        <option value="terrain">Terrain</option>
        <option value="viridis">Viridis</option>
        <option value="jet">Jet</option>
        <option value="hot">Hot</option>
        <option value="gray">Gray</option>
      </select>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px 10px;margin-bottom:6px;">
      <label class="check-label"><input type="checkbox" id="cityLayerBuildings" checked aria-label="Show city buildings polygons"> 🏠 Buildings</label>
      <label class="check-label"><input type="checkbox" id="cityLayerRoads" checked aria-label="Show city roads polylines"> 🛣 Roads</label>
      <label class="check-label"><input type="checkbox" id="cityLayerWaterways" checked aria-label="Show city waterways"> 💧 Waterways</label>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
      <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;">Buildings <input type="color" id="layerBuildingsColor" value="#c8b89a" class="city-color-swatch" aria-label="Buildings polygon color"></label>
      <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;">Roads <input type="color" id="layerRoadsColor" value="#cc8844" class="city-color-swatch" aria-label="Roads polyline color"></label>
      <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;">Water <input type="color" id="layerWaterwaysColor" value="#4488cc" class="city-color-swatch" aria-label="Waterways color"></label>
    </div>
    <div style="margin-top:6px;">
      <button id="viewOpenCityTablePanelBtn" class="btn btn-secondary" style="font-size:11px;padding:4px 8px;" @click="toggleCityTablePanel">📋 {{ cityTableToggleLabel }}</button>
    </div>
  </CollapsibleSection>

  <CollapsibleSection title="🥾 Trails Display" :start-open="false">
    <div style="display:flex;flex-wrap:wrap;gap:6px 10px;margin-bottom:6px;">
      <label class="check-label"><input type="checkbox" id="trailsShowSki" checked aria-label="Show ski pistes"> ⛷ Ski</label>
      <label class="check-label"><input type="checkbox" id="trailsShowHiking" checked aria-label="Show hiking paths"> 🥾 Hiking</label>
      <label class="check-label"><input type="checkbox" id="trailsShowAreas" checked aria-label="Tint ski-area and piste polygon interiors"> ▨ Area fill</label>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
      <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;">Ski <input type="color" id="trailsSkiColor" value="#28bee6" class="city-color-swatch" aria-label="Ski trail color"></label>
      <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;">Hiking <input type="color" id="trailsHikingColor" value="#eb8228" class="city-color-swatch" aria-label="Hiking trail color"></label>
    </div>
    <div style="margin-top:6px;">
      <label class="check-label"><input type="checkbox" id="trailsColorByDifficulty" aria-label="Color ski pistes by difficulty grade"> 🎨 Color ski by difficulty</label>
    </div>
    <!-- Built from window.TRAILS_DIFFICULTY_RGB and the class names the last
         response carried, so the swatches always match what was painted. -->
    <div v-if="difficultyLegend.length" style="display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:6px;">
      <span v-for="entry in difficultyLegend" :key="entry.name"
            style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;">
        <span :style="{background: entry.css, width: '10px', height: '10px', borderRadius: '2px', display: 'inline-block'}"></span>{{ entry.name }}
      </span>
      <span v-if="difficultyUntagged" style="font-size:11px;color:var(--text-dim);">ungraded pistes keep the ski color</span>
    </div>
  </CollapsibleSection>

  <!-- Moved from the Composite tab header: recolouring the composite preview
       changes nothing in the 3D model. composite-dem.js listens by id. -->
  <CollapsibleSection title="★ Composite Display" :start-open="false">
    <div class="param-group">
      <label for="compositeColormap" title="Colour scheme for the composite output. 'Same as DEM' follows the main DEM colormap.">Colormap:</label>
      <select id="compositeColormap" aria-label="Composite layer colormap">
        <option value="inherit" selected>Same as DEM</option>
        <option value="rainbow">Rainbow</option>
        <option value="terrain">Terrain</option>
        <option value="viridis">Viridis</option>
        <option value="jet">Jet</option>
        <option value="hot">Hot</option>
        <option value="gray">Gray</option>
      </select>
    </div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { onBeforeUnmount, onMounted, ref } from 'vue';

const cityTableToggleLabel = ref('Show Buildings Table');

// One entry per graded difficulty class, in the server's order. Empty until a
// trails payload has been received, because the class names come from the
// response rather than from a second list kept here.
const difficultyLegend = ref<{ name: string; css: string }[]>([]);
const difficultyUntagged = ref(false);

/** Rebuild the legend from the retained payload's class list and the palette. */
function _syncDifficultyLegend() {
  const classes = (window as any).appState?.lastTrailsData?.difficulty_classes;
  const palette = (window as any).TRAILS_DIFFICULTY_RGB;
  if (!Array.isArray(classes) || !Array.isArray(palette)) {
    difficultyLegend.value = [];
    return;
  }
  // Class index 0 is "untagged" and has no swatch, so the palette is offset by
  // one relative to the name list.
  difficultyLegend.value = classes.map((name: string, i: number) => {
    const rgb = palette[i + 1];
    return rgb ? { name, css: `rgb(${rgb[0]},${rgb[1]},${rgb[2]})` } : null;
  }).filter(Boolean) as { name: string; css: string }[];
  difficultyUntagged.value = difficultyLegend.value.length > 0;
}

function _syncCityTableToggleLabel() {
  const collapsed = (window as any).isCityBuildingsPanelCollapsed?.();
  cityTableToggleLabel.value = collapsed ? 'Show Buildings Table' : 'Hide Buildings Table';
}

function toggleCityTablePanel() {
  (window as any).toggleCityBuildingsPanel?.();
  _syncCityTableToggleLabel();
}

let _onCityPanelState: ((evt: Event) => void) | null = null;
let _onTrailsRendered: ((evt: Event) => void) | null = null;

onMounted(() => {
  _onCityPanelState = () => _syncCityTableToggleLabel();
  window.addEventListener('city-buildings-panel-state', _onCityPanelState);
  _syncCityTableToggleLabel();

  // The legend can only be built once a payload has arrived, so it is rebuilt
  // on every trails repaint. The sync also runs now, for the case where a
  // payload was already loaded before this section mounted.
  _onTrailsRendered = () => _syncDifficultyLegend();
  window.addEventListener('trails-rendered', _onTrailsRendered);
  _syncDifficultyLegend();
});

onBeforeUnmount(() => {
  if (_onCityPanelState) {
    window.removeEventListener('city-buildings-panel-state', _onCityPanelState);
    _onCityPanelState = null;
  }
  if (_onTrailsRendered) {
    window.removeEventListener('trails-rendered', _onTrailsRendered);
    _onTrailsRendered = null;
  }
});
</script>

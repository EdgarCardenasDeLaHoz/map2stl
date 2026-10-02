<template>
  <!-- Leaflet map view — IDs must stay for Leaflet init and event listeners -->
  <div id="mapContainer" class="map-container">
    <div id="map"></div>

    <!-- View controls in the map's corner (design guidelines §2 Designer, F-DESIGN): Globe and
         the map menu. Terrain relief, grid and labels live in that menu; their old floating
         buttons stay in the DOM (hidden) because event-listeners-map.js / map-globe.js keep
         their state by id. -->
    <div class="map-floating-controls map-view-ctl">
      <button id="floatingGlobeToggle"   class="map-floating-btn" title="Switch to 3D Globe view" aria-label="Switch to 3D Globe">🌍 <span class="btn-label">Globe</span></button>
      <button id="floatingMapSettingsBtn" class="map-floating-btn" title="Map style, terrain relief, grid and labels" aria-label="Map style and layers">🗺 <span class="btn-label">Map ▾</span></button>
      <button id="floatingTerrainToggle" class="map-floating-btn" hidden title="Terrain relief" aria-label="Terrain relief"></button>
      <button id="floatingGridToggle"    class="map-floating-btn" hidden title="Grid lines" aria-label="Grid lines"></button>
      <button id="floatingLabelsToggle"  class="map-floating-btn" hidden title="Map labels" aria-label="Map labels"></button>
    </div>

    <!-- Map Settings Panel -->
    <div id="mapSettingsPanel" class="map-settings-panel hidden">
      <div class="map-settings-header">
        <span>Map Settings</span>
        <button id="closeMapSettingsBtn" class="map-settings-close" aria-label="Close map settings" title="Close">✕</button>
      </div>
      <div class="map-settings-body">
        <div class="map-settings-row">
          <label for="mapTileLayerExplore">Map Style</label>
          <select id="mapTileLayerExplore" class="map-settings-select">
            <option value="osm">OpenStreetMap</option>
            <option value="osm-topo">OpenTopoMap</option>
            <option value="esri-world">ESRI World Imagery</option>
            <option value="esri-topo">ESRI World Topo</option>
            <option value="carto-light">CartoDB Positron</option>
            <option value="carto-dark">CartoDB Dark Matter</option>
            <option value="stamen-terrain">Stamen Terrain</option>
          </select>
        </div>
        <div class="map-settings-row">
          <label><input type="checkbox" id="showTerrainOverlayExplore"> Terrain Overlay</label>
        </div>
        <div class="map-settings-row" id="terrainOpacityRowExplore" style="display:none;">
          <label>Opacity</label>
          <input type="range" id="terrainOverlayOpacityExplore" min="0" max="100" value="70" class="map-settings-terrain-opacity">
          <span id="terrainOpacityValueExplore" class="map-settings-opacity-value">70%</span>
        </div>
        <div class="map-settings-row">
          <label><input type="checkbox" id="showGridlinesExplore"> Grid Lines</label>
        </div>
        <div class="map-settings-row">
          <label><input type="checkbox" id="showLabelsExplore"> Map Labels</label>
        </div>
      </div>
    </div>

    <!-- Landmark search + named landmarks near the box edge (F-UX batch 2) -->
    <LandmarkSearch />
    <EdgeLandmarkWarnings fetcher />

    <!-- The selected region (design guidelines §2 Manager: details of the selection, one
         primary action). "+ New region" sits under the region list (SidebarListView.vue). -->
    <!-- New region: a searched place, drawing, or a box being named (new-region.js). -->
    <NewRegionCard />

    <div v-if="hasSelection && newRegionPhase === 'idle'" class="map-selection-card" role="region" :aria-label="`${regionName} selected`">
      <div class="msc-text">
        <div class="msc-name">{{ regionName }}</div>
        <div class="msc-meta">{{ regionMeta }}</div>
      </div>
      <button type="button" class="msc-btn" title="Edit the name, group and bounds" @click="editBox">✎ Edit box</button>
      <button id="exploreLoadDemBtn" type="button" class="msc-btn primary"
              :title="`Open ${regionName} in Edit and load its DEM`"
              :aria-label="`Load DEM for ${regionName}`"
              @click="loadDem">Load DEM ›</button>
    </div>

  </div>
</template>
<script setup lang="ts">
// Leaflet initialises by reading #map after DOMContentLoaded.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useAppStore } from '../../stores/app';
import EdgeLandmarkWarnings from './EdgeLandmarkWarnings.vue';
import LandmarkSearch from './LandmarkSearch.vue';
import NewRegionCard from './NewRegionCard.vue';
import { formatBboxDims } from '../../../modules/regions/region-geometry.js';

const store = useAppStore();
type Box = { name: string; north: number; south: number; east: number; west: number };
const hasSelection = computed(() => !!store.selectedRegion);
const regionName = computed(() => store.selectedRegion?.name ?? '');
const regionMeta = computed(() => {
  const r = store.selectedRegion as Box | null;
  if (!r) return '';
  const lat = (r.north + r.south) / 2;
  const lon = (r.east + r.west) / 2;
  const pos = `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(2)}° ${lon >= 0 ? 'E' : 'W'}`;
  return `${formatBboxDims(r)} · ${pos}`;
});

const newRegionPhase = ref('idle');
const onNewRegion = (e: Event) => { newRegionPhase.value = (e as CustomEvent).detail.phase; };
onMounted(() => window.addEventListener('map2stl:new-region', onNewRegion));
onBeforeUnmount(() => window.removeEventListener('map2stl:new-region', onNewRegion));

function loadDem() {
  (window as any).loadSelectedRegionDem?.();
}
function editBox() {
  const w = window as any;
  const i = (w.getCoordinatesData?.() || []).findIndex((r: Box) => r.name === regionName.value);
  if (i >= 0) void w.openRegionEditor?.(i);
}
</script>

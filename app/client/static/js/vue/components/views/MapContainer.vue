<template>
  <!-- Leaflet map view — IDs must stay for Leaflet init and event listeners -->
  <div id="mapContainer" class="map-container">
    <ExploreMap />

    <!-- View controls in the map's corner (mockups 03 and 08): the Globe and the Map menu.
         Terrain relief, grid lines and place names live in the menu (MapMenu.vue). -->
    <div class="map-floating-controls map-view-ctl">
      <button id="floatingGlobeToggle" type="button" class="map-floating-btn" :class="{ active: globeOpen }"
              :aria-pressed="globeOpen" title="Switch to the 3D globe" aria-label="Switch to 3D Globe"
              @click="toggleGlobe">🌍 <span class="btn-label">Globe</span></button>
      <MapMenu />
    </div>

    <!-- Landmark search + named landmarks near the box edge (F-UX batch 2) -->
    <LandmarkSearch />
    <EdgeLandmarkWarnings fetcher />

    <!-- The selected region (design guidelines §2 Manager: details of the selection, one
         primary action). "+ New region" sits under the region list (RegionList.vue). -->
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
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useAppStore } from '../../stores/app';
import EdgeLandmarkWarnings from './EdgeLandmarkWarnings.vue';
import LandmarkSearch from './LandmarkSearch.vue';
import NewRegionCard from './NewRegionCard.vue';
import ExploreMap from '../../explore/ExploreMap.vue';
import MapMenu from '../../explore/MapMenu.vue';
import { globeOpen } from '../../explore/globeState';
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

function toggleGlobe() {
  (window as any).setGlobeOpen?.(!globeOpen.value);
}
function loadDem() {
  (window as any).loadSelectedRegionDem?.();
}
function editBox() {
  const w = window as any;
  const i = (w.getCoordinatesData?.() || []).findIndex((r: Box) => r.name === regionName.value);
  if (i >= 0) void w.openRegionEditor?.(i);
}
</script>

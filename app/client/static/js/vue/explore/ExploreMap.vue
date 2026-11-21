<template>
  <!-- The Explore page's Leaflet map. #map stays: Edit, Extrude and the draw tools reach the
       map through window.getMap(), registered by leafletMap.ts::initMap. -->
  <div id="map" ref="el"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRegionsStore } from '../stores/regions';
import { destroyMap, initMap, installLeafletGlobals } from './leafletMap';
import { mapLayers } from './mapLayers';
import { startRegionBoxes } from './regionBoxes';

declare const L: any;

const el = ref<HTMLElement | null>(null);
const store = useRegionsStore();
let stopBoxes: (() => void) | null = null;
let stopMirror: (() => void) | null = null;
let resizeObs: ResizeObserver | null = null;

/** Edit's map controls are hidden inputs that other modules still read: keep them in step. */
function mirrorIntoEdit() {
  return watch(mapLayers, (m) => {
    const tile = document.getElementById('mapTileLayer') as HTMLSelectElement | null;
    if (tile && tile.value !== m.style) tile.value = m.style;
    const terrain = document.getElementById('showTerrainOverlay') as HTMLInputElement | null;
    if (terrain) terrain.checked = m.terrain;
    const op = document.getElementById('terrainOverlayOpacity') as HTMLInputElement | null;
    if (op) op.value = String(m.terrainOpacity);
    const label = document.getElementById('terrainOpacityValue');
    if (label) label.textContent = `${m.terrainOpacity}%`;
  }, { deep: true });
}

function start() {
  if (!el.value || typeof L === 'undefined') return;
  installLeafletGlobals();
  const map = initMap(el.value);
  stopBoxes = startRegionBoxes(map, store);
  stopMirror = mirrorIntoEdit();
  // The panel width animates and the window resizes: Leaflet needs to be told.
  if (typeof ResizeObserver !== 'undefined' && el.value.parentElement) {
    resizeObs = new ResizeObserver(() => map.invalidateSize());
    resizeObs.observe(el.value.parentElement);
  }
}

// main-vue.ts announces the app-state bridge once it has taken over window.appState: the map
// must not register itself (setMap, setPreloadedLayer ...) before that.
onMounted(() => {
  if ((window as any).__map2stlBridgeReady) start();
  else window.addEventListener('map2stl:bridge-ready', start, { once: true });
});

onBeforeUnmount(() => {
  window.removeEventListener('map2stl:bridge-ready', start);
  resizeObs?.disconnect();
  stopMirror?.();
  stopBoxes?.();
  destroyMap();
});
</script>

<style>
/* Dark controls on the map (mockups 01-10): Leaflet's white bars become panel-coloured cards. */
#map .leaflet-bar { border: 0; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.4); border-radius: 10px; overflow: hidden; }
#map .leaflet-bar a, #map .leaflet-bar a:hover {
  background-color: #2c2c2e; color: #f2f2f7; border-bottom: 1px solid #3a3a3c; width: 34px; height: 34px; line-height: 34px;
}
#map .leaflet-bar a:last-child { border-bottom: 0; }
#map .leaflet-bar a:hover { background-color: #3a3a3c; }
#map .leaflet-bar a.leaflet-disabled { background-color: #2c2c2e; color: #636366; }
/* Leaflet.draw's toolbar icons are dark sprites on white: invert the whole button. */
#map .leaflet-draw-toolbar a { background-color: #d3d3d8; filter: invert(1); }
#map .leaflet-draw-toolbar a:hover { background-color: #c0c0c6; }
</style>

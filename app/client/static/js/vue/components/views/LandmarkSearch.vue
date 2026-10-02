<template>
  <!-- Landmark search on the Explore map (GET /api/geocode, Nominatim). Searches
       on submit only — the Nominatim usage policy forbids search-as-you-type. -->
  <div id="landmarkSearch" class="landmark-search" @keydown.esc="clearResults">
    <form class="landmark-search-row" @submit.prevent="search">
      <input id="landmarkSearchInput" v-model="query" type="search" class="landmark-search-input"
             placeholder="Search a place or landmark…" aria-label="Search a place or landmark"
             autocomplete="off">
      <button id="landmarkSearchBtn" type="submit" class="landmark-search-btn"
              :disabled="loading || !query.trim()" title="Search (OpenStreetMap Nominatim)">
        {{ loading ? '…' : '🔍' }}
      </button>
    </form>
    <div v-if="error" class="landmark-search-msg error">{{ error }}</div>
    <div v-else-if="searched && !results.length && !loading" class="landmark-search-msg">No matches.</div>
    <ul v-if="results.length" class="landmark-results" role="listbox">
      <li v-for="(r, i) in results" :key="i" role="option" :aria-selected="picked === r"
          :class="{ picked: picked === r }" :title="r.display_name" @click="goTo(r)">
        <span class="landmark-name">{{ r.name }}</span>
        <span class="landmark-caption">{{ caption(r) }}</span>
        <span class="landmark-where">{{ r.display_name }}</span>
      </li>
    </ul>
    <div v-if="picked" class="landmark-actions">
      <span v-if="pickedInside" class="landmark-inside">✓ inside the box</span>
      <button v-else-if="hasBox" id="landmarkExtendBtn" type="button" class="landmark-action-btn landmark-extend-btn"
              title="Grow the region box just enough to include this place (plus 100 m)"
              @click="extendBox">⤢ Extend the box to include it</button>
      <span v-else class="landmark-inside">Select or draw a region to extend it</span>
      <button type="button" class="landmark-action-btn" title="Clear" @click="clearAll">✕</button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useAppStore } from '../../stores/app';
import {
  bboxContains, extendBboxToInclude, placeCaption, toBbox,
} from '../../../modules/map/landmarks.js';

type Place = {
  name: string; display_name: string; lat: number; lon: number;
  bbox: { north: number; south: number; east: number; west: number } | null;
  class?: string; type?: string;
};

const store = useAppStore();
const query = ref('');
const results = ref<Place[]>([]);
const picked = ref<Place | null>(null);
const loading = ref(false);
const searched = ref(false);
const error = ref('');
let marker: any = null;

const w = window as any;

function currentBox() {
  return toBbox(store.boundingBox as any) || toBbox(store.selectedRegion as any);
}
// boundingBox is a raw (non-reactive) Leaflet layer, so box edits are tracked
// through EV.BBOX_CHANGED instead.
const boxVersion = ref(0);
const hasBox = computed(() => {
  void boxVersion.value;
  void store.selectedRegion;
  return !!currentBox();
});
const pickedInside = computed(() => {
  const p = picked.value;
  void boxVersion.value;
  void store.selectedRegion;
  return !!p && bboxContains(currentBox(), p.lat, p.lon);
});
let unsubscribe: (() => void) | null = null;
function subscribe() {
  unsubscribe = w.events?.on?.(w.EV?.BBOX_CHANGED, () => { boxVersion.value++; }) || null;
}
onMounted(() => {
  // vue-main.js runs before main.js defines window.events.
  if (w.events) subscribe();
  else document.addEventListener('DOMContentLoaded', subscribe, { once: true });
});

function caption(r: Place) { return placeCaption(r); }

async function search() {
  const q = query.value.trim();
  if (!q || loading.value) return;
  loading.value = true;
  error.value = '';
  const { data, error: err } = await w.api.geocode.search(q, 6);
  loading.value = false;
  searched.value = true;
  if (err) { error.value = String(err); results.value = []; return; }
  results.value = data?.results || [];
  if (results.value.length === 1) goTo(results.value[0]);
}

function goTo(r: Place) {
  picked.value = r;
  const map = w.getMap?.();
  const L = w.L;
  if (!map || !L) return;
  if (r.bbox) {
    map.fitBounds([[r.bbox.south, r.bbox.west], [r.bbox.north, r.bbox.east]], { maxZoom: 17 });
  } else {
    map.setView([r.lat, r.lon], 16);
  }
  if (marker) marker.remove();
  marker = L.circleMarker([r.lat, r.lon], {
    radius: 7, color: '#ffcc00', weight: 3, fillColor: '#ffcc00', fillOpacity: 0.35,
  }).addTo(map).bindTooltip(r.name, { direction: 'top', offset: [0, -8] });
}

function extendBox() {
  const p = picked.value;
  const box = currentBox();
  const next = p && box ? extendBboxToInclude(box, p) : null;
  if (!next) return;
  const r5 = (v: number) => Math.round(v * 1e5) / 1e5;
  const b = { north: r5(next.north), south: r5(next.south), east: r5(next.east), west: r5(next.west) };
  w.setBboxInputValues?.(b.north, b.south, b.east, b.west);
  const sel = store.selectedRegion as any;
  if (sel) w.setSelectedRegion?.({ ...sel, ...b });
  w.setBboxRectangle?.(b.north, b.south, b.east, b.west);   // emits EV.BBOX_CHANGED
  w.getMap?.()?.fitBounds([[b.south, b.west], [b.north, b.east]], { padding: [30, 30] });
  w.showToast?.(`Box extended to include ${p!.name}. Reload layers (↺) and 💾 Save in the Edit tab to keep it.`,
    'info', 7000);
}

function clearResults() { results.value = []; searched.value = false; }

function clearAll() {
  clearResults();
  picked.value = null;
  error.value = '';
  if (marker) { marker.remove(); marker = null; }
}

onBeforeUnmount(() => { if (marker) marker.remove(); unsubscribe?.(); });
</script>
<style scoped>
.landmark-search {
  position: absolute;
  top: 10px;
  left: 60px;
  z-index: 1000;
  width: min(320px, calc(100% - 140px));
  font-size: 12px;
}
.landmark-search-row { display: flex; gap: 4px; }
.landmark-search-input {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
  border-radius: 4px;
  border: 1px solid #555;
  background: rgba(30, 30, 30, 0.92);
  color: #eee;
}
.landmark-search-btn, .landmark-action-btn {
  border: 1px solid #555;
  border-radius: 4px;
  background: rgba(40, 40, 40, 0.92);
  color: #eee;
  cursor: pointer;
  padding: 4px 8px;
}
.landmark-search-btn:disabled { opacity: 0.5; cursor: default; }
.landmark-search-msg {
  margin-top: 3px; padding: 4px 8px; border-radius: 4px;
  background: rgba(30, 30, 30, 0.92); color: #bbb;
}
.landmark-search-msg.error { color: #e74c3c; }
.landmark-results {
  list-style: none; margin: 3px 0 0; padding: 0;
  max-height: 260px; overflow-y: auto;
  background: rgba(30, 30, 30, 0.95); border: 1px solid #444; border-radius: 4px;
}
.landmark-results li {
  display: grid; grid-template-columns: 1fr auto; gap: 0 6px;
  padding: 5px 8px; cursor: pointer; border-bottom: 1px solid #333;
}
.landmark-results li:hover, .landmark-results li.picked { background: #2d3b48; }
.landmark-name { color: #eee; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.landmark-caption { color: #8ab; font-size: 10px; }
.landmark-where {
  grid-column: 1 / -1; color: var(--text-dim); font-size: 10px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.landmark-actions {
  display: flex; gap: 4px; align-items: center; margin-top: 3px;
  padding: 3px; border-radius: 4px; background: rgba(30, 30, 30, 0.92);
}
.landmark-extend-btn { flex: 1; }
.landmark-inside { flex: 1; color: #8c8; padding-left: 5px; }
</style>

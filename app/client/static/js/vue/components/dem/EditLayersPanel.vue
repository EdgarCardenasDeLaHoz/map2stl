<template>
  <!-- Edit page, left: the layers of the print, each a picture from its own canvas, a status
       line and a switch for whether it is in the model (design guidelines §2 Designer; mockup
       claude/mockups/2026-10-01/edit.html). Clicking a row selects it; its settings show on the
       right (LayerSettings.vue). Definitions: stores/editLayers.ts. -->
  <aside id="editLayersPanel" class="el-panel" aria-label="Layers">
    <div class="el-cap">Layers</div>
    <div v-for="l in shown" :key="l.id" class="el-row" role="button" tabindex="0"
         :class="{ sel: store.selected === l.id }" :aria-pressed="store.selected === l.id"
         :aria-label="`${l.name}: ${status(l)}. Show its settings`"
         :data-layer="l.id" @click="store.select(l.id)" @keydown.enter.prevent="store.select(l.id)"
         @keydown.space.prevent="store.select(l.id)">
      <canvas :ref="(c) => setThumb(l.id, c as HTMLCanvasElement | null)" class="el-thumb" width="80" height="80"
              :data-icon="l.icon" aria-hidden="true"></canvas>
      <div class="el-grow">
        <div class="el-name">{{ l.name }}</div>
        <div class="el-meta"><span class="el-dot" :class="dotClass(l)"></span>{{ status(l) }}</div>
      </div>
      <input v-if="!l.fixed" type="checkbox" role="switch" class="el-switch"
             :aria-label="`${l.name} in the model`" :title="switchTitle(l)"
             :checked="on(l)" :disabled="blocked(l)" @click.stop @change="toggle(l, $event)">
    </div>
    <div class="el-spacer"></div>
    <div class="el-hint">A layer loads when switched on; its dot turns green when it is ready.</div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue';
import { EDIT_LAYERS, setPreviewVisible, useEditLayersStore, type EditLayer, type EditLayerId } from '../../stores/editLayers';
import { useUiModeStore } from '../../stores/uiMode';
import { useAppStore } from '../../stores/app';
import { val } from '../../dom-fields';
import { bboxDiagonalKm } from '../../../modules/export/print-scale.js';

const store = useEditLayersStore();
const ui = useUiModeStore();
const app = useAppStore();
const w = () => window as any;

const available = computed(() => EDIT_LAYERS.filter((l) => !l.tool || ui.shows(l.tool)));
// Every layer is listed from the start (user 2026-10-05: no "+ Add layer" to unfold).
const shown = available;

function on(l: EditLayer): boolean {
  void store.tick;
  return l.inModel();
}

const cityTooLarge = computed(() => {
  const b = app.selectedRegion as { north: number; south: number; east: number; west: number } | null;
  return !!b && bboxDiagonalKm(b) > (w().CITY_COARSE_MAX_DIAG_KM ?? 25);
});
function blocked(l: EditLayer): boolean {
  return (l.id === 'city' || l.id === 'trails') && cityTooLarge.value;
}
function switchTitle(l: EditLayer): string {
  if (blocked(l)) return 'City data covers areas up to 25 km across';
  if (l.id === 'landcover') return 'Show land cover on the map (preview only: not carved into the model yet)';
  return `Put ${l.name.toLowerCase()} in the model and show it here`;
}

function hasPicture(l: EditLayer): boolean {
  return l.pictures().some((c) => !!c && c.width > 0 && c.height > 0);
}
/** The hydrology result is in, and no river of the chosen size crosses this box. */
function noRivers(): boolean {
  const h = w().appState?.lastWaterHydrology?.hydroData;
  return !!h && h.feature_count === 0;
}
function dotClass(l: EditLayer): string {
  if (l.id === 'terrain') return app.lastDemData ? 'ok' : 'off';
  if (!on(l)) return 'off';
  if (l.id === 'water' && noRivers()) return 'busy';
  return hasPicture(l) ? 'ok' : 'busy';
}

function status(l: EditLayer): string {
  void store.tick;
  const dem = app.lastDemData as { vmin?: number; vmax?: number } | null;
  switch (l.id) {
    case 'terrain':
      return dem ? `${Math.round(dem.vmin ?? 0)} – ${Math.round(dem.vmax ?? 0)} m` : 'Not loaded yet';
    case 'water': {
      if (!on(l)) return 'Not in the model';
      if (!app.lastDemData) return 'Waiting for the terrain';
      if (noRivers()) return 'No rivers this size here';
      const src = val('hydroSource') === 'natural_earth' ? 'Natural Earth' : 'HydroRIVERS';
      return `${src} · carved in`;
    }
    case 'city': {
      if (cityTooLarge.value) return 'Area too large for city data';
      if (!on(l)) return 'Not in the model';
      const n = w().appState?.osmCityData?.buildings?.features?.length;
      return Number.isFinite(n) ? `${n.toLocaleString()} buildings · 3D in print` : 'OpenStreetMap · 3D in print';
    }
    case 'satellite':
      return on(l) ? 'Colours the 3D model' : 'Off';
    case 'trails':
      return blocked(l) ? 'Area too large for trail data' : on(l) ? 'Raised in the model' : 'Not in the model';
    case 'landcover':
      return on(l) ? 'Preview only' : 'Off';
    case 'mesh':
      return on(l) ? 'Shown on the map' : 'Off';
    case 'borders': {
      if (!on(l)) return 'Off';
      const c = (window as any).appState?.lastBordersData?.counts;
      return c ? `Map only · ${c.countries ?? 0} country, ${c.states ?? 0} state lines` : 'Map only';
    }
  }
  return '';
}

function toggle(l: EditLayer, e: Event) {
  const want = (e.target as HTMLInputElement).checked;
  l.setInModel(want);
  // The canvas shows what prints (land cover / mesh: the switch is the preview itself).
  setPreviewVisible(l.stack, want);
  if (want) store.select(l.id);
  store.bump();
}

// ── Thumbnails: a scaled copy of each layer's own canvas (the real renderer) ──
const thumbs = new Map<EditLayerId, HTMLCanvasElement>();
function setThumb(id: EditLayerId, c: HTMLCanvasElement | null) {
  if (c) thumbs.set(id, c); else thumbs.delete(id);
}
function paintThumbs() {
  for (const l of EDIT_LAYERS) {
    const t = thumbs.get(l.id);
    if (!t) continue;
    const ctx = t.getContext('2d');
    if (!ctx) continue;
    const src = l.pictures().find((c) => !!c && c.width > 0 && c.height > 0);
    ctx.fillStyle = '#2c2c2e';
    ctx.fillRect(0, 0, t.width, t.height);
    if (src) {
      // Centre crop, so a long region still fills the tile.
      const s = Math.min(src.width, src.height);
      ctx.drawImage(src, (src.width - s) / 2, (src.height - s) / 2, s, s, 0, 0, t.width, t.height);
    } else {
      ctx.font = '34px system-ui, "Segoe UI Emoji", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(l.icon, t.width / 2, t.height / 2 + 2);
    }
  }
}

// Loads finish asynchronously and the modules don't all announce it, so the panel also
// refreshes on a slow timer while the Edit view is open.
let timer: number | undefined;
const bump = () => { store.bump(); requestAnimationFrame(paintThumbs); };
// What prints is what shows (§1.6): the canvas's layer visibility is not saved with the
// region, so when a terrain arrives, show the preview of every layer that is in the print
// (switching a preview on also fetches it, stacked-layers.js LAYER_AUTOLOAD).
function showPrintedPreviews() {
  for (const l of EDIT_LAYERS) {
    if (!l.fixed && l.inModel()) setPreviewVisible(l.stack, true);
  }
  bump();
}
// The store, not the event bus: Vue mounts before modules/core/events.js has run.
watch(() => app.lastDemData, (d) => { if (d) showPrintedPreviews(); });
onMounted(() => {
  window.addEventListener('layer-stack-changed', bump);
  document.addEventListener('change', bump, true);
  timer = window.setInterval(() => {
    if (!document.getElementById('demContainer')?.classList.contains('hidden')) bump();
  }, 1500);
  bump();
});
onBeforeUnmount(() => {
  window.removeEventListener('layer-stack-changed', bump);
  document.removeEventListener('change', bump, true);
  if (timer) window.clearInterval(timer);
});
</script>

<style scoped>
.el-panel {
  width: 260px; flex: none; display: flex; flex-direction: column; gap: 4px;
  background: #1c1c1e; border-radius: 18px; padding: 14px 10px; margin: 0 12px 0 0;
  overflow-y: auto; min-height: 0;
}
.el-cap {
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-muted); margin: 0 6px 6px;
}
.el-row {
  display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 12px; cursor: pointer;
}
.el-row:hover { background: #232326; }
.el-row.sel { background: #2c2c2e; outline: 2px solid #0a84ff; outline-offset: -2px; }
.el-row:focus-visible { outline: 2px solid #0a84ff; outline-offset: -2px; }
.el-thumb { width: 40px; height: 40px; border-radius: 10px; flex: none; }
.el-grow { flex: 1; min-width: 0; }
.el-name { font-weight: 600; font-size: 13px; color: #f5f5f7; }
.el-meta {
  font-size: 12px; color: var(--text-muted); display: flex; align-items: center; gap: 5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.el-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: #3a3a3c; }
.el-dot.ok { background: #30d158; }
.el-dot.busy { background: #ff9f0a; }
.el-switch {
  appearance: none; -webkit-appearance: none; flex: none; position: relative; cursor: pointer;
  width: 40px; height: 24px; border-radius: 999px; background: #3a3a3c; margin: 0; transition: background .15s;
}
.el-switch::after {
  content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
  border-radius: 50%; background: #fff; transition: left .15s;
}
.el-switch:checked { background: #30d158; }
.el-switch:checked::after { left: 18px; }
.el-switch:disabled { opacity: 0.4; cursor: not-allowed; }
.el-switch:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
.el-spacer { flex: 1; }
.el-hint { font-size: 12px; color: var(--text-muted); padding: 0 8px; line-height: 1.4; }
</style>

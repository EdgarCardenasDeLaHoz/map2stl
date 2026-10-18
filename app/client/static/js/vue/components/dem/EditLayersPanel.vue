<template>
  <!-- Edit page, left: the layers of the print, each a picture from its own canvas, a status
       line and a switch (design guidelines §2 Designer; mockup claude/mockups/2026-10-01/edit.html).
       Clicking a row selects it; its settings show on the right (LayerSettings.vue). Definitions:
       stores/editLayers.ts. Since 2026-10-14 the rail also owns the map's layer order (top of the
       list is drawn on top: drag a row, or Alt+Up/Down), each layer's opacity on the map (slider on
       the selected row) and Terrain's map-only visibility (mockup
       claude/mockups/2026-10-14-layers-rail/rail.png). Saved per region (presets.js view.layers). -->
  <aside id="editLayersPanel" class="el-panel" :class="{ 'el-collapsed': collapsed }" aria-label="Layers">
    <!-- Collapsed (◀ Hide, like the settings panel's): only the reopen tab is left. Saved per viewer. -->
    <button v-if="collapsed" id="layersCollapsedTab" type="button" class="el-tab"
            title="Show the layers" aria-label="Show the layers" @click="setCollapsed(false)">▶ Layers</button>
    <template v-else>
    <div class="el-cap"><span>Layers</span>
      <button id="layersHideBtn" type="button" class="el-hide" title="Hide the layers list"
              @click="setCollapsed(true)">◀ Hide</button></div>
    <div v-for="l in rows" :key="l.id" class="el-row" role="button" tabindex="0"
         :class="{ sel: store.selected === l.id, 'el-canvas': l.id === 'canvas', drag: drag.id === l.id && drag.active,
                   'ins-above': insAbove === l.id, 'ins-below': insBelow === l.id }"
         :style="drag.id === l.id && drag.active ? { transform: `translateY(${drag.dy}px)` } : undefined"
         :aria-pressed="store.selected === l.id"
         :aria-label="`${l.name}: ${status(l)}. Show its settings${l.stack ? '. Alt+arrow keys move it' : ''}`"
         :data-layer="l.id" @click="onRowClick(l)" @keydown="onRowKey(l, $event)"
         @pointerdown="onPointerDown(l, $event)">
      <div class="el-head">
        <span class="el-grip" :class="{ 'el-grip-none': !l.stack }" aria-hidden="true">{{ l.stack ? '⋮⋮' : '' }}</span>
        <canvas :ref="(c) => setThumb(l.id, c as HTMLCanvasElement | null)" class="el-thumb" width="80" height="80"
                :data-icon="l.icon" aria-hidden="true"></canvas>
        <div class="el-grow">
          <div class="el-name">{{ l.name }}</div>
          <div class="el-meta" :title="status(l)"><span class="el-dot" :class="dotClass(l)"></span>{{ status(l) }}</div>
        </div>
        <input v-if="l.stack" type="checkbox" role="switch" class="el-switch"
               :aria-label="l.id === 'terrain' ? 'Show the terrain on the map' : `${l.name} in the model`"
               :title="switchTitle(l)"
               :checked="on(l)" :disabled="blocked(l)" @click.stop @change="toggle(l, $event)">
      </div>
      <div v-if="store.selected === l.id && l.stack" class="el-op" @click.stop @pointerdown.stop>
        <span>On the map</span>
        <input type="range" class="el-range" min="0" max="100" step="5" :value="opacityPct(l)"
               :aria-label="`${l.name} opacity on the map`"
               @input="setOpacity(l, ($event.target as HTMLInputElement).value)" @change="save()">
        <span class="el-pct">{{ opacityPct(l) }}%</span>
      </div>
    </div>
    <div class="el-spacer"></div>
    <div class="el-hint">Top of the list is drawn on top. Drag a layer to move it; switch it off to hide it.
      A layer loads when switched on; its dot turns green when it is ready.</div>
    </template>
    <div class="el-live" aria-live="polite">{{ announce }}</div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import {
  EDIT_LAYERS, isPreviewVisible, setPreviewVisible, useEditLayersStore, type EditLayer, type EditLayerId,
} from '../../stores/editLayers';
import { moveRow, stackToRailOrder } from '../../../modules/layers/layer-stack-state.js';
import { useUiModeStore } from '../../stores/uiMode';
import { useAppStore } from '../../stores/app';
import { val } from '../../dom-fields';
import { bboxDiagonalKm } from '../../../modules/export/print-scale.js';
import { riverLoadingMessage } from '../../../modules/layers/hydrology-print.js';

const store = useEditLayersStore();
const ui = useUiModeStore();
const app = useAppStore();
const w = () => window as any;

const available = computed(() => EDIT_LAYERS.filter((l) => !l.tool || ui.shows(l.tool)));
/** The rows that can move: every listed layer but Canvas, by their stacked-layers.js key. */
const railStacks = computed(() => available.value.filter((l) => l.stack).map((l) => l.stack));
/**
 * Canvas first, then the layers in draw order, top of the list on top of the map. Every layer
 * is listed from the start (user 2026-10-05: no "+ Add layer" to unfold).
 */
const rows = computed(() => {
  void store.tick;
  const order: string[] = w().getLayerOrder?.() ?? [];
  const byStack = new Map(available.value.filter((l) => l.stack).map((l) => [l.stack, l]));
  const rail = stackToRailOrder(order, railStacks.value)
    .map((st: string) => byStack.get(st))
    .filter((l): l is EditLayer => !!l);
  return [...available.value.filter((l) => !l.stack), ...rail];
});

/** Terrain's switch is the map only (it always prints); the others put the layer in the model. */
function on(l: EditLayer): boolean {
  void store.tick;
  return l.id === 'terrain' ? isPreviewVisible('Dem') : l.inModel();
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
  if (l.id === 'terrain') return 'Show the terrain on the map (it is always in the print)';
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
/**
 * The rivers are being fetched: the hydrology preview (water-hydrology-combined.js) or the
 * composite's carve (composite-dem.js::_fetchHydro). A large region takes about a minute.
 */
function riversLoading(): boolean {
  const s = app.layerStatus;
  return s.waterHydrology === 'loading' || s.riverCarve === 'loading';
}
function dotClass(l: EditLayer): string {
  if (l.id === 'terrain' && !on(l)) return 'off';
  if (l.id === 'terrain' || l.id === 'canvas') return app.lastDemData ? 'ok' : 'off';
  if (!on(l)) return 'off';
  if (l.id === 'water' && riversLoading()) return 'busy';
  if (l.id === 'water' && noRivers()) return 'busy';
  return hasPicture(l) ? 'ok' : 'busy';
}

/** Short projection names for Canvas's status line (the select's labels are long). */
const PROJ_NAMES: Record<string, string> = {
  none: 'No projection', cosine: 'Cosine', mercator: 'Mercator', equidistant: 'Equidistant',
  lambert: 'Lambert', miller: 'Miller', gall: 'Gall', sinusoidal: 'Sinusoidal',
  laea: 'Lambert azimuthal', lcc: 'Lambert conic',
};

function status(l: EditLayer): string {
  void store.tick;
  const dem = app.lastDemData as { vmin?: number; vmax?: number } | null;
  switch (l.id) {
    case 'canvas':
      return `${PROJ_NAMES[val('paramProjection') || 'none'] || val('paramProjection')} · ${val('paramDim') || '?'} px`;
    case 'terrain':
      if (!on(l)) return 'Hidden · still prints';
      return dem ? `${Math.round(dem.vmin ?? 0)} – ${Math.round(dem.vmax ?? 0)} m` : 'Not loaded yet';
    case 'water': {
      if (!on(l)) return 'Not in the model';
      if (!app.lastDemData) return 'Waiting for the terrain';
      if (riversLoading()) {
        return riverLoadingMessage(app.selectedRegion as any, parseInt(val('hydroMinOrder'), 10));
      }
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
  // The canvas shows what prints (land cover / mesh: the switch is the preview itself;
  // Terrain: the map only, its setInModel does nothing).
  setPreviewVisible(l.stack, want);
  if (want) store.select(l.id);
  store.bump();
  save();
}

/** Save the region's settings (the rail is outside the panels presets.js watches). */
function save() {
  w().scheduleSettingsAutoSave?.();
}

// ── ◀ Hide: collapse the list to a reopen tab (like the settings panel's), saved per viewer ──
const COLLAPSED_KEY = 'map2stl_editLayersCollapsed';
function readCollapsed(): boolean {
  try { return localStorage.getItem(COLLAPSED_KEY) === '1'; } catch { return false; }
}
const collapsed = ref(readCollapsed());
function setCollapsed(c: boolean) {
  collapsed.value = c;
  try { localStorage.setItem(COLLAPSED_KEY, c ? '1' : '0'); } catch { /* private window */ }
  // The map gains or loses the panel's width: re-measure it as toggleSettingsPanel does
  // (view-management.js), after Vue has redrawn the panel.
  requestAnimationFrame(() => {
    w().events?.emit(w().EV?.STACKED_UPDATE);
    w().emitStackUpdate?.();
    window.dispatchEvent(new Event('resize'));
    requestAnimationFrame(() => w()._ensureDemViewportSpace?.());
  });
}

// ── Opacity on the map: the selected row's slider ──
function opacityPct(l: EditLayer): number {
  void store.tick;
  const o = Number(w().getLayerOpacities?.()?.[l.stack]);
  return Math.round((Number.isFinite(o) ? o : 1) * 100);
}
function setOpacity(l: EditLayer, v: string) {
  w().setLayerOpacity?.(l.stack, Number(v) / 100);
  store.bump();
}

// ── Order: drag a row, or Alt+Up/Down (top of the list is drawn on top) ──
const announce = ref('');
function railIds(): EditLayerId[] {
  return rows.value.filter((l) => l.stack).map((l) => l.id);
}
function moveTo(l: EditLayer, to: number) {
  const order = w().getLayerOrder?.();
  if (!Array.isArray(order)) return;
  const ids = railIds();
  const from = ids.indexOf(l.id);
  const t = Math.max(0, Math.min(ids.length - 1, to));
  if (from < 0 || t === from) return;
  w().setLayerOrder?.(moveRow(order, railStacks.value, l.stack, t));
  announce.value = `${l.name} moved to ${t + 1} of ${ids.length}${t === 0 ? ', drawn on top' : ''}`;
  store.bump();
  save();
}

function onRowKey(l: EditLayer, e: KeyboardEvent) {
  if (e.target !== e.currentTarget) return;   // keys on the switch or the slider are theirs
  if ((e.key === 'Enter' || e.key === ' ') && !e.altKey) {
    e.preventDefault();
    store.select(l.id);
  } else if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && l.stack) {
    e.preventDefault();
    moveTo(l, railIds().indexOf(l.id) + (e.key === 'ArrowUp' ? -1 : 1));
  }
}

const drag = reactive({ id: '' as EditLayerId | '', active: false, startY: 0, dy: 0, to: -1 });
let suppressClick = false;
const DRAG_THRESHOLD = 5;

/** The row the blue insertion line sits on: above row `to`, or below the last row. */
const insAbove = computed(() => {
  if (!drag.active) return '';
  const others = railIds().filter((id) => id !== drag.id);
  return drag.to < others.length ? others[drag.to] : '';
});
const insBelow = computed(() => {
  if (!drag.active) return '';
  const others = railIds().filter((id) => id !== drag.id);
  return drag.to >= others.length ? others[others.length - 1] : '';
});

function onPointerDown(l: EditLayer, e: PointerEvent) {
  if (!l.stack || e.button !== 0) return;
  if ((e.target as HTMLElement).closest('input')) return;
  drag.id = l.id;
  drag.active = false;
  drag.startY = e.clientY;
  drag.dy = 0;
  drag.to = -1;
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  window.addEventListener('pointercancel', onPointerCancel);
}
function onPointerMove(e: PointerEvent) {
  const dy = e.clientY - drag.startY;
  if (!drag.active && Math.abs(dy) < DRAG_THRESHOLD) return;
  drag.active = true;
  drag.dy = dy;
  // Drop index among the other rows: how many of their middles are above the pointer.
  const others = railIds().filter((id) => id !== drag.id);
  let to = 0;
  for (const id of others) {
    const r = document.querySelector(`#editLayersPanel .el-row[data-layer="${id}"]`)?.getBoundingClientRect();
    if (r && r.top + r.height / 2 < e.clientY) to++;
  }
  drag.to = to;
}
function endDrag() {
  window.removeEventListener('pointermove', onPointerMove);
  window.removeEventListener('pointerup', onPointerUp);
  window.removeEventListener('pointercancel', onPointerCancel);
}
function onPointerUp() {
  endDrag();
  if (drag.active) {
    const l = EDIT_LAYERS.find((x) => x.id === drag.id);
    if (l && drag.to >= 0) moveTo(l, drag.to);
    // The pointerup is followed by a click on the row: it must not count as selecting it.
    suppressClick = true;
    window.setTimeout(() => { suppressClick = false; }, 0);
  }
  drag.id = '';
  drag.active = false;
}
function onPointerCancel() {
  endDrag();
  drag.id = '';
  drag.active = false;
}
function onRowClick(l: EditLayer) {
  if (suppressClick) return;
  store.select(l.id);
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
      ctx.fillStyle = '#a1a1a6';   // a plain glyph (Canvas's ▦) would otherwise draw in the tile colour
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
  endDrag();
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
  color: var(--text-muted); margin: 0 6px 6px; display: flex; align-items: center; justify-content: space-between;
}
.el-hide {
  background: none; border: 1px solid transparent; border-radius: 6px; color: var(--text-muted);
  font-size: 12px; text-transform: none; letter-spacing: 0; padding: 2px 8px; cursor: pointer;
}
.el-hide:hover { color: #f5f5f7; background: #2c2c2e; border-color: #38383a; }
.el-hide:focus-visible, .el-tab:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
/* Collapsed: a narrow strip holding the vertical reopen tab (as .settings-collapsed-tab). */
.el-panel.el-collapsed { width: auto; padding: 0; background: none; overflow: visible; }
.el-tab {
  writing-mode: vertical-rl; flex: 1; min-height: 120px; padding: 16px 10px; border: none; border-radius: 12px;
  background: #007acc; color: #fff; font-size: 12px; font-weight: 600; letter-spacing: 0.04em; cursor: pointer;
}
.el-tab:hover { background: #0088e0; }
.el-row {
  display: flex; flex-direction: column; padding: 8px 8px 8px 4px; border-radius: 12px; cursor: pointer;
  position: relative; user-select: none; touch-action: none;
}
.el-head { display: flex; align-items: center; gap: 8px; }
.el-row:hover { background: #232326; }
/* The grip shows where a row can be dragged: on hover, selection and keyboard focus. */
.el-grip {
  width: 10px; flex: none; text-align: center; font-size: 12px; letter-spacing: -2px; color: #a1a1a6;
  visibility: hidden;
}
.el-row:hover .el-grip, .el-row.sel .el-grip, .el-row:focus-visible .el-grip, .el-row.drag .el-grip { visibility: visible; }
.el-row .el-grip.el-grip-none { visibility: hidden; }
.el-row.drag { z-index: 2; background: #3a3a3c; box-shadow: 0 10px 24px rgba(0, 0, 0, 0.55); cursor: grabbing; }
.el-row.ins-above::before, .el-row.ins-below::after {
  content: ""; position: absolute; left: 8px; right: 8px; height: 3px; border-radius: 2px; background: #0a84ff;
}
.el-row.ins-above::before { top: -3px; }
.el-row.ins-below::after { bottom: -3px; }
.el-op {
  display: flex; align-items: center; gap: 8px; margin: 8px 0 0 58px; font-size: 12px;
  color: var(--text-muted); cursor: default;
}
.el-range { flex: 1; min-width: 0; accent-color: #0a84ff; margin: 0; }
.el-pct { width: 34px; text-align: right; color: #f5f5f7; font-variant-numeric: tabular-nums; }
.el-live { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.el-row.el-canvas { margin-bottom: 6px; }
.el-row.el-canvas::after {
  content: ""; position: absolute; left: 8px; right: 8px; bottom: -4px; height: 1px; background: #38383a;
}
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

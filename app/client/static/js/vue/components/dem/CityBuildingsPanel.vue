<template>
  <div
    id="cityTablePanelResizeHandle"
    :class="['city-table-resize-handle', resizing && 'dragging', collapsed && 'hidden']"
    title="Drag to resize buildings table panel"
    @mousedown="startResize"
  ></div>

  <div :class="['city-table-panel', collapsed && 'city-table-panel-collapsed']" id="cityTablePanel">
    <div class="city-table-panel-header">
      <div class="city-table-panel-title">🏙 Buildings Table</div>
      <button class="city-table-panel-toggle" type="button" @click="hidePanel" title="Hide buildings table panel">◀</button>
    </div>

    <div class="city-table-panel-body">
      <div class="city-table-toolbar">
        <input
          v-model="searchText"
          type="text"
          class="search-input city-table-search"
          placeholder="Search buildings..."
          aria-label="Search buildings"
        >
        <div class="city-table-meta">{{ filteredRows.length }} buildings</div>
      </div>

      <div class="city-table-wrapper">
        <table class="city-table-view" id="cityBuildingsTable">
          <thead>
            <tr>
              <th>Building</th>
              <th>H</th>
              <th>Levels</th>
              <th>Source</th>
              <th>Type</th>
              <th>Centroid</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="pagedRows.length === 0">
              <td colspan="6" class="city-table-empty">No buildings match this filter.</td>
            </tr>
            <tr
              v-for="row in pagedRows"
              :key="row.index"
              :data-building-index="row.index"
              :class="{ selected: selectedIndex === row.index }"
              @click="selectBuilding(row.index)"
            >
              <td>
                <div class="city-building-name">{{ row.label }}</div>
                <div class="city-building-sub">#{{ row.index + 1 }}</div>
              </td>
              <td>{{ row.heightText }}</td>
              <td>{{ row.levelsText }}</td>
              <td>{{ row.sourceText }}</td>
              <td>{{ row.geometryText }}</td>
              <td>{{ row.centroidText }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="city-table-pagination">
        <button class="btn btn-secondary city-table-page-btn" :disabled="currentPage === 0" @click="currentPage = Math.max(0, currentPage - 1)">Prev</button>
        <span class="city-table-page-label">{{ pageLabel }}</span>
        <button class="btn btn-secondary city-table-page-btn" :disabled="currentPage >= totalPages - 1" @click="currentPage = Math.min(totalPages - 1, currentPage + 1)">Next</button>
      </div>
    </div>
  </div>

  <button
    id="cityTableCollapsedTab"
    class="city-table-collapsed-tab"
    type="button"
    :class="{ visible: collapsed && tableAvailable }"
    title="Open buildings table panel"
    @click="showPanel"
  >
    📋 Buildings
  </button>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

type BuildingRow = {
  index: number;
  label: string;
  heightText: string;
  levelsText: string;
  sourceText: string;
  geometryText: string;
  centroidText: string;
};

const collapsed = ref(true);
const resizing = ref(false);
const tableAvailable = ref(true);
const searchText = ref('');
const currentPage = ref(0);
const selectedIndex = ref<number | null>(null);
const pageSize = 20;
const buildingRows = ref<BuildingRow[]>([]);
const panelWidth = ref(420);

const PANEL_MIN_WIDTH = 260;
const PANEL_MAX_WIDTH = 900;
const PANEL_WIDTH_STORAGE_KEY = 'strm2stl_cityTablePanelWidth';

let _resizeStartX = 0;
let _resizeStartW = panelWidth.value;
let _rafPending = false;
let _onResizeMove: ((e: MouseEvent) => void) | null = null;
let _onResizeUp: (() => void) | null = null;

function _toFiniteNumber(val: unknown): number | null {
  const num = typeof val === 'number' ? val : Number(val);
  return Number.isFinite(num) ? num : null;
}

function _geomCentroid(geom: any): [number, number] | null {
  const coords: number[][] = [];
  function collect(node: any) {
    if (!Array.isArray(node)) return;
    if (typeof node[0] === 'number' && typeof node[1] === 'number') {
      coords.push(node as number[]);
      return;
    }
    for (const child of node) collect(child);
  }
  collect(geom?.coordinates);
  if (!coords.length) return null;
  const lon = coords.reduce((sum, coord) => sum + coord[0], 0) / coords.length;
  const lat = coords.reduce((sum, coord) => sum + coord[1], 0) / coords.length;
  return [lon, lat];
}

function _buildRows() {
  const features = (window as any).appState?.osmCityData?.buildings?.features || [];
  buildingRows.value = features.map((feat: any, index: number) => {
    const props = feat?.properties || {};
    const centroid = _geomCentroid(feat?.geometry);
    const height = _toFiniteNumber(props.height_m);
    const label = String(props.name || props.building || props['roof:shape'] || `Building ${index + 1}`);
    return {
      index,
      label,
      heightText: height != null ? `${height.toFixed(1)} m` : '—',
      levelsText: props['building:levels'] != null ? String(props['building:levels']) : '—',
      sourceText: String(props.height_source || '—'),
      geometryText: String(feat?.geometry?.type || '—'),
      centroidText: centroid ? `${centroid[1].toFixed(5)}, ${centroid[0].toFixed(5)}` : '—',
    };
  });
}

const filteredRows = computed(() => {
  const q = searchText.value.trim().toLowerCase();
  if (!q) return buildingRows.value;
  return buildingRows.value.filter((row) =>
    [row.label, row.heightText, row.levelsText, row.sourceText, row.geometryText, row.centroidText]
      .join(' ')
      .toLowerCase()
      .includes(q)
  );
});

const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / pageSize)));
const pagedRows = computed(() => {
  const safePage = Math.min(Math.max(currentPage.value, 0), totalPages.value - 1);
  const start = safePage * pageSize;
  return filteredRows.value.slice(start, start + pageSize);
});
const pageLabel = computed(() => {
  if (!filteredRows.value.length) return '0 of 0';
  const start = Math.min(currentPage.value * pageSize + 1, filteredRows.value.length);
  const end = Math.min((currentPage.value + 1) * pageSize, filteredRows.value.length);
  return `${start}–${end} of ${filteredRows.value.length}`;
});

function _syncRowsFromState() {
  _buildRows();
  if (currentPage.value >= totalPages.value) currentPage.value = totalPages.value - 1;
}

async function _syncSelectedRow(index: number | null) {
  selectedIndex.value = typeof index === 'number' ? index : null;
  if (selectedIndex.value == null) return;
  const position = filteredRows.value.findIndex((row) => row.index === selectedIndex.value);
  if (position < 0) return;
  collapsed.value = false;
  currentPage.value = Math.floor(position / pageSize);
  await nextTick();
  const row = document.querySelector<HTMLElement>(`#cityTablePanel [data-building-index="${selectedIndex.value}"]`);
  row?.scrollIntoView({ block: 'nearest' });
}

function selectBuilding(index: number) {
  (window as any).selectCityBuilding?.(index);
}

function _emitPanelResizeEffects() {
  if (_rafPending) return;
  _rafPending = true;
  requestAnimationFrame(() => {
    (window as any).events?.emit?.((window as any).EV?.STACKED_UPDATE);
    (window as any).emitStackUpdate?.();
    (window as any)._globalMap?.invalidateSize?.();
    window.dispatchEvent(new Event('resize'));
    if ((window as any).appState?.lastDemData?.values?.length) {
      (window as any).recolorDEM?.();
    }
    _rafPending = false;
  });
}

function hidePanel() {
  collapsed.value = true;
}

function showPanel() {
  if (!_isTableOpenAllowed()) {
    collapsed.value = true;
    return;
  }
  collapsed.value = false;
}

function _isTableOpenAllowed() {
  if (!tableAvailable.value) return false;
  const region = (window as any).appState?.selectedRegion;
  const haversine = (window as any).appState?.haversineDiagKm;
  if (region && typeof haversine === 'function') {
    const diagKm = haversine(region.north, region.south, region.east, region.west);
    if (diagKm > 10) return false;
  }
  return true;
}

function stopResize() {
  if (!resizing.value) return;
  resizing.value = false;
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
  try {
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(panelWidth.value));
  } catch (_) {
    // Ignore storage failures (private mode, quota, etc.)
  }
  _emitPanelResizeEffects();
}

function startResize(e: MouseEvent) {
  if (collapsed.value) return;
  _resizeStartX = e.clientX;
  _resizeStartW = panelWidth.value;
  resizing.value = true;
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
  e.preventDefault();
}

function _emitPanelState() {
  window.dispatchEvent(new CustomEvent('city-buildings-panel-state', {
    detail: { collapsed: collapsed.value, available: tableAvailable.value },
  }));
}

watch(
  () => (window as any).appState?.osmCityData,
  () => _syncRowsFromState(),
  { immediate: true }
);

watch(
  () => (window as any).appState?.selectedCityBuildingIndex,
  (index) => { void _syncSelectedRow(typeof index === 'number' ? index : null); },
  { immediate: true }
);

watch(searchText, () => {
  currentPage.value = 0;
});

watch(filteredRows, () => {
  if (currentPage.value >= totalPages.value) currentPage.value = totalPages.value - 1;
});

watch(collapsed, () => {
  _emitPanelState();
  _emitPanelResizeEffects();
});

onMounted(() => {
  try {
    const savedWidth = localStorage.getItem(PANEL_WIDTH_STORAGE_KEY);
    if (savedWidth) {
      const parsedWidth = parseInt(savedWidth, 10);
      if (Number.isFinite(parsedWidth)) {
        panelWidth.value = Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, parsedWidth));
      }
    }
  } catch (_) {
    // Ignore storage failures.
  }

  _onResizeMove = (e: MouseEvent) => {
    if (!resizing.value) return;
    panelWidth.value = Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, _resizeStartW + (_resizeStartX - e.clientX)));
    _emitPanelResizeEffects();
  };
  _onResizeUp = () => stopResize();
  document.addEventListener('mousemove', _onResizeMove);
  document.addEventListener('mouseup', _onResizeUp);

  (window as any).openCityBuildingsPanel = () => {
    showPanel();
    if (collapsed.value) return;
    const panel = document.getElementById('cityTablePanel');
    panel?.scrollIntoView({ block: 'nearest' });
  };
  (window as any).toggleCityBuildingsPanel = () => {
    if (!_isTableOpenAllowed()) {
      collapsed.value = true;
      return true;
    }
    collapsed.value = !collapsed.value;
    if (!collapsed.value) {
      const panel = document.getElementById('cityTablePanel');
      panel?.scrollIntoView({ block: 'nearest' });
    }
    return collapsed.value;
  };
  (window as any).hideCityBuildingsPanel = () => {
    collapsed.value = true;
  };
  (window as any).setCityBuildingsTableAvailable = (available: boolean) => {
    tableAvailable.value = !!available;
    if (!tableAvailable.value) collapsed.value = true;
    _emitPanelState();
  };
  (window as any).isCityBuildingsPanelCollapsed = () => collapsed.value;
  (window as any).isCityBuildingsPanelVisible = () => !collapsed.value && tableAvailable.value;
  (window as any).syncCityBuildingsTable = () => _syncRowsFromState();
  (window as any).syncSelectedCityBuilding = (index: number | null) => { void _syncSelectedRow(index); };
  void _syncRowsFromState();
  void _syncSelectedRow((window as any).appState?.selectedCityBuildingIndex ?? null);
  _emitPanelState();
});

onBeforeUnmount(() => {
  stopResize();
  if (_onResizeMove) {
    document.removeEventListener('mousemove', _onResizeMove);
    _onResizeMove = null;
  }
  if (_onResizeUp) {
    document.removeEventListener('mouseup', _onResizeUp);
    _onResizeUp = null;
  }
  if ((window as any).openCityBuildingsPanel) {
    delete (window as any).openCityBuildingsPanel;
  }
  if ((window as any).toggleCityBuildingsPanel) {
    delete (window as any).toggleCityBuildingsPanel;
  }
  if ((window as any).hideCityBuildingsPanel) {
    delete (window as any).hideCityBuildingsPanel;
  }
  if ((window as any).setCityBuildingsTableAvailable) {
    delete (window as any).setCityBuildingsTableAvailable;
  }
  if ((window as any).isCityBuildingsPanelCollapsed) {
    delete (window as any).isCityBuildingsPanelCollapsed;
  }
  if ((window as any).isCityBuildingsPanelVisible) {
    delete (window as any).isCityBuildingsPanelVisible;
  }
  if ((window as any).syncCityBuildingsTable === _syncRowsFromState) {
    delete (window as any).syncCityBuildingsTable;
  }
  if ((window as any).syncSelectedCityBuilding === _syncSelectedRow) {
    delete (window as any).syncSelectedCityBuilding;
  }
});
</script>

<style scoped>
.city-table-resize-handle {
  width: 5px;
  height: 100%;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
  position: relative;
  z-index: 10;
  transition: background 0.15s;
}

.city-table-resize-handle:hover,
.city-table-resize-handle.dragging {
  background: rgba(100, 160, 255, 0.25);
}

.city-table-resize-handle.hidden {
  width: 0;
  pointer-events: none;
}

.city-table-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  flex-shrink: 1;
  overflow: hidden;
  border-left: 1px solid var(--bg-light);
  width: v-bind('panelWidth + "px"');
  min-width: 260px;
  background: #1f1f1f;
}

.city-table-panel-collapsed {
  width: 0;
  min-width: 0;
  border-left: none;
}

.city-table-panel-collapsed .city-table-panel-header,
.city-table-panel-collapsed .city-table-panel-body {
  display: none;
}

.city-table-collapsed-tab {
  display: none;
  writing-mode: vertical-rl;
  text-orientation: mixed;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 10px 6px;
  background: #0f6f82;
  border: none;
  border-left: 1px solid var(--bg-light);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s;
  letter-spacing: 0.03em;
}

.city-table-collapsed-tab.visible {
  display: flex;
}

.city-table-collapsed-tab:hover {
  background: #1287a0;
}

.city-table-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-bottom: 1px solid var(--bg-light);
  background: #222;
}

.city-table-panel-title {
  font-size: 12px;
  color: #d2d2d2;
  font-weight: 600;
}

.city-table-panel-toggle {
  border: 1px solid #3a3a3a;
  background: #2b2b2b;
  color: #bbb;
  border-radius: 4px;
  font-size: 11px;
  padding: 2px 6px;
  cursor: pointer;
}

.city-table-panel-body {
  width: 100%;
  flex: 1;
  height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 10px 12px;
  box-sizing: border-box;
}

.city-table-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px;
}

.city-table-search {
  flex: 1;
  min-width: 0;
}

.city-table-meta {
  font-size: 10px;
  color: #888;
  white-space: nowrap;
}

.city-table-wrapper {
  max-height: calc(100vh - 260px);
  overflow: auto;
  border: 1px solid #2a2a2a;
  border-radius: 4px;
}

.city-table-view {
  width: 100%;
  border-collapse: collapse;
  font-size: 10px;
}

.city-table-view thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #1d1d1d;
  color: #aaa;
  text-align: left;
  padding: 5px 6px;
  border-bottom: 1px solid #2b2b2b;
  font-weight: 600;
}

.city-table-view tbody td {
  padding: 5px 6px;
  border-bottom: 1px solid #242424;
  color: #d5d5d5;
  vertical-align: top;
}

.city-table-view tbody tr:hover {
  background: rgba(255, 255, 255, 0.04);
  cursor: pointer;
}

.city-table-view tbody tr.selected {
  background: rgba(255, 210, 77, 0.14);
  box-shadow: inset 2px 0 0 #ffd24d;
}

.city-table-empty {
  text-align: center;
  color: #777;
  padding: 12px 6px;
}

.city-building-name {
  font-size: 10px;
  font-weight: 600;
  color: #eee;
  line-height: 1.2;
}

.city-building-sub {
  font-size: 9px;
  color: #7a7a7a;
  line-height: 1.2;
}

.city-table-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: 6px;
}

.city-table-page-btn {
  flex: 0 0 auto !important;
  min-width: 56px;
}

.city-table-page-label {
  font-size: 10px;
  color: #888;
  white-space: nowrap;
}
</style>
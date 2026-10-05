<template>
  <!-- Mirrors the original <div class="sidebar expanded" id="sidebar"> structure exactly.
       All child element IDs are preserved so existing JS (document.getElementById)
       continues to work during the migration. -->
  <div class="sidebar" :class="sidebarClass" id="sidebar">

    <!-- Header ─────────────────────────────────────────────────────────────── -->
    <div class="sidebar-header">
      <div class="sidebar-title">Region Selection</div>
      <div class="row-gap6 sidebar-header-actions">
        <button class="sidebar-header-action-btn"
                title="Export saved regions to JSON"
                @click="exportRegions">
          Export
        </button>
        <button class="sidebar-header-action-btn"
                title="Import regions from JSON"
                @click="openImportPicker">
          Import
        </button>
        <button class="sidebar-vis-btn" id="bboxVisToggleBtn"
                title="Show/hide region boxes on map"
                @click="toggleBboxVis">👁</button>
        <button class="sidebar-toggle-btn" id="sidebarToggleBtn"
                :title="`${widthLabel} the region panel`"
                :aria-label="`${widthLabel} the region panel`"
                @click="toggleWidth">
          <span class="state-icon">{{ widthIcon }}</span>
          <span class="state-label">{{ widthLabel }}</span>
        </button>
        <button class="sidebar-hide-btn" id="sidebarHideBtn"
                title="Hide the region panel"
                aria-label="Hide the region panel"
                @click="hideSidebar">✕</button>
      </div>
      <input
        ref="regionImportInput"
        class="hidden"
        type="file"
        accept="application/json,.json"
        @change="handleRegionImport"
      />
    </div>

    <!-- Content ─────────────────────────────────────────────────────────────── -->
    <div class="sidebar-content">

      <!-- Compact list (normal mode); the region editor replaces it while open -->
      <SidebarListView :visible="mode === 'normal' && !editViewOpen" />

      <!-- Region editor (opened by a row's ✎ button, region-editor.js) -->
      <SidebarEditView :visible="editViewOpen" @back="setRegionEditorOpen(false)" />

      <!-- Expanded table (expanded mode) -->
      <RegionListTable v-show="mode === 'expanded'" />

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount, onMounted } from 'vue';
import { useAppStore } from '../../stores/app';

import SidebarListView      from './SidebarListView.vue';
import SidebarEditView      from './SidebarEditView.vue';
import RegionListTable      from './RegionListTable.vue';

const store = useAppStore();

// Mirror Pinia sidebarMode → local ref for immediate reactivity
const mode       = computed(() => store.sidebarMode);
const editViewOpen = ref(false);
const regionImportInput = ref<HTMLInputElement | null>(null);

// `normal` was never emitted, which left the `.sidebar.normal` rules in
// app.css matching nothing.
const sidebarClass = computed(() => ({
  expanded: mode.value === 'expanded',
  normal: mode.value === 'normal',
  collapsed: mode.value === 'hidden',
}));

// The panel is on the left, so widening moves its right edge right.
const widthIcon  = computed(() => (mode.value === 'expanded' ? '«' : '»'));
const widthLabel = computed(() => (mode.value === 'expanded' ? 'Narrow' : 'Widen'));

// Restored by the floating "Regions" button, so hiding and reopening the panel
// does not quietly change its width.
let lastVisibleMode: 'expanded' | 'normal' = 'normal';

// One control for width and one for hiding. The single three-state cycle these
// replace ran expanded -> hidden -> normal -> expanded, so from the default
// width the button offering to change the panel made it wider, and hiding took
// two clicks through a state the user had not asked for.
function toggleWidth() {
  setSidebarMode(mode.value === 'expanded' ? 'normal' : 'expanded');
}

function hideSidebar() {
  setSidebarMode('hidden');
}

function setSidebarMode(newMode: 'expanded' | 'normal' | 'hidden') {
  if (newMode !== 'hidden') lastVisibleMode = newMode;
  store.sidebarMode = newMode;
  // Keep app.js closure in sync until Stage 7
  window.setSidebarState?.(newMode);
  window._setSidebarViews?.(newMode);

  const openBtn = document.getElementById('openSidebarBtn');
  if (openBtn) {
    openBtn.classList.toggle('hidden', newMode !== 'hidden');
  }

  // The width transition runs for 300ms. Relaying out only on the next frame
  // sizes the map to the width the panel is leaving, not the one it is going
  // to, so do it again once the transition has finished.
  const relayout = () => {
    window.getMap?.()?.invalidateSize?.();
    window.emitStackUpdate?.();
    window.dispatchEvent(new Event('resize'));
  };
  requestAnimationFrame(relayout);
  window.setTimeout(relayout, 340);
}

/**
 * Show or hide the region editor. The one way the modules open or close it
 * (region-editor.js opens it; switching views or sidebar width closes it), so
 * this component's state never disagrees with the DOM.
 */
function setRegionEditorOpen(open: boolean) {
  editViewOpen.value = open;
}

function handleOpenSidebarClick() {
  setSidebarMode(lastVisibleMode);
}

let _openSidebarButton: HTMLElement | null = null;

function toggleBboxVis() {
  window.toggleBboxLayerVisibility?.();
}

function exportRegions() {
  (window as any).exportRegionsJson?.();
}

function openImportPicker() {
  regionImportInput.value?.click();
}

async function handleRegionImport(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  await (window as any).importRegionsJsonFile?.(file);
  input.value = '';
}

onMounted(() => {
  // Start in normal mode so region selection stays compact by default.
  setSidebarMode('normal');

  // The one way for the non-Vue modules to change the mode. They used to set
  // `sidebar.classList` and the toggle button's text by hand, which left this
  // component's store stale - the panel then snapped back to whatever the store
  // still said on its next render.
  (window as any).setSidebarMode = setSidebarMode;
  (window as any).setRegionEditorOpen = setRegionEditorOpen;

  _openSidebarButton = document.getElementById('openSidebarBtn');
  _openSidebarButton?.addEventListener('click', handleOpenSidebarClick);
});

onBeforeUnmount(() => {
  _openSidebarButton?.removeEventListener('click', handleOpenSidebarClick);
  _openSidebarButton = null;
  if ((window as any).setSidebarMode === setSidebarMode) {
    delete (window as any).setSidebarMode;
  }
  if ((window as any).setRegionEditorOpen === setRegionEditorOpen) {
    delete (window as any).setRegionEditorOpen;
  }
});
</script>

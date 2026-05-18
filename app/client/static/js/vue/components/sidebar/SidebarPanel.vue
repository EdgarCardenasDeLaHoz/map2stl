<template>
  <!-- Mirrors the original <div class="sidebar expanded" id="sidebar"> structure exactly.
       All child element IDs are preserved so existing JS (document.getElementById)
       continues to work during the migration. -->
  <div class="sidebar" :class="sidebarClass" id="sidebar">

    <!-- Header ─────────────────────────────────────────────────────────────── -->
    <div class="sidebar-header">
      <div class="sidebar-title">Region Selection</div>
      <div class="row-gap6">
      <button class="sidebar-header-action-btn"
        title="Export saved regions to JSON"
        @click="exportRegions">
        Export
      </button>
      <button v-show="mode !== 'hidden'" class="sidebar-header-action-btn"
        title="Import regions from JSON"
        @click="openImportPicker">
        Import
      </button>
        <button class="sidebar-vis-btn" id="bboxVisToggleBtn"
                title="Show/hide region boxes on map">👁</button>
        <button class="sidebar-toggle-btn" id="sidebarToggleBtn"
                :title="`${stateLabel} sidebar (${stateDescription})`"
                :aria-label="`Toggle sidebar: currently ${mode} — ${stateLabel} to ${nextStateDescription}`"
                @click="cycleSidebar">
          <span class="state-icon">{{ stateIcon }}</span>
          <span class="state-label">{{ stateLabel }}</span>
        </button>
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

      <!-- Compact list (normal mode) -->
      <SidebarListView :visible="mode === 'normal'" />

      <!-- Compact edit view -->
      <SidebarEditView :visible="editViewOpen" @back="editViewOpen = false" />

      <!-- Expanded table (expanded mode) -->
      <RegionListTable v-if="mode === 'expanded'" />

      <!-- New region -->
      <NewRegionSection />

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount, onMounted } from 'vue';
import { useAppStore } from '../../stores/app';

import SidebarListView      from './SidebarListView.vue';
import SidebarEditView      from './SidebarEditView.vue';
import RegionListTable      from './RegionListTable.vue';
import NewRegionSection     from './NewRegionSection.vue';

const store = useAppStore();

// Mirror Pinia sidebarMode → local ref for immediate reactivity
const mode       = computed(() => store.sidebarMode);
const editViewOpen = ref(false);
const regionImportInput = ref<HTMLInputElement | null>(null);

const sidebarClass = computed(() => ({
  expanded: mode.value === 'expanded',
  collapsed: mode.value === 'hidden',
}));

const stateIcon  = computed(() => ({ expanded: '⇐', normal: '⇔', hidden: '⇒' }[mode.value]));
const stateLabel = computed(() => ({ expanded: 'Hide', normal: 'Expand', hidden: 'Show' }[mode.value]));
const stateDescription = computed(() => ({ expanded: 'Large', normal: 'Normal', hidden: 'Compact' }[mode.value]));
const nextStateDescription = computed(() => {
  const next = { expanded: 'Hidden', normal: 'Expanded', hidden: 'Normal' }[mode.value];
  return next;
});

function cycleSidebar() {
  const next: Record<string, 'expanded' | 'normal' | 'hidden'> = {
    expanded: 'hidden',
    hidden:   'normal',
    normal:   'expanded',
  };
  setSidebarMode(next[mode.value]);
}

function setSidebarMode(newMode: 'expanded' | 'normal' | 'hidden') {
  store.sidebarMode = newMode;
  // Keep app.js closure in sync until Stage 7
  window.setSidebarState?.(newMode);
  window._setSidebarViews?.(newMode);

  const openBtn = document.getElementById('openSidebarBtn');
  if (openBtn) {
    openBtn.classList.toggle('hidden', newMode !== 'hidden');
  }

  requestAnimationFrame(() => {
    (window as any)._globalMap?.invalidateSize?.();
    window.emitStackUpdate?.();
    window.dispatchEvent(new Event('resize'));
  });
}

function handleOpenSidebarClick() {
  setSidebarMode('normal');
}

let _openSidebarButton: HTMLElement | null = null;

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

  _openSidebarButton = document.getElementById('openSidebarBtn');
  _openSidebarButton?.addEventListener('click', handleOpenSidebarClick);
});

onBeforeUnmount(() => {
  _openSidebarButton?.removeEventListener('click', handleOpenSidebarClick);
  _openSidebarButton = null;
});
</script>

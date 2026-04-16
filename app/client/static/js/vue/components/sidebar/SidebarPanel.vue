<template>
  <!-- Mirrors the original <div class="sidebar expanded" id="sidebar"> structure exactly.
       All child element IDs are preserved so existing JS (document.getElementById)
       continues to work during the migration. -->
  <div class="sidebar" :class="sidebarClass" id="sidebar">

    <!-- Header ─────────────────────────────────────────────────────────────── -->
    <div class="sidebar-header">
      <div class="sidebar-title">Region Selection</div>
      <div class="row-gap6">
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
    </div>

    <!-- Content ─────────────────────────────────────────────────────────────── -->
    <div class="sidebar-content">

      <!-- Compact list (normal mode) -->
      <SidebarListView :visible="mode === 'normal'" />

      <!-- Compact edit view -->
      <SidebarEditView :visible="editViewOpen" @back="editViewOpen = false" />

      <!-- Expanded table (expanded mode) -->
      <RegionListTable v-if="mode === 'expanded'" />

      <!-- Region parameters (expanded mode) -->
      <RegionParamsSection :visible="mode === 'expanded'" />

      <!-- New region -->
      <NewRegionSection />

      <!-- Cache management -->
      <CacheManagement />

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useAppStore } from '../../stores/app';

import SidebarListView      from './SidebarListView.vue';
import SidebarEditView      from './SidebarEditView.vue';
import RegionListTable      from './RegionListTable.vue';
import RegionParamsSection  from './RegionParamsSection.vue';
import NewRegionSection     from './NewRegionSection.vue';
import CacheManagement      from './CacheManagement.vue';

const store = useAppStore();

// Mirror Pinia sidebarMode → local ref for immediate reactivity
const mode       = computed(() => store.sidebarMode);
const editViewOpen = ref(false);

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
  const newMode = next[mode.value];
  store.sidebarMode = newMode;
  // Keep app.js closure in sync until Stage 7
  window.setSidebarState?.(newMode);
  window._setSidebarViews?.(newMode);
}

onMounted(() => {
  // Start expanded — matches app.js DOMContentLoaded initialisation
  store.sidebarMode = 'expanded';
});
</script>

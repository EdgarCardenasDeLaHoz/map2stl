<template>
  <!-- The Explore page's left panel (F-UNIFY Phase 6): the region list, the wide table or the
       region editor. #sidebar and .sidebar stay: view-management.js hides the panel on the
       Edit/Extrude/tool pages through them, and app.css sizes the page around them. -->
  <div class="sidebar sbp" :class="sidebarClass" id="sidebar">
    <RegionEditor v-if="regions.editingName && mode !== 'expanded'" />
    <template v-else>
      <div class="sbp-head">
        <h2 class="sbp-title">{{ mode === 'expanded' ? 'All regions' : 'Regions' }}</h2>
        <RegionsMenu @wide="toggleWidth" @hide="hideSidebar" />
        <button id="bboxVisToggleBtn" type="button" class="sbp-ib" :class="{ on: regions.boxesVisible }"
                :aria-pressed="regions.boxesVisible"
                :title="regions.boxesVisible ? 'Hide region boxes on the map' : 'Show region boxes on the map'"
                :aria-label="regions.boxesVisible ? 'Hide region boxes on the map' : 'Show region boxes on the map'"
                @click="regions.boxesVisible = !regions.boxesVisible">{{ regions.boxesVisible ? '👁' : '🙈' }}</button>
        <button id="sidebarToggleBtn" type="button" class="sbp-ib" :class="{ on: mode === 'expanded' }"
                :title="`${widthLabel} the region panel`" :aria-label="`${widthLabel} the region panel`"
                @click="toggleWidth">⤢</button>
        <button id="sidebarHideBtn" type="button" class="sbp-ib" title="Hide the region panel"
                aria-label="Hide the region panel" @click="hideSidebar">✕</button>
      </div>
      <RegionTable v-if="mode === 'expanded'" />
      <RegionList v-else />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue';
import { useAppStore } from '../../stores/app';
import { useRegionsStore } from '../../stores/regions';
import RegionEditor from '../../explore/RegionEditor.vue';
import RegionList from '../../explore/RegionList.vue';
import RegionsMenu from '../../explore/RegionsMenu.vue';
import RegionTable from '../../explore/RegionTable.vue';

const app = useAppStore();
const regions = useRegionsStore();

const mode = computed(() => app.sidebarMode as 'expanded' | 'normal' | 'hidden');
const sidebarClass = computed(() => ({
  expanded: mode.value === 'expanded',
  normal: mode.value === 'normal',
  collapsed: mode.value === 'hidden',
}));
const widthLabel = computed(() => (mode.value === 'expanded' ? 'Narrow' : 'Widen'));

// Restored by the floating "Regions" button, so hiding and reopening the panel does not
// quietly change its width.
let lastVisibleMode: 'expanded' | 'normal' = 'normal';

function toggleWidth() { setSidebarMode(mode.value === 'expanded' ? 'normal' : 'expanded'); }
function hideSidebar() { setSidebarMode('hidden'); }

function setSidebarMode(newMode: 'expanded' | 'normal' | 'hidden') {
  if (newMode !== 'hidden') lastVisibleMode = newMode;
  app.sidebarMode = newMode;
  regions.tableOpen = newMode === 'expanded';
  window.setSidebarState?.(newMode);
  // The editor only lives in the narrow panel.
  if (newMode !== 'normal') regions.editingName = null;
  document.getElementById('openSidebarBtn')?.classList.toggle('hidden', newMode !== 'hidden');
  // The width transition runs for 300 ms: re-measure the map when it starts and when it ends.
  const relayout = () => {
    window.getMap?.()?.invalidateSize?.();
    window.emitStackUpdate?.();
    window.dispatchEvent(new Event('resize'));
  };
  requestAnimationFrame(relayout);
  window.setTimeout(relayout, 340);
}

function setRegionEditorOpen(open: boolean) {
  if (!open) regions.editingName = null;
  else if (regions.selectedName) regions.editingName = regions.selectedName;
}

function openRegionEditor(index: number) {
  const r = regions.regions[index];
  return r ? regions.openEditor(r.name) : Promise.resolve();
}

function handleOpenSidebarClick() { setSidebarMode(lastVisibleMode); }
let _openBtn: HTMLElement | null = null;

onMounted(() => {
  setSidebarMode('normal');
  // The one way for the non-Vue modules to change the panel (see view-management.js).
  const w = window as any;
  w.setSidebarMode = setSidebarMode;
  w.setRegionEditorOpen = setRegionEditorOpen;
  w.openRegionEditor = openRegionEditor;
  _openBtn = document.getElementById('openSidebarBtn');
  _openBtn?.addEventListener('click', handleOpenSidebarClick);
});

onBeforeUnmount(() => {
  _openBtn?.removeEventListener('click', handleOpenSidebarClick);
  const w = window as any;
  if (w.setSidebarMode === setSidebarMode) delete w.setSidebarMode;
  if (w.setRegionEditorOpen === setRegionEditorOpen) delete w.setRegionEditorOpen;
  if (w.openRegionEditor === openRegionEditor) delete w.openRegionEditor;
});
</script>

<style scoped>
.sbp {
  display: flex; flex-direction: column; background: var(--panel, #1c1c1e); color: var(--text, #f2f2f7);
  border-right: 1px solid var(--line, #3a3a3c); min-height: 0; padding: 0; overflow: visible;
}
.sbp.normal, .sbp:not(.expanded):not(.collapsed) { width: 300px; }
.sbp.expanded { width: 520px; }
.sbp.collapsed { width: 0; visibility: hidden; border-right: 0; }
.sbp-head { display: flex; align-items: center; gap: 6px; padding: 12px 14px 6px; }
.sbp-title { flex: 1; margin: 0; font-size: 15px; font-weight: 700; }
.sbp-ib {
  width: 28px; height: 28px; border-radius: 8px; border: 0; background: var(--card, #2c2c2e);
  color: var(--dim, #98989f); font-size: 13px; cursor: pointer;
}
.sbp-ib:hover { color: #fff; }
.sbp-ib.on { background: rgba(10, 132, 255, 0.25); color: #fff; }
@media (max-width: 1200px) {
  .sbp.normal, .sbp:not(.expanded):not(.collapsed) { width: 260px; }
  .sbp.expanded { width: 440px; }
}
</style>

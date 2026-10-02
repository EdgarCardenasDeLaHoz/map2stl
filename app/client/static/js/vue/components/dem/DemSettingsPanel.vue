<template>
  <!-- Settings resize handle + right panel -->
  <div id="settingsPanelResizeHandle" class="settings-resize-handle" title="Drag to resize settings panel"></div>
  <div class="dem-right-panel" id="demRightPanel">

    <!-- Tab strip — mirrors the Extrude panel's Fetch/View/Export pattern -->
    <!-- The full panels are ⚙ tools now (F-DESIGN): Data sources (Fetch), Display (View),
         Composite & imports (Composite), Settings as JSON. Beginner shows only the selected
         layer's settings (LayerProperties.vue) above. -->
    <div v-show="anyTool" class="dem-strip" id="demStrip">
      <button v-show="ui.shows('editData')" :class="['dem-strip-btn', tab==='fetch' && 'active']"
              @click="activeTab='fetch'"
              title="DEM source, projection, and per-layer fetch parameters">📥 Fetch</button>
      <div class="dem-strip-divider"></div>
      <button v-show="ui.shows('editLook')" :class="['dem-strip-btn', tab==='view' && 'active']"
              @click="activeTab='view'"
              title="Canvas display mode and per-layer visibility / opacity">👁 View</button>
      <button v-show="ui.shows('editModel')" :class="['dem-strip-btn', tab==='composite' && 'active']"
              @click="activeTab='composite'"
              title="Composite DEM and saved presets">⊕ Composite</button>
      <div style="flex:1"></div>
      <a class="dem-strip-btn guide-link" :href="guideLink" target="_blank" rel="noopener"
         title="Open the step-by-step guide for this part (new tab)">? Guide</a>
      <button class="dem-strip-btn" id="settingsHideBtn" title="Hide settings panel">◀ Hide</button>
      <button v-show="ui.shows('editJson')" class="dem-strip-btn" id="jsonViewToggleBtn" title="Toggle between form and JSON editor">{ } JSON</button>
    </div>

    <!-- Main scrollable settings area -->
    <div class="dem-controls" id="demControls">
      <div class="dem-controls-inner" id="demControlsInner">
        <!-- Landmarks near the box edge (a warning, so it shows in every mode). -->
        <EdgeLandmarkWarnings compact :max="3" />
        <LayerProperties />
        <div v-if="!anyTool" class="lp-more">Every other setting is a tool in
          <button type="button" class="lp-more-link" @click="openSettings">⚙ Settings</button>.</div>

        <!-- Primary actions — pinned at the top of the Fetch tab. Load DEM is the
             step everything else depends on, so it leads; its handler is wired
             by id in event-listeners-map.js (window.loadDEM). Source and
             resolution stay under Fetch Layers → DEM Source. -->
        <div v-show="tab==='fetch'" id="settingsSaveRow" class="settings-primary-row">
          <button id="loadDemBtn" class="btn btn-primary settings-load-dem-btn"
                  title="Fetch the DEM for the selected region with the source and resolution under Fetch Layers → DEM Source">🏔 Load DEM</button>
          <button id="clearRegionCacheBtn" class="btn btn-secondary settings-icon-btn"
                  aria-label="Clear region cache"
                  title="Clear all cached data (DEM, water, satellite, etc.) and re-fetch">🗑️</button>
        </div>
        <WorkflowPresetBar v-show="tab==='fetch'" />

        <!-- ═══════════ Fetch tab ═══════════ -->
        <div v-show="tab==='fetch'">
          <ProjectionSection />
          <FetchLayersSection />
          <CityLandmarksSection />
          <PresetsSection />
        </div>

        <!-- ═══════════ View tab ═══════════ -->
        <!-- IMPORTANT: Rendering section contains #curveCanvas — never use v-if here, only v-show -->
        <!-- Global chrome first, then one section per layer in render-stack
             order: DEM, land cover, city, trails. These used to interleave,
             with Canvas sitting between two per-layer blocks. -->
        <div v-show="tab==='view'">
          <LayerViewSection />
          <VisualizationSection />
          <LayerDisplaySections />
        </div>

        <!-- ═══════════ Composite tab ═══════════ -->
        <div v-show="tab==='composite'">
          <CompositeDemSection />
          <MeshImportSection />
          <PlateRegistrationSection />
        </div>

      </div><!-- /dem-controls-inner -->

      <!-- JSON settings editor (hidden by default, toggled by { } JSON button) -->
      <div id="settingsJsonView" class="settings-json-view hidden">
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;">Edit settings as JSON. Click Apply to update the form.</div>
        <textarea id="settingsJsonEditor" class="settings-json-editor" spellcheck="false" aria-label="Settings JSON editor"></textarea>
        <div id="settingsJsonError" class="settings-json-error hidden"></div>
        <div class="row-gap6" style="margin-top:6px;">
          <button id="applyJsonSettingsBtn" class="btn btn-primary" style="flex:1;font-size:11px;">✓ Apply</button>
          <button id="cancelJsonSettingsBtn" class="btn btn-secondary" style="font-size:11px;">✕ Cancel</button>
        </div>
      </div>
    </div><!-- /dem-controls -->

  </div><!-- /dem-right-panel -->
  <button id="settingsCollapsedTab" class="settings-collapsed-tab" title="Open settings panel">⚙ Settings</button>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue';
import { guideHref } from '../../../modules/ui/guide-links.js';
import VisualizationSection  from './VisualizationSection.vue';
import LayerViewSection      from './LayerViewSection.vue';
import LayerDisplaySections from './LayerDisplaySections.vue';
import ProjectionSection     from './ProjectionSection.vue';
import FetchLayersSection    from './FetchLayersSection.vue';
import CityLandmarksSection  from './CityLandmarksSection.vue';
import CompositeDemSection   from './CompositeDemSection.vue';
import MeshImportSection     from './MeshImportSection.vue';
import PlateRegistrationSection from './PlateRegistrationSection.vue';
import PresetsSection        from './PresetsSection.vue';
import WorkflowPresetBar     from './WorkflowPresetBar.vue';
import EdgeLandmarkWarnings  from '../views/EdgeLandmarkWarnings.vue';
import LayerProperties       from './LayerProperties.vue';
import { useUiModeStore }    from '../../stores/uiMode';

const ui = useUiModeStore();
const TAB_TOOL = { fetch: 'editData', view: 'editLook', composite: 'editModel' } as const;
const activeTab = ref<'fetch' | 'view' | 'composite'>('fetch');
const anyTool = computed(() => ['editData', 'editLook', 'editModel', 'editJson'].some((t) => ui.shows(t)));
/** The tab shown: the chosen one while its tool is on, else the first tool that is. */
const tab = computed(() => {
  if (ui.shows(TAB_TOOL[activeTab.value])) return activeTab.value;
  return (Object.keys(TAB_TOOL) as (keyof typeof TAB_TOOL)[]).find((k) => ui.shows(TAB_TOOL[k])) ?? null;
});
// City SOP: step 2 (Load terrain) for Fetch/View, step 3 (Terrain edits) for Composite.
const guideLink = computed(() => guideHref('city-stl-and-puzzle-sop',
  tab.value === 'composite' ? 'step-3' : 'step-2'));
function openSettings() {
  (window as any).openSettingsSheet?.();
}
</script>
<style scoped>
.lp-more { color: var(--text-muted); font-size: 12px; text-align: center; padding: 0 8px 12px; }
.lp-more-link { background: none; border: 0; padding: 0; color: #0a84ff; font-size: 12px; cursor: pointer; }
.settings-primary-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 4px 0 8px;
  border-bottom: 1px solid #333;
  margin-bottom: 6px;
}
.settings-load-dem-btn {
  flex: 1;
  padding: 7px 0;
  font-size: 13px;
  font-weight: 600;
}
.settings-icon-btn {
  padding: 4px 8px;
  font-size: 12px;
}
</style>

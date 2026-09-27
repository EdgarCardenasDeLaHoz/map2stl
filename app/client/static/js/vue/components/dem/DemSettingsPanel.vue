<template>
  <!-- Settings resize handle + right panel -->
  <div id="settingsPanelResizeHandle" class="settings-resize-handle" title="Drag to resize settings panel"></div>
  <div class="dem-right-panel" id="demRightPanel">

    <!-- Tab strip — mirrors the Extrude panel's Fetch/View/Export pattern -->
    <div class="dem-strip" id="demStrip">
      <button :class="['dem-strip-btn', activeTab==='fetch' && 'active']"
              @click="activeTab='fetch'"
              title="DEM source, projection, and per-layer fetch parameters">📥 Fetch</button>
      <div class="dem-strip-divider"></div>
      <button :class="['dem-strip-btn', activeTab==='view' && 'active']"
              @click="activeTab='view'"
              title="Canvas display mode and per-layer visibility / opacity">👁 View</button>
      <button :class="['dem-strip-btn', activeTab==='composite' && 'active']"
              @click="activeTab='composite'"
              title="Composite DEM and saved presets">⊕ Composite</button>
      <div style="flex:1"></div>
      <a class="dem-strip-btn guide-link" :href="guideLink" target="_blank" rel="noopener"
         title="Open the step-by-step guide for this part (new tab)">? Guide</a>
      <button class="dem-strip-btn" id="settingsHideBtn" title="Hide settings panel">◀ Hide</button>
      <button class="dem-strip-btn" id="jsonViewToggleBtn" title="Toggle between form and JSON editor">{ } JSON</button>
    </div>

    <!-- Main scrollable settings area -->
    <div class="dem-controls" id="demControls">
      <div class="dem-controls-inner" id="demControlsInner">

        <!-- Primary actions — pinned at the top of the Fetch tab. Load DEM is the
             step everything else depends on, so it leads; its handler is wired
             by id in event-listeners-map.js (window.loadDEM). Source and
             resolution stay under Fetch Layers → DEM Source. -->
        <div v-show="activeTab==='fetch'" id="settingsSaveRow" class="settings-primary-row">
          <button id="loadDemBtn" class="btn btn-primary settings-load-dem-btn"
                  title="Fetch the DEM for the selected region with the source and resolution under Fetch Layers → DEM Source">🏔 Load DEM</button>
          <button id="saveRegionSettingsBtn" class="btn btn-secondary settings-save-btn"
                  title="Save all current panel settings for the selected region">💾 Save settings</button>
          <button id="clearRegionCacheBtn" class="btn btn-secondary settings-icon-btn"
                  aria-label="Clear region cache"
                  title="Clear all cached data (DEM, water, satellite, etc.) and re-fetch">🗑️</button>
          <label class="check-label settings-autosave" title="Auto-save settings after changes (does not load data)">
            <input type="checkbox" id="autoSaveEnabled" aria-label="Auto save region settings"> Auto-save
          </label>
          <span id="saveSettingsStatus" class="settings-save-status"></span>
        </div>
        <WorkflowPresetBar v-show="activeTab==='fetch'" />
        <!-- Landmarks near the box edge; fetched by the Explore map's instance. -->
        <EdgeLandmarkWarnings v-show="activeTab==='fetch'" compact :max="3" />

        <!-- ═══════════ Fetch tab ═══════════ -->
        <div v-show="activeTab==='fetch'">
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
        <div v-show="activeTab==='view'">
          <LayerViewSection />
          <VisualizationSection />
          <LayerDisplaySections />
        </div>

        <!-- ═══════════ Composite tab ═══════════ -->
        <div v-show="activeTab==='composite'">
          <CompositeDemSection />
          <MeshImportSection />
          <PlateRegistrationSection />
        </div>

      </div><!-- /dem-controls-inner -->

      <!-- JSON settings editor (hidden by default, toggled by { } JSON button) -->
      <div id="settingsJsonView" class="settings-json-view hidden">
        <div style="font-size:10px;color:#888;margin-bottom:4px;">Edit settings as JSON. Click Apply to update the form.</div>
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

const activeTab = ref<'fetch' | 'view' | 'composite'>('fetch');
// City SOP: step 2 (Load terrain) for Fetch/View, step 3 (Terrain edits) for Composite.
const guideLink = computed(() => guideHref('city-stl-and-puzzle-sop',
  activeTab.value === 'composite' ? 'step-3' : 'step-2'));
</script>
<style scoped>
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
  flex: 1 1 100%;
  padding: 7px 0;
  font-size: 13px;
  font-weight: 600;
}
.settings-save-btn {
  flex: 1;
  padding: 4px 8px;
  font-size: 11px;
}
.settings-icon-btn {
  padding: 4px 8px;
  font-size: 12px;
}
.settings-autosave {
  font-size: 11px;
  color: #aaa;
  white-space: nowrap;
}
.settings-save-status {
  font-size: 10px;
  color: #888;
  min-width: 60px;
  text-align: right;
}
</style>

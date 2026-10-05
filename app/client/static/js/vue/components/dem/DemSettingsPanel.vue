<template>
  <!-- Settings resize handle + right panel -->
  <div id="settingsPanelResizeHandle" class="settings-resize-handle" title="Drag to resize settings panel"></div>
  <div class="dem-right-panel" id="demRightPanel">

    <!-- Strip: the guide and hiding the panel. The JSON editor's toggle stays here (hidden) as
         the target of the Terrain panel's "Settings as JSON" link. -->
    <div class="dem-strip" id="demStrip">
      <div style="flex:1"></div>
      <a class="dem-strip-btn guide-link" :href="guideLink" target="_blank" rel="noopener"
         title="Open the step-by-step guide for this part (new tab)">? Guide</a>
      <button class="dem-strip-btn" id="settingsHideBtn" title="Hide settings panel">◀ Hide</button>
      <button v-show="false" class="dem-strip-btn" id="jsonViewToggleBtn" title="Toggle between form and JSON editor">{ } JSON</button>
    </div>

    <!-- Main scrollable settings area -->
    <div class="dem-controls" id="demControls">
      <div class="dem-controls-inner" id="demControlsInner">
        <!-- Landmarks near the box edge (a warning, so it shows on every layer). -->
        <EdgeLandmarkWarnings compact :max="3" />
        <!-- The selected layer's Fetch / View / Composite and the Canvas group (F-EDITPANEL). -->
        <LayerSettings />

        <!-- The old sections, hidden: they hold the controls (by id) the rows above write and
             the modules read. The richer ones open as sub-pages of LayerSettings (their
             CollapsibleSection `sub`). Never v-if: modules bind to these ids at startup, and
             the Rendering section holds #curveCanvas. -->
        <div id="legacyControls" hidden>
          <div id="settingsSaveRow" class="settings-primary-row">
            <button id="loadDemBtn" class="btn btn-primary settings-load-dem-btn"
                    title="Fetch the DEM for the selected region">🏔 Load DEM</button>
            <button id="clearRegionCacheBtn" class="btn btn-secondary settings-icon-btn"
                    aria-label="Clear region cache"
                    title="Clear all cached data (DEM, water, satellite, etc.) and re-fetch">🗑️</button>
          </div>
          <WorkflowPresetBar />
          <ProjectionSection />
          <FetchLayersSection />
          <CityLandmarksSection />
          <PresetsSection />
          <LayerViewSection />
          <VisualizationSection />
          <LayerDisplaySections />
          <CompositeDemSection />
          <MeshImportSection />
          <PlateRegistrationSection />
          <!-- Borders layer (view only): read by modules/layers/borders-overlay.js -->
          <div id="bordersControls">
            <input type="checkbox" id="bordersShowCountries" checked aria-label="Show country borders">
            <input type="checkbox" id="bordersShowStates" checked aria-label="Show state and province borders">
            <input type="color" id="bordersCountryColor" value="#ff453a" aria-label="Country border colour">
            <input type="color" id="bordersStateColor" value="#ffd60a" aria-label="State border colour">
            <button type="button" id="reloadBordersBtn" @click="reloadBorders">Reload borders</button>
          </div>
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
import { computed } from 'vue';
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
import LayerSettings         from './LayerSettings.vue';
import { useEditLayersStore } from '../../stores/editLayers';

const layers = useEditLayersStore();
const reloadBorders = () => (window as any).loadBorders?.({ activate: true });
// City SOP: step 2 (Load terrain) for the terrain, step 3 (Terrain edits) for the carved layers.
const guideLink = computed(() => guideHref('city-stl-and-puzzle-sop',
  layers.selected === 'terrain' ? 'step-2' : 'step-3'));
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

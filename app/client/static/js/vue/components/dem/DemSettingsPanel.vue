<template>
  <!-- Settings resize handle + right panel -->
  <div id="settingsPanelResizeHandle" class="settings-resize-handle" title="Drag to resize settings panel"></div>
  <div class="dem-right-panel" id="demRightPanel">

    <!-- Tab/toggle strip bar -->
    <div class="dem-strip" id="demStrip">
      <button class="dem-strip-btn active" id="settingsStripBtn" title="Toggle settings panel">⚙ Settings</button>
      <div class="dem-strip-divider"></div>
      <button class="dem-strip-btn active" id="layersStripBtn" data-subtab="layers" title="Switch to Layers view">
        📚 Layers
        <span class="strip-status-dots">
          <span class="strip-dot" id="stripDotDem"></span>
          <span class="strip-dot" id="stripDotWater"></span>
          <span class="strip-dot" id="stripDotLandCover"></span>
        </span>
      </button>
      <button class="dem-strip-btn" id="compareStripBtn" data-subtab="compare" title="Switch to Compare view">⚖ Compare</button>
      <div style="flex:1"></div>
      <button class="dem-strip-btn" id="jsonViewToggleBtn" title="Toggle between form and JSON editor">{ } JSON</button>
    </div>

    <!-- Main scrollable settings area -->
    <div class="dem-controls" id="demControls">
      <div class="dem-controls-inner" id="demControlsInner">
        <LayerViewSection />
        <DemSourceSection />
        <ProjectionSection />
        <!-- IMPORTANT: VisualizationSection contains #curveCanvas — never use v-if here, only v-show -->
        <VisualizationSection />
        <WaterLandCoverSection />
        <HydrologySection />
        <CitiesSection />
        <CompositeDemSection />
        <PresetsSection />

        <!-- Save Settings row — always visible at bottom -->
        <div id="settingsSaveRow" class="row-gap6" style="padding:10px 0 4px;border-top:1px solid #333;margin-top:6px;">
          <button id="saveRegionSettingsBtn" class="btn btn-primary"
                  style="flex:1;padding:6px 0;font-size:12px;"
                  title="Save all current panel settings for the selected region">💾 Save Settings</button>
          <span id="saveSettingsStatus" style="font-size:10px;color:#888;min-width:60px;text-align:right;"></span>
        </div>
      </div><!-- /dem-controls-inner -->

      <!-- JSON settings editor (hidden by default, toggled by { } JSON button) -->
      <div id="settingsJsonView" class="settings-json-view hidden">
        <div style="font-size:10px;color:#888;margin-bottom:4px;">Edit settings as JSON. Click Apply to update the form.</div>
        <textarea id="settingsJsonEditor" class="settings-json-editor" spellcheck="false"></textarea>
        <div id="settingsJsonError" class="settings-json-error hidden"></div>
        <div class="row-gap6" style="margin-top:6px;">
          <button id="applyJsonSettingsBtn" class="btn btn-primary" style="flex:1;font-size:11px;">✓ Apply</button>
          <button id="cancelJsonSettingsBtn" class="btn btn-secondary" style="font-size:11px;">✕ Cancel</button>
        </div>
      </div>

      <!-- Merge subtab panel -->
      <div id="mergePanel" class="hidden">
        <div class="dem-controls-inner">
          <!-- Layer stack header -->
          <div class="row" style="justify-content:space-between;margin-bottom:6px;">
            <span style="font-size:11px;color:#aaa;">Elevation layers</span>
            <button id="mergeSyncBtn" class="btn btn-secondary" style="font-size:10px;padding:2px 8px;"
                    title="Rebuild layers from current DEM source and water mask settings">↺ Sync from layers</button>
          </div>

          <!-- Layer stack -->
          <div id="mergeLayerList" class="col" style="gap:8px;"></div>

          <!-- Add layer button -->
          <button id="mergeAddLayerBtn" class="btn btn-secondary" style="width:100%;margin-top:8px;font-size:11px;">+ Add Layer</button>

          <!-- Actions -->
          <div class="row-gap6" style="margin-top:10px;">
            <button id="mergePreviewBtn" class="btn btn-primary" style="flex:1;font-size:11px;">👁 Preview</button>
            <button id="mergeApplyBtn" class="btn btn-success" style="flex:1;font-size:11px;">✓ Apply as DEM</button>
          </div>
          <div id="mergeStatus" style="font-size:10px;color:#888;margin-top:6px;min-height:16px;"></div>
        </div>
      </div><!-- /mergePanel -->
    </div><!-- /dem-controls -->

  </div><!-- /dem-right-panel -->
  <button id="settingsCollapsedTab" class="settings-collapsed-tab" title="Open settings panel">⚙ Settings</button>
</template>
<script setup lang="ts">
import LayerViewSection      from './LayerViewSection.vue';
import DemSourceSection      from './DemSourceSection.vue';
import ProjectionSection     from './ProjectionSection.vue';
import VisualizationSection  from './VisualizationSection.vue';
import WaterLandCoverSection from './WaterLandCoverSection.vue';
import HydrologySection      from './HydrologySection.vue';
import CitiesSection         from './CitiesSection.vue';
import CompositeDemSection   from './CompositeDemSection.vue';
import PresetsSection        from './PresetsSection.vue';
</script>

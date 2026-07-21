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
            <button class="dem-strip-btn" id="settingsHideBtn" title="Hide settings panel">◀ Hide</button>
      <button class="dem-strip-btn" id="jsonViewToggleBtn" title="Toggle between form and JSON editor">{ } JSON</button>
    </div>

    <!-- Main scrollable settings area -->
    <div class="dem-controls" id="demControls">
      <div class="dem-controls-inner" id="demControlsInner">

        <!-- Save Settings row — pinned at top across tabs for quick access -->
        <div v-show="activeTab==='fetch'" id="settingsSaveRow" class="row-gap6" style="padding:4px 0 8px;border-bottom:1px solid #333;margin-bottom:6px;">
          <button id="saveRegionSettingsBtn" class="btn btn-primary"
                  style="flex:1;padding:6px 0;font-size:14px;"
                  aria-label="Save region settings"
                  title="Save all current panel settings for the selected region">💾</button>
          <button id="clearRegionCacheBtn" class="btn btn-secondary"
                  style="padding:6px 8px;font-size:14px;"
                  aria-label="Clear region cache"
                  title="Clear all cached data (DEM, water, satellite, etc.) and re-fetch">🗑️</button>
          <label class="check-label" style="font-size:11px;color:#aaa;white-space:nowrap;" title="Auto-save settings after changes">
            <input type="checkbox" id="autoSaveEnabled" aria-label="Auto save region settings"> Auto
          </label>
          <span id="saveSettingsStatus" style="font-size:10px;color:#888;min-width:60px;text-align:right;"></span>
        </div>

        <!-- ═══════════ Fetch tab ═══════════ -->
        <div v-show="activeTab==='fetch'">
          <ProjectionSection />
          <FetchLayersSection />
          <PresetsSection />
        </div>

        <!-- ═══════════ View tab ═══════════ -->
        <!-- IMPORTANT: Rendering section contains #curveCanvas — never use v-if here, only v-show -->
        <div v-show="activeTab==='view'">
          <LayerViewSection />
          <VisualizationSection />
        </div>

        <!-- ═══════════ Composite tab ═══════════ -->
        <div v-show="activeTab==='composite'">
          <CompositeDemSection />
          <MeshImportSection />
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

      <!-- Merge subtab panel -->
      <div id="mergePanel" class="hidden">
        <div class="dem-controls-inner">
          <div class="row" style="justify-content:space-between;margin-bottom:6px;">
            <span style="font-size:11px;color:#aaa;">Elevation layers</span>
            <button id="mergeSyncBtn" class="btn btn-secondary" style="font-size:10px;padding:2px 8px;"
                    title="Rebuild layers from current DEM source and water mask settings">↺ Sync from layers</button>
          </div>
          <div id="mergeLayerList" class="col" style="gap:8px;"></div>
          <button id="mergeAddLayerBtn" class="btn btn-secondary" style="width:100%;margin-top:8px;font-size:11px;">+ Add Layer</button>
          <div class="row-gap6" style="margin-top:10px;">
            <button id="mergePreviewBtn" class="btn btn-primary" style="flex:1;font-size:11px;">👁 Preview</button>
            <button id="mergeApplyBtn" class="btn btn-success" style="flex:1;font-size:11px;">✓ Apply as DEM</button>
          </div>
          <div id="mergeStatus" style="font-size:10px;color:#888;margin-top:6px;min-height:16px;"></div>
        </div>
      </div>
    </div><!-- /dem-controls -->

  </div><!-- /dem-right-panel -->
  <button id="settingsCollapsedTab" class="settings-collapsed-tab" title="Open settings panel">⚙ Settings</button>
</template>
<script setup lang="ts">
import { ref } from 'vue';
import VisualizationSection  from './VisualizationSection.vue';
import LayerViewSection      from './LayerViewSection.vue';
import ProjectionSection     from './ProjectionSection.vue';
import FetchLayersSection    from './FetchLayersSection.vue';
import CompositeDemSection   from './CompositeDemSection.vue';
import MeshImportSection     from './MeshImportSection.vue';
import PresetsSection        from './PresetsSection.vue';

const activeTab = ref<'fetch' | 'view' | 'composite'>('fetch');
</script>

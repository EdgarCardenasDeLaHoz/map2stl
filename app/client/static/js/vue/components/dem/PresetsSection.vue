<template>
  <CollapsibleSection title="💾 Parameter Presets" wrap-style="margin-bottom:10px;"
                      header-title="Save and load named snapshots of your current visualization settings.">
    <div class="preset-profiles">
      <div class="param-group">
        <label for="presetSelect">Load Preset:</label>
        <select id="presetSelect" class="ctrl-select" style="padding:6px;">
          <option value="">-- Select Preset --</option>
          <option value="default">Default</option>
          <option value="high-detail">High Detail</option>
          <option value="print-ready">Print Ready</option>
          <option value="mountain">Mountain Focus</option>
          <option value="coastal">Coastal Detail</option>
        </select>
      </div>
      <div class="preset-buttons">
        <button id="loadPresetBtn"   class="btn btn-primary"   style="flex:1;"><span class="btn-icon">📂</span> Load</button>
        <button id="savePresetBtn"   class="btn btn-secondary" style="flex:1;"><span class="btn-icon">💾</span> Save</button>
        <button id="deletePresetBtn" class="btn btn-danger"    style="flex:0;padding:6px 12px;"><span class="btn-icon">🗑️</span></button>
      </div>
      <div id="presetSaveDialog" class="preset-save-dialog hidden">
        <div class="param-group" style="margin-bottom:8px;">
          <label for="newPresetName">New Preset Name:</label>
          <input type="text" id="newPresetName" placeholder="My Preset" class="ctrl-input" style="padding:6px;">
        </div>
        <div class="row-gap8">
          <button id="confirmSavePresetBtn" class="btn btn-success"   style="flex:1;">Save</button>
          <button id="cancelSavePresetBtn"  class="btn btn-secondary" style="flex:1;">Cancel</button>
        </div>
      </div>
      <div style="font-size:10px;color:#888;margin-top:8px;">Saves: Dimension, Depth/Water scales, Colormap, Elevation curve</div>
    </div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

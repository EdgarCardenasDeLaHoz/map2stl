<template>
  <div id="newRegionSection" class="control-group">
    <CollapsibleSection title="+ New Region" wrap-style="">
      <div class="new-region-section">
        <label for="regionName" style="font-size:11px;color:#888;">Name</label>
        <input type="text" id="regionName" class="new-region-input" placeholder="Region name…">
        <label for="regionLabel" style="font-size:11px;color:#888;margin-top:4px;">
          Group <span style="font-size:10px;color:#666;">(optional)</span>
        </label>
        <input type="text" id="regionLabel" class="new-region-input" placeholder="e.g. North America, Europe…">
        <div class="row-gap6" style="margin-top:6px;">
          <button id="startDrawBtn" class="btn btn-secondary" style="flex:1;"
                  title="Draw a rectangle on the map to define this region's bounding box">
            ✏️ Draw bbox
          </button>
          <button id="saveRegionBtn" class="btn btn-success" style="flex:1;"
                  title="Save the drawn bounding box as a new named region">
            💾 Save
          </button>
        </div>
      </div>
    </CollapsibleSection>
  </div>
  <!-- Hidden stubs kept for JS backward-compat until Stage 7 -->
  <button id="loadRegionBtn" style="display:none;"></button>
  <button id="submitBtn"     style="display:none;"></button>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

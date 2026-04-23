<template>
  <div id="newRegionSection" class="control-group">
    <div class="new-region-section" style="padding:6px 8px;">
      <div style="font-size:11px;color:#888;margin-bottom:4px;">Save drawn region</div>
      <label for="regionName" style="font-size:11px;color:#888;">Name</label>
      <input type="text" id="regionName" class="new-region-input" placeholder="Region name…">
      <label for="regionLabel" style="font-size:11px;color:#888;margin-top:4px;">
        Group <span style="font-size:10px;color:#666;">(optional)</span>
      </label>
      <input type="text" id="regionLabel" class="new-region-input" placeholder="e.g. North America, Europe…">
      <div class="row-gap6" style="margin-top:6px;">
        <button id="saveRegionBtn" class="btn btn-success" style="flex:1;"
                title="Save the drawn bounding box as a new named region">
          💾 Save Region
        </button>
      </div>
    </div>
  </div>
  <!-- Hidden stubs kept for JS backward-compat until Stage 7 -->
  <button id="startDrawBtn"  style="display:none;"></button>
  <button id="loadRegionBtn" style="display:none;"></button>
  <button id="submitBtn"     style="display:none;"></button>
</template>
<script setup lang="ts">
</script>

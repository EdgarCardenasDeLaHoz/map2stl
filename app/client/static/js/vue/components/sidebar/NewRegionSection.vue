<template>
  <div id="newRegionSection" class="control-group">
    <div class="new-region-section">
      <div class="new-region-form-hint">Save drawn region</div>
      <label for="regionName" class="new-region-form-label">Name</label>
      <input type="text" id="regionName" class="new-region-input" placeholder="Region name…">
      <label for="regionLabel" class="new-region-form-label new-region-group-label">
        Group <span class="new-region-optional-hint">(optional)</span>
      </label>
      <input type="text" id="regionLabel" class="new-region-input" placeholder="e.g. North America, Europe…">
      <div class="row-gap6 new-region-save-row">
        <button id="saveRegionBtn" class="btn btn-success new-region-save-btn"
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

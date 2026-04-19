<template>
  <CollapsibleSection title="💧 Water Mask">

    <!-- water.dataset -->
    <div class="param-group">
      <label for="waterDataset" title="Source dataset for water detection.">Dataset:</label>
      <select id="waterDataset" class="ctrl-select">
        <option value="esa" selected>ESA WorldCover</option>
        <option value="jrc">JRC Global Surface Water</option>
      </select>
    </div>

    <!-- water.sat_scale -->
    <div class="param-group" style="margin-top:6px;">
      <label for="waterResolution" title="Earth Engine fetch resolution in metres/pixel. Lower = finer, slower.">Resolution:</label>
      <select id="waterResolution" class="ctrl-select">
        <option value="10">10 m/px — ESA Native (city)</option>
        <option value="30">30 m/px — High Detail</option>
        <option value="100">100 m/px — Medium</option>
        <option value="200" selected>200 m/px — Default</option>
        <option value="500">500 m/px — Fast</option>
        <option value="1000">1000 m/px — Very Fast</option>
      </select>
      <div id="waterResWarning" style="font-size:10px;color:#f90;display:none;">⚠️ May require tiling for large areas</div>
    </div>

    <button id="loadWaterMaskBtn" class="btn btn-secondary" style="width:100%;font-size:11px;padding:4px 0;margin-top:8px;">🌊 Load Water Mask</button>
    <div id="waterMaskStats" style="font-size:10px;color:#888;margin-top:4px;min-height:14px;"></div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

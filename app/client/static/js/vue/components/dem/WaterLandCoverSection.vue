<template>
  <CollapsibleSection title="💧 Water &amp; Land Cover">

    <!-- water.dataset -->
    <div class="param-group">
      <label for="waterDataset" title="Source dataset for water and land-cover detection.">Dataset:</label>
      <select id="waterDataset" class="ctrl-select">
        <option value="esa" selected>ESA WorldCover</option>
        <option value="jrc">JRC Global Surface Water</option>
      </select>
    </div>

    <!-- water.dim -->
    <div class="param-group" style="margin-top:6px;">
      <label for="waterResolution" title="Output resolution in pixels per side. The backend computes the Earth Engine scale from your bbox and this value.">Resolution (px):</label>
      <select id="waterResolution" class="ctrl-select">
        <option value="100">100 px — Very Fast</option>
        <option value="200">200 px — Fast</option>
        <option value="400">400 px — Medium</option>
        <option value="600" selected>600 px — Default</option>
        <option value="1200">1200 px — High Detail</option>
      </select>
      <div id="waterResWarning" style="font-size:10px;color:#f90;display:none;">⚠️ May require tiling for large areas</div>
    </div>

    <button id="loadWaterMaskBtn" class="btn btn-secondary" style="width:100%;font-size:11px;padding:4px 0;margin-top:8px;">🌊 Load Water &amp; ESA</button>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

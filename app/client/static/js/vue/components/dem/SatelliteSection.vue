<template>
  <CollapsibleSection title="🛰️ Satellite Imagery">

    <div style="font-size:10px;color:#888;margin-bottom:8px;">
      Real satellite tiles from ESRI World Imagery (WMTS).
    </div>

    <!-- Resolution -->
    <div class="param-group">
      <label for="satImgResolution" title="Satellite image resolution (pixels per side).">Resolution (px):</label>
      <select id="satImgResolution" class="ctrl-select">
        <option value="200">200 px</option>
        <option value="400">400 px</option>
        <option value="600">600 px</option>
        <option value="800" selected>800 px</option>
        <option value="1200">1200 px</option>
      </select>
    </div>

    <!-- buttons -->
    <div class="row-gap6" style="margin-top:8px;">
      <button id="loadSatImgBtn" class="btn btn-secondary" style="flex:1;font-size:11px;" title="Fetch satellite imagery for current bbox">🛰 Load</button>
      <button id="clearSatImgBtn" class="btn btn-secondary" style="font-size:11px;" title="Clear satellite layer">✕ Clear</button>
    </div>
    <div id="satImgStatus" style="font-size:10px;color:#888;margin-top:4px;min-height:14px;"></div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

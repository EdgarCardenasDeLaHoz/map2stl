<template>
  <CollapsibleSection title="📊 Composite DEM" :start-open="true"
                      header-title="Adjust per-layer height contributions to build a composite elevation model">
    <div style="margin-bottom:8px;">
      <label class="check-label" style="font-size:12px;">
        <input type="checkbox" id="compositeEnabled" checked> Enable composite layer
      </label>
    </div>

    <div class="composite-group-label">Water contribution</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Depth</span>
      <input type="range" id="compositeWaterDepth" min="0" max="50" value="5" step="0.5">
      <span id="compositeWaterDepthLabel" class="composite-slider-value">5.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeWaterWeight" min="0" max="5" value="1" step="0.1">
      <span id="compositeWaterWeightLabel" class="composite-slider-value">1.0</span>
    </div>

    <div class="composite-group-label">City / OSM contribution</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Building scale</span>
      <input type="range" id="compositeBuildingScale" min="0" max="5" value="1" step="0.1">
      <span id="compositeBuildingScaleLabel" class="composite-slider-value">1.0</span>
      <span class="composite-slider-label">Road cut</span>
      <input type="range" id="compositeRoadCut" min="0" max="5" value="0.5" step="0.1">
      <span id="compositeRoadCutLabel" class="composite-slider-value">0.5 m</span>
      <span class="composite-slider-label">River depth</span>
      <input type="range" id="compositeRiverDepth" min="0" max="20" value="3" step="0.5">
      <span id="compositeRiverDepthLabel" class="composite-slider-value">3.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeCityWeight" min="0" max="5" value="1" step="0.1">
      <span id="compositeCityWeightLabel" class="composite-slider-value">1.0</span>
    </div>

    <div class="composite-group-label">Land cover contribution</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Tree height</span>
      <input type="range" id="compositeTreeHeight" min="0" max="40" value="8" step="0.5">
      <span id="compositeTreeHeightLabel" class="composite-slider-value">8.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeLandcoverWeight" min="0" max="5" value="0" step="0.1">
      <span id="compositeLandcoverWeightLabel" class="composite-slider-value">0.0</span>
    </div>

    <div class="composite-group-label">Satellite vegetation</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Veg height</span>
      <input type="range" id="compositeVegHeight" min="0" max="30" value="5" step="0.5">
      <span id="compositeVegHeightLabel" class="composite-slider-value">5.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeSatWeight" min="0" max="5" value="0" step="0.1">
      <span id="compositeSatWeightLabel" class="composite-slider-value">0.0</span>
    </div>

    <div class="row-gap6" style="margin-top:10px;">
      <button id="applyCompositeToDemBtn" class="btn btn-primary"
              style="flex:1;padding:5px 0;font-size:11px;"
              title="Replace current DEM with composite values">Apply to DEM</button>
      <span id="compositeStats" style="font-size:10px;color:#aaa;white-space:nowrap;"></span>
    </div>
    <div style="font-size:10px;color:#666;margin-top:4px;">Replaces base DEM with composite values for STL export</div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

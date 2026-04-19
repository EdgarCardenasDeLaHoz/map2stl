<template>
  <CollapsibleSection title="📊 Composite DEM — Output" :start-open="true"
                      header-title="Final height map that feeds into Extrude. Adjust per-layer contributions below.">
    <div style="font-size:10px;color:#777;margin-bottom:8px;padding:4px 6px;background:#2a2a2a;border-radius:3px;border-left:2px solid #4a9eff;">
      Pipeline: DEM → <span style="color:#4a9eff;">− water</span> → <span style="color:#cc8844;">+ city</span> → <span style="color:#66aa66;">+ land cover</span> → <span style="color:#88aacc;">+ vegetation</span> → <strong style="color:#fff;">Composite ➜ Extrude</strong>
    </div>
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

    <!-- Preview thumbnail -->
    <div style="margin-top:8px;text-align:center;">
      <canvas id="compositePreviewThumb" width="160" height="100"
              style="border:1px solid #444;border-radius:3px;background:#1a1a1a;max-width:100%;"></canvas>
      <div id="compositeContribStatus" style="font-size:10px;color:#888;margin-top:2px;"></div>
    </div>

    <div class="row-gap6" style="margin-top:8px;">
      <button id="previewCompositeBtn" class="btn btn-secondary"
              style="flex:1;padding:5px 0;font-size:11px;"
              title="Preview composite in the layer view">👁 Preview</button>
      <button id="applyCompositeToDemBtn" class="btn btn-primary"
              style="flex:1;padding:5px 0;font-size:11px;"
              title="Replace current DEM with composite values">✓ Apply to DEM</button>
    </div>
    <span id="compositeStats" style="font-size:10px;color:#aaa;display:block;margin-top:4px;"></span>
    <div style="font-size:10px;color:#666;margin-top:2px;">Preview shows the composite; Apply replaces base DEM for STL export</div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

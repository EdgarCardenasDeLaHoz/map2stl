<template>
  <CollapsibleSection title="📊 Composite DEM — Output" :start-open="true"
                      header-title="Final height map that feeds into Extrude. Adjust per-layer contributions below.">
    <div class="composite-info-box">
      Pipeline: DEM → <span class="composite-pipe-water">− water</span> → <span class="composite-pipe-city">+ city</span> → <span class="composite-pipe-landcover">+ land cover</span> → <span class="composite-pipe-veg">+ vegetation</span> → <strong class="composite-pipe-output">Composite → Extrude</strong>
    </div>
    <div class="composite-enable-row">
      <label class="check-label composite-enable-label">
        <input type="checkbox" id="compositeEnabled" checked aria-label="Enable composite DEM layer"> Enable composite layer
      </label>
    </div>

    <div class="composite-group-label">Water contribution</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Depth</span>
      <input type="range" id="compositeWaterDepth" min="0" max="50" value="5" step="0.5" aria-label="Composite water depth">
      <span id="compositeWaterDepthLabel" class="composite-slider-value">5.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeWaterWeight" min="0" max="5" value="1" step="0.1" aria-label="Composite water weight">
      <span id="compositeWaterWeightLabel" class="composite-slider-value">1.0</span>
    </div>

    <div class="composite-group-label">City / OSM contribution</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Building scale</span>
      <input type="range" id="compositeBuildingScale" min="0" max="5" value="1" step="0.1" aria-label="Composite building scale">
      <span id="compositeBuildingScaleLabel" class="composite-slider-value">1.0</span>
      <span class="composite-slider-label">Road cut</span>
      <input type="range" id="compositeRoadCut" min="0" max="5" value="0.5" step="0.1" aria-label="Composite road cut depth">
      <span id="compositeRoadCutLabel" class="composite-slider-value">0.5 m</span>
      <span class="composite-slider-label">River depth</span>
      <input type="range" id="compositeRiverDepth" min="0" max="20" value="3" step="0.5" aria-label="Composite river depth">
      <span id="compositeRiverDepthLabel" class="composite-slider-value">3.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeCityWeight" min="0" max="5" value="1" step="0.1" aria-label="Composite city weight">
      <span id="compositeCityWeightLabel" class="composite-slider-value">1.0</span>
    </div>

    <div class="composite-group-label">Land cover contribution</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Tree height</span>
      <input type="range" id="compositeTreeHeight" min="0" max="40" value="8" step="0.5" aria-label="Composite tree height">
      <span id="compositeTreeHeightLabel" class="composite-slider-value">8.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeLandcoverWeight" min="0" max="5" value="0" step="0.1" aria-label="Composite landcover weight">
      <span id="compositeLandcoverWeightLabel" class="composite-slider-value">0.0</span>
    </div>

    <div class="composite-group-label">Satellite vegetation</div>
    <div class="composite-sliders">
      <span class="composite-slider-label">Veg height</span>
      <input type="range" id="compositeVegHeight" min="0" max="30" value="5" step="0.5" aria-label="Composite vegetation height">
      <span id="compositeVegHeightLabel" class="composite-slider-value">5.0 m</span>
      <span class="composite-slider-label">Weight</span>
      <input type="range" id="compositeSatWeight" min="0" max="5" value="0" step="0.1" aria-label="Composite satellite vegetation weight">
      <span id="compositeSatWeightLabel" class="composite-slider-value">0.0</span>
    </div>

    <div class="row-gap6 composite-actions">
      <button id="applyCompositeToDemBtn" class="btn btn-primary composite-action-btn"
              title="Replace current DEM with composite values">✓ Apply to DEM</button>
    </div>
    <span id="compositeStats" class="composite-stats"></span>
    <div class="composite-footer-hint">Apply replaces base DEM with the composite for export and 3D generation</div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

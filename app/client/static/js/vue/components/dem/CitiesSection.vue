<template>
  <CollapsibleSection title="🏙️ Cities" id="citiesSettingsSection">

    <div style="font-size:10px;color:#888;margin-bottom:8px;" id="cityInfoRow">
      Load OSM building, road, and water data for small regions (≤ 10 km).
    </div>

    <!-- city.layers — which OSM layers to fetch -->
    <div style="margin-bottom:8px;">
      <div style="font-size:10px;color:#aaa;margin-bottom:4px;">Layers to fetch:</div>
      <div class="row-wrap">
        <label class="check-label" style="font-size:11px;" title="Include building footprints">
          <input type="checkbox" id="cityLayerBuildings" checked> 🏠 Buildings
        </label>
        <label class="check-label" style="font-size:11px;" title="Include road network">
          <input type="checkbox" id="cityLayerRoads" checked> 🛣 Roads
        </label>
        <label class="check-label" style="font-size:11px;" title="Include waterways">
          <input type="checkbox" id="cityLayerWaterways" checked> 💧 Waterways
        </label>
      </div>
    </div>

    <div class="row-gap6" style="margin-bottom:6px;">
      <button id="loadCityDataBtn"  class="btn btn-primary"   style="flex:1;font-size:11px;" title="Fetch from OpenStreetMap.">📥 Load Cities</button>
      <button id="clearCityDataBtn" class="btn btn-secondary" style="font-size:11px;"        title="Clear city overlay">✕ Clear</button>
    </div>
    <div id="cityDataStatus" style="font-size:10px;color:#888;margin-bottom:8px;"></div>

    <div style="font-size:10px;color:#888;margin-bottom:4px;">
      <span id="cityBuildingCount"  class="city-layer-count"></span>
      <span id="cityRoadCount"      class="city-layer-count"></span>
      <span id="cityWaterwayCount"  class="city-layer-count"></span>
    </div>

    <!-- city.simplify_tolerance / city.min_area -->
    <div class="param-grid" style="border-top:1px solid #333;padding-top:8px;margin-top:4px;">
      <label for="citySimplifyTolerance" title="Polygon simplification tolerance in metres.">Tolerance (m)</label>
      <input type="number" id="citySimplifyTolerance" value="0.5" min="0" max="50" step="0.5" class="ctrl-input-sm">
      <label for="cityMinArea" title="Minimum building footprint in m².">Min area (m²)</label>
      <input type="number" id="cityMinArea" value="5" min="0" max="5000" step="5" class="ctrl-input-sm">
    </div>

    <!-- city.building_scale / city.road_depression_m / city.water_depression_m -->
    <div class="param-grid" style="border-top:1px solid #333;padding-top:8px;margin-top:6px;">
      <span class="param-grid-header">3D Heights</span>
      <label for="cityBuildingScale" title="Building height scale: mm per real metre.">Building scale (mm/m)</label>
      <input type="number" id="cityBuildingScale" value="0.5" min="0" max="10" step="0.1" class="ctrl-input-sm">
      <label for="cityRoadDepression" title="How far roads are depressed below surrounding terrain (metres, negative = down).">Road depression (m)</label>
      <input type="number" id="cityRoadDepression" value="0.0" min="-10" max="2" step="0.5" class="ctrl-input-sm">
      <label for="cityWaterOffset" title="Waterway surface height relative to ground (metres).">Water offset (m)</label>
      <input type="number" id="cityWaterOffset" value="-2.0" min="-20" max="0" step="0.5" class="ctrl-input-sm">
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

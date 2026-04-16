<template>
  <CollapsibleSection title="🔍 DEM Source">

    <!-- dem.dem_source -->
    <div class="param-group">
      <label for="paramDemSource" title="Elevation data source.">Source:</label>
      <select id="paramDemSource" class="ctrl-select">
        <option value="local">Local SRTM Tiles</option>
        <option value="SRTMGL1">OpenTopo — SRTM 30m</option>
        <option value="SRTMGL3">OpenTopo — SRTM 90m</option>
        <option value="AW3D30">OpenTopo — ALOS World 3D 30m</option>
        <option value="COP30">OpenTopo — Copernicus DSM 30m</option>
        <option value="COP90">OpenTopo — Copernicus DSM 90m</option>
        <option value="SRTM15Plus">OpenTopo — SRTM15+ (Bathymetry)</option>
      </select>
      <div id="demSourceApiKeyWarning" style="font-size:10px;color:#f90;display:none;">
        ⚠️ OpenTopography API key not configured.
      </div>
    </div>

    <!-- dem.dim -->
    <div class="param-group" style="margin-top:6px;">
      <label for="paramDim" title="Number of grid points per side fetched from the DEM source.">Resolution:</label>
      <input type="number" id="paramDim" value="200" min="50" max="2000" step="50"
             title="Grid resolution: pixels per side (50–2000).">
      <div id="demResWarning" style="font-size:10px;color:#f90;display:none;">⚠️ High resolution may be slow</div>
    </div>

    <!-- dem.depth_scale -->
    <div class="param-group" style="margin-top:6px;">
      <label for="paramDepthScale" title="Vertical exaggeration of ocean/depth areas. 1.0 = true scale.">Depth Scale:</label>
      <input type="number" id="paramDepthScale" value="0.5" min="0" max="10" step="0.1" class="ctrl-input"
             title="Scale factor for below-sea-level elevations (0–10).">
    </div>

    <!-- dem.water_scale -->
    <div class="param-group" style="margin-top:6px;">
      <label for="paramWaterScale" title="How strongly to depress water areas relative to land (0–1).">Water Scale:</label>
      <input type="number" id="paramWaterScale" value="0.05" min="0" max="1" step="0.01" class="ctrl-input"
             title="Water depression strength (0 = no depression, 1 = maximum).">
    </div>

    <!-- dem.subtract_water -->
    <div class="param-group" style="margin-top:6px;">
      <label class="check-label" title="Depress water-masked pixels below terrain level.">
        <input type="checkbox" id="paramSubtractWater" checked> Subtract Water
      </label>
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

<template>
  <CollapsibleSection title="🗺️ Layers" :start-open="true" wrap-style="">

    <!-- View mode toggle buttons -->
    <div style="margin-bottom:8px;">
      <div id="layerModeSelector">
        <button class="layer-mode-btn active" data-mode="Dem"        title="Base elevation">🏔 DEM</button>
        <button class="layer-mode-btn" data-mode="CompositeDem" title="Composite DEM">★ Composite</button>
        <button class="layer-mode-btn" data-mode="Water"      title="Water mask">💧 Water</button>
        <button class="layer-mode-btn" data-mode="Sat"        title="ESA land cover">🌿 ESA</button>
        <button class="layer-mode-btn" data-mode="SatImg"     title="Satellite imagery">🛰 Sat</button>
        <button class="layer-mode-btn" data-mode="CityRaster" title="City heights raster">🏙 City</button>
        <button class="layer-mode-btn" data-mode="Hydrology" title="River depression overlay">🌊 Hydro</button>
      </div>
    </div>

    <!-- Opacity -->
    <div id="layerOpacitySliders"></div>
    <div style="display:grid;grid-template-columns:auto 1fr 32px;gap:4px 6px;align-items:center;margin-bottom:8px;">
      <span style="font-size:12px;color:#ccc;white-space:nowrap;">Opacity</span>
      <input type="range" id="activeLayerOpacity" min="0" max="100" value="100" style="width:100%;" title="Active view opacity">
      <span id="activeLayerOpacityLabel" style="font-size:11px;color:#aaa;text-align:right;">100%</span>
    </div>

    <!-- Quick-load bar — each row shows name + resolution from fetch section + load button -->
    <div style="margin-top:4px;padding-top:6px;border-top:1px solid #333;">
      <div style="font-size:11px;color:#888;margin-bottom:4px;">Quick Load</div>
      <div style="display:grid;grid-template-columns:auto 1fr auto;gap:3px 6px;align-items:center;">
        <span style="font-size:10px;color:#aaa;">🏔 DEM</span>
        <span id="qlResDem" style="font-size:10px;color:#666;"></span>
        <button id="qlLoadDem" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;">⟳ Load</button>

        <span style="font-size:10px;color:#aaa;">💧 Water</span>
        <span id="qlResWater" style="font-size:10px;color:#666;"></span>
        <button id="qlLoadWater" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;">⟳ Load</button>

        <span style="font-size:10px;color:#aaa;">🛰 Sat</span>
        <span id="qlResSat" style="font-size:10px;color:#666;"></span>
        <button id="qlLoadSat" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;">⟳ Load</button>

        <span style="font-size:10px;color:#aaa;">🌿 ESA</span>
        <span id="qlResEsa" style="font-size:10px;color:#666;"></span>
        <button id="qlLoadEsa" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;">⟳ Load</button>

        <span style="font-size:10px;color:#aaa;">🌊 Hydro</span>
        <span id="qlResHydro" style="font-size:10px;color:#666;"></span>
        <button id="qlLoadHydro" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;">⟳ Load</button>
      </div>
    </div>

    <!-- City overlay display toggles -->
    <div style="margin-top:8px;padding-top:6px;border-top:1px solid #333;">
      <div style="font-size:11px;color:#888;margin-bottom:4px;">City Overlay</div>
      <div class="row-wrap">
        <label class="check-label" title="Toggle building polygons">
          <input type="checkbox" id="layerBuildingsToggle"> 🏠 Bldgs
        </label>
        <label class="check-label" title="Toggle road network">
          <input type="checkbox" id="layerRoadsToggle"> 🛣 Roads
        </label>
        <label class="check-label" title="Toggle waterway lines">
          <input type="checkbox" id="layerWaterwaysToggle"> 💧 Water
        </label>
      </div>
      <!-- Color swatches — referenced by city-overlay.js -->
      <div style="display:none;">
        <input type="color" id="layerBuildingsColor"  value="#c8b89a" class="city-color-swatch">
        <input type="color" id="layerRoadsColor"      value="#cc8844" class="city-color-swatch">
        <input type="color" id="layerWaterwaysColor"  value="#4488cc" class="city-color-swatch">
      </div>
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

<template>
  <CollapsibleSection title="🗺️ Layer View" :start-open="true" wrap-style="">

    <!-- View mode toggle buttons -->
    <div style="margin-bottom:8px;">
      <div style="font-size:11px;color:#888;margin-bottom:5px;">View Mode</div>
      <div id="layerModeSelector">
        <button class="layer-mode-btn active" data-mode="Dem"        title="Base elevation">🏔 DEM</button>
        <button class="layer-mode-btn" data-mode="CompositeDem" title="Composite DEM">★ Composite</button>
        <button class="layer-mode-btn" data-mode="Water"      title="Water mask">💧 Water</button>
        <button class="layer-mode-btn" data-mode="Sat"        title="ESA land cover">🌿 ESA</button>
        <button class="layer-mode-btn" data-mode="SatImg"     title="Satellite imagery">🛰 Sat</button>
        <button class="layer-mode-btn" data-mode="CityRaster" title="City heights raster">🏙 City</button>
      </div>
    </div>

    <!-- Opacity -->
    <div id="layerOpacitySliders"></div>
    <div style="display:grid;grid-template-columns:auto 1fr 32px;gap:4px 6px;align-items:center;margin-bottom:8px;">
      <span style="font-size:12px;color:#ccc;white-space:nowrap;">Opacity</span>
      <input type="range" id="activeLayerOpacity" min="0" max="100" value="100" style="width:100%;" title="Active view opacity">
      <span id="activeLayerOpacityLabel" style="font-size:11px;color:#aaa;text-align:right;">100%</span>
    </div>

    <!-- Per-layer load controls with independent resolutions -->
    <div style="display:grid;grid-template-columns:auto 1fr auto;gap:4px 6px;align-items:center;margin-bottom:6px;">
      <!-- DEM -->
      <span style="font-size:11px;color:#aaa;white-space:nowrap;">🏔 DEM</span>
      <select id="demLayerResolution" title="DEM grid resolution (pixels per side)" class="ctrl-select" style="font-size:10px;">
        <option value="100">100 px</option>
        <option value="200" selected>200 px</option>
        <option value="400">400 px</option>
        <option value="600">600 px</option>
        <option value="800">800 px</option>
        <option value="1200">1200 px</option>
      </select>
      <button id="loadDemLayerBtn" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;white-space:nowrap;" title="Load DEM layer">⟳ Load</button>

      <!-- Water / ESA -->
      <span style="font-size:11px;color:#aaa;white-space:nowrap;">💧 Water</span>
      <select id="waterLayerResolution" title="Water mask fetch resolution (m/px)" class="ctrl-select" style="font-size:10px;">
        <option value="10">10 m/px</option>
        <option value="30">30 m/px</option>
        <option value="100">100 m/px</option>
        <option value="200" selected>200 m/px</option>
        <option value="500">500 m/px</option>
        <option value="1000">1000 m/px</option>
      </select>
      <button id="loadWaterLayerBtn" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;white-space:nowrap;" title="Load water mask + ESA land cover">⟳ Load</button>

      <!-- Satellite imagery -->
      <span style="font-size:11px;color:#aaa;white-space:nowrap;">🛰 Sat</span>
      <select id="satImgResolution" title="Satellite image resolution (pixels per side)" class="ctrl-select" style="font-size:10px;">
        <option value="200">200 px</option>
        <option value="400">400 px</option>
        <option value="600">600 px</option>
        <option value="800" selected>800 px</option>
        <option value="1200">1200 px</option>
      </select>
      <button id="loadSatImgBtn" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;white-space:nowrap;" title="Load satellite imagery">⟳ Load</button>

      <!-- ESA land use -->
      <span style="font-size:11px;color:#aaa;white-space:nowrap;">🌿 ESA</span>
      <div style="font-size:10px;color:#666;">(uses Water resolution)</div>
      <button id="loadSatBtn" class="btn btn-secondary" style="font-size:10px;padding:2px 6px;white-space:nowrap;" title="Load ESA land cover only">⟳ Load</button>
    </div>
    <div id="waterLayerResWarning" style="font-size:10px;color:#f90;display:none;margin-bottom:4px;">⚠️ Large area — water fetch may be slow</div>

    <!-- Land cover legend + controls -->
    <div style="margin-top:8px;padding-top:6px;border-top:1px solid #333;">
      <div style="font-size:11px;color:#888;margin-bottom:4px;">Land Cover</div>
      <!-- water-mask.js writes to #landCoverLegend via innerHTML -->
      <div id="landCoverLegend" style="overflow-y:auto;max-height:300px;"></div>
      <div class="row-gap6" style="margin-top:6px;">
        <button id="applyLandCoverMapping" class="btn btn-secondary" style="flex:1;padding:5px;font-size:11px;">✓ Apply Colors</button>
        <button id="resetLandCoverMapping" class="btn btn-secondary" style="flex:1;padding:5px;font-size:11px;">↺ Reset</button>
      </div>
    </div>

    <!-- City overlay toggles -->
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

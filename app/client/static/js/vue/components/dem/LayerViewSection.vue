<template>
  <CollapsibleSection title="🗺️ Layers" :start-open="true" wrap-style="">

    <!-- Per-layer rows — one row per entry in _layerOrder -->
    <div id="layerRows" style="display:flex;flex-direction:column;gap:2px;">

      <!-- Dem row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="Base elevation">🏔</span>
        <span class="layer-row-label">DEM</span>
        <span class="layer-row-res" id="layerRes_Dem"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Dem" min="0" max="100" value="100" title="DEM opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Dem">100%</span>
      </div>

      <!-- Water row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="Water mask">💧</span>
        <span class="layer-row-label">Water</span>
        <span class="layer-row-res" id="layerRes_Water"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Water" min="0" max="100" value="70" title="Water opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Water">70%</span>
      </div>

      <!-- ESA row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="ESA land cover">🌿</span>
        <span class="layer-row-label">ESA</span>
        <span class="layer-row-res" id="layerRes_Sat"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Sat" min="0" max="100" value="70" title="ESA opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Sat">70%</span>
      </div>

      <!-- Satellite imagery row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="Satellite imagery">🛰</span>
        <span class="layer-row-label">Sat Img</span>
        <span class="layer-row-res" id="layerRes_SatImg"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_SatImg" min="0" max="100" value="80" title="Sat imagery opacity">
        <span class="layer-row-pct" id="layerOpacityPct_SatImg">80%</span>
      </div>

      <!-- City Raster row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="City heights raster">🏙</span>
        <span class="layer-row-label">City ↑</span>
        <span class="layer-row-res" id="layerRes_CityRaster"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_CityRaster" min="0" max="100" value="70" title="City raster opacity">
        <span class="layer-row-pct" id="layerOpacityPct_CityRaster">70%</span>
      </div>

      <!-- City Vector overlay row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="City vector overlay">🏙</span>
        <span class="layer-row-label">City ⬡</span>
        <span class="layer-row-res" id="layerRes_CityOverlay"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_CityOverlay" min="0" max="100" value="85" title="City vector opacity">
        <span class="layer-row-pct" id="layerOpacityPct_CityOverlay">85%</span>
      </div>


      <!-- Hydrology row -->
      <div class="layer-row">
        <span class="layer-row-icon" title="River depression overlay">🌊</span>
        <span class="layer-row-label">Hydro</span>
        <span class="layer-row-res" id="layerRes_Hydrology"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Hydrology" min="0" max="100" value="80" title="Hydrology opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Hydrology">80%</span>
      </div>

      <!-- Composite DEM row — no load button -->
      <div class="layer-row">
        <span class="layer-row-icon" title="Composite DEM">★</span>
        <span class="layer-row-label">Composite</span>
        <span class="layer-row-res" id="layerRes_CompositeDem"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_CompositeDem" min="0" max="100" value="100" title="Composite DEM opacity">
        <span class="layer-row-pct" id="layerOpacityPct_CompositeDem">100%</span>
      </div>

    </div><!-- /layerRows -->

    <!-- Hidden legacy div — kept so _updateLayerOpacitySliders() doesn't error -->
    <div id="layerOpacitySliders" style="display:none;"></div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import { onMounted } from 'vue';

onMounted(() => {
    // Wire per-layer opacity sliders to window.setLayerOpacity
    const layers = ['Dem', 'Water', 'Sat', 'SatImg', 'CityRaster', 'CityOverlay', 'CompositeDem', 'Hydrology'];
    for (const mode of layers) {
        const slider = document.getElementById(`layerOpacity_${mode}`);
        const pct    = document.getElementById(`layerOpacityPct_${mode}`);
        if (slider && pct) {
            slider.addEventListener('input', () => {
                pct.textContent = slider.value + '%';
                window.setLayerOpacity?.(mode, Number(slider.value) / 100);
            });
        }
    }

    // Update res spans from linked inputs whenever values change
    function _syncResSpans() {
        const resMap: Record<string, string> = {
            Dem: 'paramDim', Water: 'waterResolution', Sat: 'esaResolution',
            SatImg: 'satImgResolution', CityRaster: 'cityRasterDim', Hydrology: 'hydroDim',
        };
        for (const [mode, inputId] of Object.entries(resMap)) {
            const inp = document.getElementById(inputId);
            const span = document.getElementById(`layerRes_${mode}`);
            if (!inp || !span) continue;
            span.textContent = inp.value ? inp.value + ' px' : '';
            inp.addEventListener('change', () => { span.textContent = inp.value ? inp.value + ' px' : ''; });
            inp.addEventListener('input', () => { span.textContent = inp.value ? inp.value + ' px' : ''; });
        }
    }
    // Defer slightly so fetch section inputs are mounted
    setTimeout(_syncResSpans, 200);
});
</script>
<style scoped>
.layer-row {
    display: grid;
  grid-template-columns: 22px 70px 46px minmax(80px, 1fr) 32px;
    gap: 0 6px;
    align-items: center;
    min-height: 28px;
}
.layer-row-icon {
  font-size: 13px;
  width: 20px;
  text-align: center;
  opacity: 0.9;
}
.layer-row-label {
    font-size: 10px;
    color: #aaa;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.layer-row-res {
    font-size: 9px;
    color: #666;
    white-space: nowrap;
}
.layer-row-opacity {
    width: 100%;
}
.layer-row-pct {
    font-size: 9px;
    color: #888;
    text-align: right;
    white-space: nowrap;
}
.layer-row-load {
    font-size: 11px;
    padding: 0 !important;
    width: 28px;
    min-width: 28px;
    max-width: 28px;
    height: 22px;
    box-sizing: border-box;
}
.layer-subrow {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 8px;
    padding: 2px 0 4px 32px;
    border-bottom: 1px solid #2a2a2a;
}
</style>

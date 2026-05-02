<template>
  <CollapsibleSection title="🗺️ Layers" :start-open="true" wrap-style="">

    <!-- Per-layer rows — one row per entry in _layerOrder -->
    <div id="layerRows" style="display:flex;flex-direction:column;gap:2px;">

      <!-- Dem row -->
      <div class="layer-row">
        <button class="layer-mode-btn active" data-mode="Dem" title="Base elevation">🏔</button>
        <span class="layer-row-label">DEM</span>
        <span class="layer-row-res" id="layerRes_Dem"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Dem" min="0" max="100" value="100" title="DEM opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Dem">100%</span>
      </div>

      <!-- Water row -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="Water" title="Water mask">💧</button>
        <span class="layer-row-label">Water</span>
        <span class="layer-row-res" id="layerRes_Water"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Water" min="0" max="100" value="70" title="Water opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Water">70%</span>
      </div>

      <!-- ESA row -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="Sat" title="ESA land cover">🌿</button>
        <span class="layer-row-label">ESA</span>
        <span class="layer-row-res" id="layerRes_Sat"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Sat" min="0" max="100" value="70" title="ESA opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Sat">70%</span>
      </div>

      <!-- Satellite imagery row -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="SatImg" title="Satellite imagery">🛰</button>
        <span class="layer-row-label">Sat Img</span>
        <span class="layer-row-res" id="layerRes_SatImg"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_SatImg" min="0" max="100" value="80" title="Sat imagery opacity">
        <span class="layer-row-pct" id="layerOpacityPct_SatImg">80%</span>
      </div>

      <!-- City Raster row -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="CityRaster" title="City heights raster">🏙</button>
        <span class="layer-row-label">City ↑</span>
        <span class="layer-row-res" id="layerRes_CityRaster"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_CityRaster" min="0" max="100" value="70" title="City raster opacity">
        <span class="layer-row-pct" id="layerOpacityPct_CityRaster">70%</span>
      </div>

      <!-- City Vector overlay row -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="CityOverlay" title="City vector overlay">🏙</button>
        <span class="layer-row-label">City ⬡</span>
        <span class="layer-row-res" id="layerRes_CityOverlay"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_CityOverlay" min="0" max="100" value="85" title="City vector opacity">
        <span class="layer-row-pct" id="layerOpacityPct_CityOverlay">85%</span>
      </div>
      <!-- City vector sub-row: feature toggles -->
      <div class="layer-subrow" id="cityOverlaySubrow">
        <label class="check-label" style="font-size:10px;" title="Toggle building polygons">
          <input type="checkbox" id="layerBuildingsToggle" checked> 🏠 Bldgs
        </label>
        <label class="check-label" style="font-size:10px;" title="Toggle road network">
          <input type="checkbox" id="layerRoadsToggle" checked> 🛣 Roads
        </label>
        <label class="check-label" style="font-size:10px;" title="Toggle waterway lines">
          <input type="checkbox" id="layerWaterwaysToggle" checked> 💧 Ways
        </label>
        <!-- Color swatches — referenced by city-overlay.js -->
        <div style="display:none;">
          <input type="color" id="layerBuildingsColor"  value="#c8b89a" class="city-color-swatch">
          <input type="color" id="layerRoadsColor"      value="#cc8844" class="city-color-swatch">
          <input type="color" id="layerWaterwaysColor"  value="#4488cc" class="city-color-swatch">
        </div>
      </div>

      <!-- Hydrology row -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="Hydrology" title="River depression overlay">🌊</button>
        <span class="layer-row-label">Hydro</span>
        <span class="layer-row-res" id="layerRes_Hydrology"></span>
        <input type="range" class="layer-row-opacity" id="layerOpacity_Hydrology" min="0" max="100" value="80" title="Hydrology opacity">
        <span class="layer-row-pct" id="layerOpacityPct_Hydrology">80%</span>
      </div>

      <!-- Composite DEM row — no load button -->
      <div class="layer-row">
        <button class="layer-mode-btn" data-mode="CompositeDem" title="Composite DEM">★</button>
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

    // Wire mode toggle buttons — clicking a row button calls setStackMode
    document.querySelectorAll('#layerRows .layer-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            window.setStackMode?.(btn.dataset.mode);
            // Sync button active states after toggle
            document.querySelectorAll('#layerRows .layer-mode-btn').forEach(b => {
                // setStackMode manages active class via _layerModeBtns — but those now
                // point to a different selector; update manually here too.
                b.classList.toggle('active', b === btn ? true : b.classList.contains('active'));
            });
        });
    });

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
    grid-template-columns: 30px 70px 46px minmax(80px, 1fr) 32px 30px;
    gap: 0 6px;
    align-items: center;
    min-height: 28px;
}
.layer-row .layer-mode-btn {
    padding: 0 !important;
    font-size: 13px;
    width: 28px;
    min-width: 28px;
    max-width: 28px;
    height: 24px;
    border-radius: 4px;
    border: 1px solid #444;
    background: #2a2a2a;
    color: #aaa;
    cursor: pointer;
    box-sizing: border-box;
}
.layer-row .layer-mode-btn.active {
    background: #1a4a6a;
    border-color: #4a9fd4;
    color: #fff;
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

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
      <select id="compositeColormap" class="ctrl-select composite-colormap-select" aria-label="Composite layer colormap"
              title="Colour scheme for the composite output. 'Same as DEM' follows the main DEM colormap.">
        <option value="inherit" selected>Same as DEM</option>
        <option value="rainbow">Rainbow</option>
        <option value="terrain">Terrain</option>
        <option value="viridis">Viridis</option>
        <option value="jet">Jet</option>
        <option value="hot">Hot</option>
        <option value="gray">Gray</option>
      </select>
    </div>

    <details class="composite-layer-group" open>
      <summary class="composite-layer-header">🏔 Base DEM</summary>
      <div class="composite-layer-body">
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeDemEnabled" checked aria-label="Enable base DEM contribution"> Include base elevation
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Weight</span>
          <input type="range" id="compositeDemWeight" min="0" max="2" value="1" step="0.1" aria-label="Composite DEM weight">
          <span id="compositeDemWeightLabel" class="composite-slider-value">1.0</span>
        </div>
        <canvas id="compositeHistDem" class="composite-histogram" width="240" height="20" title="Base DEM contribution distribution"></canvas>
      </div>
    </details>

    <details class="composite-layer-group">
      <summary class="composite-layer-header">💧 Water</summary>
      <div class="composite-layer-body">
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeWaterEnabled" checked aria-label="Enable water contribution"> Enable
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Depth</span>
          <input type="range" id="compositeWaterDepth" min="0" max="50" value="5" step="0.5" aria-label="Composite water depth">
          <span id="compositeWaterDepthLabel" class="composite-slider-value">5.0 m</span>
          <span class="composite-slider-label">Weight</span>
          <input type="range" id="compositeWaterWeight" min="0" max="5" value="1" step="0.1" aria-label="Composite water weight">
          <span id="compositeWaterWeightLabel" class="composite-slider-value">1.0</span>
        </div>
        <canvas id="compositeHistWater" class="composite-histogram" width="240" height="20" title="Water contribution distribution"></canvas>
      </div>
    </details>

    <details class="composite-layer-group">
      <summary class="composite-layer-header">🏙 City / OSM</summary>
      <div class="composite-layer-body">
        <div class="composite-footer-hint" style="margin:0 0 4px;">Regions &gt;10km fetch a coarser tier automatically (roads + water + large buildings only)</div>

        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeBuildingsEnabled" checked aria-label="Enable buildings contribution"> Buildings
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Scale</span>
          <input type="range" id="compositeBuildingScale" min="0" max="5" value="1" step="0.1" aria-label="Composite building scale">
          <span id="compositeBuildingScaleLabel" class="composite-slider-value">1.0</span>
        </div>
        <canvas id="compositeHistBuildings" class="composite-histogram" width="240" height="20" title="Buildings contribution distribution"></canvas>

        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeRoadsEnabled" checked aria-label="Enable roads contribution"> Roads
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Cut</span>
          <input type="range" id="compositeRoadCut" min="0" max="5" value="0.5" step="0.1" aria-label="Composite road cut depth">
          <span id="compositeRoadCutLabel" class="composite-slider-value">0.5 m</span>
        </div>
        <canvas id="compositeHistRoads" class="composite-histogram" width="240" height="20" title="Roads contribution distribution"></canvas>

        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeWaterwaysEnabled" checked aria-label="Enable waterways contribution"> Waterways
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Depth</span>
          <input type="range" id="compositeRiverDepth" min="0" max="20" value="3" step="0.5" aria-label="Composite river depth">
          <span id="compositeRiverDepthLabel" class="composite-slider-value">3.0 m</span>
        </div>
        <canvas id="compositeHistWaterways" class="composite-histogram" width="240" height="20" title="Waterways contribution distribution"></canvas>

        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeWallsEnabled" checked aria-label="Enable walls contribution"> Walls
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Scale</span>
          <input type="range" id="compositeWallScale" min="0" max="5" value="1" step="0.1" aria-label="Composite wall scale">
          <span id="compositeWallScaleLabel" class="composite-slider-value">1.0</span>
        </div>
        <canvas id="compositeHistWalls" class="composite-histogram" width="240" height="20" title="Walls contribution distribution"></canvas>
      </div>
    </details>

    <details class="composite-layer-group">
      <summary class="composite-layer-header">🌿 Land cover</summary>
      <div class="composite-layer-body">
        <div class="composite-sliders">
          <span class="composite-slider-label">Tree height</span>
          <input type="range" id="compositeTreeHeight" min="0" max="40" value="8" step="0.5" aria-label="Composite tree height">
          <span id="compositeTreeHeightLabel" class="composite-slider-value">8.0 m</span>
          <span class="composite-slider-label">Weight</span>
          <input type="range" id="compositeLandcoverWeight" min="0" max="5" value="0" step="0.1" aria-label="Composite landcover weight">
          <span id="compositeLandcoverWeightLabel" class="composite-slider-value">0.0</span>
        </div>
        <canvas id="compositeHistLandcover" class="composite-histogram" width="240" height="20" title="Land cover contribution distribution"></canvas>
      </div>
    </details>

    <details class="composite-layer-group">
      <summary class="composite-layer-header">🛰 Satellite vegetation</summary>
      <div class="composite-layer-body">
        <div class="composite-sliders">
          <span class="composite-slider-label">Veg height</span>
          <input type="range" id="compositeVegHeight" min="0" max="30" value="5" step="0.5" aria-label="Composite vegetation height">
          <span id="compositeVegHeightLabel" class="composite-slider-value">5.0 m</span>
          <span class="composite-slider-label">Weight</span>
          <input type="range" id="compositeSatWeight" min="0" max="5" value="0" step="0.1" aria-label="Composite satellite vegetation weight">
          <span id="compositeSatWeightLabel" class="composite-slider-value">0.0</span>
        </div>
        <canvas id="compositeHistSatellite" class="composite-histogram" width="240" height="20" title="Satellite vegetation contribution distribution"></canvas>
      </div>
    </details>

    <div class="composite-group-label">Combined composite output</div>
    <canvas id="compositeHistCombined" class="composite-histogram composite-histogram-combined" width="240" height="36" title="Final composite elevation distribution"></canvas>

    <div class="row-gap6 composite-actions">
      <button id="previewCompositeBtn" class="btn composite-action-btn"
              title="Recompute and preview the composite layer">👁 Preview</button>
      <button id="applyCompositeToDemBtn" class="btn btn-primary composite-action-btn"
              title="Replace current DEM with composite values">✓ Apply to DEM</button>
      <button id="splitViewToggleBtn" class="btn composite-action-btn"
              title="Show Composite DEM and satellite image side by side, synced pan/zoom">⬓ Split view</button>
    </div>
    <span id="compositeStats" class="composite-stats"></span>
    <div id="compositeContribStatus" class="composite-footer-hint"></div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>
<style scoped>
.composite-enable-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin: 4px 0;
}
.composite-colormap-select {
    flex: 0 0 auto;
    width: auto;
    font-size: 11px;
    padding: 2px 4px;
    height: 22px;
}

/* Collapsible per-layer group — mirrors FetchLayersSection.vue's <details> pattern */
.composite-layer-group {
    margin-top: 2px;
}
.composite-layer-header {
    font-size: 11px;
    color: #bbb;
    cursor: pointer;
    user-select: none;
    padding: 2px 6px;
    list-style: none;
    font-weight: 600;
    background: #232323;
    border-radius: 3px;
    border-left: 2px solid #4a9fd4;
    line-height: 1.4;
}
.composite-layer-header:hover { background: #2a2a2a; }
.composite-layer-header::-webkit-details-marker { display: none; }
.composite-layer-header::before {
    content: '▶';
    display: inline-block;
    font-size: 8px;
    margin-right: 4px;
    color: #888;
    transition: transform 0.15s;
}
details[open] > .composite-layer-header::before { transform: rotate(90deg); }
.composite-layer-body {
    padding: 3px 4px 1px 8px;
    display: flex;
    flex-direction: column;
    gap: 1px;
}
</style>

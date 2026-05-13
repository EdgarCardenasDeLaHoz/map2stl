<template>
  <CollapsibleSection title="🗂 Fetch Layers">

    <!-- ═══ DEM Source ═══ -->
    <details open>
      <summary class="fetch-section-header">🏔 DEM Source</summary>
      <div class="fetch-section-body">

        <div class="param-group">
          <label for="paramDemSource" title="Elevation data source.">Source</label>
          <select id="paramDemSource" class="ctrl-select">
            <option value="local">Local SRTM Tiles</option>
            <option value="SRTMGL1">OpenTopo — SRTM 30m</option>
            <option value="SRTMGL3">OpenTopo — SRTM 90m</option>
            <option value="AW3D30">OpenTopo — ALOS 30m</option>
            <option value="COP30">OpenTopo — Copernicus 30m</option>
            <option value="COP90">OpenTopo — Copernicus 90m</option>
            <option value="SRTM15Plus">OpenTopo — SRTM15+ Bathy</option>
          </select>
          <div id="demSourceApiKeyWarning" style="font-size:10px;color:#f90;display:none;">⚠️ OpenTopography API key not configured.</div>
        </div>

        <div class="param-group">
          <label for="paramDim" title="Number of grid points per side fetched from the DEM source.">Resolution</label>
          <input type="number" id="paramDim" value="600" min="50" max="2000" step="50">
          <div id="demResWarning" style="font-size:10px;color:#f90;display:none;">⚠️ High resolution may be slow</div>
        </div>

        <div class="fetch-inline-row">
          <label for="paramDepthScale" title="Vertical exaggeration of ocean/depth areas.">Depth</label>
          <input type="number" id="paramDepthScale" value="0.5" min="0" max="10" step="0.1" class="ctrl-input fetch-num-sm">
          <label for="paramWaterScale" title="How strongly to depress water areas (0–1).">Water</label>
          <input type="number" id="paramWaterScale" value="0.05" min="0" max="1" step="0.01" class="ctrl-input fetch-num-sm">
          <label class="check-label" title="Depress water-masked pixels.">
            <input type="checkbox" id="paramSubtractWater" checked aria-label="Subtract water from DEM"> Subtract
          </label>
        </div>

        <div class="fetch-action-row">
          <button id="loadDemBtn" class="btn btn-primary">🏔 Load DEM</button>
        </div>

      </div>
    </details>

    <!-- ═══ Hydrology ═══ -->
    <details>
      <summary class="fetch-section-header">🌊 Hydrology</summary>
      <div class="fetch-section-body">

        <div class="fetch-subsection-header">Water Mask</div>
        <div class="param-group">
          <label for="waterDataset" title="Source dataset for water detection.">Dataset</label>
          <select id="waterDataset" class="ctrl-select">
            <option value="esa" selected>ESA WorldCover</option>
            <option value="jrc">JRC Global Surface Water</option>
          </select>
        </div>
        <div class="param-group">
          <label for="waterResolution" title="Output resolution in pixels per side.">Resolution</label>
          <select id="waterResolution" class="ctrl-select">
            <option value="100">100 px — Very Fast</option>
            <option value="200">200 px — Fast</option>
            <option value="400">400 px — Medium</option>
            <option value="600" selected>600 px — Default</option>
            <option value="1200">1200 px — High Detail</option>
          </select>
          <div id="waterResWarning" style="font-size:10px;color:#f90;display:none;">⚠️ May require tiling for large areas</div>
        </div>

        <div class="fetch-subsection-header" style="margin-top:8px;">Hydrology</div>
        <div class="param-group">
          <label for="hydroSource" title="Data source for river network.">Source</label>
          <select id="hydroSource" class="ctrl-select">
            <option value="natural_earth">Natural Earth (coarse)</option>
            <option value="hydrorivers" selected>HydroRIVERS (~500m)</option>
          </select>
        </div>
        <div class="fetch-inline-row">
          <label for="hydroDim" title="Grid resolution in pixels per side.">Res</label>
          <input type="number" id="hydroDim" class="ctrl-input fetch-num-sm" value="600" min="50" max="2000" step="50">
          <label for="hydroDepressionM" title="Maximum river depression depth (m).">Dep&nbsp;(m)</label>
          <input type="number" id="hydroDepressionM" class="ctrl-input fetch-num-sm" value="-5.0" min="-100" max="0" step="0.5">
        </div>
        <div id="hydroRiversControls">
          <div class="fetch-inline-row">
            <label for="hydroMinOrder" title="Min Strahler order (1=all, 9=Amazon only).">Min&nbsp;ord</label>
            <input type="number" id="hydroMinOrder" class="ctrl-input fetch-num-sm" value="3" min="1" max="9" step="1">
            <label for="hydroOrderExponent" title="Depth exponent for smaller rivers.">Exp</label>
            <input type="number" id="hydroOrderExponent" class="ctrl-input fetch-num-sm" value="1.5" min="0.5" max="3.0" step="0.1">
            <label for="hydroWidthFactor" title="Multiplier on rendered river width (1.0=default, 2.0=double).">Width</label>
            <input type="number" id="hydroWidthFactor" class="ctrl-input fetch-num-sm" value="0.5" min="0.1" max="20" step="0.1">
          </div>
        </div>
        
        <!-- Unified Hydrology Load Button -->
        <div class="fetch-action-row">
          <button id="loadWaterHydrologyBtn" class="btn btn-primary" style="flex:1;">🌊 Load Hydrology</button>
          <button id="clearWaterHydrologyBtn" class="btn btn-secondary btn-clear">✕</button>
        </div>
        <div id="waterHydrologyStatus" class="fetch-status"></div>


      </div>
    </details>

    <!-- ═══ ESA Land Cover ═══ -->
    <details>
      <summary class="fetch-section-header">🌿 ESA Land Cover</summary>
      <div class="fetch-section-body">

        <div class="param-group">
          <label for="esaResolution" title="Output resolution in pixels per side for ESA WorldCover.">Resolution</label>
          <select id="esaResolution" class="ctrl-select">
            <option value="100">100 px — Very Fast</option>
            <option value="200">200 px — Fast</option>
            <option value="400">400 px — Medium</option>
            <option value="600" selected>600 px — Default</option>
            <option value="1200">1200 px — High Detail</option>
          </select>
        </div>
        <div class="fetch-action-row">
          <button id="loadEsaBtn" class="btn btn-secondary">🌿 Load ESA Land Cover</button>
        </div>

      </div>
    </details>

    <!-- ═══ Satellite Imagery ═══ -->
    <details>
      <summary class="fetch-section-header">🛰 Satellite Imagery</summary>
      <div class="fetch-section-body">

        <div class="fetch-help-text">Real satellite tiles from ESRI World Imagery (WMTS).</div>
        <div class="param-group">
          <label for="satImgResolution" title="Satellite image resolution (pixels per side).">Resolution</label>
          <select id="satImgResolution" class="ctrl-select">
            <option value="200">200 px</option>
            <option value="400">400 px</option>
            <option value="600" selected>600 px</option>
            <option value="800">800 px</option>
            <option value="1200">1200 px</option>
          </select>
        </div>
        <div class="fetch-action-row">
          <button id="loadSatImgBtn"  class="btn btn-secondary">🛰 Load</button>
          <button id="clearSatImgBtn" class="btn btn-secondary btn-clear">✕</button>
        </div>
        <div id="satImgStatus" class="fetch-status"></div>

      </div>
    </details>

    <!-- ═══ Cities ═══ -->
    <details>
      <summary class="fetch-section-header">🏙 Cities</summary>
      <div class="fetch-section-body">

        <div class="fetch-help-text" id="cityInfoRow">
          OSM buildings, roads, water (≤ 10 km regions).
        </div>

        <div class="param-group">
          <label for="cityRasterDim" title="Resolution of the city heights raster (pixels per side).">Raster res</label>
          <select id="cityRasterDim" class="ctrl-select">
            <option value="100">100 px — Fast</option>
            <option value="200" selected>200 px — Default</option>
            <option value="400">400 px — Detail</option>
            <option value="600">600 px — High</option>
          </select>
        </div>

        <label class="check-label" style="font-size:11px;margin:2px 0;" title="Burn slanted roof surfaces (gabled / hipped / pyramidal / skillion / dome) using OSM roof:shape tags. Visible at ≥400 px raster resolution. Slower than flat tops.">
          <input type="checkbox" id="cityRoofShapes" aria-label="Enable slanted city roof shapes"> 🏠 Slanted roofs
        </label>

        <div class="param-grid">
          <label for="citySimplifyTolerance" title="Polygon simplification tolerance in metres.">Tolerance (m)</label>
          <input type="number" id="citySimplifyTolerance" value="3" min="0" max="50" step="0.5" class="ctrl-input-sm">
          <label for="cityMinArea" title="Minimum building footprint in m².">Min area (m²)</label>
          <input type="number" id="cityMinArea" value="5" min="0" max="5000" step="5" class="ctrl-input-sm">
          <label for="cityMPerLevel" title="Floor-to-floor height in metres.">m / floor</label>
          <input type="number" id="cityMPerLevel" value="3.5" min="2.0" max="6.0" step="0.05" class="ctrl-input-sm">
        </div>

        <div class="fetch-action-row">
          <button id="loadCityDataBtn"  class="btn btn-primary">📥 Load Cities</button>
          <button id="clearCityDataBtn" class="btn btn-secondary btn-clear">✕</button>
        </div>
        <div id="cityDataStatus" class="fetch-status"></div>
        <div class="fetch-status" style="display:flex;gap:8px;">
          <span id="cityBuildingCount"  class="city-layer-count"></span>
          <span id="cityRoadCount"      class="city-layer-count"></span>
          <span id="cityWaterwayCount"  class="city-layer-count"></span>
        </div>

        <div class="fetch-action-row" style="margin-top:2px;">
           <button id="openCityTablePanelBtn" class="btn btn-secondary" @click="toggleCityTablePanel">📋 Toggle Buildings Table Panel</button>
        </div>

        <details class="nested-details">
          <summary class="nested-summary">3D Heights</summary>
          <div class="param-grid">
            <label for="cityBuildingScale" title="Building height scale: mm per real metre.">Bldg scale (mm/m)</label>
            <input type="number" id="cityBuildingScale" value="0.5" min="0" max="10" step="0.1" class="ctrl-input-sm">
            <label for="cityRoadDepression" title="Road depression relative to terrain (m).">Road dep (m)</label>
            <input type="number" id="cityRoadDepression" value="0.0" min="-10" max="2" step="0.5" class="ctrl-input-sm">
            <label for="cityWaterOffset" title="Waterway surface height relative to ground (m).">Water off (m)</label>
            <input type="number" id="cityWaterOffset" value="-2.0" min="-20" max="0" step="0.5" class="ctrl-input-sm">
          </div>
        </details>

        <div id="enhanceHeightsSection" style="border-top:1px solid #333;padding-top:6px;margin-top:6px;display:none;">
          <div class="fetch-subsection-header">Height Enhancement</div>
          <div class="fetch-action-row">
            <button id="enhanceHeightsBtn" class="btn btn-secondary" disabled>
              Enhance Heights (Google 3D)
            </button>
          </div>
          <div id="enhanceHeightsStatus" class="fetch-status"></div>
        </div>

      </div>
    </details>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';

function toggleCityTablePanel() {
  (window as any).toggleCityBuildingsPanel?.();
}
</script>
<style scoped>
/* Outer collapsible <details> sub-section header (e.g. "🏔 DEM Source") */
.fetch-section-header {
    font-size: 11px;
    color: #bbb;
    cursor: pointer;
    user-select: none;
    padding: 4px 6px;
    list-style: none;
    font-weight: 600;
    background: #232323;
    border-radius: 3px;
    border-left: 2px solid #4a9fd4;
}
.fetch-section-header:hover { background: #2a2a2a; }
.fetch-section-header::-webkit-details-marker { display: none; }
.fetch-section-header::before {
    content: '▶';
    display: inline-block;
    font-size: 8px;
    margin-right: 4px;
    color: #888;
    transition: transform 0.15s;
}
details[open] > .fetch-section-header::before { transform: rotate(90deg); }

.fetch-section-body {
    padding: 6px 4px 4px 8px;
    display: flex;
    flex-direction: column;
    gap: 4px;
}

/* Override the global .param-group's huge 15px margin and flex behavior in this panel. */
.fetch-section-body :deep(.param-group) {
    margin: 0;
    display: grid;
    grid-template-columns: 70px 1fr;
    align-items: center;
    gap: 4px 6px;
}
.fetch-section-body :deep(.param-group label) {
    font-size: 11px;
    font-weight: 400;
    color: #bbb;
    flex: none;
    margin: 0;
    white-space: nowrap;
}
.fetch-section-body :deep(.param-group select),
.fetch-section-body :deep(.param-group input[type="number"]),
.fetch-section-body :deep(.param-group input[type="text"]) {
    width: 100%;
    max-width: none;
    padding: 3px 6px;
    font-size: 11px;
    height: 24px;
    box-sizing: border-box;
}
/* Warnings and trailing notes span both columns */
.fetch-section-body :deep(.param-group > div[id$="Warning"]) {
    grid-column: 1 / -1;
}

/* Inline row for compact label/number/label/number layouts */
.fetch-inline-row {
    display: flex;
    align-items: center;
    gap: 4px 6px;
    flex-wrap: wrap;
}
.fetch-inline-row :deep(label) {
    font-size: 11px;
    color: #bbb;
    margin: 0;
    white-space: nowrap;
}
.fetch-num-sm {
    width: 56px !important;
    padding: 3px 6px !important;
    font-size: 11px !important;
    height: 24px !important;
    box-sizing: border-box;
}

/* Sub-section divider header (Water Mask / Hydrology) */
.fetch-subsection-header {
    font-size: 9px;
    color: #6aa;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-weight: 600;
    margin: 4px 0 2px 0;
    padding-bottom: 2px;
    border-bottom: 1px solid #2a2a2a;
}

/* Compact param-grid override: labels left-aligned, inputs right-aligned narrow */
.fetch-section-body :deep(.param-grid) {
    display: grid;
    grid-template-columns: 1fr 64px;
    align-items: center;
    gap: 3px 6px;
    font-size: 11px;
}
.fetch-section-body :deep(.param-grid label) {
    font-size: 11px;
    color: #bbb;
    margin: 0;
}
.fetch-section-body :deep(.ctrl-input-sm) {
    padding: 3px 6px !important;
    font-size: 11px !important;
    height: 22px !important;
    box-sizing: border-box;
    width: 100%;
}

/* Action button row (Load / Clear) — compact and tight */
.fetch-action-row {
    display: flex;
    gap: 4px;
    margin-top: 4px;
}
.fetch-action-row :deep(button) {
    flex: 1;
    font-size: 11px !important;
    padding: 4px 6px !important;
    height: 26px;
}
.fetch-action-row :deep(button.btn-clear) {
    flex: 0 0 auto;
}

/* Inline status text under buttons */
.fetch-status {
    font-size: 10px;
    color: #888;
    margin-top: 2px;
    min-height: 12px;
}

/* Layer-checkboxes row (Buildings/Roads/Waterways) */
.fetch-checkbox-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 10px;
    font-size: 11px;
    margin: 2px 0;
}
.fetch-checkbox-row :deep(label) {
    font-size: 11px;
    color: #bbb;
    margin: 0;
}

/* Help / hint text under section header */
.fetch-help-text {
    font-size: 10px;
    color: #888;
    margin: 0 0 2px 0;
    line-height: 1.3;
}

/* Nested <details> inside a fetch section (e.g. "Land Cover Classes", "3D Heights") */
.nested-details {
    margin-top: 4px;
}
.nested-summary {
    font-size: 10px;
    color: #888;
    cursor: pointer;
    user-select: none;
    padding: 2px 4px;
    list-style: none;
}
.nested-summary::-webkit-details-marker { display: none; }
.nested-summary::before {
    content: '▶';
    display: inline-block;
    font-size: 7px;
    margin-right: 4px;
    color: #666;
    transition: transform 0.15s;
}
.nested-details[open] > .nested-summary::before { transform: rotate(90deg); }

/* Spacing between top-level <details> sub-sections */
.fetch-section-header { margin-top: 4px; }
details:first-of-type > .fetch-section-header { margin-top: 0; }
</style>

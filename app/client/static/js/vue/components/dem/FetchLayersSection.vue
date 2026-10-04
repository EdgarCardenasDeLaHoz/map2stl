<template>
  <!-- Per-layer resolutions are numbers (F-EDITPANEL): each follows #paramDim unless the
       layer has its own (LayerSettings.vue), so a fixed list of sizes no longer fits. -->
  <CollapsibleSection title="🗂 Fetch Layers">

    <!-- ═══ DEM Source ═══ -->
    <details open>
      <summary class="fetch-section-header">🏔 DEM Source</summary>
      <div class="fetch-section-body">

        <div class="param-group">
          <label for="paramDemSource" title="Elevation data source.">Source</label>
          <!-- Replaced at load by populateDemSources(), which asks the server which
               sources actually work and lands on the first usable one. This list is
               the fallback if that call fails, so h5_local leads: it needs no API key
               and no tile folder. The three common sources come first; the rest sit
               under "More sources" (same split as PRIMARY_DEM_SOURCES in dem-main.js). -->
          <select id="paramDemSource" class="ctrl-select">
            <option value="h5_local">Local SRTM H5 (City-scale, ~90m)</option>
            <option value="SRTMGL1">SRTM 30m (Global)</option>
            <option value="COP30">Copernicus DSM 30m</option>
            <optgroup label="More sources">
              <option value="local">Local SRTM Tiles</option>
              <option value="SRTMGL3">SRTM 90m (Global)</option>
              <option value="AW3D30">ALOS World 3D 30m</option>
              <option value="COP90">Copernicus DSM 90m</option>
              <option value="SRTM15Plus">SRTM15+ (Bathymetry+Land)</option>
            </optgroup>
          </select>
          <div id="demSourceApiKeyWarning" style="font-size:11px;color:#f90;display:none;">⚠️ OpenTopography API key not configured.</div>
        </div>

        <div class="param-group">
          <label for="paramDim" title="Number of grid points per side fetched from the DEM source.">Resolution</label>
          <input type="number" id="paramDim" value="600" min="50" max="2000" step="50">
          <div id="demResWarning" style="font-size:11px;color:#f90;display:none;">⚠️ High resolution may be slow</div>
          <DemSamplingInfo />
        </div>

        <!-- Depth / Water / Subtract only shape the 3D terrain: Composite → Base DEM. -->

        <div class="fetch-help-text">Load with the 🏔 Load DEM button at the top of this panel.</div>

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
          <input type="number" id="waterResolution" class="ctrl-input" value="600" min="50" max="4000" step="50">
          <div id="waterResWarning" style="font-size:11px;color:#f90;display:none;">⚠️ May require tiling for large areas</div>
        </div>

        <div class="fetch-subsection-header" style="margin-top:8px;">Hydrology</div>
        <div class="param-group">
          <label for="hydroSource" title="Data source for river network.">Source</label>
          <select id="hydroSource" class="ctrl-select">
            <option value="natural_earth">Natural Earth (coarse)</option>
            <option value="hydrorivers" selected>HydroRIVERS (~500m)</option>
          </select>
        </div>
        <!-- One river source of truth: the Composite carve reads these three
             (composite-dem.js::_syncRiverParamsFromHydrology). The preview is
             carved on the loaded DEM's grid with Composite → Depth ×, so it
             has no resolution or depth of its own. Natural Earth rivers get a
             pseudo order from scalerank, so Min ord applies to both sources. -->
        <div class="fetch-inline-row">
          <label for="hydroMinOrder" title="Min Strahler order (1=all, 9=Amazon only).">Min&nbsp;ord</label>
          <input type="number" id="hydroMinOrder" class="ctrl-input fetch-num-sm" value="3" min="1" max="9" step="1">
          <label for="hydroWidthFactor" title="× river width from discharge (min 1 px)">Width&nbsp;×</label>
          <input type="number" id="hydroWidthFactor" class="ctrl-input fetch-num-sm" value="1.0" min="0.5" max="10" step="0.5">
        </div>
        <!-- Colour mode + order legend are display-only: View → Hydrology Display. -->

        <!-- Unified Hydrology Load Button -->
        <div class="fetch-action-row">
          <button id="loadWaterHydrologyBtn" class="btn btn-primary" style="flex:1;">🌊 Load Hydrology</button>
          <button id="clearWaterHydrologyBtn" class="btn btn-secondary btn-clear" aria-label="Clear water and hydrology layers" title="Clear water and hydrology layers">✕</button>
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
          <input type="number" id="esaResolution" class="ctrl-input" value="600" min="50" max="4000" step="50">
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
          <input type="number" id="satImgResolution" class="ctrl-input" value="600" min="50" max="4000" step="50">
        </div>
        <div class="fetch-action-row">
          <button id="loadSatImgBtn"  class="btn btn-secondary">🛰 Load</button>
          <button id="clearSatImgBtn" class="btn btn-secondary btn-clear" aria-label="Clear satellite imagery layer" title="Clear satellite imagery layer">✕</button>
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
          <input type="number" id="cityRasterDim" class="ctrl-input" value="200" min="50" max="4000" step="50">
        </div>

        <!-- Colormap: View → City Polygon Display. Slanted roofs and the 3D
             heights (scale / road / water offsets): Composite → City / OSM. -->

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
          <button id="clearCityDataBtn" class="btn btn-secondary btn-clear" aria-label="Clear city data layer" title="Clear city data layer">✕</button>
        </div>
        <div id="cityDataStatus" class="fetch-status"></div>
        <CityFetchProgress />
        <div class="fetch-status" style="display:flex;gap:8px;">
          <span id="cityBuildingCount"  class="city-layer-count"></span>
          <span id="cityRoadCount"      class="city-layer-count"></span>
          <span id="cityWaterwayCount"  class="city-layer-count"></span>
        </div>

        <div class="fetch-action-row" style="margin-top:2px;">
           <button id="openCityTablePanelBtn" class="btn btn-secondary" @click="toggleCityTablePanel">📋 Toggle Buildings Table Panel</button>
        </div>

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

    <!-- ═══ Trails ═══ -->
    <details>
      <summary class="fetch-section-header">🥾 Trails</summary>
      <div class="fetch-section-body">

        <div class="fetch-help-text">Ski pistes and hiking paths. OSM covers both worldwide; the US Forest Service adds hiking trails inside the United States.</div>

        <div class="param-group">
          <label for="trailsSource" title="Data source for trail geometry.">Source</label>
          <select id="trailsSource" class="ctrl-select">
            <option value="all" selected>All sources</option>
            <option value="osm">OpenStreetMap (ski + hiking)</option>
            <option value="usfs">US Forest Service (hiking, US only)</option>
          </select>
        </div>
        <div class="fetch-inline-row">
          <label for="trailsDim" title="Grid resolution in pixels per side.">Res</label>
          <input type="number" id="trailsDim" class="ctrl-input fetch-num-sm" value="600" min="50" max="2000" step="50">
          <label for="trailsWidthM" title="Rendered trail width in metres (floored at two pixels).">Width&nbsp;(m)</label>
          <input type="number" id="trailsWidthM" class="ctrl-input fetch-num-sm" value="8" min="1" max="500" step="1">
        </div>
        <!-- Relief (m) shapes only the 3D carve: Composite → Trails.
             Both categories are always fetched in one request, so which of them
             is drawn is a display choice and lives under Trails Display in the
             View tab, not here. -->

        <div class="fetch-action-row">
          <button id="loadTrailsBtn" class="btn btn-primary" style="flex:1;">🥾 Load Trails</button>
          <button id="clearTrailsBtn" class="btn btn-secondary btn-clear" aria-label="Clear trails layer" title="Clear trails layer">✕</button>
        </div>
        <div id="trailsStatus" class="fetch-status"></div>

      </div>
    </details>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import CityFetchProgress from './CityFetchProgress.vue';
import DemSamplingInfo from './DemSamplingInfo.vue';

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
    content: '▸';
    display: inline-block;
    font-size: 11px;
    margin-right: 4px;
    color: var(--text-dim);
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
/* Warnings and trailing notes (every div child: #demResWarning, DemSamplingInfo's
   #demSamplingInfo, ...) span both columns. Matching only ids ending in "Warning"
   left the DEM sampling note in the 70 px label column, wrapping every word. */
.fetch-section-body :deep(.param-group > div) {
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
    font-size: 11px;
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
    font-size: 11px;
    color: var(--text-dim);
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
    font-size: 11px;
    color: var(--text-dim);
    margin: 0 0 2px 0;
    line-height: 1.3;
}

/* Nested <details> inside a fetch section (e.g. "Land Cover Classes", "3D Heights") */
.nested-details {
    margin-top: 4px;
}
.nested-summary {
    font-size: 11px;
    color: var(--text-dim);
    cursor: pointer;
    user-select: none;
    padding: 2px 4px;
    list-style: none;
}
.nested-summary::-webkit-details-marker { display: none; }
.nested-summary::before {
    content: '▸';
    display: inline-block;
    font-size: 11px;
    margin-right: 4px;
    color: var(--text-dim);
    transition: transform 0.15s;
}
.nested-details[open] > .nested-summary::before { transform: rotate(90deg); }

/* Spacing between top-level <details> sub-sections */
.fetch-section-header { margin-top: 4px; }
details:first-of-type > .fetch-section-header { margin-top: 0; }
</style>

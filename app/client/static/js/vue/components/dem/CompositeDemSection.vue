<template>
  <CollapsibleSection title="📊 Composite DEM — Output" :start-open="true"
                      header-title="Final height map that feeds into Extrude. Adjust per-layer contributions below.">
    <div class="composite-info-box">
      Pipeline: DEM → <span class="composite-pipe-water">− water</span> → <span class="composite-pipe-landcover">+ land cover</span> → <span class="composite-pipe-veg">+ vegetation</span> → <strong class="composite-pipe-output">Composite terrain → Extrude</strong>
      (<span class="composite-pipe-city">city</span>: 2D preview only)
    </div>
    <div class="composite-enable-row">
      <label class="check-label composite-enable-label">
        <input type="checkbox" id="compositeEnabled" checked aria-label="Enable composite DEM layer"> Enable composite layer
      </label>
      <!-- The composite colormap is display-only: View → Composite Display. -->
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
        <!-- Server-side DEM request params that only shape the 3D terrain (moved
             from Fetch → DEM Source). event-listeners-map.js copies them into
             appState.demParams by id; they take effect on the next Load DEM. -->
        <div class="composite-subhead">DEM request (next Load DEM)</div>
        <div class="composite-num-row">
          <label for="paramDepthScale" title="Vertical exaggeration of ocean/depth areas.">Depth</label>
          <input type="number" id="paramDepthScale" value="0.5" min="0" max="10" step="0.1" class="ctrl-input composite-num">
          <label for="paramWaterScale" title="How strongly to depress water areas (0–1).">Water</label>
          <input type="number" id="paramWaterScale" value="0.05" min="0" max="1" step="0.01" class="ctrl-input composite-num">
          <label class="check-label" title="Depress water-masked pixels.">
            <input type="checkbox" id="paramSubtractWater" checked aria-label="Subtract water from DEM"> Subtract
          </label>
        </div>
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
      <summary class="composite-layer-header">🌊 Rivers &amp; lakes</summary>
      <div class="composite-layer-body">
        <!-- Terrain channels (F-REGION, geo2stl/water_layers.py): depths relative
             to the ground, computed server-side and carved after the export's
             median filter, so they are part of Apply to DEM and every export. -->
        <div class="composite-footer-hint" style="margin:0 0 4px;">
          Carved relative to the ground: channel width and depth follow discharge
          (or stream order), never narrower than one pixel. Lakes (OSM) are cut flat
          below their lowest shore. River source, min order and width are set in
          Fetch → Hydrology (the hydrology preview shows this same carve).
        </div>
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeRiversEnabled" aria-label="Enable rivers contribution"> Rivers
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Depth ×</span>
          <input type="range" id="compositeRiverDepthScale" min="0" max="20" value="1" step="0.5" aria-label="River depth scale"
                 title="× channel depth from discharge (also scales the hydrology preview)">
          <span id="compositeRiverDepthScaleLabel" class="composite-slider-value">1.0</span>
        </div>
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeLakesEnabled" aria-label="Enable lakes contribution"> Lakes
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Depth</span>
          <input type="range" id="compositeLakeDepth" min="0.5" max="50" value="2" step="0.5" aria-label="Lake depth below shore">
          <span id="compositeLakeDepthLabel" class="composite-slider-value">2.0 m</span>
          <span class="composite-slider-label">Min area</span>
          <input type="range" id="compositeLakeMinAreaHa" min="0.5" max="100" value="1" step="0.5" aria-label="Minimum lake area in hectares">
          <span id="compositeLakeMinAreaHaLabel" class="composite-slider-value">1.0 ha</span>
        </div>
        <canvas id="compositeHistHydro" class="composite-histogram" width="240" height="20" title="Rivers and lakes contribution distribution"></canvas>
      </div>
    </details>

    <details class="composite-layer-group">
      <summary class="composite-layer-header">🏙 City / OSM <span class="composite-2d-only">2D preview only</span></summary>
      <div class="composite-layer-body">
        <!-- Two-stage mesh pipeline (docs/plans/active/F-ARCH-consolidation.md): the
             3D model takes these features from the City Model's vector stage,
             so Apply to DEM and every export leave these channels out of the
             terrain — otherwise buildings would print twice. -->
        <div class="composite-footer-hint composite-2d-note">
          In 3D, buildings, roads, waterways and walls come from the City Model
          layers (Extrude tab). These toggles only change the 2D preview; Apply
          to DEM and exports leave them out of the terrain.
        </div>
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

        <!-- City heights-raster params (moved from Fetch → Cities → 3D Heights).
             Read by city-render.js / presets.js by id; they take effect on the
             next Load Cities. -->
        <div class="composite-subhead">City heights raster (next Load Cities)</div>
        <div class="composite-num-grid">
          <label for="cityBuildingScale" title="Building height scale: mm per real metre.">Bldg scale (mm/m)</label>
          <input type="number" id="cityBuildingScale" value="0.5" min="0" max="10" step="0.1" class="ctrl-input composite-num">
          <label for="cityRoadDepression" title="Road depression relative to terrain (m).">Road dep (m)</label>
          <input type="number" id="cityRoadDepression" value="0.0" min="-10" max="2" step="0.5" class="ctrl-input composite-num">
          <label for="cityWaterOffset" title="Waterway surface height relative to ground (m).">Water off (m)</label>
          <input type="number" id="cityWaterOffset" value="-2.0" min="-20" max="0" step="0.5" class="ctrl-input composite-num">
        </div>
        <label class="composite-toggle-row" title="Burn slanted roof surfaces (gabled / hipped / pyramidal / skillion / dome) using OSM roof:shape tags. Visible at ≥400 px raster resolution. Slower than flat tops.">
          <input type="checkbox" id="cityRoofShapes" aria-label="Enable slanted city roof shapes"> 🏠 Slanted roofs
        </label>
      </div>
    </details>

    <details class="composite-layer-group">
      <summary class="composite-layer-header">🌿 Land cover</summary>
      <div class="composite-layer-body">
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeLandcoverEnabled" checked aria-label="Enable land cover contribution"> Enable
        </label>
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
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeSatEnabled" checked aria-label="Enable satellite vegetation contribution"> Enable
        </label>
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

    <details class="composite-layer-group">
      <summary class="composite-layer-header">🥾 Trails</summary>
      <div class="composite-layer-body">
        <!-- Uses the relief already rasterized by the Trails layer, so the sign
             and depth come from the Trails fetch section. Weight starts at 0:
             having trails loaded to look at must not silently carve them into
             an export. -->
        <div class="composite-footer-hint" style="margin:0 0 4px;">Load the Trails layer first — depth comes from Relief (m) below, applied on the next Load Trails</div>
        <div class="composite-num-row">
          <label for="trailsReliefM" title="Signed trail relief in metres; negative engraves the trail into the terrain. Used by the next Load Trails.">Relief&nbsp;(m)</label>
          <input type="number" id="trailsReliefM" class="ctrl-input composite-num" value="-2.0" min="-100" max="100" step="0.5">
        </div>

        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeTrailsEnabled" checked aria-label="Enable trails contribution"> Enable
        </label>
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeTrailsSkiEnabled" checked aria-label="Enable ski piste contribution"> Ski pistes
        </label>
        <label class="composite-toggle-row">
          <input type="checkbox" id="compositeTrailsHikingEnabled" checked aria-label="Enable hiking path contribution"> Hiking paths
        </label>
        <div class="composite-sliders">
          <span class="composite-slider-label">Weight</span>
          <input type="range" id="compositeTrailsWeight" min="0" max="5" value="0" step="0.1" aria-label="Composite trails weight">
          <span id="compositeTrailsWeightLabel" class="composite-slider-value">0.0</span>
        </div>
        <canvas id="compositeHistTrails" class="composite-histogram" width="240" height="20" title="Trails contribution distribution"></canvas>
      </div>
    </details>

    <div class="composite-group-label">Combined composite output</div>
    <canvas id="compositeHistCombined" class="composite-histogram composite-histogram-combined" width="240" height="36" title="Final composite elevation distribution"></canvas>

    <div class="row-gap6 composite-actions">
      <button id="previewCompositeBtn" class="btn composite-action-btn"
              title="Recompute and preview the composite layer">👁 Preview</button>
      <button id="applyCompositeToDemBtn" class="btn btn-primary composite-action-btn"
              title="Replace current DEM with the composite terrain (City / OSM channels excluded — they come from the City Model in 3D)">✓ Apply to DEM</button>
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
.composite-subhead {
    font-size: 9px;
    color: #6aa;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-weight: 600;
    margin: 6px 0 2px;
    padding-bottom: 2px;
    border-bottom: 1px solid #2a2a2a;
}
.composite-num-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 6px;
    font-size: 11px;
}
.composite-num-grid {
    display: grid;
    grid-template-columns: 1fr 64px;
    align-items: center;
    gap: 3px 6px;
    font-size: 11px;
}
.composite-num-row label,
.composite-num-grid label {
    font-size: 11px;
    color: #bbb;
    margin: 0;
    white-space: nowrap;
}
.composite-num {
    width: 56px;
    padding: 3px 6px;
    font-size: 11px;
    height: 22px;
    box-sizing: border-box;
}
.composite-num-grid .composite-num { width: 100%; }

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
    color: var(--text-dim);
    transition: transform 0.15s;
}
details[open] > .composite-layer-header::before { transform: rotate(90deg); }
.composite-2d-only {
    font-size: 9px;
    font-weight: 400;
    color: #d9a441;
    margin-left: 4px;
}
.composite-2d-note {
    margin: 0 0 4px;
    color: #d9a441;
}
.composite-layer-body {
    padding: 3px 4px 1px 8px;
    display: flex;
    flex-direction: column;
    gap: 1px;
}
</style>

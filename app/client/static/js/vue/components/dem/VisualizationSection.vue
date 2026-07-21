<template>
  <CollapsibleSection title="🖼 Canvas" :start-open="false" wrap-style="margin-top:0;">
    <div class="row-gap6" style="margin-bottom:10px;">
      <button id="layersStripBtn" class="dem-strip-btn active" data-subtab="layers"
              style="flex:1;font-size:11px;padding:5px 8px;"
              title="Switch the canvas to the stacked layers view">
        📚 Layers
        <span class="strip-status-dots">
          <span class="strip-dot" id="stripDotDem"></span>
          <span class="strip-dot" id="stripDotWater"></span>
          <span class="strip-dot" id="stripDotLandCover"></span>
          <span class="strip-dot" id="stripDotCities"></span>
        </span>
      </button>
      <button id="compareStripBtn" class="dem-strip-btn" data-subtab="compare"
              style="flex:1;font-size:11px;padding:5px 8px;"
              title="Switch the canvas to the compare panes view">⚖ Compare</button>
    </div>

    <div style="margin-top:8px;padding-top:8px;border-top:1px solid #333;">
      <div style="display:grid;grid-template-columns:auto 1fr 32px;gap:4px 6px;align-items:center;">
        <label class="check-label" style="white-space:nowrap;" title="Show lat/lon gridlines overlay">
          <input type="checkbox" id="showGridlines" checked aria-label="Show DEM gridlines"> 📐 Gridlines
        </label>
        <select id="gridlineCount" title="Grid line density" aria-label="Grid line density"
                style="width:100%;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;">
          <option value="3">Sparse (3)</option>
          <option value="5">Normal (5)</option>
          <option value="10" selected>Dense (10)</option>
          <option value="20">Very Dense (20)</option>
        </select>
        <button id="gridPixelModeBtn" title="Toggle pixel coordinates"
                style="padding:2px 5px;font-size:10px;background:#404040;color:#aaa;border:1px solid #555;border-radius:3px;cursor:pointer;white-space:nowrap;">px</button>
      </div>
      <div style="display:grid;grid-template-columns:auto 1fr auto;gap:4px 6px;align-items:center;margin-top:6px;">
        <label class="check-label" style="white-space:nowrap;" title="Grid spacing mode. 'Meters' draws a constant real-world-distance section grid so projection distortion of real space is visible.">Grid units</label>
        <select id="gridUnitMode" title="Degrees = lat/lon graticule. Meters = constant real-distance section grid (shows real-space distortion)."
                style="width:100%;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;">
          <option value="degrees" selected>Degrees (lat/lon)</option>
          <option value="meters">Meters (real-space section grid)</option>
        </select>
        <input type="color" id="gridLineColor" value="#000000" title="Grid line colour (default black)"
               style="width:28px;height:22px;padding:0;border:1px solid #555;border-radius:3px;background:#404040;cursor:pointer;">
      </div>
      <div id="gridMetersSpacingRow" class="hidden" style="display:grid;grid-template-columns:auto 1fr;gap:4px 6px;align-items:center;margin-top:4px;">
        <label style="white-space:nowrap;font-size:11px;color:#ccc;" title="Distance between section-grid lines, in kilometres.">Spacing (km)</label>
        <select id="gridMetersSpacing" style="width:100%;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;">
          <option value="1">1 km</option>
          <option value="2">2 km</option>
          <option value="5" selected>5 km</option>
          <option value="10">10 km</option>
          <option value="25">25 km</option>
          <option value="50">50 km</option>
          <option value="100">100 km</option>
        </select>
      </div>
      <!-- Pixel grid: independent red grid in DEM pixel space (shows raster dims) -->
      <div style="display:grid;grid-template-columns:auto 1fr auto;gap:4px 6px;align-items:center;margin-top:8px;padding-top:8px;border-top:1px solid #333;">
        <label class="check-label" style="white-space:nowrap;" title="Overlay a grid in DEM pixel space (independent of the geographic grid). Shows the raster's pixel dimensions.">
          <input type="checkbox" id="showPixelGrid" checked aria-label="Show pixel grid"> 🟥 Pixel grid
        </label>
        <div style="display:flex;align-items:center;gap:4px;">
          <span style="font-size:11px;color:#ccc;white-space:nowrap;">every</span>
          <input type="number" id="pixelGridSpacing" value="100" min="10" max="100000" step="10"
                 title="Pixel grid spacing (DEM pixels between lines). Default 100."
                 style="width:100%;min-width:52px;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;">
          <span style="font-size:11px;color:#ccc;">px</span>
        </div>
        <input type="color" id="pixelGridColor" value="#ff0000" title="Pixel grid colour (default red)"
               style="width:28px;height:22px;padding:0;border:1px solid #555;border-radius:3px;background:#404040;cursor:pointer;">
      </div>
      <div id="pixelGridDimsLabel" class="hidden" style="font-size:11px;color:#ff6b6b;margin-top:3px;text-align:right;font-weight:600;"></div>

      <div id="demPixelSizeLabel" class="hidden" style="font-size:10px;color:#8af;margin-top:3px;text-align:right;"></div>
    </div>
  </CollapsibleSection>

  <CollapsibleSection title="🏔 DEM" :start-open="false">
    <div class="param-group">
      <label for="demColormap" title="Colour scheme applied to elevation data.">Colormap:</label>
      <select id="demColormap">
        <option value="rainbow" selected>Rainbow</option>
        <option value="terrain">Terrain</option>
        <option value="viridis">Viridis</option>
        <option value="jet">Jet</option>
        <option value="hot">Hot</option>
        <option value="gray">Gray</option>
      </select>
    </div>

    <div class="param-group row-gap6" style="flex-wrap:nowrap;margin-top:6px;">
      <label title="Elevation range (metres) mapped to the colormap" style="white-space:nowrap;font-size:12px;">Elev<br>Range:</label>
      <input type="number" id="rescaleMin" placeholder="Min" aria-label="Minimum elevation for color range"
             style="width:55px;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;"
             title="Minimum elevation (m)">
      <span style="color:#888;font-size:11px;">to</span>
      <input type="number" id="rescaleMax" placeholder="Max" aria-label="Maximum elevation for color range"
             style="width:55px;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;"
             title="Maximum elevation (m)">
      <button id="applyRescaleBtn" class="btn btn-secondary" style="padding:2px 6px;font-size:11px;" title="Apply min/max range">Apply</button>
      <button id="resetRescaleBtn" class="btn btn-secondary" style="padding:2px 6px;font-size:11px;" title="Auto-fit to data range">Auto</button>
      <label class="check-label" title="Auto-fit color range on each update" style="font-size:11px;color:#aaa;white-space:nowrap;">
        <input type="checkbox" id="autoRescale" checked aria-label="Auto fit elevation range"> Auto
      </label>
    </div>

    <CollapsibleSection title="📊 Histogram" :start-open="false">
      <div id="histogram"></div>
    </CollapsibleSection>

    <!-- IMPORTANT: #curveCanvas must never be unmounted — curve-editor.js holds a direct ref -->
    <CollapsibleSection title="📈 Curve Editor" :start-open="false">
      <div class="curve-editor">
        <div class="curve-canvas-container" title="Left-click to add · Drag to move · Right-click to delete">
          <canvas id="curveCanvas"></canvas>
        </div>
        <div class="curve-presets" style="margin-top:6px;">
          <button data-preset="linear" class="active" title="Straight 1:1 mapping">Linear</button>
          <button data-preset="enhance-peaks" title="Boost contrast at high elevations">Peaks</button>
          <button data-preset="compress-depths" title="Compress low-elevation range">Depths</button>
          <button data-preset="s-curve" title="S-shaped curve">S-Curve</button>
        </div>
      </div>
      <div class="curve-actions-row">
        <div style="display:flex;gap:4px;flex-wrap:wrap;">
          <button id="undoCurveBtn" class="btn btn-secondary" style="padding:4px 8px;font-size:12px;" title="Undo (Ctrl+Z)" disabled>⟵ Undo</button>
          <button id="redoCurveBtn" class="btn btn-secondary" style="padding:4px 8px;font-size:12px;" title="Redo (Ctrl+Y)" disabled>Redo ⟶</button>
        </div>
        <div style="display:flex;gap:4px;flex-wrap:wrap;">
          <button id="applyCurveBtn" class="btn btn-primary" style="padding:4px 8px;font-size:12px;" title="Apply the elevation curve">✓ Apply</button>
          <button id="resetCurveBtn" class="btn btn-secondary" style="padding:4px 8px;font-size:12px;" title="Reset to linear">↺ Reset</button>
          <button id="seaLevelBufferBtn" class="btn btn-secondary" style="padding:4px 8px;font-size:12px;" title="Insert sea-level shelf">🌊 Sea</button>
        </div>
      </div>
    </CollapsibleSection>
  </CollapsibleSection>

  <CollapsibleSection title="🎨 Land Use Cover" :start-open="false">
    <div id="landCoverLegend" class="landcover-legend"></div>
    <div class="landcover-actions">
      <button id="applyLandCoverMapping" class="btn btn-secondary">Apply Colors</button>
      <button id="resetLandCoverMapping" class="btn btn-secondary">Reset</button>
    </div>
  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>
<style scoped>
.curve-actions-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  gap: 4px;
}

.landcover-legend {
  overflow-y: auto;
  max-height: 240px;
}

.landcover-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
</style>

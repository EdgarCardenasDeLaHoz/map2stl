<template>
  <CollapsibleSection title="🎨 Visualization">

    <!-- view.colormap -->
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

    <!-- view.rescale_min / rescale_max -->
    <div class="param-group row-gap6" style="flex-wrap:nowrap;margin-top:6px;">
      <label title="Elevation range (metres) mapped to the colormap" style="white-space:nowrap;font-size:12px;">Elev<br>Range:</label>
      <input type="number" id="rescaleMin" placeholder="Min"
             style="width:55px;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;"
             title="Minimum elevation (m)">
      <span style="color:#888;font-size:11px;">to</span>
      <input type="number" id="rescaleMax" placeholder="Max"
             style="width:55px;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;"
             title="Maximum elevation (m)">
      <button id="applyRescaleBtn" class="btn btn-secondary" style="padding:2px 6px;font-size:11px;" title="Apply min/max range">Apply</button>
      <button id="resetRescaleBtn" class="btn btn-secondary" style="padding:2px 6px;font-size:11px;" title="Auto-fit to data range">Auto</button>
      <label class="check-label" title="Auto-fit color range on each update" style="font-size:11px;color:#aaa;white-space:nowrap;">
        <input type="checkbox" id="autoRescale" checked> Auto
      </label>
    </div>

    <!-- view.gridlines_show / gridlines_count — rendered on the DEM canvas -->
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid #333;">
      <div style="display:grid;grid-template-columns:auto 1fr 32px;gap:4px 6px;align-items:center;">
        <label class="check-label" style="white-space:nowrap;" title="Show lat/lon gridlines overlay">
          <input type="checkbox" id="showGridlines" checked> 📐 Gridlines
        </label>
        <select id="gridlineCount" title="Grid line density"
                style="width:100%;background:#404040;color:#ccc;border:1px solid #555;padding:3px;border-radius:3px;font-size:11px;">
          <option value="3">Sparse (3)</option>
          <option value="5">Normal (5)</option>
          <option value="10" selected>Dense (10)</option>
          <option value="20">Very Dense (20)</option>
        </select>
        <button id="gridPixelModeBtn" title="Toggle pixel coordinates"
                style="padding:2px 5px;font-size:10px;background:#404040;color:#aaa;border:1px solid #555;border-radius:3px;cursor:pointer;white-space:nowrap;">px</button>
      </div>
      <div id="demPixelSizeLabel" style="display:none;font-size:10px;color:#8af;margin-top:3px;text-align:right;"></div>
    </div>

    <!-- view.elevation_curve / elevation_curve_points — curve editor canvas -->
    <!-- IMPORTANT: #curveCanvas must never be unmounted — curve-editor.js holds a direct ref -->
    <div style="margin-top:10px;padding-top:8px;border-top:1px solid #333;">
      <div style="font-size:11px;color:#888;margin-bottom:6px;">Elevation Curve</div>
      <div class="curve-editor">
        <div class="curve-canvas-container" title="Left-click to add · Drag to move · Right-click to delete">
          <canvas id="curveCanvas"></canvas>
        </div>
        <div class="curve-presets" style="margin-top:6px;">
          <button data-preset="linear"          class="active" title="Straight 1:1 mapping">Linear</button>
          <button data-preset="enhance-peaks"          title="Boost contrast at high elevations">Peaks</button>
          <button data-preset="compress-depths"         title="Compress low-elevation range">Depths</button>
          <button data-preset="s-curve"                 title="S-shaped curve">S-Curve</button>
        </div>
      </div>
      <div id="histogram" style="margin-top:8px;"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:6px;">
        <div style="display:flex;gap:4px;">
          <button id="undoCurveBtn" class="btn btn-secondary" style="padding:6px 10px;font-size:12px;" title="Undo (Ctrl+Z)" disabled>⟵ Undo</button>
          <button id="redoCurveBtn" class="btn btn-secondary" style="padding:6px 10px;font-size:12px;" title="Redo (Ctrl+Y)" disabled>Redo ⟶</button>
        </div>
        <div style="display:flex;gap:4px;">
          <button id="applyCurveBtn"     class="btn btn-primary"   style="padding:6px 12px;font-size:12px;" title="Apply the elevation curve">✓ Apply</button>
          <button id="resetCurveBtn"     class="btn btn-secondary" style="padding:6px 12px;font-size:12px;" title="Reset to linear">↺ Reset</button>
          <button id="seaLevelBufferBtn" class="btn btn-secondary" style="padding:6px 12px;font-size:12px;" title="Insert sea-level shelf">🌊 Sea Level</button>
        </div>
      </div>
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

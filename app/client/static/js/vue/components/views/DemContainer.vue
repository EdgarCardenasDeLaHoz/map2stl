<template>
  <!-- DEM/Edit view — IMPORTANT: never use v-if on canvas elements; use hidden class only -->
  <div id="demContainer" class="dem-container hidden">
    <div class="dem-image-section">

      <!-- Empty state when no DEM loaded -->
      <div id="demEmptyState" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:300px;color:#555;font-size:14px;gap:10px;">
        <span style="font-size:36px;">🏔️</span>
        <span>Select a region, then load terrain data to begin</span>
        <button id="emptyStateLoadBtn" class="btn btn-primary" style="margin-top:6px;padding:10px 28px;font-size:15px;" title="Load terrain elevation data for the selected region">
          ↺ Load DEM
        </button>
      </div>

      <!-- Stacked Layers View (napari-style) with axis labels -->
      <div class="layers-container" id="layersContainer" style="display:none;">
        <div class="layers-main">
          <div class="layers-with-axes">
            <div class="layers-y-axis" id="layersYAxis"></div>
            <div class="layers-stack-wrapper">
              <div class="layers-stack" id="layersStack">
                <!-- Hidden source buffers — written to by layer renderers, never displayed directly -->
                <canvas class="layer-canvas" id="layerDemCanvas"          style="display:none;"></canvas>
                <canvas class="layer-canvas" id="layerWaterCanvas"        style="display:none;"></canvas>
                <canvas class="layer-canvas" id="layerSatCanvas"          style="display:none;"></canvas>
                <canvas class="layer-canvas" id="layerSatImgCanvas"       style="display:none;"></canvas>
                <canvas class="layer-canvas" id="layerCityRasterCanvas"   style="display:none;"></canvas>
                <canvas class="layer-canvas" id="layerCompositeDemCanvas" style="display:none;"></canvas>
                <canvas class="layer-canvas" id="layerHydroCanvas"       style="display:none;"></canvas>
                <!-- Single display canvas — shows the active view mode -->
                <canvas class="layer-canvas" id="stackViewCanvas"></canvas>
                <!-- Grid overlay — always on top, exempt from zoom transform -->
                <canvas class="layer-canvas" id="layerGridCanvas"
                  style="pointer-events:none; transform:none !important;"></canvas>
              </div>
              <div class="layers-x-axis" id="layersXAxis"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Compare View (inline) -->
      <div class="compare-inline-container hidden" id="compareInlineContainer">
        <div class="compare-inline-panels">
          <div class="compare-inline-panel">
            <div class="compare-inline-header">
              <select id="compareInlineLeft" style="flex:1;background:#3a3a3a;color:#ccc;border:1px solid #555;padding:4px;border-radius:3px;">
                <option value="dem">DEM (Elevation)</option>
                <option value="water">Water Mask</option>
                <option value="sat">Satellite / Land Cover</option>
                <option value="combined">Combined</option>
              </select>
            </div>
            <canvas id="compareInlineLeftCanvas" class="compare-inline-canvas"></canvas>
          </div>
          <div class="compare-inline-panel">
            <div class="compare-inline-header">
              <select id="compareInlineRight" style="flex:1;background:#3a3a3a;color:#ccc;border:1px solid #555;padding:4px;border-radius:3px;">
                <option value="dem">DEM (Elevation)</option>
                <option value="water">Water Mask</option>
                <option value="sat" selected>Satellite / Land Cover</option>
                <option value="combined">Combined</option>
              </select>
            </div>
            <canvas id="compareInlineRightCanvas" class="compare-inline-canvas"></canvas>
          </div>
        </div>
      </div>

      <!-- Hidden containers for legacy compatibility -->
      <div class="dem-image-container hidden" id="demSubtabContent">
        <div id="demImage"></div>
      </div>
      <div class="water-mask-container hidden" id="waterMaskContainer">
        <div id="waterMaskImage"></div>
        <div id="waterMaskStats" style="padding:10px;font-size:12px;color:#aaa;"></div>
      </div>
      <div class="satellite-container hidden" id="satelliteContainer">
        <div id="satelliteImage"></div>
      </div>
      <div class="combined-container hidden" id="combinedContainer">
        <div id="combinedImage"></div>
      </div>
      <div class="dem-landuse-container hidden">
        <div id="demLanduse"></div>
      </div>

      <!-- BBox editor + colorbar -->
      <div class="dem-info">
        <div class="bbox-editor" id="bboxEditor">
          <!-- Row 1: 2×2 coordinate grid -->
          <div class="bbox-coords-grid">
            <div class="bbox-coord-cell">
              <label class="bbox-lbl" for="bboxNorth">N</label>
              <input type="number" id="bboxNorth" class="bbox-input" step="0.01" placeholder="North" title="Northern boundary">
            </div>
            <div class="bbox-coord-cell">
              <label class="bbox-lbl" for="bboxSouth">S</label>
              <input type="number" id="bboxSouth" class="bbox-input" step="0.01" placeholder="South" title="Southern boundary">
            </div>
            <div class="bbox-coord-cell">
              <label class="bbox-lbl" for="bboxEast">E</label>
              <input type="number" id="bboxEast" class="bbox-input" step="0.01" placeholder="East" title="Eastern boundary">
            </div>
            <div class="bbox-coord-cell">
              <label class="bbox-lbl" for="bboxWest">W</label>
              <input type="number" id="bboxWest" class="bbox-input" step="0.01" placeholder="West" title="Western boundary">
            </div>
          </div>
          <!-- Row 2: action buttons + colorbar -->
          <div class="bbox-action-row">
            <button id="bboxReloadBtn"     class="bbox-reload-btn" title="Reload layers">↺ Reload</button>
            <button id="editBboxOnMapBtn"  class="bbox-reload-btn bbox-map-btn" title="Drag-edit bbox on mini-map">🗺 Map</button>
            <button id="saveBboxBtn"       class="bbox-reload-btn bbox-save-btn" title="Save bbox to region">💾 Save</button>
            <div class="bbox-divider"></div>
            <div id="colorbar" class="bbox-colorbar" title="Elevation colorbar"></div>
            <span id="bboxElevRange" class="bbox-elev-range"></span>
            <button id="settingsExternalBtn" class="bbox-reload-btn bbox-settings-btn" title="Toggle settings panel">⚙</button>
          </div>
        </div>
        <!-- Inline mini-map for drag-editing the bounding box -->
        <div id="bboxMiniMap" class="bbox-mini-map hidden"></div>
        <!-- Hidden elements kept for JS compatibility -->
        <datalist id="regionLabelsList"></datalist>
        <input type="hidden" id="regionLabelEdit">
        <button id="saveRegionLabelBtn" style="display:none;"></button>
      </div>
    </div>

    <!-- Stage 4: DEM Settings Panel rendered directly (not teleported — avoids timing issues) -->
    <DemSettingsPanel />
  </div>
</template>
<script setup lang="ts">
import DemSettingsPanel from '../dem/DemSettingsPanel.vue';
// All canvas elements are never conditionally unmounted — JS modules hold direct refs
</script>

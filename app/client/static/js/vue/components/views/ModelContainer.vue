<template>
  <!-- Three.js model viewer — display:none toggled by switchView() -->
  <div id="modelContainer" class="model-container hidden">
    <div class="model-layout">
      <div class="model-viewport">
        <div id="modelViewer"></div>
        <div id="modelEmptyState" style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#444;font-size:14px;gap:10px;pointer-events:none;">
          <span style="font-size:40px;">🗺️</span>
          <span>Generate a model to preview it here</span>
        </div>
        <div class="model-overlay">
          <span id="modelStatus">No model generated</span>
        </div>
      </div>
      <div class="model-sidebar">
        <h3>🏔️ 3D Model Generation</h3>

        <!-- ── Export Settings (export group) ─────────────────────────── -->
        <CollapsibleSection title="📐 Export Settings" :start-open="true" wrap-style="margin-bottom:10px;">

          <!-- export.model_height -->
          <div class="param-group">
            <label for="exportModelHeight" title="Physical height of the tallest terrain point in mm.">Model Height (mm):</label>
            <input type="number" id="exportModelHeight" value="30" min="1" max="200" step="1">
          </div>

          <!-- export.base_height -->
          <div class="param-group">
            <label for="exportBaseHeight" title="Solid base plate thickness in mm.">Base Height (mm):</label>
            <input type="number" id="exportBaseHeight" value="10" min="0" max="50" step="0.5">
          </div>

          <!-- export.exaggeration -->
          <div class="param-group">
            <label for="exportExaggeration" title="Vertical exaggeration multiplier applied to the mesh.">Exaggeration:</label>
            <input type="number" id="exportExaggeration" value="1.0" step="0.1" min="0.1" max="10">
          </div>

          <!-- export.sea_level_cap -->
          <div class="param-group">
            <label for="exportSeaLevelCap" title="Clamp all ocean surfaces to z=0 (prevents deep-trench artefacts).">Sea Level Cap:</label>
            <input type="checkbox" id="exportSeaLevelCap">
          </div>

          <!-- export.floor_val -->
          <div class="param-group">
            <label for="exportFloorVal" title="Minimum elevation value (metres) after normalisation. 0 = natural minimum.">Floor Value (m):</label>
            <input type="number" id="exportFloorVal" value="0" step="1" min="-500" max="500" style="width:80px;">
          </div>

          <!-- export.engrave_label -->
          <div class="param-group">
            <label for="exportEngraveLabel" title="Engrave region name into the base.">Engrave Label:</label>
            <input type="checkbox" id="exportEngraveLabel">
          </div>
          <div class="param-group" id="exportLabelTextRow" style="display:none;">
            <label for="exportLabelText" title="Label text (leave blank to use region name).">Label Text:</label>
            <input type="text" id="exportLabelText" placeholder="(region name)" class="ctrl-input">
          </div>

          <!-- export.contours -->
          <div class="param-group">
            <label for="exportContours" title="Add topo contour lines engraved into model.">Contour Lines:</label>
            <input type="checkbox" id="exportContours">
          </div>
          <div id="exportContoursParams" style="display:none;">
            <div class="param-group">
              <label for="exportContourInterval" title="Contour interval in metres.">Interval (m):</label>
              <select id="exportContourInterval">
                <option value="50">50 m</option>
                <option value="100" selected>100 m</option>
                <option value="250">250 m</option>
                <option value="500">500 m</option>
                <option value="1000">1000 m</option>
              </select>
            </div>
            <div class="param-group">
              <label for="exportContourStyle" title="Raised or engraved contours.">Style:</label>
              <select id="exportContourStyle">
                <option value="engraved" selected>Engraved</option>
                <option value="raised">Raised</option>
              </select>
            </div>
          </div>

        </CollapsibleSection>

        <!-- ── Split / Puzzle (split group) ───────────────────────────── -->
        <CollapsibleSection title="🧩 Split / Puzzle" wrap-style="margin-bottom:10px;" id="puzzleControlsSection">
          <div class="param-group">
            <label title="Split terrain into interlocking puzzle pieces">Enable:</label>
            <input type="checkbox" id="puzzleEnabled">
          </div>
          <div id="puzzleParams" style="display:none;">

            <!-- split.split_rows / split.split_cols -->
            <div class="param-group">
              <label title="Number of columns in the puzzle grid">Columns (X):</label>
              <input type="number" id="splitCols" value="4" min="1" max="20">
            </div>
            <div class="param-group">
              <label title="Number of rows in the puzzle grid">Rows (Y):</label>
              <input type="number" id="splitRows" value="4" min="1" max="20">
            </div>

            <!-- split.puzzle_m — connector size in mm -->
            <div class="param-group">
              <label title="Puzzle connector size in mm">Connector size (mm):</label>
              <input type="number" id="splitPuzzleM" value="50" min="5" max="200" step="5">
            </div>

            <!-- split.puzzle_base_n — base connector count per edge -->
            <div class="param-group">
              <label title="Number of connector bumps per edge">Connectors / edge:</label>
              <input type="number" id="splitPuzzleBaseN" value="10" min="1" max="40">
            </div>

            <!-- split.border_height / split.border_offset -->
            <div class="param-group">
              <label title="Raised lip height around each piece base (mm)">Border height (mm):</label>
              <input type="number" id="splitBorderHeight" value="1.0" min="0" max="10" step="0.5">
            </div>
            <div class="param-group">
              <label title="Inset from piece edge for raised lip (mm)">Border offset (mm):</label>
              <input type="number" id="splitBorderOffset" value="5.0" min="0" max="20" step="0.5">
            </div>

            <!-- split.include_border -->
            <div class="param-group">
              <label title="Add a raised lip around each piece base">Include Border:</label>
              <input type="checkbox" id="splitIncludeBorder" checked>
            </div>

            <button id="exportPuzzle3MFBtn" class="btn btn-success" style="width:100%;margin-top:6px;font-size:11px;">
              🖨 Export Puzzle 3MF
            </button>
          </div>
        </CollapsibleSection>

        <!-- ── Generate + Export ───────────────────────────────────────── -->
        <div class="param-group">
          <label for="modelResolution" title="Grid dimension for mesh generation.">Resolution:</label>
          <select id="modelResolution">
            <option value="100">Low (100×100)</option>
            <option value="200" selected>Medium (200×200)</option>
            <option value="400">High (400×400)</option>
            <option value="600">Ultra (600×600)</option>
          </select>
        </div>

        <div class="model-buttons">
          <button id="generateModelBtn2" class="btn btn-primary" title="Generate 3D terrain model from current DEM.">
            <span class="btn-icon">⚙️</span> Generate Model
          </button>
          <button id="previewModelBtn" class="btn btn-secondary" title="Show interactive 3D preview.">
            <span class="btn-icon">👁️</span> Preview 3D
          </button>
        </div>
        <div class="export-buttons" style="margin-top:8px;">
          <label style="font-size:12px;color:#888;margin-bottom:4px;display:block;">Export Format:</label>
          <div class="row-gap6">
            <button id="downloadSTLBtn" class="btn btn-success btn-sm" title="Download as STL."><span class="btn-icon">💾</span> STL</button>
            <button id="downloadOBJBtn" class="btn btn-success btn-sm" title="Download as OBJ."><span class="btn-icon">📦</span> OBJ</button>
            <button id="download3MFBtn" class="btn btn-success btn-sm" title="Download as 3MF."><span class="btn-icon">🖨️</span> 3MF</button>
          </div>
        </div>

        <!-- City Export -->
        <CollapsibleSection title="🏙️ City Export" wrap-style="margin-top:8px;">
          <div class="row-gap6">
            <button id="exportCityBtn" class="btn btn-success btn-sm" title="Export terrain + OSM buildings as 3MF.">
              <span class="btn-icon">🏙️</span> 3MF + Buildings
            </button>
          </div>
          <div class="row-gap6" style="margin-top:5px;font-size:10px;color:#aaa;">
            <input type="checkbox" id="citySimplifyMesh" checked>
            <label for="citySimplifyMesh" style="cursor:pointer;">Simplify terrain mesh</label>
          </div>
        </CollapsibleSection>

        <!-- Progress bar -->
        <div id="modelProgress" class="model-progress">
          <div class="progress-bar-container">
            <div id="modelProgressBar" class="progress-bar"></div>
          </div>
          <span id="modelProgressText">Generating...</span>
        </div>

        <!-- ── Print Dimensions & Bed Optimizer ───────────────────────── -->
        <div id="printDimensions" class="print-dimensions-panel" style="display:none;">
          <div class="dim-header">📐 Print Dimensions</div>
          <div class="dim-row"><span class="dim-label">Real area:</span><span id="dimRealArea">—</span></div>
          <div class="dim-row"><span class="dim-label">Footprint:</span><span id="dimFootprint">—</span></div>
          <div class="dim-row"><span class="dim-label">Scale:</span><span id="dimScale">—</span></div>
          <div class="dim-row"><span class="dim-label">Peak height:</span><span id="dimHeight">—</span></div>
          <div class="dim-row" id="dimBedFitRow"><span class="dim-label">Bed fit:</span><span id="dimBedFitText">—</span></div>
          <div style="margin-top:8px;padding-top:8px;border-top:1px solid #2d6a4f;">
            <div class="dim-header" style="margin-bottom:6px;">🖨️ Bed Optimizer</div>
            <div class="dim-row" style="gap:4px;">
              <label class="dim-label" style="flex-shrink:0;">Bed:</label>
              <select id="bedSizeSelect" style="flex:1;font-size:11px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;padding:2px;">
                <option value="220x220">Ender 220×220</option>
                <option value="235x235">Ender3 235×235</option>
                <option value="250x210" selected>Prusa 250×210</option>
                <option value="256x256">Bambu 256×256</option>
                <option value="300x300">Bambu 300×300</option>
                <option value="350x350">Bambu 350×350</option>
                <option value="custom">Custom…</option>
              </select>
            </div>
            <div id="bedCustomRow" class="dim-row" style="gap:4px;display:none;">
              <label class="dim-label">W×H (mm):</label>
              <input type="number" id="bedCustomW" value="220" min="50" max="1000" style="width:50px;font-size:11px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;padding:2px;">
              <span style="color:#888;">×</span>
              <input type="number" id="bedCustomH" value="220" min="50" max="1000" style="width:50px;font-size:11px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;padding:2px;">
            </div>
            <div id="bedOptimizerResult" style="font-size:11px;color:#ccc;margin-top:6px;line-height:1.5;"></div>
          </div>
        </div>

        <!-- ── Cross-Section Export ────────────────────────────────────── -->
        <CollapsibleSection title="✂️ Cross-Section" wrap-style="margin-top:10px;" id="crossSectionSection">
          <div class="param-group">
            <label title="Cut along a latitude or longitude line">Cut along:</label>
            <select id="crossSectionAxis">
              <option value="lat">Latitude (horizontal)</option>
              <option value="lon">Longitude (vertical)</option>
            </select>
          </div>
          <div class="param-group">
            <label title="Exact coordinate value for the cut">Cut at:</label>
            <input type="number" id="crossSectionValue" step="0.0001" placeholder="e.g. 40.7128" style="width:120px;">
            <button id="crossSectionMidBtn" class="btn btn-xs" style="margin-left:4px;">Mid</button>
          </div>
          <div class="param-group">
            <label title="Thickness of the slab in mm">Slab depth (mm):</label>
            <input type="number" id="crossSectionThickness" value="5" min="2" max="20" step="1" style="width:60px;">
          </div>
          <button id="downloadCrossSectionBtn" class="btn btn-success btn-sm" style="margin-top:6px;">
            <span class="btn-icon">✂️</span> Download Cross-Section STL
          </button>
          <div id="crossSectionStatus" style="font-size:11px;color:#888;margin-top:4px;"></div>
        </CollapsibleSection>

        <!-- ── 3D Viewer Controls ──────────────────────────────────────── -->
        <CollapsibleSection title="🎮 Viewer" wrap-style="margin-top:10px;">
          <div class="param-group">
            <label>Wireframe:</label>
            <input type="checkbox" id="viewerWireframe">
          </div>
          <div class="param-group">
            <label>Normals:</label>
            <input type="checkbox" id="viewerNormals">
          </div>
          <div class="param-group">
            <label>Colormap:</label>
            <select id="viewerColormap">
              <option value="terrain" selected>Terrain</option>
              <option value="viridis">Viridis</option>
              <option value="gray">Gray</option>
              <option value="none">None (flat)</option>
            </select>
          </div>
          <div class="param-group">
            <label>Auto-rotate:</label>
            <input type="checkbox" id="viewerAutoRotate">
          </div>
          <div class="param-group">
            <button id="viewerResetCamera" class="btn btn-secondary btn-sm">Reset Camera</button>
          </div>
        </CollapsibleSection>

      </div><!-- /model-sidebar -->
    </div><!-- /model-layout -->
  </div><!-- /modelContainer -->
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
// Three.js viewer initialises by reading #modelViewer after mount
</script>

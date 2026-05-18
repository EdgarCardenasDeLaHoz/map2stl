<template>
  <!-- Three.js model viewer — display:none toggled by switchView() -->
  <div id="modelContainer" class="model-container hidden">
    <div class="model-layout">
      <div class="model-viewport">
        <div id="modelViewer"></div>
        <div id="modelEmptyState" class="model-empty-state">
          <span style="font-size:40px;">🗺️</span>
          <span>Load a DEM in the Edit tab to render the 3D model</span>
        </div>
        <div class="model-overlay">
          <span id="modelStatus">No model generated</span>
        </div>
      </div>

      <div id="modelSidebarResizeHandle" class="settings-resize-handle" title="Drag to resize panel"></div>

      <!-- Right panel — mirrors Edit-tab structure -->
      <div id="modelRightPanel" class="dem-right-panel model-sidebar">

        <!-- Tab strip -->
        <div class="dem-strip" id="modelStrip">
          <button :class="['dem-strip-btn', activeTab==='fetch' && 'active']"
                  @click="activeTab='fetch'"
                  title="Build parameters — change any value to auto-rebuild the model">📥 Fetch</button>
          <div class="dem-strip-divider"></div>
          <button :class="['dem-strip-btn', activeTab==='view' && 'active']"
                  @click="activeTab='view'"
                  title="Viewer display options (does not rebuild the mesh)">👁 View</button>
          <button :class="['dem-strip-btn', activeTab==='export' && 'active']"
                  @click="activeTab='export'"
                  title="Download the model and printer-related options">📤 Export</button>
        </div>

        <div class="dem-controls" id="modelControls">
          <div class="dem-controls-inner">

            <!-- Progress bar (visible across tabs while a build is running) -->
            <div id="modelProgress" class="model-progress hidden" style="margin-bottom:6px;">
              <div class="progress-bar-container">
                <div id="modelProgressBar" class="progress-bar"></div>
              </div>
              <span id="modelProgressText">Building...</span>
            </div>

            <!-- ═══════════ Fetch tab ═══════════ -->
            <div v-show="activeTab==='fetch'">
              <div style="font-size:11px;color:#888;margin:2px 0 8px;line-height:1.4;">
                Changes here automatically rebuild the 3D model.
              </div>

              <div class="param-grid">
                <label for="mmPerPixel" title="Horizontal scale: how many millimetres each DEM pixel becomes in the printed model. 1.0 means an N×M DEM produces an N×M mm STL.">Resolution (mm/px)</label>
                <input type="number" id="mmPerPixel" value="1.0" min="0.05" max="20" step="0.05" class="ctrl-input-sm">

                <label for="exportModelHeight" title="Physical height of the tallest terrain point in mm.">Height (mm)</label>
                <input type="number" id="exportModelHeight" value="30" min="1" max="200" step="1" class="ctrl-input-sm">

                <label for="exportBaseHeight" title="Solid base plate thickness in mm.">Base (mm)</label>
                <input type="number" id="exportBaseHeight" value="10" min="0" max="50" step="0.5" class="ctrl-input-sm">

                <label for="exportExaggeration" title="Vertical exaggeration multiplier applied to the mesh.">Exaggeration</label>
                <input type="number" id="exportExaggeration" value="1.0" step="0.1" min="0.1" max="10" class="ctrl-input-sm">
              </div>

              <div style="display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 4px;font-size:11px;">
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Clamp all ocean surfaces to z=0 (prevents deep-trench artefacts).">
                  <input type="checkbox" id="exportSeaLevelCap"> Sea-level cap
                </label>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Render the full solid mesh (walls + floor) — matches what export will produce. Slower.">
                  <input type="checkbox" id="viewerSolidPreview"> Solid mesh
                </label>
              </div>
            </div>

            <!-- ═══════════ View tab ═══════════ -->
            <div v-show="activeTab==='view'">
              <div class="param-grid">
                <label for="viewerColormap">Colormap</label>
                <select id="viewerColormap" class="ctrl-input-sm" style="width:auto;">
                  <option value="terrain" selected>Terrain</option>
                  <option value="viridis">Viridis</option>
                  <option value="gray">Gray</option>
                  <option value="none">None (flat)</option>
                </select>
              </div>

              <div style="display:flex;flex-wrap:wrap;gap:6px 12px;margin:10px 0 4px;font-size:11px;">
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Overlay mesh wireframe">
                  <input type="checkbox" id="viewerWireframe"> Wireframe
                </label>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Show vertex normals debug material">
                  <input type="checkbox" id="viewerNormals"> Normals
                </label>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
                  <input type="checkbox" id="viewerAutoRotate"> Auto-rotate
                </label>
              </div>

              <div style="display:flex;flex-wrap:wrap;gap:6px 12px;margin:4px 0;font-size:11px;">
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Color each connected mesh patch a distinct random hue">
                  <input type="checkbox" id="viewerSurfaceGroups"> Surface groups
                </label>
              </div>

              <div style="display:flex;align-items:center;gap:6px;margin:4px 0;font-size:11px;flex-wrap:wrap;">
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Subsample the grid client-side — no re-fetch.">
                  <input type="checkbox" id="viewerSimplify"> Simplify
                </label>
                <input type="number" id="viewerSimplifyRatio" value="0.25" min="0.05" max="0.95" step="0.05"
                  class="ctrl-input-sm" style="width:48px;" title="Keep fraction (0.05 = very coarse, 0.95 = near-full)">
                <span style="color:#888;font-size:10px;">keep</span>
              </div>

              <button id="viewerResetCamera" class="btn btn-secondary btn-sm" style="width:100%;margin-top:8px;">Reset Camera</button>
            </div>

            <!-- ═══════════ Export tab ═══════════ -->
            <div v-show="activeTab==='export'">

              <!-- Download buttons -->
              <div class="row-gap6" style="margin-bottom:10px;">
                <button id="downloadSTLBtn" class="btn btn-success btn-sm" style="flex:1;" title="Download as STL.">
                  <span class="btn-icon">💾</span> STL
                </button>
                <button id="downloadOBJBtn" class="btn btn-success btn-sm" style="flex:1;" title="Download as OBJ.">
                  <span class="btn-icon">📦</span> OBJ
                </button>
                <button id="download3MFBtn" class="btn btn-success btn-sm" style="flex:1;" title="Download as 3MF.">
                  <span class="btn-icon">🖨️</span> 3MF
                </button>
              </div>

              <!-- Engraving + contour options -->
              <CollapsibleSection title="🖋 Engraving & Contours" wrap-style="margin-bottom:10px;">
                <div style="display:flex;flex-wrap:wrap;gap:6px 14px;font-size:11px;">
                  <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Engrave region name into the base.">
                    <input type="checkbox" id="exportEngraveLabel"> Engrave label
                  </label>
                  <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Add topo contour lines engraved into model.">
                    <input type="checkbox" id="exportContours"> Contours
                  </label>
                </div>
                <div id="exportLabelTextRow" style="display:none;margin-top:6px;">
                  <input type="text" id="exportLabelText" placeholder="Label text (blank = region name)" class="ctrl-input" style="width:100%;box-sizing:border-box;">
                </div>
                <div id="exportContoursParams" style="display:none;margin-top:6px;">
                  <div class="param-grid">
                    <label for="exportContourInterval" title="Contour interval in metres.">Interval (m)</label>
                    <select id="exportContourInterval" class="ctrl-input-sm" style="width:auto;">
                      <option value="50">50 m</option>
                      <option value="100" selected>100 m</option>
                      <option value="250">250 m</option>
                      <option value="500">500 m</option>
                      <option value="1000">1000 m</option>
                    </select>
                    <label for="exportContourStyle" title="Raised or engraved contours.">Style</label>
                    <select id="exportContourStyle" class="ctrl-input-sm" style="width:auto;">
                      <option value="engraved" selected>Engraved</option>
                      <option value="raised">Raised</option>
                    </select>
                  </div>
                </div>
              </CollapsibleSection>

              <!-- Split / Puzzle -->
              <CollapsibleSection title="🧩 Split / Puzzle" wrap-style="margin-bottom:10px;" id="puzzleControlsSection">
                <div class="param-group">
                  <label title="Split terrain into interlocking puzzle pieces">Enable:</label>
                  <input type="checkbox" id="puzzleEnabled">
                </div>
                <div id="puzzleParams" style="display:none;">
                  <div class="param-group">
                    <label title="Number of columns in the puzzle grid">Columns (X):</label>
                    <input type="number" id="splitCols" value="4" min="1" max="20">
                  </div>
                  <div class="param-group">
                    <label title="Number of rows in the puzzle grid">Rows (Y):</label>
                    <input type="number" id="splitRows" value="4" min="1" max="20">
                  </div>
                  <div class="param-group">
                    <label title="Puzzle connector size in mm">Connector size (mm):</label>
                    <input type="number" id="splitPuzzleM" value="50" min="5" max="200" step="5">
                  </div>
                  <div class="param-group">
                    <label title="Number of connector bumps per edge">Connectors / edge:</label>
                    <input type="number" id="splitPuzzleBaseN" value="10" min="1" max="40">
                  </div>
                  <div class="param-group">
                    <label title="Raised lip height around each piece base (mm)">Border height (mm):</label>
                    <input type="number" id="splitBorderHeight" value="1.0" min="0" max="10" step="0.5">
                  </div>
                  <div class="param-group">
                    <label title="Inset from piece edge for raised lip (mm)">Border offset (mm):</label>
                    <input type="number" id="splitBorderOffset" value="5.0" min="0" max="20" step="0.5">
                  </div>
                  <div class="param-group">
                    <label title="Add a raised lip around each piece base">Include Border:</label>
                    <input type="checkbox" id="splitIncludeBorder" checked>
                  </div>
                  <button id="exportPuzzle3MFBtn" class="btn btn-success" style="width:100%;margin-top:6px;font-size:11px;">
                    🖨 Export Puzzle 3MF
                  </button>
                </div>
              </CollapsibleSection>

              <!-- Cross-Section -->
              <CollapsibleSection title="✂️ Cross-Section" wrap-style="margin-bottom:10px;" id="crossSectionSection">
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

              <!-- City Export -->
              <CollapsibleSection title="🏙️ City Export" wrap-style="margin-bottom:10px;">
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

              <!-- Print Dimensions + Bed Optimizer -->
              <CollapsibleSection title="🖨 Printer" wrap-style="margin-bottom:10px;">
                <div id="printDimensions" class="print-dimensions-panel hidden">
                  <div class="dim-row"><span class="dim-label">Real area:</span><span id="dimRealArea">—</span></div>
                  <div class="dim-row"><span class="dim-label">Footprint:</span><span id="dimFootprint">—</span></div>
                  <div class="dim-row"><span class="dim-label">Scale:</span><span id="dimScale">—</span></div>
                  <div class="dim-row"><span class="dim-label">Peak height:</span><span id="dimHeight">—</span></div>
                  <div class="dim-row" id="dimBedFitRow"><span class="dim-label">Bed fit:</span><span id="dimBedFitText">—</span></div>
                  <div style="margin-top:8px;padding-top:8px;border-top:1px solid #2d6a4f;">
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
              </CollapsibleSection>

            </div><!-- /tab Export -->

          </div><!-- /dem-controls-inner -->
        </div><!-- /dem-controls -->

      </div><!-- /modelRightPanel -->
    </div><!-- /model-layout -->
  </div><!-- /modelContainer -->
</template>
<script setup lang="ts">
import { ref } from 'vue';
import CollapsibleSection from '../shared/CollapsibleSection.vue';

const activeTab = ref<'fetch' | 'view' | 'export'>('fetch');
// Auto-rebuild wiring lives in modules/export/model-viewer.js
// (attached to Fetch-tab inputs and to modelContainer visibility changes).
</script>

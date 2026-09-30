<template>
  <!-- Three.js model viewer — display:none toggled by switchView() -->
  <!-- input/change bubble up from every Extrude control (including preset and
       vanilla-module dispatches), which is when the scale/bed readouts recompute. -->
  <div id="modelContainer" class="model-container hidden" @input="formTick++" @change="formTick++">
    <div class="model-layout">
      <div class="model-viewport">
        <div id="modelViewer"></div>
        <div id="modelEmptyState" class="model-empty-state">
          <span style="font-size:40px;">{{ emptyState.icon }}</span>
          <span>{{ emptyState.text }}</span>
        </div>
        <div class="model-overlay">
          <span id="modelStatus">No model generated</span>
          <span v-if="scaleText" id="modelScaleInfo" class="model-scale-info"
                title="Horizontal scale from the DEM bbox and Resolution (mm/px); vertical relative to that scale (1× = true scale)">{{ scaleText }}</span>
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
          <div style="flex:1"></div>
          <a class="dem-strip-btn guide-link" :href="guideLink" target="_blank" rel="noopener"
             title="Open the step-by-step guide for this part (new tab)">? Guide</a>
        </div>

        <div class="dem-controls" id="modelControls">
          <div class="dem-controls-inner">

            <!-- Progress bar (visible across tabs while a build is running) -->
            <div id="modelProgress" class="model-progress hidden" style="margin-bottom:6px;">
              <div class="progress-bar-container">
                <div id="modelProgressBar" class="progress-bar"></div>
              </div>
              <span id="modelProgressText">Building...</span>
              <!-- Shown only while an export is polling; export-handlers.js
                   toggles it. The server has no cancel route, so this abandons
                   the task client-side and lets it expire. -->
              <button id="modelProgressCancel" type="button" class="dem-cancel-btn hidden"
                      aria-label="Cancel export"
                      title="Stop waiting for this export"
                      @click="cancelExport">✕ Cancel</button>
            </div>

            <!-- ═══════════ Fetch tab ═══════════ -->
            <div v-show="activeTab==='fetch'">
              <div style="font-size:11px;color:#888;margin:2px 0 8px;line-height:1.4;">
                Changes here automatically rebuild the 3D model.
              </div>

              <div class="param-grid">
                <label for="mmPerPixel" title="Horizontal scale: how many millimetres each DEM pixel becomes in the printed model. 1.0 means an N×M DEM produces an N×M mm STL.">Resolution (mm/px)</label>
                <input type="number" id="mmPerPixel" value="1.0" min="0.05" max="20" step="0.05" class="ctrl-input-sm">

                <label for="exportModelHeight" title="Relief height in mm when the vertical scale is Fit (and Auto on regions over 20 km diagonal).">Fit height (mm)</label>
                <input type="number" id="exportModelHeight" value="30" min="1" max="200" step="1" class="ctrl-input-sm">

                <label for="exportBaseHeight" title="Solid base plate thickness in mm.">Base (mm)</label>
                <input type="number" id="exportBaseHeight" value="10" min="0" max="50" step="0.5" class="ctrl-input-sm">

                <label for="exportExaggeration" title="Vertical exaggeration multiplier applied to the mesh.">Exaggeration</label>
                <input type="number" id="exportExaggeration" value="1.0" step="0.1" min="0.1" max="10" class="ctrl-input-sm">

                <label for="exportZMode" title="Auto: true scale x exaggeration when the region diagonal is under 20 km (buildings and terrain share it), otherwise the relief is fitted to Fit height.">Vertical</label>
                <select id="exportZMode" class="ctrl-input-sm" style="width:auto;">
                  <option value="auto" selected>Auto</option>
                  <option value="true">True scale × exag.</option>
                  <option value="fit">Fit to height</option>
                </select>

                <label for="exportMedian" title="Median filter on the DEM: removes the blocky steps of an upsampled DEM.">Smoothing</label>
                <select id="exportMedian" class="ctrl-input-sm" style="width:auto;">
                  <option value="0">Off</option>
                  <option value="3" selected>3×3 median</option>
                  <option value="5">5×5 median</option>
                </select>
              </div>

              <div style="display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 4px;font-size:11px;">
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Clamp all ocean surfaces to z=0 (prevents deep-trench artefacts).">
                  <input type="checkbox" id="exportSeaLevelCap"> Sea-level cap
                </label>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Render the full solid mesh (walls + floor) — matches what export will produce. Slower.">
                  <input type="checkbox" id="viewerSolidPreview" checked> Solid mesh
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
                  <option value="satellite">Satellite image</option>
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

              <!-- Pre-flight: size vs bed, pieces, counts, filament/time, warnings -->
              <PreflightPanel :form-tick="formTick" />

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
                <div v-if="bedFit" class="bed-fit-note" :class="bedFit.fits ? 'ok' : 'warn'">
                  {{ bedFitText }}
                  <button v-if="!bedFit.fits" type="button" class="btn btn-xs" style="margin-left:4px;"
                          title="Set Columns and Rows to this grid" @click="useBedGrid">Use</button>
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
                    <label title="Width of the tongue on each shared edge (mm)">Knob width (mm):</label>
                    <input type="number" id="splitKnobWidth" value="20" min="2" max="200" step="1">
                  </div>
                  <div class="param-group">
                    <label title="How far the tongue reaches into the neighbouring piece (mm)">Knob depth (mm):</label>
                    <input type="number" id="splitKnobDepth" value="8" min="1" max="50" step="0.5">
                  </div>
                  <div class="param-group">
                    <label title="Gap taken off the groove side so printed pieces fit (mm)">Clearance (mm):</label>
                    <input type="number" id="splitClearance" value="0.3" min="0" max="2" step="0.05">
                  </div>
                  <div class="param-group">
                    <label for="splitKnobShape" title="Tongue outline: classic rounded knob, dovetail, or a plain rectangular tab">Knob shape:</label>
                    <select id="splitKnobShape" class="ctrl-input-sm" style="width:auto;">
                      <option value="classic" selected>Classic (rounded)</option>
                      <option value="dovetail">Dovetail</option>
                      <option value="rectangular">Rectangular</option>
                    </select>
                  </div>
                  <div style="display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11px;margin:4px 0;">
                    <label title="Engrave the piece id (r1c2) and a north arrow into each piece's underside, 0.6 mm deep">
                      <input type="checkbox" id="puzzleEngrave" checked> Engrave ids + north arrow
                    </label>
                    <label title="Also write one 3MF per print bed with the pieces laid out side by side">
                      <input type="checkbox" id="puzzleLayout" checked> Lay out on plates
                    </label>
                  </div>
                  <div class="bed-fit-note" style="color:#aaa;">
                    Drag the red cut lines in the preview to move a cut.
                    <button type="button" class="btn btn-xs" style="margin-left:4px;"
                            title="Back to equal pieces" @click="resetCuts">Reset cuts</button>
                  </div>
                  <button id="exportPuzzle3MFBtn" class="btn btn-success" style="width:100%;margin-top:6px;font-size:11px;">
                    🧩 Export puzzle (.zip: OBJ pieces + 3MF)
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

              <!-- City Model -->
              <CollapsibleSection title="🏙️ City Model" wrap-style="margin-bottom:10px;">
                <div style="font-size:10px;color:#aaa;margin-bottom:6px;line-height:1.4;">
                  Terrain + OSM layers merged into one solid at the Resolution and Vertical
                  settings. Downloads a .zip: merged STL, 3MF with one part per layer,
                  report, and puzzle pieces (OBJ) if enabled.
                </div>
                <table class="city-layer-table" style="width:100%;font-size:11px;border-collapse:collapse;">
                  <tr v-for="l in cityLayers" :key="l.id">
                    <td style="white-space:nowrap;">
                      <input :id="'cityLayer_' + l.id + '_enabled'" v-model="l.enabled" type="checkbox">
                      <label :for="'cityLayer_' + l.id + '_enabled'" style="cursor:pointer;">{{ l.label }}</label>
                    </td>
                    <td>
                      <select :id="'cityLayer_' + l.id + '_mode'" v-model="l.mode" :disabled="!l.enabled"
                              class="ctrl-input-sm" style="width:auto;">
                        <option v-for="m in l.modes" :key="m" :value="m">{{ m }}</option>
                      </select>
                    </td>
                    <td style="white-space:nowrap;">
                      <input :id="'cityLayer_' + l.id + '_value'" v-model.number="l.value" type="number"
                             :disabled="!l.enabled" min="0" step="0.1" class="ctrl-input-sm" style="width:52px;">
                      <span style="color:#888;">{{ l.mode === 'extrude' ? '× height' : 'mm' }}</span>
                    </td>
                  </tr>
                </table>
                <div class="param-group" style="margin-top:6px;">
                  <label for="cityPuzzleEnabled" title="Cut the merged model into interlocking jigsaw pieces">Puzzle pieces:</label>
                  <input id="cityPuzzleEnabled" type="checkbox">
                  <label for="cityPieceMm" title="Largest piece size; the grid is chosen so pieces fit (your bed)">max</label>
                  <input id="cityPieceMm" type="number" value="200" min="30" max="1000" step="10" style="width:56px;"
                         @input="onPieceInput"> mm
                </div>
                <div v-if="bedFit" class="bed-fit-note" :class="bedFit.fits ? 'ok' : 'warn'">{{ bedFitText }}</div>
                <div style="font-size:10px;color:#888;">Knobs, engraving, plates and dragged cuts: see Split / Puzzle
                  (cut lines show while Split / Puzzle is off).</div>
                <button id="exportCityBtn" class="btn btn-success btn-sm" style="width:100%;margin-top:6px;"
                        title="Build terrain + all enabled layers as one model and download a .zip.">
                  <span class="btn-icon">🏙️</span> Build city model (.zip)
                </button>
                <ModelScorePanel />
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
                      <select id="bedSizeSelect" @change="onBedChange" style="flex:1;font-size:11px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;padding:2px;">
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
                      <input type="number" id="bedCustomW" @input="onBedChange" value="220" min="50" max="1000" style="width:50px;font-size:11px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;padding:2px;">
                      <span style="color:#888;">×</span>
                      <input type="number" id="bedCustomH" @input="onBedChange" value="220" min="50" max="1000" style="width:50px;font-size:11px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;padding:2px;">
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
import { computed, onMounted, ref } from 'vue';
import CollapsibleSection from '../shared/CollapsibleSection.vue';
import PreflightPanel from './PreflightPanel.vue';
import { useAppStore } from '../../stores/app';
import { guideHref } from '../../../modules/ui/guide-links.js';
import {
  defaultPieceMm, formatGroundLength, modelScale, parseBedSize, piecesNeeded,
} from '../../../modules/export/print-scale.js';

const activeTab = ref<'fetch' | 'view' | 'export'>('fetch');
// City SOP: step 5 (Model) covers Fetch/View, step 6 onward (pre-flight, puzzle, build) Export.
const guideLink = computed(() => guideHref('city-stl-and-puzzle-sop',
  activeTab.value === 'export' ? 'step-6' : 'step-5'));
const store = useAppStore();

// ── Empty state ──────────────────────────────────────────────────────────────
// The overlay is hidden by export-handlers.js:_setExportButtonsEnabled once a
// mesh exists; while it shows, say why: no DEM yet, mesh on its way, or failed.
const emptyState = computed(() => {
  const dem = store.lastDemData as { values?: ArrayLike<number> } | null;
  if (!dem?.values?.length) return { icon: '🗺️', text: 'Load a DEM in the Edit tab to render the 3D model' };
  if (store.modelPreviewState === 'error') return { icon: '⚠️', text: 'Mesh build failed — see the status line below' };
  return { icon: '⏳', text: 'Building mesh…' };
});

// ── Form readouts (scale, bed fit) ───────────────────────────────────────────
// Extrude inputs are plain DOM controls read by the vanilla modules; formTick
// makes these computeds re-read them after any input/change inside the view.
const formTick = ref(0);
function _val(id: string): string | undefined {
  return (document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null)?.value;
}
function _num(id: string, fallback: number): number {
  const v = parseFloat(_val(id) ?? '');
  return Number.isFinite(v) && v > 0 ? v : fallback;
}
function _bed() {
  return parseBedSize(_val('bedSizeSelect'), _val('bedCustomW'), _val('bedCustomH'));
}

const scale = computed(() => {
  void formTick.value;
  void store.modelPreviewState;
  const dem = store.lastDemData as { width?: number; height?: number; vmin?: number; vmax?: number } | null;
  const bbox = (store.currentDemBbox || store.selectedRegion) as
    { north: number; south: number; east: number; west: number } | null;
  if (!dem?.width || !dem?.height || !bbox) return null;
  return modelScale({
    bbox, cols: dem.width, rows: dem.height,
    mmPerPx: _num('mmPerPixel', 1),
    zMode: (_val('exportZMode') || 'auto') as 'auto' | 'true' | 'fit',
    exaggeration: _num('exportExaggeration', 1),
    fitHeightMm: _num('exportModelHeight', 30),
    elevMin: dem.vmin ?? 0,
    elevMax: dem.vmax ?? 0,
  });
});

const scaleText = computed(() => {
  const s = scale.value;
  if (!s) return '';
  const ground = `1 mm = ${formatGroundLength(s.mPerMm)} (1:${s.scaleDenominator.toLocaleString()})`;
  // A flat DEM (vmin == vmax) has no relief to fit to height: the ratio would
  // be fit height / 1e-6 m, i.e. billions of times.
  if (s.flatRelief) return `${ground} · vertical: DEM is flat (no relief to fit)`;
  const v = s.verticalExaggeration;
  const vText = v >= 10 ? v.toFixed(0) : v.toFixed(v >= 1 ? 1 : 2);
  const vert = Math.abs(v - 1) < 0.005
    ? 'vertical 1× (true scale)'
    : `vertical ${vText}× true scale${s.zMode === 'fit' ? ' (fit to height)' : ''}`;
  return `${ground} · ${vert}`;
});

const bedFit = computed(() => {
  void formTick.value;
  const s = scale.value;
  if (!s) return null;
  const bed = _bed();
  const pieceMm = _num('cityPieceMm', defaultPieceMm(bed));
  const fit = piecesNeeded(s.widthMm, s.depthMm, bed, pieceMm);
  return { ...fit, bed, pieceMm, widthMm: s.widthMm, depthMm: s.depthMm };
});

const bedFitText = computed(() => {
  const f = bedFit.value;
  if (!f) return '';
  const size = `${Math.round(f.widthMm)}×${Math.round(f.depthMm)} mm`;
  return f.fits
    ? `✓ ${size} fits the ${f.bed.w}×${f.bed.h} bed`
    : `⚠ ${size} needs ${f.cols} × ${f.rows} pieces (≤ ${f.pieceMm} mm each)`;
});

function _setField(id: string, value: string) {
  const el = document.getElementById(id) as HTMLInputElement | null;
  if (!el || el.value === value) return;
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function useBedGrid() {
  const f = bedFit.value;
  if (!f) return;
  _setField('splitCols', String(f.cols));
  _setField('splitRows', String(f.rows));
  (window as any).updatePuzzlePreview?.();
}

// The bed sets the default City Model piece size (shorter side less 10 mm)
// until the user types their own; after that a bed change leaves it alone.
const pieceTouched = ref(false);
function onPieceInput(e: Event) {
  if (e.isTrusted) pieceTouched.value = true;
}
function onBedChange() {
  if (!pieceTouched.value) _setField('cityPieceMm', String(defaultPieceMm(_bed())));
}
onMounted(onBedChange);

// City Model layers. Defaults mirror city2stl.city_model.DEFAULT_LAYERS; the export
// handler reads them back from the DOM ids (cityLayer_<id>_enabled/_mode/_value).
// value: extrude -> multiplier on real height; raised -> mm above; engraved/water -> mm deep.
const SURFACE = ['raised', 'engraved'];
const cityLayers = ref([
    { id: 'buildings', label: 'Buildings', enabled: true, mode: 'extrude', value: 1, modes: ['extrude'] },
    { id: 'fortifications', label: 'Fortifications', enabled: true, mode: 'extrude', value: 1, modes: ['extrude'] },
    { id: 'walls', label: 'City walls', enabled: true, mode: 'extrude', value: 1, modes: ['extrude'] },
    { id: 'towers', label: 'Towers', enabled: true, mode: 'extrude', value: 1, modes: ['extrude'] },
    { id: 'churches', label: 'Churches', enabled: true, mode: 'extrude', value: 1, modes: ['extrude'] },
    { id: 'roads', label: 'Roads', enabled: true, mode: 'raised', value: 0.4, modes: SURFACE },
    { id: 'railways', label: 'Rail', enabled: true, mode: 'raised', value: 0.3, modes: SURFACE },
    // Off by default: turn on for hiking / mountain models (the Mountain preset does).
    { id: 'trails', label: 'Trails', enabled: false, mode: 'raised', value: 0.3, modes: SURFACE },
    { id: 'green', label: 'Parks / green', enabled: true, mode: 'raised', value: 0.2, modes: SURFACE },
    { id: 'waterways', label: 'Water', enabled: true, mode: 'water', value: 1.0, modes: ['water', 'engraved'] },
]);
// Auto-rebuild wiring lives in modules/export/model-viewer.js
// (attached to Fetch-tab inputs and to modelContainer visibility changes).

// The export lifecycle lives in modules/export/export-handlers.js, which is a
// plain ES module loaded outside the Vue bundle; reach it through window.
function resetCuts() {
    (window as any).resetPuzzleEdges?.();
}

function cancelExport() {
    (window as any).cancelExport?.();
}
</script>
<style scoped>
.model-scale-info {
  display: block;
  font-size: 11px;
  color: #9cc;
  margin-top: 2px;
}
.bed-fit-note {
  font-size: 11px;
  margin: 4px 0;
  line-height: 1.4;
}
.bed-fit-note.ok { color: #7c7; }
.bed-fit-note.warn { color: #e67e22; }
</style>

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

      <!-- Right panel: Beginner cards (printed size, model, download), then one card per
           tool switched on in ⚙ Settings (stores/uiMode.ts). Design guidelines §1.4 / §1.7,
           mockup claude/mockups/2026-10-01/extrude.html. Control ids are the interface the
           modules read (model-viewer.js auto-rebuild, export-handlers.js): keep them. -->
      <div id="modelRightPanel" class="dem-right-panel model-sidebar">
        <div class="dem-controls" id="modelControls">
          <div class="dem-controls-inner model-cards">

            <!-- Progress bar (visible while a build or export is running) -->
            <div id="modelProgress" class="model-progress hidden">
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

            <!-- ═══ PRINTED SIZE ═══ -->
            <section class="mcard">
              <div class="mcap">Printed size
                <a class="mcap-link" :href="guideLink" target="_blank" rel="noopener"
                   title="Open the step-by-step guide (new tab)">? Guide</a></div>
              <div id="modelPrintSize" class="mbig">{{ sizeText || '—' }}</div>
              <div v-if="bedFit" class="mfit" :class="bedFit.fits ? 'ok' : 'warn'">{{ fitText }}</div>
              <div v-if="scaleText" class="mhint">{{ scaleText }}</div>
              <!-- Footprint / scale / peak / bed-fit readouts and the bed optimizer, written
                   by dem-main.js; shown with the Vertical & surface tool. -->
              <div v-show="ui.shows('vertical')" id="printDimensions" class="print-dimensions-panel hidden">
                <div class="dim-row"><span class="dim-label">Real area:</span><span id="dimRealArea">—</span></div>
                <div class="dim-row"><span class="dim-label">Footprint:</span><span id="dimFootprint">—</span></div>
                <div class="dim-row"><span class="dim-label">Scale:</span><span id="dimScale">—</span></div>
                <div class="dim-row"><span class="dim-label">Peak height:</span><span id="dimHeight">—</span></div>
                <div class="dim-row" id="dimBedFitRow"><span class="dim-label">Bed fit:</span><span id="dimBedFitText">—</span></div>
                <div id="bedOptimizerResult" class="mhint"></div>
              </div>
            </section>

            <!-- ═══ MODEL ═══ -->
            <section class="mcard">
              <div class="mcap">Model
                <button type="button" class="mcap-link" title="Fill the bed, true-scale height, 10 mm base"
                        @click="resetModel">Reset</button></div>

              <div class="mrow">
                <label for="bedSizeSelect" class="mname">Printer</label>
                <select id="bedSizeSelect" class="mselect" @change="onBedChange">
                  <option value="220x220" selected>Ender 220×220</option>
                  <option value="235x235">Ender3 235×235</option>
                  <option value="250x210">Prusa 250×210</option>
                  <option value="256x256">Bambu 256×256</option>
                  <option value="300x300">Bambu 300×300</option>
                  <option value="350x350">Bambu 350×350</option>
                  <option value="custom">Custom…</option>
                </select>
              </div>
              <div id="bedCustomRow" class="mrow" style="display:none;">
                <span class="mname">Bed (mm)</span>
                <span>
                  <input type="number" id="bedCustomW" class="ctrl-input-sm mnum" @input="onBedChange" value="220" min="50" max="1000" aria-label="Bed width (mm)">
                  ×
                  <input type="number" id="bedCustomH" class="ctrl-input-sm mnum" @input="onBedChange" value="220" min="50" max="1000" aria-label="Bed depth (mm)">
                </span>
              </div>

              <div class="mctl">
                <div class="mlbl"><label for="modelWidthSlider">Width</label>
                  <span class="mval">{{ widthMm ? Math.round(widthMm) + ' mm' : '—' }}
                    <button type="button" class="mlink" title="Size the model to fill the printer bed" @click="fillBed">Fill bed</button></span></div>
                <!-- Writes #mmPerPixel (width ÷ DEM columns); the exact mm/px is under Vertical & surface. -->
                <input id="modelWidthSlider" type="range" class="mrange" min="30" :max="widthMax" step="1"
                       :value="Math.round(widthMm || 0)" :disabled="!demCols"
                       @input="onWidthInput" @change="onWidthChange">
              </div>

              <!-- Height drives exaggeration while the vertical scale is true scale, and the
                   relief height while it is fit-to-height (Auto picks fit above 20 km). -->
              <div class="mctl">
                <div class="mlbl"><label for="modelHeightSlider">Height</label><span class="mval">{{ heightText }}</span></div>
                <input id="modelHeightSlider" type="range" class="mrange"
                       :min="heightRange.min" :max="heightRange.max" :step="heightRange.step"
                       :value="heightDraft ?? heightValue" :title="heightRange.title"
                       @input="heightDraft = Number(($event.target as HTMLInputElement).value)"
                       @change="onHeightChange">
              </div>

              <div class="mctl">
                <div class="mlbl"><label for="exportBaseHeight">Base</label><span class="mval">{{ baseText }}</span></div>
                <input type="range" id="exportBaseHeight" class="mrange" value="10" min="0" max="30" step="0.5"
                       title="Solid base plate under the terrain">
              </div>

              <div class="mrow mswitch-row">
                <div><div class="mname">Split into pieces</div>
                  <div class="mhint">{{ splitHint }}</div></div>
                <input type="checkbox" class="mswitch" role="switch" aria-label="Split into pieces"
                       :checked="splitOn" @change="onSplitToggle">
              </div>
            </section>

            <!-- ═══ DOWNLOAD ═══ -->
            <section class="mcard">
              <div class="mcap">Download</div>
              <div v-if="isCity" class="mhint" style="margin-bottom:10px;">Terrain with buildings, roads and water:
                a .zip with one STL and a 3MF with a part per layer{{ splitOn ? ', plus the puzzle pieces' : '' }}.</div>
              <div v-else class="mseg" role="radiogroup" aria-label="File format">
                <button v-for="f in FORMATS" :key="f.id" type="button" role="radio"
                        :aria-checked="format === f.id" :class="{ on: format === f.id }"
                        :title="f.title" @click="format = f.id">{{ f.label }}</button>
              </div>
              <button id="modelDownloadBtn" type="button" class="btn btn-primary mdownload" @click="download">
                ⬇ {{ downloadLabel }}
              </button>
              <!-- The per-format buttons the export modules wire by id; the button above clicks the
                   matching one, so enabling, progress and errors stay in export-handlers.js. -->
              <div hidden>
                <button id="downloadSTLBtn" type="button">STL</button>
                <button id="downloadOBJBtn" type="button">OBJ</button>
                <button id="download3MFBtn" type="button">3MF</button>
                <button id="exportCityBtn" type="button">City model</button>
                <button id="exportPuzzle3MFBtn" type="button">Puzzle</button>
              </div>
              <!-- "Check before printing": size vs bed, pieces, filament, time, warnings -->
              <PreflightPanel :form-tick="formTick" />
            </section>

            <!-- More settings: switches for the sections below (F-DESIGN, user 2026-10-02). -->
            <ToolSwitches page="extrude" />

            <!-- ═══ Vertical & surface (tool) ═══ -->
            <section v-show="ui.shows('vertical')" class="mcard">
              <div class="mcap">Vertical &amp; surface</div>
              <div class="param-grid">
                <label for="mmPerPixel" title="Horizontal scale: millimetres per DEM pixel. Width above sets it.">Resolution (mm/px)</label>
                <input type="number" id="mmPerPixel" value="0.35" min="0.05" max="20" step="0.01" class="ctrl-input-sm">

                <label for="exportExaggeration" title="Vertical exaggeration multiplier (1 = true scale). Height above sets it.">Exaggeration</label>
                <input type="number" id="exportExaggeration" value="1.0" step="0.1" min="0.1" max="10" class="ctrl-input-sm">

                <label for="exportModelHeight" title="Relief height in mm when the vertical scale is Fit (and Auto on regions over 20 km diagonal).">Fit height (mm)</label>
                <input type="number" id="exportModelHeight" value="30" min="1" max="200" step="1" class="ctrl-input-sm">

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
              <div class="mchecks">
                <label title="Clamp all ocean surfaces to z=0 (prevents deep-trench artefacts).">
                  <input type="checkbox" id="exportSeaLevelCap"> Sea-level cap
                </label>
                <label title="Render the full solid mesh (walls + floor) — matches what export will produce. Slower.">
                  <input type="checkbox" id="viewerSolidPreview" checked> Solid mesh
                </label>
              </div>
            </section>

            <!-- ═══ City model layers (tool) ═══ -->
            <section v-show="ui.shows('cityLayers')" class="mcard">
              <div class="mcap">City model layers</div>
              <div class="mhint" style="margin-bottom:6px;">Extrude multiplies the real height; raised,
                engraved and water are in mm.</div>
              <table class="city-layer-table">
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
                    <span class="mhint">{{ l.mode === 'extrude' ? '× height' : 'mm' }}</span>
                  </td>
                </tr>
              </table>
            </section>

            <!-- ═══ Puzzle details (tool) ═══ -->
            <section v-show="ui.shows('puzzle')" class="mcard" id="puzzleControlsSection">
              <div class="mcap">Puzzle details</div>
              <div class="param-group">
                <label for="puzzleEnabled" title="Terrain-only regions: split the terrain into interlocking pieces">Terrain puzzle:</label>
                <input type="checkbox" id="puzzleEnabled">
              </div>
              <div class="param-group">
                <label for="cityPuzzleEnabled" title="City regions: cut the merged model into interlocking jigsaw pieces">City puzzle:</label>
                <input id="cityPuzzleEnabled" type="checkbox">
                <label for="cityPieceMm" title="Largest piece size; the grid is chosen so pieces fit (your bed)">max</label>
                <input id="cityPieceMm" type="number" value="210" min="30" max="1000" step="10" style="width:56px;"
                       @input="onPieceInput"> mm
              </div>
              <div v-if="bedFit" class="mhint" :class="bedFit.fits ? 'ok' : 'warn'">
                {{ bedFitText }}
                <button v-if="!bedFit.fits" type="button" class="btn btn-xs" style="margin-left:4px;"
                        title="Set Columns and Rows to this grid" @click="useBedGrid">Use</button>
              </div>
              <div id="puzzleParams">
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
                <div class="mchecks">
                  <label title="Engrave the piece id (r1c2) and a north arrow into each piece's underside, 0.6 mm deep">
                    <input type="checkbox" id="puzzleEngrave" checked> Engrave ids + north arrow
                  </label>
                  <label title="Also write one 3MF per print bed with the pieces laid out side by side">
                    <input type="checkbox" id="puzzleLayout" checked> Lay out on plates
                  </label>
                </div>
                <div class="mhint">
                  Drag the red cut lines in the preview to move a cut.
                  <button type="button" class="btn btn-xs" style="margin-left:4px;"
                          title="Back to equal pieces" @click="resetCuts">Reset cuts</button>
                </div>
              </div>
            </section>

            <!-- ═══ Engraving & contours (tool) ═══ -->
            <section v-show="ui.shows('engraving')" class="mcard">
              <div class="mcap">Engraving &amp; contours</div>
              <div class="mchecks">
                <label title="Engrave region name into the base.">
                  <input type="checkbox" id="exportEngraveLabel"> Engrave label
                </label>
                <label title="Add topo contour lines engraved into model.">
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
            </section>

            <!-- ═══ Cross-section (tool) ═══ -->
            <section v-show="ui.shows('crossSection')" class="mcard" id="crossSectionSection">
              <div class="mcap">Cross-section</div>
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
              <button id="downloadCrossSectionBtn" class="btn btn-secondary btn-sm" style="margin-top:6px;">
                ✂️ Download cross-section STL
              </button>
              <div id="crossSectionStatus" class="mhint"></div>
            </section>

            <!-- ═══ Model score (tool) ═══ -->
            <section v-show="ui.shows('modelScore')" class="mcard">
              <div class="mcap">Model score</div>
              <ModelScorePanel />
            </section>

            <!-- ═══ 3D view options (tool) ═══ -->
            <section v-show="ui.shows('viewer')" class="mcard">
              <div class="mcap">3D view</div>
              <div class="param-grid">
                <label for="viewerColormap">Colours</label>
                <select id="viewerColormap" class="ctrl-input-sm" style="width:auto;">
                  <option value="terrain" selected>Terrain</option>
                  <option value="viridis">Viridis</option>
                  <option value="gray">Gray</option>
                  <option value="satellite">Satellite image</option>
                  <option value="none">None (flat)</option>
                </select>
              </div>
              <div class="mchecks">
                <label title="Overlay mesh wireframe"><input type="checkbox" id="viewerWireframe"> Wireframe</label>
                <label title="Show vertex normals debug material"><input type="checkbox" id="viewerNormals"> Normals</label>
                <label><input type="checkbox" id="viewerAutoRotate"> Auto-rotate</label>
                <button id="viewerResetCamera" type="button" class="btn btn-secondary btn-sm">Reset view</button>
              </div>
            </section>
          </div><!-- /dem-controls-inner -->
        </div><!-- /dem-controls -->

      </div><!-- /modelRightPanel -->
    </div><!-- /model-layout -->
  </div><!-- /modelContainer -->
</template>
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import PreflightPanel from './PreflightPanel.vue';
import ToolSwitches from '../shared/ToolSwitches.vue';
import { useAppStore } from '../../stores/app';
import { useUiModeStore } from '../../stores/uiMode';
import { guideHref } from '../../../modules/ui/guide-links.js';
import {
  el as _el, val as _val, num, checked as _checked, setField as _setField, setChecked as _setChecked,
} from '../../dom-fields';
import {
  DEFAULT_BED, bboxDiagonalKm, defaultPieceMm, fillBedMmPerPx, formatGroundLength, modelScale,
  parseBedSize, piecesNeeded,
} from '../../../modules/export/print-scale.js';

// City SOP step 5 covers the model settings and step 6 the download.
const guideLink = guideHref('city-stl-and-puzzle-sop', 'step-5');
const store = useAppStore();
const ui = useUiModeStore();

// ── Empty state ──────────────────────────────────────────────────────────────
// The overlay is hidden by export-handlers.js:_setExportButtonsEnabled once a
// mesh exists; while it shows, say why: no DEM yet, mesh on its way, or failed.
const emptyState = computed(() => {
  const dem = store.lastDemData as { values?: ArrayLike<number> } | null;
  if (!dem?.values?.length) {
    return store.selectedRegion
      ? { icon: '⏳', text: 'Loading the terrain…' }
      : { icon: '🗺️', text: 'Pick a region in Explore to see it in 3D' };
  }
  if (store.modelPreviewState === 'error') return { icon: '⚠️', text: 'Mesh build failed — see the status line below' };
  return { icon: '⏳', text: 'Building mesh…' };
});

// ── Form readouts (scale, bed fit) ───────────────────────────────────────────
// Extrude inputs are plain DOM controls read by the vanilla modules; formTick
// makes these computeds re-read them after any input/change inside the view.
const formTick = ref(0);
/** A positive number from a control, else `fallback`. */
function _num(id: string, fallback: number): number {
  const v = num(id, NaN);
  return v > 0 ? v : fallback;
}
function _bed() {
  return parseBedSize(_val('bedSizeSelect'), _val('bedCustomW'), _val('bedCustomH'));
}

const dem = computed(() => store.lastDemData as
  { width?: number; height?: number; vmin?: number; vmax?: number; values?: ArrayLike<number> } | null);
const demCols = computed(() => dem.value?.width || 0);
const bbox = computed(() => (store.currentDemBbox || store.selectedRegion) as
  { north: number; south: number; east: number; west: number } | null);

const scale = computed(() => {
  void formTick.value;
  void store.modelPreviewState;
  const d = dem.value;
  if (!d?.width || !d?.height || !bbox.value) return null;
  return modelScale({
    bbox: bbox.value, cols: d.width, rows: d.height,
    mmPerPx: _num('mmPerPixel', 1),
    zMode: (_val('exportZMode') || 'auto') as 'auto' | 'true' | 'fit',
    exaggeration: _num('exportExaggeration', 1),
    fitHeightMm: _num('exportModelHeight', 30),
    elevMin: d.vmin ?? 0,
    elevMax: d.vmax ?? 0,
  });
});

const scaleText = computed(() => {
  const s = scale.value;
  if (!s) return '';
  return `1 mm = ${formatGroundLength(s.mPerMm)} · scale 1 : ${s.scaleDenominator.toLocaleString()}`;
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

const bedName = computed(() => {
  void formTick.value;
  const sel = _el('bedSizeSelect') as HTMLSelectElement | null;
  const b = _bed();
  return sel?.value === 'custom' ? `${b.w} × ${b.h} mm` : (sel?.selectedOptions[0]?.text || '');
});

const bedFitText = computed(() => {
  const f = bedFit.value;
  if (!f) return '';
  const size = `${Math.round(f.widthMm)}×${Math.round(f.depthMm)} mm`;
  return f.fits
    ? `✓ ${size} fits the ${f.bed.w}×${f.bed.h} bed`
    : `⚠ ${size} needs ${f.cols} × ${f.rows} pieces (≤ ${f.pieceMm} mm each)`;
});

// Printed size card: "180 × 144 × 26 mm". Width/depth follow the inputs live (DEM
// cols/rows × mm/px); the height is the last preview's z_max (model-viewer.js stores it
// as generatedModelData.zMaxMm).
const sizeText = computed(() => {
  const f = bedFit.value;
  if (!f) return '';
  const gm = store.generatedModelData as { zMaxMm?: number } | null;
  const h = Number.isFinite(gm?.zMaxMm) ? ` × ${Math.round(gm!.zMaxMm as number)}` : '';
  return `${Math.round(f.widthMm)} × ${Math.round(f.depthMm)}${h} mm`;
});
const fitText = computed(() => {
  const f = bedFit.value;
  if (!f) return '';
  return f.fits
    ? `✓ Fits the ${bedName.value} bed`
    : `⚠ Bigger than the ${bedName.value} bed: lower the width, or split into ${f.cols} × ${f.rows} pieces`;
});

// ── Width (writes mm/px) ─────────────────────────────────────────────────────
const widthDraft = ref<number | null>(null);
const widthMm = computed(() => widthDraft.value ?? scale.value?.widthMm ?? 0);
const widthMax = computed(() => {
  const b = _bed();
  return Math.max(4 * Math.max(b.w, b.h), Math.ceil(scale.value?.widthMm ?? 0));
});
function onWidthInput(e: Event) {
  widthDraft.value = Number((e.target as HTMLInputElement).value);
}
function onWidthChange(e: Event) {
  const w = Number((e.target as HTMLInputElement).value);
  widthDraft.value = null;
  if (demCols.value && w > 0) _setField('mmPerPixel', String(Math.round((w / demCols.value) * 10000) / 10000));
}
function fillBed() {
  const d = dem.value;
  if (!d?.width || !d?.height) return;
  const mm = fillBedMmPerPx(d.width, d.height, _bed());
  if (mm > 0) _setField('mmPerPixel', String(mm));
}

// ── Height (exaggeration, or relief height when fitting) ─────────────────────
const fitting = computed(() => scale.value?.zMode === 'fit');
const heightDraft = ref<number | null>(null);
const heightRange = computed(() => (fitting.value
  ? { min: 5, max: 100, step: 1, title: 'Height of the relief (highest minus lowest point) in mm' }
  : { min: 0.5, max: 5, step: 0.1, title: 'Vertical exaggeration: 1 = true scale' }));
const heightValue = computed(() => {
  void formTick.value;
  return fitting.value ? _num('exportModelHeight', 30) : _num('exportExaggeration', 1);
});
const heightText = computed(() => {
  const v = heightDraft.value ?? heightValue.value;
  if (fitting.value) return `${Math.round(v)} mm of relief`;
  return Math.abs(v - 1) < 0.05 ? 'true scale ×1.0' : `×${v.toFixed(1)}`;
});
function onHeightChange(e: Event) {
  const v = (e.target as HTMLInputElement).value;
  heightDraft.value = null;
  _setField(fitting.value ? 'exportModelHeight' : 'exportExaggeration', v);
}

const baseText = computed(() => {
  void formTick.value;
  return `${parseFloat(_val('exportBaseHeight') ?? '10')} mm`;
});

function resetModel() {
  fillBed();
  _setField(fitting.value ? 'exportModelHeight' : 'exportExaggeration', fitting.value ? '30' : '1');
  _setField('exportBaseHeight', '10');
}

// ── Split into pieces ────────────────────────────────────────────────────────
// A city-sized box (≤ CITY_COARSE_MAX_DIAG_KM, 25 km) downloads the City model, whose
// puzzle is #cityPuzzleEnabled; anything larger is terrain, whose puzzle is #puzzleEnabled.
const isCity = computed(() => {
  const b = bbox.value;
  if (!b) return false;
  return bboxDiagonalKm(b) <= ((window as any).CITY_COARSE_MAX_DIAG_KM ?? 25);
});
const splitOn = computed(() => {
  void formTick.value;
  return _checked(isCity.value ? 'cityPuzzleEnabled' : 'puzzleEnabled');
});
const splitHint = computed(() => {
  const f = bedFit.value;
  if (!f) return 'Only needed when the model is bigger than the bed';
  if (f.fits) return splitOn.value ? 'The model fits the bed; pieces are optional' : 'Only needed when the model is bigger than the bed';
  return `Needed at this size: ${f.cols} × ${f.rows} pieces`;
});
function onSplitToggle(e: Event) {
  const on = (e.target as HTMLInputElement).checked;
  _setChecked(isCity.value ? 'cityPuzzleEnabled' : 'puzzleEnabled', on);
  if (on && !isCity.value) useBedGrid();
  formTick.value++;
}

function useBedGrid() {
  const f = bedFit.value;
  if (!f) return;
  _setField('splitCols', String(f.cols));
  _setField('splitRows', String(f.rows));
  (window as any).updatePuzzlePreview?.();
}

// ── Download ─────────────────────────────────────────────────────────────────
const FORMATS = [
  { id: '3mf', label: '3MF', title: '3MF: one file, opens in every slicer', btn: 'download3MFBtn' },
  { id: 'stl', label: 'STL', title: 'STL: the classic mesh format', btn: 'downloadSTLBtn' },
  { id: 'obj', label: 'OBJ', title: 'OBJ: mesh for 3D tools', btn: 'downloadOBJBtn' },
] as const;
const format = ref<'3mf' | 'stl' | 'obj'>('3mf');
const downloadLabel = computed(() => {
  if (isCity.value) return splitOn.value ? 'Download model + pieces (.zip)' : 'Download model (.zip)';
  if (splitOn.value) return 'Download puzzle (.zip)';
  return `Download ${format.value.toUpperCase()}`;
});
function download() {
  const id = isCity.value ? 'exportCityBtn'
    : splitOn.value ? 'exportPuzzle3MFBtn'
      : FORMATS.find((f) => f.id === format.value)!.btn;
  (_el(id) as HTMLButtonElement | null)?.click();
}

// ── Printer bed ──────────────────────────────────────────────────────────────
// The printer is the viewer's, not the region's: remembered in this browser.
const BED_KEY = 'map2stl_bed';
// The bed sets the default City Model piece size (shorter side less 10 mm)
// until the user types their own; after that a bed change leaves it alone.
const pieceTouched = ref(false);
function onPieceInput(e: Event) {
  if (e.isTrusted) pieceTouched.value = true;
}
function onBedChange() {
  if (!pieceTouched.value) _setField('cityPieceMm', String(defaultPieceMm(_bed())));
  try {
    localStorage.setItem(BED_KEY, JSON.stringify(
      { value: _val('bedSizeSelect'), w: _val('bedCustomW'), h: _val('bedCustomH') }));
  } catch { /* storage unavailable: the default bed is used next time */ }
}
onMounted(() => {
  try {
    const saved = JSON.parse(localStorage.getItem(BED_KEY) || 'null');
    if (saved?.value) {
      if (saved.w) _setField('bedCustomW', String(saved.w));
      if (saved.h) _setField('bedCustomH', String(saved.h));
      _setField('bedSizeSelect', String(saved.value));
    }
  } catch { /* keep DEFAULT_BED */ }
  if (!_val('bedSizeSelect')) _setField('bedSizeSelect', DEFAULT_BED);
  onBedChange();
});

// Defaults that work (guidelines §1.10): mm/px is not saved per region, so each new DEM
// is sized to fill the bed, unless the model is being split into pieces on purpose.
watch(() => store.lastDemData, (d: any) => {
  if (!d?.width || !d?.height || splitOn.value) return;
  fillBed();
});

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
// Auto-rebuild wiring lives in modules/export/model-viewer.js (attached to the build
// inputs by id and to modelContainer visibility changes).

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
/* Extrude cards (design guidelines §4: cards 18 px radius, background steps, 11 px floor). */
.model-cards { display: flex; flex-direction: column; gap: 12px; }
.mcard { background: var(--panel-card, #232326); border-radius: 18px; padding: 14px 16px; }
.mcap {
  display: flex; align-items: center; gap: 6px; margin-bottom: 8px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted);
}
.mcap-link, .mlink {
  margin-left: auto; background: none; border: 0; padding: 0; cursor: pointer;
  color: #0a84ff; font-size: 12px; font-weight: 400; letter-spacing: 0; text-transform: none; text-decoration: none;
}
.mlink { margin-left: 8px; }
.mbig { font-size: 24px; font-weight: 700; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.mfit { font-size: 13px; margin-top: 4px; }
.mfit.ok, .mhint.ok { color: #30d158; }
.mfit.warn, .mhint.warn { color: #ff9f0a; }
.mhint { font-size: 12px; color: var(--text-muted); line-height: 1.4; }
.mrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 6px 0 10px; }
.mname { font-weight: 600; font-size: 13px; color: var(--text, #e0e0e0); }
.mselect { background: #2c2c2e; color: inherit; border: 0; border-radius: 10px; padding: 6px 10px; font-size: 13px; }
.mnum { width: 60px; }
.mctl { margin: 12px 0; }
.mlbl { display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; margin-bottom: 4px; }
.mlbl label { font-weight: 600; }
.mval { font-variant-numeric: tabular-nums; }
.mrange { width: 100%; accent-color: #0a84ff; }
.mswitch-row { margin-top: 14px; margin-bottom: 0; }
.mswitch {
  appearance: none; -webkit-appearance: none; flex: none; position: relative; cursor: pointer;
  width: 40px; height: 24px; border-radius: 999px; background: #3a3a3c; margin: 0; transition: background .15s;
}
.mswitch::after {
  content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
  border-radius: 50%; background: #fff; transition: left .15s;
}
.mswitch:checked { background: #30d158; }
.mswitch:checked::after { left: 18px; }
.mswitch:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
.mseg { display: flex; background: #2c2c2e; border-radius: 10px; padding: 3px; gap: 2px; margin-bottom: 10px; }
.mseg button {
  flex: 1; border: 0; border-radius: 8px; padding: 5px 0; background: none; color: var(--text-muted);
  font-size: 12px; cursor: pointer;
}
.mseg button.on { background: #3a3a3c; color: #f5f5f7; font-weight: 600; }
.mdownload { width: 100%; padding: 10px 0; font-size: 14px; font-weight: 600; border-radius: 12px; margin-bottom: 8px; }
.mchecks { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; margin: 10px 0 4px; font-size: 12px; }
.mchecks label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.city-layer-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.model-progress { margin-bottom: 0; }
.model-scale-info { display: block; font-size: 11px; color: #9cc; margin-top: 2px; }
/* Cards sit on the page background (guidelines §4: background steps, no frame around cards). */
#modelRightPanel.model-sidebar { background: transparent; border: 0; box-shadow: none; padding: 0; }
</style>

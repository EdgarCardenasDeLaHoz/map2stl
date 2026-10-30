<template>
  <!-- Plate align page (Tools menu, F-UNIFY Phase 4 part 3): place a plate on the map by eye, let
       the app snap it (ECC), see how well it fits, save it with the plate. Replaces the standalone
       tools/align_tool/drag_align.html; both call the same code (city2stl/registration/align_edit.py).
       Hidden unless the view is 'plate' (modules/ui/view-management.js). Data: routers/plate.py. -->
  <div id="plateContainer" class="plate-page hidden">
    <aside class="pl-left" aria-label="Plate and overlay">
      <h2 class="pl-h2">Plate</h2>
      <div class="pl-card">
        <label class="pl-row" for="plPlate">
          <span>Plate</span>
          <select id="plPlate" class="pl-select" :value="slug" :disabled="busy" @change="onPlate">
            <option v-for="p in plates" :key="p.slug" :value="p.slug">{{ p.city }}{{ p.saved ? ' ✓' : '' }}</option>
          </select>
        </label>
        <label class="pl-row" for="plLayer">
          <span>Map layer</span>
          <select id="plLayer" v-model="layer" class="pl-select">
            <option value="osm">Buildings</option>
            <option value="sat" :disabled="!hasSat">Satellite{{ hasSat ? '' : ' (none)' }}</option>
          </select>
        </label>
      </div>

      <div class="pl-seg" role="tablist" aria-label="Placement mode">
        <button type="button" role="tab" :aria-selected="mode === 'auto'" :class="{ on: mode === 'auto' }"
                @click="mode = 'auto'">Auto</button>
        <button type="button" role="tab" :aria-selected="mode === 'manual'" :class="{ on: mode === 'manual' }"
                @click="mode = 'manual'">Manual</button>
      </div>
      <template v-if="mode === 'manual'">
        <p class="pl-note">Drag the plate to move it, drag a corner to scale, drag the round handle to rotate. Then let Refine snap it.</p>
      </template>
      <template v-else>
        <p class="pl-note">The app places the plate by its streets and checks the result against the map. You can still adjust it in Manual.</p>
        <button type="button" class="pl-btn" :disabled="!detail || autoJob?.status === 'running'" @click="runAuto">
          {{ autoJob?.status === 'running' ? 'Placing…' : 'Place by streets' }}
        </button>
        <p v-if="autoJob?.status === 'running'" class="pl-note" role="status">{{ autoJob.message || 'Working…' }}</p>
        <p v-else-if="autoNote" class="pl-note" :class="{ bad: autoBad }" role="status">{{ autoNote }}</p>
      </template>

      <h2 class="pl-h2">Overlay</h2>
      <div class="pl-card">
        <div class="pl-row pl-block">
          <div class="pl-between"><label for="plOpacity">Plate opacity</label><span class="pl-v pl-num">{{ opacity }} %</span></div>
          <input id="plOpacity" v-model.number="opacity" type="range" min="5" max="100" class="pl-range">
        </div>
        <div class="pl-row">
          <span id="plLockLbl">Lock aspect</span>
          <button type="button" role="switch" class="pl-switch" :class="{ off: !lockAspect }"
                  :aria-checked="lockAspect" aria-labelledby="plLockLbl" @click="lockAspect = !lockAspect"></button>
        </div>
        <div class="pl-row">
          <span id="plFlipLbl">Flip</span>
          <button type="button" role="switch" class="pl-switch" :class="{ off: !flipped }"
                  :aria-checked="flipped" aria-labelledby="plFlipLbl" :disabled="!detail" @click="doFlip"></button>
        </div>
      </div>

      <h2 class="pl-h2">Map window</h2>
      <div class="pl-card">
        <label class="pl-row" for="plSize">
          <span>Size</span>
          <select id="plSize" v-model.number="margin" class="pl-select">
            <option v-for="m in MARGINS" :key="m" :value="m">{{ m }}× the plate</option>
          </select>
        </label>
        <label class="pl-row pl-block" for="plCentre">
          <span class="pl-between"><span>Centre (lat, lon)</span></span>
          <input id="plCentre" v-model="centreText" type="text" class="pl-input" autocomplete="off"
                 :placeholder="windowCentre || 'here'">
        </label>
      </div>
      <p class="pl-note">Used by “Fetch fresh map data”. Now: window centre {{ windowCentre || '—' }}.</p>
      <p class="pl-note pl-keys">Keys: arrows nudge (Shift = faster), Q / E rotate, + / − scale, hold Space to hide the plate. Shift + wheel rotates, wheel zooms, drag empty map to pan.</p>
    </aside>

    <section class="pl-main" aria-label="Map and plate">
      <div ref="stage" class="pl-stage">
        <canvas ref="canvas" class="pl-canvas" :class="cursorClass" tabindex="0"
                role="img" :aria-label="mapLabel"
                @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
                @contextmenu.prevent></canvas>
        <p v-if="loadError" class="pl-empty pl-bad" role="alert">{{ loadError }}</p>
        <p v-else-if="!detail" class="pl-empty pl-v">{{ plates.length ? 'Loading the plate…' : (platesNote) }}</p>
        <button v-if="detail" type="button" class="pl-fit" @click="fit">Fit view</button>
        <div v-if="detail && offset" class="pl-pill" role="status">{{ offset }}</div>
      </div>
    </section>

    <aside class="pl-right" aria-label="Fit and save">
      <h2 class="pl-h2">Fit</h2>
      <div class="pl-card">
        <div class="pl-row">
          <span>Overlap (IoU)</span>
          <span class="pl-num" :class="'pl-' + iouTone(score?.iou)">{{ fmtIou(score?.iou) }}</span>
        </div>
        <div class="pl-row">
          <span>Mean edge gap</span>
          <span class="pl-v pl-num">{{ fmtGap(score?.edge_gap_m) }}</span>
        </div>
      </div>
      <p v-if="fresh" class="pl-note">The numbers use the saved map data, not the fresh fetch. Reset to the auto result to compare again.</p>
      <p v-else-if="scoreError" class="pl-note pl-bad" role="alert">{{ scoreError }}</p>

      <button type="button" class="pl-btn primary" :disabled="!detail || busy || !!fresh" @click="refine">
        {{ job?.op === 'refine' && job.status === 'running' ? 'Refining…' : 'Refine (ECC)' }}
      </button>
      <button type="button" class="pl-btn" :disabled="!detail || busy" @click="refetch">
        {{ job?.op === 'refetch' && job.status === 'running' ? 'Fetching…' : 'Fetch fresh map data' }}
      </button>
      <button type="button" class="pl-btn" :disabled="!detail || busy" @click="resetAuto">Reset to auto result</button>

      <div v-if="job && job.status !== 'done'" class="pl-card pl-run" role="status">
        <div class="pl-v">{{ job.status === 'error' ? (job.error || job.message) : job.message }}</div>
        <div v-if="job.status === 'running'" class="pl-bar"><i :style="{ width: (job.progress || 10) + '%' }"></i></div>
      </div>
      <p v-if="message" class="pl-note" :class="{ 'pl-bad': messageBad }" role="status">{{ message }}</p>

      <h2 class="pl-h2">Save</h2>
      <button type="button" class="pl-go" :disabled="!detail || busy" @click="save">Save placement</button>
      <button type="button" class="pl-btn" :disabled="!detail" @click="exportJson">Export JSON</button>
      <p class="pl-note">The placement is stored with the plate and used by “Place + verify” in Edit.</p>
      <p v-if="savedNote" class="pl-note">{{ savedNote }}</p>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useAppStore } from '../../stores/app';
import {
  apply, decompose, plateCorners, plateCentre, translate, rotatePlate, scaleAbout, flipPlate,
  cornerScale, rotateDelta, rotateHandle, offsetText, fmtIou, iouTone, fmtGap, parseCenter,
  windowCentreText, buildGroundTruth, placementFromSaved, fitPoints, toScreen, fromScreen, zoomView,
  hitPlate, refineText, keyOp,
} from '../../../modules/ui/plate-align-format.js';

const MARGINS = [1.2, 1.5, 2, 3, 4];

const store = useAppStore();

const plates = ref<any[]>([]);
const platesNote = ref('No plates are exported yet.');
const slug = ref('');
const detail = ref<any>(null);
const loadError = ref('');
const M = ref<number[][] | null>(null);            // STL px -> OSM px
const autoM = ref<number[][] | null>(null);        // what "Reset to auto result" returns to
const mode = ref<'auto' | 'manual'>('manual');
const layer = ref<'osm' | 'sat'>('osm');
const opacity = ref(55);
const lockAspect = ref(true);
const margin = ref(1.5);
const centreText = ref('');
const score = ref<any>(null);
const scoreError = ref('');
const job = ref<any>(null);
const autoJob = ref<any>(null);
const autoNote = ref('');
const autoBad = ref(false);
const message = ref('');
const messageBad = ref(false);
const savedNote = ref('');
const fresh = ref<any>(null);                      // result of a re-fetch, until reset
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let scoreTimer: ReturnType<typeof setTimeout> | null = null;
let scoreSeq = 0;

const busy = computed(() => job.value?.status === 'running');
const hasSat = computed(() => !!(fresh.value ? fresh.value.images?.sat : detail.value?.images?.satellite));
const flipped = computed(() => (M.value ? decompose(M.value).flip : false));
const cellM = computed(() => fresh.value?.cell_size_m ?? detail.value?.cell_size_m ?? 1);
const windowCentre = computed(() => windowCentreText(fresh.value?.bbox_nsew ?? detail.value?.bbox_nsew));
const sw = computed(() => detail.value?.stl_shape?.[1] ?? 512);
const sh = computed(() => detail.value?.stl_shape?.[0] ?? 512);
const mapW = computed(() => fresh.value?.osm_shape?.[1] ?? detail.value?.osm_shape?.[1] ?? 512);
const mapH = computed(() => fresh.value?.osm_shape?.[0] ?? detail.value?.osm_shape?.[0] ?? 512);
const offset = computed(() => (M.value && autoM.value
  ? offsetText(M.value, autoM.value, sw.value, sh.value, cellM.value) : ''));
const mapLabel = computed(() => (detail.value
  ? `Map of ${detail.value.city} with the plate placed over it` : 'Map and plate'));

async function getJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 160)}`);
  return res.json();
}
function postJson(url: string, body: unknown) {
  return getJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
function say(text: string, bad = false) { message.value = text; messageBad.value = bad; }

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------
async function loadPlates() {
  try {
    const j = await getJson('/api/plate/plates');
    plates.value = j.plates;
    if (!j.plates.length) { platesNote.value = 'No plates are exported yet.'; return; }
    if (!slug.value || !j.plates.some((p: any) => p.slug === slug.value)) {
      const name = (store.selectedRegion?.name || '').toLowerCase();
      const hit = j.plates.find((p: any) => name && p.city && name.includes(String(p.city).toLowerCase()));
      await selectPlate((hit ?? j.plates[0]).slug);
    }
  } catch (e) {
    platesNote.value = `Could not load plates: ${(e as Error).message}`;
  }
}

function onPlate(e: Event) { selectPlate((e.target as HTMLSelectElement).value); }

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => rej(new Error('image failed to load'));
    im.src = url;
  });
}

function pixels(img: HTMLImageElement, w: number, h: number) {
  const t = document.createElement('canvas'); t.width = w; t.height = h;
  const c = t.getContext('2d')!;
  c.drawImage(img, 0, 0, w, h);
  return c.getImageData(0, 0, w, h);
}

let plateCv: HTMLCanvasElement | null = null;
let osmCv: HTMLCanvasElement | null = null;
let satCv: HTMLCanvasElement | null = null;

/** The plate: height ramp inside its mask on a dark ground. */
function buildPlate(hm: HTMLImageElement, mk: HTMLImageElement, w: number, h: number) {
  const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
  const c = cv.getContext('2d')!;
  const ph = pixels(hm, w, h), pm = pixels(mk, w, h);
  const out = c.createImageData(w, h);
  for (let i = 0; i < w * h; i++) {
    let r = 26, g = 32, b = 38;
    if (pm.data[i * 4] > 127) { const t = ph.data[i * 4] / 255; r = 30 + t * 70; g = 110 + t * 145; b = 60 + t * 60; }
    out.data[i * 4] = r; out.data[i * 4 + 1] = g; out.data[i * 4 + 2] = b; out.data[i * 4 + 3] = 255;
  }
  c.putImageData(out, 0, 0);
  return cv;
}

/** The map: buildings light, water blue, dark ground; or the satellite picture. */
function buildOsm(bld: HTMLImageElement, wat: HTMLImageElement | null, w: number, h: number) {
  const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
  const c = cv.getContext('2d')!;
  const pb = pixels(bld, w, h), pw = wat ? pixels(wat, w, h) : null;
  const out = c.createImageData(w, h);
  for (let i = 0; i < w * h; i++) {
    const b = pb.data[i * 4], wv = pw ? pw.data[i * 4] : 0;
    let r = 18, g = 20, bl = 24;
    if (wv > 127) { r = 40; g = 110; bl = 210; }
    if (b > 0) { const v = 90 + (b / 255) * 165; r = v; g = v; bl = v; }
    out.data[i * 4] = r; out.data[i * 4 + 1] = g; out.data[i * 4 + 2] = bl; out.data[i * 4 + 3] = 255;
  }
  c.putImageData(out, 0, 0);
  return cv;
}

function buildSat(img: HTMLImageElement, w: number, h: number) {
  const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
  cv.getContext('2d')!.drawImage(img, 0, 0, w, h);
  return cv;
}

async function buildRasters(d: any, f: any) {
  const W = f?.osm_shape?.[1] ?? d.osm_shape[1], H = f?.osm_shape?.[0] ?? d.osm_shape[0];
  const bUrl = f ? f.images.osm_buildings : d.images.buildings;
  const wUrl = f ? f.images.osm_water : d.images.water;
  const sUrl = f ? f.images.sat : d.images.satellite;
  const [bld, wat, hm, mk, sat] = await Promise.all([
    loadImage(bUrl), wUrl ? loadImage(wUrl) : Promise.resolve(null),
    loadImage(d.images.stl), loadImage(d.images.stl_mask), sUrl ? loadImage(sUrl) : Promise.resolve(null),
  ]);
  plateCv = buildPlate(hm, mk, d.stl_shape[1], d.stl_shape[0]);
  osmCv = buildOsm(bld, wat, W, H);
  satCv = sat ? buildSat(sat, W, H) : null;
}

async function selectPlate(next: string) {
  slug.value = next;
  detail.value = null;
  loadError.value = '';
  fresh.value = null;
  job.value = null;
  autoJob.value = null;
  autoNote.value = '';
  score.value = null;
  scoreError.value = '';
  savedNote.value = '';
  say('');
  stopPoll();
  try {
    const d = await getJson(`/api/plate/plates/${encodeURIComponent(next)}`);
    if (slug.value !== next) return;
    await buildRasters(d, null);
    if (slug.value !== next) return;
    detail.value = d;
    if (!d.images.satellite && layer.value === 'sat') layer.value = 'osm';
    autoM.value = d.guess?.matrix ?? null;
    const saved = d.saved ? placementFromSaved(d.saved) : null;
    if (saved) {
      M.value = saved;
      savedNote.value = `Restored your saved placement${d.saved.exported_at ? ` (${String(d.saved.exported_at).slice(0, 10)})` : ''}.`;
    } else {
      M.value = d.guess?.matrix ?? [[1, 0, 0], [0, 1, 0]];
    }
    await nextTick();
    fit();
    queueScore();
  } catch (e) {
    if (slug.value === next) loadError.value = `Could not load this plate: ${(e as Error).message}`;
  }
}

function openPage() { loadPlates(); }

// ---------------------------------------------------------------------------
// Fit numbers
// ---------------------------------------------------------------------------
function queueScore() {
  if (scoreTimer) clearTimeout(scoreTimer);
  if (!detail.value || !M.value || fresh.value) return;
  scoreTimer = setTimeout(runScore, 350);
}

async function runScore() {
  const my = ++scoreSeq;
  const s = slug.value;
  try {
    const r = await postJson(`/api/plate/plates/${encodeURIComponent(s)}/score`, { matrix: M.value });
    if (my !== scoreSeq || s !== slug.value) return;
    score.value = r;
    scoreError.value = '';
  } catch (e) {
    if (my === scoreSeq) { score.value = null; scoreError.value = `Could not score: ${(e as Error).message}`; }
  }
}

// ---------------------------------------------------------------------------
// Jobs: refine, fetch fresh map data
// ---------------------------------------------------------------------------
function stopPoll() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }

async function pollJob(op: string, taskId: string, on: (r: any) => void) {
  let j: any;
  try {
    j = await getJson(`/api/plate/jobs/${taskId}`);
  } catch (e) {
    job.value = { op, status: 'error', error: (e as Error).message };
    return;
  }
  job.value = { ...j, op };
  if (j.status === 'running') { pollTimer = setTimeout(() => pollJob(op, taskId, on), 700); return; }
  if (j.status === 'done') on(j.result);
}

async function startJob(op: string, url: string, body: unknown, on: (r: any) => void) {
  stopPoll();
  say('');
  try {
    const j = await postJson(url, body);
    job.value = { ...j, op };
    await pollJob(op, j.task_id, on);
  } catch (e) {
    job.value = { op, status: 'error', error: (e as Error).message };
  }
}

function refine() {
  if (!M.value) return;
  startJob('refine', `/api/plate/plates/${encodeURIComponent(slug.value)}/refine`, { matrix: M.value }, (r) => {
    if (r.improved) { M.value = r.matrix; queueScore(); }
    say(refineText(r));
  });
}

function refetch() {
  if (!M.value) return;
  let center: number[] | null = null;
  try { center = parseCenter(centreText.value); } catch (e) { say((e as Error).message, true); return; }
  const rot = decompose(M.value).rot_deg;
  startJob('refetch', `/api/plate/plates/${encodeURIComponent(slug.value)}/refetch`,
    { matrix: M.value, margin: margin.value, center }, async (r) => {
      try {
        await buildRasters(detail.value, r);
      } catch (e) { say(`Fetched, but the map could not be drawn: ${(e as Error).message}`, true); return; }
      fresh.value = r;
      score.value = null;
      // The new window was cut to the plate, so the right start is the plate centred at the fit scale.
      const k = 1 / r.fit_scale;
      let N = [[k, 0, 0], [0, k, 0]];
      const c = plateCentre(N, sw.value, sh.value);
      N = translate(N, r.osm_shape[1] / 2 - c[0], r.osm_shape[0] / 2 - c[1]);
      M.value = rotatePlate(N, sw.value, sh.value, rot);
      say(`Fetched ${Math.round(r.side_m)} m across at ${r.cell_size_m.toFixed(2)} m per pixel.`);
      fit();
      schedule();
    });
}

function resetAuto() {
  if (!detail.value) return;
  const wasFresh = !!fresh.value;
  const go = async () => {
    if (wasFresh) {
      fresh.value = null;
      try { await buildRasters(detail.value, null); } catch (e) { say((e as Error).message, true); return; }
    }
    if (autoM.value) M.value = autoM.value;
    say('Back at the auto result.');
    fit();
    queueScore();
  };
  go();
}

// ---------------------------------------------------------------------------
// Auto placement (the existing plate registration job)
// ---------------------------------------------------------------------------
function runAuto() {
  autoNote.value = '';
  autoBad.value = false;
  postJson('/api/registration/plate/start', { slug: slug.value, place: true, fix: true })
    .then((j) => { autoJob.value = j; pollAuto(j.task_id, slug.value); })
    .catch((e) => { autoNote.value = `Could not start: ${e.message}`; autoBad.value = true; });
}

async function pollAuto(taskId: string, forSlug: string) {
  let j: any;
  try { j = await getJson(`/api/registration/plate/status/${taskId}`); } catch (e) {
    autoJob.value = { status: 'error' }; autoNote.value = (e as Error).message; autoBad.value = true; return;
  }
  autoJob.value = j;
  if (forSlug !== slug.value) return;
  if (j.status === 'running') { setTimeout(() => pollAuto(taskId, forSlug), 800); return; }
  if (j.status === 'done' && j.result?.matrix) {
    autoM.value = j.result.matrix;
    M.value = j.result.matrix;
    const v = j.result.verdict?.status;
    autoNote.value = `Placed by ${String(j.result.matrix_source || 'streets').replace(/_/g, ' ')}${v ? `, check: ${v}` : ''}.`;
    fit();
    queueScore();
  } else if (j.status === 'error') {
    autoNote.value = j.error || 'The automatic placement failed.';
    autoBad.value = true;
  }
}

// ---------------------------------------------------------------------------
// Save / export
// ---------------------------------------------------------------------------
function currentRecord() {
  const meta = {
    ...detail.value,
    bbox_nsew: fresh.value?.bbox_nsew ?? detail.value.bbox_nsew,
    cell_size_m: cellM.value,
    resolution: fresh.value?.resolution ?? detail.value.resolution,
  };
  return buildGroundTruth(meta, M.value!, layer.value);
}

async function save() {
  if (!detail.value || !M.value) return;
  try {
    const r = await postJson(`/api/plate/plates/${encodeURIComponent(slug.value)}/save`, { payload: currentRecord() });
    const name = String(r.path || '').split(/[\\/]/).pop();
    savedNote.value = `Saved ${name} (${r.bytes} bytes).`;
    const p = plates.value.find((x) => x.slug === slug.value);
    if (p) p.saved = true;
    say('Placement saved.');
  } catch (e) {
    say(`Could not save: ${(e as Error).message}`, true);
  }
}

function exportJson() {
  if (!detail.value || !M.value) return;
  const blob = new Blob([JSON.stringify(currentRecord(), null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${slug.value}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  say(`Exported ${slug.value}.json to your Downloads folder.`);
}

// ---------------------------------------------------------------------------
// Map canvas
// ---------------------------------------------------------------------------
const stage = ref<HTMLDivElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
let view = { k: 1, vx: 0, vy: 0 };
let raf = 0;
let hidePlate = false;
const hoverPart = ref<string | null>(null);
const dragKind = ref<string | null>(null);
let drag: any = null;

const cursorClass = computed(() => {
  if (dragKind.value === 'pan') return 'grabbing';
  if (dragKind.value) return 'moving';
  const h = hoverPart.value;
  if (h === 'rotate') return 'rot';
  if (h && h.startsWith('c')) return 'size';
  if (h === 'body') return 'move';
  return '';
});

function size() {
  const c = canvas.value, s = stage.value;
  if (!c || !s) return { w: 0, h: 0 };
  const w = s.clientWidth, h = s.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    c.style.width = `${w}px`; c.style.height = `${h}px`;
  }
  return { w, h };
}

function fit() {
  const { w, h } = size();
  if (!w || !detail.value) { schedule(); return; }
  const pts: number[][] = [[0, 0], [mapW.value, 0], [0, mapH.value], [mapW.value, mapH.value]];
  if (M.value) pts.push(...plateCorners(M.value, sw.value, sh.value));
  view = fitPoints(pts, w, h);
  schedule();
}

function schedule() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; draw(); });
}

function css(name: string, fallback: string) {
  return canvas.value ? (getComputedStyle(canvas.value).getPropertyValue(name).trim() || fallback) : fallback;
}

function draw() {
  const c = canvas.value;
  if (!c) return;
  const { w, h } = size();
  const ctx = c.getContext('2d');
  if (!ctx || !w) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = css('--h-map', '#0a0a0b');
  ctx.fillRect(0, 0, w, h);
  if (!detail.value || !osmCv) return;
  const k = view.k;
  ctx.imageSmoothingEnabled = k < 3;
  const base = layer.value === 'sat' && satCv ? satCv : osmCv;
  ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * view.vx, dpr * view.vy);
  ctx.drawImage(base, 0, 0);
  if (!M.value || !plateCv) return;
  const m = M.value;
  if (!hidePlate) {
    ctx.globalAlpha = opacity.value / 100;
    ctx.setTransform(dpr * k * m[0][0], dpr * k * m[1][0], dpr * k * m[0][1], dpr * k * m[1][1],
      dpr * (k * m[0][2] + view.vx), dpr * (k * m[1][2] + view.vy));
    ctx.drawImage(plateCv, 0, 0);
    ctx.globalAlpha = 1;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const accent = css('--b-accent', '#0a84ff');
  const sc = plateCorners(m, sw.value, sh.value).map(([x, y]) => toScreen(view, x, y));
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2;
  ctx.beginPath();
  sc.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  ctx.stroke();
  // rotate handle: stem from the middle of the top edge, round knob
  const top = toScreen(view, ...(apply(m, sw.value / 2, 0) as [number, number]));
  const rh = rotateHandle(m, sw.value, sh.value, 28 / view.k);
  const rs = toScreen(view, rh[0], rh[1]);
  ctx.beginPath(); ctx.moveTo(top[0], top[1]); ctx.lineTo(rs[0], rs[1]); ctx.stroke();
  ctx.fillStyle = accent;
  ctx.beginPath(); ctx.arc(rs[0], rs[1], 7, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = 'rgba(0,0,0,0.45)';
  ctx.lineWidth = 1;
  for (const [x, y] of sc) { ctx.fillRect(x - 5, y - 5, 10, 10); ctx.strokeRect(x - 5.5, y - 5.5, 11, 11); }
  // window centre dot (where the plate was expected)
  if (autoM.value) {
    const g = plateCentre(autoM.value, sw.value, sh.value);
    const gs = toScreen(view, g[0], g[1]);
    ctx.fillStyle = accent;
    ctx.beginPath(); ctx.arc(gs[0], gs[1], 4, 0, Math.PI * 2); ctx.fill();
  }
}

function localXY(e: PointerEvent | WheelEvent) {
  const r = canvas.value!.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

const editable = computed(() => mode.value === 'manual');

function onDown(e: PointerEvent) {
  if (!detail.value) return;
  canvas.value?.focus();
  const p = localXY(e);
  const part = M.value && editable.value && !e.shiftKey && e.button === 0
    ? hitPlate(view, M.value, sw.value, sh.value, p.x, p.y) : null;
  const pt = fromScreen(view, p.x, p.y);
  if (part && M.value) {
    drag = { kind: part, start: pt, M0: M.value, view0: { ...view } };
    if (part === 'rotate') drag.centre = plateCentre(M.value, sw.value, sh.value);
    dragKind.value = part.startsWith('c') ? 'size' : part;
  } else {
    drag = { kind: 'pan', last: p };
    dragKind.value = 'pan';
  }
  canvas.value?.setPointerCapture?.(e.pointerId);
  e.preventDefault();
}

function onMove(e: PointerEvent) {
  const p = localXY(e);
  if (!drag) {
    const h = M.value && editable.value && detail.value
      ? hitPlate(view, M.value, sw.value, sh.value, p.x, p.y) : null;
    if (h !== hoverPart.value) hoverPart.value = h;
    return;
  }
  if (drag.kind === 'pan') {
    view = { ...view, vx: view.vx + (p.x - drag.last.x), vy: view.vy + (p.y - drag.last.y) };
    drag.last = p;
    schedule();
    return;
  }
  const now = fromScreen(view, p.x, p.y);
  if (drag.kind === 'body') {
    M.value = translate(drag.M0, now[0] - drag.start[0], now[1] - drag.start[1]);
  } else if (drag.kind === 'rotate') {
    const d = rotateDelta(drag.centre, drag.start, now);
    M.value = rotatePlate(drag.M0, sw.value, sh.value, d);
  } else {
    M.value = cornerScale(drag.M0, sw.value, sh.value, drag.start, now, lockAspect.value);
  }
  schedule();
}

function onUp(e: PointerEvent) {
  const moved = drag && drag.kind !== 'pan';
  drag = null;
  dragKind.value = null;
  canvas.value?.releasePointerCapture?.(e.pointerId);
  if (moved) queueScore();
}

function onWheel(e: WheelEvent) {
  if (!detail.value) return;
  e.preventDefault();
  const p = localXY(e);
  if (e.shiftKey && M.value && editable.value) {
    const dir = e.deltaY > 0 ? 1 : -1;
    M.value = rotatePlate(M.value, sw.value, sh.value, dir * ((e.ctrlKey || e.metaKey) ? 1 : 0.1));
    queueScore();
  } else {
    view = zoomView(view, p.x, p.y, Math.exp(-e.deltaY * 0.0015));
  }
  schedule();
}

function doFlip() {
  if (!M.value) return;
  M.value = flipPlate(M.value, sw.value, sh.value);
  queueScore();
}

function onKey(e: KeyboardEvent) {
  if (store.activeView !== 'plate' || !detail.value || !M.value) return;
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === 'SELECT' || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
  if (e.key === ' ') { hidePlate = true; schedule(); e.preventDefault(); return; }
  if (!editable.value) return;
  const op = keyOp(e.key, e.shiftKey);
  if (!op) return;
  if (op.op === 'move') M.value = translate(M.value, op.dx!, op.dy!);
  else if (op.op === 'rotate') M.value = rotatePlate(M.value, sw.value, sh.value, op.deg!);
  else if (op.op === 'scale') M.value = scaleAbout(M.value, sw.value, sh.value, op.f!);
  e.preventDefault();
  queueScore();
}

function onKeyUp(e: KeyboardEvent) {
  if (e.key === ' ' && hidePlate) { hidePlate = false; schedule(); }
}

let ro: ResizeObserver | null = null;
let themeObs: MutationObserver | null = null;
const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
let sized = false;

onMounted(() => {
  canvas.value?.addEventListener('wheel', onWheel, { passive: false });
  window.addEventListener('keydown', onKey);
  window.addEventListener('keyup', onKeyUp);
  if (stage.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      const { w } = size();
      if (w && detail.value && !sized) { sized = true; fit(); } else schedule();
    });
    ro.observe(stage.value);
  }
  themeObs = new MutationObserver(schedule);
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  mq?.addEventListener?.('change', schedule);
});

watch(() => store.activeView, (v) => {
  if (v === 'plate') { openPage(); nextTick(fit); } else { stopPoll(); }
}, { immediate: true });
watch([M, layer, opacity, detail], schedule);
onBeforeUnmount(() => {
  stopPoll();
  if (scoreTimer) clearTimeout(scoreTimer);
  ro?.disconnect();
  themeObs?.disconnect();
  mq?.removeEventListener?.('change', schedule);
  canvas.value?.removeEventListener('wheel', onWheel);
  window.removeEventListener('keydown', onKey);
  window.removeEventListener('keyup', onKeyUp);
  if (raf) cancelAnimationFrame(raf);
});
</script>

<style scoped>
/* Page tokens: dark by default (the app is dark); the light set follows the system setting,
   and :root[data-theme] forces either. */
.plate-page {
  --b-bg: #0e0e10; --b-panel: #1c1c1e; --b-card: #2c2c2e; --b-line: #3a3a3c;
  --b-text: #f2f2f7; --b-dim: #a1a1a8; --b-accent: #0a84ff; --b-sel: rgba(10, 132, 255, 0.18);
  --b-good: #30d158; --b-bad: #ff6b60; --b-warn: #ff9f0a; --b-off: #48484a;
  --h-map: #0a0a0b;
  display: flex; height: 100%; min-height: 0; background: var(--b-bg); color: var(--b-text);
  font: 13px/1.4 -apple-system, 'Segoe UI', system-ui, sans-serif;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme='dark']) .plate-page {
    --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
    --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
    --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
    --h-map: #f7f7fa;
  }
}
:root[data-theme='light'] .plate-page {
  --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
  --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
  --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
  --h-map: #f7f7fa;
}

.pl-left, .pl-right { background: var(--b-panel); padding: 14px; overflow-y: auto; flex: none; box-sizing: border-box; }
.pl-left { width: 280px; border-right: 1px solid var(--b-line); }
.pl-right { width: 300px; border-left: 1px solid var(--b-line); }
.pl-main { flex: 1; min-width: 0; position: relative; }
.pl-stage { position: absolute; inset: 0; overflow: hidden; background: var(--h-map); }
.pl-canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; outline: none; }
.pl-canvas.move, .pl-canvas.moving { cursor: move; }
.pl-canvas.size { cursor: nwse-resize; }
.pl-canvas.rot { cursor: crosshair; }
.pl-canvas.grabbing { cursor: grabbing; }
.pl-canvas:focus-visible { box-shadow: inset 0 0 0 2px var(--b-accent); }

.pl-h2 {
  font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--b-dim);
  margin: 14px 4px 6px; font-weight: 600;
}
.pl-h2:first-child { margin-top: 0; }
.pl-card { background: var(--b-card); border-radius: 12px; padding: 4px 0; margin-bottom: 12px; }
.pl-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
  padding: 9px 14px; border: 0; border-bottom: 1px solid var(--b-line); background: none;
  color: inherit; font: inherit; text-align: left; box-sizing: border-box;
}
.pl-row:last-child { border-bottom: 0; }
.pl-block { display: block; }
.pl-between { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.pl-v { color: var(--b-dim); }
.pl-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.pl-good { color: var(--b-good); }
.pl-warn { color: var(--b-warn); }
.pl-bad { color: var(--b-bad); }
.pl-note { color: var(--b-dim); font-size: 12px; padding: 0 4px; margin: 0 0 10px; }
.pl-note.bad, .pl-note.pl-bad { color: var(--b-bad); }
.pl-keys { margin-top: 4px; }

.pl-select {
  max-width: 62%; background: none; border: 0; color: var(--b-dim); font: inherit; text-align: right;
  text-align-last: right; cursor: pointer; padding: 2px 0;
}
.pl-select option { background: var(--b-card); color: var(--b-text); }
.pl-select:focus-visible, .pl-input:focus-visible, .pl-range:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.pl-input {
  width: 100%; box-sizing: border-box; background: var(--b-panel); color: var(--b-text);
  border: 1px solid var(--b-line); border-radius: 8px; padding: 6px 8px; font: inherit;
}
.pl-range { width: 100%; accent-color: var(--b-accent); margin: 0; }

.pl-seg { display: flex; background: var(--b-card); border-radius: 10px; padding: 3px; margin: 0 0 10px; }
.pl-seg button {
  flex: 1; border: 0; background: none; color: var(--b-dim); font: inherit; padding: 7px 0;
  border-radius: 8px; cursor: pointer;
}
.pl-seg button.on { background: var(--b-sel); color: var(--b-text); box-shadow: inset 0 0 0 1px var(--b-line); }
.pl-seg button:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 1px; }

.pl-switch {
  width: 38px; height: 22px; border-radius: 11px; border: 0; padding: 0; flex: none;
  background: var(--b-good); position: relative; cursor: pointer;
}
.pl-switch::after {
  content: ''; position: absolute; right: 2px; top: 2px; width: 18px; height: 18px;
  border-radius: 9px; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}
.pl-switch.off { background: var(--b-off); }
.pl-switch.off::after { right: auto; left: 2px; }
.pl-switch:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.pl-switch:disabled { opacity: 0.5; cursor: default; }

.pl-btn {
  display: block; width: 100%; text-align: center; padding: 11px 14px; border-radius: 10px; border: 0;
  background: var(--b-card); color: var(--b-text); font: inherit; font-weight: 600; margin: 0 0 8px;
  cursor: pointer; box-sizing: border-box;
}
.pl-btn.primary { background: var(--b-accent); color: #fff; }
.pl-btn:disabled, .pl-go:disabled { opacity: 0.5; cursor: default; }
.pl-btn:focus-visible, .pl-go:focus-visible, .pl-fit:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.pl-go {
  display: block; width: 100%; padding: 12px; border-radius: 12px; border: 0; margin: 0 0 8px;
  background: linear-gradient(90deg, #0a84ff, #6e5be8); color: #fff; font: inherit; font-weight: 600;
  cursor: pointer;
}
.pl-run { padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }
.pl-bar { height: 6px; border-radius: 3px; background: var(--b-line); overflow: hidden; }
.pl-bar i { display: block; height: 100%; background: var(--b-accent); transition: width 0.3s; }

.pl-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; margin: 0; pointer-events: none; }
.pl-fit {
  position: absolute; right: 16px; top: 16px; z-index: 2; background: var(--b-card); color: var(--b-text);
  border: 1px solid var(--b-line); border-radius: 10px; padding: 7px 12px; font: inherit; cursor: pointer;
}
.pl-pill {
  position: absolute; left: 16px; bottom: 16px; z-index: 2; background: var(--b-card);
  border: 1px solid var(--b-line); border-radius: 12px; padding: 9px 14px; color: var(--b-text);
  max-width: calc(100% - 32px);
}

@media (max-width: 1100px) {
  .pl-left { width: 240px; }
  .pl-right { width: 260px; }
}
</style>

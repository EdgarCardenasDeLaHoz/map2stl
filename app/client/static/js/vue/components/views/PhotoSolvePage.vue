<template>
  <!-- Photo solve page (Tools menu, F-UNIFY Phase 4 part 3): pair points on a photo with the same
       points on the map, solve the camera, read building heights from the photo. Replaces the
       standalone tools/photo_label page for these jobs; both call the same code
       (tools/photo_label/server.py api_* functions, via app/server/core/photo_solve.py).
       Hidden unless the view is 'photo' (modules/ui/view-management.js). Data: routers/photo.py. -->
  <div id="photoContainer" class="ph-page hidden">
    <aside class="ph-left" aria-label="Photos">
      <h2 class="ph-h2">Photos</h2>
      <label v-if="regions.length > 1" class="ph-card ph-row" for="phArea">
        <span>Area</span>
        <select id="phArea" class="ph-select" :value="region" @change="onRegion">
          <option v-for="r in regions" :key="r.id" :value="r.id">{{ r.label }} ({{ r.photos }})</option>
        </select>
      </label>
      <div class="ph-card">
        <p v-if="!shownPhotos.length" class="ph-empty-row">{{ listNote }}</p>
        <button v-for="p in shownPhotos" :key="p.path" type="button" class="ph-photo"
                :class="{ on: p.path === sel, bad: p.bad }" :aria-current="p.path === sel"
                @click="selectPhoto(p.path)">
          <span class="ph-photo-main">
            <span class="ph-photo-name">{{ photoName(p.path) }}</span>
            <span class="ph-photo-sub">{{ photoStatus(p) }}</span>
          </span>
          <span class="ph-tick" aria-hidden="true">{{ p.solved && !p.bad ? '✓' : '›' }}</span>
        </button>
      </div>

      <input ref="fileInput" type="file" accept=".jpg,.jpeg,.png,.webp,image/*" class="ph-hidden" @change="onFile">
      <button type="button" class="ph-btn" :disabled="uploading" @click="fileInput?.click()">
        {{ uploading ? 'Adding…' : '+ Add photo' }}
      </button>
      <button type="button" class="ph-btn" :disabled="commonsRunning || !region" @click="findCommons">
        {{ commonsRunning ? 'Searching Commons…' : 'Find Commons photos here' }}
      </button>
      <p v-if="commonsNote" class="ph-note" :class="{ bad: commonsBad }" role="status">{{ commonsNote }}</p>
      <div v-if="commons.length" class="ph-card">
        <div v-for="c in commons" :key="c.url" class="ph-row ph-common">
          <span class="ph-common-t">{{ commonsText(c) }}</span>
          <button type="button" class="ph-mini" :disabled="addingUrl === c.url" @click="addCommons(c)">
            {{ addingUrl === c.url ? '…' : 'Add' }}
          </button>
        </div>
      </div>
      <p class="ph-note">Click a point in the photo, then the same point on the map. Four pairs are enough; more make the solve steadier.</p>

      <h2 class="ph-h2">This photo</h2>
      <div class="ph-card">
        <div class="ph-row">
          <span id="phBadLbl">Mark unusable</span>
          <button type="button" role="switch" class="ph-switch" :class="{ off: !selBad }" :aria-checked="selBad"
                  aria-labelledby="phBadLbl" :disabled="!sel" @click="toggleBad"></button>
        </div>
        <div class="ph-row">
          <span id="phShowLbl">Show unusable photos</span>
          <button type="button" role="switch" class="ph-switch" :class="{ off: !showBad }" :aria-checked="showBad"
                  aria-labelledby="phShowLbl" @click="showBad = !showBad"></button>
        </div>
        <div class="ph-row">
          <span id="phBldLbl">Buildings on photo</span>
          <button type="button" role="switch" class="ph-switch" :class="{ off: !showBuildings }"
                  :aria-checked="showBuildings" aria-labelledby="phBldLbl" @click="showBuildings = !showBuildings"></button>
        </div>
      </div>
      <button type="button" class="ph-btn" :disabled="!region || screening" @click="screenPhotos">
        {{ screening ? screenText : 'Screen photos' }}
      </button>
      <p v-if="screenNote" class="ph-note" role="status">{{ screenNote }}</p>
    </aside>

    <section class="ph-main" aria-label="Photo and map">
      <div ref="photoStage" class="ph-pane">
        <canvas ref="photoCv" class="ph-canvas" :class="{ pan: panning === 'photo' }" tabindex="0" role="img"
                :aria-label="photoLabel" @pointerdown="(e) => onDown(e, 'photo')" @pointermove="(e) => onMove(e, 'photo')"
                @pointerup="(e) => onUp(e, 'photo')" @pointercancel="(e) => onUp(e, 'photo')"></canvas>
        <span class="ph-chip">Photo · click a corner or roof edge</span>
        <p v-if="!sel" class="ph-empty">{{ photos.length ? 'Choose a photo on the left.' : 'No photo yet. Add one, or find some on Commons.' }}</p>
        <p v-else-if="imgError" class="ph-empty ph-bad" role="alert">{{ imgError }}</p>
      </div>
      <div ref="mapStage" class="ph-pane ph-map">
        <canvas ref="mapCv" class="ph-canvas" :class="{ pan: panning === 'map', aim: !!pending }" tabindex="0" role="img"
                :aria-label="mapLabel" @pointerdown="(e) => onDown(e, 'map')" @pointermove="(e) => onMove(e, 'map')"
                @pointerup="(e) => onUp(e, 'map')" @pointercancel="(e) => onUp(e, 'map')"></canvas>
        <span class="ph-chip">Map · click the same point</span>
        <button type="button" class="ph-fit" @click="fitMap">Fit map</button>
        <div class="ph-hint" role="status">{{ hint }}</div>
      </div>
    </section>

    <aside class="ph-right" aria-label="Camera and heights">
      <h2 class="ph-h2">Camera</h2>
      <div class="ph-card">
        <template v-if="camRows.length">
          <div v-for="r in camRows" :key="r.label" class="ph-row">
            <span>{{ r.label }}</span><span class="ph-num" :class="'ph-' + (r.tone || 'dim')">{{ r.value }}</span>
          </div>
        </template>
        <p v-else class="ph-empty-row">Not solved yet.</p>
      </div>
      <p v-if="stale" class="ph-note">The pairs changed after this solve. Solve again to update.</p>
      <p v-if="solveError" class="ph-note bad" role="alert">{{ solveError }}</p>
      <button type="button" class="ph-btn primary" :disabled="!canSolve || solving" @click="solve">
        {{ solving ? 'Solving…' : 'Solve camera' }}
      </button>
      <button type="button" class="ph-btn" :disabled="!sel || regJob?.status === 'running'" @click="autoRegister">
        {{ regJob?.status === 'running' ? 'Matching skyline…' : 'Auto register (start camera)' }}
      </button>
      <div v-if="regJob && regJob.status !== 'done'" class="ph-card ph-run" role="status">
        <div class="ph-dim">{{ regJob.status === 'error' ? (regJob.error || regJob.message) : regJob.message }}</div>
        <div v-if="regJob.status === 'running'" class="ph-bar"><i :style="{ width: (regJob.progress || 10) + '%' }"></i></div>
      </div>
      <p v-if="regNote" class="ph-note" :class="{ bad: regBad }" role="status">{{ regNote }}</p>
      <div v-if="candidates.length" class="ph-card">
        <button v-for="(c, i) in candidates" :key="i" type="button" class="ph-row ph-cand" :class="{ on: i === candIdx }"
                @click="useCandidate(i)">
          <span>{{ candidateText(c, i) }}</span>
        </button>
      </div>

      <h2 class="ph-h2">Pairs ({{ pairs.length }})</h2>
      <div class="ph-card">
        <p v-if="!pairs.length" class="ph-empty-row">No pairs yet.</p>
        <div v-for="(p, i) in pairs" :key="i" class="ph-row ph-pair">
          <span class="ph-dot">{{ i + 1 }}</span>
          <span class="ph-pair-t">{{ p.label || `Point ${i + 1}` }}</span>
          <span v-if="resid(i) !== null" class="ph-num" :class="'ph-' + rmsTone(resid(i))">{{ fmtRms(resid(i)) }}</span>
          <button type="button" class="ph-x" :aria-label="`Remove pair ${i + 1}`" @click="removePair(i)">×</button>
        </div>
      </div>
      <button type="button" class="ph-btn" :disabled="!sel" @click="savePairs">Save labels</button>
      <p v-if="saveNote" class="ph-note" :class="{ bad: saveBad }" role="status">{{ saveNote }}</p>

      <h2 class="ph-h2">Heights read from this photo</h2>
      <div class="ph-card">
        <div v-if="hRows.length" class="ph-hhead"><span>Building</span><span>Photo</span><span>Now</span></div>
        <div v-for="r in hRows" :key="r.index" class="ph-hrow">
          <span class="ph-hname">{{ r.building }}</span>
          <span class="ph-num">{{ r.photo_m.toFixed(1) }} m</span>
          <span class="ph-num ph-dim" :title="r.diff">{{ r.osm_m == null ? '—' : r.osm_m.toFixed(1) }}</span>
        </div>
        <p v-if="!hRows.length" class="ph-empty-row">Solve the camera to read heights.</p>
      </div>
      <button type="button" class="ph-go" :disabled="!hRows.length || keeping" @click="keepHeights">{{ keepLabel }}</button>
      <p class="ph-note">Saved as source “photo”. Never replaces OSM tags, survey or LiDAR.</p>
      <p v-if="keepNote" class="ph-note" role="status">{{ keepNote }}</p>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useAppStore } from '../../stores/app';
import {
  stepHint, rmsTone, fmtRms, cameraRows, photoStatus, photoName, labelsPayload, fitRect, photoToScreen,
  screenToPhoto, insidePhoto, llToScreen, screenToLl, fitMapView, zoomMap, panMap, mapRadius,
  needsFootprints, fovWedge, cameraFromCandidate, candidateText, footprintAt, heightRows, keepText,
  savedHeightsText, commonsText, MIN_PAIRS,
} from '../../../modules/ui/photo-solve-format.js';

const store = useAppStore();

const regions = ref<any[]>([]);
const region = ref('');
const photos = ref<any[]>([]);
const listNote = ref('Loading photos…');
const sel = ref('');
const showBad = ref(false);
const showBuildings = ref(true);
const pairs = ref<any[]>([]);
const pending = ref<{ x: number; y: number } | null>(null);
const imagePx = ref<number[]>([0, 0]);
const imgError = ref('');
const pose = ref<any>(null);
const cameraSource = ref<string>('solve');
const solveRes = ref<any>(null);
const stale = ref(false);
const solving = ref(false);
const solveError = ref('');
const buildings = ref<any[]>([]);
const candidates = ref<any[]>([]);
const candIdx = ref(-1);
const regJob = ref<any>(null);
const regNote = ref('');
const regBad = ref(false);
const saveNote = ref('');
const saveBad = ref(false);
const keepNote = ref('');
const keeping = ref(false);
const uploading = ref(false);
const commons = ref<any[]>([]);
const commonsRunning = ref(false);
const commonsNote = ref('');
const commonsBad = ref(false);
const addingUrl = ref('');
const screening = ref(false);
const screenText = ref('Screening…');
const screenNote = ref('');
const panning = ref<'photo' | 'map' | ''>('');
const fileInput = ref<HTMLInputElement | null>(null);
const photoStage = ref<HTMLDivElement | null>(null);
const mapStage = ref<HTMLDivElement | null>(null);
const photoCv = ref<HTMLCanvasElement | null>(null);
const mapCv = ref<HTMLCanvasElement | null>(null);

const timers = new Set<ReturnType<typeof setTimeout>>();
function later(fn: () => void, ms: number) {
  const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
  timers.add(t);
  return t;
}

const shownPhotos = computed(() => photos.value.filter((p) => showBad.value || !p.bad));
const selPhoto = computed(() => photos.value.find((p) => p.path === sel.value) || null);
const selBad = computed(() => !!selPhoto.value?.bad);
const canSolve = computed(() => !!sel.value && pairs.value.length >= MIN_PAIRS);
const camRows = computed(() => cameraRows(pose.value));
const hRows = computed(() => heightRows(solveRes.value?.heights));
const keepLabel = computed(() => keepText(hRows.value.length));
const hint = computed(() => stepHint(pairs.value.length, pending.value, !!pose.value && !stale.value));
const photoLabel = computed(() => (sel.value ? `Photo ${photoName(sel.value)} with ${pairs.value.length} pairs` : 'Photo'));
const mapLabel = computed(() => 'Map of the building footprints with the paired points and the camera');
function resid(i: number): number | null {
  const v = solveRes.value?.residuals_px?.[i];
  return typeof v === 'number' && !stale.value ? v : null;
}

async function getJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 160)}`);
  return res.json();
}
function postJson(url: string, body: unknown) {
  return getJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
const api = (path: string) => `/api/photo/regions/${encodeURIComponent(region.value)}/${path}`;

// ---------------------------------------------------------------------------
// Regions and photos
// ---------------------------------------------------------------------------
async function loadRegions() {
  try {
    const j = await getJson('/api/photo/regions');
    regions.value = j.regions;
    if (!j.regions.length) { listNote.value = 'No photo areas are set up.'; return; }
    if (!region.value || !j.regions.some((r: any) => r.id === region.value)) {
      const name = (store.selectedRegion?.name || '').toLowerCase();
      const hit = j.regions.find((r: any) => name && name.includes(String(r.label).toLowerCase()));
      await setRegion((hit ?? j.regions[0]).id);
    }
  } catch (e) {
    listNote.value = `Could not load photos: ${(e as Error).message}`;
  }
}

function onRegion(e: Event) { setRegion((e.target as HTMLSelectElement).value); }

async function setRegion(id: string) {
  region.value = id;
  sel.value = '';
  clearPhotoState();
  commons.value = [];
  commonsNote.value = '';
  screenNote.value = '';
  footLoaded = null;
  foot = [];
  const r = regions.value.find((x) => x.id === id);
  if (r?.view) { mv = { lat: r.view.lat, lon: r.view.lon, mpp: 3 }; }
  await loadPhotos();
  schedule();
  queueFootprints();
}

async function loadPhotos(keep = '') {
  try {
    const j = await getJson(api('photos'));
    photos.value = j.photos;
    listNote.value = 'No photos yet.';
    const first = keep || (j.photos.find((p: any) => !p.bad) ?? j.photos[0])?.path;
    if (first && !sel.value) await selectPhoto(first);
  } catch (e) {
    listNote.value = `Could not load photos: ${(e as Error).message}`;
  }
}

function clearPhotoState() {
  pairs.value = [];
  pending.value = null;
  pose.value = null;
  solveRes.value = null;
  stale.value = false;
  solveError.value = '';
  buildings.value = [];
  candidates.value = [];
  candIdx.value = -1;
  regJob.value = null;
  regNote.value = '';
  saveNote.value = '';
  keepNote.value = '';
  cameraSource.value = 'solve';
  img = null;
  imgError.value = '';
}

let img: HTMLImageElement | null = null;
let selSeq = 0;

async function selectPhoto(path: string) {
  const my = ++selSeq;
  sel.value = path;
  clearPhotoState();
  const p = photos.value.find((x) => x.path === path);
  if (p) imagePx.value = [p.w, p.h];
  const im = new Image();
  const loaded = new Promise<void>((res, rej) => { im.onload = () => res(); im.onerror = () => rej(new Error('The photo could not be loaded.')); });
  im.src = `${api('image')}?path=${encodeURIComponent(path)}`;
  try { await loaded; } catch (e) { if (my === selSeq) { imgError.value = (e as Error).message; schedule(); } return; }
  if (my !== selSeq) return;
  img = im;
  imagePx.value = [im.naturalWidth, im.naturalHeight];
  fitPhoto();
  try {
    const doc = await getJson(`${api('labels')}?path=${encodeURIComponent(path)}`);
    if (my !== selSeq) return;
    pairs.value = (doc.labels || []).map((l: any) => ({ x: l.x, y: l.y, lat: l.lat, lon: l.lon, label: l.label, auto: l.auto }));
    if (doc.camera && Object.keys(doc.camera).length) {
      pose.value = doc.camera;
      cameraSource.value = doc.camera_source || 'solve';
      projectCamera();
    }
  } catch { /* a photo with no labels file is normal */ }
  fitMap();
  schedule();
}

async function onFile(e: Event) {
  const inp = e.target as HTMLInputElement;
  const f = inp.files?.[0];
  inp.value = '';
  if (!f || !region.value) return;
  uploading.value = true;
  try {
    const added = await getJson(`${api('photos')}?name=${encodeURIComponent(f.name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: f,
    });
    await loadPhotos();
    await selectPhoto(added.path);
  } catch (err) {
    listNote.value = `Could not add the photo: ${(err as Error).message}`;
  } finally {
    uploading.value = false;
  }
}

async function toggleBad() {
  if (!sel.value) return;
  const bad = !selBad.value;
  try {
    await postJson(api('flag'), { photo: sel.value, bad, reason: bad ? 'marked unusable on the Photo solve page' : '' });
    const p = photos.value.find((x) => x.path === sel.value);
    if (p) { if (bad) p.bad = true; else delete p.bad; }
  } catch (e) {
    listNote.value = `Could not change the flag: ${(e as Error).message}`;
  }
}

async function screenPhotos() {
  screening.value = true;
  screenNote.value = '';
  try {
    await postJson(api('screen'), {});
    pollScreen();
  } catch (e) {
    screening.value = false;
    screenNote.value = `Could not start: ${(e as Error).message}`;
  }
}

async function pollScreen() {
  try {
    const s = await getJson(api('screen'));
    if (s.state === 'running') {
      screenText.value = s.total ? `Screening ${s.done} of ${s.total}…` : 'Screening…';
      later(pollScreen, 900);
      return;
    }
    screening.value = false;
    const n = (s.flagged || []).length;
    screenNote.value = s.message || (n ? `${n} photo${n === 1 ? '' : 's'} flagged as unusable.` : 'No photo was flagged.');
    await refreshPhotos();
  } catch (e) {
    screening.value = false;
    screenNote.value = (e as Error).message;
  }
}

async function refreshPhotos() {
  try { photos.value = (await getJson(api('photos'))).photos; } catch { /* keep the list */ }
}

// ---------------------------------------------------------------------------
// Commons
// ---------------------------------------------------------------------------
async function runJob(url: string, on: (j: any) => void, set: (j: any) => void) {
  let j: any;
  try { j = await getJson(url); } catch (e) { set({ status: 'error', error: (e as Error).message }); return; }
  set(j);
  if (j.status === 'running') { later(() => runJob(url, on, set), 800); return; }
  if (j.status === 'done') on(j.result);
}

async function findCommons() {
  commonsRunning.value = true;
  commonsNote.value = 'Searching Wikimedia Commons…';
  commonsBad.value = false;
  try {
    const j = await postJson(api('commons'), {});
    runJob(`/api/photo/jobs/${j.task_id}`, (r) => {
      commonsRunning.value = false;
      commons.value = r?.photos || [];
      commonsNote.value = commons.value.length ? `${commons.value.length} located photos found.` : 'No located skyline photos found for this area.';
    }, (s) => {
      if (s.status === 'error') { commonsRunning.value = false; commonsNote.value = s.error || s.message; commonsBad.value = true; }
    });
  } catch (e) {
    commonsRunning.value = false; commonsNote.value = (e as Error).message; commonsBad.value = true;
  }
}

async function addCommons(c: any) {
  addingUrl.value = c.url;
  try {
    const added = await postJson(api('commons/add'), { url: c.url, title: c.title });
    commons.value = commons.value.filter((x) => x.url !== c.url);
    await loadPhotos();
    await selectPhoto(added.path);
  } catch (e) {
    commonsNote.value = `Could not add it: ${(e as Error).message}`;
    commonsBad.value = true;
  } finally {
    addingUrl.value = '';
  }
}

// ---------------------------------------------------------------------------
// Pairs, solve, projection, heights
// ---------------------------------------------------------------------------
function removePair(i: number) {
  pairs.value.splice(i, 1);
  if (pose.value) stale.value = true;
  schedule();
}

async function solve() {
  if (!canSolve.value) return;
  solving.value = true;
  solveError.value = '';
  keepNote.value = '';
  try {
    const r = await postJson(api('solve'), { photo: sel.value, labels: pairs.value, image_px: imagePx.value });
    if (!r.ok) { solveError.value = r.error || 'The camera could not be solved.'; return; }
    solveRes.value = r;
    pose.value = r.pose;
    cameraSource.value = 'solve';
    stale.value = false;
    pairs.value.forEach((p) => { p.auto = undefined; });
    await projectCamera();
    fitMap(true);
  } catch (e) {
    solveError.value = `Could not solve: ${(e as Error).message}`;
  } finally {
    solving.value = false;
    schedule();
  }
}

async function projectCamera() {
  if (!pose.value || !sel.value) return;
  const my = selSeq;
  try {
    const r = await postJson(api('project'), { camera: pose.value, image_px: imagePx.value });
    if (my !== selSeq) return;
    buildings.value = r.ok ? r.buildings : [];
  } catch { buildings.value = []; }
  schedule();
}

async function savePairs() {
  saveBad.value = false;
  try {
    const r = await postJson(api('labels'), labelsPayload(sel.value, pairs.value, imagePx.value, pose.value, cameraSource.value));
    saveNote.value = `Saved ${r.n} pair${r.n === 1 ? '' : 's'}${pose.value ? ' and the camera' : ''}.`;
    await refreshPhotos();
  } catch (e) {
    saveBad.value = true;
    saveNote.value = `Could not save: ${(e as Error).message}`;
  }
}

async function keepHeights() {
  keeping.value = true;
  try {
    const r = await postJson(api('heights'), { photo: sel.value, rows: hRows.value });
    keepNote.value = savedHeightsText(r);
  } catch (e) {
    keepNote.value = `Could not save the heights: ${(e as Error).message}`;
  } finally {
    keeping.value = false;
  }
}

async function autoRegister() {
  regNote.value = '';
  regBad.value = false;
  candidates.value = [];
  try {
    const j = await postJson(api('register'), { photo: sel.value, refresh: false });
    regJob.value = j;
    runJob(`/api/photo/jobs/${j.task_id}`, (r) => {
      if (!r?.ok) { regNote.value = r?.error || 'No start camera found.'; regBad.value = true; return; }
      candidates.value = r.candidates || [];
      if (!candidates.value.length) { regNote.value = r.reason || 'No start camera found.'; regBad.value = true; return; }
      regNote.value = 'Pick a start camera. Its buildings show on the photo; add pairs and solve to refine it.';
      useCandidate(0);
    }, (s) => { regJob.value = s; if (s.status === 'error') { regNote.value = s.error || s.message; regBad.value = true; } });
  } catch (e) {
    regNote.value = (e as Error).message;
    regBad.value = true;
  }
}

function useCandidate(i: number) {
  const c = candidates.value[i];
  if (!c) return;
  candIdx.value = i;
  pose.value = cameraFromCandidate(c);
  cameraSource.value = 'auto';
  solveRes.value = null;
  stale.value = false;
  projectCamera();
  schedule();
}

// ---------------------------------------------------------------------------
// Footprints for the map
// ---------------------------------------------------------------------------
let foot: any[] = [];
let footLoaded: { lat: number; lon: number; radius: number } | null = null;
let footTimer: ReturnType<typeof setTimeout> | null = null;
let footSeq = 0;

function queueFootprints() {
  if (footTimer) clearTimeout(footTimer);
  footTimer = later(loadFootprints, 250);
}

async function loadFootprints() {
  if (!region.value || !mapSize().w) return;
  const { w, h } = mapSize();
  const radius = mapRadius(mv, w, h);
  if (!needsFootprints(footLoaded, mv, radius)) return;
  const my = ++footSeq;
  const asked = { lat: mv.lat, lon: mv.lon, radius };
  try {
    const j = await getJson(`${api('footprints')}?lat=${mv.lat}&lon=${mv.lon}&radius_m=${Math.round(radius * 1.6)}`);
    if (my !== footSeq) return;
    foot = j.features;
    footLoaded = { ...asked, radius: radius * 1.6 };
    schedule();
  } catch { /* the map still works without the outlines */ }
}

// ---------------------------------------------------------------------------
// Canvases
// ---------------------------------------------------------------------------
let pv = { k: 1, ox: 0, oy: 0 };
let mv = { lat: 0, lon: 0, mpp: 3 };
let raf = 0;
let drag: { which: 'photo' | 'map'; x: number; y: number; sx: number; sy: number; moved: boolean } | null = null;

function sizeOf(cv: HTMLCanvasElement | null, st: HTMLDivElement | null) {
  if (!cv || !st) return { w: 0, h: 0 };
  const w = st.clientWidth, h = st.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    cv.style.width = `${w}px`; cv.style.height = `${h}px`;
  }
  return { w, h };
}
const photoSize = () => sizeOf(photoCv.value, photoStage.value);
const mapSize = () => sizeOf(mapCv.value, mapStage.value);

function fitPhoto() {
  const { w, h } = photoSize();
  pv = fitRect(imagePx.value[0], imagePx.value[1], w, h);
  schedule();
}

function fitMap(includeCamera = false) {
  const { w, h } = mapSize();
  const pts: number[][] = pairs.value.map((p) => [p.lat, p.lon]);
  if (pose.value && (includeCamera || !pts.length)) pts.push([pose.value.lat, pose.value.lon]);
  if (!pts.length) {
    const r = regions.value.find((x) => x.id === region.value);
    if (r?.view && w) mv = { lat: r.view.lat, lon: r.view.lon, mpp: 3 };
  } else {
    const v = fitMapView(pts, w, h, 60);
    if (v) mv = v;
  }
  schedule();
  queueFootprints();
}

function schedule() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; draw(); });
}

function css(name: string) {
  const el = photoStage.value;
  return el ? getComputedStyle(el).getPropertyValue(name).trim() : '';
}

const HUES = ['#ff6b60', '#ffd60a', '#30d158', '#64d2ff', '#bf5af2', '#ff9f0a', '#5e9bff', '#ff6fb5'];
const hue = (i: number) => HUES[i % HUES.length];

function marker(c: CanvasRenderingContext2D, x: number, y: number, n: number, colour: string, ring = false) {
  c.beginPath(); c.arc(x, y, 9, 0, Math.PI * 2);
  c.fillStyle = 'rgba(0,0,0,0.55)'; c.fill();
  c.lineWidth = 2; c.strokeStyle = ring ? '#ff453a' : colour; c.stroke();
  c.fillStyle = '#fff'; c.font = '600 11px system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText(String(n), x, y + 0.5);
}

function drawPhoto() {
  const { w, h } = photoSize();
  const cv = photoCv.value;
  if (!cv || !w) return;
  const dpr = window.devicePixelRatio || 1;
  const c = cv.getContext('2d')!;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = css('--h-map') || '#0a0a0b';
  c.fillRect(0, 0, w, h);
  if (!img) return;
  c.drawImage(img, pv.ox, pv.oy, imagePx.value[0] * pv.k, imagePx.value[1] * pv.k);
  if (showBuildings.value && buildings.value.length && pose.value) {
    c.lineWidth = 1.5;
    buildings.value.forEach((b, bi) => {
      if (!b.contours?.length) return;
      c.strokeStyle = hue(bi); c.globalAlpha = 0.9;
      for (const ring of b.contours) {
        if (ring.length < 2) continue;
        c.beginPath();
        ring.forEach(([x, y]: number[], i: number) => {
          const [sx, sy] = photoToScreen(pv, x, y);
          if (i) c.lineTo(sx, sy); else c.moveTo(sx, sy);
        });
        c.closePath(); c.stroke();
      }
      if (b.visible_px > 600 && b.height_m && b.contours[0]?.length) {
        const [sx, sy] = photoToScreen(pv, b.contours[0][0][0], b.contours[0][0][1]);
        c.globalAlpha = 1; c.fillStyle = '#fff'; c.font = '600 11px system-ui, sans-serif';
        c.textAlign = 'left'; c.textBaseline = 'bottom';
        c.strokeStyle = 'rgba(0,0,0,0.7)'; c.lineWidth = 3; c.strokeText(`${Math.round(b.height_m)} m`, sx + 2, sy - 2);
        c.fillText(`${Math.round(b.height_m)} m`, sx + 2, sy - 2); c.lineWidth = 1.5;
      }
    });
    c.globalAlpha = 1;
  }
  pairs.value.forEach((p, i) => {
    const [sx, sy] = photoToScreen(pv, p.x, p.y);
    const r = resid(i);
    marker(c, sx, sy, i + 1, hue(i), r !== null && r > 10);
  });
  if (pending.value) {
    const [sx, sy] = photoToScreen(pv, pending.value.x, pending.value.y);
    c.beginPath(); c.arc(sx, sy, 11, 0, Math.PI * 2);
    c.lineWidth = 2.5; c.strokeStyle = '#ffd60a'; c.stroke();
  }
}

function drawMap() {
  const { w, h } = mapSize();
  const cv = mapCv.value;
  if (!cv || !w) return;
  const dpr = window.devicePixelRatio || 1;
  const c = cv.getContext('2d')!;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = css('--h-map') || '#0a0a0b';
  c.fillRect(0, 0, w, h);
  const fill = css('--h-fill') || 'rgba(160,140,255,0.35)';
  const edge = css('--h-edge') || 'rgba(190,170,255,0.7)';
  c.lineWidth = 1;
  for (const f of foot) {
    c.beginPath();
    f.ring.forEach(([lon, lat]: number[], i: number) => {
      const [sx, sy] = llToScreen(mv, w, h, lat, lon);
      if (i) c.lineTo(sx, sy); else c.moveTo(sx, sy);
    });
    c.closePath();
    c.fillStyle = fill; c.fill();
    c.strokeStyle = edge; c.stroke();
  }
  const p0 = pose.value;
  if (p0) {
    const [cx, cy] = llToScreen(mv, w, h, p0.lat, p0.lon);
    const wd = fovWedge(p0, 0.4 * Math.min(w, h) * mv.mpp);
    if (wd) {
      const [lx, ly] = llToScreen(mv, w, h, wd.left[0], wd.left[1]);
      const [rx, ry] = llToScreen(mv, w, h, wd.right[0], wd.right[1]);
      c.beginPath(); c.moveTo(cx, cy); c.lineTo(lx, ly); c.lineTo(rx, ry); c.closePath();
      c.fillStyle = 'rgba(10,132,255,0.14)'; c.fill();
      c.setLineDash([5, 4]); c.strokeStyle = 'rgba(10,132,255,0.85)'; c.lineWidth = 1.2;
      c.beginPath(); c.moveTo(cx, cy); c.lineTo(lx, ly); c.moveTo(cx, cy); c.lineTo(rx, ry); c.stroke();
    }
    c.strokeStyle = 'rgba(10,132,255,0.55)'; c.lineWidth = 1;
    for (const p of pairs.value) {
      const [sx, sy] = llToScreen(mv, w, h, p.lat, p.lon);
      c.beginPath(); c.moveTo(cx, cy); c.lineTo(sx, sy); c.stroke();
    }
    c.setLineDash([]);
    c.beginPath(); c.arc(cx, cy, 6, 0, Math.PI * 2); c.fillStyle = '#0a84ff'; c.fill();
    c.lineWidth = 2; c.strokeStyle = '#fff'; c.stroke();
  }
  candidates.value.forEach((cd, i) => {
    const [sx, sy] = llToScreen(mv, w, h, cd.lat, cd.lon);
    marker(c, sx, sy, i + 1, i === candIdx.value ? '#0a84ff' : '#bf5af2');
  });
  pairs.value.forEach((p, i) => {
    const [sx, sy] = llToScreen(mv, w, h, p.lat, p.lon);
    marker(c, sx, sy, i + 1, hue(i));
  });
}

function draw() { drawPhoto(); drawMap(); }

function pos(e: PointerEvent, which: 'photo' | 'map') {
  const r = (which === 'photo' ? photoCv : mapCv).value!.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

function onDown(e: PointerEvent, which: 'photo' | 'map') {
  if (e.button !== 0) return;
  const p = pos(e, which);
  drag = { which, x: p.x, y: p.y, sx: p.x, sy: p.y, moved: false };
  (which === 'photo' ? photoCv : mapCv).value?.setPointerCapture?.(e.pointerId);
  (which === 'photo' ? photoCv : mapCv).value?.focus();
}

function onMove(e: PointerEvent, which: 'photo' | 'map') {
  if (!drag || drag.which !== which) return;
  const p = pos(e, which);
  if (!drag.moved && Math.hypot(p.x - drag.sx, p.y - drag.sy) > 4) { drag.moved = true; panning.value = which; }
  if (!drag.moved) return;
  const dx = p.x - drag.x, dy = p.y - drag.y;
  drag.x = p.x; drag.y = p.y;
  if (which === 'photo') pv = { ...pv, ox: pv.ox + dx, oy: pv.oy + dy };
  else { mv = panMap(mv, dx, dy); queueFootprints(); }
  schedule();
}

function onUp(e: PointerEvent, which: 'photo' | 'map') {
  const d = drag;
  drag = null;
  panning.value = '';
  (which === 'photo' ? photoCv : mapCv).value?.releasePointerCapture?.(e.pointerId);
  if (!d || d.moved || d.which !== which) return;
  const p = pos(e, which);
  if (which === 'photo') clickPhoto(p.x, p.y); else clickMap(p.x, p.y);
}

function clickPhoto(sx: number, sy: number) {
  if (!img) return;
  const [x, y] = screenToPhoto(pv, sx, sy);
  if (!insidePhoto(x, y, imagePx.value[0], imagePx.value[1])) return;
  pending.value = { x, y };
  schedule();
}

function clickMap(sx: number, sy: number) {
  const { w, h } = mapSize();
  if (!pending.value) {
    const i = candidates.value.findIndex((cd) => {
      const [px, py] = llToScreen(mv, w, h, cd.lat, cd.lon);
      return Math.hypot(px - sx, py - sy) < 14;
    });
    if (i >= 0) useCandidate(i);
    return;
  }
  const [lat, lon] = screenToLl(mv, w, h, sx, sy);
  const fp = footprintAt(foot, lat, lon);
  pairs.value.push({ x: pending.value.x, y: pending.value.y, lat, lon, label: fp?.name || undefined });
  pending.value = null;
  if (pose.value) stale.value = true;
  schedule();
}

function onWheel(which: 'photo' | 'map') {
  return (e: WheelEvent) => {
    e.preventDefault();
    const r = (which === 'photo' ? photoCv : mapCv).value!.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const f = Math.exp(-e.deltaY * 0.0015);
    if (which === 'photo') {
      const k = Math.min(Math.max(pv.k * f, 0.05), 20);
      const g = k / pv.k;
      pv = { k, ox: sx - (sx - pv.ox) * g, oy: sy - (sy - pv.oy) * g };
    } else {
      const { w, h } = mapSize();
      mv = zoomMap(mv, w, h, sx, sy, f);
      queueFootprints();
    }
    schedule();
  };
}

let ro: ResizeObserver | null = null;
let themeObs: MutationObserver | null = null;
const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
const wheelPhoto = onWheel('photo');
const wheelMap = onWheel('map');
let lastW = 0;

function onKey(e: KeyboardEvent) {
  if (store.activeView !== 'photo') return;
  if (e.key === 'Escape' && pending.value) { pending.value = null; schedule(); }
}

onMounted(() => {
  photoCv.value?.addEventListener('wheel', wheelPhoto, { passive: false });
  mapCv.value?.addEventListener('wheel', wheelMap, { passive: false });
  window.addEventListener('keydown', onKey);
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      const w = photoSize().w;
      if (w && w !== lastW && img) fitPhoto();
      if (w && !lastW) fitMap();
      lastW = w;
      schedule();
    });
    if (photoStage.value) ro.observe(photoStage.value);
    if (mapStage.value) ro.observe(mapStage.value);
  }
  themeObs = new MutationObserver(schedule);
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  mq?.addEventListener?.('change', schedule);
});

watch(() => store.activeView, (v) => {
  if (v === 'photo') {
    if (!regions.value.length) loadRegions();
    nextTick(() => { fitPhoto(); fitMap(); });
  } else {
    pending.value = null;
  }
}, { immediate: true });
watch([pairs, pending, pose, buildings, candidates, candIdx, showBuildings, stale], schedule, { deep: true });

onBeforeUnmount(() => {
  timers.forEach((t) => clearTimeout(t));
  timers.clear();
  ro?.disconnect();
  themeObs?.disconnect();
  mq?.removeEventListener?.('change', schedule);
  photoCv.value?.removeEventListener('wheel', wheelPhoto);
  mapCv.value?.removeEventListener('wheel', wheelMap);
  window.removeEventListener('keydown', onKey);
  if (raf) cancelAnimationFrame(raf);
});
</script>

<style scoped>
/* Page tokens: dark by default (the app is dark); the light set follows the system setting,
   and :root[data-theme] forces either. */
.ph-page {
  --b-bg: #0e0e10; --b-panel: #1c1c1e; --b-card: #2c2c2e; --b-line: #3a3a3c;
  --b-text: #f2f2f7; --b-dim: #a1a1a8; --b-accent: #0a84ff; --b-sel: rgba(10, 132, 255, 0.18);
  --b-good: #30d158; --b-bad: #ff6b60; --b-warn: #ff9f0a; --b-off: #48484a;
  --h-map: #0a0a0b; --h-fill: rgba(150, 120, 255, 0.38); --h-edge: rgba(190, 170, 255, 0.75);
  display: flex; height: 100%; min-height: 0; background: var(--b-bg); color: var(--b-text);
  font: 13px/1.4 -apple-system, 'Segoe UI', system-ui, sans-serif;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme='dark']) .ph-page {
    --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
    --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
    --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
    --h-map: #f7f7fa; --h-fill: rgba(110, 80, 220, 0.3); --h-edge: rgba(90, 60, 200, 0.7);
  }
}
:root[data-theme='light'] .ph-page {
  --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
  --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
  --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
  --h-map: #f7f7fa; --h-fill: rgba(110, 80, 220, 0.3); --h-edge: rgba(90, 60, 200, 0.7);
}

.ph-left, .ph-right { background: var(--b-panel); padding: 14px; overflow-y: auto; flex: none; box-sizing: border-box; }
.ph-left { width: 250px; border-right: 1px solid var(--b-line); }
.ph-right { width: 290px; border-left: 1px solid var(--b-line); }
.ph-main { flex: 1; min-width: 0; display: flex; }
.ph-pane { flex: 1; min-width: 0; position: relative; overflow: hidden; background: var(--h-map); }
.ph-map { border-left: 1px solid var(--b-line); }
.ph-canvas { display: block; width: 100%; height: 100%; cursor: crosshair; touch-action: none; outline: none; }
.ph-canvas.pan { cursor: grabbing; }
.ph-canvas.aim { cursor: crosshair; }
.ph-canvas:focus-visible { box-shadow: inset 0 0 0 2px var(--b-accent); }
.ph-hidden { display: none; }

.ph-chip {
  position: absolute; left: 14px; top: 14px; background: rgba(28, 28, 30, 0.88); color: #f2f2f7;
  border-radius: 10px; padding: 8px 12px; pointer-events: none; font-size: 13px;
}
.ph-hint {
  position: absolute; left: 14px; right: 14px; bottom: 14px; background: var(--b-card);
  border: 1px solid var(--b-line); border-radius: 12px; padding: 9px 14px; color: var(--b-text);
}
.ph-fit {
  position: absolute; right: 14px; top: 14px; background: var(--b-card); color: var(--b-text);
  border: 1px solid var(--b-line); border-radius: 10px; padding: 7px 12px; font: inherit; cursor: pointer;
}
.ph-fit:focus-visible, .ph-btn:focus-visible, .ph-go:focus-visible, .ph-mini:focus-visible,
.ph-photo:focus-visible, .ph-cand:focus-visible, .ph-x:focus-visible, .ph-select:focus-visible {
  outline: 2px solid var(--b-accent); outline-offset: 2px;
}
.ph-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; margin: 0; color: var(--b-dim); pointer-events: none; text-align: center; padding: 0 24px; }

.ph-h2 {
  font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--b-dim);
  margin: 14px 4px 6px; font-weight: 600;
}
.ph-h2:first-child { margin-top: 0; }
.ph-card { background: var(--b-card); border-radius: 12px; padding: 4px 0; margin-bottom: 12px; }
.ph-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
  padding: 9px 14px; border: 0; border-bottom: 1px solid var(--b-line); background: none;
  color: inherit; font: inherit; text-align: left; box-sizing: border-box;
}
.ph-row:last-child { border-bottom: 0; }
.ph-empty-row { color: var(--b-dim); margin: 0; padding: 10px 14px; }
.ph-dim { color: var(--b-dim); }
.ph-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.ph-good { color: var(--b-good); }
.ph-warn { color: var(--b-warn); }
.ph-bad { color: var(--b-bad); }
.ph-note { color: var(--b-dim); font-size: 12px; padding: 0 4px; margin: 0 0 10px; }
.ph-note.bad { color: var(--b-bad); }

.ph-select {
  max-width: 70%; background: none; border: 0; color: var(--b-dim); font: inherit; text-align: right;
  text-align-last: right; cursor: pointer; padding: 2px 0;
}
.ph-select option { background: var(--b-card); color: var(--b-text); }

.ph-photo {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
  padding: 9px 14px; border: 0; border-bottom: 1px solid var(--b-line); background: none;
  color: inherit; font: inherit; text-align: left; cursor: pointer; box-sizing: border-box;
}
.ph-photo:last-child { border-bottom: 0; }
.ph-photo.on { background: var(--b-sel); }
.ph-photo.bad { opacity: 0.6; }
.ph-photo-main { display: flex; flex-direction: column; min-width: 0; }
.ph-photo-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ph-photo-sub { color: var(--b-dim); font-size: 12px; }
.ph-tick { color: var(--b-good); flex: none; }
.ph-photo:not(.on) .ph-tick { color: var(--b-dim); }

.ph-common { padding: 7px 14px; }
.ph-common-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; font-size: 12px; }
.ph-mini {
  flex: none; border: 0; border-radius: 8px; padding: 4px 10px; background: var(--b-accent); color: #fff;
  font: inherit; cursor: pointer;
}
.ph-mini:disabled { opacity: 0.5; cursor: default; }

.ph-switch {
  width: 38px; height: 22px; border-radius: 11px; border: 0; padding: 0; flex: none;
  background: var(--b-good); position: relative; cursor: pointer;
}
.ph-switch::after {
  content: ''; position: absolute; right: 2px; top: 2px; width: 18px; height: 18px;
  border-radius: 9px; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}
.ph-switch.off { background: var(--b-off); }
.ph-switch.off::after { right: auto; left: 2px; }
.ph-switch:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.ph-switch:disabled { opacity: 0.5; cursor: default; }

.ph-btn {
  display: block; width: 100%; text-align: center; padding: 11px 14px; border-radius: 10px; border: 0;
  background: var(--b-card); color: var(--b-text); font: inherit; font-weight: 600; margin: 0 0 8px;
  cursor: pointer; box-sizing: border-box;
}
.ph-btn.primary { background: var(--b-accent); color: #fff; }
.ph-btn:disabled, .ph-go:disabled { opacity: 0.5; cursor: default; }
.ph-go {
  display: block; width: 100%; padding: 12px; border-radius: 12px; border: 0; margin: 0 0 8px;
  background: linear-gradient(90deg, #0a84ff, #6e5be8); color: #fff; font: inherit; font-weight: 600;
  cursor: pointer;
}
.ph-run { padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }
.ph-bar { height: 6px; border-radius: 3px; background: var(--b-line); overflow: hidden; }
.ph-bar i { display: block; height: 100%; background: var(--b-accent); transition: width 0.3s; }
.ph-cand { cursor: pointer; font-size: 12px; }
.ph-cand.on { background: var(--b-sel); }

.ph-pair { justify-content: flex-start; padding: 6px 10px 6px 14px; }
.ph-pair-t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ph-dot {
  flex: none; width: 20px; height: 20px; border-radius: 10px; background: var(--b-sel); color: var(--b-text);
  display: inline-flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600;
}
.ph-x { flex: none; border: 0; background: none; color: var(--b-dim); font-size: 18px; line-height: 1; cursor: pointer; border-radius: 6px; padding: 0 6px; }

.ph-hhead, .ph-hrow { display: grid; grid-template-columns: 1fr auto auto; gap: 12px; padding: 7px 14px; align-items: baseline; }
.ph-hhead { color: var(--b-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--b-line); }
.ph-hhead span:not(:first-child), .ph-hrow span:not(:first-child) { text-align: right; min-width: 52px; }
.ph-hrow { border-bottom: 1px solid var(--b-line); }
.ph-hrow:last-child { border-bottom: 0; }
.ph-hname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }

@media (max-width: 1100px) {
  .ph-left { width: 220px; }
  .ph-right { width: 260px; }
}
</style>

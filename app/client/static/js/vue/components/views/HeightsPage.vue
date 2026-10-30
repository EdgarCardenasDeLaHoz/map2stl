<template>
  <!-- Street View heights page (Tools menu, F-UNIFY Phase 4): the saved reports of the selected
       region on a footprint map coloured by tier, and a run card to measure again. Hidden unless
       the view is 'heights' (modules/ui/view-management.js). Data: routers/skyline.py. -->
  <div id="heightsContainer" class="heights-page hidden">
    <aside class="ht-left" aria-label="Reports">
      <h2 class="ht-h2">Reports for this region</h2>
      <div class="ht-card" role="listbox" aria-label="Reports">
        <button v-for="r in reports" :key="r.dir" type="button" role="option"
                class="ht-row ht-report" :class="{ sel: r.dir === selectedDir }"
                :aria-selected="r.dir === selectedDir" @click="selectReport(r.dir)">
          <span class="ht-name">{{ reportTitle(r) }}<br>
            <span class="ht-v">{{ reportSub(r) }}</span></span>
          <span v-if="r.dir !== selectedDir" class="ht-v" aria-hidden="true">›</span>
        </button>
        <div v-if="!reports.length" class="ht-row ht-v">{{ reportsNote }}</div>
      </div>

      <template v-if="selReport">
        <h2 class="ht-h2">Selected report</h2>
        <div class="ht-card">
          <div v-for="t in rows" :key="t.key" class="ht-row">
            <span><span class="ht-dot" :style="{ background: t.color }"></span>{{ t.label }}</span>
            <span class="ht-v ht-num">{{ fmtCount(t.count) }}</span>
          </div>
        </div>
        <a class="ht-btn ghost" :href="selReport.index_url" target="_blank" rel="noopener">Open full report ↗</a>
      </template>
    </aside>

    <section class="ht-main" aria-label="Footprint map">
      <div ref="stage" class="ht-stage">
        <canvas ref="canvas" class="ht-canvas" :class="{ grab: dragging }"
                role="img" :aria-label="mapLabel"
                @pointermove="onMove" @pointerdown="onDown" @pointerup="onUp"
                @pointerleave="onLeave" @dblclick="resetView"></canvas>
        <p v-if="mapError" class="ht-empty ht-bad" role="alert">{{ mapError }}</p>
        <p v-else-if="!map" class="ht-empty ht-v">{{ selReport ? 'Loading the map…' : 'No report to show yet.' }}</p>
        <div v-if="hover && info" class="ht-hover" :style="hoverStyle" role="status">
          <b>{{ info.title }}</b>
          <div class="ht-v">{{ info.height }} · {{ info.tier }} · {{ info.panos }}</div>
          <div class="ht-v">{{ info.tag }}</div>
        </div>
        <div v-if="map" class="ht-pill">
          Colour by <b>tier</b> <span class="ht-sep">·</span> {{ fmtCount(measured) }} buildings measured
        </div>
      </div>
    </section>

    <aside class="ht-right" aria-label="Measure heights">
      <h2 class="ht-h2">New run</h2>
      <div class="ht-card">
        <div class="ht-row"><span>Area</span><span class="ht-v">{{ regionName || 'No region selected' }}</span></div>
        <div class="ht-row">
          <span>Panorama spacing</span>
          <span class="ht-v" title="The run picks the viewpoints itself, from the saved ones for this region.">Automatic</span>
        </div>
        <div class="ht-row">
          <span id="htCacheLbl">Use only cached images</span>
          <button type="button" role="switch" class="ht-switch" :class="{ off: !cacheOnly }"
                  :aria-checked="cacheOnly" aria-labelledby="htCacheLbl" :disabled="running"
                  @click="cacheOnly = !cacheOnly"></button>
        </div>
      </div>

      <div class="ht-card">
        <template v-if="est">
          <div class="ht-row"><span>Panoramas needed</span><span class="ht-v ht-num">{{ est.known ? fmtCount(est.panoramas) : '—' }}</span></div>
          <div class="ht-row"><span>Already cached</span><span class="ht-v ht-num">{{ est.known ? fmtCount(est.cached_panoramas) : '—' }}</span></div>
          <div class="ht-row">
            <span>Paid fetches</span>
            <span class="ht-num" :class="paidClass">{{ est.known ? fmtCount(cacheOnly ? 0 : est.paid_fetches) : '—' }}</span>
          </div>
          <div class="ht-row ht-block">
            <div class="ht-between"><span>Street View this month</span>
              <span class="ht-v ht-num">{{ capText(est.month_count, est.cap) }}</span></div>
            <div class="ht-bar"><i :class="{ hot: est.over_warn || capFrac > 0.8 }" :style="{ width: capFrac * 100 + '%' }"></i></div>
          </div>
        </template>
        <div v-else class="ht-row ht-v">{{ estError || 'Checking the cache…' }}</div>
      </div>
      <p v-if="est && !est.known" class="ht-note">{{ est.note }}</p>
      <p v-else-if="est && cacheOnly && est.paid_fetches > 0" class="ht-note">
        {{ fmtCount(est.paid_fetches) }} images are not cached. They are skipped, so some buildings keep their prior estimate.
      </p>
      <p v-else-if="est && !cacheOnly && est.paid_fetches > 0" class="ht-note ht-warn">
        This run may fetch {{ est.rough ? 'at least' : 'about' }} {{ fmtCount(est.paid_fetches) }} paid images. You will be asked first.
      </p>
      <p v-if="est && est.known && (est.rough || est.basis_report)" class="ht-note">{{ est.note }}</p>

      <button v-if="!running" type="button" class="ht-go" :disabled="!regionName || starting" @click="measure">
        Measure heights
      </button>
      <p v-if="startError" class="ht-note ht-bad" role="alert">{{ startError }}</p>

      <template v-if="job">
        <h2 class="ht-h2">{{ running ? 'Running' : 'Last run' }}</h2>
        <div class="ht-card ht-run" role="status">
          <div class="ht-runtop">
            <b>{{ heading }}</b>
          </div>
          <div class="ht-v">{{ job.status === 'error' ? (job.error || job.message) : runText }}</div>
          <div class="ht-bar"><i :style="{ width: (job.status === 'done' ? 100 : percent) + '%' }"></i></div>
          <div class="ht-runfoot">
            <span class="ht-v">{{ running ? gpu : '' }}</span>
            <button v-if="running" type="button" class="ht-cancel" @click="cancel">Cancel</button>
          </div>
        </div>
      </template>
    </aside>

    <div v-if="confirm" class="ht-scrim" @click.self="confirm = null">
      <div class="ht-dialog" role="alertdialog" aria-labelledby="htConfirmT" aria-describedby="htConfirmB">
        <h3 id="htConfirmT">Fetch paid Street View images?</h3>
        <p id="htConfirmB">{{ confirm.text }}</p>
        <p v-if="confirm.warnText" class="ht-bad">{{ confirm.warnText }}</p>
        <div class="ht-dlgbtns">
          <button type="button" class="ht-btn ghost" @click="confirm = null">Cancel</button>
          <button ref="okBtn" type="button" class="ht-btn" @click="confirmOk">Fetch and measure</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useAppStore } from '../../stores/app';
import {
  fmtCount, reportSub, tierRows, measuredCount, capFraction, capText, confirmInfo, stepHeading,
  stepText, runPercent, gpuText, boundsOf, fitView, project, unproject, zoomAt, panBy, hitTest,
  hoverInfo,
} from '../../../modules/ui/heights-format.js';

const store = useAppStore();
const regionName = computed(() => store.selectedRegion?.name || '');

const reports = ref<any[]>([]);
const reportsNote = ref('Pick a region first.');
const selectedDir = ref('');
const map = ref<any>(null);
const mapError = ref('');
const est = ref<any>(null);
const estError = ref('');
const cacheOnly = ref(true);
const job = ref<any>(null);
const starting = ref(false);
const startError = ref('');
const confirm = ref<any>(null);
const okBtn = ref<HTMLButtonElement | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const selReport = computed(() => reports.value.find((r) => r.dir === selectedDir.value) ?? null);
const rows = computed(() => tierRows(map.value?.tier_counts ?? selReport.value?.tier_counts, map.value?.tiers));
const measured = computed(() => measuredCount(map.value?.tier_counts ?? selReport.value?.tier_counts));
const running = computed(() => job.value?.status === 'running');
const capFrac = computed(() => capFraction(est.value?.month_count, est.value?.cap) ?? 0);
const paidClass = computed(() => (cacheOnly.value || !est.value?.paid_fetches) ? 'ht-good' : 'ht-warn');
const heading = computed(() => stepHeading(job.value, regionName.value || 'Run'));
const percent = computed(() => runPercent(job.value));
const gpu = computed(() => gpuText(job.value?.gpu));
const runText = computed(() => {
  const j = job.value;
  if (!j) return '';
  if (j.status === 'done') return 'Finished';
  if (j.status === 'cancelled') return 'Cancelled';
  return stepText(j) || j.message;
});
const mapLabel = computed(() => map.value
  ? `Footprint map of ${map.value.buildings.length} buildings coloured by height tier` : 'Footprint map');

function reportTitle(r: any) {
  // "v20 agree" -> "Cartagena v20 agree"
  return `${regionName.value} ${r.title}`.replace(/^ /, '');
}

async function getJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 160)}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------
async function loadReports(prefer?: string) {
  const name = regionName.value;
  reports.value = [];
  map.value = null;
  selectedDir.value = '';
  if (!name) { reportsNote.value = 'Pick a region first.'; return; }
  try {
    const j = await getJson(`/api/skyline/regions/${encodeURIComponent(name)}/reports`);
    reports.value = j.reports;
    reportsNote.value = 'No Street View report for this region yet.';
    const pick = j.reports.find((r: any) => r.dir === prefer) ?? j.reports[0];
    if (pick) await selectReport(pick.dir);
  } catch (e) {
    reportsNote.value = `Could not load reports: ${(e as Error).message}`;
  }
}

async function selectReport(dir: string) {
  selectedDir.value = dir;
  map.value = null;
  mapError.value = '';
  try {
    const m = await getJson(`/api/skyline/reports/${encodeURIComponent(dir)}/map`);
    if (dir !== selectedDir.value) return;
    map.value = m;
    await nextTick();
    resetView();
  } catch (e) {
    if (dir === selectedDir.value) mapError.value = `Could not load the map: ${(e as Error).message}`;
  }
}

async function loadEstimate() {
  est.value = null;
  estError.value = '';
  if (!regionName.value) { estError.value = 'Pick a region first.'; return; }
  try {
    est.value = await getJson(`/api/skyline/estimate?region=${encodeURIComponent(regionName.value)}`);
  } catch (e) {
    estError.value = `Could not estimate: ${(e as Error).message}`;
  }
}

function openPage() {
  loadReports();
  loadEstimate();
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
function stopPoll() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }

async function poll(taskId: string) {
  try {
    job.value = await getJson(`/api/skyline/runs/${taskId}`);
  } catch (e) {
    job.value = { status: 'error', error: (e as Error).message, message: '' };
    return;
  }
  if (job.value.status === 'running') {
    pollTimer = setTimeout(() => poll(taskId), 500);
  } else {
    loadEstimate();
    if (job.value.status === 'done') await loadReports(job.value.report);
  }
}

async function start(allowPaid: boolean) {
  stopPoll();
  starting.value = true;
  startError.value = '';
  try {
    const j = await getJson('/api/skyline/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region: regionName.value, allow_paid_fetches: allowPaid }),
    });
    job.value = j;
    await poll(j.task_id);
  } catch (e) {
    startError.value = (e as Error).message;
  } finally {
    starting.value = false;
  }
}

function measure() {
  if (cacheOnly.value) { start(false); return; }
  const info = confirmInfo(est.value, true);
  if (info) { confirm.value = info; nextTick(() => okBtn.value?.focus()); return; }
  // Nothing to pay for (or unknown): run, but still without paid permission.
  start(false);
}

function confirmOk() {
  confirm.value = null;
  start(true);
}

async function cancel() {
  stopPoll();
  if (!job.value?.task_id) return;
  try {
    job.value = await getJson(`/api/jobs/${job.value.task_id}/cancel`, { method: 'POST' });
    pollTimer = setTimeout(() => poll(job.value.task_id), 500);   // settle on the real final state
  } catch { /* already finished */ }
}

// ---------------------------------------------------------------------------
// Map canvas
// ---------------------------------------------------------------------------
const stage = ref<HTMLDivElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
let view: any = null;
let fitScale = 1;
const dragging = ref(false);
const hover = ref<any>(null);
const pointer = ref({ x: 0, y: 0 });
let dragFrom: { x: number; y: number } | null = null;
let raf = 0;

const info = computed(() => hoverInfo(hover.value, map.value?.tiers));
const hoverStyle = computed(() => {
  const w = stage.value?.clientWidth ?? 0;
  const h = stage.value?.clientHeight ?? 0;
  const x = Math.min(pointer.value.x + 16, Math.max(0, w - 250));
  const y = Math.min(pointer.value.y + 16, Math.max(0, h - 90));
  return { left: `${x}px`, top: `${y}px` };
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

function resetView() {
  const { w, h } = size();
  if (!w || !map.value) { view = null; schedule(); return; }
  view = fitView(boundsOf(map.value.buildings), w, h);
  fitScale = view.scale;
  schedule();
}

function schedule() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; draw(); });
}

const DRAW_ORDER = ['unlabelled', 'prior', 'tag', 'single', 'corroborated'];   // measured buildings on top

function draw() {
  const c = canvas.value;
  if (!c) return;
  const { w, h } = size();
  const ctx = c.getContext('2d');
  if (!ctx || !w) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(c);
  ctx.fillStyle = css.getPropertyValue('--h-map').trim() || '#0a0a0b';
  ctx.fillRect(0, 0, w, h);
  if (!map.value || !view) return;
  if (view.w !== w || view.h !== h) {
    view = { ...view, w, h };
  }
  const bs = map.value.buildings;
  for (const tier of DRAW_ORDER) {
    ctx.fillStyle = map.value.tiers?.[tier]?.color || '#999999';
    ctx.beginPath();
    for (const b of bs) {
      if (b.tier !== tier || !b.ring?.length) continue;
      b.ring.forEach(([lon, lat]: number[], i: number) => {
        const [x, y] = project(view, lon, lat);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.closePath();
    }
    ctx.fill();
  }
  const hb = hover.value;
  if (hb?.ring?.length) {
    ctx.beginPath();
    hb.ring.forEach(([lon, lat]: number[], i: number) => {
      const [x, y] = project(view, lon, lat);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = css.getPropertyValue('--h-hi').trim() || '#ffffff';
    ctx.stroke();
  }
}

function localXY(e: PointerEvent | WheelEvent) {
  const r = canvas.value!.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

function onMove(e: PointerEvent) {
  if (!view || !map.value) return;
  const p = localXY(e);
  pointer.value = p;
  if (dragFrom) {
    const dx = p.x - dragFrom.x, dy = p.y - dragFrom.y;
    view = panBy(view, dx, dy);
    dragFrom = p;
    hover.value = null;
    schedule();
    return;
  }
  const [lon, lat] = unproject(view, p.x, p.y);
  const tol = 3 / (view.kx * view.scale);              // 3 px, so thin buildings are reachable
  const hit = hitTest(map.value.buildings, lon, lat, tol);
  if (hit !== hover.value) { hover.value = hit; schedule(); }
}

function onDown(e: PointerEvent) {
  if (!view) return;
  dragFrom = localXY(e);
  moved = false;
  dragging.value = true;
  canvas.value?.setPointerCapture?.(e.pointerId);
}

function onUp(e: PointerEvent) {
  dragFrom = null;
  dragging.value = false;
  canvas.value?.releasePointerCapture?.(e.pointerId);
}

function onLeave() {
  if (dragFrom) return;
  if (hover.value) { hover.value = null; schedule(); }
}

function onWheel(e: WheelEvent) {
  if (!view) return;
  e.preventDefault();
  const p = localXY(e);
  view = zoomAt(view, p.x, p.y, Math.exp(-e.deltaY * 0.0015), fitScale);
  hover.value = null;
  schedule();
}

let ro: ResizeObserver | null = null;
let themeObs: MutationObserver | null = null;
const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
onMounted(() => {
  canvas.value?.addEventListener('wheel', onWheel, { passive: false });
  if (stage.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      const { w } = size();
      if (w && map.value && !view) resetView(); else schedule();
    });
    ro.observe(stage.value);
  }
  themeObs = new MutationObserver(schedule);
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  mq?.addEventListener?.('change', schedule);
});

// Load when the page opens (and again on every visit: reports may have changed) and when the
// region changes while it is open.
watch(() => store.activeView, (v) => {
  if (v === 'heights') { openPage(); nextTick(resetView); } else { stopPoll(); }
}, { immediate: true });
watch(regionName, () => { if (store.activeView === 'heights') { job.value = null; openPage(); } });
watch(map, schedule);
onBeforeUnmount(() => {
  stopPoll();
  ro?.disconnect();
  themeObs?.disconnect();
  mq?.removeEventListener?.('change', schedule);
  canvas.value?.removeEventListener('wheel', onWheel);
  if (raf) cancelAnimationFrame(raf);
});
</script>

<style scoped>
/* Page tokens: dark by default (the app is dark); the light set follows the system setting,
   and :root[data-theme] forces either. The shared header stays dark in both. */
.heights-page {
  --b-bg: #0e0e10; --b-panel: #1c1c1e; --b-card: #2c2c2e; --b-line: #3a3a3c;
  --b-text: #f2f2f7; --b-dim: #a1a1a8; --b-accent: #0a84ff; --b-sel: rgba(10, 132, 255, 0.18);
  --b-good: #30d158; --b-bad: #ff6b60; --b-warn: #ff9f0a; --b-off: #48484a;
  --h-map: #0a0a0b; --h-hi: #ffffff;
  display: flex; height: 100%; min-height: 0; background: var(--b-bg); color: var(--b-text);
  font: 13px/1.4 -apple-system, 'Segoe UI', system-ui, sans-serif;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme='dark']) .heights-page {
    --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
    --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
    --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
    --h-map: #f7f7fa; --h-hi: #1c1c1e;
  }
}
:root[data-theme='light'] .heights-page {
  --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
  --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
  --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
  --h-map: #f7f7fa; --h-hi: #1c1c1e;
}

.ht-left, .ht-right { background: var(--b-panel); padding: 14px; overflow-y: auto; flex: none; box-sizing: border-box; }
.ht-left { width: 280px; border-right: 1px solid var(--b-line); }
.ht-right { width: 300px; border-left: 1px solid var(--b-line); }
.ht-main { flex: 1; min-width: 0; position: relative; }
.ht-stage { position: absolute; inset: 0; overflow: hidden; background: var(--h-map); }
.ht-canvas { display: block; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
.ht-canvas.grab { cursor: grabbing; }

.ht-h2 {
  font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--b-dim);
  margin: 14px 4px 6px; font-weight: 600;
}
.ht-h2:first-child { margin-top: 0; }
.ht-card { background: var(--b-card); border-radius: 12px; padding: 4px 0; margin-bottom: 12px; }
.ht-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
  padding: 9px 14px; border: 0; border-bottom: 1px solid var(--b-line); background: none;
  color: inherit; font: inherit; text-align: left; box-sizing: border-box;
}
.ht-row:last-child { border-bottom: 0; }
.ht-report { cursor: pointer; }
.ht-report:hover, .ht-report.sel { background: var(--b-sel); }
.ht-report:focus-visible { outline: 2px solid var(--b-accent); outline-offset: -2px; }
.ht-name { min-width: 0; }
.ht-v { color: var(--b-dim); }
.ht-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.ht-good { color: var(--b-good); }
.ht-warn { color: var(--b-warn); }
.ht-bad { color: var(--b-bad); }
.ht-note { color: var(--b-dim); font-size: 12px; padding: 0 4px; margin: 0 0 10px; }
.ht-note.ht-warn { color: var(--b-warn); }
.ht-note.ht-bad { color: var(--b-bad); }
.ht-dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 9px; vertical-align: -1px; }
.ht-block { display: block; }
.ht-between { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 8px; }

.ht-bar { height: 6px; border-radius: 3px; background: var(--b-line); overflow: hidden; }
.ht-bar i { display: block; height: 100%; background: var(--b-accent); transition: width 0.3s; }
.ht-bar i.hot { background: var(--b-warn); }

.ht-switch {
  width: 38px; height: 22px; border-radius: 11px; border: 0; padding: 0; flex: none;
  background: var(--b-good); position: relative; cursor: pointer;
}
.ht-switch::after {
  content: ''; position: absolute; right: 2px; top: 2px; width: 18px; height: 18px;
  border-radius: 9px; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}
.ht-switch.off { background: var(--b-off); }
.ht-switch.off::after { right: auto; left: 2px; }
.ht-switch:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.ht-switch:disabled { opacity: 0.5; cursor: default; }

.ht-btn {
  display: block; text-align: center; padding: 10px 14px; border-radius: 10px; border: 0;
  background: var(--b-accent); color: #fff; font: inherit; font-weight: 600; margin: 8px 0;
  cursor: pointer; text-decoration: none; box-sizing: border-box;
}
.ht-left .ht-btn { width: 100%; }
.ht-btn.ghost { background: var(--b-card); color: var(--b-text); border: 1px solid var(--b-line); }
.ht-btn:focus-visible, .ht-go:focus-visible, .ht-cancel:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.ht-go {
  display: block; width: 100%; padding: 12px; border-radius: 12px; border: 0; margin: 6px 0 10px;
  background: linear-gradient(90deg, #0a84ff, #6e5be8); color: #fff; font: inherit; font-weight: 600;
  cursor: pointer;
}
.ht-go:disabled { opacity: 0.5; cursor: default; }

.ht-run { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.ht-runtop { display: flex; justify-content: space-between; gap: 8px; }
.ht-runfoot { display: flex; justify-content: space-between; align-items: center; min-height: 20px;
  border-top: 1px solid var(--b-line); padding-top: 8px; }
.ht-cancel { background: none; border: 0; color: var(--b-bad); font: inherit; cursor: pointer; padding: 0; }

.ht-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; margin: 0; pointer-events: none; }
.ht-hover {
  position: absolute; z-index: 3; width: 230px; box-sizing: border-box; pointer-events: none;
  background: var(--b-card); color: var(--b-text); border: 1px solid var(--b-line);
  border-radius: 12px; padding: 10px 14px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
}
.ht-pill {
  position: absolute; left: 16px; bottom: 16px; z-index: 2; background: var(--b-card);
  border: 1px solid var(--b-line); border-radius: 12px; padding: 9px 14px; color: var(--b-dim);
}
.ht-pill b { color: var(--b-text); }
.ht-sep { margin: 0 4px; }

.ht-scrim { position: fixed; inset: 0; z-index: 50; background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center; }
.ht-dialog { width: 360px; max-width: calc(100vw - 32px); background: var(--b-card); color: var(--b-text);
  border-radius: 14px; padding: 18px; box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5); }
.ht-dialog h3 { margin: 0 0 8px; font-size: 16px; }
.ht-dialog p { margin: 0 0 8px; }
.ht-dlgbtns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.ht-dlgbtns .ht-btn { margin: 0; }

@media (max-width: 1100px) {
  .ht-left { width: 230px; }
  .ht-right { width: 260px; }
}
</style>

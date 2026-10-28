<template>
  <!-- Benchmark page (Tools menu, F-UNIFY Phase 4): score the height pipeline against the
       truth the project has cached. Same shell as the other pages; hidden unless the view is
       'benchmark' (modules/ui/view-management.js). Data: routers/benchmark.py. -->
  <div id="benchmarkContainer" class="benchmark-page hidden">
    <aside class="bm-left" aria-label="Benchmark regions">
      <h2 class="bm-h2">Benchmark regions</h2>
      <div class="bm-card" role="listbox" aria-label="Regions">
        <button v-for="r in regions" :key="r.id" type="button" role="option" class="bm-row bm-region"
                :class="{ sel: r.id === selected }" :aria-selected="r.id === selected"
                @click="select(r.id)">
          <span class="bm-name">{{ r.name }}<br><span class="bm-v">{{ r.sub }}</span></span>
          <span class="bm-mae" :class="{ 'bm-v': r.mae_m == null }">{{ fmtMeters(r.mae_m) }}</span>
        </button>
        <div v-if="!regions.length && !loadError" class="bm-row bm-v">Loading…</div>
      </div>
      <p class="bm-note">Numbers are mean absolute error of the final heights against the truth source.</p>
    </aside>

    <section class="bm-main" aria-live="polite">
      <p v-if="loadError" class="bm-error" role="alert">{{ loadError }}</p>
      <template v-else-if="detail">
        <div class="bm-title">
          <h1>{{ detail.name }}</h1>
          <span v-if="cur" class="bm-v">truth: {{ cur.truth }} · scored {{ fmtStamp(cur.stamp) }}</span>
          <span v-else class="bm-v">{{ detail.sub }}</span>
        </div>

        <template v-if="cur">
          <div class="bm-cards">
            <div class="bm-card bm-stat">
              <div class="bm-v">Final heights MAE</div>
              <div class="bm-big">{{ fmtMeters(cur.mae_m) }}</div>
              <div v-if="delta.kind !== 'none'" :class="['bm-delta', delta.kind]">{{ delta.text }}</div>
              <div v-else class="bm-v">first score on record</div>
            </div>
            <div class="bm-card bm-stat">
              <div class="bm-v">{{ withinLabel(cur.within_kind) }}</div>
              <div class="bm-big">{{ fmtShare(cur.within) }}</div>
              <div class="bm-v">of {{ fmtCount(cur.buildings) }} buildings with truth</div>
            </div>
            <div class="bm-card bm-stat">
              <div class="bm-v">Measured by Street View</div>
              <div class="bm-big">{{ fmtCount(measured) }}</div>
              <div class="bm-v">{{ measuredShare }}</div>
            </div>
          </div>

          <div class="bm-card bm-tablecard">
            <table>
              <thead>
                <tr>
                  <th>Height source</th><th class="n">Buildings</th><th class="n">MAE</th>
                  <th class="n">Bias</th><th class="n">{{ withinLabel(cur.within_kind) }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in cur.rows" :key="row.key" :title="row.hint">
                  <td><span class="bm-dot" :style="{ background: row.color }"></span>{{ row.label }}</td>
                  <td class="n">{{ fmtCount(row.buildings) }}</td>
                  <td class="n">{{ fmtMeters(row.mae_m) }}</td>
                  <td class="n">{{ fmtSignedMeters(row.bias_m) }}</td>
                  <td class="n">{{ fmtShare(row.within) }}</td>
                </tr>
                <tr v-if="!cur.rows.length"><td colspan="5" class="bm-v">No per-source breakdown in this score.</td></tr>
              </tbody>
            </table>
          </div>
          <p class="bm-note">
            Rows are the verification tier of each building's published height, scored against the same truth.
            <span v-if="cur.within_kind !== '3m'">This score predates the 3 m column: "Within 25 %" counts heights within a quarter of the true height.</span>
          </p>
          <p v-if="cur.note" class="bm-note bm-warn">{{ cur.note }}</p>
        </template>
        <div v-else class="bm-card bm-empty">
          <b>{{ detail.name }} has not been scored yet.</b>
          <span class="bm-v">{{ detail.id === 'cartagena'
            ? 'Cartagena has weak truth (3D Tiles are flat there), so it is not scored.'
            : 'Run the region first, then use Score on the right.' }}</span>
        </div>
      </template>
    </section>

    <aside class="bm-right" aria-label="Score again">
      <h2 class="bm-h2">Score again</h2>
      <div class="bm-card">
        <label class="bm-row">
          <span>Heights from</span>
          <select v-model="headsFrom" class="bm-select" :disabled="running || !detail"
                  aria-label="Heights from">
            <option v-for="o in detail?.heights_from || []" :key="o.id" :value="o.id">{{ o.label }}</option>
          </select>
        </label>
        <div class="bm-row">
          <span>Truth</span>
          <span class="bm-v">{{ cur ? 'Cached ' + cur.truth : 'Cached' }}</span>
        </div>
        <div class="bm-row">
          <span id="bmPaidLbl">Allow paid fetches</span>
          <button type="button" role="switch" class="bm-switch" :class="{ off: !paid }"
                  :aria-checked="paid" aria-labelledby="bmPaidLbl" :disabled="running"
                  @click="paid = !paid"></button>
        </div>
      </div>
      <p v-if="paid" class="bm-note bm-warn">
        {{ gapText || 'Checking what is missing…' }} Fetches use the monthly Google 3D Tiles allowance.
      </p>
      <p v-else class="bm-note">Off: scores only what is already cached, and fetches nothing.</p>

      <button v-if="!running" type="button" class="bm-btn" :disabled="!detail" @click="score">Score</button>
      <button v-else type="button" class="bm-btn ghost" @click="cancel">Cancel</button>
      <div v-if="job" class="bm-job" :class="{ err: job.status === 'error' }" role="status">
        <div v-if="running" class="bm-bar"><i :style="{ width: job.progress + '%' }"></i></div>
        <span>{{ job.status === 'error' ? (job.error || job.message) : job.message }}</span>
      </div>

      <template v-if="usageFrac !== null">
        <h2 class="bm-h2">3D Tiles this month</h2>
        <div class="bm-card">
          <div class="bm-row bm-block">
            Root requests <span class="bm-v bm-right-v">{{ usageText(usage.value, usage.limit) }}</span>
            <div class="bm-bar"><i :class="{ hot: usageFrac > 0.8 }" :style="{ width: usageFrac * 100 + '%' }"></i></div>
          </div>
        </div>
      </template>

      <a v-if="cur?.report_url" class="bm-btn ghost" :href="cur.report_url" target="_blank" rel="noopener">Open report ↗</a>
      <span v-else class="bm-btn ghost disabled" aria-disabled="true">Open report ↗</span>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useAppStore } from '../../stores/app';
import {
  fmtMeters, fmtSignedMeters, fmtShare, fmtCount, fmtStamp, deltaInfo, withinLabel,
  usageFraction, usageText, gapHint,
} from '../../../modules/ui/benchmark-format.js';

const store = useAppStore();
const regions = ref<any[]>([]);
const selected = ref('');
const detail = ref<any>(null);
const loadError = ref('');
const headsFrom = ref('latest');
const paid = ref(false);
const gap = ref<any>(null);
const usage = ref<{ value: number | null; limit: number }>({ value: null, limit: 1000 });
const job = ref<any>(null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const cur = computed(() => detail.value?.current ?? null);
const delta = computed(() => deltaInfo(detail.value?.delta_mae_m, detail.value?.previous?.stamp));
const running = computed(() => job.value?.status === 'running');
const usageFrac = computed(() => usageFraction(usage.value.value as number, usage.value.limit));
const gapText = computed(() => gapHint(gap.value));
// Street View readings: the corroborated and single-reading tiers.
const measured = computed(() => (cur.value?.rows ?? [])
  .filter((r: any) => r.key === 'corroborated' || r.key === 'single')
  .reduce((s: number, r: any) => s + r.buildings, 0));
const measuredShare = computed(() => {
  const n = cur.value?.buildings;
  return n ? `${Math.round((100 * measured.value) / n)} % of buildings` : '';
});

async function getJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 160)}`);
  return res.json();
}

async function loadRegions() {
  try {
    regions.value = (await getJson('/api/benchmark/regions')).regions;
    loadError.value = '';
    if (!selected.value && regions.value.length) {
      await select((regions.value.find((r) => r.mae_m != null) ?? regions.value[0]).id);
    }
  } catch (e) {
    loadError.value = `Could not load the benchmark: ${(e as Error).message}`;
  }
}

async function select(id: string) {
  selected.value = id;
  headsFrom.value = 'latest';
  job.value = null;
  try {
    detail.value = await getJson(`/api/benchmark/regions/${encodeURIComponent(id)}`);
  } catch (e) {
    detail.value = null;
    loadError.value = `Could not load ${id}: ${(e as Error).message}`;
  }
}

async function loadUsage() {
  try { usage.value = await getJson('/api/benchmark/usage'); } catch { /* the bar stays hidden */ }
}

watch([paid, headsFrom, selected], async () => {
  gap.value = null;
  if (!paid.value || !selected.value) return;
  try {
    gap.value = (await getJson(
      `/api/benchmark/regions/${encodeURIComponent(selected.value)}/gap?heights_from=${encodeURIComponent(headsFrom.value)}`)).gap;
  } catch { /* the hint just stays on "checking" */ }
});

function stopPoll() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }

async function poll(taskId: string) {
  try {
    job.value = await getJson(`/api/jobs/${taskId}`);
  } catch (e) {
    job.value = { status: 'error', error: (e as Error).message, message: '', progress: 0 };
    return;
  }
  if (job.value.status === 'running') {
    pollTimer = setTimeout(() => poll(taskId), 500);
  } else if (job.value.status === 'done') {
    const id = selected.value;
    await loadRegions();
    if (id === selected.value) detail.value = await getJson(`/api/benchmark/regions/${encodeURIComponent(id)}`);
  }
}

async function score() {
  stopPoll();
  try {
    const j = await getJson('/api/benchmark/score', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region: selected.value, heights_from: headsFrom.value, allow_paid_fetches: paid.value }),
    });
    job.value = j;
    await poll(j.task_id);
  } catch (e) {
    job.value = { status: 'error', error: (e as Error).message, message: '', progress: 0 };
  }
}

async function cancel() {
  stopPoll();
  if (!job.value?.task_id) return;
  try {
    job.value = await getJson(`/api/jobs/${job.value.task_id}/cancel`, { method: 'POST' });
  } catch { /* already finished */ }
}

// Load when the page opens (and again on every visit: scores may have changed).
watch(() => store.activeView, (v) => {
  if (v === 'benchmark') { loadRegions(); loadUsage(); } else { stopPoll(); }
}, { immediate: true });
onBeforeUnmount(stopPoll);
</script>

<style scoped>
/* Page tokens: dark by default (the app is dark); the light set follows the system setting,
   and :root[data-theme] forces either. The shared header stays dark in both. */
.benchmark-page {
  --b-bg: #0e0e10; --b-panel: #1c1c1e; --b-card: #2c2c2e; --b-line: #3a3a3c;
  --b-text: #f2f2f7; --b-dim: #a1a1a8; --b-accent: #0a84ff; --b-sel: rgba(10, 132, 255, 0.18);
  --b-good: #30d158; --b-bad: #ff6b60; --b-warn: #ff9f0a; --b-off: #48484a;
  display: flex; height: 100%; min-height: 0; background: var(--b-bg); color: var(--b-text);
  font: 13px/1.4 -apple-system, 'Segoe UI', system-ui, sans-serif;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme='dark']) .benchmark-page {
    --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
    --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
    --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
  }
}
:root[data-theme='light'] .benchmark-page {
  --b-bg: #f2f2f7; --b-panel: #e9e9ef; --b-card: #ffffff; --b-line: #d8d8de;
  --b-text: #1c1c1e; --b-dim: #5f5f68; --b-accent: #0a6fe0; --b-sel: rgba(10, 111, 224, 0.12);
  --b-good: #1a7f37; --b-bad: #c62828; --b-warn: #a15c00; --b-off: #c7c7cc;
}

.bm-left, .bm-right { background: var(--b-panel); padding: 14px; overflow-y: auto; flex: none; }
.bm-left { width: 300px; border-right: 1px solid var(--b-line); }
.bm-right { width: 320px; border-left: 1px solid var(--b-line); }
.bm-main { flex: 1; min-width: 0; padding: 20px; overflow: auto; }

.bm-h2 {
  font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--b-dim);
  margin: 14px 4px 6px; font-weight: 600;
}
.bm-h2:first-child { margin-top: 0; }
.bm-card { background: var(--b-card); border-radius: 12px; padding: 4px 0; margin-bottom: 12px; }
.bm-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
  padding: 9px 14px; border: 0; border-bottom: 1px solid var(--b-line); background: none;
  color: inherit; font: inherit; text-align: left;
}
.bm-row:last-child { border-bottom: 0; }
.bm-region { cursor: pointer; }
.bm-region:hover { background: var(--b-sel); }
.bm-region:focus-visible { outline: 2px solid var(--b-accent); outline-offset: -2px; }
.bm-region.sel { background: var(--b-sel); }
.bm-name { min-width: 0; }
.bm-mae { white-space: nowrap; flex: none; font-variant-numeric: tabular-nums; }
.bm-v { color: var(--b-dim); }
.bm-note { color: var(--b-dim); font-size: 12px; padding: 4px 4px 0; margin: 0 0 8px; }
.bm-warn { color: var(--b-warn); }
.bm-error { color: var(--b-bad); }

.bm-title { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.bm-title h1 { margin: 0; font-size: 22px; font-weight: 700; }
.bm-cards { display: flex; gap: 12px; margin: 16px 0 12px; }
.bm-cards .bm-card { margin-bottom: 0; }
.bm-stat { flex: 1; min-width: 0; padding: 14px; }
.bm-big { font-size: 28px; font-weight: 700; line-height: 1.2; }
.bm-delta.better { color: var(--b-good); }
.bm-delta.worse { color: var(--b-bad); }
.bm-delta.same { color: var(--b-dim); }
.bm-empty { margin-top: 16px; padding: 18px 16px; display: flex; flex-direction: column; gap: 4px; }

.bm-tablecard { padding: 4px 0; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 9px 14px; text-align: left; border-bottom: 1px solid var(--b-line); white-space: nowrap; }
th { color: var(--b-dim); font-weight: 600; font-size: 12px; }
tr:last-child td { border-bottom: 0; }
.n { text-align: right; font-variant-numeric: tabular-nums; }
.bm-dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 8px; vertical-align: -1px; }

.bm-select {
  background: none; border: 0; color: var(--b-dim); font: inherit; text-align: right;
  cursor: pointer; max-width: 170px; text-overflow: ellipsis;
}
.bm-select:focus-visible { outline: 2px solid var(--b-accent); border-radius: 6px; }
.bm-select option { color: #1c1c1e; background: #fff; }
.bm-switch {
  width: 38px; height: 22px; border-radius: 11px; border: 0; padding: 0; flex: none;
  background: var(--b-good); position: relative; cursor: pointer;
}
.bm-switch::after {
  content: ''; position: absolute; right: 2px; top: 2px; width: 18px; height: 18px;
  border-radius: 9px; background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}
.bm-switch.off { background: var(--b-off); }
.bm-switch.off::after { right: auto; left: 2px; }
.bm-switch:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }
.bm-switch:disabled { opacity: 0.5; cursor: default; }

.bm-btn {
  display: block; width: 100%; text-align: center; padding: 10px; border-radius: 10px; border: 0;
  background: var(--b-accent); color: #fff; font: inherit; font-weight: 600; margin: 8px 0;
  cursor: pointer; text-decoration: none; box-sizing: border-box;
}
.bm-btn:disabled { opacity: 0.5; cursor: default; }
.bm-btn.ghost { background: var(--b-card); color: var(--b-text); font-weight: 500; border: 1px solid var(--b-line); }
.bm-btn.ghost.disabled { opacity: 0.5; cursor: default; }
.bm-btn:focus-visible { outline: 2px solid var(--b-accent); outline-offset: 2px; }

.bm-job { font-size: 12px; color: var(--b-dim); margin: 0 4px 8px; display: flex; flex-direction: column; gap: 6px; }
.bm-job.err { color: var(--b-bad); }
.bm-bar { height: 6px; border-radius: 3px; background: var(--b-line); overflow: hidden; }
.bm-bar i { display: block; height: 100%; background: var(--b-accent); transition: width 0.3s; }
.bm-bar i.hot { background: var(--b-warn); }
.bm-block { display: block; }
.bm-right-v { float: right; }

@media (max-width: 1100px) {
  .bm-left { width: 230px; }
  .bm-right { width: 270px; }
  .bm-cards { flex-wrap: wrap; }
}
</style>

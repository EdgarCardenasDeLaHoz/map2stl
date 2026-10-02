<template>
  <div class="main-header">
    <!-- Short title so the header stays on one line from 1024 px up (UI audit 2026-09-30). -->
    <div class="main-title" title="3D Maps: globe &amp; map selector">3D Maps</div>
    <!-- Context pill: what is open (design guidelines §2). Click: back to the region list. -->
    <button v-if="regionPill" type="button" class="ctx-pill" id="regionContextPill"
            :title="`${regionPill.name}: choose another region`" @click="goExplore">
      <span aria-hidden="true">📍</span> <b>{{ regionPill.name }}</b>
      <span class="ctx-sub">{{ regionPill.size }}</span> <span aria-hidden="true">▾</span>
    </button>
    <div class="tabs">
      <!-- data-view attributes must stay — window.switchView() reads them via querySelector -->
      <button class="tab active" data-view="map" id="tabExplore">
        <span class="tab-step" id="tabStep1">1</span> Explore
      </button>
      <span class="tab-arrow">›</span>
      <button class="tab" data-view="dem" id="tabEdit">
        <span class="tab-step" id="tabStep2">2</span> Edit
      </button>
      <span class="tab-arrow">›</span>
      <button class="tab" data-view="model" id="tabExtrude">
        <span class="tab-step" id="tabStep3">3</span> Extrude
      </button>
    </div>

    <div class="header-actions">
      <!-- The whole job in one click (modules/ui/make-printable.js). -->
      <button type="button" id="makePrintableBtn" class="magic-btn"
              title="Pick a preset for this region, load it, size it to your printer bed and open the 3D model"
              @click="makePrintable">✨<span class="hdr-btn-label"> Make it printable</span></button>
      <!-- Region settings save themselves (modules/ui/presets.js, autosave); this says
           whether they have. No Save button: design guidelines §1.5. -->
      <span id="saveSettingsStatus" class="save-status" role="status" aria-live="polite"></span>
      <!-- ⚙ Settings: mode, tools, keys, diagnostics, guides and docs (SettingsSheet.vue). -->
      <button type="button" id="settingsSheetBtn" class="gear-btn" aria-label="Settings" title="Settings"
              @click="settingsOpen = true">⚙</button>
    </div>
    <SettingsSheet :open="settingsOpen" @close="settingsOpen = false" @keys="openKeys" @diag="openDiag" />

    <!-- Keys modal -->
    <div v-if="keysOpen" class="keys-overlay" @click.self="keysOpen = false">
      <div class="keys-modal">
        <div class="keys-modal-header">
          <span>🔑 Service Authentication</span>
          <button class="keys-close-btn" aria-label="Close keys dialog" title="Close" @click="keysOpen = false">✕</button>
        </div>

        <div class="keys-modal-body">
          <!-- Earth Engine -->
          <div class="keys-service">
            <div class="keys-service-header">
              <span class="keys-service-name">🌍 Google Earth Engine</span>
              <span v-if="eeStatus === null" class="keys-badge keys-badge-checking">checking…</span>
              <span v-else-if="eeStatus" class="keys-badge keys-badge-ok">✓ Authenticated</span>
              <span v-else class="keys-badge keys-badge-error">✗ Not authenticated</span>
            </div>
            <p class="keys-service-desc">Required for ESA WorldCover land cover and water mask.</p>

            <div v-if="eeStatus === false" class="keys-instructions">
              <!-- Step 1: request an auth URL from the server -->
              <template v-if="!eeAuthUrl">
                <p>Authenticate without leaving the browser — click below, approve access on Google's page, then paste the code back here.</p>
                <button class="btn btn-secondary keys-save-btn" :disabled="eeStarting" @click="startEeAuth">
                  {{ eeStarting ? 'Starting…' : '🌍 Authenticate with Google' }}
                </button>
              </template>
              <!-- Step 2: open the URL, paste the resulting code -->
              <template v-else>
                <p>1. <a :href="eeAuthUrl" target="_blank" rel="noopener" @click="eeUrlOpened = true">Open the Google authorization page</a> and approve access.</p>
                <p>2. Paste the code Google shows you:</p>
                <div class="keys-input-row">
                  <input
                    v-model="eeCode"
                    type="text"
                    class="keys-input"
                    placeholder="Paste authorization code…"
                    @keyup.enter="completeEeAuth"
                  />
                  <button class="btn btn-secondary keys-save-btn" :disabled="!eeCode.trim() || eeCompleting" @click="completeEeAuth">
                    {{ eeCompleting ? 'Verifying…' : 'Submit' }}
                  </button>
                </div>
                <p class="keys-hint">
                  <a href="#" @click.prevent="startEeAuth">Get a new link</a> if this one expired.
                </p>
              </template>
              <p v-if="eeMsg" :class="eeMsgOk ? 'keys-msg-ok' : 'keys-msg-err'">{{ eeMsg }}</p>
              <p class="keys-hint">Don't have an account? <a href="https://earthengine.google.com/signup/" target="_blank" rel="noopener">Sign up free</a> (non-commercial use).</p>
            </div>
          </div>

          <hr class="keys-divider" />

          <!-- OpenTopography -->
          <div class="keys-service">
            <div class="keys-service-header">
              <span class="keys-service-name">🗻 OpenTopography</span>
              <span v-if="otopoStatus === null" class="keys-badge keys-badge-checking">checking…</span>
              <span v-else-if="otopoStatus" class="keys-badge keys-badge-ok">✓ Key configured</span>
              <span v-else class="keys-badge keys-badge-error">✗ No key</span>
            </div>
            <p class="keys-service-desc">Required for downloading SRTM, Copernicus, and ALOS DEM tiles.</p>
            <div class="keys-input-row">
              <input
                v-model="otopoKey"
                type="text"
                class="keys-input"
                placeholder="Paste API key…"
                @keyup.enter="saveOtopoKey"
              />
              <button class="btn btn-secondary keys-save-btn" :disabled="!otopoKey.trim() || otopoSaving" @click="saveOtopoKey">
                {{ otopoSaving ? 'Saving…' : 'Save' }}
              </button>
            </div>
            <p v-if="otopoMsg" :class="otopoMsgOk ? 'keys-msg-ok' : 'keys-msg-err'">{{ otopoMsg }}</p>
            <p class="keys-hint"><a href="https://opentopography.org/developers" target="_blank" rel="noopener">Get a free API key</a></p>
          </div>

          <hr class="keys-divider" />

          <!-- Local SRTM tile folder (the `local` DEM source) -->
          <div class="keys-service">
            <div class="keys-service-header">
              <span class="keys-service-name">🗂 Local SRTM tiles</span>
              <span v-if="tileStore === null" class="keys-badge keys-badge-checking">checking…</span>
              <span v-else-if="tileStore.available" class="keys-badge keys-badge-ok">✓ {{ tileStore.tile_count }} tiles</span>
              <span v-else class="keys-badge keys-badge-error">✗ No tiles</span>
            </div>
            <p class="keys-service-desc">
              The folder of GeoTIFF tiles behind the “Local SRTM Tiles” DEM source. Without it that
              source returns a flat, empty elevation grid.
            </p>
            <p v-if="tileStore && !tileStore.available" class="keys-msg-err">{{ tileStore.note }}</p>
            <div class="keys-input-row">
              <input
                v-model="tilePath"
                type="text"
                class="keys-input"
                placeholder="C:\path\to\srtm_tifs"
                @keyup.enter="saveTilePath"
              />
              <button class="btn btn-secondary keys-save-btn" :disabled="tileBrowsing || tileSaving" @click="browseTilePath">
                {{ tileBrowsing ? 'Picking…' : 'Browse…' }}
              </button>
              <button class="btn btn-secondary keys-save-btn" :disabled="!tilePath.trim() || tileSaving" @click="saveTilePath">
                {{ tileSaving ? 'Checking…' : 'Save' }}
              </button>
            </div>
            <p v-if="tileMsg" :class="tileMsgOk ? 'keys-msg-ok' : 'keys-msg-err'">{{ tileMsg }}</p>
            <p class="keys-hint">
              Pick or paste the folder that holds the <code>.tif</code> files directly. Browse opens
              a dialog on the machine running the server. Saved to <code>config.json</code> as
              <code>ocean_root</code> and applied without a restart.
            </p>
          </div>
        </div>
      </div>
    </div>

    <!-- Diagnostics modal -->
    <div v-if="diagOpen" class="keys-overlay" @click.self="diagOpen = false">
      <div class="keys-modal">
        <div class="keys-modal-header">
          <span>🩺 Diagnostics</span>
          <button class="keys-close-btn" aria-label="Close diagnostics" title="Close" @click="diagOpen = false">✕</button>
        </div>
        <div class="keys-modal-body">
          <p v-if="diag === null" class="keys-service-desc">Loading…</p>
          <template v-else-if="diag">
            <!-- Keys -->
            <div class="keys-service">
              <div class="keys-service-header">
                <span class="keys-service-name">🔑 API keys</span>
              </div>
              <p class="diag-line">OpenTopography key:
                <span :class="diag.auth.opentopo_key ? 'keys-badge keys-badge-ok' : 'keys-badge keys-badge-error'">
                  {{ diag.auth.opentopo_key ? 'configured' : 'missing' }}
                </span>
                <button v-if="!diag.auth.opentopo_key" class="keys-copy-btn" @click="diagOpen=false; openKeys()">Add key</button>
              </p>
            </div>
            <hr class="keys-divider" />
            <!-- DEM sources -->
            <div class="keys-service">
              <div class="keys-service-header"><span class="keys-service-name">🗺️ DEM sources</span></div>
              <p v-for="(s, id) in diag.dem_sources" :key="id" class="diag-line">
                <span :class="s.available ? 'keys-badge keys-badge-ok' : 'keys-badge keys-badge-error'">
                  {{ s.available ? 'ready' : 'unavailable' }}
                </span>
                {{ s.label }}
                <span v-if="s.note" class="keys-hint">— {{ s.note }}</span>
              </p>
            </div>
            <hr class="keys-divider" />
            <!-- Region probe -->
            <div v-if="diag.region_probe" class="keys-service">
              <div class="keys-service-header"><span class="keys-service-name">📐 Selected region</span></div>
              <p class="diag-line">Span: {{ diag.region_probe.span_deg.ns }}° × {{ diag.region_probe.span_deg.ew }}°
                <span :class="diag.region_probe.likely_local_coverage ? 'keys-badge keys-badge-ok' : 'keys-badge keys-badge-error'">
                  {{ diag.region_probe.likely_local_coverage ? 'local coverage likely' : 'too large for local DEM' }}
                </span>
              </p>
              <p class="keys-hint">{{ diag.region_probe.recommendation }}</p>
            </div>
            <div v-else class="keys-service">
              <p class="keys-hint">Select a region to see coverage advice.</p>
            </div>
            <hr class="keys-divider" />
            <!-- Cache -->
            <div class="keys-service">
              <div class="keys-service-header"><span class="keys-service-name">💾 Cache</span></div>
              <p class="diag-line">{{ diag.cache.size_mb }} MB</p>
              <p class="keys-hint keys-code">{{ diag.cache.path }}</p>
            </div>
          </template>
          <p v-else class="keys-msg-err">Failed to load diagnostics.</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import SettingsSheet from './SettingsSheet.vue';
import { useAppStore } from '../../stores/app';
import { formatBboxDims } from '../../../modules/regions/region-geometry.js';

const store = useAppStore();
const settingsOpen = ref(false);
// Modules (and the Extrude panel's "⚙ Settings" link) open the sheet through window.
(window as any).openSettingsSheet = () => { settingsOpen.value = true; };

const regionPill = computed(() => {
  const r = store.selectedRegion as { name: string; north: number; south: number; east: number; west: number } | null;
  return r ? { name: r.name, size: formatBboxDims(r) } : null;
});

function goExplore() {
  (window as any).switchView?.('map');
}
function makePrintable() {
  (window as any).makePrintable?.();
}

// Keys modal state
const keysOpen = ref(false);
const eeStatus = ref<boolean | null>(null);
const otopoStatus = ref<boolean | null>(null);
const otopoKey = ref('');
const otopoSaving = ref(false);
const otopoMsg = ref('');
const otopoMsgOk = ref(false);

// Local SRTM tile folder state (the `local` DEM source)
const tileStore = ref<{ path: string | null; available: boolean; tile_count: number; note: string } | null>(null);
const tilePath = ref('');
const tileSaving = ref(false);
const tileBrowsing = ref(false);
const tileMsg = ref('');
const tileMsgOk = ref(false);

// Earth Engine OAuth flow state
const eeAuthUrl = ref('');
const eeUrlOpened = ref(false);
const eeCode = ref('');
const eeStarting = ref(false);
const eeCompleting = ref(false);
const eeMsg = ref('');
const eeMsgOk = ref(false);

async function fetchStatus() {
  eeStatus.value = null;
  otopoStatus.value = null;
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    eeStatus.value = data.earth_engine?.authenticated ?? false;
    otopoStatus.value = data.opentopo?.authenticated ?? false;
  } catch {
    eeStatus.value = false;
    otopoStatus.value = false;
  }
}

async function fetchTileStore() {
  tileStore.value = null;
  try {
    const res = await fetch('/api/auth/tile-store');
    const data = await res.json();
    tileStore.value = data;
    // Prefill with the configured path so a broken one can be corrected in
    // place rather than retyped from scratch.
    if (!tilePath.value && data.path) tilePath.value = data.path;
  } catch {
    tileStore.value = { path: null, available: false, tile_count: 0, note: 'Could not reach the server.' };
  }
}

function openKeys() {
  keysOpen.value = true;
  eeAuthUrl.value = '';
  eeCode.value = '';
  eeMsg.value = '';
  tileMsg.value = '';
  fetchStatus();
  fetchTileStore();
}

async function startEeAuth() {
  eeStarting.value = true;
  eeMsg.value = '';
  eeAuthUrl.value = '';
  eeUrlOpened.value = false;
  eeCode.value = '';
  try {
    const res = await fetch('/api/auth/earth-engine/start', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.auth_url) {
      eeAuthUrl.value = data.auth_url;
      window.open(data.auth_url, '_blank', 'noopener');
      eeUrlOpened.value = true;
    } else {
      eeMsg.value = data.error || 'Failed to start authentication.';
      eeMsgOk.value = false;
    }
  } catch {
    eeMsg.value = 'Network error starting authentication.';
    eeMsgOk.value = false;
  } finally {
    eeStarting.value = false;
  }
}

async function completeEeAuth() {
  const code = eeCode.value.trim();
  if (!code) return;
  eeCompleting.value = true;
  eeMsg.value = '';
  try {
    const res = await fetch('/api/auth/earth-engine/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      eeMsg.value = '✓ Authenticated — water mask and ESA land cover are ready.';
      eeMsgOk.value = true;
      eeStatus.value = true;
      eeAuthUrl.value = '';
      eeCode.value = '';
    } else {
      eeMsg.value = data.error || 'Invalid or expired code — try again.';
      eeMsgOk.value = false;
    }
  } catch {
    eeMsg.value = 'Network error completing authentication.';
    eeMsgOk.value = false;
  } finally {
    eeCompleting.value = false;
  }
}

// Diagnostics modal state
const diagOpen = ref(false);
const diag = ref<any>(null);

async function openDiag() {
  diagOpen.value = true;
  diag.value = null;
  // Include the selected region's bbox so the server can add a coverage probe.
  let qs = '';
  try {
    const r = (window as any).appState?.selectedRegion;
    if (r && r.north != null) {
      qs = `?north=${r.north}&south=${r.south}&east=${r.east}&west=${r.west}`;
    }
  } catch { /* no selection */ }
  try {
    const res = await fetch('/api/diagnostics' + qs);
    diag.value = res.ok ? await res.json() : false;
  } catch {
    diag.value = false;
  }
}

async function saveOtopoKey() {
  const key = otopoKey.value.trim();
  if (!key) return;
  otopoSaving.value = true;
  otopoMsg.value = '';
  try {
    const res = await fetch('/api/auth/opentopo-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    const data = await res.json();
    if (res.ok) {
      otopoMsg.value = data.applied
        ? '✓ Key saved and applied — OpenTopography DEM sources are ready.'
        : '✓ Key saved — restart the server to apply.';
      otopoMsgOk.value = true;
      otopoStatus.value = true;
      otopoKey.value = '';
    } else {
      otopoMsg.value = data.error || 'Failed to save key.';
      otopoMsgOk.value = false;
    }
  } catch {
    otopoMsg.value = 'Network error saving key.';
    otopoMsgOk.value = false;
  } finally {
    otopoSaving.value = false;
  }
}

async function browseTilePath() {
  // The dialog opens on the server's desktop, not in this page: a browser is not
  // allowed to report an absolute path, and the path is exactly what config.json
  // needs. Same machine, so the two are equivalent from the user's side.
  tileBrowsing.value = true;
  tileMsg.value = '';
  try {
    const res = await fetch('/api/auth/tile-store/browse', { method: 'POST' });
    const data = await res.json();
    if (res.status === 501) {
      tileMsg.value = `${data.error || 'No folder dialog on the server.'} Type the path instead.`;
      tileMsgOk.value = false;
      return;
    }
    if (!res.ok) {
      tileMsg.value = data.error || 'Could not open the folder dialog.';
      tileMsgOk.value = false;
      return;
    }
    if (data.cancelled || !data.path) return;
    tilePath.value = data.path;
    // Picking a folder is the whole gesture; making the user press Save
    // afterwards would only be a second chance to change nothing.
    await saveTilePath();
  } catch {
    tileMsg.value = 'Network error opening the folder dialog.';
    tileMsgOk.value = false;
  } finally {
    tileBrowsing.value = false;
  }
}

async function saveTilePath() {
  const path = tilePath.value.trim();
  if (!path) return;
  tileSaving.value = true;
  tileMsg.value = '';
  try {
    const res = await fetch('/api/auth/tile-store', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (res.ok && data.saved) {
      tileStore.value = data;
      tileMsg.value = `✓ ${data.tile_count} tiles found — the Local SRTM Tiles source is ready.`;
      tileMsgOk.value = true;
      // The source list is built from availability, so refresh it: the option
      // was disabled a moment ago and is selectable now.
      (window as any).populateDemSources?.();
    } else {
      tileMsg.value = data.error || 'Could not use that folder.';
      tileMsgOk.value = false;
    }
  } catch {
    tileMsg.value = 'Network error saving the folder.';
    tileMsgOk.value = false;
  } finally {
    tileSaving.value = false;
  }
}

</script>

<style scoped>
.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}

.save-status {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  margin-right: 4px;
}
.save-status.saved   { color: #30d158; }
.save-status.pending { color: var(--text-muted); }
.save-status.failed  { color: #ff6b6b; }

.ctx-pill {
  display: inline-flex; align-items: center; gap: 6px; flex-shrink: 1; min-width: 0;
  background: #1c1c1e; color: #f5f5f7; border: 0; border-radius: 999px; padding: 5px 12px;
  font-size: 13px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ctx-pill:hover { background: #2c2c2e; }
.ctx-sub { color: var(--text-muted); }
.magic-btn {
  border: 0; border-radius: 999px; padding: 6px 14px; cursor: pointer; white-space: nowrap;
  background: linear-gradient(90deg, #0a84ff, #5e5ce6); color: #fff; font-weight: 600; font-size: 13px;
}
.magic-btn[aria-busy="true"] { opacity: 0.6; cursor: progress; }
.gear-btn {
  width: 32px; height: 32px; border-radius: 999px; border: 0; cursor: pointer;
  background: #1c1c1e; color: var(--text-muted); font-size: 16px;
}
.gear-btn:hover { background: #2c2c2e; color: #f5f5f7; }

/* Below ~1200 px the magic button shows its icon only (title keeps the name). */
@media (max-width: 1199px) {
  .hdr-btn-label { display: none; }
}

/* Keys modal overlay */
.keys-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 9000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.keys-modal {
  background: #2a2a2a;
  border: 1px solid #444;
  border-radius: 8px;
  width: 480px;
  max-width: 95vw;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
}

.keys-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #444;
  font-weight: 600;
  font-size: 14px;
  color: #e0e0e0;
}

.keys-close-btn {
  background: none;
  border: none;
  color: #aaa;
  cursor: pointer;
  font-size: 16px;
  padding: 0 4px;
}
.keys-close-btn:hover { color: #fff; }

.keys-modal-body {
  padding: 16px;
}

.keys-service {
  padding: 4px 0 8px;
}

.keys-service-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}

.keys-service-name {
  font-weight: 600;
  font-size: 13px;
  color: #ddd;
}

.keys-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.keys-badge-ok      { background: #1a4a1a; color: #6fcf6f; }
.keys-badge-error   { background: #4a1a1a; color: #cf6f6f; }
.keys-badge-checking { background: #333; color: var(--text-dim); }

.keys-service-desc {
  font-size: 12px;
  color: var(--text-dim);
  margin: 4px 0 8px;
}

.keys-instructions {
  background: #1e1e1e;
  border-radius: 6px;
  padding: 10px 12px;
  margin-top: 6px;
  font-size: 12px;
  color: #ccc;
}
.keys-instructions p { margin: 4px 0; }

.keys-code-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0;
}

.keys-code {
  flex: 1;
  background: #111;
  border: 1px solid #333;
  border-radius: 4px;
  padding: 5px 8px;
  font-size: 11px;
  color: #a0d0ff;
  word-break: break-all;
}

.keys-copy-btn {
  background: #404040;
  border: 1px solid #555;
  color: #ccc;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
}
.keys-copy-btn:hover { background: #505050; }

.keys-hint {
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 6px;
}
.keys-hint a { color: #6aacff; }

.keys-input-row {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}

.keys-input {
  flex: 1;
  background: #1e1e1e;
  border: 1px solid #444;
  border-radius: 4px;
  color: #e0e0e0;
  padding: 5px 8px;
  font-size: 12px;
}
.keys-input:focus { outline: none; border-color: #6aacff; }

.keys-save-btn {
  padding: 4px 14px;
  font-size: 12px;
}

.keys-divider {
  border: none;
  border-top: 1px solid #383838;
  margin: 12px 0;
}

.keys-msg-ok  { font-size: 12px; color: #6fcf6f; margin-top: 6px; }
.keys-msg-err { font-size: 12px; color: #cf6f6f; margin-top: 6px; }

.diag-line {
  font-size: 12px;
  color: #ccc;
  margin: 6px 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.diag-line .keys-badge { margin: 0; }
</style>

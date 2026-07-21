<template>
  <div class="main-header">
    <div class="main-title">3D Maps Globe &amp; Map Selector</div>
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
      <!-- Diagnostics button -->
      <button class="btn btn-secondary docs-menu-btn" @click="openDiag" title="Check server status, keys, DEM sources, and region coverage">
        🩺 Diagnostics
      </button>

      <!-- Keys button -->
      <button class="btn btn-secondary docs-menu-btn" @click="openKeys">
        🔑 Keys
      </button>

      <!-- Docs dropdown -->
      <div class="docs-menu">
        <button class="btn btn-secondary docs-menu-btn" id="docsMenuBtn"
                @click="docsOpen = !docsOpen">
          📖 Docs
        </button>
        <div v-if="docsOpen" class="docs-dropdown">
          <a v-for="link in docsLinks" :key="link.href"
             :href="link.href" target="_blank" rel="noopener"
             class="docs-dropdown-link">
            {{ link.label }}
          </a>
        </div>
      </div>
    </div>

    <!-- Keys modal -->
    <div v-if="keysOpen" class="keys-overlay" @click.self="keysOpen = false">
      <div class="keys-modal">
        <div class="keys-modal-header">
          <span>🔑 Service Authentication</span>
          <button class="keys-close-btn" @click="keysOpen = false">✕</button>
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
        </div>
      </div>
    </div>

    <!-- Diagnostics modal -->
    <div v-if="diagOpen" class="keys-overlay" @click.self="diagOpen = false">
      <div class="keys-modal">
        <div class="keys-modal-header">
          <span>🩺 Diagnostics</span>
          <button class="keys-close-btn" @click="diagOpen = false">✕</button>
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
import { ref, onMounted } from 'vue';

const docsOpen = ref(false);
const docsLinks = [
  { label: '📋 Swagger UI',          href: '/docs' },
  { label: '📘 ReDoc',               href: '/redoc' },
  { label: '📚 Project Docs',        href: '/project-docs/' },
  { label: '🐍 Python API Reference', href: '/api-reference/' },
];

// Close docs dropdown when clicking outside
if (typeof document !== 'undefined') {
  document.addEventListener('click', (e: Event) => {
    const btn = document.getElementById('docsMenuBtn');
    if (btn && !btn.contains(e.target as Node)) {
      docsOpen.value = false;
    }
  });
}

// Keys modal state
const keysOpen = ref(false);
const eeStatus = ref<boolean | null>(null);
const otopoStatus = ref<boolean | null>(null);
const otopoKey = ref('');
const otopoSaving = ref(false);
const otopoMsg = ref('');
const otopoMsgOk = ref(false);

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

function openKeys() {
  keysOpen.value = true;
  eeAuthUrl.value = '';
  eeCode.value = '';
  eeMsg.value = '';
  fetchStatus();
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

</script>

<style scoped>
.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.docs-menu {
  position: relative;
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
.keys-badge-checking { background: #333; color: #888; }

.keys-service-desc {
  font-size: 12px;
  color: #888;
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
  color: #777;
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

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
              <p>Run this command in a terminal, then reload the page:</p>
              <div class="keys-code-row">
                <code class="keys-code">C:\venvs\strm2stl\Scripts\python.exe -c "import ee; ee.Authenticate()"</code>
                <button class="keys-copy-btn" @click="copy(eeCmd)">{{ eeCopied ? '✓' : 'Copy' }}</button>
              </div>
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
const eeCopied = ref(false);

const eeCmd = `C:\\venvs\\strm2stl\\Scripts\\python.exe -c "import ee; ee.Authenticate()"`;

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
  fetchStatus();
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
      otopoMsg.value = '✓ Key saved — restart the server to apply.';
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

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    eeCopied.value = true;
    setTimeout(() => { eeCopied.value = false; }, 2000);
  } catch {}
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
</style>

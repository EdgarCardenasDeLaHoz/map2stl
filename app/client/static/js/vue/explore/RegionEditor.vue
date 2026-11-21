<template>
  <section id="sidebarEditView" class="re" aria-labelledby="sbEditTitle" @keydown.esc.prevent="back">
    <div class="re-head">
      <button id="sbBackBtn" type="button" class="re-back" aria-label="Back to region list"
              title="Back to region list (Esc)" @click="back">‹</button>
      <h2 id="sbEditTitle" class="re-title">Edit <span id="sbRegionName">{{ store.editingName }}</span></h2>
    </div>

    <form id="regionEditorForm" class="re-body" novalidate @submit.prevent="save">
      <h3 class="re-h">Region</h3>
      <div class="re-card">
        <label class="re-field">
          <small>Name</small>
          <input id="sbName" ref="nameEl" v-model="name" type="text" maxlength="128" autocomplete="off" required>
        </label>
        <label class="re-field">
          <small>Group</small>
          <input id="sbLabel" v-model="label" type="text" list="regionLabelsList" maxlength="64"
                 autocomplete="off" placeholder="e.g. City, Europe">
          <datalist id="regionLabelsList">
            <option v-for="g in groups" :key="g" :value="g"></option>
          </datalist>
        </label>
      </div>

      <h3 class="re-h">Bounds · degrees</h3>
      <div class="re-card re-grid">
        <label v-for="d in dirs" :key="d.key" class="re-field">
          <small>{{ d.label }}</small>
          <input :id="d.id" v-model="bounds[d.key]" type="number" step="0.001" :min="d.min" :max="d.max"
                 @input="onBounds">
        </label>
      </div>
      <p id="sbSizeReadout" class="re-size" :class="{ invalid: !box }" aria-live="polite">
        {{ box ? `${sizeText} · the box on the map follows as you type`
               : 'Invalid bounds: north must be above south, within ±90° / ±180°' }}
      </p>

      <button id="sbSaveBtn" type="submit" class="re-save" :disabled="saving">Save</button>

      <div class="re-card re-danger">
        <button id="sbDeleteBtn" type="button" class="re-delete" @click="remove">Delete region…</button>
      </div>

      <h3 class="re-h">More settings</h3>
      <div class="re-card">
        <button id="sbNotesToggle" type="button" class="re-row" :aria-expanded="notesOpen"
                @click="notesOpen = !notesOpen">
          <span>Notes</span>
          <span class="re-dim">{{ notes.trim() ? 'added' : 'none' }} {{ notesOpen ? '⌄' : '›' }}</span>
        </button>
        <textarea v-if="notesOpen" id="sbNotesTextarea" v-model="notes" class="re-notes" rows="4"
                  aria-label="Region notes" placeholder="Notes about this region (saved with the region)"></textarea>
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRegionsStore } from '../stores/regions';
import { bboxSizeKm, formatBboxSize, parseBbox } from '../../modules/regions/region-geometry.js';
import { restoreBox, setBoxPreview } from './exploreMap';

const store = useRegionsStore();
const name = ref('');
const label = ref('');
const notes = ref('');
const notesOpen = ref(false);
const saving = ref(false);
const nameEl = ref<HTMLInputElement | null>(null);
const bounds = reactive({ north: '', south: '', east: '', west: '' });

const dirs = [
  { key: 'north', id: 'sbNorth', label: 'North', min: -90, max: 90 },
  { key: 'south', id: 'sbSouth', label: 'South', min: -90, max: 90 },
  { key: 'east', id: 'sbEast', label: 'East', min: -180, max: 180 },
  { key: 'west', id: 'sbWest', label: 'West', min: -180, max: 180 },
] as const;

const groups = computed(() => [...new Set(store.regions.map((r) => r.label).filter(Boolean) as string[])].sort());
const box = computed(() => parseBbox(bounds.north, bounds.south, bounds.east, bounds.west));
const sizeText = computed(() => (box.value ? formatBboxSize(bboxSizeKm(box.value)) : ''));

function fill() {
  const r = store.editing;
  if (!r) return;
  name.value = r.name;
  label.value = r.label || '';
  bounds.north = Number(r.north).toFixed(5);
  bounds.south = Number(r.south).toFixed(5);
  bounds.east = Number(r.east).toFixed(5);
  bounds.west = Number(r.west).toFixed(5);
  notes.value = store.noteOf(r);
  notesOpen.value = false;
}

/** The map's working box follows the N/S/E/W fields as they are typed. */
function onBounds() {
  if (box.value) setBoxPreview(box.value);
}

async function save() {
  const oldName = store.editingName;
  if (!oldName || saving.value) return;
  const newName = name.value.trim();
  if (!newName) { (window as any).showToast?.('Name is required', 'error'); nameEl.value?.focus(); return; }
  const b = box.value;
  if (!b) { (window as any).showToast?.('Fix the bounds before saving', 'error'); return; }
  saving.value = true;
  try {
    await store.update(oldName, { name: newName, label: label.value.trim(), ...b }, notes.value);
  } finally { saving.value = false; }
}

async function remove() {
  if (store.editingName) await store.remove(store.editingName);
}

/** Leave the editor: the map's box goes back to the saved bounds; focus returns to the row. */
function back() {
  const n = store.editingName;
  const saved = store.editing;
  if (saved && store.selectedName === n) restoreBox(saved);
  store.editingName = null;
  void nextTick(() => {
    const rows = [...document.querySelectorAll('#coordinatesList .rl-row')] as HTMLElement[];
    const row = rows.find((r) => r.dataset.regionName === n);
    ((row?.querySelector('.rl-edit') as HTMLElement | null) || row)?.focus();
  });
}

// The editor opened (or moved to another region): fill the form from the saved record.
watch(() => store.editingName, (n) => { if (n) { fill(); void nextTick(() => nameEl.value?.focus()); } });
// A different region selected while the editor is open (a box click): follow it.
watch(() => store.selectedName, (n) => { if (store.editingName && n && n !== store.editingName) store.editingName = n; });
onMounted(() => { fill(); void nextTick(() => nameEl.value?.focus()); });

defineExpose({ back });
</script>

<style scoped>
.re { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow-y: auto; }
.re-head { display: flex; align-items: center; gap: 8px; padding: 10px 14px 4px; }
.re-back {
  width: 28px; height: 28px; border-radius: 8px; border: 0; background: var(--card, #2c2c2e);
  color: var(--text, #f2f2f7); font-size: 18px; line-height: 1; cursor: pointer;
}
.re-title { margin: 0; font-size: 15px; font-weight: 700; color: var(--text, #f2f2f7); }
.re-body { padding: 0 14px 16px; }
.re-h { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--dim, #98989f); margin: 14px 4px 6px; font-weight: 500; }
.re-card { background: var(--card, #2c2c2e); border-radius: 12px; overflow: hidden; }
.re-field { display: block; padding: 8px 14px; border-bottom: 1px solid var(--line, #3a3a3c); }
.re-field:last-child { border-bottom: 0; }
.re-field small { display: block; color: var(--dim, #98989f); font-size: 11px; }
.re-field input {
  width: 100%; box-sizing: border-box; background: none; border: 0; padding: 2px 0; font-size: 14px;
  color: var(--text, #f2f2f7); font-family: inherit;
}
.re-field input:focus-visible { outline: 2px solid var(--accent, #0a84ff); outline-offset: 2px; border-radius: 4px; }
.re-grid { display: grid; grid-template-columns: 1fr 1fr; }
.re-grid .re-field:nth-child(odd) { border-right: 1px solid var(--line, #3a3a3c); }
.re-grid .re-field:nth-child(n + 3) { border-bottom: 0; }
.re-size { color: var(--accent, #0a84ff); font-size: 12px; margin: 8px 4px; }
.re-size.invalid { color: var(--bad, #ff453a); }
.re-save {
  display: block; width: 100%; border: 0; border-radius: 10px; padding: 10px; margin: 8px 0 12px;
  background: var(--accent, #0a84ff); color: #fff; font-weight: 600; font-size: 14px; cursor: pointer;
}
.re-save:disabled { opacity: 0.6; cursor: default; }
.re-delete { display: block; width: 100%; background: none; border: 0; color: var(--bad, #ff453a); padding: 11px 14px; text-align: left; font-size: 14px; cursor: pointer; }
.re-row {
  display: flex; justify-content: space-between; align-items: center; width: 100%; background: none; border: 0;
  padding: 10px 14px; color: var(--text, #f2f2f7); font-size: 14px; cursor: pointer;
}
.re-dim { color: var(--dim, #98989f); }
.re-notes {
  display: block; width: calc(100% - 28px); box-sizing: border-box; margin: 0 14px 12px; resize: vertical;
  background: #1c1c1e; color: var(--text, #f2f2f7); border: 1px solid var(--line, #3a3a3c); border-radius: 8px;
  padding: 8px; font: inherit; font-size: 13px;
}
</style>

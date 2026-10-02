<template>
  <!-- New-region flow on the Explore map (modules/map/new-region.js; mockup
       claude/mockups/2026-10-02-newregion). place: a searched place with "Make a region here";
       drawing: the hint at the top; pending: name and group the box, then save. -->
  <div v-if="phase === 'drawing'" class="nr-hint" role="status">
    <b>Drag a box</b> around what you want to print · Esc cancels
    <button type="button" class="nr-hint-x" aria-label="Cancel drawing" @click="cancel">✕</button>
  </div>

  <div v-else-if="phase === 'place'" class="nr-card" role="region" aria-label="Searched place">
    <div class="nr-grow">
      <div class="nr-title">{{ place?.name }}</div>
      <div class="nr-meta">{{ placeMeta }}</div>
    </div>
    <button type="button" class="nr-btn" title="Draw your own box instead" @click="drawOwn">✏ Draw my own</button>
    <button type="button" class="nr-btn primary"
            @click="makeHere">Make a region here ›</button>
    <button type="button" class="nr-x" aria-label="Close" title="Close" @click="cancel">✕</button>
  </div>

  <!-- Esc here too: the page-wide shortcut ignores keys typed into the name field. -->
  <form v-else-if="phase === 'pending'" class="nr-card nr-pending" aria-label="New region"
        @submit.prevent="save" @keydown.esc.prevent="cancel">
    <div class="nr-grow nr-fields">
      <div class="nr-row">
        <label class="nr-field">
          <span class="nr-lbl">Name</span>
          <input ref="nameEl" v-model="name" class="nr-input" type="text" placeholder="Region name…"
                 required aria-label="Region name">
        </label>
        <div class="nr-field">
          <span class="nr-lbl">Group <span class="nr-opt">(optional)</span></span>
          <div class="nr-chips" role="radiogroup" aria-label="Group">
            <button v-for="g in groups" :key="g" type="button" role="radio" class="nr-chip"
                    :aria-checked="label === g" :class="{ on: label === g }"
                    @click="label = label === g ? '' : g">{{ g }}</button>
            <input v-if="addingGroup" ref="groupEl" v-model="label" class="nr-input nr-group-input"
                   type="text" placeholder="New group" aria-label="New group"
                   @keydown.enter.prevent="addingGroup = false">
            <button v-else type="button" class="nr-chip" @click="newGroup">＋ New</button>
          </div>
        </div>
      </div>
      <div class="nr-meta">{{ sizeText }} · {{ position }} · drag the handles to adjust</div>
    </div>
    <button type="button" class="nr-btn" @click="cancel">Cancel</button>
    <button type="submit" class="nr-btn primary" :disabled="saving || !name.trim()">
      {{ saving ? 'Saving…' : 'Save region ›' }}</button>
  </form>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { placeCaption } from '../../../modules/map/landmarks.js';

type Box = { north: number; south: number; east: number; west: number };
type Place = { name: string; display_name: string; lat: number; lon: number; class?: string; type?: string };

const w = window as any;
const phase = ref<'idle' | 'place' | 'drawing' | 'pending'>('idle');
const place = ref<Place | null>(null);
const box = ref<Box | null>(null);
const sizeText = ref('');
const name = ref('');
const label = ref('');
const saving = ref(false);
const addingGroup = ref(false);
const nameEl = ref<HTMLInputElement | null>(null);
const groupEl = ref<HTMLInputElement | null>(null);

defineExpose({ phase });

const placeMeta = computed(() => {
  const p = place.value;
  if (!p) return '';
  const where = p.display_name.split(',').slice(1, 3).map((x) => x.trim()).join(', ');
  return [placeCaption(p as any), where].filter(Boolean).join(' · ');
});
const position = computed(() => {
  const b = box.value;
  if (!b) return '';
  const lat = (b.north + b.south) / 2;
  const lon = (b.east + b.west) / 2;
  return `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(2)}° ${lon >= 0 ? 'E' : 'W'}`;
});
/** Your existing groups, most used first. */
const groups = computed(() => {
  void phase.value;
  const counts = new Map<string, number>();
  for (const r of (w.getCoordinatesData?.() || []) as { label?: string }[]) {
    if (r.label) counts.set(r.label, (counts.get(r.label) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([g]) => g);
});

async function onState(e: Event) {
  const d = (e as CustomEvent).detail;
  const was = phase.value;
  phase.value = d.phase;
  place.value = d.place;
  box.value = d.box;
  sizeText.value = d.sizeText;
  if (d.phase === 'pending' && was !== 'pending') {
    // A searched place names the region; a hand-drawn box starts empty.
    name.value = d.place ? String(d.place.name).split(' / ')[0] : '';
    label.value = '';
    addingGroup.value = false;
    await nextTick();
    nameEl.value?.focus();
    nameEl.value?.select();
  }
}

function drawOwn() { w.newRegion?.startDraw(); }
function makeHere() { w.newRegion?.fromSuggestion(); }
function cancel() { w.newRegion?.cancel(); }
async function newGroup() {
  label.value = '';
  addingGroup.value = true;
  await nextTick();
  groupEl.value?.focus();
}
async function save() {
  if (saving.value || !name.value.trim()) return;
  saving.value = true;
  try { await w.newRegion?.save(name.value, label.value); } finally { saving.value = false; }
}

onMounted(() => {
  window.addEventListener('map2stl:new-region', onState);
  window.addEventListener('map2stl:new-region-save', save);
});
onBeforeUnmount(() => {
  window.removeEventListener('map2stl:new-region', onState);
  window.removeEventListener('map2stl:new-region-save', save);
});
</script>

<style scoped>
.nr-card {
  position: absolute; left: 50%; bottom: 20px; transform: translateX(-50%); z-index: 1001;
  display: flex; align-items: center; gap: 12px; min-width: min(560px, calc(100% - 32px));
  max-width: calc(100% - 32px); padding: 12px 14px 12px 18px; border-radius: 18px; box-sizing: border-box;
  background: rgba(28, 28, 30, 0.96); box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5); color: #f5f5f7;
}
.nr-pending { align-items: flex-end; }
.nr-grow { flex: 1; min-width: 0; }
.nr-title { font-weight: 700; font-size: 16px; }
.nr-meta { font-size: 12px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nr-fields { display: flex; flex-direction: column; gap: 8px; }
.nr-row { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; }
.nr-field { display: flex; flex-direction: column; gap: 4px; }
.nr-lbl { font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.nr-opt { text-transform: none; letter-spacing: 0; font-weight: 400; }
.nr-input {
  background: #2c2c2e; color: #f5f5f7; border: 0; border-radius: 10px; padding: 8px 12px;
  font-size: 14px; min-width: 200px;
}
.nr-input:focus { outline: 2px solid #0a84ff; }
.nr-group-input { min-width: 120px; padding: 4px 10px; font-size: 12px; border-radius: 999px; }
.nr-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.nr-chip {
  border: 0; border-radius: 999px; padding: 4px 10px; font-size: 12px; cursor: pointer;
  background: #2c2c2e; color: var(--text-muted);
}
.nr-chip.on { background: #0a84ff; color: #fff; }
.nr-btn {
  border: 0; border-radius: 12px; padding: 10px 14px; cursor: pointer; white-space: nowrap;
  background: #2c2c2e; color: #f5f5f7; font-weight: 600; font-size: 14px;
}
.nr-btn:hover { background: #3a3a3c; }
.nr-btn.primary { background: #0a84ff; color: #fff; }
.nr-btn.primary:disabled { opacity: 0.5; cursor: default; }
.nr-x { background: none; border: 0; color: var(--text-muted); font-size: 16px; cursor: pointer; align-self: flex-start; }
.nr-hint {
  position: absolute; left: 50%; top: 14px; transform: translateX(-50%); z-index: 1001;
  display: flex; align-items: center; gap: 10px;
  background: rgba(28, 28, 30, 0.95); color: #f5f5f7; border-radius: 999px; padding: 8px 10px 8px 16px;
  font-size: 13px; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4); white-space: nowrap;
}
.nr-hint-x { background: #3a3a3c; border: 0; color: #f5f5f7; border-radius: 999px; width: 24px; height: 24px; cursor: pointer; }
</style>

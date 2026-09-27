<template>
  <!-- Background city fetch: one row per OSM layer, the Overpass mirror in use,
       elapsed time and Cancel. Fed by city-overlay.js:loadCityData through
       appState.cityFetch (GET /api/cities/status/{id}). -->
  <div v-if="task && visible" id="cityFetchProgress" class="city-fetch" :class="'is-' + task.status">
    <div class="city-fetch-head">
      <span class="city-fetch-summary">{{ headline }}</span>
      <button v-if="task.status === 'running'" id="cityFetchCancelBtn" type="button"
              class="btn btn-secondary city-fetch-cancel" title="Stop fetching; layers already requested finish in the background and are discarded"
              @click="cancel">Cancel</button>
      <button v-else type="button" class="city-fetch-close" aria-label="Hide fetch progress"
              title="Hide" @click="visible = false">✕</button>
    </div>
    <ul class="city-fetch-layers">
      <li v-for="l in task.layers" :key="l.name" :class="'st-' + l.state">
        <span class="city-fetch-icon">{{ icon(l.state) }}</span>
        <span class="city-fetch-name">{{ l.name }}</span>
        <span class="city-fetch-state">{{ l.state }}</span>
      </li>
    </ul>
    <div v-if="task.error" class="city-fetch-error">{{ task.error }}</div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useAppStore } from '../../stores/app';
import { LAYER_STATE_ICON, summarizeCityFetch } from '../../../modules/layers/city-fetch.js';

type Layer = { name: string; state: string };
type Task = { task_id: string; status: string; layers: Layer[]; mirror?: string | null;
  elapsed_s?: number; error?: string | null };

const store = useAppStore();
const task = computed(() => store.cityFetch as Task | null);
const visible = ref(true);

// A new fetch (new task id) shows the panel again after the user hid the last one.
watch(() => task.value?.task_id, () => { visible.value = true; });

const LABEL: Record<string, string> = {
  running: 'Fetching', done: 'Done', error: 'Failed', cancelled: 'Cancelled',
};
const headline = computed(() => {
  const t = task.value;
  return t ? `${LABEL[t.status] || t.status} · ${summarizeCityFetch(t)}` : '';
});

function icon(state: string): string {
  return (LAYER_STATE_ICON as Record<string, string>)[state] || '·';
}

function cancel() {
  (window as any).cancelCityFetch?.();
}
</script>
<style scoped>
.city-fetch {
  font-size: 10px;
  background: #1e1e1e;
  border: 1px solid #333;
  border-radius: 3px;
  padding: 4px 6px;
  margin: 2px 0;
}
.city-fetch-head { display: flex; align-items: center; gap: 6px; }
.city-fetch-summary { flex: 1; color: #bbb; }
.city-fetch-cancel { font-size: 10px !important; padding: 1px 8px !important; }
.city-fetch-close { background: none; border: none; color: #777; cursor: pointer; font-size: 10px; }
.city-fetch-layers { list-style: none; margin: 3px 0 0; padding: 0; columns: 2; column-gap: 10px; }
.city-fetch-layers li { display: flex; gap: 4px; color: #999; break-inside: avoid; }
.city-fetch-icon { width: 10px; text-align: center; }
.city-fetch-name { flex: 1; }
.city-fetch-state { color: #666; }
.st-fetching { color: #4a9fd4 !important; }
.st-done, .st-cached { color: #6c6 !important; }
.st-failed { color: #e74c3c !important; }
.city-fetch-error { color: #e74c3c; margin-top: 3px; word-break: break-word; }
.is-error { border-color: #7a2d2d; }
</style>

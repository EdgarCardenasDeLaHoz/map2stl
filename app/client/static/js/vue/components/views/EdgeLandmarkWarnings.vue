<template>
  <!-- Named landmarks within 200 m of the region box edge, inside or outside
       ("Alhambra is 120 m outside the east edge"). GET /api/geocode/edge-landmarks,
       one Overpass query per box, cached server-side. The instance with `fetcher`
       (Explore map) watches the box and fills appState.edgeLandmarks; the others
       only display it. -->
  <div v-if="state && visibleList.length && !dismissed" :id="fetcher ? 'edgeLandmarkWarnings' : undefined"
       class="edge-landmarks" :class="{ compact }">
    <div class="edge-landmarks-head">
      <span>⚠️ Near the box edge</span>
      <button type="button" class="edge-landmarks-close" aria-label="Dismiss landmark warning"
              title="Dismiss for this box" @click="dismiss">✕</button>
    </div>
    <ul>
      <li v-for="l in visibleList" :key="l.name + l.lat" :title="`${l.class || ''} ${l.type || ''}`.trim()"
          @click="panTo(l)">{{ l.message }}</li>
    </ul>
    <div v-if="hiddenCount" class="edge-landmarks-more">+{{ hiddenCount }} more</div>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useAppStore } from '../../stores/app';
import { bboxKey, toBbox } from '../../../modules/map/landmarks.js';

const props = withDefaults(defineProps<{ fetcher?: boolean; compact?: boolean; max?: number }>(),
  { fetcher: false, compact: false, max: 5 });

type Landmark = { name: string; message: string; lat: number; lon: number;
  class?: string; type?: string };
type State = { key: string; loading: boolean; error: string | null; landmarks: Landmark[] };

const w = window as any;
const store = useAppStore();
const state = computed(() => store.edgeLandmarks as State | null);
const dismissedKey = ref('');
const dismissed = computed(() => !!state.value && dismissedKey.value === state.value.key);
const visibleList = computed(() => (state.value?.landmarks || []).slice(0, props.max));
const hiddenCount = computed(() => Math.max(0, (state.value?.landmarks?.length || 0) - props.max));

function dismiss() { dismissedKey.value = state.value?.key || ''; }

function panTo(l: Landmark) {
  const map = w.getMap?.();
  if (map && store.activeView === 'map') map.setView([l.lat, l.lon], Math.max(map.getZoom(), 16));
}

// ── Fetcher (one instance) ───────────────────────────────────────────────────
const DEBOUNCE_MS = 1500;
let timer: ReturnType<typeof setTimeout> | null = null;
let controller: AbortController | null = null;
let unsubscribe: (() => void) | null = null;

async function check(bbox: any) {
  const box = toBbox(bbox);
  const key = bboxKey(box);
  if (!box || key === state.value?.key) return;
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  store.edgeLandmarks = { key, loading: true, error: null, landmarks: [] };
  const { data, error } = await w.api.geocode.edgeLandmarks(box, signal);
  if (signal.aborted) return;
  if (error) console.warn('Edge landmark check failed:', error);
  const landmarks: Landmark[] = error ? [] : (data?.landmarks || []);
  store.edgeLandmarks = { key, loading: false, error: error ? String(error) : null, landmarks };
  if (landmarks.length) {
    const more = landmarks.length > 1 ? ` (+${landmarks.length - 1} more)` : '';
    w.showToast?.(`${landmarks[0].message}${more}`, 'warning', 8000);
  }
}

function onBboxChanged(bbox: any) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => check(bbox), DEBOUNCE_MS);
}

function subscribe() {
  if (!w.events || !w.EV) return;
  unsubscribe = w.events.on(w.EV.BBOX_CHANGED, onBboxChanged);
}

onMounted(() => {
  if (!props.fetcher) return;
  // vue-main.js runs before main.js defines window.events; DOMContentLoaded
  // fires after every deferred module has run.
  if (w.events) subscribe();
  else document.addEventListener('DOMContentLoaded', subscribe, { once: true });
});

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer);
  controller?.abort();
  unsubscribe?.();
});
</script>
<style scoped>
.edge-landmarks {
  position: absolute;
  left: 10px;
  bottom: 28px;
  z-index: 1000;
  max-width: min(360px, calc(100% - 20px));
  background: rgba(40, 30, 10, 0.94);
  border: 1px solid #a8741a;
  border-radius: 4px;
  padding: 5px 8px;
  font-size: 11px;
  color: #f3d9a4;
}
.edge-landmarks.compact {
  position: static;
  max-width: none;
  margin: 4px 0 0;
  font-size: 10px;
}
.edge-landmarks-head { display: flex; justify-content: space-between; font-weight: 600; color: #f90; }
.edge-landmarks-close { background: none; border: none; color: #caa; cursor: pointer; }
.edge-landmarks ul { list-style: none; margin: 3px 0 0; padding: 0; }
.edge-landmarks li { cursor: pointer; padding: 1px 0; }
.edge-landmarks li:hover { color: #fff; }
.edge-landmarks-more { color: #b98; margin-top: 2px; }
</style>

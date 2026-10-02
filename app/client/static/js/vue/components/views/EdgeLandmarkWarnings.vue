<template>
  <!-- Named landmarks within 200 m of the region box edge, inside or outside
       ("Alhambra is 120 m outside the east edge"). GET /api/geocode/edge-landmarks,
       one Overpass query per box, cached server-side. The instance with `fetcher`
       (Explore map) watches the box and fills appState.edgeLandmarks; the others
       only display it. -->
  <!-- One compact line ("⚠ 20 landmarks near the box edge · Show") that expands to the
       list on click; on the Explore map it is a small chip under the search bar so it no
       longer covers the box (UI audit 2026-09-30). Dismissible per box. -->
  <div v-if="state && visibleList.length && !dismissed" :id="fetcher ? 'edgeLandmarkWarnings' : undefined"
       class="edge-landmarks" :class="{ compact, open }" role="status">
    <div class="edge-landmarks-head">
      <button type="button" class="edge-landmarks-toggle" :aria-expanded="open ? 'true' : 'false'"
              :title="open ? 'Hide the list' : 'Show the landmarks near the box edge'" @click="open = !open">
        <span aria-hidden="true">⚠</span> {{ totalCount }} landmark{{ totalCount === 1 ? '' : 's' }} near the box edge
        · <span class="edge-landmarks-show">{{ open ? 'Hide' : 'Show' }}</span>
      </button>
      <button type="button" class="edge-landmarks-close" aria-label="Dismiss landmark warning"
              title="Dismiss for this box" @click="dismiss">✕</button>
    </div>
    <ul v-if="open">
      <li v-for="l in visibleList" :key="l.name + l.lat">
        <button type="button" class="edge-landmarks-item" :title="`${l.class || ''} ${l.type || ''}`.trim()"
                @click="panTo(l)">{{ l.message }}</button>
      </li>
    </ul>
    <div v-if="open && hiddenCount" class="edge-landmarks-more">+{{ hiddenCount }} more</div>
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
const totalCount = computed(() => state.value?.landmarks?.length || 0);
const hiddenCount = computed(() => Math.max(0, totalCount.value - props.max));
const open = ref(false);

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
  // No toast: the chip itself (role=status) announces the count.
  store.edgeLandmarks = { key, loading: false, error: error ? String(error) : null, landmarks };
  open.value = false;
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
/* Explore map: a chip under the landmark search bar (LandmarkSearch.vue sits at top 10 / left 60). */
.edge-landmarks {
  position: absolute;
  left: 60px;
  top: 46px;
  z-index: 999; /* below the search results dropdown (1000) */
  max-width: min(360px, calc(100% - 120px));
  background: rgba(40, 30, 10, 0.94);
  border: 1px solid #a8741a;
  border-radius: 12px;
  padding: 2px 4px 2px 8px;
  font-size: 12px;
  color: #f3d9a4;
}
.edge-landmarks.open { border-radius: 6px; padding-bottom: 5px; }
.edge-landmarks.compact {
  position: static;
  max-width: none;
  margin: 4px 0 0;
}
.edge-landmarks-head { display: flex; align-items: center; gap: 4px; }
.edge-landmarks-toggle {
  flex: 1; min-height: 24px; padding: 0; text-align: left;
  background: none; border: none; color: #ffb84d; font: inherit; font-weight: 600; cursor: pointer;
}
.edge-landmarks-show { text-decoration: underline; white-space: nowrap; }
.edge-landmarks-toggle:hover .edge-landmarks-show,
.edge-landmarks-toggle:focus-visible .edge-landmarks-show { color: #fff; }
.edge-landmarks-close { min-width: 24px; min-height: 24px; background: none; border: none; color: #e0c8a8; cursor: pointer; }
.edge-landmarks-close:hover { color: #fff; }
.edge-landmarks ul { list-style: none; margin: 3px 0 0; padding: 0; }
.edge-landmarks-item {
  display: block; width: 100%; padding: 2px 0; text-align: left;
  background: none; border: none; color: inherit; font: inherit; cursor: pointer;
}
.edge-landmarks-item:hover, .edge-landmarks-item:focus-visible { color: #fff; }
.edge-landmarks-more { color: #d9b98a; margin-top: 2px; }
</style>

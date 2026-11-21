<template>
  <!-- The 3D globe (mockup 08). #globeContainer / #globe stay: view-management.js shows it for
       the 'globe' view and the Globe pill in MapContainer.vue opens it over the map. -->
  <div id="globeContainer" ref="root" class="globe-container hidden">
    <div id="globe" ref="host" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp"
         @pointercancel="onUp" @wheel.prevent="onWheel"></div>
    <div v-if="pill" class="gv-pill" :style="{ left: pill.x + 'px', top: pill.y + 'px' }">{{ pill.text }}</div>
    <div v-if="failed" class="gv-fail" role="status">The globe needs WebGL, which this browser does not have.</div>
    <div v-else class="gv-hint">Drag to rotate · scroll to zoom · click a marker to select its region</div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRegionsStore } from '../stores/regions';
import { formatBboxDims, regionColor } from '../../modules/regions/region-geometry.js';
import { clamp, facesCamera, latLngToVec, rotateXY, yawToFace } from './globeMath';
import { globeOpen } from './globeState';

declare const THREE: any;

const RADIUS = 5;
const MIN_Z = 6.5;
const MAX_Z = 22;

const store = useRegionsStore();
const root = ref<HTMLElement | null>(null);
const host = ref<HTMLElement | null>(null);
const failed = ref(false);
const pill = ref<{ x: number; y: number; text: string } | null>(null);

let scene: any, camera: any, renderer: any, earth: any, markers: any;
let raf = 0;
let ready = false;
let resizeObs: ResizeObserver | null = null;
let mo: MutationObserver | null = null;
let drag: { x: number; y: number; moved: number } | null = null;
let pickables: { name: string; sprite: any; lat: number; lng: number }[] = [];

function markerTexture(color: string, selected: boolean) {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d')!;
  g.fillStyle = color;
  g.fillRect(10, 10, 44, 44);
  g.lineWidth = selected ? 8 : 5;
  g.strokeStyle = '#ffffff';
  g.strokeRect(10, 10, 44, 44);
  const t = new THREE.CanvasTexture(c);
  return t;
}

function buildMarkers() {
  if (!markers) return;
  while (markers.children.length) {
    const m = markers.children[0];
    m.material?.map?.dispose?.();
    m.material?.dispose?.();
    markers.remove(m);
  }
  pickables = [];
  for (const r of store.regions) {
    const lat = (r.north + r.south) / 2;
    const lng = (r.east + r.west) / 2;
    const selected = r.name === store.selectedName;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: markerTexture(selected ? '#30d158' : regionColor(r.name), selected), depthTest: false,
    }));
    const p = latLngToVec(lat, lng, RADIUS * 1.01);
    sprite.position.set(p.x, p.y, p.z);
    const s = selected ? 0.42 : 0.3;
    sprite.scale.set(s, s, 1);
    sprite.renderOrder = selected ? 3 : 2;
    markers.add(sprite);
    pickables.push({ name: r.name, sprite, lat, lng });
  }
}

function size() {
  const el = host.value;
  return { w: el?.clientWidth || 0, h: el?.clientHeight || 0 };
}

function init(): boolean {
  if (ready) return true;
  const { w, h } = size();
  if (!w || !h || !host.value) return false;
  try {
    if (typeof THREE === 'undefined') throw new Error('Three.js not loaded');
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000);
    camera.position.z = 14;
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h);
    host.value.appendChild(renderer.domElement);

    earth = new THREE.Group();
    earth.rotation.order = 'YXZ';
    // Plain blue until the elevation image arrives (and when it cannot be loaded).
    const material = new THREE.MeshBasicMaterial({ color: 0x1f5fb5 });
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(RADIUS, 64, 64), material);
    earth.add(mesh);
    new THREE.TextureLoader().load('/static/global_dem.png', (tex: any) => {
      material.map = tex;
      material.color.set(0xffffff);
      material.needsUpdate = true;
    });
    markers = new THREE.Group();
    earth.add(markers);
    scene.add(earth);

    // The old globe object, for the legacy getters (setGlobeScene / setGlobe).
    (window as any).setGlobeScene?.(scene);
    (window as any).setGlobe?.(mesh);
    ready = true;
    faceSelected();
    buildMarkers();
    return true;
  } catch (e) {
    console.error('Error initializing globe:', e);
    failed.value = true;
    const btn = document.getElementById('floatingGlobeToggle');
    if (btn) { btn.classList.add('hidden'); btn.title = 'Globe unavailable (WebGL not supported)'; }
    return false;
  }
}

/** Turn the globe so the selected region (else the Atlantic) faces the camera. */
function faceSelected() {
  if (!earth) return;
  const r = store.regions.find((x) => x.name === store.selectedName);
  const lng = r ? (r.east + r.west) / 2 : -30;
  const lat = r ? (r.north + r.south) / 2 : 15;
  earth.rotation.y = yawToFace(lng);
  earth.rotation.x = clamp(lat * Math.PI / 180, -1.2, 1.2);
}

function frame() {
  raf = 0;
  if (!globeOpen.value || !ready) return;
  renderer.render(scene, camera);
  updatePill();
  raf = requestAnimationFrame(frame);
}

function screenOf(v: { x: number; y: number; z: number }) {
  const { w, h } = size();
  const p = new THREE.Vector3(v.x, v.y, v.z).project(camera);
  return { x: (p.x * 0.5 + 0.5) * w, y: (-p.y * 0.5 + 0.5) * h };
}

/** The name pill rides on the selected (or hovered) marker while it faces the camera. */
function updatePill() {
  const name = store.hoverName || store.selectedName;
  const hit = pickables.find((p) => p.name === name);
  const r = store.regions.find((x) => x.name === name);
  if (!hit || !r) { pill.value = null; return; }
  const world = rotateXY(latLngToVec(hit.lat, hit.lng, RADIUS), earth.rotation.x, earth.rotation.y);
  if (!facesCamera(world, camera.position.z, RADIUS)) { pill.value = null; return; }
  const s = screenOf(world);
  const text = `${r.name} · ${formatBboxDims(r)}`;
  const cur = pill.value;
  if (!cur || cur.text !== text || Math.abs(cur.x - s.x) > 0.5 || Math.abs(cur.y - s.y) > 0.5) {
    pill.value = { x: s.x, y: s.y - 22, text };
  }
}

function pickAt(clientX: number, clientY: number): string | null {
  const rect = host.value!.getBoundingClientRect();
  const px = clientX - rect.left, py = clientY - rect.top;
  let best: { name: string; d: number } | null = null;
  for (const p of pickables) {
    const world = rotateXY(latLngToVec(p.lat, p.lng, RADIUS), earth.rotation.x, earth.rotation.y);
    if (!facesCamera(world, camera.position.z, RADIUS)) continue;
    const s = screenOf(world);
    const d = Math.hypot(s.x - px, s.y - py);
    if (d < 14 && (!best || d < best.d)) best = { name: p.name, d };
  }
  return best?.name ?? null;
}

function onDown(e: PointerEvent) {
  if (!ready) return;
  drag = { x: e.clientX, y: e.clientY, moved: 0 };
  (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
}
function onMove(e: PointerEvent) {
  if (!ready) return;
  if (!drag) {
    store.hoverName = pickAt(e.clientX, e.clientY);
    (e.currentTarget as HTMLElement).style.cursor = store.hoverName ? 'pointer' : 'grab';
    return;
  }
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  drag.moved += Math.abs(dx) + Math.abs(dy);
  drag.x = e.clientX; drag.y = e.clientY;
  const k = 0.005 * (camera.position.z / 14);
  earth.rotation.y += dx * k;
  earth.rotation.x = clamp(earth.rotation.x + dy * k, -1.4, 1.4);
}
function onUp(e: PointerEvent) {
  if (!drag) return;
  const wasClick = drag.moved < 5;
  drag = null;
  if (wasClick) {
    const name = pickAt(e.clientX, e.clientY);
    if (name) void store.select(name);
  }
}
function onWheel(e: WheelEvent) {
  if (!ready) return;
  camera.position.z = clamp(camera.position.z * (e.deltaY > 0 ? 1.08 : 1 / 1.08), MIN_Z, MAX_Z);
}

function resize() {
  if (!ready) return;
  const { w, h } = size();
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}

/** Open or close the globe over the map (the Globe pill) and keep `globeOpen` in step. */
function setOpen(open: boolean) {
  const el = root.value;
  if (!el) return;
  if (open) {
    Object.assign(el.style, { position: 'absolute', top: '0', left: '0', width: '100%', height: '100%', zIndex: '500' });
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
    el.style.position = '';
  }
  syncOpen();
}

/** The container can also be shown by switchView('globe'): follow its class, not our own calls. */
function syncOpen() {
  const open = !!root.value && !root.value.classList.contains('hidden');
  globeOpen.value = open;
}

watch(globeOpen, (open) => {
  if (!open) { cancelAnimationFrame(raf); raf = 0; pill.value = null; return; }
  requestAnimationFrame(() => {
    if (!init()) return;
    resize();
    if (!raf) raf = requestAnimationFrame(frame);
  });
});
watch(() => [store.regions, store.selectedName], () => { buildMarkers(); });
watch(() => store.selectedName, (n, old) => { if (n && n !== old && !drag) faceSelected(); });

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && globeOpen.value) setOpen(false);
}

onMounted(() => {
  const w = window as any;
  w.initGlobe = () => { syncOpen(); if (globeOpen.value && init()) { resize(); if (!raf) raf = requestAnimationFrame(frame); } };
  w.setGlobeOpen = setOpen;
  mo = new MutationObserver(syncOpen);
  if (root.value) mo.observe(root.value, { attributes: true, attributeFilter: ['class'] });
  if (host.value) { resizeObs = new ResizeObserver(resize); resizeObs.observe(host.value); }
  window.addEventListener('keydown', onKey);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey);
  mo?.disconnect();
  resizeObs?.disconnect();
  cancelAnimationFrame(raf);
  renderer?.dispose?.();
  const w = window as any;
  delete w.initGlobe;
  delete w.setGlobeOpen;
});
</script>

<style scoped>
.globe-container { background: #000; }
#globe { position: absolute; inset: 0; cursor: grab; touch-action: none; }
#globe :deep(canvas) { display: block; }
.gv-pill {
  position: absolute; transform: translate(-50%, -100%); pointer-events: none; white-space: nowrap;
  background: rgba(28, 28, 30, 0.95); color: #f2f2f7; border-radius: 10px; padding: 4px 10px;
  font-size: 12px; font-weight: 600; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
}
.gv-hint, .gv-fail {
  position: absolute; left: 50%; bottom: 20px; transform: translateX(-50%); pointer-events: none;
  background: rgba(28, 28, 30, 0.9); color: #98989f; border-radius: 16px; padding: 8px 16px; font-size: 12px;
}
</style>

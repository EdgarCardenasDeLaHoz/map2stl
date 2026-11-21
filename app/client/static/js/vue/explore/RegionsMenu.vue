<template>
  <div ref="root" class="rm">
    <button id="regionsMenuBtn" type="button" class="rm-btn" aria-haspopup="menu" :aria-expanded="open"
            aria-label="Region list options" title="Export, import, boxes, wide table, hide" @click="open = !open">⋯</button>
    <div v-if="open" class="rm-menu" role="menu" aria-label="Region list options" @keydown.esc.stop="close">
      <button type="button" role="menuitem" class="rm-item" @click="doExport">
        <span>Export regions…</span><span class="rm-dim">.json</span>
      </button>
      <button type="button" role="menuitem" class="rm-item" @click="pick">
        <span>Import regions…</span><span class="rm-dim">.json</span>
      </button>
      <button type="button" role="menuitemcheckbox" class="rm-item" :aria-checked="store.boxesVisible"
              @click="store.boxesVisible = !store.boxesVisible">
        <span>Show boxes on map</span>
        <span class="rm-switch" :class="{ off: !store.boxesVisible }"></span>
      </button>
      <button type="button" role="menuitem" class="rm-item" @click="emit('wide')">
        <span>Wide table</span><span class="rm-dim">⤢</span>
      </button>
      <button type="button" role="menuitem" class="rm-item" @click="emit('hide')">
        <span>Hide this panel</span><span class="rm-dim">✕</span>
      </button>
    </div>
    <input ref="file" class="rm-file" type="file" accept="application/json,.json" tabindex="-1"
           aria-hidden="true" @change="onFile">
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useRegionsStore } from '../stores/regions';

const emit = defineEmits<{ (e: 'wide'): void; (e: 'hide'): void }>();
const store = useRegionsStore();
const open = ref(false);
const root = ref<HTMLElement | null>(null);
const file = ref<HTMLInputElement | null>(null);

function close() { open.value = false; }
function doExport() { close(); void (window as any).exportRegionsJson?.(); }
function pick() { close(); file.value?.click(); }
async function onFile(e: Event) {
  const input = e.target as HTMLInputElement;
  await (window as any).importRegionsJsonFile?.(input.files?.[0]);
  input.value = '';
}
function outside(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) close();
}
onMounted(() => document.addEventListener('mousedown', outside));
onBeforeUnmount(() => document.removeEventListener('mousedown', outside));
</script>

<style scoped>
.rm { position: relative; }
.rm-btn {
  width: 28px; height: 28px; border-radius: 8px; border: 0; background: var(--card, #2c2c2e);
  color: var(--dim, #98989f); font-size: 14px; cursor: pointer;
}
.rm-btn[aria-expanded='true'] { background: rgba(10, 132, 255, 0.25); color: #fff; }
.rm-menu {
  position: absolute; top: 34px; left: 0; z-index: 50; width: 250px; padding: 6px; border-radius: 12px;
  background: #2c2c2e; border: 1px solid var(--line, #3a3a3c); box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
}
.rm-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; border: 0;
  background: none; color: var(--text, #f2f2f7); padding: 9px 10px; border-radius: 8px; font-size: 13px;
  cursor: pointer; text-align: left;
}
.rm-item:hover, .rm-item:focus-visible { background: rgba(255, 255, 255, 0.08); }
.rm-dim { color: var(--dim, #98989f); }
.rm-switch { width: 38px; height: 22px; border-radius: 11px; background: var(--corr, #30d158); position: relative; flex: none; }
.rm-switch::after { content: ''; position: absolute; right: 2px; top: 2px; width: 18px; height: 18px; border-radius: 9px; background: #fff; }
.rm-switch.off { background: #48484a; }
.rm-switch.off::after { right: auto; left: 2px; }
.rm-file { position: absolute; width: 0; height: 0; opacity: 0; pointer-events: none; }
</style>

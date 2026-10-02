<template>
  <!-- "More settings" in a page's right-hand panel (F-DESIGN, user 2026-10-02: settings on the
       right, merged with that panel; only the open page's own). Each switch shows its full
       section in the same panel, under this list. Tools: stores/uiMode.ts. -->
  <section class="ts" :aria-label="`More ${page} settings`">
    <div class="ts-cap">More settings
      <button v-if="anyOn" type="button" class="ts-reset" title="Hide every section below"
              @click="ui.setAll(false, page)">Hide all</button></div>
    <div class="ts-group">
      <label v-for="t in tools" :key="t.id" class="ts-row">
        <span class="ts-ic" aria-hidden="true">{{ t.icon }}</span>
        <span class="ts-grow"><span class="ts-n">{{ t.label }}</span><span class="ts-d">{{ t.hint }}</span></span>
        <input type="checkbox" role="switch" class="ts-switch" :aria-label="t.label" :data-tool="t.id"
               :checked="ui.shows(t.id)"
               @change="ui.setTool(t.id, ($event.target as HTMLInputElement).checked)">
      </label>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { UI_TOOLS, useUiModeStore, type UiTool } from '../../stores/uiMode';

const props = defineProps<{ page: UiTool['page'] }>();
const ui = useUiModeStore();
const tools = computed(() => UI_TOOLS.filter((t) => t.page === props.page));
const anyOn = computed(() => tools.value.some((t) => ui.shows(t.id)));
</script>

<style scoped>
.ts { margin: 0 0 12px; }
.ts-cap {
  display: flex; align-items: center; margin: 0 4px 8px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted);
}
.ts-reset { margin-left: auto; background: none; border: 0; color: #0a84ff; font-size: 12px; cursor: pointer; text-transform: none; letter-spacing: 0; }
.ts-group { background: #1c1c1e; border-radius: 14px; overflow: hidden; }
.ts-row {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px; cursor: pointer;
  border-top: 1px solid #2f2f31; color: #f5f5f7;
}
.ts-group .ts-row:first-child { border-top: 0; }
.ts-ic { width: 26px; height: 26px; border-radius: 8px; background: #2c2c2e; display: grid; place-items: center; flex: none; font-size: 13px; }
.ts-grow { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ts-n { font-weight: 600; font-size: 13px; }
.ts-d { color: var(--text-muted); font-size: 12px; line-height: 1.35; }
.ts-switch {
  appearance: none; -webkit-appearance: none; flex: none; position: relative; cursor: pointer;
  width: 40px; height: 24px; border-radius: 999px; background: #3a3a3c; margin: 0; transition: background .15s;
}
.ts-switch::after {
  content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
  border-radius: 50%; background: #fff; transition: left .15s;
}
.ts-switch:checked { background: #30d158; }
.ts-switch:checked::after { left: 18px; }
.ts-switch:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
</style>

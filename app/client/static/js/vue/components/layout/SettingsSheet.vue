<template>
  <!-- ⚙ Settings: a sheet from the right over the dimmed, live page (design guidelines §3).
       Mode and tool switches come from stores/uiMode.ts; Account & help replaces the header's
       Guides / Results / Diagnostics / Keys / Docs buttons. The region's data / look / model
       settings join this sheet with the Edit rebuild (F-DESIGN). -->
  <div v-if="open" class="ss-backdrop" @click.self="$emit('close')" @keydown.esc="$emit('close')">
    <aside class="ss-sheet" role="dialog" aria-modal="true" aria-labelledby="ssTitle">
      <div class="ss-head">
        <h2 id="ssTitle">Settings</h2>
        <button type="button" class="ss-x" aria-label="Close settings" @click="$emit('close')">✕</button>
      </div>
      <input ref="searchEl" v-model="query" type="search" class="ss-search"
             placeholder="Search settings (e.g. “puzzle”, “keys”)" aria-label="Search settings">

      <section v-if="match('mode beginner custom everything tools')">
        <div class="ss-cap">Mode
          <span class="ss-info" title="Beginner shows the essentials. Switching a tool on adds its card to the page and sets Custom. Everything shows every tool.">ⓘ</span></div>
        <div class="ss-seg" role="radiogroup" aria-label="Mode">
          <button v-for="m in MODES" :key="m.id" type="button" role="radio" :aria-checked="ui.mode === m.id"
                  :class="{ on: ui.mode === m.id }" @click="ui.setMode(m.id)">{{ m.label }}</button>
        </div>
      </section>

      <section v-if="tools.length">
        <div class="ss-cap">Extrude tools
          <button v-if="anyToolOn" type="button" class="ss-reset" @click="resetTools">Reset</button></div>
        <div class="ss-group">
          <label v-for="t in tools" :key="t.id" class="ss-row">
            <span class="ss-ic">{{ t.icon }}</span>
            <span class="ss-grow"><span class="ss-n">{{ t.label }}</span><span class="ss-d">{{ t.hint }}</span></span>
            <input type="checkbox" role="switch" class="ss-switch" :aria-label="t.label"
                   :checked="ui.shows(t.id)" :disabled="ui.mode === 'everything'"
                   @change="ui.setTool(t.id, ($event.target as HTMLInputElement).checked)">
          </label>
        </div>
        <div v-if="ui.mode === 'everything'" class="ss-note">Everything shows every tool. Pick Custom to choose.</div>
      </section>

      <section v-if="help.length">
        <div class="ss-cap">Account &amp; help</div>
        <div class="ss-group">
          <component :is="h.href ? 'a' : 'button'" v-for="h in help" :key="h.label" class="ss-row ss-link"
                     :href="h.href" :target="h.href ? '_blank' : undefined" :rel="h.href ? 'noopener' : undefined"
                     :type="h.href ? undefined : 'button'" @click="h.action?.()">
            <span class="ss-ic">{{ h.icon }}</span>
            <span class="ss-grow"><span class="ss-n">{{ h.label }}</span><span class="ss-d">{{ h.hint }}</span></span>
            <span class="ss-chev">›</span>
          </component>
        </div>
      </section>

      <div v-if="!tools.length && !help.length && !match('mode beginner custom everything tools')" class="ss-note">
        Nothing matches “{{ query }}”.</div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { UI_TOOLS, useUiModeStore } from '../../stores/uiMode';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; keys: []; diag: [] }>();
const ui = useUiModeStore();
// Screenshot and SOP scripts switch modes without clicking through the sheet.
(window as any).setUiMode = (m: 'beginner' | 'custom' | 'everything') => ui.setMode(m);

const MODES = [
  { id: 'beginner', label: 'Beginner' },
  { id: 'custom', label: 'Custom' },
  { id: 'everything', label: 'Everything' },
] as const;

const query = ref('');
const searchEl = ref<HTMLInputElement | null>(null);
watch(() => props.open, async (o) => {
  if (!o) return;
  query.value = '';
  await nextTick();
  searchEl.value?.focus();
});

function match(text: string): boolean {
  const q = query.value.trim().toLowerCase();
  return !q || text.toLowerCase().includes(q);
}

const tools = computed(() => UI_TOOLS.filter((t) => match(`${t.label} ${t.hint} ${t.page}`)));
const anyToolOn = computed(() => Object.values(ui.tools).some(Boolean));
function resetTools() {
  for (const t of UI_TOOLS) ui.setTool(t.id, false);
}

const HELP = [
  { icon: '🔑', label: 'Keys & data folders', hint: 'Earth Engine, OpenTopography, local elevation tiles',
    action: () => { emit('close'); emit('keys'); } },
  { icon: '🩺', label: 'Diagnostics', hint: 'Server, keys, elevation sources, cache',
    action: () => { emit('close'); emit('diag'); } },
  { icon: '📘', label: 'Guides', hint: 'Step-by-step guides with screenshots', href: '/guides' },
  { icon: '📊', label: 'Pipeline results', hint: 'Skyline and registration reports', href: '/reports' },
  { icon: '📋', label: 'API docs', hint: 'Swagger UI', href: '/docs' },
  { icon: '📘', label: 'API reference (ReDoc)', hint: 'The same API, as a document', href: '/redoc' },
  { icon: '📚', label: 'Project docs', hint: 'How map2stl works', href: '/project-docs/' },
  { icon: '🐍', label: 'Python API reference', hint: 'numpy2stl, geo2stl, city2stl', href: '/api-reference/' },
];
const help = computed(() => HELP.filter((h) => match(`${h.label} ${h.hint}`)));
</script>

<style scoped>
.ss-backdrop { position: fixed; inset: 0; z-index: 8500; background: rgba(0, 0, 0, 0.45); }
.ss-sheet {
  position: absolute; top: 0; right: 0; bottom: 0; width: min(500px, 100vw); overflow-y: auto;
  background: #151517; box-shadow: -10px 0 40px rgba(0, 0, 0, 0.6); padding: 18px 20px 24px;
  display: flex; flex-direction: column; gap: 16px; color: #f5f5f7; font-size: 14px;
}
.ss-head { display: flex; align-items: center; }
.ss-head h2 { margin: 0; font-size: 20px; }
.ss-x { margin-left: auto; background: none; border: 0; color: #a1a1a6; font-size: 18px; cursor: pointer; }
.ss-search { background: #2c2c2e; border: 0; border-radius: 10px; padding: 9px 12px; color: inherit; font-size: 13px; }
.ss-cap {
  display: flex; align-items: center; gap: 6px; margin: 0 0 8px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #a1a1a6;
}
.ss-info { cursor: help; color: #0a84ff; text-transform: none; }
.ss-reset { margin-left: auto; background: none; border: 0; color: #0a84ff; font-size: 12px; cursor: pointer; }
.ss-seg { display: flex; background: #1c1c1e; border-radius: 10px; padding: 3px; gap: 2px; }
.ss-seg button { flex: 1; border: 0; border-radius: 8px; padding: 7px 0; background: none; color: #a1a1a6; font-size: 13px; cursor: pointer; }
.ss-seg button.on { background: #3a3a3c; color: #f5f5f7; font-weight: 600; }
.ss-group { background: #1c1c1e; border-radius: 14px; overflow: hidden; }
.ss-row {
  display: flex; align-items: center; gap: 12px; padding: 10px 14px; width: 100%;
  border: 0; border-top: 1px solid #2f2f31; background: none; color: inherit; text-align: left;
  font: inherit; text-decoration: none; cursor: pointer;
}
.ss-group .ss-row:first-child { border-top: 0; }
.ss-link:hover { background: #232326; }
.ss-ic { width: 28px; height: 28px; border-radius: 8px; background: #2c2c2e; display: grid; place-items: center; flex: none; }
.ss-grow { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ss-n { font-weight: 600; }
.ss-d { color: #a1a1a6; font-size: 12px; }
.ss-chev { color: #a1a1a6; }
.ss-note { color: #a1a1a6; font-size: 12px; margin-top: 6px; }
.ss-switch {
  appearance: none; -webkit-appearance: none; flex: none; position: relative; cursor: pointer;
  width: 40px; height: 24px; border-radius: 999px; background: #3a3a3c; margin: 0; transition: background .15s;
}
.ss-switch::after {
  content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
  border-radius: 50%; background: #fff; transition: left .15s;
}
.ss-switch:checked { background: #30d158; }
.ss-switch:checked::after { left: 18px; }
.ss-switch:disabled { opacity: 0.6; cursor: default; }
.ss-switch:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
</style>

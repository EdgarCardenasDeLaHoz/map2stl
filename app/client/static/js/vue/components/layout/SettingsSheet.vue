<template>
  <!-- ⚙ Settings: a panel docked on the left of the page (it pushes the page over; it does
       not float over it), listing the mode and the current page's tools only: Edit tools on
       Edit, Extrude tools on Extrude (user, 2026-10-02). Account & help replaces the header's
       Guides / Results / Diagnostics / Keys / Docs buttons. Teleported into .app-container,
       ordered first. -->
  <Teleport to=".app-container">
    <aside v-if="open" class="ss-sheet" role="complementary" aria-labelledby="ssTitle"
           @keydown.esc="$emit('close')">
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

      <section v-for="g in toolGroups" :key="g.page">
        <div class="ss-cap">{{ g.title }}
          <button v-if="g.anyOn" type="button" class="ss-reset" @click="resetTools(g.page)">Reset</button></div>
        <div class="ss-group">
          <label v-for="t in g.tools" :key="t.id" class="ss-row">
            <span class="ss-ic">{{ t.icon }}</span>
            <span class="ss-grow"><span class="ss-n">{{ t.label }}</span><span class="ss-d">{{ t.hint }}</span></span>
            <input type="checkbox" role="switch" class="ss-switch" :aria-label="t.label"
                   :checked="ui.shows(t.id)" :disabled="ui.mode === 'everything'"
                   @change="ui.setTool(t.id, ($event.target as HTMLInputElement).checked)">
          </label>
        </div>
      </section>
      <div v-if="toolGroups.length && ui.mode === 'everything'" class="ss-note">Everything shows every tool. Pick Custom to choose.</div>

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

      <div v-if="!toolGroups.length && !help.length && !match('mode beginner custom everything tools')" class="ss-note">
        Nothing matches “{{ query }}”.</div>
    </aside>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { UI_TOOLS, useUiModeStore } from '../../stores/uiMode';
import { useAppStore } from '../../stores/app';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; keys: []; diag: [] }>();
const ui = useUiModeStore();
// Screenshot and SOP scripts switch modes without clicking through the sheet.
(window as any).setUiMode = (m: 'beginner' | 'custom' | 'everything') => ui.setMode(m);
// body.ui-<mode> lets CSS hide legacy chrome (e.g. the Buildings side tab) outside Everything.
watch(() => ui.mode, (m) => {
  document.body.classList.remove('ui-beginner', 'ui-custom', 'ui-everything');
  document.body.classList.add(`ui-${m}`);
}, { immediate: true });

const MODES = [
  { id: 'beginner', label: 'Beginner' },
  { id: 'custom', label: 'Custom' },
  { id: 'everything', label: 'Everything' },
] as const;

const query = ref('');
const searchEl = ref<HTMLInputElement | null>(null);
watch(() => props.open, async (o) => {
  await nextTick();
  window.dispatchEvent(new Event('resize'));
  (window as any).getMap?.()?.invalidateSize?.();
  if (!o) return;
  query.value = '';
  await nextTick();
  searchEl.value?.focus();
});

function match(text: string): boolean {
  const q = query.value.trim().toLowerCase();
  return !q || text.toLowerCase().includes(q);
}

const PAGE_TITLES = { edit: 'Edit tools', extrude: 'Extrude tools' } as const;
const tools = computed(() => UI_TOOLS.filter((t) => match(`${t.label} ${t.hint} ${t.page}`)));
const app = useAppStore();
/** Tools of the page that is open: Edit or Extrude; Explore has none. */
const page = computed(() => ({ dem: 'edit', model: 'extrude' } as const)[app.activeView as 'dem' | 'model'] ?? null);
const toolGroups = computed(() => (page.value ? [page.value] : [])
  .map((page) => {
    const list = tools.value.filter((t) => t.page === page);
    return { page, title: PAGE_TITLES[page], tools: list, anyOn: list.some((t) => ui.tools[t.id]) };
  })
  .filter((g) => g.tools.length));
function resetTools(page: 'edit' | 'extrude') {
  for (const t of UI_TOOLS) if (t.page === page) ui.setTool(t.id, false);
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
.ss-sheet {
  order: -1; flex: none; width: 340px; height: 100vh; overflow-y: auto; box-sizing: border-box;
  background: #151517; border-right: 1px solid #2f2f31; padding: 18px 18px 24px;
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

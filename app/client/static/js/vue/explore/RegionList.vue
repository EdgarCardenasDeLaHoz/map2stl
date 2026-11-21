<template>
  <div id="sidebarListView" class="rl">
    <div class="rl-filters">
      <input id="coordSearch" v-model="search" type="search" class="rl-search" autocomplete="off"
             placeholder="Search regions…" aria-label="Search regions">
      <div class="rl-bar">
        <div v-if="list.showScopeTabs" class="rl-seg" role="tablist" aria-label="Which regions to list">
          <button type="button" role="tab" :aria-selected="list.effectiveScope === 'view'"
                  :class="{ on: list.effectiveScope === 'view' }" :title="list.inViewTitle"
                  @click="store.setScope('view')">In view ({{ list.inView }})</button>
          <button type="button" role="tab" :aria-selected="list.effectiveScope === 'all'"
                  :class="{ on: list.effectiveScope === 'all' }" title="Every saved region"
                  @click="store.setScope('all')">All ({{ list.total }})</button>
        </div>
        <div v-else class="rl-found">{{ list.filtered }} found</div>
        <select id="coordContinentFilter" v-model="continent" class="rl-continent"
                aria-label="Filter regions by continent">
          <option value="all">All continents</option>
          <option v-for="c in store.continents" :key="c" :value="c">{{ c }}</option>
        </select>
      </div>
    </div>

    <div id="coordinatesList" ref="listEl" class="rl-list coordinates-list" @keydown="onKey">
      <div v-if="store.loading && !store.loaded" class="rl-empty">Loading regions…</div>
      <div v-else-if="store.error" class="rl-empty">Could not load regions: {{ store.error }}</div>
      <div v-else-if="list.state === 'empty'" class="rl-empty">
        <b>Draw a region on the map to begin</b>
        <span>Use ＋ New region below, or the ▭ button on the map, to select an area.</span>
      </div>
      <div v-else-if="list.state === 'no-view'" class="rl-empty">
        <b>No saved regions fit this view.</b>
        <span>Zoom out, or <button type="button" class="rl-link" @click="store.setScope('all')">show all</button>.</span>
      </div>
      <div v-else-if="!list.groups.length" class="rl-empty">No regions match.</div>
      <template v-else>
        <section v-for="g in list.groups" :key="g.continent" class="rl-group">
          <button type="button" class="rl-group-head" :aria-expanded="!isCollapsed(g.continent)"
                  @click="store.toggleGroup(g.continent)">
            <span class="rl-caret">{{ isCollapsed(g.continent) ? '▸' : '▾' }}</span>
            <span class="rl-group-name">{{ g.continent }}</span>
            <span class="rl-group-count">{{ g.regions.length }}</span>
          </button>
          <template v-if="!isCollapsed(g.continent)">
            <div v-for="r in g.regions" :key="r.name" class="rl-row coordinate-item" role="button" tabindex="0"
                 :data-region-name="r.name" :class="{ sel: r.name === store.selectedName, hot: r.name === store.hoverName }"
                 :aria-current="r.name === store.selectedName ? 'true' : undefined"
                 :title="`Select ${r.name}`"
                 @click="store.select(r.name)"
                 @mouseenter="store.hoverName = r.name" @mouseleave="clearHover(r.name)"
                 @focus="store.hoverName = r.name" @blur="clearHover(r.name)">
              <span class="rl-sw" :style="{ background: colorOf(r.name) }"></span>
              <span class="rl-text">
                <span class="rl-name coordinate-item-name">{{ r.name }}</span>
                <span class="rl-meta">{{ meta(r) }}</span>
              </span>
              <button type="button" class="rl-edit coordinate-item-edit" :aria-label="`Edit ${r.name}`"
                      title="Edit name, group and bounds" @click.stop="store.openEditor(r.name)">✎</button>
            </div>
          </template>
        </section>
      </template>
    </div>

    <div v-if="list.effectiveScope === 'all' && list.pages > 1" class="rl-pager">
      <button type="button" :disabled="list.page <= 0" @click="store.page = list.page - 1">‹ Prev</button>
      <span>{{ list.start + 1 }}–{{ list.end }} of {{ list.filtered }}</span>
      <button type="button" :disabled="list.page >= list.pages - 1" @click="store.page = list.page + 1">Next ›</button>
    </div>

    <div class="rl-foot">
      <!-- new-region.js binds this button by id: it starts drawing a box on the map. -->
      <button id="floatingDrawBtn" type="button" class="rl-new map-draw-region-btn"
              title="Draw a new region on the map"
              @click="startDraw">＋ New region</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useRegionsStore } from '../stores/regions';

function startDraw() { (window as any).newRegion?.startDraw(); }
import { formatBboxDims, regionColor } from '../../modules/regions/region-geometry.js';
import { metaLine } from './regionList';

const store = useRegionsStore();
const list = computed(() => store.list);
const listEl = ref<HTMLElement | null>(null);

const search = computed({ get: () => store.search, set: (v: string) => store.setSearch(v) });
const continent = computed({ get: () => store.continent, set: (v: string) => store.setContinent(v) });

const isCollapsed = (c: string) => store.collapsed.includes(c);
const colorOf = (name: string) => regionColor(name);
const meta = (r: any) => metaLine([formatBboxDims(r), r.description]);
function clearHover(name: string) { if (store.hoverName === name) store.hoverName = null; }

/** Enter/Space select the focused row; Up/Down move between rows; the ✎ button keeps its own keys. */
function onKey(e: KeyboardEvent) {
  const row = (e.target as HTMLElement).closest('.rl-row') as HTMLElement | null;
  if (!row || e.target !== row) return;
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    void store.select(row.dataset.regionName || '');
  } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const rows = [...(listEl.value?.querySelectorAll('.rl-row') ?? [])] as HTMLElement[];
    rows[rows.indexOf(row) + (e.key === 'ArrowDown' ? 1 : -1)]?.focus();
  }
}

// Keep the selected row in sight when the selection changes elsewhere (a box click, a link).
watch(() => store.selectedName, async (name) => {
  if (!name) return;
  await nextTick();
  const rows = [...(listEl.value?.querySelectorAll('.rl-row') ?? [])] as HTMLElement[];
  rows.find((r) => r.dataset.regionName === name)?.scrollIntoView?.({ block: 'nearest' });
});
</script>

<style scoped>
.rl { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.rl-filters { padding: 4px 14px 0; }
.rl-search {
  width: 100%; box-sizing: border-box; background: var(--card, #2c2c2e); color: var(--text, #f2f2f7);
  border: 0; border-radius: 10px; padding: 8px 12px; font-size: 13px; margin: 6px 0;
}
.rl-search:focus-visible { outline: 2px solid var(--accent, #0a84ff); }
.rl-bar { display: flex; align-items: center; gap: 8px; margin: 2px 0 8px; }
.rl-seg { flex: 1; display: flex; background: #111113; border-radius: 9px; padding: 2px; }
.rl-seg button {
  flex: 1; border: 0; border-radius: 7px; padding: 5px 4px; background: none; cursor: pointer;
  color: var(--dim, #98989f); font-size: 12px; white-space: nowrap;
}
.rl-seg button.on { background: var(--card, #2c2c2e); color: var(--text, #f2f2f7); }
.rl-found { flex: 1; color: var(--dim, #98989f); font-size: 12px; padding-left: 4px; }
.rl-continent {
  border: 0; border-radius: 14px; background: var(--accent, #0a84ff); color: #fff; font-size: 12px;
  padding: 4px 8px; max-width: 128px; cursor: pointer;
}
.rl-continent option { background: #2c2c2e; color: #f2f2f7; }
.rl-list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 10px 8px; }
.rl-empty { display: flex; flex-direction: column; gap: 6px; padding: 24px 12px; text-align: center; color: var(--dim, #98989f); font-size: 12px; }
.rl-empty b { color: var(--text, #f2f2f7); font-size: 13px; }
.rl-link { background: none; border: 0; color: var(--accent, #0a84ff); cursor: pointer; padding: 0; font: inherit; text-decoration: underline; }
.rl-group-head {
  display: flex; align-items: center; gap: 6px; width: 100%; background: none; border: 0; cursor: pointer;
  margin: 8px 0 2px; padding: 2px 6px; color: var(--dim, #98989f); font-size: 11px; letter-spacing: 0.06em;
  text-transform: uppercase; text-align: left;
}
.rl-group-name { flex: 1; }
.rl-row {
  display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 10px; cursor: pointer;
  color: var(--text, #f2f2f7); outline: 1px solid transparent;
}
.rl-row:hover, .rl-row.hot { background: rgba(255, 255, 255, 0.06); }
.rl-row.sel { background: rgba(10, 132, 255, 0.22); outline-color: var(--accent, #0a84ff); }
.rl-row:focus-visible { outline: 2px solid var(--accent, #0a84ff); }
.rl-sw { width: 12px; height: 12px; border-radius: 4px; flex: none; }
.rl-text { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.rl-name { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rl-meta { color: var(--dim, #98989f); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rl-edit {
  background: none; border: 0; color: var(--dim, #98989f); cursor: pointer; font-size: 14px;
  border-radius: 6px; width: 26px; height: 26px; flex: none;
}
.rl-edit:hover { color: #fff; background: var(--card, #2c2c2e); }
.rl-pager { display: flex; align-items: center; justify-content: space-between; padding: 6px 14px; color: var(--dim, #98989f); font-size: 12px; }
.rl-pager button { background: var(--card, #2c2c2e); color: var(--text, #f2f2f7); border: 0; border-radius: 8px; padding: 4px 10px; cursor: pointer; }
.rl-pager button:disabled { opacity: 0.4; cursor: default; }
.rl-foot { padding: 12px 14px; border-top: 1px solid var(--line, #3a3a3c); }
.rl-new {
  display: block; width: 100%; border: 0; border-radius: 10px; padding: 10px; background: var(--accent, #0a84ff);
  color: #fff; font-weight: 600; font-size: 14px; cursor: pointer;
}
.rl-new.drawing { background: #0066cc; }
</style>

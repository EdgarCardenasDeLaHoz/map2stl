<template>
  <div id="sidebarTableView" class="rt">
    <div class="rt-controls">
      <input id="sidebarTableSearch" v-model="store.tableSearch" type="search" class="rt-search"
             autocomplete="off" placeholder="Search regions…" aria-label="Search regions table">
      <select id="sidebarCategoryFilter" v-model="store.tableCategory" class="rt-cat"
              aria-label="Filter by group" title="Filter by region group">
        <option value="">All groups</option>
        <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
      </select>
    </div>
    <div class="rt-scroll">
      <table id="sidebarRegionsTable" class="rt-table">
        <thead>
          <tr>
            <th>Name</th>
            <th class="n" title="North">N</th>
            <th class="n" title="South">S</th>
            <th class="n" title="East">E</th>
            <th class="n" title="West">W</th>
            <th class="act"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody id="sidebarRegionsTableBody">
          <template v-for="g in groups" :key="g.continent">
            <tr class="rt-group">
              <td colspan="6">
                <button type="button" :aria-expanded="!isCollapsed(g.continent)"
                        @click="store.toggleGroup('t:' + g.continent)">
                  <span>{{ isCollapsed(g.continent) ? '▸' : '▾' }}</span>
                  {{ g.continent }} <span class="rt-count">{{ g.regions.length }}</span>
                </button>
              </td>
            </tr>
            <template v-if="!isCollapsed(g.continent)">
              <tr v-for="r in g.regions" :key="r.name" class="rt-row" :class="{ sel: r.name === store.selectedName }"
                  :data-category="categoryOf(r)" @click="store.select(r.name)">
                <td class="rt-name" :title="r.name">{{ r.name }}</td>
                <td class="n">{{ fmt(r.north) }}</td>
                <td class="n">{{ fmt(r.south) }}</td>
                <td class="n">{{ fmt(r.east) }}</td>
                <td class="n">{{ fmt(r.west) }}</td>
                <td class="act">
                  <button type="button" class="rt-edit" :aria-label="`Edit ${r.name}`"
                          title="Open this region in Edit" @click.stop="store.goToEdit(r.name)">✎ Edit</button>
                  <button type="button" class="rt-del" :aria-label="`Delete ${r.name}`"
                          title="Delete this region" @click.stop="store.remove(r.name)">🗑</button>
                </td>
              </tr>
            </template>
          </template>
          <tr v-if="!groups.length"><td colspan="6" class="rt-none">No regions match.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue';
import { useRegionsStore } from '../stores/regions';
import { buildTableGroups, categoryOf, categoryOptions } from './regionList';

const store = useRegionsStore();
const categories = computed(() => categoryOptions(store.regions));
const groups = computed(() => buildTableGroups({
  regions: store.regions, search: store.tableSearch, category: store.tableCategory,
}));
const isCollapsed = (c: string) => store.collapsed.includes('t:' + c);
const fmt = (v: number) => Number(v).toFixed(3);

// A group that no longer exists (its last region was deleted or renamed) must not stay chosen.
watch(categories, (list) => {
  if (store.tableCategory && !list.includes(store.tableCategory)) store.tableCategory = '';
});
</script>

<style scoped>
.rt { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.rt-controls { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; padding: 6px 14px 10px; }
.rt-search {
  align-self: stretch; min-width: 0; background: var(--card, #2c2c2e); color: var(--text, #f2f2f7); border: 0;
  border-radius: 10px; padding: 8px 12px; font-size: 13px;
}
.rt-search:focus-visible { outline: 2px solid var(--accent, #0a84ff); }
.rt-cat {
  border: 0; border-radius: 14px; background: var(--accent, #0a84ff); color: #fff; font-size: 12px;
  padding: 3px 10px; max-width: 180px; height: auto; width: auto; cursor: pointer;
}
.rt-cat option { background: #2c2c2e; color: #f2f2f7; }
.rt-scroll { flex: 1; min-height: 0; overflow: auto; padding: 0 10px 10px; }
.rt-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.rt-table th {
  color: var(--dim, #98989f); font-weight: 500; font-size: 12px; text-align: left; padding: 6px 8px;
  border-bottom: 1px solid var(--line, #3a3a3c); position: sticky; top: 0; background: var(--panel, #1c1c1e);
}
.rt-table th:first-child { width: 28%; }
.n { text-align: right; font-variant-numeric: tabular-nums; width: 52px; }
.act { width: 92px; text-align: right; white-space: nowrap; padding-right: 2px; }
.rt-table td { padding: 7px 8px; border-bottom: 1px solid var(--line, #3a3a3c); font-size: 12px; color: var(--text, #f2f2f7); }
.rt-row { cursor: pointer; }
.rt-row:hover { background: rgba(255, 255, 255, 0.05); }
.rt-row.sel { background: rgba(10, 132, 255, 0.2); }
.rt-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rt-group td { padding: 8px 4px 2px; border: 0; }
.rt-group button {
  background: none; border: 0; cursor: pointer; color: var(--dim, #98989f); font-size: 11px;
  letter-spacing: 0.06em; text-transform: uppercase; padding: 0;
}
.rt-count { margin-left: 6px; }
.rt-edit {
  border: 0; border-radius: 12px; background: var(--accent, #0a84ff); color: #fff; font-size: 11px;
  padding: 2px 8px; height: auto; width: auto; min-width: 0; cursor: pointer;
}
.rt-del { border: 0; background: none; cursor: pointer; font-size: 12px; opacity: 0.8; padding: 2px 3px; filter: grayscale(1) brightness(2.2); min-width: 0; width: auto; }
.rt-del:hover { opacity: 1; }
.rt-none { text-align: center; color: var(--dim, #98989f); padding: 24px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>

<template>
  <div id="sidebarTableView" style="display:flex;flex-direction:column;flex:1;overflow:hidden;">
    <div style="padding:4px 8px;border-bottom:1px solid #333;flex-shrink:0;display:flex;flex-direction:column;gap:4px;">
      <input type="text" id="sidebarTableSearch" class="search-input"
             v-model="tableSearch"
             placeholder="Search regions…"
             style="margin:0;">
      <!-- Label filter chips -->
      <div v-if="availableLabels.length > 0" style="display:flex;flex-wrap:wrap;gap:3px;">
        <button
          class="label-chip"
          :class="{ active: activeLabels.size === 0 }"
          @click="clearLabels"
          title="Show all labels"
        >All</button>
        <button
          v-for="lbl in availableLabels"
          :key="lbl"
          class="label-chip"
          :class="{ active: activeLabels.has(lbl) }"
          @click="toggleLabel(lbl)"
          :title="`Filter by ${lbl}`"
        >{{ lbl }}</button>
      </div>
    </div>
    <div style="flex:1;overflow-y:auto;">
      <table class="sidebar-table-view" id="sidebarRegionsTable">
        <colgroup>
          <col style="width:auto">
          <col style="width:52px">
          <col style="width:52px">
          <col style="width:52px">
          <col style="width:52px">
          <col style="width:44px">
          <col style="width:80px">
        </colgroup>
        <thead>
          <tr>
            <th>Name</th>
            <th title="North">N</th>
            <th title="South">S</th>
            <th title="East">E</th>
            <th title="West">W</th>
            <th title="Grid dimension">Dim</th>
            <th>Actions</th>
          </tr>
        </thead>
        <!-- region-ui.js renderSidebarTable() still populates tbody via innerHTML
             during the transition; replaced by Vue rows in Stage 7 -->
        <tbody id="sidebarRegionsTableBody"></tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';

const tableSearch = ref('');
const availableLabels = ref<string[]>([]);
const activeLabels = ref<Set<string>>(new Set());

function refreshLabels() {
  const data: any[] = (window as any).getCoordinatesData?.() || [];
  const labels = [...new Set(data.map((r: any) => r.label).filter(Boolean))].sort() as string[];
  availableLabels.value = labels;
}

function toggleLabel(lbl: string) {
  const s = new Set(activeLabels.value);
  if (s.has(lbl)) s.delete(lbl);
  else s.add(lbl);
  activeLabels.value = s;
  applyLabelFilter();
}

function clearLabels() {
  activeLabels.value = new Set();
  applyLabelFilter();
}

function applyLabelFilter() {
  const tbody = document.getElementById('sidebarRegionsTableBody');
  if (!tbody) return;
  const active = activeLabels.value;
    for (const row of tbody.querySelectorAll('tr:not(.tbl-group-header)')) {
      if (active.size === 0) {
        (row as HTMLElement).style.display = '';
      } else {
        const label = (row as HTMLElement).dataset.label || '';
        (row as HTMLElement).style.display = active.has(label) ? '' : 'none';
      }
    }

    // Hide continent headers when all their rows are filtered out.
    const headers = Array.from(tbody.querySelectorAll('tr.tbl-group-header')) as HTMLElement[];
    for (const header of headers) {
      let sibling = header.nextElementSibling as HTMLElement | null;
      let hasVisibleRows = false;
      while (sibling && !sibling.classList.contains('tbl-group-header')) {
        if (sibling.style.display !== 'none') {
          hasVisibleRows = true;
          break;
        }
        sibling = sibling.nextElementSibling as HTMLElement | null;
      }
      header.style.display = hasVisibleRows ? '' : 'none';
    }
}

onMounted(() => {
  refreshLabels();
  // Expose for view-management.js to call after each re-render
  (window as any)._applyRegionLabelFilter = applyLabelFilter;
  // Re-read labels whenever region data changes
  (window as any).events?.on?.((window as any).EV?.REGION_SELECTED, () => refreshLabels());
});

watch(tableSearch, (val) => {
  // Notify existing JS handler attached to #sidebarTableSearch
  const el = document.getElementById('sidebarTableSearch') as HTMLInputElement | null;
  if (el && el.value !== val) {
    el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // Reapply label filter after search updates DOM
  setTimeout(applyLabelFilter, 50);
});
</script>

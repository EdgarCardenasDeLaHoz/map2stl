<template>
  <div id="sidebarTableView" style="display:flex;flex-direction:column;flex:1;overflow:hidden;">
    <div class="sidebar-table-controls">
      <input type="text" id="sidebarTableSearch" class="search-input" aria-label="Search regions table"
             v-model="tableSearch"
             placeholder="Search regions…"
             style="margin:0;">
      <select id="sidebarCategoryFilter" class="ctrl-select sidebar-category-select"
              v-model="selectedCategory"
              title="Filter by region category">
        <option value="">All categories</option>
        <option v-for="cat in availableCategories" :key="cat" :value="cat">{{ cat }}</option>
      </select>
    </div>
    <div style="flex:1;overflow-y:auto;">
      <table class="sidebar-table-view" id="sidebarRegionsTable">
        <colgroup>
          <col style="width:17%">
          <col style="width:48px">
          <col style="width:48px">
          <col style="width:48px">
          <col style="width:48px">
          <col style="width:42px">
          <col style="width:78px">
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
const availableCategories = ref<string[]>([]);
const selectedCategory = ref('');

function _categoryForRegion(region: any): string {
  const label = (region?.label || '').trim();
  if (label) return label;
  const lat = (Number(region?.north) + Number(region?.south)) / 2;
  const lon = (Number(region?.east) + Number(region?.west)) / 2;
  return (window as any).detectContinent?.(lat, lon) || 'Other';
}

function refreshCategories() {
  const data: any[] = (window as any).getCoordinatesData?.() || [];
  const categories = [...new Set(data.map((r: any) => _categoryForRegion(r)).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b)) as string[];
  availableCategories.value = categories;
  if (selectedCategory.value && !categories.includes(selectedCategory.value)) {
    selectedCategory.value = '';
  }
}

function applyTableFilter() {
  const tbody = document.getElementById('sidebarRegionsTableBody');
  if (!tbody) return;
  refreshCategories();
  const category = selectedCategory.value;
  for (const row of tbody.querySelectorAll('tr:not(.tbl-group-header)')) {
    if (!category) {
      (row as HTMLElement).style.display = '';
    } else {
      const rowCategory = (row as HTMLElement).dataset.category || '';
      (row as HTMLElement).style.display = rowCategory === category ? '' : 'none';
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
  refreshCategories();
  // Expose for view-management.js to call after each re-render
  (window as any)._applyRegionLabelFilter = applyTableFilter;
  // Re-read categories whenever region data changes
  (window as any).events?.on?.((window as any).EV?.REGION_SELECTED, () => refreshCategories());
});

watch(tableSearch, (val) => {
  // Notify existing JS handler attached to #sidebarTableSearch
  const el = document.getElementById('sidebarTableSearch') as HTMLInputElement | null;
  if (el && el.value !== val) {
    el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // Reapply category filter after search updates DOM
  setTimeout(applyTableFilter, 50);
});

watch(selectedCategory, () => {
  setTimeout(applyTableFilter, 0);
});
</script>

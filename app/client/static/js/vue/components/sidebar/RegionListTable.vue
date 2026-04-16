<template>
  <div id="sidebarTableView" style="display:flex;flex-direction:column;flex:1;overflow:hidden;">
    <div class="row-gap6" style="padding:4px 8px 4px;border-bottom:1px solid #333;flex-shrink:0;">
      <input type="text" id="sidebarTableSearch" class="search-input"
             v-model="tableSearch"
             placeholder="Search regions…"
             style="flex:1;margin:0;">
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
import { ref, watch } from 'vue';

const tableSearch = ref('');

watch(tableSearch, (val) => {
  // Notify existing JS handler attached to #sidebarTableSearch
  const el = document.getElementById('sidebarTableSearch') as HTMLInputElement | null;
  if (el && el.value !== val) {
    el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
});
</script>

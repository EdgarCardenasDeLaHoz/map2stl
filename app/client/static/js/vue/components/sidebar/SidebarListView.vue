<template>
  <div id="sidebarListView" :class="{ 'hidden': !visible }"
       class="sidebar-list-view">
    <input type="text" id="coordSearch" class="search-input sidebar-search-input" aria-label="Search regions"
           v-model="searchQuery"
           placeholder="Search regions...">
    <!-- region-ui.js / regions.js populate this div via innerHTML.
         Do NOT place Vue-managed children here — innerHTML replaces them and
         breaks Vue's virtual-DOM references (causes insertBefore-on-null).
         Once region-ui.js is fully migrated to composables, render from
         store.coordinatesData instead. -->
    <div id="coordinatesList" class="coordinates-list sidebar-coord-list"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { useAppStore } from '../../stores/app';

const props = defineProps<{ visible: boolean }>();
const store = useAppStore();
const searchQuery = ref('');

// Sync search input to the existing coordSearch handler
// (event-listeners-ui.js listens to #coordSearch input event)
watch(searchQuery, (val) => {
  const el = document.getElementById('coordSearch') as HTMLInputElement | null;
  if (el && el.value !== val) el.value = val;
});
</script>

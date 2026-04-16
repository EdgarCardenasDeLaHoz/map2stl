<template>
  <div id="sidebarListView" :class="{ 'hidden': !visible }"
       style="display:flex;flex-direction:column;height:100%;">
    <input type="text" id="coordSearch" class="search-input"
           v-model="searchQuery"
           placeholder="Search regions..."
           style="margin:4px 6px;width:calc(100% - 12px);flex-shrink:0;box-sizing:border-box;margin-bottom:4px;">
    <div id="coordinatesList" class="coordinates-list" style="flex:1;overflow-y:auto;">
      <div v-if="store.coordinatesData.length === 0" class="loading">
        <span class="spinner"></span>Loading coordinates...
      </div>
      <!-- Existing region-ui.js renderCoordinatesList() writes to #coordinatesList.
           During the migration the JS still populates this div via innerHTML.
           Once region-ui.js is fully migrated to composables this div renders
           from store.coordinatesData instead. -->
    </div>
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

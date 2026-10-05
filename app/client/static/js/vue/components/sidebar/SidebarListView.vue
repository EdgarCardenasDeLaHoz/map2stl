<template>
  <div id="sidebarListView" :class="{ 'hidden': !visible }"
       class="sidebar-list-view">
    <div class="sidebar-list-filters">
      <input type="text" id="coordSearch" class="search-input sidebar-search-input"
             placeholder="Search regions...">
      <select id="coordContinentFilter" class="sidebar-continent-filter" aria-label="Filter regions by continent">
        <option value="all" selected>All Continents</option>
      </select>
    </div>
    <!-- region-ui.js / regions.js populate this div via innerHTML.
         Do NOT place Vue-managed children here — innerHTML replaces them and
         breaks Vue's virtual-DOM references (causes insertBefore-on-null).
         Once region-ui.js is fully migrated to composables, render from
         store.coordinatesData instead. -->
    <div id="coordinatesList" class="coordinates-list sidebar-coord-list"></div>
    <!-- Draws a new box on the map (event-listeners-map.js::activateDrawTool, by id). -->
    <button id="floatingDrawBtn" type="button" class="map-draw-region-btn sidebar-new-region-btn"
            title="Draw a new region on the map">＋ New region</button>
  </div>
</template>

<script setup lang="ts">
// #coordSearch is wired by ui-helpers.js::setupCoordinateSearch.
defineProps<{ visible: boolean }>();
</script>

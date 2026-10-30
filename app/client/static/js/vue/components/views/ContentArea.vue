<template>
  <div class="content-area">
    <!-- Leaflet 2D map -->
    <MapContainer />

    <!-- Three.js 3D Globe -->
    <div id="globeContainer" class="globe-container hidden">
      <div id="globe"></div>
    </div>

    <!-- DEM/Edit view with canvas layers and settings panel -->
    <DemContainer />

    <!-- 3D Model generation view -->
    <ModelContainer />

    <!-- Benchmark page (Tools menu) -->
    <BenchmarkPage />

    <!-- Street View heights page (Tools menu) -->
    <HeightsPage />

    <!-- Photo solve page (Tools menu) -->
    <PhotoSolvePage />

    <!-- Plate align page (Tools menu) -->
    <PlateAlignPage />
  </div>
</template>
<script setup lang="ts">
import MapContainer   from './MapContainer.vue';
import DemContainer   from './DemContainer.vue';
import ModelContainer from './ModelContainer.vue';
import BenchmarkPage  from './BenchmarkPage.vue';
import HeightsPage    from './HeightsPage.vue';
import PhotoSolvePage from './PhotoSolvePage.vue';
import PlateAlignPage from './PlateAlignPage.vue';
</script>

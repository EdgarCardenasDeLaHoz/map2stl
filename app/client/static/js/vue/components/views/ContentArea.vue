<template>
  <div class="content-area">
    <!-- Leaflet 2D map -->
    <MapContainer />

    <!-- Three.js 3D Globe -->
    <div id="globeContainer" class="globe-container hidden">
      <div id="globe"></div>
    </div>

    <!-- Regions Table (legacy, hidden) -->
    <div id="regionsContainer" class="regions-container hidden">
      <div class="regions-header">
        <h3>📋 All Regions</h3>
        <div class="regions-actions">
          <input type="text" id="regionsSearch" placeholder="Search regions..." class="regions-search">
          <button id="refreshRegionsBtn" class="btn btn-secondary" title="Refresh list">🔄</button>
        </div>
      </div>
      <div class="regions-table-wrapper">
        <table class="regions-table" id="regionsTable">
          <thead>
            <tr>
              <th>Name</th>
              <th>North</th>
              <th>South</th>
              <th>East</th>
              <th>West</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="regionsTableBody">
            <!-- Populated by JavaScript -->
          </tbody>
        </table>
      </div>
    </div>

    <!-- Compare Container — side-by-side DEM comparison -->
    <div id="compareContainer" class="compare-container hidden">
      <div class="compare-panel" id="compareLeft">
        <div class="compare-panel-header">
          <span class="compare-panel-title">Left: <span id="compareLeftName">--</span></span>
          <div class="compare-panel-controls">
            <select id="compareLeftRegion"><option value="">Select region...</option></select>
          </div>
        </div>
        <div class="compare-panel-body">
          <img id="compareLeftImage" class="compare-dem-image" style="display:none;" />
          <div id="compareLeftEmpty" class="compare-empty">Select a region to compare</div>
        </div>
        <div class="compare-settings">
          <div class="compare-setting-group">
            <label>Colormap:</label>
            <select id="compareLeftColormap">
              <option value="terrain" selected>Terrain</option>
              <option value="viridis">Viridis</option>
              <option value="plasma">Plasma</option>
              <option value="gray">Gray</option>
            </select>
          </div>
          <div class="compare-setting-group">
            <label>Exag:</label>
            <input type="range" id="compareLeftExag" min="0.5" max="5" step="0.1" value="1">
            <span id="compareLeftExagLabel">1.0x</span>
          </div>
        </div>
      </div>
      <div class="compare-panel" id="compareRight">
        <div class="compare-panel-header">
          <span class="compare-panel-title">Right: <span id="compareRightName">--</span></span>
          <div class="compare-panel-controls">
            <select id="compareRightRegion"><option value="">Select region...</option></select>
          </div>
        </div>
        <div class="compare-panel-body">
          <img id="compareRightImage" class="compare-dem-image" style="display:none;" />
          <div id="compareRightEmpty" class="compare-empty">Select a region to compare</div>
        </div>
        <div class="compare-settings">
          <div class="compare-setting-group">
            <label>Colormap:</label>
            <select id="compareRightColormap">
              <option value="terrain" selected>Terrain</option>
              <option value="viridis">Viridis</option>
              <option value="plasma">Plasma</option>
              <option value="gray">Gray</option>
            </select>
          </div>
          <div class="compare-setting-group">
            <label>Exag:</label>
            <input type="range" id="compareRightExag" min="0.5" max="5" step="0.1" value="1">
            <span id="compareRightExagLabel">1.0x</span>
          </div>
        </div>
      </div>
    </div>

    <!-- DEM/Edit view with canvas layers and settings panel -->
    <DemContainer />

    <!-- 3D Model generation view -->
    <ModelContainer />
  </div>
</template>
<script setup lang="ts">
import MapContainer   from './MapContainer.vue';
import DemContainer   from './DemContainer.vue';
import ModelContainer from './ModelContainer.vue';
</script>

<template>
  <!-- Leaflet map view — IDs must stay for Leaflet init and event listeners -->
  <div id="mapContainer" class="map-container">
    <div id="map"></div>

    <!-- Floating Map Controls -->
    <div class="map-floating-controls">
      <button id="floatingTerrainToggle" class="map-floating-btn" title="Load terrain DEM for current map view" aria-label="Load terrain">🏔️ <span class="btn-label">Terrain</span></button>
      <button id="floatingGridToggle"    class="map-floating-btn" title="Toggle grid lines" aria-label="Toggle grid lines">📐 <span class="btn-label">Grid</span></button>
      <button id="floatingGlobeToggle"   class="map-floating-btn" title="Switch to 3D Globe view" aria-label="Switch to 3D Globe">🌍 <span class="btn-label">Globe</span></button>
      <button id="floatingRegionsToggle" class="map-floating-btn" title="Toggle regions panel" aria-label="Toggle regions panel">📋 <span class="btn-label">Regions</span></button>
      <button id="floatingLabelsToggle"  class="map-floating-btn" title="Toggle map labels" aria-label="Toggle map labels">🏷️ <span class="btn-label">Labels</span></button>
      <button id="floatingMapSettingsBtn" class="map-floating-btn" title="Map display settings" aria-label="Map display settings">⚙️ <span class="btn-label">Settings</span></button>
    </div>

    <!-- Map Settings Panel -->
    <div id="mapSettingsPanel" class="map-settings-panel hidden">
      <div class="map-settings-header">
        <span>Map Settings</span>
        <button id="closeMapSettingsBtn" class="map-settings-close">✕</button>
      </div>
      <div class="map-settings-body">
        <div class="map-settings-row">
          <label for="mapTileLayerExplore">Map Style</label>
          <select id="mapTileLayerExplore" class="map-settings-select">
            <option value="osm">OpenStreetMap</option>
            <option value="osm-topo">OpenTopoMap</option>
            <option value="esri-world">ESRI World Imagery</option>
            <option value="esri-topo">ESRI World Topo</option>
            <option value="carto-light">CartoDB Positron</option>
            <option value="carto-dark">CartoDB Dark Matter</option>
            <option value="stamen-terrain">Stamen Terrain</option>
          </select>
        </div>
        <div class="map-settings-row">
          <label><input type="checkbox" id="showTerrainOverlayExplore"> Terrain Overlay</label>
        </div>
        <div class="map-settings-row" id="terrainOpacityRowExplore" style="display:none;">
          <label>Opacity</label>
          <input type="range" id="terrainOverlayOpacityExplore" min="0" max="100" value="70" style="flex:1;">
          <span id="terrainOpacityValueExplore" style="font-size:10px;color:#aaa;min-width:28px;text-align:right;">70%</span>
        </div>
        <div class="map-settings-row">
          <label><input type="checkbox" id="showGridlinesExplore"> Grid Lines</label>
        </div>
        <div class="map-settings-row">
          <label><input type="checkbox" id="showLabelsExplore"> Map Labels</label>
        </div>
      </div>
    </div>

    <!-- "Create Region" button -->
    <button id="floatingDrawBtn" class="map-draw-region-btn" title="Draw a new region on the map">+ New Region</button>

    <!-- Regions Panel (hideable right panel) -->
    <div id="regionsPanel" class="regions-panel hidden">
      <div class="regions-panel-header">
        <span class="regions-panel-title">Regions</span>
        <button id="closeRegionsPanel" class="regions-panel-close">✕</button>
      </div>
      <div class="regions-panel-toolbar">
        <input type="text" id="regionsPanelSearch" placeholder="Search regions..." class="regions-panel-search">
        <button id="regionsPanelNewBtn" class="regions-panel-new-btn" title="Draw a new region on the map">+ New</button>
      </div>
      <div id="regionsPanelList" class="regions-panel-list"></div>
    </div>
  </div>
</template>
<script setup lang="ts">
// No local state — Leaflet initialises by reading #map after DOMContentLoaded
</script>

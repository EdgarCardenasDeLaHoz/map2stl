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
          <button id="refreshRegionsBtn" class="btn btn-secondary" title="Refresh list">🔄</button>          <button id="viewportFilterBtn" class="btn btn-secondary" title="Show only regions visible on map">🗺 In View</button>        </div>
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
              <th>Tags</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="regionsTableBody">
            <!-- Populated by JavaScript -->
          </tbody>
        </table>
        <div id="regionsEmptyState" class="regions-empty-state" style="display:none;text-align:center;color:#888;padding:24px;">
          <span style="font-size:32px;">📭</span>
          <div>No regions found.<br>Add a region using the map or import a region file.</div>
        </div>
        <div id="regionsPagination" class="regions-pagination"></div>
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

    <!-- Cache inventory view -->
    <div id="cacheInventoryContainer" class="cache-inventory-container hidden">
      <div class="cache-inventory-header">
        <div>
          <h3>Cache Inventory</h3>
          <p id="cacheInventorySummary" class="cache-inventory-summary">Loading cache inventory...</p>
          <p id="cacheInventoryLegend" class="cache-inventory-legend"></p>
        </div>
        <div class="cache-inventory-toolbar">
          <label class="cache-filter-label" for="cacheInventoryRegionFilter">Region</label>
          <select id="cacheInventoryRegionFilter" class="cache-filter-select">
            <option value="__all__">All Regions</option>
          </select>
          <button id="cacheInventoryRefreshBtn" class="btn btn-secondary">Refresh</button>
          <button id="preloadRegionsBtn" class="btn btn-secondary" title="Preload all regions into cache">⚡ Preload All</button>
          <button id="clearClientCacheBtn" class="btn btn-secondary" title="Clear in-memory client cache">🧹 Clear Client</button>
          <button id="clearServerCacheBtn" class="btn btn-secondary" title="Clear server-side cache files">🗑️ Clear Server</button>
          <button id="genGlobalDemBtn" class="btn btn-secondary" title="Pre-generate a low-resolution global terrain overview PNG cached on disk.">🗺️ Build Terrain</button>
          <span id="genGlobalDemStatus" style="font-size:10px;color:#888;"></span>
        </div>
      </div>

      <div class="cache-inventory-layout">
        <section class="cache-card">
          <h4>Treemap</h4>
          <div id="cacheTreemap" class="cache-treemap"></div>
          <div id="cacheTreemapEmpty" class="cache-empty-state" style="display:none;text-align:center;color:#888;padding:18px;">
            <span style="font-size:28px;">🗂️</span>
            <div>No cache data available.<br>Load a region to populate the cache.</div>
          </div>
        </section>

        <section class="cache-card">
          <h4>Files</h4>
          <div class="cache-table-wrap">
            <table class="cache-table" id="cacheInventoryTable">
              <thead>
                <tr>
                  <th><button type="button" class="cache-sort-btn" data-sort-key="region_group">Region</button></th>
                  <th><button type="button" class="cache-sort-btn" data-sort-key="namespace">Layer</button></th>
                  <th><button type="button" class="cache-sort-btn" data-sort-key="root">Root</button></th>
                  <th><button type="button" class="cache-sort-btn" data-sort-key="relative_path">Path</button></th>
                  <th><button type="button" class="cache-sort-btn" data-sort-key="size_bytes">Size</button></th>
                  <th><button type="button" class="cache-sort-btn" data-sort-key="mtime">Modified</button></th>
                </tr>
              </thead>
              <tbody id="cacheInventoryTableBody"></tbody>
            </table>
            <div id="cacheTableEmptyState" class="cache-empty-state" style="display:none;text-align:center;color:#888;padding:18px;">
              <span style="font-size:28px;">📦</span>
              <div>No cached files found.<br>Export or load data to see files here.</div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import MapContainer   from './MapContainer.vue';
import DemContainer   from './DemContainer.vue';
import ModelContainer from './ModelContainer.vue';
</script>

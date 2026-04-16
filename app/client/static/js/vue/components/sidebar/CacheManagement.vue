<template>
  <div class="control-group">
    <CollapsibleSection title="💾 Cache Management" wrap-style="">
      <div class="cache-status" id="cacheStatus">
        <div class="cache-stat">
          <span class="cache-stat-label">Memory Cache:</span>
          <span class="cache-stat-value" id="memoryCacheCount">0 items</span>
        </div>
        <div class="cache-stat">
          <span class="cache-stat-label">Server Cache:</span>
          <span class="cache-stat-value" id="serverCacheCount">0 files</span>
        </div>
        <div class="cache-stat">
          <span class="cache-stat-label">Hit Rate:</span>
          <span class="cache-stat-value" id="cacheHitRate">0%</span>
        </div>
        <div class="cache-stat">
          <span class="cache-stat-label">Preloaded:</span>
          <span class="cache-stat-value" id="preloadedCount">0 regions</span>
        </div>
      </div>
      <div class="cache-actions">
        <button id="preloadRegionsBtn" class="btn btn-secondary"
                style="margin-bottom:8px;width:100%;">
          <span class="btn-icon">⚡</span> Preload All Regions
        </button>
        <button id="clearClientCacheBtn" class="btn btn-secondary"
                style="margin-bottom:8px;width:100%;">
          <span class="btn-icon">🧹</span> Clear Client Cache
        </button>
        <button id="clearServerCacheBtn" class="btn btn-secondary" style="width:100%;">
          <span class="btn-icon">🗑️</span> Clear Server Cache
        </button>
        <div style="height:1px;background:#333;margin:4px 0;"></div>
        <div class="row-gap6">
          <button id="genGlobalDemBtn" class="btn btn-secondary" style="flex:1;font-size:11px;"
                  title="Pre-generate a low-resolution global terrain overview PNG cached on disk.">
            <span class="btn-icon">🗺️</span> Build Terrain Cache
          </button>
          <span id="genGlobalDemStatus" style="font-size:10px;color:#888;"></span>
        </div>
      </div>
      <div id="preloadProgress" class="preload-progress hidden">
        <div class="progress-bar">
          <div class="progress-fill" id="preloadFill"></div>
        </div>
        <span class="progress-text" id="preloadText">0 / 0</span>
      </div>
    </CollapsibleSection>
  </div>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

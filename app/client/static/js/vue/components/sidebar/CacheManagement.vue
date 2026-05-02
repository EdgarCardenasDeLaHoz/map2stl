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
        <button id="openCacheInventoryBtn" class="btn btn-secondary"
                style="width:100%;">
          <span class="btn-icon">🗂️</span> View Cache
        </button>
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

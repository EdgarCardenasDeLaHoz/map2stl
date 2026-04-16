<template>
  <CollapsibleSection title="🌍 Projection">

    <!-- projection.projection -->
    <div class="param-group">
      <label for="paramProjection" title="Server-side projection applied to all layers before returning.">Projection:</label>
      <select id="paramProjection" class="ctrl-select" style="font-size:11px;">
        <option value="none">None (Plate Carrée)</option>
        <option value="cosine" selected>Cosine Correction</option>
        <option value="mercator">Web Mercator</option>
        <option value="lambert">Lambert Equal-Area</option>
        <option value="sinusoidal">Sinusoidal</option>
      </select>
      <div id="projectionDescription" style="font-size:10px;color:#888;margin-top:4px;">
        Horizontal scaling by cos(latitude). Corrects east-west distances.
      </div>
    </div>

    <!-- projection.maintain_dimensions -->
    <div class="param-group" style="margin-top:6px;">
      <label class="check-label" title="Pad the projected output to dim×dim instead of cropping to valid extent.">
        <input type="checkbox" id="paramMaintainDimensions"> Maintain Dimensions
      </label>
      <div style="font-size:10px;color:#666;margin-top:2px;margin-left:18px;">
        Pad to dim×dim; unchecked crops to projected extent.
      </div>
    </div>

    <!-- frontend-only: auto-reload -->
    <div class="param-group" style="margin-top:6px;padding-top:6px;border-top:1px solid #333;">
      <label class="check-label" title="Automatically reload all layers when bounding box or region changes.">
        <input type="checkbox" id="autoReloadLayers" checked> Auto-reload layers
      </label>
    </div>

    <!-- Hidden map tile controls synced from Explore tab -->
    <select id="mapTileLayer" style="display:none;"></select>
    <input  type="checkbox" id="showTerrainOverlay" style="display:none;">
    <label  id="terrainOpacityLabel" style="display:none;"></label>
    <div    id="terrainOpacityGroup" style="display:none;">
      <input type="range" id="terrainOverlayOpacity" min="0" max="100" value="50">
      <span  id="terrainOpacityValue"></span>
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

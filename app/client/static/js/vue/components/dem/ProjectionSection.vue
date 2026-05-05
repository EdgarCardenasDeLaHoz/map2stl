<template>
  <CollapsibleSection title="🌍 Projection">

    <!-- Projection + Clip NaNs on one compact row -->
    <div style="display:grid;grid-template-columns:1fr auto;gap:6px;align-items:center;">
      <select id="paramProjection" class="ctrl-select" style="font-size:11px;" aria-label="Projection mode"
              title="Server-side projection applied to all layers before returning.">
        <option value="none">None (Plate Carrée)</option>
        <option value="cosine" selected>Cosine Correction</option>
        <option value="mercator">Web Mercator</option>
        <option value="lambert">Lambert Equal-Area</option>
        <option value="sinusoidal">Sinusoidal</option>
      </select>
      <label class="check-label" style="font-size:11px;white-space:nowrap;"
             title="Strip all-NaN border rows/columns introduced by projection warping.">
        <input type="checkbox" id="paramClipNans" checked aria-label="Clip projection NaN edges"> Clip edges
      </label>
    </div>
    <div id="projectionDescription" style="font-size:10px;color:#666;margin-top:3px;">
      Horizontal scaling by cos(latitude). Corrects east-west distances.
    </div>

    <!-- Auto-reload -->
    <div style="margin-top:6px;">
      <label class="check-label" style="font-size:11px;color:#aaa;"
             title="Automatically reload all layers when bounding box or region changes.">
        <input type="checkbox" id="autoReloadLayers" checked aria-label="Auto reload layers on bbox change"> Auto-reload on bbox change
      </label>
    </div>

    <!-- Hidden map tile controls synced from Explore tab -->
    <select id="mapTileLayer" style="display:none;" aria-label="Map tile layer for edit tab"></select>
    <input  type="checkbox" id="showTerrainOverlay" style="display:none;" aria-label="Show terrain overlay (edit tab)">
    <label  id="terrainOpacityLabel" style="display:none;"></label>
    <div    id="terrainOpacityGroup" style="display:none;">
      <input type="range" id="terrainOverlayOpacity" min="0" max="100" value="50" aria-label="Terrain overlay opacity (edit tab)">
      <span  id="terrainOpacityValue"></span>
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

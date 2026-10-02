<template>
  <CollapsibleSection title="🌍 Projection">

    <!-- Projection + Clip NaNs on one compact row -->
    <div style="display:grid;grid-template-columns:1fr auto;gap:6px;align-items:center;">
      <select id="paramProjection" class="ctrl-select" style="font-size:11px;" aria-label="Projection mode"
              title="Server-side projection applied to all layers before returning.">
        <option value="none">None (Plate Carrée)</option>
        <optgroup label="Cylindrical">
          <option value="cosine" selected>Cosine Correction</option>
          <option value="mercator">Web Mercator (conformal)</option>
          <option value="equidistant">Equidistant Cylindrical</option>
          <option value="lambert">Lambert Equal-Area</option>
          <option value="miller">Miller Cylindrical</option>
          <option value="gall">Gall Stereographic</option>
        </optgroup>
        <optgroup label="Pseudocylindrical">
          <option value="sinusoidal">Sinusoidal (equal-area)</option>
        </optgroup>
      </select>
      <label class="check-label" style="font-size:11px;white-space:nowrap;"
             title="Strip all-NaN border rows/columns introduced by projection warping.">
        <input type="checkbox" id="paramClipNans" checked aria-label="Clip projection NaN edges"> Clip edges
      </label>
    </div>
    <div id="projectionDescription" style="font-size:11px;color:var(--text-dim);margin-top:3px;">
      Horizontal scaling by cos(latitude). Corrects east-west distances.
    </div>

    <label class="check-label" style="font-size:11px;color:#aaa;margin-top:4px;"
           title="Off (default): output shape reflects the projection's true geographic aspect ratio — switching projections visibly changes the canvas shape. On: output always keeps the DEM's current pixel dimensions (legacy behavior), only the content within that fixed grid warps.">
      <input type="checkbox" id="paramMaintainDimensions" aria-label="Maintain fixed output dimensions across projections"> Keep fixed canvas shape across projections
    </label>
    <div id="noneProjectionNote" style="font-size:11px;color:var(--text-dim);margin-top:3px;">
      Note: "None" is equal-angle (equal degrees lat/lon per pixel), not equal-distance — pixels are only square on the ground near the equator. This is standard Plate Carrée convention, not a bug.
    </div>

    <!-- Auto-reload -->
    <div style="margin-top:6px;">
      <label class="check-label" style="font-size:11px;color:#aaa;"
             title="Automatically reload all layers when bounding box or region changes.">
        <input type="checkbox" id="autoReloadLayers" checked aria-label="Auto reload layers on bbox change"> Auto-reload on bbox change
      </label>
    </div>

    <!-- Map tile / terrain overlay mirrors: View → Canvas (VisualizationSection.vue). -->

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

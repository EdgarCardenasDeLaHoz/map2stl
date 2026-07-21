<template>
  <!-- Mesh registration picker — shown/hidden by JS via classList (modules/layers/mesh-registration.js) -->
  <div id="meshRegistrationModal" class="mesh-reg-modal hidden">
    <div class="mesh-reg-dialog">
      <div class="mesh-reg-header">
        <h3>🎯 Register Mesh Heightmap</h3>
        <span id="meshRegResidualLabel" class="mesh-reg-residual"></span>
        <button id="meshRegCloseBtn" class="mesh-reg-close-btn" title="Close" aria-label="Close registration picker">×</button>
      </div>
      <div class="mesh-reg-hint">
        Click a point on the DEM (left), then the matching point on the mesh heightmap (right). Repeat for at least 3 pairs. Scroll to zoom, drag to pan, double-click to reset a side.
      </div>
      <div class="mesh-reg-panels">
        <div class="mesh-reg-panel">
          <div class="mesh-reg-panel-title">DEM / Reference</div>
          <div id="meshRegRefWrap" class="mesh-reg-canvas-wrap">
            <canvas id="meshRegRefCanvas" class="mesh-reg-canvas"></canvas>
          </div>
        </div>
        <div class="mesh-reg-panel">
          <div class="mesh-reg-panel-title">Imported Mesh Heightmap</div>
          <div id="meshRegMeshWrap" class="mesh-reg-canvas-wrap">
            <canvas id="meshRegMeshCanvas" class="mesh-reg-canvas"></canvas>
          </div>
        </div>
      </div>
      <div id="meshRegPairList" class="mesh-reg-pair-list"></div>
      <div class="mesh-reg-actions">
        <button id="meshRegUndoBtn" class="btn btn-secondary" title="Undo the last point/pair">↩ Undo</button>
        <button id="meshRegClearBtn" class="btn btn-secondary" title="Clear all pairs">🗑 Clear</button>
        <div style="flex:1"></div>
        <button id="meshRegComputeBtn" class="btn btn-primary" disabled title="Fit and apply the registration">✓ Compute Registration</button>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
// Modal open/close/interaction driven by modules/layers/mesh-registration.js
</script>

<template>
  <CollapsibleSection title="🌊 Hydrology">

    <!-- source -->
    <div class="param-group">
      <label for="hydroSource" title="Data source for river network.">Source:</label>
      <select id="hydroSource" class="ctrl-select">
        <option value="natural_earth">Natural Earth (global, coarse)</option>
        <option value="hydrorivers" selected>HydroRIVERS (~500 m detail)</option>
      </select>
    </div>

    <!-- dim -->
    <div class="param-group" style="margin-top:6px;">
      <label for="hydroDim" title="Output grid resolution in pixels per side.">Resolution (px):</label>
      <input type="number" id="hydroDim" class="ctrl-input" value="300" min="50" max="2000" step="50">
    </div>

    <!-- depression_m -->
    <div class="param-group" style="margin-top:6px;">
      <label for="hydroDepressionM" title="Maximum river depression depth in metres (negative).">Depression (m):</label>
      <input type="number" id="hydroDepressionM" class="ctrl-input" value="-5.0" min="-100" max="0" step="0.5">
    </div>

    <!-- HydroRIVERS-only controls — shown/hidden by hydrology-overlay.js -->
    <div id="hydroRiversControls">
      <div class="param-group" style="margin-top:6px;">
        <label for="hydroMinOrder" title="Minimum Strahler order to include (1=all streams, 9=Amazon/Congo only).">Min order:</label>
        <input type="number" id="hydroMinOrder" class="ctrl-input" value="3" min="1" max="9" step="1">
      </div>
      <div class="param-group" style="margin-top:6px;">
        <label for="hydroOrderExponent" title="Exponent controlling how steeply smaller rivers are cut. Higher = shallower small streams.">Order exponent:</label>
        <input type="number" id="hydroOrderExponent" class="ctrl-input" value="1.5" min="0.5" max="3.0" step="0.1">
      </div>
    </div>

    <!-- buttons -->
    <div class="row-gap6" style="margin-top:8px;">
      <button id="loadHydrologyBtn"  class="btn btn-secondary" style="flex:1;font-size:11px;" title="Fetch river network for current bbox">🌊 Load</button>
      <button id="clearHydrologyBtn" class="btn btn-secondary" style="font-size:11px;"        title="Clear hydrology layer">✕ Clear</button>
    </div>
    <div id="hydroStatus" style="font-size:10px;color:#888;margin-top:4px;min-height:14px;"></div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

<template>
  <CollapsibleSection title="🌿 ESA Land Cover">

    <!-- esa.sat_scale — independent resolution for ESA land cover -->
    <div class="param-group">
      <label for="esaResolution" title="Earth Engine fetch resolution in metres/pixel for ESA WorldCover.">Resolution:</label>
      <select id="esaResolution" class="ctrl-select">
        <option value="10">10 m/px — ESA Native (city)</option>
        <option value="30">30 m/px — High Detail</option>
        <option value="100">100 m/px — Medium</option>
        <option value="200" selected>200 m/px — Default</option>
        <option value="500">500 m/px — Fast</option>
        <option value="1000">1000 m/px — Very Fast</option>
      </select>
    </div>

    <button id="loadEsaBtn" class="btn btn-secondary" style="width:100%;font-size:11px;padding:4px 0;margin-top:8px;">🌿 Load ESA Land Cover</button>

    <!-- Land cover legend + colour editor -->
    <div style="margin-top:8px;padding-top:6px;border-top:1px solid #333;">
      <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none;"
           onclick="const c=this.nextElementSibling; c.classList.toggle('hidden'); this.querySelector('.lc-collapse-icon').textContent = c.classList.contains('hidden') ? '\u25B6' : '\u25BC';">
        <div style="font-size:11px;color:#888;">Land Cover Classes</div>
        <span class="lc-collapse-icon" style="font-size:9px;color:#666;">&#x25BC;</span>
      </div>
      <div>
        <!-- water-mask.js writes to #landCoverLegend via innerHTML -->
        <div id="landCoverLegend" style="overflow-y:auto;max-height:300px;"></div>
        <div class="row-gap6" style="margin-top:6px;">
          <button id="applyLandCoverMapping" class="btn btn-secondary" style="flex:1;padding:5px;font-size:11px;">Apply Colors</button>
          <button id="resetLandCoverMapping" class="btn btn-secondary" style="flex:1;padding:5px;font-size:11px;">Reset</button>
        </div>
      </div>
    </div>

  </CollapsibleSection>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';
</script>

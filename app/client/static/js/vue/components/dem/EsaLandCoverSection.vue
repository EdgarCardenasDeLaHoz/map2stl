<template>
  <CollapsibleSection title="🌿 ESA Land Cover">

    <!-- esa.dim — independent resolution for ESA land cover -->
    <div class="param-group">
      <label for="esaResolution" title="Output resolution in pixels per side for ESA WorldCover. The backend computes the Earth Engine scale from your bbox and this value.">Resolution (px):</label>
      <select id="esaResolution" class="ctrl-select">
        <option value="100">100 px — Very Fast</option>
        <option value="200">200 px — Fast</option>
        <option value="400">400 px — Medium</option>
        <option value="600" selected>600 px — Default</option>
        <option value="1200">1200 px — High Detail</option>
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

<template>
  <div id="regionParamsSection" class="control-group" :class="{ hidden: !visible }">
    <CollapsibleSection title="⚙️ Region Parameters" :start-open="true" wrap-style="">
      <table class="region-params-table">
        <thead>
          <tr><th>Parameter</th><th>Value</th></tr>
        </thead>
        <!-- region-ui.js still populates #regionParamsBody via innerHTML -->
        <tbody id="regionParamsBody">
          <tr><td colspan="2" style="color:#888;text-align:center;">Select a region</td></tr>
        </tbody>
      </table>
      <div class="buttons" style="margin-top:10px;">
        <button id="applyParamsBtn" class="btn btn-primary" style="width:100%;"
                @click="applyParams">
          <span class="btn-icon">✓</span> Apply Changes
        </button>
      </div>
    </CollapsibleSection>
  </div>
</template>
<script setup lang="ts">
import CollapsibleSection from '../shared/CollapsibleSection.vue';

defineProps<{ visible: boolean }>();

function applyParams() {
  // Delegate to existing applyParamsBtn click handler (event-listeners.js)
  const btn = document.getElementById('applyParamsBtn');
  btn?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}
</script>

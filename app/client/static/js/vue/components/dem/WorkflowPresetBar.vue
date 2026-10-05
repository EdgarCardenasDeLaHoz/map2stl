<template>
  <!-- One-click City / Mountain / Coast set-ups. The definitions and the apply
       logic live in modules/ui/workflow-presets.js; presets.js owns the revert
       snapshot, so the click goes through window.applyWorkflowPreset. -->
  <div class="workflow-preset-bar" id="workflowPresetBar">
    <span class="workflow-preset-label">Preset</span>
    <button v-for="p in presets" :key="p.name" type="button"
            class="btn btn-secondary workflow-preset-btn"
            :id="'workflowPreset_' + p.name"
            :title="p.title"
            @click="apply(p.name)">{{ p.label }}</button>
  </div>
</template>
<script setup lang="ts">
import { WORKFLOW_PRESETS } from '../../../modules/ui/workflow-presets.js';

const presets = Object.entries(WORKFLOW_PRESETS).map(([name, p]) => ({ name, label: p.label, title: p.title }));

function apply(name: string) {
  (window as any).applyWorkflowPreset?.(name);
}
</script>

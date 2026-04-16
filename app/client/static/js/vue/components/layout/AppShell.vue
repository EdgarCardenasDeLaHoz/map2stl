<template>
  <div style="display:contents">
    <!-- Toast container stays in DOM — written by window.showToast() in ui-helpers.js -->
    <Teleport to="#vue-toast">
      <div id="toastContainer" class="toast-container"></div>
    </Teleport>
    <Teleport to="#vue-notes-modal">
      <RegionNotesModal />
    </Teleport>
    <Teleport to="#vue-main-header">
      <MainHeader />
    </Teleport>
    <Teleport to="#vue-workflow-hint">
      <WorkflowHint />
    </Teleport>
  </div>
</template>
<script setup lang="ts">
import { Teleport }        from 'vue';
import MainHeader          from './MainHeader.vue';
import WorkflowHint        from './WorkflowHint.vue';
import RegionNotesModal    from './RegionNotesModal.vue';
</script>

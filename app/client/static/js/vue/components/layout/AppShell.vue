<template>
  <div style="display:contents">
    <!-- Toast container stays in DOM — written by window.showToast() in ui-helpers.js -->
    <Teleport to="#vue-toast">
      <!-- aria-live: each toast also carries role=status (info/success/warning) or role=alert (error) -->
      <div id="toastContainer" class="toast-container" aria-live="polite" aria-relevant="additions"></div>
    </Teleport>
    <Teleport to="#vue-mesh-registration-modal">
      <MeshRegistrationModal />
    </Teleport>
    <Teleport to="#vue-main-header">
      <MainHeader />
    </Teleport>
  </div>
</template>
<script setup lang="ts">
import MainHeader          from './MainHeader.vue';
import MeshRegistrationModal from './MeshRegistrationModal.vue';
</script>

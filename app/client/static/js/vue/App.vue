<template>
  <div style="display:contents">
    <!-- Stage 3: Sidebar — teleported into #vue-sidebar placeholder in index.html -->
    <Teleport to="#vue-sidebar">
      <SidebarPanel />
    </Teleport>
    <!-- Stage 5: Layout shell (header, tabs, workflow hint, toast, notes modal) -->
    <AppShell />
    <!-- Stage 6: Content area (map, globe, dem, model containers) -->
    <Teleport to="#vue-content-area">
      <ContentArea />
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { Teleport } from 'vue';
import SidebarPanel from './components/sidebar/SidebarPanel.vue';
import AppShell     from './components/layout/AppShell.vue';
import ContentArea  from './components/views/ContentArea.vue';
</script>

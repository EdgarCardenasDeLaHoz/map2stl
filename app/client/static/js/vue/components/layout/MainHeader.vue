<template>
  <div class="main-header">
    <div class="main-title">3D Maps Globe &amp; Map Selector</div>
    <div class="tabs">
      <!-- data-view attributes must stay — window.switchView() reads them via querySelector -->
      <button class="tab active" data-view="map" id="tabExplore">
        <span class="tab-step" id="tabStep1">1</span> Explore
      </button>
      <span class="tab-arrow">›</span>
      <button class="tab" data-view="dem" id="tabEdit">
        <span class="tab-step" id="tabStep2">2</span> Edit
      </button>
      <span class="tab-arrow">›</span>
      <button class="tab" data-view="model" id="tabExtrude">
        <span class="tab-step" id="tabStep3">3</span> Extrude
      </button>
    </div>
    <div class="docs-menu">
      <button class="btn btn-secondary docs-menu-btn" id="docsMenuBtn"
              @click="docsOpen = !docsOpen">
        📖 Docs
      </button>
      <div v-if="docsOpen" class="docs-dropdown">
        <a v-for="link in docsLinks" :key="link.href"
           :href="link.href" target="_blank" rel="noopener"
           class="docs-dropdown-link">
          {{ link.label }}
        </a>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue';

const docsOpen = ref(false);
const docsLinks = [
  { label: '📋 Swagger UI',          href: '/docs' },
  { label: '📘 ReDoc',               href: '/redoc' },
  { label: '📚 Project Docs',        href: '/project-docs/' },
  { label: '🐍 Python API Reference', href: '/api-reference/' },
];

// Close dropdown when clicking outside
if (typeof document !== 'undefined') {
  document.addEventListener('click', (e: Event) => {
    const btn = document.getElementById('docsMenuBtn');
    if (btn && !btn.contains(e.target as Node)) {
      docsOpen.value = false;
    }
  });
}
</script>

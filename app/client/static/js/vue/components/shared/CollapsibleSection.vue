<template>
  <div class="collapsible-section" :class="{ collapsed: !open }" :style="wrapStyle" :id="id || undefined">
    <!-- @click.stop prevents the global toggleCollapsible delegation in event-listeners.js
         from also firing — CollapsibleSection owns its own open state via Vue -->
    <div class="collapsible-header" @click.stop="open = !open" :title="headerTitle || undefined">
      <h4>{{ title }}</h4>
      <span class="collapsible-icon">{{ open ? '▲' : '▼' }}</span>
    </div>
    <!-- v-show and :class="{ collapsed }" are kept in sync; the CSS
         .collapsible-section.collapsed .collapsible-content { display:none }
         rule is redundant here but harmless -->
    <div class="collapsible-content" v-show="open">
      <slot />
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue';

const props = withDefaults(defineProps<{
  title: string;
  startOpen?: boolean;
  wrapStyle?: string;
  id?: string;
  headerTitle?: string;
}>(), {
  startOpen: false,
  wrapStyle: 'margin-top:10px;',
});

const open = ref(props.startOpen);
</script>

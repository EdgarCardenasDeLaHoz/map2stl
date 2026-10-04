<template>
  <!-- With `sub`, the section is also an Edit-panel sub-page (F-EDITPANEL): while that sub-page
       is open the Teleport moves it, open and without its header, into the panel; otherwise it
       stays where it is mounted. Moving keeps the component (and canvases) alive. -->
  <Teleport defer to="#lsSubBody" :disabled="!asSub">
    <div class="collapsible-section" :class="{ collapsed: !isOpen, 'as-sub': asSub }" :style="asSub ? '' : wrapStyle" :id="id || undefined">
      <!-- @click.stop prevents the global toggleCollapsible delegation in event-listeners.js
           from also firing — CollapsibleSection owns its own open state via Vue -->
      <div v-show="!asSub" class="collapsible-header" @click.stop="open = !open" :title="headerTitle || undefined">
        <h4>{{ title }}</h4>
        <span class="collapsible-icon">{{ open ? '▲' : '▼' }}</span>
      </div>
      <div class="collapsible-content" v-show="isOpen">
        <slot />
      </div>
    </div>
  </Teleport>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue';
import { useEditPanelStore } from '../../stores/editPanel';

const props = withDefaults(defineProps<{
  title: string;
  startOpen?: boolean;
  wrapStyle?: string;
  id?: string;
  headerTitle?: string;
  /** Edit-panel sub-page this section is shown on (stores/editPanel.ts). */
  sub?: string;
}>(), {
  startOpen: false,
  wrapStyle: 'margin-top:10px;',
  sub: '',
});

const open = ref(props.startOpen);
const panel = useEditPanelStore();
const asSub = computed(() => !!props.sub && panel.sub === props.sub);
const isOpen = computed(() => asSub.value || open.value);
</script>

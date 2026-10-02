<template>
  <!-- Real samples of the DEM source across the box vs the grid returned
       (source_resolution of the last /api/terrain/dem response). -->
  <div v-if="info" id="demSamplingInfo" class="dem-sampling" :class="{ warn: info.warn }"
       :title="info.warning || 'Native resolution of the DEM source, and how many real samples span this box'">
    <span>{{ info.warn ? '⚠️ ' : '' }}{{ info.text }}</span>
    <div v-if="info.warn" class="dem-sampling-warning">{{ info.warning }}</div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import { useAppStore } from '../../stores/app';
import { describeDemSampling } from '../../../modules/dem/dem-sampling.js';

const store = useAppStore();
const info = computed(() => describeDemSampling(store.demSampling as any));
</script>
<style scoped>
.dem-sampling {
  font-size: 11px;
  color: #9ab;
  margin-top: 2px;
  line-height: 1.35;
}
.dem-sampling.warn { color: #f90; }
.dem-sampling-warning { color: #c96; margin-top: 1px; }
</style>

<template>
  <div id="sidebarEditView" :class="{ hidden: !visible }">
    <div class="row-gap6" style="padding:6px 8px;border-bottom:1px solid #333;">
      <button id="sbBackBtn"
              style="padding:2px 8px;font-size:11px;background:#333;border:1px solid #555;color:#ccc;border-radius:3px;cursor:pointer;flex-shrink:0;"
              @click="$emit('back')">← Back</button>
      <span id="sbRegionName"
            style="font-size:12px;font-weight:bold;color:#ddd;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;">
        {{ regionName }}
      </span>
    </div>
    <div style="padding:8px;">
      <div style="margin-bottom:8px;">
        <label for="regionLabelEdit" style="font-size:10px;color:#888;display:block;">Label</label>
        <div style="display:flex;gap:4px;align-items:center;">
          <input type="text" id="regionLabelEdit" list="regionLabelsList"
                 aria-label="Region label"
                 placeholder="e.g. City, Europe"
                 style="flex:1;min-width:0;font-size:11px;padding:3px 4px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;box-sizing:border-box;">
          <button id="saveRegionLabelBtn"
                  style="padding:4px 8px;font-size:11px;background:#24462a;border:1px solid #3f7a4a;color:#d6f5dc;border-radius:3px;cursor:pointer;white-space:nowrap;"
                  title="Save region label">Save Label</button>
        </div>
        <datalist id="regionLabelsList"></datalist>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:6px;">
        <div v-for="dir in dirs" :key="dir.id">
          <label style="font-size:10px;color:#888;display:block;">{{ dir.label }}</label>
          <input type="number" :id="dir.id" :aria-label="dir.label + ' boundary'" step="0.01"
                 style="width:100%;font-size:11px;padding:3px 4px;background:#1a1a1a;border:1px solid #444;color:#ccc;border-radius:3px;box-sizing:border-box;">
        </div>
      </div>
      <button id="sbReloadBtn"
              style="width:100%;padding:6px;background:#1a3a5c;border:1px solid #2a6aa8;color:#aad;border-radius:3px;cursor:pointer;font-size:12px;"
              >↺ Reload Layers</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useAppStore } from '../../stores/app';

defineProps<{ visible: boolean }>();
defineEmits<{ (e: 'back'): void }>();

const store = useAppStore();
const regionName = computed(() => store.selectedRegion?.name ?? '');

const dirs = [
  { id: 'sbNorth', label: 'North' },
  { id: 'sbSouth', label: 'South' },
  { id: 'sbEast',  label: 'East'  },
  { id: 'sbWest',  label: 'West'  },
];
</script>

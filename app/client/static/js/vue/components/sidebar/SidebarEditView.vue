<template>
  <!-- Region editor. Markup only: modules/regions/region-editor.js fills the
       fields, reacts to them (live size readout, map box) and saves through
       PUT /api/regions/{name} (which also renames). Opened by a list row's ✎
       button through SidebarPanel.vue::setRegionEditorOpen. -->
  <section id="sidebarEditView" class="region-editor" :class="{ hidden: !visible }"
           aria-labelledby="sbEditTitle">
    <div class="region-editor-header">
      <button id="sbBackBtn" type="button" class="region-editor-back"
              aria-label="Back to region list" title="Back to region list"
              @click="$emit('back')">← Back</button>
      <h2 id="sbEditTitle" class="region-editor-title">
        Edit <span id="sbRegionName">{{ regionName }}</span>
      </h2>
    </div>

    <form id="regionEditorForm" class="region-editor-body" novalidate @submit.prevent>
      <label for="regionNameEdit" class="region-editor-label">Name</label>
      <input id="regionNameEdit" type="text" class="region-editor-input"
             maxlength="128" autocomplete="off" required>

      <label for="regionLabelEdit" class="region-editor-label">Group</label>
      <input id="regionLabelEdit" type="text" class="region-editor-input" list="regionLabelsList"
             maxlength="64" autocomplete="off" placeholder="e.g. City, Europe">
      <datalist id="regionLabelsList"></datalist>

      <fieldset class="region-editor-bounds">
        <legend class="region-editor-label">Bounds (degrees)</legend>
        <div v-for="dir in dirs" :key="dir.id" class="region-editor-bound">
          <label :for="dir.id" class="region-editor-label">{{ dir.label }}</label>
          <input :id="dir.id" type="number" class="region-editor-input" step="0.001"
                 :min="dir.min" :max="dir.max">
        </div>
      </fieldset>
      <p id="sbSizeReadout" class="region-editor-size" aria-live="polite"></p>

      <div class="region-editor-actions">
        <button id="sbSaveBtn" type="submit" class="region-editor-save">Save</button>
        <button id="sbDeleteBtn" type="button" class="region-editor-delete">Delete</button>
      </div>

      <details id="sbNotesSection" class="region-editor-notes">
        <summary>Notes</summary>
        <textarea id="sbNotesTextarea" class="region-editor-input" rows="4"
                  aria-label="Region notes"
                  placeholder="Notes about this region (saved in this browser)"></textarea>
      </details>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useAppStore } from '../../stores/app';

defineProps<{ visible: boolean }>();
defineEmits<{ (e: 'back'): void }>();

const store = useAppStore();
const regionName = computed(() => store.selectedRegion?.name ?? '');

const dirs = [
  { id: 'sbNorth', label: 'North', min: -90, max: 90 },
  { id: 'sbSouth', label: 'South', min: -90, max: 90 },
  { id: 'sbEast',  label: 'East',  min: -180, max: 180 },
  { id: 'sbWest',  label: 'West',  min: -180, max: 180 },
];
</script>

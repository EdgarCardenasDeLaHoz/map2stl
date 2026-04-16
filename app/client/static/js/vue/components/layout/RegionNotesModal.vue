<template>
  <!-- Region Notes Modal — shown/hidden by JS via classList -->
  <div id="regionNotesModal" class="region-notes-modal hidden">
    <div class="region-notes-dialog">
      <h3>📝 Notes for: <span id="notesRegionName">--</span></h3>
      <textarea id="notesTextarea" placeholder="Add notes about this region..."></textarea>
      <div class="dialog-buttons">
        <button class="btn btn-secondary" data-action="notes-cancel">Cancel</button>
        <button class="btn btn-primary" data-action="notes-save">Save</button>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
// Modal open/close driven by JS event listeners in event-listeners.js
</script>

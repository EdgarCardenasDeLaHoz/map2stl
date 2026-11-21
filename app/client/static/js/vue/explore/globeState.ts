/**
 * explore/globeState.ts - whether the globe is showing. GlobeView.vue keeps it in step with
 * its container (the Globe pill and view-management.js::switchView('globe') both show it), and
 * the pill in MapContainer.vue reads it to light up.
 */
import { ref } from 'vue';

export const globeOpen = ref(false);

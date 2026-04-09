/**
 * main.js — ES module entry point.
 *
 * Imports all application modules in dependency order.
 * Third-party libs (Leaflet, Three.js, Plotly) remain as plain <script> tags
 * loaded before this module in index.html.
 *
 * Because this is a type="module" script it always defers, so the DOM is
 * fully parsed before any module code runs — DOMContentLoaded fires after.
 */

import './modules/core/events.js';
import './modules/core/api.js';
import './modules/core/cache.js';
import './modules/core/ui-helpers.js';
import './modules/core/state.js';
import './modules/dem/dem-loader.js';
import './modules/dem/dem-gridlines.js';
import './modules/ui/presets.js';
import './modules/ui/curve-editor.js';
import './modules/layers/city-overlay.js';
import './modules/layers/city-render.js';
import './modules/layers/stacked-layers.js';
import './modules/layers/composite-dem.js';
import './modules/export/export-handlers.js';
import './modules/export/model-viewer.js';
import './modules/map/compare-view.js';
import './modules/regions/region-ui.js';
import './modules/dem/dem-merge.js';
import './modules/layers/water-mask.js';
import './modules/map/map-globe.js';
import './modules/regions/regions.js';
import './modules/map/bbox-panel.js';
import './modules/ui/app-setup.js';
import './modules/ui/keyboard-shortcuts.js';
import './modules/events/event-listeners-map.js';
import './modules/events/event-listeners-export.js';
import './modules/events/event-listeners-ui.js';
import './modules/events/event-listeners.js';
import './modules/ui/view-management.js';
import './modules/dem/dem-main.js';
import './app.js';

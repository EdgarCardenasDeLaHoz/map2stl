/**
 * modules/event-listeners.js
 * ==========================
 * Orchestrator: wires all UI event listeners by calling domain modules.
 *
 * Domain modules (loaded before this file):
 *   event-listeners-map.js     — map, DEM, terrain overlays, draw tool, bbox
 *   event-listeners-export.js  — model export, city, puzzle, 3D viewer
 *   event-listeners-ui.js      — resizable panel, JSON toggle, sidebar edit view
 *   keyboard-shortcuts.js      — global keyboard shortcuts
 *
 * Exposes on window:
 *   window.setupEventListeners()
 */

// Named so addEventListener deduplicates if setupEventListeners is called more than once
function _onCollapsibleClick(e) {
    const header = e.target.closest('.collapsible-header');
    if (header) window.toggleCollapsible?.(header);
}

let _listenersWired = false;
window.setupEventListeners = function setupEventListeners() {
    if (_listenersWired) return;
    _listenersWired = true;
    // Tab buttons
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => window.switchView?.(tab.dataset.view));
    });

    // Collapsible section headers — event delegation replaces inline onclick="toggleCollapsible(this)"
    document.addEventListener('click', _onCollapsibleClick);

    // Control buttons
    document.getElementById('loadRegionBtn')?.addEventListener('click', () => window.loadSelectedRegion?.());
    document.getElementById('submitBtn')?.addEventListener('click', () => window.submitBoundingBox?.());

    window._setupBboxListeners?.();

    // sidebarToggleBtn is handled by the Vue SidebarPanel component's @click="cycleSidebar"
    // bboxVisToggleBtn is handled by SidebarPanel.vue's @click="toggleBboxVis"; a
    // second listener here toggled twice per click, so the button did nothing.

    document.getElementById('sidebarTableSearch')?.addEventListener('input', e => window.renderSidebarTable?.(e.target.value));
    document.getElementById('statusToggleBtn')?.addEventListener('click', () => window.toggleStatusPanel?.());
    document.getElementById('applyParamsBtn')?.addEventListener('click', () => window.applyRegionParams?.());
    document.getElementById('clearBboxBtn')?.addEventListener('click', () => window.clearAllBoundingBoxes?.());

    window._setupModelExportListeners?.();
    window._setupMapAndDemListeners?.();

    window.setupOpacityControls?.();
    window.setupAutoReload?.();
    window.setupStackedLayers?.();
    window.setupCoordinateSearch?.();
    window.setupRegionsTable?.();
    window.setupKeyboardShortcuts?.();

    // Compare view — region load, colormap, exaggeration
    for (const side of ['Left', 'Right']) {
        document.getElementById(`compare${side}Region`)?.addEventListener('change', () => window.loadCompareRegion?.(side.toLowerCase()));
        document.getElementById(`compare${side}Colormap`)?.addEventListener('change', () => window.applyCompareColormap?.(side.toLowerCase()));
        document.getElementById(`compare${side}Exag`)?.addEventListener('change', () => window.updateCompareExagLabel?.(side.toLowerCase()));
    }

    window._setupResizablePanel?.();

    window.initCurveEditor?.();
    window.initPresetProfiles?.();
    window.initRegionNotes?.();
    window.initRegionThumbnails?.();
    window.enableStackedZoomPan?.();

    window._setupSettingsJsonToggle?.();
    window._setupCityAndExportListeners?.();
    window.setupRegionEditor?.();

    // Combined Water + Hydrology section
    document.getElementById('loadWaterHydrologyBtn')?.addEventListener('click', () => window.loadWaterHydrology?.());
    document.getElementById('clearWaterHydrologyBtn')?.addEventListener('click', () => window.clearWaterHydrology?.());
    // Min order and Width x apply to both river sources (Natural Earth gets a
    // pseudo order), so #hydroSource no longer hides them.

    // qlLoadHydro (quick-load) wired in event-listeners-map.js via _asyncBtn

    // Trails section. Only the two buttons here are fetch controls; everything
    // below repaints from the retained payload rather than refetching, since one
    // response already carries both categories, the area masks, and the piste
    // grades. Those controls live in the Trails Display view section.
    document.getElementById('loadTrailsBtn')?.addEventListener('click', () => window.loadTrails?.());
    document.getElementById('clearTrailsBtn')?.addEventListener('click', () => window.clearTrails?.());
    for (const id of ['trailsShowSki', 'trailsShowHiking', 'trailsShowAreas',
                      'trailsColorByDifficulty']) {
        document.getElementById(id)?.addEventListener(
            'change', () => window.refreshTrailsCategories?.());
    }
    // Colour pickers fire `input` continuously while dragging; the repaint is a
    // canvas rewrite of a grid already in memory, so it keeps up.
    for (const id of ['trailsSkiColor', 'trailsHikingColor']) {
        document.getElementById(id)?.addEventListener(
            'input', () => window.refreshTrailsCategories?.());
    }
};

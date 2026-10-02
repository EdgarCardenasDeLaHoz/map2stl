/**
 * modules/keyboard-shortcuts.js
 *
 * Global keyboard shortcuts:
 *   Ctrl+1/2/3    — switch to the Explore / Edit / Extrude tabs
 *   Ctrl+S        — save region
 *   Ctrl+R        — reload layers
 *   Ctrl+Z/Y      — undo / redo curve
 *   Escape        — clear all bounding boxes
 *   (Arrow Up/Down are handled by the region list rows, not here)
 *   G             — toggle pixel/geo grid mode
 *
 * Exposes on window:
 *   window.setupKeyboardShortcuts()
 */

window.setupKeyboardShortcuts = function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        if (e.ctrlKey || e.metaKey) {
            switch (e.key) {
                // One shortcut per header tab (MainHeader.vue data-view), in
                // tab order. There is no Globe tab (the globe is an overlay
                // toggled from the map), so it has no shortcut.
                case '1':
                    e.preventDefault();
                    window.switchView?.('map');
                    window.showToast?.('Explore (Ctrl+1)', 'info');
                    break;
                case '2':
                    e.preventDefault();
                    window.switchView?.('dem');
                    window.showToast?.('Edit (Ctrl+2)', 'info');
                    break;
                case '3':
                    e.preventDefault();
                    window.switchView?.('model');
                    window.showToast?.('Extrude (Ctrl+3)', 'info');
                    break;
                case 's': case 'S':
                    e.preventDefault();
                    window.saveCurrentRegion?.();
                    break;
                case 'r': case 'R':
                    e.preventDefault();
                    if (window.appState.selectedRegion) window.loadAllLayers?.();
                    break;
                case 'z': case 'Z':
                    e.preventDefault();
                    window.undoCurve?.();
                    break;
                case 'y': case 'Y':
                    e.preventDefault();
                    window.redoCurve?.();
                    break;
            }
        }

        if (e.key === 'Escape') {
            window.clearAllBoundingBoxes?.();
        }

        if (e.key === 'g' || e.key === 'G') {
            document.getElementById('gridPixelModeBtn')?.click();
        }

        // No global ↑/↓: the region list rows move focus themselves (region-ui.js). A
        // page-wide handler looked up a missing #coordinateList, threw, and stopped
        // arrow-key scrolling everywhere (removed 2026-10-01).
    });
};

/**
 * modules/keyboard-shortcuts.js
 *
 * Global keyboard shortcuts:
 *   Ctrl+1/2/3/4  — switch views
 *   Ctrl+S        — save region
 *   Ctrl+R        — reload layers
 *   Ctrl+Z/Y      — undo / redo curve
 *   Escape        — clear all bounding boxes
 *   Arrow Up/Down — navigate region list
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
                case '1':
                    e.preventDefault();
                    window.switchView?.('map');
                    window.showToast?.('Map View (Ctrl+1)', 'info');
                    break;
                case '2':
                    e.preventDefault();
                    window.switchView?.('globe');
                    window.showToast?.('Globe View (Ctrl+2)', 'info');
                    break;
                case '3':
                    e.preventDefault();
                    window.switchView?.('dem');
                    window.showToast?.('Layers View (Ctrl+3)', 'info');
                    break;
                case '4':
                    e.preventDefault();
                    window.switchView?.('model');
                    window.showToast?.('Model View (Ctrl+4)', 'info');
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

        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            const regionList = document.getElementById('coordinateList');
            const items = regionList.querySelectorAll('li');
            if (items.length === 0) return;

            const activeItem = regionList.querySelector('li.active');
            let currentIndex = activeItem ? Array.from(items).indexOf(activeItem) : -1;

            currentIndex = e.key === 'ArrowUp'
                ? Math.max(0, currentIndex - 1)
                : Math.min(items.length - 1, currentIndex + 1);

            items[currentIndex].click();
        }
    });
};

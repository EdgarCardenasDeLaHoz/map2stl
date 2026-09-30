// Global bridge functions injected by legacy JS modules.
// These are populated at runtime and accessed via optional chaining.

interface Window {
    // Map layer opacity control (map-layers.js)
    setLayerOpacity?: (mode: string, opacity: number) => void;

    // Sidebar state management (app.js / view-management.js)
    setSidebarState?: (mode: string) => void;
    _setSidebarViews?: (mode: string) => void;

    // Leaflet map instance (set by map-globe.js::initMap via window.setMap)
    getMap?: () => { invalidateSize?: (options?: { animate?: boolean }) => void } | null;

    // DEM stack update trigger
    emitStackUpdate?: () => void;

    // Legacy app state proxy (replaced by Pinia in Stage 7+)
    appState?: Record<string, unknown> & {
        on?: (key: string, cb: (val: unknown) => void) => void;
        off?: (key: string, cb: (val: unknown) => void) => void;
    };
}

// Global bridge functions injected by legacy JS modules.
// These are populated at runtime and accessed via optional chaining.

interface Window {
    // Map layer stack (modules/layers/stacked-layers.js), driven by the Layers rail
    setLayerOpacity?: (mode: string, opacity: number) => void;
    getLayerOpacities?: () => Record<string, number>;
    getLayerOrder?: () => string[];
    setLayerOrder?: (order: string[]) => void;
    getActiveLayers?: () => Set<string>;
    setLayerVisible?: (mode: string, on: boolean, opts?: { autoload?: boolean }) => void;
    getLayerStackState?: () => { order: string[]; opacity: Record<string, number>; visible: Record<string, boolean> };
    applyLayerStackState?: (saved: unknown) => void;
    // Debounced region-settings save (modules/ui/presets.js)
    scheduleSettingsAutoSave?: () => void;

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
    };
}

/**
 * explore/exploreMap.ts - the small API the sidebar uses to drive the Explore map's working
 * box without knowing about Leaflet: the editor previews typed bounds, and puts the saved
 * bounds back when it closes. ExploreMap.vue owns the rectangle (via window.setBboxRectangle,
 * bbox-panel.js) and the Edit page's bounds inputs (window.setBboxInputValues).
 */
export interface Box { north: number; south: number; east: number; west: number }

/** Show a box as the map's working rectangle and in the Edit page's bounds fields. */
export function setBoxPreview(box: Box): void {
    const w = window as any;
    w.setBboxRectangle?.(box.north, box.south, box.east, box.west);
    w.setBboxInputValues?.(box.north, box.south, box.east, box.west);
}

/** Put the working box back on a saved region's bounds (dropping unsaved edits). */
export function restoreBox(region: Box | null | undefined): void {
    if (region) setBoxPreview(region);
}

/**
 * Pure extraction of haversineDiagKm from export/model-viewer.js.
 * Source: app/client/static/js/modules/export/model-viewer.js:507–515
 */
export function haversineDiagKm(north, south, east, west) {
    const R    = 6371;
    const dLat = (north - south) * Math.PI / 180;
    const mid  = ((north + south) / 2) * Math.PI / 180;
    const dLon = (east - west) * Math.PI / 180;
    const dy   = R * dLat;
    const dx   = R * Math.cos(mid) * dLon;
    return Math.sqrt(dx * dx + dy * dy);
}

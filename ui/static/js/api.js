/* DEAD — not loaded by app.js or index.html. Editing this file has no effect on the running app. */
/**
 * API Module
 * Centralized API calls for the 3D Maps application
 */


/**
 * Generic fetch wrapper with error handling
 * @param {string} endpoint - API endpoint
 * @param {Object} params - Query parameters
 * @param {Object} options - Fetch options
 * @returns {Promise<Object>} Response data
 */
export async function fetchAPI(endpoint, params = {}, options = {}) {
    const url = new URL(endpoint, window.location.origin);
    Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
            url.searchParams.set(key, String(value));
        }
    });

    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });

        if (!response.ok) {
            const errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            console.error(errorMessage);
            throw new Error(errorMessage);
        }

        const data = await response.json();

        if (data.error) {
            console.error('API error:', data.error);
            throw new Error(data.error);
        }

        return data;
    } catch (error) {
        console.error('Fetch error:', error);
        throw error;
    }
}

/**
 * Load coordinates/regions from the server
 * @returns {Promise<Array>} Array of region objects
 */
export async function loadCoordinates() {
    const data = await fetchAPI('/api/coordinates');
    return data.regions || [];
}

/**
 * Save a new coordinate/region
 * @param {Object} regionData - Region data to save
 * @returns {Promise<Object>} Save result
 */
export async function saveCoordinate(regionData) {
    const response = await fetch('/api/save_coordinate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(regionData)
    });

    const result = await response.json();

    if (result.status !== 'success') {
        throw new Error(result.error || 'Failed to save region');
    }

    return result;
}

/**
 * Delete a coordinate/region
 * @param {string} name - Region name to delete
 * @returns {Promise<Object>} Delete result
 */
export async function deleteCoordinate(name) {
    const response = await fetch(`/api/regions/${encodeURIComponent(name)}`, {
        method: 'DELETE',
    });

    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Fetch DEM preview data
 * @param {Object} params - Parameters for DEM request
 * @returns {Promise<Object>} DEM data
 */
export async function fetchDEM(params) {
    const {
        north, south, east, west,
        dim = 200,
        depth_scale = 0.5,
        water_scale = 0.05,
        height = 10,
        base = 2,
        subtract_water = true,
        dataset = 'esa',
        show_landuse = false
    } = params;

    return fetchAPI('/api/preview_dem', {
        north, south, east, west,
        dim, depth_scale, water_scale,
        height, base, subtract_water,
        dataset, show_landuse
    });
}

/**
 * Fetch raw DEM data (unprocessed)
 * @param {Object} params - Parameters for raw DEM request
 * @returns {Promise<Object>} Raw DEM data
 */
export async function fetchRawDEM(params) {
    const { north, south, east, west, dim = 200 } = params;
    return fetchAPI('/api/raw_dem', { north, south, east, west, dim });
}

/**
 * Fetch water mask data
 * @param {Object} params - Parameters for water mask request
 * @returns {Promise<Object>} Water mask data
 */
export async function fetchWaterMask(params) {
    const { north, south, east, west, sat_scale = 500, dim = 200 } = params;
    return fetchAPI('/api/water_mask', { north, south, east, west, sat_scale, dim });
}

/**
 * Fetch satellite/land cover data
 * @param {Object} params - Parameters for satellite request
 * @returns {Promise<Object>} Satellite data
 */
export async function fetchSatellite(params) {
    const { north, south, east, west, dim = 200, dataset = 'esa' } = params;
    return fetchAPI('/api/preview_dem', {
        north, south, east, west,
        dim, show_sat: true, dataset
    });
}

/**
 * Submit bounding box to server
 * @param {Object} bounds - Bounding box coordinates
 * @returns {Promise<Object>} Submit result
 */
export async function submitBoundingBox(bounds) {
    const { south, west, north, east } = bounds;

    const response = await fetch('/submit_bounding_box', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            southWestLat: south,
            southWestLng: west,
            northEastLat: north,
            northEastLng: east
        })
    });

    return response.json();
}

/**
 * Generate 3D model
 * @param {Object} params - Model generation parameters
 * @returns {Promise<Object>} Model data
 */
export async function generateModel(params) {
    const {
        north, south, east, west,
        dim = 200,
        depth_scale = 0.5,
        water_scale = 0.05,
        height = 10,
        base = 2,
        subtract_water = true
    } = params;

    return fetchAPI('/api/generate_model', {
        north, south, east, west,
        dim, depth_scale, water_scale,
        height, base, subtract_water
    });
}

/**
 * Download STL file
 * @param {Object} params - Model parameters for STL generation
 */
export async function downloadSTL(params) {
    const url = new URL('/api/download_stl', window.location.origin);
    Object.entries(params).forEach(([key, value]) => {
        url.searchParams.set(key, String(value));
    });

    // Trigger download
    window.location.href = url.toString();
}

/**
 * Clear server cache
 * @returns {Promise<Object>} Clear result
 */
export async function clearCache() {
    return fetchAPI('/api/clear_cache', {}, { method: 'POST' });
}

// Export default object for convenience
export default {
    fetchAPI,
    loadCoordinates,
    saveCoordinate,
    deleteCoordinate,
    fetchDEM,
    fetchRawDEM,
    fetchWaterMask,
    fetchSatellite,
    submitBoundingBox,
    generateModel,
    downloadSTL,
    clearCache
};
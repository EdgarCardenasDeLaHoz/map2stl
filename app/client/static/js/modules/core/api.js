/**
 * modules/api.js — Centralized API route definitions and fetch helpers.
 *
 * Loaded as a plain <script> before app.js. All functions exposed on window.api.
 * app.js gradually migrates raw fetch() calls to use these helpers.
 *
 * Usage:
 *   const regions = await api.regions.list();
 *   const result  = await api.dem.load(params, signal);
 *   await api.regions.saveSettings(name, settings);
 */

window.api = (() => {

    // -------------------------------------------------------------------------
    // Core fetch helper
    // -------------------------------------------------------------------------

    /**
     * Fetch a URL, parse JSON, return { data, error }.
     * Never throws — always returns an object.
     * @param {string} url
     * @param {RequestInit} [options]
     * @returns {Promise<{data: any, error: string|null}>}
     */
    async function _fetch(url, options = {}) {
        try {
            const resp = await fetch(url, options);
            let data;
            const ct = resp.headers.get('content-type') || '';
            if (ct.includes('application/json')) {
                data = await resp.json();
            } else {
                data = await resp.blob();
            }
            if (!resp.ok) {
                const msg = (data && data.error) || `HTTP ${resp.status} ${resp.statusText}`;
                return { data: null, error: msg };
            }
            return { data, error: null };
        } catch (err) {
            return { data: null, error: err.message || String(err) };
        }
    }

    function _json(body) {
        return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    }

    // -------------------------------------------------------------------------
    // Regions
    // -------------------------------------------------------------------------
    const regions = {
        /** GET /api/regions → { regions: [...] } */
        list: () => _fetch('/api/regions'),

        /** POST /api/regions */
        create: (payload) => _fetch('/api/regions', _json(payload)),

        /** PUT /api/regions/{name} */
        update: (name, payload) => _fetch(`/api/regions/${encodeURIComponent(name)}`, {
            ..._json(payload), method: 'PUT'
        }),

        /** DELETE /api/regions/{name} */
        delete: (name) => _fetch(`/api/regions/${encodeURIComponent(name)}`, { method: 'DELETE' }),

        /** GET /api/regions/{name}/settings */
        getSettings: (name) => _fetch(`/api/regions/${encodeURIComponent(name)}/settings`),

        /** PUT /api/regions/{name}/settings */
        saveSettings: (name, settings) => _fetch(`/api/regions/${encodeURIComponent(name)}/settings`, {
            ..._json(settings), method: 'PUT'
        }),
    };

    // -------------------------------------------------------------------------
    // DEM / Terrain
    // -------------------------------------------------------------------------
    const dem = {
        /** GET /api/terrain/dem?{params} */
        load: (params, signal) => _fetch(`/api/terrain/dem?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/dem/raw?{params} */
        loadRaw: (params, signal) => _fetch(`/api/terrain/dem/raw?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/water-mask?{params} */
        waterMask: (params, signal) => _fetch(`/api/terrain/water-mask?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/hydrology?{params} */
        hydrology: (params, signal) => _fetch(`/api/terrain/hydrology?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/satellite?{params} */
        satellite: (params, signal) => _fetch(`/api/terrain/satellite?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/sources */
        sources: () => _fetch('/api/terrain/sources'),

        /** POST /api/dem/merge */
        merge: (body) => _fetch('/api/dem/merge', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Export
    // -------------------------------------------------------------------------
    const exportApi = {
        /** POST /api/export/stl → blob */
        stl: (body) => _fetch('/api/export/stl', _json(body)),

        /** POST /api/export/{format} → blob */
        model: (format, body) => _fetch(`/api/export/${format}`, _json(body)),

        /** POST /api/export/crosssection → blob */
        crossSection: (body) => _fetch('/api/export/crosssection', _json(body)),

        /** POST /api/export/preview → mesh data for 3D viewer */
        preview: (body) => _fetch('/api/export/preview', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Cities
    // -------------------------------------------------------------------------
    const cities = {
        /** POST /api/cities */
        fetch: (body) => _fetch('/api/cities', _json(body)),

        /** GET /api/cities/cached?{params} */
        cached: (params) => _fetch(`/api/cities/cached?${params}`),

        /** POST /api/cities/raster */
        raster: (body) => _fetch('/api/cities/raster', _json(body)),

        /** POST /api/cities/export3mf → blob */
        export3mf: (body) => _fetch('/api/cities/export3mf', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Cache
    // -------------------------------------------------------------------------
    const cache = {
        /** GET /api/cache */
        status: () => _fetch('/api/cache'),

        /** DELETE /api/cache */
        clear: () => _fetch('/api/cache', { method: 'DELETE' }),

        /** GET /api/cache/check?{params} */
        check: (params) => _fetch(`/api/cache/check?${params}`),
    };

    // -------------------------------------------------------------------------
    // Settings
    // -------------------------------------------------------------------------
    const settings = {
        projections: () => _fetch('/api/settings/projections'),
        colormaps:   () => _fetch('/api/settings/colormaps'),
        datasets:    () => _fetch('/api/settings/datasets'),
    };

    // -------------------------------------------------------------------------
    // Misc
    // -------------------------------------------------------------------------
    const misc = {
        globalDemOverview: (regen = false) => _fetch(`/api/global_dem_overview${regen ? '?regen=true' : ''}`),
    };

    // -------------------------------------------------------------------------
    // Composite DEM
    // -------------------------------------------------------------------------
    const composite = {
        /** POST /api/composite/city-raster — rasterize OSM features server-side */
        cityRaster: (body) => _fetch('/api/composite/city-raster', _json(body)),
    };

    return { _fetch, regions, dem, export: exportApi, cities, composite, cache, settings, misc };
})();

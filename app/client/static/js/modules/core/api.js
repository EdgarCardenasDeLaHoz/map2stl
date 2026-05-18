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
    // Dev-mode OpenAPI schema validation (B-OPENAPI)
    // -------------------------------------------------------------------------

    /** @type {Object|null} Cached OpenAPI schema (fetched once in dev mode) */
    let _openApiSchema = null;
    /** @type {boolean} Whether dev validation is active */
    const _devValidation = (location.hostname === 'localhost' || location.hostname === '127.0.0.1');

    if (_devValidation) {
        fetch('/openapi.json')
            .then(r => r.json())
            .then(schema => {
                _openApiSchema = schema;
                console.debug('[api] OpenAPI schema loaded (%d paths)', Object.keys(schema.paths || {}).length);
            })
            .catch(() => { /* non-fatal */ });
    }

    /**
     * Validate a JSON response against the OpenAPI schema (dev mode only).
     * Logs warnings to console — never throws or blocks.
     * @param {string} url - The request URL
     * @param {string} method - HTTP method (GET, POST, etc.)
     * @param {number} status - HTTP status code
     * @param {any} data - Parsed response body
     */
    function _validateResponse(url, method, status, data) {
        if (!_openApiSchema || !data || typeof data !== 'object') return;

        const pathname = new URL(url, location.origin).pathname;
        const pathDef = _findPathDef(pathname);
        if (!pathDef) return;

        const opDef = pathDef[method.toLowerCase()];
        if (!opDef) return;

        const respDef = opDef.responses?.[String(status)] || opDef.responses?.['200'];
        if (!respDef) return;

        const schemaRef = respDef.content?.['application/json']?.schema;
        if (!schemaRef) return;

        const schema = _resolveRef(schemaRef);
        if (!schema) return;

        const errors = _checkSchema(data, schema, '');
        if (errors.length > 0) {
            console.warn(`[api] Schema mismatch: ${method} ${pathname}`, errors.slice(0, 5));
        }
    }

    /** Find the OpenAPI path definition matching a URL pathname. */
    function _findPathDef(pathname) {
        const paths = _openApiSchema?.paths || {};
        // Exact match first
        if (paths[pathname]) return paths[pathname];
        // Try template matching: /api/regions/{name} vs /api/regions/foo
        for (const [tmpl, def] of Object.entries(paths)) {
            const re = new RegExp('^' + tmpl.replace(/\{[^}]+\}/g, '[^/]+') + '$');
            if (re.test(pathname)) return def;
        }
        return null;
    }

    /** Resolve a $ref in the OpenAPI schema (handles #/components/schemas/X). */
    function _resolveRef(obj) {
        if (!obj) return null;
        if (obj.$ref) {
            const parts = obj.$ref.replace('#/', '').split('/');
            let node = _openApiSchema;
            for (const p of parts) {
                node = node?.[p];
            }
            return node || obj;
        }
        return obj;
    }

    /**
     * Shallow schema check — validates required fields and types.
     * Returns array of error strings (empty = valid).
     */
    function _checkSchema(data, schema, path) {
        const resolved = _resolveRef(schema);
        if (!resolved) return [];
        const errors = [];

        if (resolved.type === 'object' && typeof data === 'object' && data !== null) {
            // Check required fields
            for (const req of (resolved.required || [])) {
                if (!(req in data)) {
                    errors.push(`${path}.${req}: required field missing`);
                }
            }
            // Check property types (one level deep)
            for (const [key, propSchema] of Object.entries(resolved.properties || {})) {
                if (key in data) {
                    const propResolved = _resolveRef(propSchema);
                    const val = data[key];
                    if (propResolved?.type && val !== null && val !== undefined) {
                        const actual = Array.isArray(val) ? 'array' : typeof val;
                        const expected = propResolved.type === 'integer' ? 'number' : propResolved.type;
                        if (actual !== expected) {
                            errors.push(`${path}.${key}: expected ${expected}, got ${actual}`);
                        }
                    }
                }
            }
        } else if (resolved.type === 'array' && Array.isArray(data)) {
            // Check first element against items schema
            if (data.length > 0 && resolved.items) {
                const itemErrors = _checkSchema(data[0], resolved.items, `${path}[0]`);
                errors.push(...itemErrors);
            }
        }

        return errors;
    }

    // -------------------------------------------------------------------------
    // Core fetch helper
    // -------------------------------------------------------------------------

    /**
     * Fetch a URL, parse JSON, return { data, error }.
     * Never throws — always returns an object.
     * In dev mode, validates JSON responses against OpenAPI schema.
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
            // Dev-mode schema validation (non-blocking)
            if (_devValidation && _openApiSchema && typeof data === 'object') {
                try {
                    _validateResponse(url, options.method || 'GET', resp.status, data);
                } catch (_) { /* never block on validation errors */ }
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

        /** GET /api/terrain/water-mask?{params} */
        waterMask: (params, signal) => _fetch(`/api/terrain/water-mask?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/esa-land-cover?{params} */
        esaLandCover: (params, signal) => _fetch(`/api/terrain/esa-land-cover?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/hydrology?{params} */
        hydrology: (params, signal) => _fetch(`/api/terrain/hydrology?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/satellite?{params} */
        satellite: (params, signal) => _fetch(`/api/terrain/satellite?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/sources */
        sources: () => _fetch('/api/terrain/sources'),

        /** POST /api/composite/dem-merge */
        merge: (body) => _fetch('/api/composite/dem-merge', _json(body)),
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

        /** POST /api/export/puzzle → start async puzzle 3MF export */
        puzzle: (body) => _fetch('/api/export/puzzle', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Cities
    // -------------------------------------------------------------------------
    const cities = {
        /** POST /api/cities */
        fetch: (body, signal) => _fetch('/api/cities', signal ? { ..._json(body), signal } : _json(body)),

        /** GET /api/cities/cached?{params} */
        cached: (params) => _fetch(`/api/cities/cached?${params}`),

        /** POST /api/cities/raster */
        raster: (body, signal) => _fetch('/api/cities/raster', signal ? { ..._json(body), signal } : _json(body)),

        /** POST /api/cities/export3mf → blob */
        export3mf: (body) => _fetch('/api/cities/export3mf', _json(body)),

        /** GET /api/cities/google3d-available */
        google3dAvailable: () => _fetch('/api/cities/google3d-available'),

        /** POST /api/cities/enhance-heights */
        enhanceHeights: (body) => _fetch('/api/cities/enhance-heights', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Cache
    // -------------------------------------------------------------------------
    const cache = {
        /** GET /api/cache */
        status: () => _fetch('/api/cache'),

        /** GET /api/cache/inventory */
        inventory: () => _fetch('/api/cache/inventory'),

        /** DELETE /api/cache */
        clear: () => _fetch('/api/cache', { method: 'DELETE' }),

        /** DELETE /api/cache/region?north=...&south=...&east=...&west=... */
        clearRegion: (bbox) => _fetch(
            `/api/cache/region?north=${bbox.north}&south=${bbox.south}&east=${bbox.east}&west=${bbox.west}`,
            { method: 'DELETE' }
        ),

        /** GET /api/cache/check?{params} */
        check: (params) => _fetch(`/api/cache/check?${params}`),
    };

    // -------------------------------------------------------------------------
    // Settings
    // -------------------------------------------------------------------------
    const settings = {
        projections: () => _fetch('/api/settings/projections'),
        colormaps: () => _fetch('/api/settings/colormaps'),
        datasets: () => _fetch('/api/settings/datasets'),
        default: () => _fetch('/api/settings/default'),
    };

    // -------------------------------------------------------------------------
    // Misc
    // -------------------------------------------------------------------------
    const misc = {};

    // -------------------------------------------------------------------------
    // Composite DEM
    // -------------------------------------------------------------------------
    const composite = {
        /** POST /api/composite/city-raster — rasterize OSM features server-side */
        cityRaster: (body) => _fetch('/api/composite/city-raster', _json(body)),
    };

    return { _fetch, regions, dem, export: exportApi, cities, composite, cache, settings, misc };
})();

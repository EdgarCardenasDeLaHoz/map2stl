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
     * Describe a failed response using the server's own words where it has any.
     *
     * Our routers answer with {error: "..."}, but anything FastAPI raises itself
     * answers with `detail` instead — a string for HTTPException, and a list of
     * per-field objects for a 422 request-validation failure. Reading only
     * `error` reduced both of those to "HTTP 422 Unprocessable Entity", which
     * names neither the field nor the problem.
     *
     * @param {Response} resp
     * @param {any} data - Already-parsed body: an object for JSON, a Blob otherwise.
     * @returns {string} A message fit to show the user.
     */
    function _describeFailure(resp, data) {
        const detail = data && data.detail;
        if (data && typeof data.error === 'string' && data.error) return data.error;
        if (typeof detail === 'string' && detail) return detail;
        if (Array.isArray(detail) && detail.length) {
            // loc is like ["body", "model_height"]; drop the first element, which
            // only says which part of the request the field lives in.
            const first = detail[0] || {};
            const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : '';
            const msg = first.msg || 'invalid value';
            const more = detail.length > 1 ? ` (and ${detail.length - 1} more)` : '';
            return (field ? `${field}: ${msg}` : msg) + more;
        }
        return `HTTP ${resp.status} ${resp.statusText}`;
    }

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
                return { data: null, error: _describeFailure(resp, data) };
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

        /** PUT /api/regions/{name}; a payload.name different from `name` renames (409 if taken) */
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

        /** GET /api/regions/{name}/landmarks → {overrides: {osm_id: spec}} (F-LANDMARK §3) */
        landmarks: (name) => _fetch(`/api/regions/${encodeURIComponent(name)}/landmarks`),

        /** PUT /api/regions/{name}/landmarks/{osm_id} — osm_id like "way/123" */
        saveLandmark: (name, osmId, spec) => _fetch(
            `/api/regions/${encodeURIComponent(name)}/landmarks/${osmId.split('/').map(encodeURIComponent).join('/')}`,
            { ..._json(spec), method: 'PUT' }),

        /** DELETE /api/regions/{name}/landmarks/{osm_id} */
        deleteLandmark: (name, osmId) => _fetch(
            `/api/regions/${encodeURIComponent(name)}/landmarks/${osmId.split('/').map(encodeURIComponent).join('/')}`,
            { method: 'DELETE' }),
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

        /** GET /api/terrain/trails?{params} */
        trails: (params, signal) => _fetch(`/api/terrain/trails?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/satellite?{params} */
        satellite: (params, signal) => _fetch(`/api/terrain/satellite?${params}`, signal ? { signal } : {}),

        /** GET /api/terrain/sources */
        sources: () => _fetch('/api/terrain/sources'),
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

        /** POST /api/export/preflight → size/bed/pieces/counts/estimates report (no build) */
        preflight: (body) => _fetch('/api/export/preflight', _json(body)),
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

        /** GET /api/cities/google3d-available */
        google3dAvailable: () => _fetch('/api/cities/google3d-available'),

        /** POST /api/cities/enhance-heights */
        enhanceHeights: (body) => _fetch('/api/cities/enhance-heights', _json(body)),

        /** POST /api/cities/start — background fetch; → status (task_id, layers, mirror…) */
        start: (body) => _fetch('/api/cities/start', _json(body)),

        /** GET /api/cities/status/{id} */
        status: (taskId) => _fetch(`/api/cities/status/${encodeURIComponent(taskId)}`),

        /** GET /api/cities/result/{id} — the POST /api/cities payload once done */
        result: (taskId) => _fetch(`/api/cities/result/${encodeURIComponent(taskId)}`),

        /** POST /api/cities/cancel/{id} */
        cancel: (taskId) => _fetch(`/api/cities/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' }),

        /** POST /api/cities/landmarks {buildings, tallest_n, region} → {landmarks, survey_sources, overrides, ids_missing} */
        landmarks: (body) => _fetch('/api/cities/landmarks', _json(body)),

        /** POST /api/cities/landmarks/preview {buildings, osm_id, override} → {vertices, faces, size_mm, report} */
        landmarkPreview: (body) => _fetch('/api/cities/landmarks/preview', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Geocoding / landmarks (F-UX batch 2)
    // -------------------------------------------------------------------------
    const geocode = {
        /** GET /api/geocode?q= → {query, results: [{name, lat, lon, bbox, class, type, …}]} */
        search: (q, limit = 5) => _fetch(`/api/geocode?${new URLSearchParams({ q, limit })}`),

        /** GET /api/geocode/edge-landmarks?north&south&east&west → {landmarks: [...]} */
        edgeLandmarks: (bbox, signal) => _fetch(
            `/api/geocode/edge-landmarks?${new URLSearchParams({
                north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west,
            })}`, signal ? { signal } : {}),
    };

    // -------------------------------------------------------------------------
    // Cache
    // -------------------------------------------------------------------------
    // Only the per-region clear is wired to the UI (the Edit view's clear
    // button). GET /api/cache, /api/cache/check and DELETE /api/cache lost their
    // client wrappers with the cache-status panel (removed 2026-09-30); the routes
    // remain for scripts. /api/cache/inventory was deleted 2026-10-05.
    const cache = {
        /** DELETE /api/cache/region?north=...&south=...&east=...&west=... */
        clearRegion: (bbox) => _fetch(
            `/api/cache/region?north=${bbox.north}&south=${bbox.south}&east=${bbox.east}&west=${bbox.west}`,
            { method: 'DELETE' }
        ),
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
        /** POST /api/composite/dem-merge — server-side layer merge (rivers/lakes preview) */
        demMerge: (body) => _fetch('/api/composite/dem-merge', _json(body)),
    };

    // -------------------------------------------------------------------------
    // Mesh import (STL/OBJ layer) — F-MESHIMPORT
    // -------------------------------------------------------------------------
    const mesh = {
        /** POST /api/layers/mesh/upload (multipart) → {upload_id, filename, size_bytes, format} */
        upload: (file) => {
            const form = new FormData();
            form.append('file', file);
            return _fetch('/api/layers/mesh/upload', { method: 'POST', body: form });
        },

        /** POST /api/layers/mesh/{upload_id}/heightmap */
        heightmap: (uploadId, body) => _fetch(
            `/api/layers/mesh/${encodeURIComponent(uploadId)}/heightmap`, _json(body)),

        /** POST /api/layers/mesh/{upload_id}/register */
        register: (uploadId, body) => _fetch(
            `/api/layers/mesh/${encodeURIComponent(uploadId)}/register`, _json(body)),

        /** DELETE /api/layers/mesh/{upload_id} */
        delete: (uploadId) => _fetch(
            `/api/layers/mesh/${encodeURIComponent(uploadId)}`, { method: 'DELETE' }),

        /** GET /api/layers/mesh/library → {cities: [{city, files: [...]}]} */
        library: () => _fetch('/api/layers/mesh/library'),

        /** POST /api/layers/mesh/library/{relPath}/location */
        setLibraryLocation: (relPath, body) => _fetch(
            `/api/layers/mesh/library/${relPath.split('/').map(encodeURIComponent).join('/')}/location`,
            _json(body)),

        /** POST /api/layers/mesh/library/{relPath}/heightmap */
        libraryHeightmap: (relPath, body) => _fetch(
            `/api/layers/mesh/library/${relPath.split('/').map(encodeURIComponent).join('/')}/heightmap`,
            _json(body)),

        /** POST /api/layers/mesh/library/{relPath}/register */
        libraryRegister: (relPath, body) => _fetch(
            `/api/layers/mesh/library/${relPath.split('/').map(encodeURIComponent).join('/')}/register`,
            _json(body)),

        /** POST /api/layers/mesh/{uploadId}/auto-register */
        autoRegister: (uploadId, body) => _fetch(
            `/api/layers/mesh/${encodeURIComponent(uploadId)}/auto-register`, _json(body)),

        /** POST /api/layers/mesh/library/{relPath}/auto-register */
        libraryAutoRegister: (relPath, body) => _fetch(
            `/api/layers/mesh/library/${relPath.split('/').map(encodeURIComponent).join('/')}/auto-register`,
            _json(body)),
    };

    // -------------------------------------------------------------------------
    // Plate registration + model critic (F-REGION 5, F-LANDMARK 6)
    // -------------------------------------------------------------------------
    const registration = {
        /** GET /api/registration/packs -> {packs: [{slug, city, window, placeable, scorable, ...}]} */
        packs: () => _fetch('/api/registration/packs'),
        /** GET /api/registration/match?rel_path= -> {rel_path, slug|null} */
        match: (relPath) => _fetch(`/api/registration/match?rel_path=${encodeURIComponent(relPath)}`),
        /** POST /api/registration/plate/start {slug, place, fix, rel_path} -> task snapshot */
        start: (body) => _fetch('/api/registration/plate/start', _json(body)),
        /** GET /api/registration/plate/status/{id} -> task snapshot (result when done) */
        status: (taskId) => _fetch(`/api/registration/plate/status/${encodeURIComponent(taskId)}`),
        /** POST /api/registration/plate/cancel/{id} */
        cancel: (taskId) => _fetch(
            `/api/registration/plate/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' }),
        /** GET /api/registration/critic/references?north&south&east&west -> {references} */
        criticReferences: (bbox) => _fetch('/api/registration/critic/references?'
            + new URLSearchParams({ north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west })),
        /** POST /api/registration/critic/score {reference, model} -> metrics */
        criticScore: (body) => _fetch('/api/registration/critic/score', _json(body)),
    };

    return { _fetch, regions, dem, export: exportApi, cities, geocode, composite, cache, settings, misc, mesh, registration };
})();

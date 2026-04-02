/**
 * modules/cache.js — Client-side water mask LRU cache + cache management UI.
 *
 * Loaded as a plain <script> before app.js. Exposes:
 *   window.waterMaskCache  — LRU cache for water mask API responses
 *     .get(bbox)           — return cached data or null; increments hit/miss stats
 *     .set(bbox, data)     — store data; evicts oldest entry when over maxSize
 *     .has(bbox)           — return true if bbox is cached
 *     .generateKey(bbox)   — return the string cache key for a bbox
 *     .getStats()          — return { hits, misses, preloaded, memorySize, hitRate }
 *     .clear()             — clear all entries and reset stats
 *   window.updateCacheStatusUI()
 *   window.fetchServerCacheStatus()
 *   window.preloadAllRegions()
 *   window.clearClientCache()
 *   window.clearServerCache()
 *   window.setupCacheManagement()
 */

window.waterMaskCache = {
    memory:  new Map(),
    maxSize: 50,
    stats:   { hits: 0, misses: 0, preloaded: 0 },

    // Generate cache key from bbox and sat_scale (ESA fetch resolution in m/px)
    generateKey(bbox) {
        // sat_scale controls ESA data quality; demWidth/demHeight ensure alignment
        const sc   = bbox.sat_scale || bbox.resolution || 0;
        const demW = bbox.demWidth  || 0;
        const demH = bbox.demHeight || 0;
        return `${bbox.north.toFixed(4)}_${bbox.south.toFixed(4)}_${bbox.east.toFixed(4)}_${bbox.west.toFixed(4)}_sc${sc}_${demW}x${demH}`;
    },

    get(bbox) {
        const key = this.generateKey(bbox);
        if (this.memory.has(key)) {
            this.stats.hits++;
            return this.memory.get(key).data;
        }
        this.stats.misses++;
        return null;
    },

    set(bbox, data) {
        const key = this.generateKey(bbox);
        this.memory.set(key, { data, timestamp: Date.now() });
        if (this.memory.size > this.maxSize) {
            const oldest = Array.from(this.memory.entries())
                .sort((a, b) => a[1].timestamp - b[1].timestamp)[0];
            this.memory.delete(oldest[0]);
        }
    },

    has(bbox) {
        return this.memory.has(this.generateKey(bbox));
    },

    getStats() {
        const total   = this.stats.hits + this.stats.misses;
        const hitRate = total > 0 ? ((this.stats.hits / total) * 100).toFixed(1) : 0;
        return { ...this.stats, memorySize: this.memory.size, hitRate };
    },

    clear() {
        this.memory.clear();
        this.stats = { hits: 0, misses: 0, preloaded: 0 };
    }
};

// ============================================================
// CACHE MANAGEMENT UI
// ============================================================

/**
 * Refresh the cache count / hit-rate elements in the UI from waterMaskCache.getStats().
 */
window.updateCacheStatusUI = function updateCacheStatusUI() {
    const stats = window.waterMaskCache.getStats();

    const memoryCount = document.getElementById('memoryCacheCount');
    const hitRate = document.getElementById('cacheHitRate');
    const preloadedCount = document.getElementById('preloadedCount');

    if (memoryCount) memoryCount.textContent = `${stats.memorySize} items`;
    if (hitRate) hitRate.textContent = `${stats.hitRate}%`;
    if (preloadedCount) preloadedCount.textContent = `${stats.preloaded} regions`;
};

/**
 * Fetch server-side cache stats from /api/cache and update the DOM counter.
 * @returns {Promise<void>}
 */
window.fetchServerCacheStatus = async function fetchServerCacheStatus() {
    try {
        const serverCacheCount = document.getElementById('serverCacheCount');
        if (serverCacheCount) serverCacheCount.textContent = 'Checking...';

        const { data, error } = await window.api.cache.status();
        if (error) throw new Error(error);

        if (serverCacheCount) {
            serverCacheCount.textContent = `${data.total_cached_files} files (${data.total_size_mb} MB)`;
        }
    } catch (e) {
        console.warn('Could not fetch server cache status:', e);
        const serverCacheCount = document.getElementById('serverCacheCount');
        if (serverCacheCount) serverCacheCount.textContent = 'Error';
    }
};

/**
 * Preload water mask data for every stored region into waterMaskCache.
 * Shows a progress bar in the UI while loading.
 * @returns {Promise<void>}
 */
window.preloadAllRegions = async function preloadAllRegions() {
    const coordinatesData = window.getCoordinatesData?.() || [];
    if (!coordinatesData.length) {
        window.showToast('No regions to preload', 'warning');
        return;
    }

    const progressContainer = document.getElementById('preloadProgress');
    const progressFill = document.getElementById('preloadFill');
    const progressText = document.getElementById('preloadText');
    const preloadBtn = document.getElementById('preloadRegionsBtn');

    progressContainer.classList.remove('hidden');
    preloadBtn.disabled = true;
    preloadBtn.innerHTML = '<span class="btn-icon">⏳</span> Preloading...';

    let loaded = 0;
    let skipped = 0;
    const total = coordinatesData.length;

    window.showToast(`Starting preload of ${total} regions...`, 'info');

    for (const region of coordinatesData) {
        const bbox = {
            north: region.north,
            south: region.south,
            east: region.east,
            west: region.west
        };

        if (window.waterMaskCache.has(bbox)) {
            skipped++;
            console.log(`[Preload] Skipping cached: ${region.name}`);
        } else {
            try {
                console.log(`[Preload] Loading: ${region.name}`);
                const params = new URLSearchParams({
                    north: bbox.north,
                    south: bbox.south,
                    east: bbox.east,
                    west: bbox.west,
                    sat_scale: region.parameters?.sat_scale || 500,
                    dim: region.parameters?.dim || 200
                });

                const { data, error: preloadErr } = await window.api.dem.waterMask(params);

                if (!preloadErr && data && !data.error) {
                    window.waterMaskCache.set(bbox, data);
                    window.waterMaskCache.stats.preloaded++;
                    loaded++;
                }

                // Small delay to not overwhelm server
                await new Promise(resolve => setTimeout(resolve, 300));
            } catch (e) {
                console.warn(`[Preload] Failed for ${region.name}:`, e);
            }
        }

        // Update progress
        const progress = ((loaded + skipped) / total * 100);
        progressFill.style.width = `${progress}%`;
        progressText.textContent = `${loaded + skipped} / ${total} (${loaded} loaded, ${skipped} cached)`;
        window.updateCacheStatusUI();
    }

    preloadBtn.disabled = false;
    preloadBtn.innerHTML = '<span class="btn-icon">⚡</span> Preload All Regions';

    window.showToast(`Preload complete: ${loaded} loaded, ${skipped} already cached`, 'success');

    setTimeout(() => {
        progressContainer.classList.add('hidden');
    }, 3000);
};

/**
 * Clear client-side water mask cache and all layer data, then refresh the status UI.
 */
window.clearClientCache = function clearClientCache() {
    window.waterMaskCache.clear();
    window.clearLayerCache?.();
    window.updateCacheStatusUI();
    window.showToast('Client cache cleared', 'success');
};

/**
 * Delete all server-side cached DEM files via DELETE /api/cache.
 * @returns {Promise<void>}
 */
window.clearServerCache = async function clearServerCache() {
    try {
        const { data, error } = await window.api.cache.clear();
        if (error) throw new Error(error);

        if (data.status === 'success') {
            window.showToast(`Server cache cleared (${data.cleared?.[0]?.files_deleted ?? 0} files)`, 'success');
            window.fetchServerCacheStatus();
        } else {
            window.showToast('Failed to clear server cache', 'error');
        }
    } catch (e) {
        window.showToast('Error clearing server cache: ' + e.message, 'error');
    }
};

/**
 * Wire cache management button click handlers and start the status refresh interval.
 */
window.setupCacheManagement = function setupCacheManagement() {
    document.getElementById('preloadRegionsBtn')?.addEventListener('click', window.preloadAllRegions);
    document.getElementById('clearClientCacheBtn')?.addEventListener('click', window.clearClientCache);
    document.getElementById('clearServerCacheBtn')?.addEventListener('click', window.clearServerCache);

    window.updateCacheStatusUI();
    window.fetchServerCacheStatus();
    setInterval(window.updateCacheStatusUI, 5000);
};

/**
 * LayerApi - API client for layer data
 * 
 * Handles all API calls for loading layer data with
 * consistent error handling, response parsing, and caching.
 */

import { LayerCache, layerCache } from '../cache/LayerCache.js';

export class LayerApi {
  constructor(baseUrl = '', options = {}) {
    this.baseUrl = baseUrl;
    this.cache = options.cache || layerCache;
    this.useCache = options.useCache !== false; // Enable caching by default
  }

  /**
   * Enable or disable caching
   */
  setCacheEnabled(enabled) {
    this.useCache = enabled;
  }

  /**
   * Get cache statistics
   */
  getCacheStats() {
    return this.cache.getStats();
  }

  /**
   * Clear all caches
   */
  clearCache() {
    this.cache.clear();
  }

  /**
   * Build query string from params
   */
  _buildParams(bbox, options = {}) {
    const params = new URLSearchParams({
      north: bbox.north,
      south: bbox.south,
      east: bbox.east,
      west: bbox.west
    });

    // Add optional params
    Object.entries(options).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        params.set(key, value);
      }
    });

    return params.toString();
  }

  /**
   * Generic fetch with error handling
   */
  async _fetch(endpoint, params) {
    const url = `${this.baseUrl}${endpoint}?${params}`;

    try {
      const response = await fetch(url);
      const data = await response.json();

      if (data.error) {
        throw new Error(data.error);
      }

      return { success: true, data };
    } catch (error) {
      console.error(`API error for ${endpoint}:`, error);
      return { success: false, error: error.message };
    }
  }

  /**
   * Load DEM data
   */
  async loadDem(bbox, options = {}) {
    const params = this._buildParams(bbox, {
      dim: options.dim || 200,
      depth_scale: options.depthScale || 0.5,
      water_scale: options.waterScale || 0.05,
      sat_scale: options.satScale || 500,
      subtract_water: options.subtractWater !== false,
      colormap: options.colormap || 'terrain'
    });

    return this._fetch('/submit_bounding_box', params);
  }

  /**
   * Load raw DEM data (without water subtraction)
   */
  async loadRawDem(bbox, options = {}) {
    const params = this._buildParams(bbox, {
      dim: options.dim || 200,
      sat_scale: options.satScale || 500
    });

    return this._fetch('/api/raw_dem', params);
  }

  /**
   * Load water mask data with caching
   */
  async loadWaterMask(bbox, options = {}) {
    const cacheKey = 'water';

    // Check cache first
    if (this.useCache) {
      const cached = this.cache.get(bbox, { ...options, type: cacheKey });
      if (cached) {
        console.log('[LayerApi] Using cached water mask data');
        return { success: true, data: cached, fromCache: true };
      }
    }

    const params = this._buildParams(bbox, {
      dim: options.dim || 200,
      sat_scale: options.satScale || 500
    });

    const result = await this._fetch('/api/water_mask', params);

    // Cache successful results
    if (result.success && this.useCache) {
      this.cache.set(bbox, { ...options, type: cacheKey }, result.data);
    }

    return result;
  }

  /**
   * Load water mask, bypassing cache
   */
  async loadWaterMaskFresh(bbox, options = {}) {
    const params = this._buildParams(bbox, {
      dim: options.dim || 200,
      sat_scale: options.satScale || 500
    });

    const result = await this._fetch('/api/water_mask', params);

    // Still update cache with fresh data
    if (result.success && this.useCache) {
      this.cache.set(bbox, { ...options, type: 'water' }, result.data);
    }

    return result;
  }

  /**
   * Load satellite/land cover data
   * Note: Currently bundled with water mask endpoint
   */
  async loadLandCover(bbox, options = {}) {
    // For now, use water_mask endpoint which includes ESA data
    const result = await this.loadWaterMask(bbox, options);

    if (result.success && result.data.esa_values) {
      // Extract only land cover data
      return {
        success: true,
        data: {
          values: result.data.esa_values,
          dimensions: result.data.esa_dimensions,
          originalDimensions: result.data.esa_original_dimensions,
          bbox: result.data.bbox
        }
      };
    }

    return result;
  }

  /**
   * Load all layers at once (parallel)
   */
  async loadAllLayers(bbox, options = {}) {
    const results = await Promise.allSettled([
      this.loadDem(bbox, options),
      this.loadWaterMask(bbox, options)
    ]);

    return {
      dem: results[0].status === 'fulfilled' ? results[0].value : { success: false, error: results[0].reason },
      water: results[1].status === 'fulfilled' ? results[1].value : { success: false, error: results[1].reason },
      // Land cover is extracted from water mask response
      landCover: results[1].status === 'fulfilled' && results[1].value.success
        ? {
          success: true,
          data: {
            values: results[1].value.data.esa_values,
            dimensions: results[1].value.data.esa_dimensions
          }
        }
        : { success: false, error: 'Failed to load land cover' }
    };
  }

  /**
   * Preload water mask data for multiple regions in background
   * @param {Array} regions - Array of region objects with north, south, east, west
   * @param {Function} onProgress - Optional callback(loaded, total)
   */
  async preloadRegions(regions, onProgress = null) {
    if (!this.useCache) {
      console.log('[LayerApi] Caching disabled, skipping preload');
      return { preloaded: 0, skipped: regions.length };
    }

    let loaded = 0;
    let skipped = 0;
    const total = regions.length;

    console.log(`[LayerApi] Starting preload of ${total} regions`);

    for (const region of regions) {
      const bbox = {
        north: region.north,
        south: region.south,
        east: region.east,
        west: region.west
      };

      const options = region.parameters || {};

      // Check if already cached
      if (this.cache.has(bbox, { ...options, type: 'water' })) {
        skipped++;
        console.log(`[LayerApi] Skipping cached: ${region.name || 'unnamed'}`);
      } else {
        try {
          console.log(`[LayerApi] Preloading: ${region.name || 'unnamed'}`);
          await this.loadWaterMask(bbox, options);
          loaded++;
        } catch (e) {
          console.warn(`[LayerApi] Preload failed for ${region.name}:`, e);
        }

        // Small delay to not overwhelm server
        await new Promise(resolve => setTimeout(resolve, 300));
      }

      if (onProgress) {
        onProgress(loaded + skipped, total);
      }
    }

    console.log(`[LayerApi] Preload complete: ${loaded} loaded, ${skipped} cached`);
    return { preloaded: loaded, skipped };
  }

  /**
   * Check which regions are cached
   * @param {Array} regions - Array of region objects
   * @returns {Object} { cached: [], uncached: [] }
   */
  checkCacheStatus(regions) {
    const cached = [];
    const uncached = [];

    for (const region of regions) {
      const bbox = {
        north: region.north,
        south: region.south,
        east: region.east,
        west: region.west
      };

      if (this.cache.has(bbox, { type: 'water' })) {
        cached.push(region);
      } else {
        uncached.push(region);
      }
    }

    return { cached, uncached };
  }
}

// Export singleton instance
export const layerApi = new LayerApi();

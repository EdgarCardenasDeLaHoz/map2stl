/* DEAD — not loaded by app.js or index.html. Editing this file has no effect on the running app. */
/**
 * LayerManager - Unified state management for map layers
 * 
 * Manages DEM, Water Mask, Land Cover, and Combined layers with:
 * - Automatic cache invalidation on region change
 * - Loading state tracking per layer
 * - Event-based updates
 */

export class LayerManager {
    constructor() {
        // Current bounding box (the source of truth)
        this.currentBbox = null;
        
        // Layer state
        this.layers = {
            dem: this._createEmptyLayer(),
            water: this._createEmptyLayer(),
            landCover: this._createEmptyLayer(),
            combined: this._createEmptyLayer()
        };
        
        // Active layer for display
        this.activeLayer = 'dem';
        
        // Event listeners
        this._listeners = new Map();
    }
    
    /**
     * Create empty layer state
     */
    _createEmptyLayer() {
        return {
            data: null,
            status: 'empty', // 'empty' | 'loading' | 'loaded' | 'error' | 'stale'
            bbox: null,
            error: null,
            loadedAt: null
        };
    }
    
    /**
     * Compare two bounding boxes for equality
     */
    _bboxEquals(a, b) {
        if (!a || !b) return false;
        const epsilon = 0.0001; // ~11 meters at equator
        return Math.abs(a.north - b.north) < epsilon &&
               Math.abs(a.south - b.south) < epsilon &&
               Math.abs(a.east - b.east) < epsilon &&
               Math.abs(a.west - b.west) < epsilon;
    }
    
    /**
     * Set the current region/bounding box
     * Automatically marks non-matching layers as stale
     */
    setRegion(bbox) {
        const oldBbox = this.currentBbox;
        this.currentBbox = {
            north: bbox.north,
            south: bbox.south,
            east: bbox.east,
            west: bbox.west
        };
        
        // If region changed, mark layers as stale
        if (!this._bboxEquals(oldBbox, this.currentBbox)) {
            Object.keys(this.layers).forEach(layerName => {
                const layer = this.layers[layerName];
                if (layer.status === 'loaded' && !this._bboxEquals(layer.bbox, this.currentBbox)) {
                    layer.status = 'stale';
                }
            });
            
            this._emit('region-changed', { oldBbox, newBbox: this.currentBbox });
        }
    }
    
    /**
     * Clear region - invalidates all layers
     */
    clearRegion() {
        this.currentBbox = null;
        Object.keys(this.layers).forEach(layerName => {
            this.layers[layerName] = this._createEmptyLayer();
        });
        this._emit('region-cleared');
    }
    
    /**
     * Check if a layer's data matches the current region
     */
    isLayerCurrent(layerName) {
        const layer = this.layers[layerName];
        return layer.status === 'loaded' && 
               this._bboxEquals(layer.bbox, this.currentBbox);
    }
    
    /**
     * Check if a layer needs to be loaded
     */
    needsLoad(layerName) {
        const layer = this.layers[layerName];
        return layer.status === 'empty' || 
               layer.status === 'stale' ||
               layer.status === 'error';
    }
    
    /**
     * Mark layer as loading
     */
    setLayerLoading(layerName) {
        this.layers[layerName].status = 'loading';
        this.layers[layerName].error = null;
        this._emit('layer-status-changed', { layerName, status: 'loading' });
    }
    
    /**
     * Set layer data (marks as loaded)
     */
    setLayerData(layerName, data) {
        this.layers[layerName] = {
            data,
            status: 'loaded',
            bbox: { ...this.currentBbox },
            error: null,
            loadedAt: Date.now()
        };
        this._emit('layer-loaded', { layerName, data });
        this._emit('layer-status-changed', { layerName, status: 'loaded' });
    }
    
    /**
     * Set layer error
     */
    setLayerError(layerName, error) {
        this.layers[layerName].status = 'error';
        this.layers[layerName].error = error;
        this._emit('layer-error', { layerName, error });
        this._emit('layer-status-changed', { layerName, status: 'error' });
    }
    
    /**
     * Get layer data
     */
    getLayerData(layerName) {
        return this.layers[layerName].data;
    }
    
    /**
     * Get layer status
     */
    getLayerStatus(layerName) {
        return this.layers[layerName].status;
    }
    
    /**
     * Set active layer
     */
    setActiveLayer(layerName) {
        const oldActive = this.activeLayer;
        this.activeLayer = layerName;
        this._emit('active-layer-changed', { oldActive, newActive: layerName });
    }
    
    /**
     * Get current bounding box
     */
    getCurrentBbox() {
        return this.currentBbox ? { ...this.currentBbox } : null;
    }
    
    /**
     * Subscribe to events
     */
    on(event, callback) {
        if (!this._listeners.has(event)) {
            this._listeners.set(event, []);
        }
        this._listeners.get(event).push(callback);
        
        // Return unsubscribe function
        return () => {
            const callbacks = this._listeners.get(event);
            const index = callbacks.indexOf(callback);
            if (index > -1) callbacks.splice(index, 1);
        };
    }
    
    /**
     * Emit event
     */
    _emit(event, data = {}) {
        const callbacks = this._listeners.get(event) || [];
        callbacks.forEach(cb => {
            try {
                cb(data);
            } catch (e) {
                console.error(`Error in LayerManager event handler for '${event}':`, e);
            }
        });
    }
    
    /**
     * Get status summary for all layers
     */
    getStatusSummary() {
        const summary = {};
        Object.keys(this.layers).forEach(layerName => {
            const layer = this.layers[layerName];
            summary[layerName] = {
                status: layer.status,
                isCurrent: this.isLayerCurrent(layerName),
                needsLoad: this.needsLoad(layerName)
            };
        });
        return summary;
    }
}

// Export singleton instance
export const layerManager = new LayerManager();
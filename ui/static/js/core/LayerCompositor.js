/**
 * LayerCompositor - Canvas compositing for map layers
 * 
 * Enables multiple layers to be rendered on top of each other
 * with individual visibility and opacity controls.
 */

export class LayerCompositor {
  constructor(container) {
    this.container = container;

    // Main composite canvas
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');

    // Individual layer canvases
    this.layers = new Map();

    // Layer settings
    this.visibility = new Map();
    this.opacity = new Map();
    this.blendMode = new Map();

    // Z-order (lower = behind)
    this.zOrder = new Map();

    // Setup container
    this.canvas.style.width = '100%';
    this.canvas.style.height = 'auto';
    this.container.innerHTML = '';
    this.container.appendChild(this.canvas);
  }

  /**
   * Set canvas dimensions
   */
  setSize(width, height) {
    this.canvas.width = width;
    this.canvas.height = height;
  }

  /**
   * Add a layer
   */
  addLayer(name, options = {}) {
    const {
      zIndex = this.layers.size,
      visible = true,
      opacity = 1.0,
      blendMode = 'source-over'
    } = options;

    const layerCanvas = document.createElement('canvas');
    layerCanvas.width = this.canvas.width;
    layerCanvas.height = this.canvas.height;

    this.layers.set(name, layerCanvas);
    this.visibility.set(name, visible);
    this.opacity.set(name, opacity);
    this.blendMode.set(name, blendMode);
    this.zOrder.set(name, zIndex);

    return layerCanvas;
  }

  /**
   * Get layer canvas for drawing
   */
  getLayerCanvas(name) {
    return this.layers.get(name);
  }

  /**
   * Remove a layer
   */
  removeLayer(name) {
    this.layers.delete(name);
    this.visibility.delete(name);
    this.opacity.delete(name);
    this.blendMode.delete(name);
    this.zOrder.delete(name);
    this.composite();
  }

  /**
   * Set layer visibility
   */
  setVisibility(name, visible) {
    this.visibility.set(name, visible);
    this.composite();
  }

  /**
   * Toggle layer visibility
   */
  toggleVisibility(name) {
    const current = this.visibility.get(name) ?? true;
    this.setVisibility(name, !current);
    return !current;
  }

  /**
   * Set layer opacity
   */
  setOpacity(name, opacity) {
    this.opacity.set(name, Math.max(0, Math.min(1, opacity)));
    this.composite();
  }

  /**
   * Set layer blend mode
   */
  setBlendMode(name, mode) {
    this.blendMode.set(name, mode);
    this.composite();
  }

  /**
   * Set layer z-order
   */
  setZOrder(name, zIndex) {
    this.zOrder.set(name, zIndex);
    this.composite();
  }

  /**
   * Composite all visible layers onto main canvas
   */
  composite() {
    // Clear main canvas
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Sort layers by z-order
    const sortedLayers = [...this.layers.entries()]
      .sort((a, b) => (this.zOrder.get(a[0]) || 0) - (this.zOrder.get(b[0]) || 0));

    // Draw each visible layer
    for (const [name, layerCanvas] of sortedLayers) {
      if (!this.visibility.get(name)) continue;

      const opacity = this.opacity.get(name) ?? 1.0;
      const blendMode = this.blendMode.get(name) ?? 'source-over';

      this.ctx.save();
      this.ctx.globalAlpha = opacity;
      this.ctx.globalCompositeOperation = blendMode;
      this.ctx.drawImage(layerCanvas, 0, 0);
      this.ctx.restore();
    }
  }

  /**
   * Render image data to a layer
   */
  renderToLayer(name, imageData) {
    const layerCanvas = this.layers.get(name);
    if (!layerCanvas) {
      console.warn(`Layer '${name}' not found`);
      return;
    }

    const ctx = layerCanvas.getContext('2d');
    ctx.putImageData(imageData, 0, 0);
    this.composite();
  }

  /**
   * Clear a layer
   */
  clearLayer(name) {
    const layerCanvas = this.layers.get(name);
    if (!layerCanvas) return;

    const ctx = layerCanvas.getContext('2d');
    ctx.clearRect(0, 0, layerCanvas.width, layerCanvas.height);
    this.composite();
  }

  /**
   * Clear all layers
   */
  clearAll() {
    for (const [name, layerCanvas] of this.layers) {
      const ctx = layerCanvas.getContext('2d');
      ctx.clearRect(0, 0, layerCanvas.width, layerCanvas.height);
    }
    this.composite();
  }

  /**
   * Get composite canvas
   */
  getCanvas() {
    return this.canvas;
  }

  /**
   * Get layer info
   */
  getLayerInfo(name) {
    return {
      exists: this.layers.has(name),
      visible: this.visibility.get(name) ?? false,
      opacity: this.opacity.get(name) ?? 1.0,
      blendMode: this.blendMode.get(name) ?? 'source-over',
      zOrder: this.zOrder.get(name) ?? 0
    };
  }

  /**
   * Get all layer names
   */
  getLayerNames() {
    return [...this.layers.keys()];
  }
}

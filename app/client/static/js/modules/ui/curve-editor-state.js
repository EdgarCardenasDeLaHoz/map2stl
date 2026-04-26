/**
 * modules/ui/curve-editor-state.js — Pure state manager for the elevation curve editor.
 *
 * No DOM, no window globals. Designed so it can be unit-tested in Node
 * and shared by `curve-editor.js` (browser) and vitest (tests/js/).
 *
 * Uses a push-AFTER history model:
 *   - The initial state is pushed during construction.
 *   - After each mutation the new state is pushed to the stack.
 *   - undo() decrements the pointer and restores the previous snapshot.
 *   - redo() increments the pointer and restores the next snapshot.
 *
 * Loaded as a plain <script> before curve-editor.js.
 * Exports: window.CurveEditorState (browser), named export (ES module / vitest).
 */

const CURVE_HISTORY_MAX = 30;

const CURVE_PRESETS = {
    'linear':          [[0, 0], [1, 1]],
    'enhance-peaks':   [[0, 0], [0.3, 0.2], [0.5, 0.4], [0.7, 0.7], [0.85, 0.9], [1, 1]],
    'compress-depths': [[0, 0.2], [0.2, 0.3], [0.4, 0.45], [0.6, 0.6], [0.8, 0.8], [1, 1]],
    's-curve':         [[0, 0], [0.25, 0.1], [0.5, 0.5], [0.75, 0.9], [1, 1]],
};

class CurveEditorState {
    constructor() {
        this.points  = [{ x: 0, y: 0 }, { x: 1, y: 1 }];
        this.preset  = 'linear';
        this._lut    = null;
        this._history    = [];
        this._historyIdx = -1;
        this._pushHistory();
    }

    // -- Read ----------------------------------------------------------------

    getPoints() {
        return this.points.map(p => ({ ...p }));
    }

    getPreset() {
        return this.preset;
    }

    // -- Mutations -----------------------------------------------------------

    setPoints(pts, preset = 'custom') {
        this.points = pts.map(p => ({ ...p }));
        this.preset = preset;
        this._lut = null;
        this._pushHistory();
    }

    /**
     * Load a named preset. Returns true if the preset exists.
     * @param {string} name
     * @returns {boolean}
     */
    loadPreset(name) {
        const data = CURVE_PRESETS[name];
        if (!data) return false;
        this.points = data.map(p => ({ x: p[0], y: p[1] }));
        this.preset = name;
        this._lut = null;
        this._pushHistory();
        return true;
    }

    /**
     * Add a control point at (x, y) in [0,1]x[0,1].
     * Skips if within snap threshold of existing point.
     * Clamps y between neighbours for monotonicity.
     * @returns {boolean} true if added
     */
    addPoint(x, y) {
        const THRESHOLD = 0.08;
        for (const p of this.points) {
            if (Math.abs(p.x - x) < THRESHOLD && Math.abs(p.y - y) < THRESHOLD) {
                return false;
            }
        }

        this.points.sort((a, b) => a.x - b.x);
        let prevY = 0, nextY = 1;
        for (let i = 0; i < this.points.length; i++) {
            if (this.points[i].x <= x) prevY = this.points[i].y;
            else { nextY = this.points[i].y; break; }
        }
        y = Math.max(prevY, Math.min(nextY, y));

        this.points.push({ x, y });
        this.points.sort((a, b) => a.x - b.x);
        this._lut = null;
        this._pushHistory();
        return true;
    }

    /**
     * Remove the control point nearest to (x, y) within the snap threshold.
     * Never removes the first or last endpoint.
     * @returns {boolean} true if removed
     */
    removePoint(x, y) {
        const THRESHOLD = 0.12;
        const index = this.points.findIndex(p =>
            Math.abs(p.x - x) < THRESHOLD && Math.abs(p.y - y) < THRESHOLD
        );
        if (index !== -1 && index !== 0 && index !== this.points.length - 1) {
            this.points.splice(index, 1);
            this._lut = null;
            this._pushHistory();
            return true;
        }
        return false;
    }

    /**
     * Move a point to new coordinates (used during drag).
     * Does NOT push history (caller should push before starting drag).
     * @param {Object} point - Reference to the point in this.points
     * @param {number} newX
     * @param {number} newY
     */
    movePoint(point, newX, newY) {
        point.x = newX;
        point.y = newY;
        this._lut = null;
    }

    /**
     * Find a control point near (x, y) within threshold.
     * @returns {Object|undefined}
     */
    findPointNear(x, y) {
        const THRESHOLD = 0.12;
        return this.points.find(p =>
            Math.abs(p.x - x) < THRESHOLD && Math.abs(p.y - y) < THRESHOLD
        );
    }

    // -- Interpolation -------------------------------------------------------

    interpolate(x) {
        const pts = this.points;
        if (pts.length < 2) return x;
        let left  = pts[0];
        let right = pts[pts.length - 1];
        for (let i = 0; i < pts.length - 1; i++) {
            if (pts[i].x <= x && pts[i + 1].x >= x) {
                left  = pts[i];
                right = pts[i + 1];
                break;
            }
        }
        const t = (x - left.x) / (right.x - left.x || 1);
        return left.y + t * (right.y - left.y);
    }

    buildLUT() {
        if (!this._lut) {
            this._lut = new Float32Array(1024);
            for (let i = 0; i < 1024; i++) this._lut[i] = this.interpolate(i / 1023);
        }
        return this._lut;
    }

    // -- Rescale (when DEM range changes) ------------------------------------

    /**
     * Rescale points so absolute elevation positions are preserved
     * when the DEM vmin/vmax range changes.
     */
    rescalePoints(oldMin, oldMax, newMin, newMax) {
        const oldRange = oldMax - oldMin;
        const newRange = newMax - newMin;
        if (!oldRange || !newRange) return;

        for (let i = 0; i < this.points.length; i++) {
            const pt = this.points[i];
            const absElev = pt.x * oldRange + oldMin;
            pt.x = Math.max(0, Math.min(1, (absElev - newMin) / newRange));
        }
        if (this.points[0].x !== 0) this.points[0].x = 0;
        if (this.points[this.points.length - 1].x !== 1) {
            this.points[this.points.length - 1].x = 1;
        }

        const seen = new Set();
        this.points = this.points.filter(pt => {
            const key = pt.x.toFixed(6);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });

        this._lut = null;
    }

    // -- Serialisation -------------------------------------------------------

    serialize() {
        return { points: this.getPoints(), preset: this.preset };
    }

    deserialize(data) {
        if (data?.points) this.setPoints(data.points, data.preset ?? 'custom');
    }

    // -- History -------------------------------------------------------------

    undo() {
        if (this._historyIdx <= 0) return false;
        this._historyIdx--;
        this.points = this._history[this._historyIdx].map(p => ({ ...p }));
        this._lut = null;
        return true;
    }

    redo() {
        if (this._historyIdx >= this._history.length - 1) return false;
        this._historyIdx++;
        this.points = this._history[this._historyIdx].map(p => ({ ...p }));
        this._lut = null;
        return true;
    }

    canUndo() { return this._historyIdx > 0; }
    canRedo() { return this._historyIdx < this._history.length - 1; }

    /** Push current state to undo stack. Call before drag operations. */
    pushHistory() { this._pushHistory(); }

    _pushHistory() {
        this._history.splice(this._historyIdx + 1);
        this._history.push(this.points.map(p => ({ ...p })));
        if (this._history.length > CURVE_HISTORY_MAX) this._history.shift();
        this._historyIdx = this._history.length - 1;
    }
}

// Browser: expose on window.  Vitest / ES module: named export.
if (typeof window !== 'undefined') {
    window.CurveEditorState = CurveEditorState;
    window.CURVE_PRESETS = CURVE_PRESETS;
}

export { CurveEditorState, CURVE_PRESETS };

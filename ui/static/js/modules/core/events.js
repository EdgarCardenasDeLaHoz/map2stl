/**
 * modules/events.js — Lightweight event emitter + event name constants.
 *
 * Exposes on window:
 *   window.events  — { on, off, emit, once }
 *   window.EV      — event name constants
 *
 * Usage:
 *   window.events.on(window.EV.STATUS_UPDATE, handler);
 *   window.events.emit(window.EV.STATUS_UPDATE);
 */

const _listeners = Object.create(null);

window.events = {
    on(event, fn) {
        if (!_listeners[event]) _listeners[event] = [];
        _listeners[event].push(fn);
        return () => this.off(event, fn);
    },
    off(event, fn) {
        if (!_listeners[event]) return;
        _listeners[event] = _listeners[event].filter(f => f !== fn);
    },
    emit(event, ...args) {
        const fns = _listeners[event];
        if (fns) fns.forEach(fn => fn(...args));
    },
    once(event, fn) {
        const wrapper = (...args) => { this.off(event, wrapper); fn(...args); };
        this.on(event, wrapper);
    },
};

const _evConstants = {
    /** Fired when layer status badges should refresh. */
    STATUS_UPDATE: 'status:update',
    /** Fired when the stacked layers view should re-render. */
    STACKED_UPDATE: 'stacked:update',
};

// Proxy warns on access to unknown event names — catches typos at runtime.
window.EV = new Proxy(_evConstants, {
    get(target, key) {
        if (typeof key === 'string' && !(key in target)) {
            console.warn(`[events] Unknown event constant: EV.${key}`);
        }
        return target[key];
    },
});

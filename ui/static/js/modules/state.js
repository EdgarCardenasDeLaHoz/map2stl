/**
 * modules/state.js — Centralised application state with reactive subscriptions.
 *
 * Loaded as the first plain <script> before app.js and all other modules.
 *
 * Usage:
 *   window.appState.set('selectedRegion', region);   // set + notify listeners
 *   window.appState.get('selectedRegion');            // read
 *   window.appState.on('selectedRegion', fn);        // subscribe
 *   window.appState.off('selectedRegion', fn);       // unsubscribe
 *
 * Backward compatibility:
 *   Direct property reads/writes (window.appState.foo = bar) also work via
 *   the Proxy wrapper, so existing app.js code requires no changes.
 */
(function () {
    'use strict';

    const _state     = {};
    const _listeners = {};

    const _methods = {
        get(key)     { return _state[key]; },
        set(key, val) {
            _state[key] = val;
            (_listeners[key] || []).forEach(fn => { try { fn(val); } catch (e) { console.error('[appState] listener error', e); } });
        },
        on(key, fn)  { (_listeners[key] ??= []).push(fn); },
        off(key, fn) { _listeners[key] = (_listeners[key] || []).filter(f => f !== fn); },
        /** Trigger all listeners for a key without changing the value (e.g. after external mutation). */
        emit(key)    { (_listeners[key] || []).forEach(fn => { try { fn(_state[key]); } catch (e) { console.error('[appState] listener error', e); } }); },
    };

    window.appState = new Proxy(_methods, {
        get(target, key) {
            // Expose methods by name; fall back to reading state for other keys
            return key in target ? target[key] : _state[key];
        },
        set(target, key, val) {
            // Intercept direct property assignment so listeners fire
            if (key in target) { target[key] = val; return true; }
            _methods.set(key, val);
            return true;
        },
        has(target, key) {
            return (key in target) || (key in _state);
        },
    });
})();

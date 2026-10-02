/**
 * modules/map/new-region.js — the new-region flow on the Explore map (F-DESIGN; mockup
 * claude/mockups/2026-10-02-newregion, approved 2026-10-02).
 *
 *   place    a searched place is framed, with a dashed suggested box and its size;
 *   drawing  "＋ New region" or "Draw my own": drag a box, its size shows as you drag;
 *   pending  the box (drawn, or made from the suggestion) has drag handles and a size label;
 *            NewRegionCard.vue names it and saves it.
 *
 * Public API (window.newRegion):
 *   suggest(place)        — frame a searched place and suggest a box (placeBox)
 *   startDraw()           — rectangle draw mode
 *   fromSuggestion()      — the suggested box becomes the pending box
 *   onCreated(layer)      — a drawn rectangle becomes the pending box (Leaflet.draw CREATED)
 *   cancel()              — back to idle: no suggestion, no pending box, drawing stopped
 *   save(name, label)     — save the pending box as a region and select it
 *   requestSave()         — ask the card to save with its name (Ctrl+S)
 *   sizeText(box)         — "13.4 × 11.4 km · ≈ 210 × 178 mm on Ender 220×220 · 1 piece"
 *
 * Every change is announced as a `map2stl:new-region` event on window, detail
 * {phase, place, box, sizeText}; NewRegionCard.vue renders it.
 */
import { formatBboxDims, bboxSizeKm, placeBox } from '../regions/region-geometry.js';
import { bedFitMm, parseBedSize } from '../export/print-scale.js';

const ACCENT = '#0a84ff';

let _state = { phase: 'idle', place: null, box: null };
let _suggestion = null;   // L.Rectangle, dashed
let _pending = null;      // L.Rectangle in the drawn-items group, editable

const _map = () => window.getMap?.();

function _bed() {
    const sel = document.getElementById('bedSizeSelect');
    const bed = parseBedSize(sel?.value, document.getElementById('bedCustomW')?.value,
        document.getElementById('bedCustomH')?.value);
    const name = sel?.value === 'custom' ? `${bed.w}×${bed.h} mm bed` : (sel?.selectedOptions?.[0]?.text || 'your bed');
    return { ...bed, name };
}

function _boxOf(bounds) {
    return { north: bounds.getNorth(), south: bounds.getSouth(), east: bounds.getEast(), west: bounds.getWest() };
}

function sizeText(box) {
    if (!box) return '';
    const { widthKm, heightKm } = bboxSizeKm(box);
    const bed = _bed();
    const mm = bedFitMm(widthKm, heightKm, bed);
    return `${formatBboxDims(box)} · ≈ ${mm.w} × ${mm.h} mm on ${bed.name} · 1 piece`;
}

function _emit() {
    window.dispatchEvent(new CustomEvent('map2stl:new-region', {
        detail: { ..._state, sizeText: sizeText(_state.box) },
    }));
}

function _tag(layer, box, muted = false) {
    const text = sizeText(box);
    if (layer.getTooltip()) layer.setTooltipContent(text);
    else layer.bindTooltip(text, { permanent: true, direction: 'top', className: `nr-size-tag${muted ? ' muted' : ''}`, offset: [0, -4] });
    // Centred over the box's top edge (a tooltip's default anchor is the box centre).
    layer.getTooltip()?.setLatLng([box.north, (box.east + box.west) / 2]);
}

function _clearSuggestion() {
    _suggestion?.remove();
    _suggestion = null;
}

function _removePending() {
    if (!_pending) return;
    _pending.editing?.disable();
    window.getDrawnItems?.()?.removeLayer(_pending);
    _pending.remove();
    _pending = null;
}

function _drawHandler() {
    return window.getDrawControl?.()?._toolbars?.draw?._modes?.rectangle?.handler || null;
}

// While dragging, Leaflet.draw's tooltip shows the box's size and printed size.
function _onDrawMove() {
    const h = _drawHandler();
    const shape = h?._shape;
    if (!shape) return;
    h._tooltip?.updateContent({ text: sizeText(_boxOf(shape.getBounds())), subtext: 'Release to finish · Esc cancels' });
}

function _stopDrawing() {
    _map()?.off('mousemove', _onDrawMove);
    const h = _drawHandler();
    if (h?.enabled?.()) h.disable();
    document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
}

function suggest(place) {
    const map = _map();
    const L = window.L;
    if (!map || !L || !place) return;
    _removePending();
    _clearSuggestion();
    const bed = _bed();
    const box = placeBox(place, bed.w / bed.h);
    _suggestion = L.rectangle([[box.south, box.west], [box.north, box.east]], {
        color: ACCENT, weight: 2, dashArray: '6 6', fillColor: ACCENT, fillOpacity: 0.05, interactive: false,
    }).addTo(map);
    _tag(_suggestion, box, true);
    // Frame the box with room around it, not the place at street level.
    map.fitBounds([[box.south, box.west], [box.north, box.east]],
        { paddingTopLeft: [60, 90], paddingBottomRight: [60, 190] });   // clear of the card
    _state = { phase: 'place', place, box };
    _emit();
}

function startDraw() {
    const map = _map();
    const h = _drawHandler();
    if (!map || !h) return;
    _removePending();
    _clearSuggestion();
    h.enable();
    map.off('mousemove', _onDrawMove);
    map.on('mousemove', _onDrawMove);
    document.getElementById('floatingDrawBtn')?.classList.add('drawing');
    _state = { phase: 'drawing', place: _state.place, box: null };
    _emit();
}

function _makePending(layer) {
    _pending = layer;
    layer.setStyle?.({ color: ACCENT, weight: 2, fillColor: ACCENT, fillOpacity: 0.1, dashArray: null });
    layer.editing?.enable();
    const box = _boxOf(layer.getBounds());
    _tag(layer, box);
    window.setBoundingBox?.(layer.getBounds());
    window.events?.emit(window.EV?.BBOX_CHANGED, box);
    _state = { phase: 'pending', place: _state.place, box };
    _emit();
}

function onCreated(layer) {
    _stopDrawing();
    _makePending(layer);
}

function fromSuggestion() {
    const L = window.L;
    const map = _map();
    const box = _state.box;
    if (!L || !map || !box) return;
    _clearSuggestion();
    const rect = L.rectangle([[box.south, box.west], [box.north, box.east]]);
    window.getDrawnItems?.()?.addLayer(rect);
    _makePending(rect);
}

// Handles moved: keep the label, the working box and the card in step.
function _onEdited(e) {
    if (!_pending || (e.layer && e.layer !== _pending)) return;
    const box = _boxOf(_pending.getBounds());
    _tag(_pending, box);
    window.setBoundingBox?.(_pending.getBounds());
    window.events?.emit(window.EV?.BBOX_CHANGED, box);
    _state = { ..._state, box };
    _emit();
}

function _onDrawStop() {
    // Esc or the toolbar's Cancel while drawing (a finished box goes through onCreated first).
    if (_state.phase !== 'drawing') return;
    _map()?.off('mousemove', _onDrawMove);
    document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
    _state = { phase: 'idle', place: null, box: null };
    _emit();
}

function cancel() {
    const was = _state.phase;
    _stopDrawing();
    _clearSuggestion();
    if (_pending) {
        _removePending();
        window.setBoundingBox?.(null);
    }
    if (was === 'place' || was === 'pending') window.clearLandmarkSearch?.();
    _state = { phase: 'idle', place: null, box: null };
    _emit();
}

/** @returns {Promise<boolean>} saved */
async function save(name, label) {
    if (_state.phase !== 'pending') return false;
    const saved = await window.saveCurrentRegion?.({ name, label });
    if (!saved) return false;
    _removePending();
    window.clearLandmarkSearch?.();
    _state = { phase: 'idle', place: null, box: null };
    _emit();
    return true;
}

function requestSave() {
    if (_state.phase !== 'pending') return false;
    window.dispatchEvent(new CustomEvent('map2stl:new-region-save'));
    return true;
}

function _wire() {
    const map = _map();
    if (!map || !window.L) { setTimeout(_wire, 200); return; }
    map.on('draw:editresize draw:editmove', _onEdited);
    map.on(window.L.Draw.Event.DRAWSTOP, _onDrawStop);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _wire);
else setTimeout(_wire, 0);

window.newRegion = {
    suggest, startDraw, fromSuggestion, onCreated, cancel, save, requestSave, sizeText,
    get phase() { return _state.phase; },
};

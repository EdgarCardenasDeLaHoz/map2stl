/**
 * modules/regions/region-editor.js — the sidebar region editor (#sidebarEditView,
 * markup in vue/components/sidebar/SidebarEditView.vue).
 *
 * Opened by the ✎ button on a region row (region-ui.js::renderCoordinatesList).
 * Edits name (rename), group label, N/S/E/W (live size readout; the map's working
 * box follows through bbox-panel.js::setBboxRectangle) and notes, then saves them
 * in one PUT /api/regions/{old name} — a different body name renames the region
 * server-side, moving its settings and landmark overrides with it. Delete reuses
 * view-management.js::deleteRegion (confirm + DELETE).
 *
 * Public API (on window):
 *   openRegionEditor(index)   — select the region if needed, fill and show the editor
 *   setupRegionEditor()       — wire the form once (called from event-listeners.js)
 *
 * Dependencies (resolved at call time):
 *   window.getCoordinatesData(), window.selectCoordinate(i), window.loadCoordinates()
 *   window.setRegionEditorOpen(open)       (SidebarPanel.vue)
 *   window.setBboxRectangle / setBboxInputValues (bbox-panel.js)
 *   window.getRegionNote / setRegionNote / renameRegionLocalData (region-ui.js)
 *   window.deleteRegion(i) (view-management.js), window.api.regions.update
 *   window.setSelectedRegion, window.appState.selectedRegion, window.showToast
 */

import { bboxSizeKm, formatBboxSize, parseBbox } from './region-geometry.js';

const BOUND_IDS = ['sbNorth', 'sbSouth', 'sbEast', 'sbWest'];

/** Name of the region the editor was opened on (the PUT path while renaming). */
let _editingName = null;

const $ = (id) => document.getElementById(id);

function _readBox() {
    return parseBbox(...BOUND_IDS.map((id) => $(id)?.value));
}

function _showSize(box) {
    const el = $('sbSizeReadout');
    if (!el) return;
    el.textContent = box ? formatBboxSize(bboxSizeKm(box)) : 'Invalid bounds: north must be above south, within ±90° / ±180°';
    el.classList.toggle('invalid', !box);
}

function _fill(region) {
    _editingName = region.name;
    $('regionNameEdit').value = region.name;
    $('regionLabelEdit').value = region.label || '';
    const vals = [region.north, region.south, region.east, region.west];
    BOUND_IDS.forEach((id, i) => { const el = $(id); if (el) el.value = Number(vals[i]).toFixed(5); });
    const notes = $('sbNotesTextarea');
    const note = window.getRegionNote?.(region.name) || '';
    if (notes) notes.value = note;
    const section = $('sbNotesSection');
    if (section) section.open = false;
    const summary = section?.querySelector('summary');
    if (summary) summary.textContent = note ? 'Notes •' : 'Notes';
    const datalist = $('regionLabelsList');
    if (datalist) {
        const labels = [...new Set((window.getCoordinatesData?.() || []).map((r) => r.label).filter(Boolean))].sort();
        datalist.innerHTML = labels.map((l) => `<option value="${window.escapeHtml(l)}">`).join('');
    }
    _showSize(region);
}

function _isOpen() {
    return !$('sidebarEditView')?.classList.contains('hidden');
}

/**
 * Open the editor on a region, selecting it first if it is not already.
 * @param {number} index - Index into getCoordinatesData()
 */
async function openRegionEditor(index) {
    const region = (window.getCoordinatesData?.() || [])[index];
    if (!region) return;
    if (window.appState.selectedRegion?.name !== region.name) await window.selectCoordinate(index);
    _fill(region);
    window.setRegionEditorOpen?.(true);
    requestAnimationFrame(() => $('regionNameEdit')?.focus());
}

/** Move the map's working box with the N/S/E/W fields as they are typed. */
function _onBoundsInput() {
    const box = _readBox();
    _showSize(box);
    if (!box) return;
    window.setBboxRectangle?.(box.north, box.south, box.east, box.west);
    window.setBboxInputValues?.(box.north, box.south, box.east, box.west);
}

async function _save() {
    const oldName = _editingName;
    const region = (window.getCoordinatesData?.() || []).find((r) => r.name === oldName);
    if (!region) return;
    const name = $('regionNameEdit').value.trim();
    if (!name) {
        window.showToast?.('Name is required', 'error');
        $('regionNameEdit').focus();
        return;
    }
    const box = _readBox();
    if (!box) {
        window.showToast?.('Fix the bounds before saving', 'error');
        return;
    }
    const label = $('regionLabelEdit').value.trim();
    const btn = $('sbSaveBtn');
    if (btn) btn.disabled = true;
    try {
        const { error } = await window.api.regions.update(oldName, {
            name, label, description: region.description ?? null, ...box,
        });
        if (error) { window.showToast?.(`Save failed: ${error}`, 'error'); return; }

        if (name !== oldName) window.renameRegionLocalData?.(oldName, name);
        window.setRegionNote?.(name, $('sbNotesTextarea')?.value || '');
        _editingName = name;

        // Keep the selection on the saved region: the list and the boxes are
        // rebuilt from fresh objects, matched by name.
        const sel = window.appState.selectedRegion;
        if (sel?.name === oldName) Object.assign(sel, { name, label, ...box });
        await window.loadCoordinates?.();
        const fresh = (window.getCoordinatesData?.() || []).find((r) => r.name === name);
        if (fresh && window.appState.selectedRegion?.name === name) {
            window.setSelectedRegion?.(fresh);
            window.appState.selectedRegion = fresh;
        }
        window.showToast?.(name !== oldName ? `Renamed to "${name}"` : 'Region saved', 'success');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function _delete() {
    const index = (window.getCoordinatesData?.() || []).findIndex((r) => r.name === _editingName);
    if (index < 0) return;
    if (await window.deleteRegion?.(index)) {
        _editingName = null;
        window.setRegionEditorOpen?.(false);
    }
}

/**
 * Leave the editor: put the map's working box back on the saved bounds (unsaved
 * N/S/E/W edits are dropped) and return focus to the row it was opened from.
 */
function _back() {
    const name = _editingName;
    const saved = (window.getCoordinatesData?.() || []).find((r) => r.name === name);
    if (saved && window.appState.selectedRegion?.name === name) {
        window.setBboxRectangle?.(saved.north, saved.south, saved.east, saved.west);
        window.setBboxInputValues?.(saved.north, saved.south, saved.east, saved.west);
    }
    requestAnimationFrame(() => {
        if (!name) return;
        const row = document.querySelector(`#coordinatesList .coordinate-item[data-region-name="${CSS.escape(name)}"]`);
        (row?.querySelector('.coordinate-item-edit') || row)?.focus();
    });
}

function setupRegionEditor() {
    const form = $('regionEditorForm');
    if (!form || form.dataset.wired) return;
    form.dataset.wired = '1';
    form.addEventListener('submit', (e) => { e.preventDefault(); void _save(); });
    $('sbDeleteBtn')?.addEventListener('click', () => { void _delete(); });
    $('sbBackBtn')?.addEventListener('click', _back);
    BOUND_IDS.forEach((id) => $(id)?.addEventListener('input', _onBoundsInput));
    $('sidebarEditView')?.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.preventDefault(); $('sbBackBtn')?.click(); }
    });
    // A different region selected on the map while the editor is open: follow it.
    window.events?.on?.(window.EV?.REGION_SELECTED, (_index, region) => {
        if (_isOpen() && region && region.name !== _editingName) _fill(region);
    });
}

window.openRegionEditor = openRegionEditor;
window.setupRegionEditor = setupRegionEditor;

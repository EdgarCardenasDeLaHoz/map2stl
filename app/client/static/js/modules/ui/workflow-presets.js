/**
 * modules/ui/workflow-presets.js — one-click City / Mountain / Coast set-ups.
 *
 * A workflow preset sets the existing form controls across the Edit and Extrude
 * tabs (DEM source + resolution, vertical mode + exaggeration, City Model layer
 * rows, puzzle options) and dispatches input + change on each, so every
 * listener that already watches those controls (Vue v-model, auto-save,
 * model auto-rebuild) runs as if the user had edited them.
 *
 * Unlike the view presets in presets.js (dim, colormap, curve...), these touch
 * controls that collectAllSettings() does not cover, so applying one returns
 * its own undo list. presets.js lists them in #presetSelect and wires revert;
 * the Edit panel's preset buttons (WorkflowPresetBar.vue) call
 * window.applyWorkflowPreset().
 *
 * No window access here: the document is passed in, so tests can use a fake.
 */
import { defaultPieceMm, parseBedSize } from '../export/print-scale.js';

/** Resolved at apply time: the default puzzle piece for the selected bed. */
export const BED_PIECE = '$bedPiece';

const EXTRUDED = ['buildings', 'fortifications', 'walls', 'towers', 'churches'];
const SURFACE = ['roads', 'railways', 'trails', 'green', 'waterways'];
const ALL_CITY_LAYERS = [...EXTRUDED, ...SURFACE];

const layer = (id, enabled) => ({ id: `cityLayer_${id}_enabled`, checked: enabled });

/**
 * Fields are applied in order; a layer's enable box comes before its mode and
 * value so they are not written while disabled.
 */
export const WORKFLOW_PRESETS = {
    city: {
        label: 'City',
        title: 'SRTM 30 m at 1000 px, true-scale vertical, every city layer on, puzzle pieces sized to the bed',
        fields: [
            { id: 'paramDemSource', value: 'SRTMGL1' },
            { id: 'paramDim', value: '1000' },
            { id: 'exportZMode', value: 'auto' },
            { id: 'exportExaggeration', value: '1' },
            ...ALL_CITY_LAYERS.map(id => layer(id, true)),
            { id: 'cityPuzzleEnabled', checked: true },
            { id: 'cityPieceMm', value: BED_PIECE },
        ],
    },
    mountain: {
        label: 'Mountain',
        title: 'SRTM 30 m at 1000 px, 1.5× vertical, buildings and roads off, trails and water on',
        fields: [
            { id: 'paramDemSource', value: 'SRTMGL1' },
            { id: 'paramDim', value: '1000' },
            { id: 'exportZMode', value: 'auto' },
            { id: 'exportExaggeration', value: '1.5' },
            ...EXTRUDED.map(id => layer(id, false)),
            layer('roads', false),
            layer('railways', false),
            layer('trails', true),
            layer('waterways', true),
        ],
    },
    coast: {
        label: 'Coast',
        title: 'SRTM 30 m at 1000 px, sea-level cap, water engraved 1 mm, buildings on',
        fields: [
            { id: 'paramDemSource', value: 'SRTMGL1' },
            { id: 'paramDim', value: '1000' },
            { id: 'exportSeaLevelCap', checked: true },
            layer('waterways', true),
            { id: 'cityLayer_waterways_mode', value: 'engraved' },
            { id: 'cityLayer_waterways_value', value: '1' },
            layer('buildings', true),
        ],
    },
};

function _fire(el) {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
}

function _resolve(value, doc) {
    if (value !== BED_PIECE) return String(value);
    const bed = parseBedSize(
        doc.getElementById('bedSizeSelect')?.value,
        doc.getElementById('bedCustomW')?.value,
        doc.getElementById('bedCustomH')?.value,
    );
    return String(defaultPieceMm(bed));
}

/**
 * Set each field and fire its events.
 *
 * @param {Array<{id:string, value?:string, checked?:boolean}>} fields
 * @param {Document} doc
 * @returns {{applied:string[], skipped:Array<{id:string, reason:string}>,
 *            undo:Array<{id:string, value?:string, checked?:boolean}>}}
 *   undo restores the previous values when passed back to applyFields().
 */
export function applyFields(fields, doc) {
    const applied = [];
    const skipped = [];
    const undo = [];
    for (const field of fields) {
        const el = doc.getElementById(field.id);
        if (!el) { skipped.push({ id: field.id, reason: 'not on the page' }); continue; }
        if ('checked' in field) {
            if (el.checked === !!field.checked) continue;
            undo.push({ id: field.id, checked: el.checked });
            el.checked = !!field.checked;
        } else {
            const value = _resolve(field.value, doc);
            if (el.tagName === 'SELECT') {
                const opt = [...(el.options || [])].find(o => o.value === value);
                if (!opt) { skipped.push({ id: field.id, reason: `no option "${value}"` }); continue; }
                if (opt.disabled) {
                    skipped.push({ id: field.id, reason: `"${opt.textContent || value}" is unavailable` });
                    continue;
                }
            }
            if (String(el.value) === value) continue;
            undo.push({ id: field.id, value: String(el.value) });
            el.value = value;
        }
        _fire(el);
        applied.push(field.id);
    }
    // Undo runs back to front so enable boxes are restored after their rows.
    return { applied, skipped, undo: undo.reverse() };
}

/**
 * Apply a named workflow preset.
 * @param {'city'|'mountain'|'coast'} name
 * @param {Document} doc
 */
export function applyWorkflowPreset(name, doc) {
    const preset = WORKFLOW_PRESETS[name];
    if (!preset) throw new Error(`Unknown workflow preset "${name}"`);
    return applyFields(preset.fields, doc);
}

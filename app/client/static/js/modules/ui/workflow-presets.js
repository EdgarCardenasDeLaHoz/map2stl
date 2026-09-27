/**
 * modules/ui/workflow-presets.js — one-click City / Mountain / Coast / Region set-ups.
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
/** Resolved at apply time from the region size: 30 m source up to ~100 km, 90 m beyond. */
export const REGION_DEM_SOURCE = '$regionDemSource';

/** Longest bbox side (km) up to which the Region preset uses a 30 m source. */
export const REGION_30M_MAX_KM = 100;

/**
 * DEM source for a large region: SRTM 30 m (SRTMGL1) while the longer side is
 * at most REGION_30M_MAX_KM, SRTM 90 m (SRTMGL3) beyond. At the preset's
 * 1000 px a 100 km box is 100 m/px, so a 30 m source is already downsampled
 * 3x; past that the 90 m tiles carry the same detail for a ninth of the data.
 * @param {{north:number, south:number, east:number, west:number}|null} bbox
 * @returns {string|null} null when no region is known
 */
export function regionDemSource(bbox) {
    if (!bbox || ![bbox.north, bbox.south, bbox.east, bbox.west].every(Number.isFinite)) return null;
    const midLat = (bbox.north + bbox.south) / 2;
    const heightKm = Math.abs(bbox.north - bbox.south) * 110.574;
    const widthKm = Math.abs(bbox.east - bbox.west) * 111.32 * Math.cos(midLat * Math.PI / 180);
    return Math.max(heightKm, widthKm) <= REGION_30M_MAX_KM ? 'SRTMGL1' : 'SRTMGL3';
}

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
    region: {
        label: 'Region',
        title: 'Large area: terrain only (city layers off), rivers + lakes carved, vertical auto '
            + '(fit above 20 km), SRTM 30 m up to ~100 km and 90 m beyond',
        // Rivers and lakes live in the Composite panel: load the DEM, then Apply.
        hint: 'Load the DEM, then Composite → Apply to DEM to carve rivers and lakes.',
        fields: [
            { id: 'paramDemSource', value: REGION_DEM_SOURCE },
            { id: 'paramDim', value: '1000' },
            { id: 'exportZMode', value: 'auto' },
            { id: 'exportExaggeration', value: '1' },
            ...ALL_CITY_LAYERS.map(id => layer(id, false)),
            { id: 'cityPuzzleEnabled', checked: false },
            { id: 'compositeEnabled', checked: true },
            { id: 'compositeRiversEnabled', checked: true },
            { id: 'compositeLakesEnabled', checked: true },
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

function _resolve(value, doc, ctx) {
    if (value === REGION_DEM_SOURCE) return regionDemSource(ctx?.bbox);
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
 * @param {{bbox?:Object}} [ctx]  the selected region, for REGION_DEM_SOURCE
 * @returns {{applied:string[], skipped:Array<{id:string, reason:string}>,
 *            undo:Array<{id:string, value?:string, checked?:boolean}>}}
 *   undo restores the previous values when passed back to applyFields().
 */
export function applyFields(fields, doc, ctx = {}) {
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
            const value = _resolve(field.value, doc, ctx);
            if (value === null) { skipped.push({ id: field.id, reason: 'select a region first' }); continue; }
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
 * @param {'city'|'mountain'|'region'|'coast'} name
 * @param {Document} doc
 * @param {{bbox?:Object}} [ctx]
 */
export function applyWorkflowPreset(name, doc, ctx = {}) {
    const preset = WORKFLOW_PRESETS[name];
    if (!preset) throw new Error(`Unknown workflow preset "${name}"`);
    return applyFields(preset.fields, doc, ctx);
}

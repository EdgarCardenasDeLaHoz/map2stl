/**
 * modules/ui/make-printable.js — "✨ Make it printable": the whole job in one click
 * (design guidelines §1.12; decisions/frontend.md 2026-10-01).
 *
 * For the selected region: pick a workflow preset from the box size, apply it, load
 * the DEM and its layers, size the model to fill the printer bed (no pieces), open
 * Extrude (which builds the preview), and announce it with an Undo.
 *
 *   window.makePrintable()  — run it (header button)
 *
 * The announcement is a `map2stl:made-printable` event on window, detail
 * {preset, label, undo}; ModelContainer.vue shows it as a banner. undo() restores
 * every field this changed and reloads the DEM if its source or resolution changed.
 */
import { WORKFLOW_PRESETS, applyFields, choosePreset } from './workflow-presets.js';
import { fillBedMmPerPx, parseBedSize } from '../export/print-scale.js';

/** Fields that decide what the DEM request fetches; a change in undo means reload. */
const DEM_FIELDS = new Set(['paramDemSource', 'paramDim', 'paramProjection']);

let _busy = false;

function _waitForDem(timeoutMs = 300_000) {
    return new Promise((resolve, reject) => {
        const t0 = Date.now();
        const tick = () => {
            const d = window.appState?.lastDemData;
            if (d?.values?.length && d.width && d.height) return resolve(d);
            if (Date.now() - t0 > timeoutMs) return reject(new Error('the DEM did not load'));
            setTimeout(tick, 250);
        };
        tick();
    });
}

async function makePrintable() {
    const region = window.appState?.selectedRegion;
    if (!region) {
        window.showToast?.('Pick a region on the map first, then ✨ Make it printable.', 'info');
        window.switchView?.('map');
        return;
    }
    if (_busy) return;
    _busy = true;
    const btn = document.getElementById('makePrintableBtn');
    btn?.setAttribute('aria-busy', 'true');
    try {
        const name = choosePreset(region);
        const preset = WORKFLOW_PRESETS[name];
        // The model is sized to the bed, so no pieces; the user can still switch them on.
        const fields = [...preset.fields,
            { id: 'cityPuzzleEnabled', checked: false }, { id: 'puzzleEnabled', checked: false }];
        const { undo } = applyFields(fields, document, { bbox: region });
        const prevMm = document.getElementById('mmPerPixel')?.value;

        window.appState.lastDemData = null;      // wait for this load, not the previous DEM
        await window.loadDEM?.();
        const dem = await _waitForDem();
        const bed = parseBedSize(document.getElementById('bedSizeSelect')?.value,
            document.getElementById('bedCustomW')?.value, document.getElementById('bedCustomH')?.value);
        const mm = fillBedMmPerPx(dem.width, dem.height, bed);
        const restore = [...undo];
        if (mm > 0) {
            const r = applyFields([{ id: 'mmPerPixel', value: String(mm) }], document);
            restore.unshift(...(r.undo.length ? r.undo : [{ id: 'mmPerPixel', value: prevMm }]));
        }
        window.switchView?.('model');

        const reloads = undo.some(f => DEM_FIELDS.has(f.id));
        window.dispatchEvent(new CustomEvent('map2stl:made-printable', {
            detail: {
                preset: name,
                label: preset.label,
                undo: async () => {
                    applyFields(restore, document);
                    if (reloads) await window.loadDEM?.();
                },
            },
        }));
    } catch (err) {
        window.showToast?.(`Could not make it printable: ${err.message}. Try Load DEM in the Edit tab.`, 'error');
    } finally {
        _busy = false;
        btn?.removeAttribute('aria-busy');
    }
}

window.makePrintable = makePrintable;

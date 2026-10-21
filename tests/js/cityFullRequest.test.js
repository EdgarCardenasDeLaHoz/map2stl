/**
 * cityFullRequest.test.js — model-viewer.js's finished-model request key and the F-DETAIL
 * switches that rebuild it. model-viewer.js is a side-effecting browser module, so
 * _fullModelRequest() is sliced out of the real source (as previewGate.test.js does).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const source = readFileSync(
    join(__dirname, '..', '..', 'app', 'client', 'static', 'js', 'modules', 'export', 'model-viewer.js'), 'utf8');
const match = source.match(/^function _fullModelRequest\(\) \{[\s\S]*?^\}$/m);
const make = (body) => new Function('window', '_isCityBox',
    `${match[0]}; return _fullModelRequest;`)({ cityExportBody: () => body }, () => true);

describe('_fullModelRequest key', () => {
    const base = { layers: { buildings: { enabled: true } }, roof_shapes: 'auto', reference_landmarks: false };
    const key = (b) => make(b)().key;

    it('changes with reference_landmarks, roof_shapes and trees', () => {
        const k = key(base);
        expect(key({ ...base, reference_landmarks: true })).not.toBe(k);
        expect(key({ ...base, roof_shapes: 'off' })).not.toBe(k);
        expect(key({ ...base, layers: { ...base.layers, trees: { enabled: true } } })).not.toBe(k);
        expect(key(base)).toBe(k);
    });
});

describe('F-DETAIL switches schedule a rebuild', () => {
    it('model-viewer listens for #cityAlhambra, #cityRoofShapes and #cityTrees', () => {
        expect(source).toMatch(/\^city\(Alhambra\|RoofShapes\|Trees\)\$/);
        expect(source).toMatch(/detailControl\.test\(e\.target\?\.id \|\| ''\)\) refreshCityLayers\(\)/);
    });
});

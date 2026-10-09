import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

// The /reports heights view's verification block (F-SKY26 2f): counts, the verified share,
// withheld singles and the survey licence lines the API sends.

const SRC = readFileSync(
    fileURLToPath(new URL('../../app/client/static/js/reports.js', import.meta.url)), 'utf8');

function loadPage() {
    const window = {};
    const document = { addEventListener() {}, querySelector() { return null; } };
    vm.runInNewContext(SRC, { window, document, console });
    return window.reportsPage;
}

const TIERS = {
    survey: { label: 'Survey lidar', color: '#0072B2', hint: 'lidar' },
    corroborated: { label: 'Corroborated', color: '#009E73', hint: 'two' },
    tag: { label: 'OSM tag', color: '#56B4E9', hint: 'tag' },
    single: { label: 'Single reading', color: '#D55E00', hint: 'one' },
    prior: { label: 'Prior estimate', color: '#999999', hint: 'prior' },
};

describe('reports tierSummary', () => {
    const { tierSummary } = loadPage().fmt;

    it('shows counts, the verified share and the no-survey line', () => {
        const html = tierSummary({
            schema_version: 3, tiers: TIERS, n_verified: 32, n_withheld: 0,
            tier_counts: { survey: 0, corroborated: 32, tag: 143, single: 223, prior: 248 },
            survey: { providers: {}, attributions: [] },
        });
        expect(html).toContain('0 survey, 32 corroborated of 646');
        expect(html).toContain('Single reading <b>223</b>');
        expect(html).toContain('No survey heights in this run.');
        expect(html).not.toContain('withheld');
    });

    it('prints every survey attribution and the withheld count', () => {
        const html = tierSummary({
            schema_version: 3, tiers: TIERS, n_verified: 5, n_withheld: 1,
            tier_counts: { survey: 5, corroborated: 0, tag: 0, single: 1, prior: 4 },
            survey: { providers: { usgs_3dep: 5 },
                      attributions: ['Survey heights: USGS 3DEP lidar. Public domain.'] },
        });
        expect(html).toContain('5 survey, 0 corroborated of 10');
        expect(html).toContain('Public domain.');
        expect(html).toContain('1 single reading withheld for the prior');
    });

    it('is empty without tier counts and flags schema 1 files', () => {
        expect(tierSummary({})).toBe('');
        expect(tierSummary({ tier_counts: { unlabelled: 3 }, n_verified: 0 }))
            .toContain('before verification tiers');
    });
});

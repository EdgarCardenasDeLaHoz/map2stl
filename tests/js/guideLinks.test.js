/**
 * guideLinks.test.js — deep-link parsing for the Guides page
 * (app/client/static/js/modules/ui/guide-links.js).
 */
import { describe, it, expect } from 'vitest';
import {
    guideHref, guideLinkTarget, parseGuideLocation,
} from '../../app/client/static/js/modules/ui/guide-links.js';

describe('parseGuideLocation', () => {
    it('reads slug and anchor from the hash form', () => {
        expect(parseGuideLocation('/guides', '#city-stl-and-puzzle-sop/step-4'))
            .toEqual({ slug: 'city-stl-and-puzzle-sop', anchor: 'step-4' });
    });

    it('reads the slug from the path and the anchor from the hash', () => {
        expect(parseGuideLocation('/guides/large-region-sop', '#5-known-limits'))
            .toEqual({ slug: 'large-region-sop', anchor: '5-known-limits' });
    });

    it('treats a bare hash on /guides as a slug (the page falls back to an anchor if unknown)', () => {
        expect(parseGuideLocation('/guides', '#large-region-sop'))
            .toEqual({ slug: 'large-region-sop', anchor: null });
    });

    it('returns nulls for the plain page and ignores the image route', () => {
        expect(parseGuideLocation('/guides', '')).toEqual({ slug: null, anchor: null });
        expect(parseGuideLocation('/guides/img', '')).toEqual({ slug: null, anchor: null });
    });

    it('rejects traversal-looking slugs', () => {
        expect(parseGuideLocation('/guides/..', '').slug).toBeNull();
        expect(parseGuideLocation('/guides', '#../x/y').slug).toBeNull();
    });

    it('decodes encoded anchors', () => {
        expect(parseGuideLocation('/guides', '#a/step%2D2').anchor).toBe('step-2');
    });
});

describe('guideHref', () => {
    it('builds the hash form, round-tripping through the parser', () => {
        const href = guideHref('city-stl-and-puzzle-sop', 'step-6');
        expect(href).toBe('/guides#city-stl-and-puzzle-sop/step-6');
        const [path, hash] = href.split('#');
        expect(parseGuideLocation(path, `#${hash}`))
            .toEqual({ slug: 'city-stl-and-puzzle-sop', anchor: 'step-6' });
    });

    it('omits the anchor when none is given', () => {
        expect(guideHref('large-region-sop')).toBe('/guides#large-region-sop');
    });
});

describe('guideLinkTarget', () => {
    it('recognises server-rewritten cross-links', () => {
        expect(guideLinkTarget('/guides/large-region-sop#5-known-limits'))
            .toEqual({ slug: 'large-region-sop', anchor: '5-known-limits' });
        expect(guideLinkTarget('/guides/large-region-sop'))
            .toEqual({ slug: 'large-region-sop', anchor: null });
    });

    it('ignores images, external and same-page links', () => {
        expect(guideLinkTarget('/guides/img/city/01-x.png')).toBeNull();
        expect(guideLinkTarget('https://example.com')).toBeNull();
        expect(guideLinkTarget('#step-1')).toBeNull();
        expect(guideLinkTarget('')).toBeNull();
    });
});

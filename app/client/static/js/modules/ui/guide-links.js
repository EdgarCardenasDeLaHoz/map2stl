/**
 * guide-links.js — URLs of the in-app Guides page (/guides, app/server/routers/guides.py).
 *
 * Pure helpers shared by the page script (static/js/guides.js) and the Vue "Guide" links in
 * the Edit and Extrude panels. Two deep-link forms are accepted:
 *
 *   /guides#<slug>/<anchor>        (the form the page writes back into the address bar)
 *   /guides/<slug>#<anchor>        (what the server rewrites SOP cross-links to)
 */

const SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

/**
 * Which guide and anchor a location points at.
 * @param {string} pathname  e.g. "/guides/city-stl-and-puzzle-sop"
 * @param {string} hash      e.g. "#step-4" or "#city-stl-and-puzzle-sop/step-4"
 * @returns {{slug: string|null, anchor: string|null}}
 */
export function parseGuideLocation(pathname, hash) {
    let slug = null;
    let anchor = null;
    const m = /^\/guides\/([^/]+)\/?$/.exec(pathname || '');
    if (m && m[1] !== 'img' && SLUG_RE.test(decodeURIComponent(m[1]))) {
        slug = decodeURIComponent(m[1]);
    }
    const h = decodeURIComponent((hash || '').replace(/^#/, ''));
    if (h) {
        const slash = h.indexOf('/');
        if (slash >= 0) {
            const head = h.slice(0, slash);
            if (SLUG_RE.test(head)) {
                slug = head;
                anchor = h.slice(slash + 1) || null;
            } else {
                anchor = h;
            }
        } else if (slug) {
            anchor = h;
        } else if (SLUG_RE.test(h)) {
            // "#large-region-sop" alone: a guide, not an anchor in the default guide.
            slug = h;
        }
    }
    return { slug, anchor };
}

/**
 * Canonical link to a guide, optionally at an anchor: /guides#<slug>[/<anchor>].
 * @param {string} slug
 * @param {string} [anchor]
 */
export function guideHref(slug, anchor) {
    const tail = anchor ? `/${encodeURIComponent(anchor)}` : '';
    return `/guides#${encodeURIComponent(slug)}${tail}`;
}

/**
 * If ``href`` (an <a> href as written in the rendered guide) points at another guide page,
 * return that target; otherwise null (external link, image, same-page anchor).
 * @param {string} href
 */
export function guideLinkTarget(href) {
    if (!href || !href.startsWith('/guides/')) return null;
    const [path, frag = ''] = href.split('#');
    const { slug, anchor } = parseGuideLocation(path, frag ? `#${frag}` : '');
    return slug ? { slug, anchor } : null;
}

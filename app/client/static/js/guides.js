/**
 * guides.js — the /guides page: SOPs from docs/sop/*.md, rendered by the server.
 *
 * Loaded as an ES module by templates/guides.html. GET /api/guides lists the guides and
 * GET /api/guides/{slug} returns {title, html, toc}; the HTML is already rewritten server-side
 * (images under /guides/img/, cross-links to /guides/<slug>), so nothing is parsed here.
 *
 * The address bar is kept at /guides#<slug>/<anchor> as the reader moves, so any position can be
 * copied as a link. Clicking a screenshot opens it full size (click again or Esc to close;
 * click the enlarged image to toggle fit / 1:1).
 */
import { guideHref, guideLinkTarget, parseGuideLocation } from './modules/ui/guide-links.js';

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = { guides: [], current: null, observer: null };
window.guidesPage = state;

async function getJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.json();
}

function renderList() {
    $('#guideList').innerHTML = state.guides.map((g) => `
        <li><a href="${guideHref(g.slug)}" data-slug="${esc(g.slug)}"
               class="${g.slug === state.current ? 'active' : ''}" title="${esc(g.summary)}">
            <div class="g-title">${esc(g.title.replace(/^SOP\s*[—-]\s*/, ''))}</div>
            <div class="g-summary">${esc(g.summary)}</div>
        </a></li>`).join('');
}

function renderToc(toc) {
    $('#toc').innerHTML = toc.map((t) => `
        <li class="lvl-${t.level}${t.id.startsWith('step-') ? ' step' : ''}">
            <a href="${guideHref(state.current, t.id)}" data-anchor="${esc(t.id)}">${esc(t.text)}</a>
        </li>`).join('');
}

function wrapTables(root) {
    root.querySelectorAll('table').forEach((t) => {
        const wrap = document.createElement('div');
        wrap.className = 'table-wrap';
        t.replaceWith(wrap);
        wrap.appendChild(t);
    });
}

function scrollToAnchor(anchor) {
    if (!anchor) { window.scrollTo(0, 0); return; }
    const el = document.getElementById(anchor);
    // Instant, not smooth: a smooth scroll is dropped when the drawer closes in the same click.
    if (el) el.scrollIntoView({ block: 'start' });
}

/** Highlight the TOC entry of the section being read. */
function trackSections(toc) {
    state.observer?.disconnect();
    const links = new Map([...document.querySelectorAll('#toc a')].map((a) => [a.dataset.anchor, a]));
    const targets = toc.map((t) => document.getElementById(t.id)).filter(Boolean);
    const visible = new Set();
    state.observer = new IntersectionObserver((entries) => {
        entries.forEach((e) => (e.isIntersecting ? visible.add(e.target) : visible.delete(e.target)));
        const top = targets.find((t) => visible.has(t));
        if (!top) return;
        links.forEach((a) => a.classList.toggle('current', a.dataset.anchor === top.id));
    }, { rootMargin: '0px 0px -70% 0px' });
    targets.forEach((t) => state.observer.observe(t));
}

async function openGuide(slug, anchor, { push = true } = {}) {
    const article = $('#guide');
    if (slug !== state.current) {
        article.innerHTML = '<p class="status">Loading…</p>';
        try {
            const doc = await getJson(`/api/guides/${encodeURIComponent(slug)}`);
            state.current = slug;
            article.innerHTML = doc.html;
            wrapTables(article);
            renderToc(doc.toc);
            renderList();
            document.title = `${doc.title.replace(/^SOP\s*[—-]\s*/, '')} — Guides — map2stl`;
            trackSections(doc.toc);
        } catch (e) {
            article.innerHTML = `<p class="status error">Could not load guide “${esc(slug)}”: ${esc(e.message)}</p>`;
            return;
        }
    }
    const url = guideHref(slug, anchor);
    if (push && location.pathname + location.hash !== url) history.pushState(null, '', url);
    else if (!push) history.replaceState(null, '', url);
    scrollToAnchor(anchor);
}

/** Resolve the current URL to a guide; "#step-4" alone means an anchor in the current guide. */
function fromLocation() {
    let { slug, anchor } = parseGuideLocation(location.pathname, location.hash);
    const known = new Set(state.guides.map((g) => g.slug));
    if (slug && !known.has(slug) && !anchor && !location.pathname.startsWith('/guides/')) {
        anchor = slug;
        slug = null;
    }
    return { slug: slug || state.current || state.guides[0]?.slug, anchor };
}

// --- lightbox ---------------------------------------------------------------

function openLightbox(img) {
    const lb = $('#lightbox');
    const big = lb.querySelector('img');
    big.src = img.currentSrc || img.src;
    big.alt = img.alt;
    const caption = img.parentElement?.querySelector(':scope > em');
    lb.querySelector('.lb-caption').textContent = caption ? caption.textContent : img.alt;
    lb.classList.add('fit', 'open');
    lb.querySelector('.lb-close').focus();
}
function closeLightbox() { $('#lightbox').classList.remove('open'); }

// --- events -----------------------------------------------------------------

document.addEventListener('click', (e) => {
    const img = e.target.closest('article img');
    if (img) { openLightbox(img); return; }

    const lb = e.target.closest('#lightbox');
    if (lb) {
        if (e.target.tagName === 'IMG') lb.classList.toggle('fit');
        else closeLightbox();
        return;
    }

    const a = e.target.closest('a');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const href = a.getAttribute('href') || '';
    let target = null;
    if (a.dataset.slug) target = { slug: a.dataset.slug, anchor: null };
    else if (a.dataset.anchor) target = { slug: state.current, anchor: a.dataset.anchor };
    else if (href.startsWith('#')) target = { slug: state.current, anchor: href.slice(1) };
    else target = guideLinkTarget(href);
    if (!target) return;
    e.preventDefault();
    document.body.classList.remove('toc-open');
    openGuide(target.slug, target.anchor);
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeLightbox();
        document.body.classList.remove('toc-open');
    }
});

$('#tocToggle').addEventListener('click', () => {
    const open = document.body.classList.toggle('toc-open');
    $('#tocToggle').setAttribute('aria-expanded', String(open));
});

window.addEventListener('popstate', () => {
    const { slug, anchor } = fromLocation();
    if (slug) openGuide(slug, anchor, { push: false });
});

(async () => {
    try {
        state.guides = await getJson('/api/guides');
    } catch (e) {
        $('#guide').innerHTML = `<p class="status error">Could not list guides: ${esc(e.message)}</p>`;
        return;
    }
    if (!state.guides.length) {
        $('#guide').innerHTML = '<p class="status">No guides in docs/sop/ yet.</p>';
        return;
    }
    renderList();
    const { slug, anchor } = fromLocation();
    await openGuide(slug, anchor, { push: false });
})();

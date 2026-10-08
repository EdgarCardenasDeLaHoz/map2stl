/**
 * reports.js — the /reports pipeline results browser.
 *
 * Loaded as a plain <script> by templates/reports.html. All state lives on window.reportsPage so
 * the page can be poked at from the console while judging a batch.
 *
 * Everything comes from GET /api/reports/index (skyline artifacts) and GET
 * /api/reports/registration (plate-registration reports and align-tool packs, F-REGION 5), which
 * scan the report directories on each request. That is deliberately not cached here: the point of the page is to look at a batch that
 * has just finished, and the Rescan button has to be able to show it.
 */

window.reportsPage = (() => {
  const state = {
    data: null,
    selection: { kind: 'all', region: null, url: null, label: '' },
    tab: 'overview',
    quality: new Set(['good', 'medium', 'weak']),
    search: '',
    heights: {},   // region dir -> summary from /api/reports/heights
    registration: null,   // /api/reports/registration, loaded beside the index
  };

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const fmtDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 16).replace('T', ' ');
  };
  const fmtSize = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB'
    : (n / 1024).toFixed(0) + ' KB');

  // --- data ------------------------------------------------------------------

  async function loadRegistration() {
    try {
      const res = await fetch('/api/reports/registration');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.registration = await res.json();
    } catch (e) {
      state.registration = { error: e.message, reports: [], packs: [], roots: [] };
    }
  }

  async function load() {
    $('#pane-overview').innerHTML = '<div class="empty">Scanning report directories…</div>';
    const reg = loadRegistration();
    try {
      const res = await fetch('/api/reports/index');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
    } catch (e) {
      $('#pane-overview').innerHTML =
        `<div class="empty err">Could not load the inventory: ${esc(e.message)}</div>`;
      return;
    }
    await reg;
    renderTotals();
    renderSidebar();
    renderActive();
  }

  function regions() {
    return (state.data && state.data.regions) || [];
  }

  function currentRegions() {
    if (state.selection.kind === 'region') {
      return regions().filter((r) => r.dir === state.selection.region);
    }
    return regions();
  }

  /** Every seed row in scope, after the quality filter and the sidebar search. */
  function currentRows() {
    const q = state.search.toLowerCase();
    const out = [];
    for (const region of currentRegions()) {
      for (const row of region.rows) {
        if (!state.quality.has(row.quality)) continue;
        if (q && !(`${region.name} ${row.seed}`.toLowerCase().includes(q))) continue;
        out.push(row);
      }
    }
    return out;
  }

  // --- header ----------------------------------------------------------------

  function renderTotals() {
    const t = state.data.totals;
    $('#totals').innerHTML = [
      ['regions', t.regions], ['seeds', t.seeds], ['buildings', t.buildings],
      ['pano renders', t.pano_images], ['view frames', t.view_images],
    ].map(([k, v]) => `<span class="chip"><b>${v.toLocaleString()}</b> ${k}</span>`).join('')
      + `<span class="chip"><b class="q-good">${t.good}</b> / `
      + `<b class="q-medium">${t.medium}</b> / <b class="q-weak">${t.weak}</b> seeds</span>`;
  }

  // --- sidebar ---------------------------------------------------------------

  function renderSidebar() {
    const d = state.data;
    const q = state.search.toLowerCase();
    const match = (name) => !q || name.toLowerCase().includes(q);
    const sel = state.selection;

    const item = (label, sub, attrs, active) =>
      `<div class="navitem${active ? ' active' : ''}" ${attrs}>`
      + `<span>${esc(label)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</div>`;

    let html = '<input type="search" id="search" placeholder="Filter regions and seeds"'
      + ` value="${esc(state.search)}">`;

    html += item('All regions', String(d.totals.regions),
      'data-all="1"', sel.kind === 'all');

    html += '<div class="sec">Region reports</div>';
    const shown = d.regions.filter((r) => match(r.name)
      || r.rows.some((row) => match(row.seed)));
    html += shown.length ? shown.map((r) => item(
      r.name, `${r.rows.length || r.seed_pages}`,
      `data-region="${esc(r.dir)}"`,
      sel.kind === 'region' && sel.region === r.dir)).join('')
      : '<div class="empty">no match</div>';

    if (d.height_reports.length) {
      html += '<div class="sec">Height reports</div>';
      html += d.height_reports.filter((h) => match(h.name)).map((h) => item(
        h.name, fmtSize(h.size),
        `data-file="${esc(h.url)}" data-label="${esc(h.name)} height report"`,
        sel.url === h.url)).join('');
    }
    if (d.traces.length) {
      html += '<div class="sec">Height traces</div>';
      html += d.traces.filter((t) => match(t.name)).map((t) => item(
        `${t.region} · ${t.feature}`, 'json',
        `data-file="${esc(t.url)}" data-label="${esc(t.name)} trace"`,
        sel.url === t.url)).join('');
    }
    if (d.pdfs.length) {
      html += '<div class="sec">PDFs</div>';
      html += d.pdfs.filter((p) => match(p.name)).map((p) => item(
        p.name, fmtSize(p.size),
        `data-file="${esc(p.url)}" data-label="${esc(p.name)}"`,
        sel.url === p.url)).join('');
    }
    const reg = state.registration;
    if (reg && reg.reports && reg.reports.length) {
      html += '<div class="sec">Registration reports</div>';
      html += reg.reports.filter((r) => match(`${r.root} ${r.name}`)).map((r) => item(
        r.name, r.root,
        `data-file="${esc(r.index_url)}" data-label="${esc(r.root)} / ${esc(r.name)}"`,
        sel.url === r.index_url)).join('');
    }

    if (d.legacy_landing_url) {
      html += '<div class="sec">Generated landing page</div>';
      html += item('build_landing_page.py output', 'static',
        `data-file="${esc(d.legacy_landing_url)}" data-label="generated landing page"`,
        sel.url === d.legacy_landing_url);
    }

    $('#sidebar').innerHTML = html;
  }

  // --- overview --------------------------------------------------------------

  function qbar(qual) {
    const total = (qual.good + qual.medium + qual.weak) || 1;
    const pct = (n) => `${(100 * n / total).toFixed(1)}%`;
    return '<div class="qbar">'
      + `<i class="good" style="width:${pct(qual.good)}"></i>`
      + `<i class="medium" style="width:${pct(qual.medium)}"></i>`
      + `<i class="weak" style="width:${pct(qual.weak)}"></i></div>`;
  }

  function detClass(n) {
    if (n < 10) return ' class="num warn"';
    if (n < 20) return ' class="num caution"';
    return ' class="num"';
  }

  function seedTable(rows, withRegion) {
    if (!rows.length) return '<div class="empty">No seeds match the current filter.</div>';
    const head = `<tr><th>seed</th>${withRegion ? '<th>region</th>' : ''}<th>source</th>`
      + '<th>detected</th><th>matched</th><th>match rate</th><th>coverage</th>'
      + '<th>quality</th></tr>';
    const body = rows.map((r) => `<tr data-file="${esc(r.url)}"`
      + ` data-label="${esc(r.region)} / ${esc(r.seed)}">`
      + `<td>${esc(r.seed)}</td>`
      + (withRegion ? `<td>${esc(r.region)}</td>` : '')
      + `<td>${esc(r.source)}</td>`
      + `<td${detClass(r.detected)}>${r.detected}</td>`
      + `<td class="num">${r.matched}</td>`
      + `<td class="num">${esc(r.match_rate)}</td>`
      + `<td class="num">${r.coverage}</td>`
      + `<td class="q-${esc(r.quality)}">${esc(r.quality_label)}</td></tr>`).join('');
    return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
  }

  function renderOverview() {
    const pane = $('#pane-overview');
    if (state.selection.kind === 'region') return renderRegionOverview(pane);

    const rs = regions();
    if (!rs.length) {
      pane.innerHTML = '<div class="empty">No region reports on disk yet. '
        + 'Run <code>scripts/08_region_skyline_pdf.py</code> to produce one.</div>';
      return;
    }
    pane.innerHTML = `<h2>All regions</h2>`
      + `<div class="meta">Scanned ${esc(fmtDate(state.data.generated))} UTC. `
      + 'Click a region for its seeds, panoramas and rendered report.</div>'
      + '<div class="cards">' + rs.map((r) => `<div class="card" data-region="${esc(r.dir)}">`
        + `<h4>${esc(r.name)}</h4>${qbar(r.quality)}`
        + `<div class="nums">${r.rows.length} seeds · ${r.buildings} buildings · `
        + `${r.pano_images} pano · ${r.view_images} views</div>`
        + `<div class="nums">${esc(fmtDate(r.modified))}`
        + `${r.heights_json ? ' · heights.json' : ''}</div></div>`).join('')
      + '</div>'
      + '<h3>All seeds</h3>' + seedTable(currentRows(), true);
  }

  function renderRegionOverview(pane) {
    const r = currentRegions()[0];
    if (!r) { pane.innerHTML = '<div class="empty">Region not found.</div>'; return; }
    let html = `<h2>${esc(r.name)}</h2>`
      + `<div class="meta">${r.rows.length} seeds · ${r.buildings} aggregated buildings · `
      + `${r.pano_images} pano renders · ${r.view_images} view frames · `
      + `rendered ${esc(fmtDate(r.modified))}`
      + (r.index_url ? ` · <a href="${esc(r.index_url)}" target="_blank">open report</a>` : '')
      + '</div>';

    const h = state.heights[r.dir];
    if (h && !h.error) {
      const srcs = Object.entries(h.height_sources || {})
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${esc(k)} ${v}`).join(' · ');
      const nKnown = h.n_known_heights ?? (Array.isArray(h.known_heights)
        ? h.known_heights.length : h.known_heights);
      html += '<h3>heights.json</h3>'
        + `<div class="meta">${h.n_buildings} buildings · ${nKnown} with a known height`
        + ` · p10 ${h.height_p10} m · median ${h.height_median} m · p90 ${h.height_p90} m`
        + ` · max ${h.height_max} m<br>sources: ${srcs}`
        + ` · <a href="${esc(r.heights_json)}" target="_blank">raw</a></div>`;
      html += tierSummary(h);
    }

    if (r.screening_map) {
      html += '<h3>Seed screening map</h3><div class="strip"><div class="shot">'
        + `<img loading="lazy" src="${esc(r.screening_map)}" alt="screening map"`
        + ` data-cap="${esc(r.name)} screening map"></div></div>`;
    }
    if (r.web_images.length) {
      html += '<h3>Web skyline sources</h3><div class="gallery">'
        + r.web_images.map((u) => `<img loading="lazy" src="${esc(u)}" alt="web skyline"`
          + ` data-cap="${esc(r.name)} web source">`).join('') + '</div>';
    }
    html += '<h3>Seeds</h3>' + seedTable(currentRows(), false);
    pane.innerHTML = html;

    if (r.heights_json && !state.heights[r.dir]) loadHeights(r.dir);
  }

  // Verification tiers (F-SKY26 2f): the same labels, colours and survey licence lines as the
  // HTML report and the PDF (city2stl/skyline/tier_display.py, sent by /api/reports/heights).
  function tierSummary(h) {
    const counts = h.tier_counts || {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (!total) return '';
    const tiers = h.tiers || {};
    const label = (t) => (tiers[t] && tiers[t].label) || t;
    const color = (t) => (tiers[t] && tiers[t].color) || '#ccc';
    const pct = (n) => Math.round((100 * n) / total);
    const entries = Object.entries(counts);
    const bar = entries.filter(([, n]) => n > 0).map(([t, n]) => `<span style="flex:${n};`
      + `background:${esc(color(t))}" title="${esc(label(t))}: ${n}"></span>`).join('');
    const chips = entries.map(([t, n]) => `<span class="tchip" title="${esc((tiers[t] || {}).hint || '')}">`
      + `<span class="tsw" style="background:${esc(color(t))}"></span>${esc(label(t))} `
      + `<b>${n}</b> <span class="muted">(${pct(n)} %)</span></span>`).join('');
    const nVer = h.n_verified || 0;
    const survey = (h.survey && h.survey.attributions) || [];
    const attr = survey.length
      ? survey.map((a) => `<div class="attr">${esc(a)}</div>`).join('')
      : '<div class="attr">No survey heights in this run.</div>';
    const withheld = h.n_withheld
      ? ` · ${h.n_withheld} single ${h.n_withheld === 1 ? 'reading' : 'readings'} withheld for the prior`
      : '';
    const legacy = (h.schema_version || 1) < 2
      ? '<div class="attr">Written before verification tiers existed.</div>' : '';
    return '<h3>Verification</h3>'
      + `<div class="meta"><b class="ink">${nVer} of ${total} verified</b> (${pct(nVer)} %)`
      + ` · ${total - nVer} unverified${withheld}</div>`
      + `<div class="tierbar">${bar}</div><div class="tchips">${chips}</div>${attr}${legacy}`;
  }

  async function loadHeights(dir) {
    state.heights[dir] = { loading: true };
    try {
      const res = await fetch(`/api/reports/heights/${encodeURIComponent(dir)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.heights[dir] = await res.json();
    } catch (e) {
      state.heights[dir] = { error: e.message };
      return;
    }
    if (state.tab === 'overview') renderOverview();
  }

  // --- registration ----------------------------------------------------------

  const fmtNum = (v, digits = 2) => (typeof v === 'number' && Number.isFinite(v)
    ? v.toFixed(digits) : '—');

  function renderRegistration() {
    const pane = $('#pane-registration');
    const reg = state.registration;
    if (!reg) { pane.innerHTML = '<div class="empty">Loading…</div>'; return; }
    if (reg.error) {
      pane.innerHTML = `<div class="empty err">Could not load registration reports: ${esc(reg.error)}</div>`;
      return;
    }
    const q = state.search.toLowerCase();
    const match = (name) => !q || String(name || '').toLowerCase().includes(q);
    const roots = reg.roots.map((r) => `<code>${esc(r.key)}</code>${r.exists ? '' : ' (missing)'}`)
      .join(' · ');
    const verdicts = Object.entries(reg.totals.verdicts || {})
      .map(([k, v]) => `<span class="v-${esc(k)}">${esc(k)} ${v}</span>`).join(' · ');

    let html = '<h2>Plate registration</h2>'
      + `<div class="meta">${reg.totals.reports} reports · ${reg.totals.packs} align packs `
      + `(${verdicts || 'no verdicts'}) · roots: ${roots}. Read-only; place and verify plates `
      + 'from the app (Composite tab, Plate registration).</div>';

    const packs = reg.packs.filter((p) => match(`${p.slug} ${p.city}`));
    html += '<h3>Align-tool packs</h3>';
    html += packs.length ? '<table><thead><tr><th>pack</th><th>verdict</th><th>placement</th>'
      + '<th>refinement</th><th>cell</th><th>rasters</th><th></th></tr></thead><tbody>'
      + packs.map((p) => {
        const v = p.registration || {};
        const sp = p.street_placement || {};
        const rf = p.refinement || {};
        const status = v.status || 'unchecked';
        const placement = p.street_placement
          ? `${sp.confident ? 'confident' : (sp.position_confident ? 'position only' : 'unsure')}`
            + ` · moved ${fmtNum(sp.moved_m, 0)} m · ×${fmtNum(sp.size, 3)}`
          : (p.guess ? esc(p.guess.source || '') : '—');
        const thumbs = (p.images || []).slice(0, 4).map((im) => `<img loading="lazy" `
          + `src="${esc(im.url)}" alt="${esc(im.name)}" data-cap="${esc(p.slug)} ${esc(im.name)}">`)
          .join('');
        return `<tr><td><b>${esc(p.city || p.slug)}</b><br><span class="meta">${esc(p.slug)}</span></td>`
          + `<td class="v-${esc(status)}">${esc(status)}${v.lead != null ? ` (lead ${fmtNum(v.lead)})` : ''}</td>`
          + `<td>${placement}</td>`
          + `<td>${p.refinement ? `${rf.accepted ? 'accepted' : 'rejected'} · r ${fmtNum(rf.r)}` : '—'}</td>`
          + `<td class="num">${fmtNum(p.cell_size_m, 1)} m</td>`
          + `<td><div class="packthumbs">${thumbs}</div></td>`
          + `<td><a href="${esc(p.meta_url)}" target="_blank">meta</a>`
          + (p.placement_url ? ` · <a href="${esc(p.placement_url)}" target="_blank">placement</a>` : '')
          + '</td></tr>';
      }).join('') + '</tbody></table>'
      : '<div class="empty">No align packs on disk.</div>';

    const reports = reg.reports.filter((r) => match(`${r.root} ${r.name} ${r.title}`));
    html += '<h3>Registration reports</h3>';
    html += reports.length ? '<table><thead><tr><th>report</th><th>root</th><th>plots</th>'
      + '<th>written</th><th></th></tr></thead><tbody>'
      + reports.map((r) => `<tr data-file="${esc(r.index_url)}" data-label="${esc(r.root)} / ${esc(r.name)}">`
        + `<td>${esc(r.title || r.name)}</td><td>${esc(r.root)}</td>`
        + `<td class="num">${r.images}</td><td>${esc(fmtDate(r.modified))}</td>`
        + `<td>${r.summary_url ? `<a href="${esc(r.summary_url)}" target="_blank">summary</a>` : ''}</td></tr>`)
        .join('') + '</tbody></table>'
      : '<div class="empty">No registration reports under the configured roots.</div>';
    pane.innerHTML = html;
  }

  // --- galleries -------------------------------------------------------------

  /** The 5:1 panorama strips, in the order the pipeline produces them. */
  const PANO_WIDE = [
    ['pano', 'stitched 360'],
    ['pano_seg', 'SegFormer mask'],
    ['pano_depth', 'depth'],
    ['pano_scan', 'scan'],
  ];
  /** Roughly square, so it sits in the side rail with the minimaps instead. */
  const PANO_SQUARE = [['pano_recon', 'reconstruction']];

  function seedHead(row) {
    return '<div class="seedhead">'
      + `<b>${esc(row.region)} / ${esc(row.seed)}</b>`
      + `<span class="q-${esc(row.quality)}">${esc(row.quality_label)}</span>`
      + `<span class="stat">${row.detected} detected · ${row.matched} matched · `
      + `${esc(row.match_rate)} · ${row.coverage} covered</span>`
      + `<span class="stat"><a href="${esc(row.url)}" target="_blank">seed page</a></span>`
      + '</div>';
  }

  function figure(row, label, url) {
    const cap = `${row.region} / ${row.seed} — ${label}`;
    return `<figure><img loading="lazy" src="${esc(url)}" alt="${esc(cap)}"`
      + ` data-cap="${esc(cap)}"><figcaption>${esc(label)}</figcaption></figure>`;
  }

  function renderPanoramas() {
    const rows = currentRows().filter(
      (r) => Object.keys(r.pano).length || r.minimaps.length);
    const pane = $('#pane-panoramas');
    if (!rows.length) {
      pane.innerHTML = '<div class="empty">No panorama renders for the current selection.</div>';
      return;
    }
    pane.innerHTML = rows.map((row) => {
      const rail = row.minimaps.map((m) => figure(row, m.label, m.url));
      for (const [key, label] of PANO_SQUARE) {
        if (row.pano[key]) rail.push(figure(row, label, row.pano[key]));
      }
      const wide = [];
      for (const [key, label] of PANO_WIDE) {
        if (row.pano[key]) wide.push(figure(row, label, row.pano[key]));
      }
      return '<div class="seedblock">' + seedHead(row)
        + '<div class="panolayout">'
        + `<div class="rail">${rail.join('')}</div>`
        + `<div class="wide">${wide.join('')
          || '<div class="empty">No panorama renders for this seed.</div>'}</div>`
        + '</div></div>';
    }).join('');
  }

  function renderViews() {
    const rows = currentRows().filter((r) => r.views.length);
    const pane = $('#pane-views');
    if (!rows.length) {
      pane.innerHTML = '<div class="empty">No Street View frames for the current selection.</div>';
      return;
    }
    pane.innerHTML = rows.map((row) => {
      const cells = [];
      for (const v of row.views) {
        for (const [key, label] of [['image', ''], ['mask', ' mask'],
        ['depth', ' depth'], ['recon', ' recon']]) {
          if (!v[key]) continue;
          cells.push(figure(row, `view ${v.index}${label}`, v[key]));
        }
      }
      return '<div class="seedblock">' + seedHead(row)
        + `<div class="viewgrid">${cells.join('')}</div></div>`;
    }).join('');
  }

  // --- tabs and selection ----------------------------------------------------

  function renderActive() {
    if (state.tab === 'overview') renderOverview();
    else if (state.tab === 'panoramas') renderPanoramas();
    else if (state.tab === 'views') renderViews();
    else if (state.tab === 'registration') renderRegistration();
    const sel = state.selection;
    $('#scope').textContent = sel.kind === 'region'
      ? `region: ${sel.region}` : (sel.label || 'all regions');
  }

  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tab').forEach(
      (el) => el.classList.toggle('active', el.dataset.tab === tab));
    ['overview', 'panoramas', 'views', 'report', 'registration'].forEach(
      (t) => { $(`#pane-${t}`).hidden = t !== tab; });
    renderActive();
  }

  function selectRegion(dir) {
    state.selection = { kind: 'region', region: dir, url: null, label: dir };
    renderSidebar();
    if (state.tab === 'report') {
      const r = regions().find((x) => x.dir === dir);
      if (r && r.index_url) return openFile(r.index_url, dir);
    }
    renderActive();
  }

  function selectAll() {
    state.selection = { kind: 'all', region: null, url: null, label: '' };
    renderSidebar();
    renderActive();
  }

  /** Show one rendered artifact in the iframe. JSON and PDF render in the browser too. */
  function openFile(url, label) {
    state.selection = { ...state.selection, url, label: label || url };
    $('#frame').src = url;
    setTab('report');
    renderSidebar();
  }

  // --- lightbox --------------------------------------------------------------

  function openLightbox(src, cap) {
    const box = $('#lightbox');
    box.querySelector('img').src = src;
    box.querySelector('.cap').textContent = cap || '';
    box.classList.add('open');
  }

  // --- wiring ----------------------------------------------------------------

  function init() {
    document.querySelectorAll('.tab').forEach(
      (el) => el.addEventListener('click', () => setTab(el.dataset.tab)));

    document.querySelectorAll('.qf').forEach((cb) => cb.addEventListener('change', () => {
      if (cb.checked) state.quality.add(cb.value); else state.quality.delete(cb.value);
      renderActive();
    }));

    $('#reload').addEventListener('click', load);

    $('#sidebar').addEventListener('input', (e) => {
      if (e.target.id !== 'search') return;
      state.search = e.target.value;
      const at = e.target.selectionStart;
      renderSidebar();
      const box = $('#search');
      box.focus();
      box.setSelectionRange(at, at);
      renderActive();
    });

    $('#sidebar').addEventListener('click', (e) => {
      const item = e.target.closest('.navitem');
      if (!item) return;
      if (item.dataset.all) return selectAll();
      if (item.dataset.region) return selectRegion(item.dataset.region);
      if (item.dataset.file) return openFile(item.dataset.file, item.dataset.label);
    });

    document.querySelectorAll('.pane').forEach((pane) => pane.addEventListener('click', (e) => {
      const img = e.target.closest('img[data-cap]');
      if (img) return openLightbox(img.src, img.dataset.cap);
      const card = e.target.closest('.card[data-region]');
      if (card) return selectRegion(card.dataset.region);
      if (e.target.closest('a')) return undefined;   // plain links open in a new tab
      const row = e.target.closest('tr[data-file]');
      if (row) return openFile(row.dataset.file, row.dataset.label);
    }));

    $('#lightbox').addEventListener('click', () => $('#lightbox').classList.remove('open'));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') $('#lightbox').classList.remove('open');
    });

    load();
  }

  document.addEventListener('DOMContentLoaded', init);
  // pure formatters, for tests (tests/js/reportsTiers.test.js)
  state.fmt = { tierSummary };
  return state;
})();

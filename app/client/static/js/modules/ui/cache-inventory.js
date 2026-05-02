/**
 * modules/ui/cache-inventory.js
 *
 * Cache inventory page: renders table and treemap from /api/cache/inventory.
 */

'use strict';

let _cacheInventoryWired = false;
const _cacheInventoryState = {
    payload: null,
    selectedRegion: '__all__',
    sortKey: 'size_bytes',
    sortDir: 'desc',
    collapsedRegions: new Set(),
};

function _formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 ** 3) return `${(n / (1024 ** 2)).toFixed(2)} MB`;
    return `${(n / (1024 ** 3)).toFixed(2)} GB`;
}

function _formatTime(epochSeconds) {
    if (!epochSeconds) return '—';
    const dt = new Date(epochSeconds * 1000);
    return dt.toLocaleString();
}

function _getRegionLabel(file) {
    return file?.region_group || 'Shared / Global';
}

function _getFilteredFiles() {
    const files = Array.isArray(_cacheInventoryState.payload?.files) ? _cacheInventoryState.payload.files : [];
    if (_cacheInventoryState.selectedRegion === '__all__') return files;
    return files.filter(file => _getRegionLabel(file) === _cacheInventoryState.selectedRegion);
}

function _compareValues(left, right) {
    if (typeof left === 'number' && typeof right === 'number') return left - right;
    return String(left || '').localeCompare(String(right || ''), undefined, { numeric: true, sensitivity: 'base' });
}

function _getSortedFiles(files) {
    const sorted = [...files];
    const direction = _cacheInventoryState.sortDir === 'asc' ? 1 : -1;
    sorted.sort((a, b) => {
        const primary = _compareValues(a?.[_cacheInventoryState.sortKey], b?.[_cacheInventoryState.sortKey]);
        if (primary !== 0) return primary * direction;
        return _compareValues(a?.relative_path, b?.relative_path);
    });
    return sorted;
}

function _groupFilesByRegion(files) {
    const groups = new Map();
    for (const file of files) {
        const regionName = _getRegionLabel(file);
        if (!groups.has(regionName)) groups.set(regionName, []);
        groups.get(regionName).push(file);
    }
    return [...groups.entries()].map(([regionName, regionFiles]) => ({
        regionName,
        files: _getSortedFiles(regionFiles),
        totalSizeBytes: regionFiles.reduce((sum, file) => sum + Number(file?.size_bytes || 0), 0),
    })).sort((a, b) => {
        if (_cacheInventoryState.selectedRegion !== '__all__') return 0;
        return a.regionName.localeCompare(b.regionName, undefined, { sensitivity: 'base' });
    });
}

function _buildTreeFromFiles(files) {
    const root = {
        name: 'cache',
        path: 'cache',
        is_dir: true,
        size_bytes: files.reduce((sum, file) => sum + Number(file?.size_bytes || 0), 0),
        file_count: files.length,
        children: [],
    };

    const grouped = _groupFilesByRegion(files);
    for (const group of grouped) {
        const regionNode = {
            name: group.regionName,
            path: group.regionName,
            is_dir: true,
            size_bytes: group.totalSizeBytes,
            file_count: group.files.length,
            children: [],
        };
        const layerMap = new Map();
        for (const file of group.files) {
            const layer = file.namespace || file.root || 'cache';
            if (!layerMap.has(layer)) layerMap.set(layer, []);
            layerMap.get(layer).push(file);
        }
        for (const [layer, layerFiles] of [...layerMap.entries()].sort((a, b) => a[0].localeCompare(b[0], undefined, { sensitivity: 'base' }))) {
            regionNode.children.push({
                name: layer,
                path: `${group.regionName}/${layer}`,
                is_dir: true,
                size_bytes: layerFiles.reduce((sum, file) => sum + Number(file?.size_bytes || 0), 0),
                file_count: layerFiles.length,
                children: layerFiles.map(file => ({
                    name: file.name || '',
                    path: file.relative_path || file.name || '',
                    is_dir: false,
                    size_bytes: Number(file.size_bytes || 0),
                    file_count: 1,
                    mtime: Number(file.mtime || 0),
                    children: [],
                })),
            });
        }
        root.children.push(regionNode);
    }
    return root;
}

function _updateLegend() {
    const legend = document.getElementById('cacheInventoryLegend');
    if (!legend) return;
    legend.textContent = 'Shared / Global covers cache entries reused across regions or files without enough bbox metadata to match a saved region exactly.';
}

function _updateSortButtons() {
    document.querySelectorAll('.cache-sort-btn').forEach((button) => {
        const key = button.getAttribute('data-sort-key');
        const active = key === _cacheInventoryState.sortKey;
        const direction = active ? (_cacheInventoryState.sortDir === 'asc' ? ' ▲' : ' ▼') : '';
        const label = (button.textContent || '').replace(/[ ▲▼]+$/, '');
        button.textContent = `${label}${direction}`;
        button.classList.toggle('active', active);
    });
}

function _populateRegionFilter(payload) {
    const select = document.getElementById('cacheInventoryRegionFilter');
    if (!select) return;
    const current = _cacheInventoryState.selectedRegion;
    const options = ['__all__', ...[...new Set((Array.isArray(payload?.files) ? payload.files : []).map(file => _getRegionLabel(file)))].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))];
    select.innerHTML = options.map(value => {
        const label = value === '__all__' ? 'All Regions' : value;
        return `<option value="${value}">${label}</option>`;
    }).join('');
    select.value = options.includes(current) ? current : '__all__';
    _cacheInventoryState.selectedRegion = select.value;
}

function _renderSummary(payload) {
    const summary = document.getElementById('cacheInventorySummary');
    if (!summary) return;

    const roots = Array.isArray(payload?.roots) ? payload.roots.length : 0;
    const filteredFiles = _getFilteredFiles();
    const files = Number(filteredFiles.length || 0);
    const size = _formatBytes(filteredFiles.reduce((sum, file) => sum + Number(file?.size_bytes || 0), 0));
    const regionBuckets = new Set(filteredFiles.map(file => _getRegionLabel(file))).size;
    summary.textContent = `${files.toLocaleString()} files across ${roots} cache roots, grouped into ${regionBuckets} region buckets, total ${size}`;
}

function _renderTable() {
    const tbody = document.getElementById('cacheInventoryTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const groups = _groupFilesByRegion(_getFilteredFiles());
    if (!groups.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="cache-empty-row">No cache files match this filter.</td></tr>';
        return;
    }

    for (const group of groups) {
        const collapsed = _cacheInventoryState.collapsedRegions.has(group.regionName);
        const headerRow = document.createElement('tr');
        headerRow.className = 'cache-group-row';
        headerRow.innerHTML = `
            <td colspan="6">
                <button type="button" class="cache-group-toggle" data-region="${group.regionName}">
                    <span class="cache-group-caret">${collapsed ? '▶' : '▼'}</span>
                    <span class="cache-group-name">${group.regionName}</span>
                    <span class="cache-group-meta">${group.files.length} items</span>
                    <span class="cache-group-size">${_formatBytes(group.totalSizeBytes)}</span>
                </button>
            </td>
        `;
        tbody.appendChild(headerRow);

        if (collapsed) continue;

        for (const file of group.files) {
            const tr = document.createElement('tr');
            const rel = file.relative_path || file.name || '';
            // Extract extension (lowercase, no leading dot). Treats compound
            // suffixes like ".tar.gz" as the last segment only — that's fine
            // for the cache, which uses single-suffix files.
            const m = rel.match(/\.([a-z0-9]+)$/i);
            const ext = m ? m[1].toLowerCase() : 'none';
            tr.className = 'cache-file-row';
            tr.setAttribute('data-ext', ext);
            tr.innerHTML = `
                <td>${group.regionName}</td>
                <td>${file.namespace || file.root || ''}</td>
                <td>${file.root || ''}</td>
                <td title="${rel}"><span class="cache-ext-tag">${ext}</span> ${rel}</td>
                <td>${_formatBytes(file.size_bytes)}</td>
                <td>${_formatTime(file.mtime)}</td>
            `;
            tbody.appendChild(tr);
        }
    }
}

// Extension colors must match the .cache-file-row[data-ext=...] CSS rules in
// app.css. Internal nodes (directories) use a neutral grey so the leaves
// stand out as the data-bearing items.
const _EXT_COLORS = {
    json:    '#f4b400',
    npz:     '#4a9fd4',
    tif:     '#b85c5c',
    tiff:    '#b85c5c',
    png:     '#6cbf6c',
    geojson: '#a070d0',
    pkl:     '#d08a4a',
    parquet: '#50c4b8',
    shp:     '#888a30',  // shapefile leftover, helps spot raw downloads
    dbf:     '#888a30',
    shx:     '#888a30',
    prj:     '#888a30',
    cpg:     '#888a30',
    pdf:     '#a35c80',
    zip:     '#506070',
};
const _DIR_COLOR = '#3a3a3a';
const _UNKNOWN_COLOR = '#666';

function _extOf(name) {
    if (!name) return null;
    const m = String(name).toLowerCase().match(/\.([a-z0-9]+)$/);
    return m ? m[1] : null;
}

function _walkTreeToTreemap(node, parentId, out) {
    const nodePath = String(node.path || node.name || '');
    const nodeId = parentId ? `${parentId}/${nodePath}` : nodePath;
    const isLeaf = !Array.isArray(node.children) || node.children.length === 0;
    const ext = isLeaf ? _extOf(node.name || node.path) : null;
    const color = isLeaf
        ? (ext && _EXT_COLORS[ext] ? _EXT_COLORS[ext] : _UNKNOWN_COLOR)
        : _DIR_COLOR;

    out.labels.push(String(node.name || node.path || 'cache'));
    out.parents.push(parentId || '');
    out.values.push(Number(node.size_bytes || 0));
    out.ids.push(nodeId);
    out.colors.push(color);

    const children = Array.isArray(node.children) ? node.children : [];
    for (const child of children) {
        _walkTreeToTreemap(child, nodeId, out);
    }
}

function _renderTreemap() {
    const target = document.getElementById('cacheTreemap');
    if (!target || typeof window.Plotly === 'undefined') return;

    const tree = _buildTreeFromFiles(_getFilteredFiles());
    if (!tree) {
        target.innerHTML = '<div class="cache-empty">No cache tree data available.</div>';
        return;
    }

    const out = { labels: [], parents: [], values: [], ids: [], colors: [] };
    _walkTreeToTreemap(tree, '', out);

    const data = [{
        type: 'treemap',
        labels: out.labels,
        parents: out.parents,
        values: out.values,
        ids: out.ids,
        branchvalues: 'total',
        textinfo: 'label+value',
        texttemplate: '%{label}<br>%{value:.3s}B',
        hovertemplate: '<b>%{label}</b><br>%{value:.3s}B<extra></extra>',
        marker: {
            // Per-leaf colours match the .cache-file-row[data-ext] CSS rules.
            // Directories are neutral grey so leaves stand out as data.
            colors: out.colors,
            line: { width: 0.5, color: '#1f1f1f' },
            pad: { t: 2, l: 2, r: 2, b: 2 },
        },
    }];

    const layout = {
        margin: { l: 8, r: 8, t: 8, b: 8 },
        paper_bgcolor: '#1f1f1f',
        plot_bgcolor: '#1f1f1f',
        font: { color: '#d9d9d9', size: 12 },
    };

    window.Plotly.react(target, data, layout, { responsive: true, displayModeBar: false });
}

function _renderCacheInventory() {
    if (!_cacheInventoryState.payload) return;
    _updateLegend();
    _updateSortButtons();
    _renderSummary(_cacheInventoryState.payload);
    _renderTable();
    _renderTreemap();
}

window.loadCacheInventory = async function loadCacheInventory() {
    const summary = document.getElementById('cacheInventorySummary');
    if (summary) summary.textContent = 'Loading cache inventory...';

    const { data, error } = await window.api.cache.inventory();
    if (error) {
        if (summary) summary.textContent = `Failed to load cache inventory: ${error}`;
        window.showToast?.(`Cache inventory failed: ${error}`, 'error');
        return;
    }

    _cacheInventoryState.payload = data;
    _populateRegionFilter(data);
    _renderCacheInventory();
};

window.setupCacheInventoryView = function setupCacheInventoryView() {
    if (_cacheInventoryWired) return;
    _cacheInventoryWired = true;

    document.getElementById('cacheInventoryRefreshBtn')?.addEventListener('click', () => {
        window.loadCacheInventory?.();
    });
    document.getElementById('cacheInventoryRegionFilter')?.addEventListener('change', (event) => {
        _cacheInventoryState.selectedRegion = event.target.value;
        _renderCacheInventory();
    });
    document.querySelectorAll('.cache-sort-btn').forEach((button) => {
        button.addEventListener('click', () => {
            const key = button.getAttribute('data-sort-key');
            if (!key) return;
            if (_cacheInventoryState.sortKey === key) {
                _cacheInventoryState.sortDir = _cacheInventoryState.sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                _cacheInventoryState.sortKey = key;
                _cacheInventoryState.sortDir = key === 'size_bytes' || key === 'mtime' ? 'desc' : 'asc';
            }
            _renderCacheInventory();
        });
    });
    document.getElementById('cacheInventoryTableBody')?.addEventListener('click', (event) => {
        const toggle = event.target.closest('.cache-group-toggle');
        if (!toggle) return;
        const region = toggle.getAttribute('data-region');
        if (!region) return;
        if (_cacheInventoryState.collapsedRegions.has(region)) _cacheInventoryState.collapsedRegions.delete(region);
        else _cacheInventoryState.collapsedRegions.add(region);
        _renderTable();
    });
};

/**
 * modules/ui-helpers.js — Toast notifications, loading overlays, layer-status UI,
 * collapsible sections, and coordinate search.
 *
 * Loaded as a plain <script> before app.js. All functions exposed on window.*.
 * Reads layer status via window.appState.layerStatus (shared reference with app.js).
 *
 * Public API:
 *   window.showToast(message, type, duration)
 *   window.toggleCollapsible(header)
 *   window.showLoading(container, message)
 *   window.hideLoading(container)
 *   window.setLayerStatus(layer, status)
 *   window.updateLayerStatusUI()
 *   window.updateLayerStatusIndicators()
 *   window.setupCoordinateSearch()
 */

// ============================================================
// TOAST NOTIFICATIONS
// ============================================================

/**
 * Show a brief toast notification in the top-right corner.
 * @param {string} message - Message text to display
 * @param {'success'|'error'|'warning'|'info'} [type='info'] - Visual style
 * @param {number} [duration=3000] - Auto-dismiss delay in milliseconds
 */
window.showToast = function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const icons = {
        success: '✓',
        error: '✕',
        warning: '⚠',
        info: 'ℹ'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${icons[type] || icons.info}</span>
        <span class="toast-message">${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, duration);
};

// ============================================================
// COLLAPSIBLE SECTIONS
// ============================================================

/**
 * Toggle a collapsible section open or closed.
 * Also reinitialises the curve canvas if it was hidden.
 * @param {HTMLElement} header - The collapsible section header element
 */
window.toggleCollapsible = function toggleCollapsible(header) {
    const section = header.closest('.collapsible-section');
    if (section) {
        const wasCollapsed = section.classList.contains('collapsed');
        section.classList.toggle('collapsed');

        // If section is being expanded, reinitialise any canvases inside it
        if (wasCollapsed) {
            setTimeout(() => {
                // Curve editor canvas
                const cc = section.querySelector('#curveCanvas');
                if (cc) {
                    const container = cc.parentElement;
                    if (container.clientWidth > 0 && container.clientHeight > 0) {
                        cc.width  = container.clientWidth;
                        cc.height = container.clientHeight;
                        window.drawCurve?.();
                    }
                }
                // Histogram + colorbar — redraw at current panel width
                if (section.querySelector('#histogram') && window.appState.lastDemData?.values?.length) {
                    window.recolorDEM?.();
                }
            }, 50);
        }
    }
};

// ============================================================
// LOADING OVERLAYS
// ============================================================

/**
 * Show a spinner loading overlay on a container element.
 * Removes any existing overlay first.
 * @param {HTMLElement|string} container - DOM element or element ID
 * @param {string} [message='Loading...'] - Text shown below the spinner
 */
window.showLoading = function showLoading(container, message = 'Loading...') {
    window.hideLoading(container);

    const overlay = document.createElement('div');
    overlay.className = 'loading-overlay';
    overlay.innerHTML = `
        <span class="spinner"></span>
        <p>${message}</p>
    `;

    if (typeof container === 'string') {
        container = document.getElementById(container);
    }

    if (container) {
        container.style.position = 'relative';
        container.appendChild(overlay);
    }
};

/**
 * Remove the loading overlay from a container element.
 * @param {HTMLElement|string} container - DOM element or element ID
 */
window.hideLoading = function hideLoading(container) {
    if (typeof container === 'string') {
        container = document.getElementById(container);
    }

    if (container) {
        const overlay = container.querySelector('.loading-overlay');
        if (overlay) overlay.remove();
    }
};

// ============================================================
// LAYER STATUS
// ============================================================

/**
 * Update a single layer's status and refresh the status UI.
 * Writes to window.appState.layerStatus (shared object reference with app.js).
 * @param {'dem'|'water'|'landCover'} layer - Layer identifier
 * @param {'empty'|'loading'|'loaded'|'error'} status - New status value
 */
window.setLayerStatus = function setLayerStatus(layer, status) {
    if (window.appState?.layerStatus) {
        window.appState.layerStatus[layer] = status;
    }
    window.updateLayerStatusUI();
};

/**
 * Sync all layer status badge elements in the DOM from window.appState.layerStatus.
 */
window.updateLayerStatusUI = function updateLayerStatusUI() {
    const layerStatus = window.appState?.layerStatus || {};

    const statusMap = {
        'dem': 'status-dem',
        'water': 'status-water',
        'landCover': 'status-satellite',
        'combined': 'status-combined'
    };

    const layerStatusMap = {
        'dem': 'dem',
        'water': 'water',
        'satellite': 'landCover',
        'combined': 'combined'
    };

    Object.entries(statusMap).forEach(([layer, elementId]) => {
        const element = document.getElementById(elementId);
        if (element) {
            element.classList.remove('empty', 'loading', 'loaded', 'error');
            const status = layerStatus[layerStatusMap[layer] || layer] || 'empty';
            element.classList.add(status);
        }
    });

    // Update strip button status dots
    const stripDotMap = { 'dem': 'stripDotDem', 'water': 'stripDotWater', 'landCover': 'stripDotLandCover' };
    Object.entries(stripDotMap).forEach(([layer, dotId]) => {
        const dot = document.getElementById(dotId);
        if (dot) {
            dot.classList.remove('loaded', 'loading', 'error');
            const s = layerStatus[layer] || 'empty';
            if (s !== 'empty') dot.classList.add(s);
        }
    });
};

/**
 * Update layer status indicator UI — updates both the new tab-status indicators
 * and the legacy badge system.
 */
window.updateLayerStatusIndicators = function updateLayerStatusIndicators() {
    window.updateLayerStatusUI();

    const layerStatus = window.appState?.layerStatus || {};
    const statusIcons = {
        'empty': '○',
        'loading': '◐',
        'loaded': '●',
        'error': '⚠️',
        'stale': '◔'
    };

    document.querySelectorAll('.layer-tab').forEach(tab => {
        const subtab = tab.dataset.subtab;
        let layerName = subtab;
        if (subtab === 'satellite') layerName = 'landCover';
        if (subtab === 'combined') return;

        const status = layerStatus[layerName] || 'empty';
        let badge = tab.querySelector('.layer-badge');

        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'layer-badge';
            badge.style.cssText = 'margin-left:4px;font-size:10px;';
            tab.appendChild(badge);
        }

        badge.textContent = statusIcons[status];
        badge.title = status;
    });
};

// ============================================================
// COORDINATE SEARCH
// ============================================================

/**
 * Wire the coordinate search input to filter the region list by name.
 * Guards against double-wiring with a _searchWired flag.
 */
window.setupCoordinateSearch = function setupCoordinateSearch() {
    const searchInput = document.getElementById('coordSearch');
    if (!searchInput || searchInput._searchWired) return;
    searchInput._searchWired = true;

    searchInput.addEventListener('input', function () {
        const query = this.value.toLowerCase();
        document.querySelectorAll('.coordinate-item').forEach(item => {
            const name = item.textContent.toLowerCase();
            item.style.display = name.includes(query) ? '' : 'none';
        });
    });
};

// Listen for STATUS_UPDATE events (replaces scattered direct calls)
window.events?.on(window.EV?.STATUS_UPDATE, () => window.updateLayerStatusIndicators());

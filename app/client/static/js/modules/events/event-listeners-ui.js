/**
 * modules/event-listeners-ui.js
 *
 * Resizable settings panel, settings JSON view toggle, and sidebar edit view.
 *
 * Exposes on window:
 *   window._setupResizablePanel()
 *   window._setupSettingsJsonToggle()
 *   window._setupSidebarEditView()
 */

window._setupResizablePanel = function _setupResizablePanel() {
    const resizeHandle = document.getElementById('settingsPanelResizeHandle');
    const rightPanel   = document.getElementById('demRightPanel');
    if (!resizeHandle || !rightPanel) return;

    let resizing = false, startX, startW, rafPending = false;
    resizeHandle.addEventListener('mousedown', e => {
        resizing = true;
        startX   = e.clientX;
        startW   = rightPanel.offsetWidth;
        resizeHandle.classList.add('dragging');
        document.body.style.cursor     = 'col-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
        if (!resizing) return;
        const newW = Math.max(280, Math.min(900, startW + (startX - e.clientX)));
        rightPanel.style.width = newW + 'px';
        if (!rafPending) {
            rafPending = true;
            requestAnimationFrame(() => {
                window.events?.emit(window.EV?.STACKED_UPDATE);
                if (window.appState.lastDemData?.values?.length) window.recolorDEM?.();
                rafPending = false;
            });
        }
    });
    document.addEventListener('mouseup', () => {
        if (!resizing) return;
        resizing = false;
        resizeHandle.classList.remove('dragging');
        document.body.style.cursor     = '';
        document.body.style.userSelect = '';
        try { localStorage.setItem('strm2stl_settingsPanelWidth', rightPanel.offsetWidth); } catch (_) {}
        window.emitStackUpdate();
    });
    try {
        const savedW = localStorage.getItem('strm2stl_settingsPanelWidth');
        if (savedW) rightPanel.style.width = parseInt(savedW) + 'px';
    } catch (_) {}

    let _raf = null;
    new ResizeObserver(() => {
        if (_raf) return;
        _raf = requestAnimationFrame(() => {
            _raf = null;
            const cc = rightPanel.querySelector('#curveCanvas');
            if (cc) {
                const cont = cc.parentElement;
                if (cont.clientWidth > 0 && cont.clientHeight > 0) {
                    cc.width  = cont.clientWidth;
                    cc.height = cont.clientHeight;
                    window.drawCurve?.();
                }
            }
            if (window.appState.lastDemData?.values?.length) window.recolorDEM?.();
        });
    }).observe(rightPanel);
};

window._setupSettingsJsonToggle = function _setupSettingsJsonToggle() {
    document.getElementById('saveRegionSettingsBtn')?.addEventListener('click', () => window.saveRegionSettings?.());

    document.getElementById('clearRegionCacheBtn')?.addEventListener('click', async () => {
        const btn = document.getElementById('clearRegionCacheBtn');
        const bbox = window.appState?.bbox;
        if (!bbox || !bbox.north) {
            window.showToast?.('No region loaded', 'warning');
            return;
        }
        btn.disabled = true;
        btn.textContent = '⏳ Clearing…';
        try {
            const { data, error } = await window.api.cache.clearRegion(bbox);
            if (error) {
                window.showToast?.(`Cache clear failed: ${error}`, 'error');
            } else {
                const n = data?.files_deleted ?? 0;
                window.showToast?.(`Cleared ${n} cached files`, 'success');
            }
        } catch (e) {
            window.showToast?.(`Cache clear error: ${e.message}`, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '🗑️ Clear Cache';
        }
    });

    const jsonToggleBtn    = document.getElementById('jsonViewToggleBtn');
    const jsonView         = document.getElementById('settingsJsonView');
    const demControlsInner = document.getElementById('demControlsInner');
    const settingsSaveRow  = document.getElementById('settingsSaveRow');

    if (jsonToggleBtn && jsonView) {
        let jsonViewOpen = false;
        jsonToggleBtn.addEventListener('click', () => {
            jsonViewOpen = !jsonViewOpen;
            jsonToggleBtn.classList.toggle('active', jsonViewOpen);
            if (jsonViewOpen) {
                const editor = document.getElementById('settingsJsonEditor');
                if (editor) editor.value = JSON.stringify(window.collectAllSettings?.() ?? {}, null, 2);
                jsonView.classList.remove('hidden');
                if (demControlsInner) demControlsInner.classList.add('hidden');
                if (settingsSaveRow) settingsSaveRow.style.display = 'none';
            } else {
                jsonView.classList.add('hidden');
                if (demControlsInner) demControlsInner.classList.remove('hidden');
                if (settingsSaveRow) settingsSaveRow.style.display = '';
                document.getElementById('settingsJsonError')?.classList.add('hidden');
            }
        });
    }

    document.getElementById('applyJsonSettingsBtn')?.addEventListener('click', () => {
        const editor  = document.getElementById('settingsJsonEditor');
        const errorEl = document.getElementById('settingsJsonError');
        try {
            window.applyAllSettings?.(JSON.parse(editor.value));
            document.getElementById('jsonViewToggleBtn')?.click();
            window.showToast?.('Settings applied from JSON', 'success');
        } catch (e) {
            if (errorEl) {
                errorEl.textContent = 'Invalid JSON: ' + e.message;
                errorEl.classList.remove('hidden');
            }
        }
    });

    document.getElementById('cancelJsonSettingsBtn')?.addEventListener('click', () => {
        document.getElementById('jsonViewToggleBtn')?.click();
    });
};

window._setupSidebarEditView = function _setupSidebarEditView() {
    document.getElementById('sbBackBtn')?.addEventListener('click', () => {
        document.getElementById('sidebarEditView')?.classList.add('hidden');
        window._setSidebarViews?.(window.getSidebarState?.());
    });
    document.getElementById('sbReloadBtn')?.addEventListener('click', () => {
        const n = document.getElementById('sbNorth')?.value;
        const s = document.getElementById('sbSouth')?.value;
        const e = document.getElementById('sbEast')?.value;
        const w = document.getElementById('sbWest')?.value;
        if (n != null) window.setBboxInputValues?.(n, s, e, w);
        const nf = parseFloat(n), sf = parseFloat(s),
              ef = parseFloat(e), wf = parseFloat(w);
        if (!isNaN(nf) && !isNaN(sf) && !isNaN(ef) && !isNaN(wf)) {
            const _map = window.getMap?.();
            const _bb = window.getBoundingBox?.();
            if (_bb && _map) _map.removeLayer(_bb);
            const newBb = L.rectangle([[sf, wf], [nf, ef]],
                { color: '#e74c3c', weight: 2, fillOpacity: 0.05 });
            if (_map) newBb.addTo(_map);
            window.setBoundingBox?.(newBb);
        }
        window.loadAllLayers?.();
    });
};

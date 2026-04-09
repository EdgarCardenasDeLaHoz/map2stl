/**
 * modules/event-listeners-map.js
 *
 * Map, DEM display, terrain overlays, grid, globe, regions panel,
 * draw tool, and bbox fine-tune event listeners.
 *
 * Exposes on window:
 *   window._setupMapAndDemListeners()
 *   window._setupBboxListeners()
 */

window._setupMapAndDemListeners = function _setupMapAndDemListeners() {
    function activateDrawTool() {
        const dc = window.getDrawControl?.();
        if (dc && dc._toolbars?.draw) {
            try { dc._toolbars.draw._modes.rectangle.handler.enable(); } catch(e) {}
        }
        const btn = document.getElementById('floatingDrawBtn');
        if (btn) btn.classList.add('drawing');
        window.showToast?.('Draw a rectangle on the map, then enter a name and click Save Region', 'info');
    }

    document.getElementById('demColormap')?.addEventListener('change', () => {
        window._invalidateLutCache?.();
        window.recolorDEM?.();
    });

    const projSelect = document.getElementById('paramProjection');
    if (projSelect) {
        const projDescriptions = {
            'none':       'No correction — raw lat/lon grid displayed as-is.',
            'cosine':     'Horizontal scaling by cos(latitude). Correct east-west distances.',
            'mercator':   'Web Mercator — vertical stretching increases towards poles.',
            'lambert':    'Lambert Cylindrical Equal-Area — preserves area at the cost of shape.',
            'sinusoidal': 'Sinusoidal — each row scaled by cos(lat), centred on meridian.',
        };
        projSelect.addEventListener('change', () => {
            const desc = document.getElementById('projectionDescription');
            if (desc) desc.textContent = projDescriptions[projSelect.value] || '';
            window.recolorDEM?.();
            const _wmd = window.appState.lastWaterMaskData;
            if (_wmd) {
                window.renderWaterMask?.(_wmd);
                window.renderEsaLandCover?.(_wmd);
            }
            // Invalidate city overlay caches so they re-render with new projection
            window._invalidateCityCache?.();
            window.renderCityOverlay?.();
            window.renderCityOnDEM?.();
            // Re-project the city heights raster canvas
            window._reprojectCityRaster?.();
            // Re-project the satellite RGB canvas
            window._reprojectSatelliteImage?.();
            // Recompute composite DEM (applies new projection to source canvas)
            window.computeCompositeDem?.();
            // Update stacked layers view
            requestAnimationFrame(() => window.events?.emit(window.EV?.STACKED_UPDATE));
            // Update print dimensions — projection changes canvas aspect ratio
            requestAnimationFrame(() => window.updatePrintDimensions?.());
        });
    }

    document.getElementById('applyRescaleBtn')?.addEventListener('click', () => {
        const minVal = parseFloat(document.getElementById('rescaleMin').value);
        const maxVal = parseFloat(document.getElementById('rescaleMax').value);
        if (isNaN(minVal) || isNaN(maxVal)) { window.showToast?.('Enter valid min and max values', 'warning'); return; }
        if (minVal >= maxVal) { window.showToast?.('Min must be less than max', 'warning'); return; }
        window.rescaleDEM?.(minVal, maxVal);
    });
    document.getElementById('resetRescaleBtn')?.addEventListener('click', () => window.resetRescale?.());

    document.getElementById('mapTileLayer')?.addEventListener('change', e => {
        window.setTileLayer?.(e.target.value);
        window.showToast?.(`Map style: ${e.target.options[e.target.selectedIndex].text}`, 'info');
    });

    document.getElementById('showTerrainOverlay')?.addEventListener('change', e => {
        window.toggleTerrainOverlay?.(e.target.checked);
        window.updateFloatingTerrainButton?.(e.target.checked);
        window.showToast?.(e.target.checked ? 'Terrain relief enabled' : 'Terrain relief disabled', 'info');
    });
    document.getElementById('floatingTerrainToggle')?.addEventListener('click', () => {
        const cb = document.getElementById('showTerrainOverlay');
        if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
    });

    const genGlobalDemBtn = document.getElementById('genGlobalDemBtn');
    genGlobalDemBtn?.addEventListener('click', async () => {
        const status = document.getElementById('genGlobalDemStatus');
        genGlobalDemBtn.disabled = true;
        if (status) status.textContent = 'Generating…';
        window.showToast?.('Generating terrain cache — this runs once and may take a minute', 'info', 5000);
        try {
            const { error } = await window.api.misc.globalDemOverview(true);
            if (!error) {
                if (status) status.textContent = '✓ Done';
                window.showToast?.('Terrain cache generated', 'success');
            } else {
                if (status) status.textContent = '✗ Failed';
                window.showToast?.('Failed: ' + error, 'error');
            }
        } catch (e) {
            if (status) status.textContent = '✗ Error';
            window.showToast?.('Error generating cache', 'error');
        } finally {
            genGlobalDemBtn.disabled = false;
        }
    });

    function _setLabels(show) {
        window.toggleMapLabels?.(show);
        const btn = document.getElementById('floatingLabelsToggle');
        btn?.classList.toggle('active', show);
        const cb = document.getElementById('showLabelsExplore');
        if (cb) cb.checked = show;
    }
    document.getElementById('floatingLabelsToggle')?.addEventListener('click', () => {
        const cb = document.getElementById('showLabelsExplore');
        _setLabels(cb ? !cb.checked : true);
    });
    document.getElementById('showLabelsExplore')?.addEventListener('change', e => {
        _setLabels(e.target.checked);
    });

    document.getElementById('floatingGridToggle')?.addEventListener('click', () => {
        const cur = window.getMapGridEnabled?.() ?? false;
        const next = !cur;
        window.setMapGridEnabled?.(next);
        window.toggleMapGrid?.(next);
        window.showToast?.(next ? 'Grid enabled' : 'Grid disabled', 'info');
    });

    document.getElementById('floatingGlobeToggle')?.addEventListener('click', () => {
        const gc  = document.getElementById('globeContainer');
        const btn = document.getElementById('floatingGlobeToggle');
        if (gc.classList.contains('hidden')) {
            Object.assign(gc.style, { position: 'absolute', top: '0', left: '0',
                                       width: '100%', height: '100%', zIndex: '500' });
            gc.classList.remove('hidden');
            btn?.classList.add('active');
            window.initGlobe?.();
        } else {
            gc.classList.add('hidden');
            gc.style.position = '';
            btn?.classList.remove('active');
        }
    });

    const floatingRegionsBtn = document.getElementById('floatingRegionsToggle');
    const regionsPanel       = document.getElementById('regionsPanel');
    floatingRegionsBtn?.addEventListener('click', () => {
        regionsPanel?.classList.toggle('hidden');
        floatingRegionsBtn.classList.toggle('active', !regionsPanel?.classList.contains('hidden'));
        if (!regionsPanel?.classList.contains('hidden')) window.populateRegionsPanelTable?.();
    });
    document.getElementById('closeRegionsPanel')?.addEventListener('click', () => {
        regionsPanel?.classList.add('hidden');
        floatingRegionsBtn?.classList.remove('active');
    });

    const mapSettingsBtn   = document.getElementById('floatingMapSettingsBtn');
    const mapSettingsPanel = document.getElementById('mapSettingsPanel');
    mapSettingsBtn?.addEventListener('click', () => {
        mapSettingsPanel?.classList.toggle('hidden');
        mapSettingsBtn.classList.toggle('active', !mapSettingsPanel?.classList.contains('hidden'));
    });
    document.getElementById('closeMapSettingsBtn')?.addEventListener('click', () => {
        mapSettingsPanel?.classList.add('hidden');
        mapSettingsBtn?.classList.remove('active');
    });

    const mapTileLayerExplore = document.getElementById('mapTileLayerExplore');
    const mapTileLayerEdit    = document.getElementById('mapTileLayer');
    mapTileLayerExplore?.addEventListener('change', () => {
        window.setTileLayer?.(mapTileLayerExplore.value);
        if (mapTileLayerEdit) mapTileLayerEdit.value = mapTileLayerExplore.value;
    });
    mapTileLayerEdit?.addEventListener('change', () => {
        if (mapTileLayerExplore) mapTileLayerExplore.value = mapTileLayerEdit.value;
    });

    const terrainCheckboxExplore     = document.getElementById('showTerrainOverlayExplore');
    const terrainRowExplore          = document.getElementById('terrainOpacityRowExplore');
    const terrainOpacityExplore      = document.getElementById('terrainOverlayOpacityExplore');
    const terrainOpacityLabelExplore = document.getElementById('terrainOpacityValueExplore');
    terrainCheckboxExplore?.addEventListener('change', () => {
        const on = terrainCheckboxExplore.checked;
        window.toggleTerrainOverlay?.(on);
        if (terrainRowExplore) terrainRowExplore.style.display = on ? 'flex' : 'none';
        const editCb = document.getElementById('showTerrainOverlay');
        if (editCb) editCb.checked = on;
    });
    terrainOpacityExplore?.addEventListener('input', () => {
        const val = parseInt(terrainOpacityExplore.value);
        window.setTerrainOverlayOpacity?.(val);
        if (terrainOpacityLabelExplore) terrainOpacityLabelExplore.textContent = val + '%';
        const editSlider = document.getElementById('terrainOverlayOpacity');
        if (editSlider) {
            editSlider.value = val;
            document.getElementById('terrainOpacityValue').textContent = val + '%';
        }
    });

    document.getElementById('showGridlinesExplore')?.addEventListener('change', e => {
        window.setMapGridEnabled?.(e.target.checked);
        window.toggleMapGrid?.(e.target.checked);
    });

    document.getElementById('regionsPanelSearch')
        ?.addEventListener('input', () => window.populateRegionsPanelTable?.());

    document.getElementById('regionsPanelNewBtn')?.addEventListener('click', () => {
        window.closeRegionsPanel?.();
        const dc = window.getDrawControl?.();
        if (dc && dc._toolbars?.draw) {
            try { dc._toolbars.draw._modes.rectangle.handler.enable(); } catch(e) {}
        }
        window.showToast?.('Draw a rectangle on the map to create a new region', 'info');
    });

    document.getElementById('floatingDrawBtn')?.addEventListener('click', activateDrawTool);
    document.getElementById('startDrawBtn')?.addEventListener('click', () => {
        activateDrawTool();
        window.switchView?.('map');
    });
    const _map = window.getMap?.();
    if (_map) {
        _map.on(L.Draw.Event.CREATED, () => {
            document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
        });
        _map.on(L.Draw.Event.DRAWSTOP, () => {
            document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
        });
    }

    const gridVisibleCb = document.getElementById('layerGridVisible');
    document.getElementById('layerGridVisible')?.addEventListener('change', () => {
        const gc = document.getElementById('layerGridCanvas');
        if (gc) {
            gc.style.display = gridVisibleCb.checked ? 'block' : 'none';
            if (gridVisibleCb.checked) window.drawLayerGrid?.();
        }
    });
    document.getElementById('layerGridDensity')?.addEventListener('change', () => {
        if (gridVisibleCb?.checked) window.drawLayerGrid?.();
    });
    document.getElementById('gridPixelModeBtn')?.addEventListener('click', () => {
        const btn = document.getElementById('gridPixelModeBtn');
        const on = btn?.classList.toggle('active');
        window.setGridPixelMode?.(!!on);
    });

    document.getElementById('terrainOverlayOpacity')?.addEventListener('input', e => {
        const val = e.target.value;
        document.getElementById('terrainOpacityValue').textContent = `${val}%`;
        window.setTerrainOverlayOpacity?.(val);
    });

    document.getElementById('paramDim')?.addEventListener('input', () => {
        const val = parseInt(document.getElementById('paramDim').value);
        const w   = document.getElementById('demResWarning');
        if (w) w.style.display = val > 500 ? 'block' : 'none';
    });
    document.getElementById('waterResolution')?.addEventListener('change', () => {
        const val = parseInt(document.getElementById('waterResolution').value);
        const w   = document.getElementById('waterResWarning');
        if (w) w.style.display = val >= 500 ? 'block' : 'none';
        window.loadWaterMask?.();
    });
    document.getElementById('loadWaterMaskBtn')?.addEventListener('click', async () => {
        const btn = document.getElementById('loadWaterMaskBtn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
        try { await window.loadWaterMask?.(); }
        finally { if (btn) { btn.disabled = false; btn.textContent = '🌊 Load Water & ESA'; } }
    });
};

window._setupBboxListeners = function _setupBboxListeners() {
    const bboxReloadBtn = document.getElementById('bboxReloadBtn');
    bboxReloadBtn?.addEventListener('click', () => {
        const n = parseFloat(document.getElementById('bboxNorth')?.value);
        const s = parseFloat(document.getElementById('bboxSouth')?.value);
        const e = parseFloat(document.getElementById('bboxEast')?.value);
        const w = parseFloat(document.getElementById('bboxWest')?.value);
        if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
            window.showToast?.('Invalid coordinates', 'error'); return;
        }
        const nc = Math.max(-90,  Math.min(90,  n));
        const sc = Math.max(-90,  Math.min(90,  s));
        const ec = Math.max(-180, Math.min(180, e));
        const wc = Math.max(-180, Math.min(180, w));
        window.setBboxInputValues?.(nc, sc, ec, wc);
        let selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion) selectedRegion = {};
        selectedRegion.north = nc; selectedRegion.south = sc;
        selectedRegion.east  = ec; selectedRegion.west  = wc;
        window.setSelectedRegion?.(selectedRegion);
        window.appState.selectedRegion = selectedRegion;
        window.appState.currentDemBbox = { north: nc, south: sc, east: ec, west: wc };
        const _map = window.getMap?.();
        const _bb = window.getBoundingBox?.();
        if (_bb && _map) _map.removeLayer(_bb);
        const newBb = L.rectangle([[sc, wc], [nc, ec]],
            { color: '#e74c3c', weight: 2, fillOpacity: 0.05 });
        if (_map) newBb.addTo(_map);
        window.setBoundingBox?.(newBb);
        window.clearLayerCache?.();
        window.loadAllLayers?.();
    });

    ['bboxNorth', 'bboxSouth', 'bboxEast', 'bboxWest'].forEach(id => {
        document.getElementById(id)?.addEventListener('keydown', ev => {
            if (ev.key === 'Enter') bboxReloadBtn?.click();
        });
    });

    // Empty-state Load DEM button — same action as bboxReloadBtn
    document.getElementById('emptyStateLoadBtn')?.addEventListener('click', () => bboxReloadBtn?.click());

    document.getElementById('editBboxOnMapBtn')
        ?.addEventListener('click', () => window.toggleBboxMiniMap?.());

    document.getElementById('saveBboxBtn')?.addEventListener('click', async () => {
        const selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion?.name) { window.showToast?.('No region selected', 'error'); return; }
        const n = parseFloat(document.getElementById('bboxNorth')?.value);
        const s = parseFloat(document.getElementById('bboxSouth')?.value);
        const e = parseFloat(document.getElementById('bboxEast')?.value);
        const w = parseFloat(document.getElementById('bboxWest')?.value);
        if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
            window.showToast?.('Invalid coordinates', 'error'); return;
        }
        try {
            const { error } = await window.api.regions.update(selectedRegion.name, {
                name:  selectedRegion.name,
                label: selectedRegion.label || '',
                north: n, south: s, east: e, west: w,
            });
            if (error) throw new Error(error);
            selectedRegion.north = n; selectedRegion.south = s;
            selectedRegion.east  = e; selectedRegion.west  = w;
            window.showToast?.('Bbox saved', 'success');
            await window.loadCoordinates?.();
        } catch (err) {
            window.showToast?.('Save failed: ' + err.message, 'error');
        }
    });

    document.getElementById('saveRegionLabelBtn')?.addEventListener('click', async () => {
        const selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion?.name) { window.showToast?.('No region selected', 'error'); return; }
        const label = document.getElementById('regionLabelEdit')?.value.trim() ?? '';
        try {
            const { error } = await window.api.regions.update(selectedRegion.name, {
                name:  selectedRegion.name,
                label,
                north: selectedRegion.north, south: selectedRegion.south,
                east:  selectedRegion.east,  west:  selectedRegion.west,
            });
            if (error) throw new Error(error);
            selectedRegion.label = label;
            window.appState.selectedRegion = selectedRegion;
            window.showToast?.('Label saved', 'success');
            await window.loadCoordinates?.();
        } catch (err) {
            window.showToast?.('Save failed: ' + err.message, 'error');
        }
    });
};

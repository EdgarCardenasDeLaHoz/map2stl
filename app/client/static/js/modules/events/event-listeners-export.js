/**
 * modules/event-listeners-export.js
 *
 * Model export (STL/OBJ/3MF/cross-section) and city/puzzle/viewer listeners.
 *
 * Exposes on window:
 *   window._setupModelExportListeners()
 *   window._setupCityAndExportListeners()
 */

window._setupModelExportListeners = function _setupModelExportListeners() {
    // Generate / Preview buttons removed — model auto-rebuilds via
    // _modelViewerAutoRebuild() (see model-viewer.js).
    document.getElementById('downloadSTLBtn')?.addEventListener('click', () => window.downloadSTL?.());
    document.getElementById('downloadOBJBtn')?.addEventListener('click', () => window.downloadModel?.('obj'));
    document.getElementById('download3MFBtn')?.addEventListener('click', () => window.downloadModel?.('3mf'));

    ['mmPerPixel', 'exportModelHeight', 'exportBaseHeight'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => window.updatePrintDimensions?.());
    });
    const bedSel = document.getElementById('bedSizeSelect');
    if (bedSel) {
        bedSel.addEventListener('change', () => {
            const customRow = document.getElementById('bedCustomRow');
            if (customRow) customRow.style.display = bedSel.value === 'custom' ? 'flex' : 'none';
            window.updatePrintDimensions?.();
        });
    }
    ['bedCustomW', 'bedCustomH'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', () => window.updatePrintDimensions?.());
    });

    // Contours and engrave-label toggle handlers are now wired in event-listeners-map.js
    // alongside the other exportContours/exportEngraveLabel listeners

    const _setMidVal = () => {
        const axis = document.getElementById('crossSectionAxis')?.value || 'lat';
        const r = window.appState?.selectedRegion;
        if (!r) { window.showToast?.('Select a region first', 'warning'); return; }
        const mid = axis === 'lat'
            ? ((r.north + r.south) / 2).toFixed(4)
            : ((r.east + r.west) / 2).toFixed(4);
        const el = document.getElementById('crossSectionValue');
        if (el) el.value = mid;
    };
    document.getElementById('crossSectionMidBtn')?.addEventListener('click', _setMidVal);
    document.getElementById('crossSectionAxis')?.addEventListener('change', () => {
        const el = document.getElementById('crossSectionValue');
        if (el && !el.value) _setMidVal();
    });
    document.getElementById('downloadCrossSectionBtn')
        ?.addEventListener('click', () => window.downloadCrossSection?.());
};

window._setupCityAndExportListeners = function _setupCityAndExportListeners() {
    document.getElementById('loadCityDataBtn')?.addEventListener('click', () => window.loadCityData?.());
    document.getElementById('clearCityDataBtn')?.addEventListener('click', () => window.clearCityOverlay?.());
    document.getElementById('enhanceHeightsBtn')?.addEventListener('click', () => window.enhanceBuildingHeights?.());

    ['cityLayerBuildings', 'cityLayerRoads', 'cityLayerWaterways'].forEach(id => {
        const toggle = document.getElementById(id);
        if (toggle) toggle.addEventListener('change', () => {
            window._invalidateCityCache?.();
            window.renderCityOverlay?.();
            window.renderCityOnDEM?.();
        });
    });
    ['layerBuildingsColor', 'layerRoadsColor', 'layerWaterwaysColor'].forEach(id => {
        const swatch = document.getElementById(id);
        if (swatch) swatch.addEventListener('input', () => {
            window._invalidateCityCache?.();
            window.renderCityOverlay?.();
            window.renderCityOnDEM?.();
        });
    });
    // cityRoadWidth removed — road canvas width is now a fixed default; road_depression_m is for 3D export

    document.getElementById('exportCityBtn')?.addEventListener('click', async () => {
        const buildings = window.appState?.osmCityData?.buildings;
        const demData = window.appState.lastDemData;
        if (!buildings?.features?.length) {
            window.showToast?.('Load city data first', 'warning'); return;
        }
        if (!demData?.values?.length) {
            window.showToast?.('Load DEM first', 'warning'); return;
        }
        const bbox = window.appState.currentDemBbox || window.appState.selectedRegion;
        if (!bbox) { window.showToast?.('No bounding box', 'warning'); return; }

        const btn = document.getElementById('exportCityBtn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Exporting…'; }
        try {
            const ds = window._demSettings ? window._demSettings() : {};
            const payload = {
                ...ds,
                model_height_mm: parseFloat(document.getElementById('exportModelHeight')?.value) || 20,
                base_mm: parseFloat(document.getElementById('exportBaseHeight')?.value) || 5,
                building_z_scale: parseFloat(document.getElementById('cityBuildingScale')?.value) || 0.5,
                simplify_terrain: document.getElementById('citySimplifyMesh')?.checked ?? true,
                name: (window.appState.selectedRegion?.name || 'city').replace(/[^a-z0-9_-]/gi, '_'),
            };
            const { data: blob, error: exportErr } = await window.api.cities.export3mf(payload);
            if (exportErr) throw new Error(exportErr);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = payload.name + '_city.3mf';
            a.click();
            URL.revokeObjectURL(url);
            window.showToast?.('City 3MF exported', 'success');
        } catch (e) {
            window.showToast?.('Export failed: ' + e.message, 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<span class="btn-icon">🏙️</span> 3MF + Buildings';
            }
        }
    });

    const puzzleEnabledChk = document.getElementById('puzzleEnabled');
    const puzzleParams = document.getElementById('puzzleParams');
    if (puzzleEnabledChk && puzzleParams) {
        puzzleEnabledChk.addEventListener('change', () => {
            puzzleParams.style.display = puzzleEnabledChk.checked ? '' : 'none';
            window.updatePuzzlePreview?.();
        });
    }
    ['splitCols', 'splitRows', 'splitPuzzleM', 'splitBorderHeight'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', () => window.updatePuzzlePreview?.());
    });

    document.getElementById('viewerWireframe')?.addEventListener('change', e => {
        if (window.appState.terrainMesh) {
            window.appState.terrainMesh.material.wireframe = e.target.checked;
            window.appState.terrainMesh.material.needsUpdate = true;
        }
    });
    document.getElementById('viewerAutoRotate')?.addEventListener('change', e => {
        window.setViewerAutoRotate?.(e.target.checked);
    });
    document.getElementById('viewerColormap')?.addEventListener('change', e => {
        window.rebuildViewerColors?.(e.target.value);
    });
    document.getElementById('viewerResetCamera')?.addEventListener('click', () => {
        window.resetViewerCamera?.();
    });
    document.getElementById('viewerNormals')?.addEventListener('change', e => {
        window.setViewerNormals?.(e.target.checked);
    });
    document.getElementById('viewerSurfaceGroups')?.addEventListener('change', e => {
        window.applySurfaceGroups?.(e.target.checked);
    });
    document.getElementById('viewerSimplify')?.addEventListener('change', () => {
        window.applySimplification?.();
    });
    document.getElementById('viewerSimplifyRatio')?.addEventListener('input', () => {
        if (document.getElementById('viewerSimplify')?.checked) window.applySimplification?.();
    });

    document.getElementById('exportPuzzle3MFBtn')
        ?.addEventListener('click', () => window.exportPuzzle3MF?.());
};

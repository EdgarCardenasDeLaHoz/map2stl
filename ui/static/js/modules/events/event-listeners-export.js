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
    document.getElementById('generateModelBtn2')?.addEventListener('click', () => window.generateModelFromTab?.());
    document.getElementById('downloadSTLBtn')?.addEventListener('click', () => window.downloadSTL?.());
    document.getElementById('downloadOBJBtn')?.addEventListener('click', () => window.downloadModel?.('obj'));
    document.getElementById('download3MFBtn')?.addEventListener('click', () => window.downloadModel?.('3mf'));
    document.getElementById('previewModelBtn')?.addEventListener('click', () => window.previewModelIn3D?.());

    ['modelResolution', 'modelBaseHeight'].forEach(id => {
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

    const contoursChk = document.getElementById('modelContours');
    if (contoursChk) {
        contoursChk.addEventListener('change', () => {
            const p = document.getElementById('modelContoursParams');
            if (p) p.style.display = contoursChk.checked ? 'block' : 'none';
        });
    }

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
    const loadCityBtn = document.getElementById('loadCityDataBtn');
    if (loadCityBtn) loadCityBtn.onclick = () => window.loadCityData?.();
    const clearCityBtn = document.getElementById('clearCityDataBtn');
    if (clearCityBtn) clearCityBtn.onclick = () => window.clearCityOverlay?.();

    ['Buildings', 'Roads', 'Waterways', 'Walls', 'Towers', 'Churches', 'Forts', 'Pois'].forEach(layer => {
        const toggle = document.getElementById(`layer${layer}Toggle`);
        const swatch = document.getElementById(`layer${layer}Color`);
        if (toggle) toggle.addEventListener('change', () => {
            window._invalidateCityCache?.();
            window.renderCityOverlay?.();
        });
        if (swatch) swatch.addEventListener('input', () => {
            window._invalidateCityCache?.();
            window.renderCityOverlay?.();
        });
    });
    document.getElementById('cityRoadWidth')
        ?.addEventListener('input', () => window.renderCityOverlay?.());

    document.getElementById('exportCityBtn')?.addEventListener('click', async () => {
        const buildings = window.appState?.osmCityData?.buildings;
        const demData   = window.appState.lastDemData;
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
            const payload = {
                north: bbox.north, south: bbox.south,
                east:  bbox.east,  west:  bbox.west,
                dem_values:  Array.from(demData.values),
                dem_width:   demData.width,
                dem_height:  demData.height,
                buildings:   buildings,
                model_height_mm: parseFloat(document.getElementById('modelHeight')?.value) || 20,
                base_mm:         parseFloat(document.getElementById('baseHeight')?.value)  || 5,
                building_z_scale: parseFloat(document.getElementById('buildingZScale')?.value) || 0.5,
                simplify_terrain: document.getElementById('citySimplifyMesh')?.checked ?? true,
                name: (window.appState.selectedRegion?.name || 'city').replace(/[^a-z0-9_-]/gi, '_'),
            };
            const { data: blob, error: exportErr } = await window.api.cities.export3mf(payload);
            if (exportErr) throw new Error(exportErr);
            const url  = URL.createObjectURL(blob);
            const a    = document.createElement('a');
            a.href     = url;
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
    ['puzzlePiecesX', 'puzzlePiecesY', 'puzzleNotchDepth', 'puzzleMargin'].forEach(id => {
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

    document.getElementById('exportPuzzle3MFBtn')
        ?.addEventListener('click', () => window.exportPuzzle3MF?.());
};

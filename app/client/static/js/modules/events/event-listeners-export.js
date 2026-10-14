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
        // #bedCustomRow is shown by ModelContainer.vue from the select's value (v-show):
        // toggling it here missed a saved "Custom…" restored before this listener attached.
        bedSel.addEventListener('change', () => {
            window.updatePrintDimensions?.();
            window.updateBedOutline?.();
        });
    }
    ['bedCustomW', 'bedCustomH'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', () => {
            window.updatePrintDimensions?.();
            window.updateBedOutline?.();
        });
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
            if (id === 'cityLayerBuildings' && !toggle.checked) {
                window.hideCityBuildingsPanel?.();
                window.appState.selectedCityBuildingIndex = null;
                window.syncSelectedCityBuilding?.(null);
            }
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

    document.getElementById('exportCityBtn')?.addEventListener('click', () => window.exportCityModel?.());

    // Knob, clearance and plate options apply to both puzzles, so the Puzzle details
    // card always shows them (no longer hidden while the terrain puzzle is off).
    document.getElementById('puzzleEnabled')
        ?.addEventListener('change', () => window.updatePuzzlePreview?.());
    // Every field that changes the drawn cut lines, the knob outline and the clearance
    // gap included (they were missing, so the preview kept the old shape).
    ['splitCols', 'splitRows', 'splitKnobWidth', 'splitKnobDepth', 'splitClearance', 'cityPieceMm']
        .forEach(id => {
            document.getElementById(id)?.addEventListener('input', () => window.updatePuzzlePreview?.());
        });
    document.getElementById('splitKnobShape')
        ?.addEventListener('change', () => window.updatePuzzlePreview?.());
    document.getElementById('cityPuzzleEnabled')
        ?.addEventListener('change', () => window.updatePuzzlePreview?.());

    // City Model layer table (ModelContainer.vue: cityLayer_<id>_enabled / _value / _mode).
    // Delegated because the rows are a Vue v-for; refreshCityLayers (model-viewer.js)
    // debounces, so a checkbox firing both input and change rebuilds once.
    const cityLayerControl = /^cityLayer_.+_(enabled|value|mode)$/;
    ['input', 'change'].forEach(type => document.addEventListener(type, e => {
        if (cityLayerControl.test(e.target?.id || '')) window.refreshCityLayers?.();
    }));

    // setViewerWireframe (model-viewer.js) covers every mesh in the scene and requests
    // a render; flipping terrainMesh's material alone left the view unchanged until
    // the next camera move, and city meshes never changed.
    document.getElementById('viewerWireframe')?.addEventListener('change', e => {
        window.setViewerWireframe?.(e.target.checked);
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
        ?.addEventListener('click', () => window.exportPuzzle?.());
};

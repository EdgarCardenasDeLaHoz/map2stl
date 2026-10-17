/**
 * modules/events/event-listeners-map.js
 *
 * Map, DEM display, terrain overlays, grid, globe,
 * draw tool, and bbox fine-tune event listeners.
 *
 * Exposes on window:
 *   window._setupMapAndDemListeners()
 *   window._setupBboxListeners()
 */
import {
    endProjectionPreview, isProjectionPreviewActive, markPreviewDemLanded, projTiming, projTimingStart,
    releaseProjectionPreview, settlePreviewLayer, startProjectionPreview,
} from '../dem/projection-preview.js';
import { nextDemGrid } from '../layers/hydrology-print.js';

window._setupMapAndDemListeners = function _setupMapAndDemListeners() {
    // New-region flow (modules/map/new-region.js): draw mode with a live size label.
    function activateDrawTool() {
        window.newRegion?.startDraw();
    }

    let _colormapTimer = null;
    document.getElementById('demColormap')?.addEventListener('change', () => {
        window._invalidateLutCache?.();
        window.events?.emit(window.EV?.COLORMAP_CHANGE);
        clearTimeout(_colormapTimer);
        _colormapTimer = setTimeout(() => window.recolorDEM?.(), 50);
    });

    // DEM source params — sync new DOM inputs into appState.demParams so loadDEM picks them up.
    // These inputs replaced the old pipeline panel + model terrain settings duplicates.
    document.getElementById('paramDepthScale')?.addEventListener('change', e => {
        const v = parseFloat(e.target.value);
        if (!isNaN(v)) window.appState.demParams.depthScale = v;
    });
    document.getElementById('paramWaterScale')?.addEventListener('change', e => {
        const v = parseFloat(e.target.value);
        if (!isNaN(v)) window.appState.demParams.waterScale = v;
    });
    document.getElementById('paramSubtractWater')?.addEventListener('change', e => {
        window.appState.demParams.subtractWater = e.target.checked;
    });
    document.getElementById('exportModelHeight')?.addEventListener('change', e => {
        const v = parseFloat(e.target.value);
        if (!isNaN(v)) window.appState.demParams.height = v;
    });
    document.getElementById('exportBaseHeight')?.addEventListener('change', e => {
        const v = parseFloat(e.target.value);
        if (!isNaN(v)) window.appState.demParams.base = v;
        window.updatePrintDimensions?.();
    });
    document.getElementById('exportEngraveLabel')?.addEventListener('change', () => {
        const row = document.getElementById('exportLabelTextRow');
        if (row) row.style.display = document.getElementById('exportEngraveLabel').checked ? 'block' : 'none';
    });
    document.getElementById('exportContours')?.addEventListener('change', () => {
        const p = document.getElementById('exportContoursParams');
        if (p) p.style.display = document.getElementById('exportContours').checked ? 'block' : 'none';
    });

    const projSelect = document.getElementById('paramProjection');
    const clipChk = document.getElementById('paramClipNans');
    if (projSelect) {
        const projDescriptions = {
            'none': 'No correction — raw lat/lon grid displayed as-is.',
            'cosine': 'Horizontal scaling by cos(latitude). Correct east-west distances.',
            'mercator': 'Web Mercator — conformal; vertical stretching increases towards poles.',
            'equidistant': 'Equidistant Cylindrical — preserves distances along meridians.',
            'lambert': 'Lambert Cylindrical Equal-Area — preserves area at the cost of shape.',
            'sinusoidal': 'Sinusoidal — pseudocylindrical equal-area; each row scaled by cos(lat).',
            'miller': 'Miller Cylindrical — compromise; Mercator-like with less polar exaggeration.',
            'gall': 'Gall Stereographic — compromise cylindrical; balanced shape/area distortion.',
        };
        const _syncClipToggleState = () => {
            if (!clipChk) return;
            const noProjection = projSelect.value === 'none';
            clipChk.disabled = noProjection;
            const label = clipChk.closest('label');
            if (label) label.classList.toggle('disabled', noProjection);
            clipChk.title = noProjection
                ? 'Clip edges has no effect when projection is None.'
                : 'Strip all-NaN border rows/columns introduced by projection warping.';
        };
        _syncClipToggleState();

        let _projChangeTimer = null;
        /** performance.now() of the last change event: a pick within 80 ms of it is a burst. */
        let _lastProjChange = -Infinity;
        // Every pick gets a number; only the newest pick's responses may finish the reload
        // or settle a layer of its preview (an older pick's requests are aborted by the
        // loaders, which drop an in-flight request when the key changes).
        let _projPick = 0;
        // Which layers the reload must re-fetch, read once when a reload starts: a pick made
        // while one is running finds them cleared by loadDEM's clearLayerCache.
        let _reloadLayers = null;
        /** The pick the preview was last warped for (a burst warps before it fetches). */
        let _previewedPick = 0;

        /** Raster size of the DEM the preview was taken from (its longest side). */
        let _previewDim = 0;

        /**
         * The loaded DEM's box, projection and raster size, or null when none is loaded. Read
         * it before loadDEM: its clearLayerCache nulls lastDemData, currentDemBbox and
         * lastDemRequest synchronously, before its first await.
         * @returns {{bbox:object, from:object, dim:number}|null}
         */
        const _loadedFrame = () => {
            const st = window.appState;
            const dem = st.lastDemData;
            const req = st.lastDemRequest;
            if (!dem || !req?.dem) return null;
            return {
                bbox: st.currentDemBbox || req.bbox,
                // The loaded layers are in the projection the DEM was fetched with.
                from: { projection: req.dem.projection || 'none', maintainDimensions: !!req.dem.maintain_dimensions },
                dim: Math.max(dem.width || 0, dem.height || 0) || req.dem.dim,
            };
        };

        /**
         * Re-draw the loaded layers in the newly picked projection. `sources` and `frame` must
         * be read before loadDEM runs: it detaches the DEM canvas and clears the loaded DEM.
         */
        const _previewPick = (pick, sources = window.getLayerSourceCanvases?.(), frame = _loadedFrame()) => {
            let from = null, bbox = null;
            if (!isProjectionPreviewActive()) {
                if (!frame) return false;
                ({ bbox, from } = frame);
                _previewDim = frame.dim;
            }
            // A pick made while a preview shows re-warps the same loaded pixels (bbox/from ignored).
            // The module shows the "Preview · loading layers…" pill (DemContainer.vue).
            _previewedPick = pick;
            return startProjectionPreview({
                pick, bbox, from, to: window.getProjectionParams(), dim: _previewDim, sources,
            });
        };

        /** Log when a request starts and when it lands (window.DEBUG_PROJ_TIMING). */
        const _timed = (what, promise) => {
            projTiming(`${what} request`);
            return Promise.resolve(promise).finally(() => projTiming(`${what} landed`));
        };

        /**
         * Re-fetch every loaded layer in the picked projection; the preview stands in meanwhile.
         * Every request goes out first, in parallel: the DEM, then Rivers & lakes, Borders,
         * Trails and the composite's rivers/lakes merge, whose parameters do not need the new
         * DEM (the grid is the form's dim and source, hydrology-print.js nextDemGrid). They used
         * to wait for the DEM to land (DEM_LOADED), ~1.5 s on Colombia. The warp runs after.
         */
        const _switchProjection = async (pick) => {
            const st = window.appState;
            if (!_reloadLayers) {
                if (!st.lastDemData) return;  // nothing loaded yet
                // Snapshot which layers were loaded BEFORE loadDEM runs, because
                // loadDEM calls clearLayerCache() which nulls lastWaterMaskData,
                // satImgSourceCanvas, and layerBboxes — making the conditions below
                // evaluate to false if checked after the await.
                _reloadLayers = {
                    water: !!st.lastWaterMaskData,
                    landCover: !!st.layerBboxes?.landCover,
                    sat: !!st.satImgSourceCanvas,
                    // satEsaLoaded tracks the GEE/ESA overlay from loadSatelliteImage
                    // (auto-loaded on region select). Cleared by clearLayerCache.
                    satEsa: !!st.satEsaLoaded,
                    // cityRasterSourceCanvas (cleared by clearLayerCache) rather than
                    // osmCityData (not cleared): re-fetch only when the raster was loaded.
                    city: !!st.cityRasterSourceCanvas,
                    cityPolygons: !!st.osmCityData,
                };
            }
            const had = _reloadLayers;
            // Read before loadDEM: it detaches the DEM canvas and clears the loaded DEM.
            const sources = window.getLayerSourceCanvases?.() || {};
            const previewMe = _previewedPick !== pick;
            const frame = previewMe ? _loadedFrame() : null;
            const active = window.getActiveLayers?.() || new Set();
            // The box and grid loadDEM is about to fetch (dem-main.js loadDEM reads the same).
            const coords = window.getBboxCoords?.(window.getBoundingBox?.(), st.selectedRegion);
            const grid = nextDemGrid();

            window.showToast?.('Projection changed — re-fetching layers…', 'info');

            // Re-fetch DEM with new projection param (includes water if subtract_water).
            const demLoad = _timed('dem', window.loadDEM?.());

            // Layers that reload on their own (they used to wait for DEM_LOADED): each settles
            // its preview when its own fetch finishes, with a picture, with none or failed.
            const pending = new Set();
            const own = (mode, load, on = active.has(mode)) => {
                if (!on) return;
                pending.add(mode);
                let run;
                try { run = load(); } catch (e) { run = Promise.reject(e); }
                return _timed(mode, run)
                    .catch((e) => console.warn(`[projection] ${mode} reload:`, e))
                    .finally(() => { if (settlePreviewLayer(mode, pick)) window.emitStackUpdate?.(); });
            };
            if (coords) {
                own('Borders', () => window.loadBorders?.({ activate: false, bbox: coords, dim: grid.dim }));
                own('WaterHydrology', () => window.loadWaterHydrology?.({ grid }));
                own('Trails', () => window.loadTrails?.({ activate: false }));
                const merge = window.prefetchCompositeHydro?.({ bbox: coords, grid });
                if (merge) _timed('composite dem-merge', merge);
            }
            // Water mask and land cover read the box and their own resolution controls, not the
            // DEM, so they go out now too (they waited for the DEM: landCover landed last, 4 s).
            // loadSatelliteImage is loadEsaLandCover: one call, or the second aborts the first.
            const early = [];
            if (had.water) early.push(_timed('water', window.loadWaterMask?.()));
            if (had.landCover || had.satEsa) early.push(own('Sat', () => window.loadEsaLandCover?.(), true));

            // Show the new shape now: the loaded layers warped in the browser
            // (modules/dem/projection-preview.js). loadDEM has cleared the stack: redraw it
            // on the next frame, before it paints.
            if (previewMe) _previewPick(pick, sources, frame);
            window.emitStackUpdate?.();

            // Layers that reload on their own keep their preview until they settle: release, not end.
            const finish = (failed) => {
                _reloadLayers = null;
                if (failed) endProjectionPreview();
                else releaseProjectionPreview(window.getLayerSourceCanvases?.(), pending);
                window.emitStackUpdate?.();
            };

            await demLoad;
            if (pick !== _projPick) return;           // a newer pick owns the map now
            if (!window.appState.lastDemData) { finish(true); return; }   // failed or cancelled
            markPreviewDemLanded(window.getLayerSourceCanvases?.());
            window.emitStackUpdate?.();

            // Re-fetch other layers that were already loaded
            const tasks = [...early];
            if (had.sat) tasks.push(_timed('sat', window.loadSatelliteRGBImage?.()));
            if (had.city) tasks.push(_timed('city', window.loadCityRaster?.()));
            if (tasks.length) await Promise.allSettled(tasks);
            if (pick !== _projPick) return;

            // City polygon overlays are frontend-rendered; force cache invalidation + redraw
            // so projection changes are reflected immediately even without refetch.
            if (had.cityPolygons) {
                window._invalidateCityCache?.();
                window.renderCityOverlay?.();
                window.renderCityOnDEM?.();
            }
            finish();

            requestAnimationFrame(() => window.updatePrintDimensions?.());
        };

        projSelect.addEventListener('change', () => {
            // Update description immediately — zero cost
            const desc = document.getElementById('projectionDescription');
            if (desc) desc.textContent = projDescriptions[projSelect.value] || '';
            _syncClipToggleState();

            const pick = ++_projPick;
            projTimingStart();
            clearTimeout(_projChangeTimer);
            const now = performance.now();
            const burst = now - _lastProjChange < 80;
            _lastProjChange = now;
            // Projection is applied server-side: re-fetch every loaded layer so all canvases
            // arrive pre-projected and aligned. A single pick fetches at once; a burst of picks
            // (arrow keys through the list) shows each one now and fetches the last, 80 ms on.
            if (!burst) { _switchProjection(pick); return; }
            if (_previewPick(pick)) window.emitStackUpdate?.();
            _projChangeTimer = setTimeout(() => _switchProjection(pick), 80);
        });
    }

    // Clip-edges checkbox also triggers refetch (projection-related param)
    let _clipNansTimer = null;
    document.getElementById('paramClipNans')?.addEventListener('change', () => {
        const proj = document.getElementById('paramProjection')?.value || 'none';
        if (proj === 'none') {
            window.showToast?.('Clip edges only applies when a projection is enabled.', 'info');
            return;
        }
        clearTimeout(_clipNansTimer);
        _clipNansTimer = setTimeout(() => {
            document.getElementById('paramProjection')?.dispatchEvent(new Event('change'));
        }, 80);
    });

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
        const on = e.target.checked;
        window.toggleTerrainOverlay?.(on);
        window.updateFloatingTerrainButton?.(on);
        // The Explore tab's settings popover drives the same overlay through its
        // own checkbox, and the floating button routes through this one, so
        // clicking the button used to leave the popover reading "off" with its
        // opacity slider hidden. Assign rather than dispatch, so the two
        // checkboxes cannot ping-pong through each other's change handlers.
        const exploreCb = document.getElementById('showTerrainOverlayExplore');
        if (exploreCb) exploreCb.checked = on;
        const exploreRow = document.getElementById('terrainOpacityRowExplore');
        if (exploreRow) exploreRow.style.display = on ? 'flex' : 'none';
        window.showToast?.(on ? 'Terrain relief enabled' : 'Terrain relief disabled', 'info');
    });
    document.getElementById('floatingTerrainToggle')?.addEventListener('click', () => {
        const cb = document.getElementById('showTerrainOverlay');
        if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
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
        const gc = document.getElementById('globeContainer');
        const btn = document.getElementById('floatingGlobeToggle');
        if (gc.classList.contains('hidden')) {
            Object.assign(gc.style, {
                position: 'absolute', top: '0', left: '0',
                width: '100%', height: '100%', zIndex: '500'
            });
            gc.classList.remove('hidden');
            btn?.classList.add('active');
            window.initGlobe?.();
        } else {
            gc.classList.add('hidden');
            gc.style.position = '';
            btn?.classList.remove('active');
        }
    });

    const mapSettingsBtn = document.getElementById('floatingMapSettingsBtn');
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
    const mapTileLayerEdit = document.getElementById('mapTileLayer');
    mapTileLayerExplore?.addEventListener('change', () => {
        window.setTileLayer?.(mapTileLayerExplore.value);
        try { localStorage.setItem('map2stl_mapStyle', mapTileLayerExplore.value); } catch (_) { /* per-browser preference */ }
        if (mapTileLayerEdit) mapTileLayerEdit.value = mapTileLayerExplore.value;
    });
    mapTileLayerEdit?.addEventListener('change', () => {
        if (mapTileLayerExplore) mapTileLayerExplore.value = mapTileLayerEdit.value;
    });

    const terrainCheckboxExplore = document.getElementById('showTerrainOverlayExplore');
    const terrainRowExplore = document.getElementById('terrainOpacityRowExplore');
    const terrainOpacityExplore = document.getElementById('terrainOverlayOpacityExplore');
    const terrainOpacityLabelExplore = document.getElementById('terrainOpacityValueExplore');
    terrainCheckboxExplore?.addEventListener('change', () => {
        const on = terrainCheckboxExplore.checked;
        window.toggleTerrainOverlay?.(on);
        // Mirror of the #showTerrainOverlay handler above. The floating button is
        // the same control by another name, so it has to follow this checkbox as
        // well; without it the button stayed unlit over a visible overlay.
        window.updateFloatingTerrainButton?.(on);
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

    document.getElementById('floatingDrawBtn')?.addEventListener('click', activateDrawTool);
    const _map = window.getMap?.();
    if (_map) {
        _map.on(L.Draw.Event.CREATED, (e) => {
            // The drawn box becomes the pending new region, named in NewRegionCard.vue.
            window.newRegion?.onCreated(e.layer);
        });
        _map.on(L.Draw.Event.DRAWSTOP, () => {
            document.getElementById('floatingDrawBtn')?.classList.remove('drawing');
        });
    }

    document.getElementById('gridPixelModeBtn')?.addEventListener('click', () => {
        const btn = document.getElementById('gridPixelModeBtn');
        const on = btn?.classList.toggle('active');
        window.setGridPixelMode?.(!!on);
    });

    // Gridline controls in View -> Canvas should update overlays immediately.
    const _refreshGridlines = () => {
        window.drawGridlinesOverlay?.('demImage');
        window.drawGridlinesOverlay?.('inlineLayersCanvas');
        window.drawLayerGrid?.();
        window.events?.emit(window.EV?.STACKED_UPDATE);
    };
    document.getElementById('gridlineCount')?.addEventListener('change', _refreshGridlines);
    document.getElementById('showGridlines')?.addEventListener('change', _refreshGridlines);

    // Grid unit mode (degrees vs meters section grid), spacing, and line colour.
    const _gridUnitMode = document.getElementById('gridUnitMode');
    const _syncMetersRow = () => {
        const row = document.getElementById('gridMetersSpacingRow');
        const dens = document.getElementById('gridlineCount');
        const isMeters = _gridUnitMode?.value === 'meters';
        if (row) row.classList.toggle('hidden', !isMeters);
        // Density count only applies to the degree graticule.
        if (dens) dens.disabled = isMeters;
    };
    _syncMetersRow();
    _gridUnitMode?.addEventListener('change', () => { _syncMetersRow(); _refreshGridlines(); });
    document.getElementById('gridMetersSpacing')?.addEventListener('change', _refreshGridlines);
    document.getElementById('gridLineColor')?.addEventListener('input', _refreshGridlines);

    // Pixel grid (independent red grid in DEM pixel space).
    document.getElementById('showPixelGrid')?.addEventListener('change', _refreshGridlines);
    document.getElementById('pixelGridSpacing')?.addEventListener('input', _refreshGridlines);
    document.getElementById('pixelGridColor')?.addEventListener('input', _refreshGridlines);

    // True grid (real meridians and parallels through the projection, Edit map only).
    document.getElementById('showRealGrid')?.addEventListener('change', _refreshGridlines);
    document.getElementById('realGridColor')?.addEventListener('input', _refreshGridlines);

    document.getElementById('terrainOverlayOpacity')?.addEventListener('input', e => {
        const val = e.target.value;
        document.getElementById('terrainOpacityValue').textContent = `${val}%`;
        window.setTerrainOverlayOpacity?.(val);
    });

    document.getElementById('paramDim')?.addEventListener('input', () => {
        const val = parseInt(document.getElementById('paramDim').value);
        const w = document.getElementById('demResWarning');
        if (w) w.style.display = val > 500 ? 'block' : 'none';
    });
    document.getElementById('waterResolution')?.addEventListener('change', () => {
        const val = parseInt(document.getElementById('waterResolution').value);
        const w = document.getElementById('waterResWarning');
        if (w) w.style.display = val >= 500 ? 'block' : 'none';
        // In merged mode, only the combined button should trigger network loads.
        const hasCombinedLoader = !!document.getElementById('loadWaterHydrologyBtn');
        if (!hasCombinedLoader) {
            window.loadWaterMask?.();
        }
    });
    /**
     * Wire a load button to an async action with disable-while-loading guard.
     * @param {string} btnId  - Element ID of the button
     * @param {Function} fn   - Async action to invoke on click
     * @param {Function} [before] - Optional sync setup to run before fn (receives btn)
     */
    function _asyncBtn(btnId, fn, before) {
        document.getElementById(btnId)?.addEventListener('click', async () => {
            const btn = document.getElementById(btnId);
            const label = btn?.textContent;
            if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
            before?.(btn);
            try { await fn(); }
            finally { if (btn) { btn.disabled = false; btn.textContent = label || '⟳ Load'; } }
        });
    }

    // --- Quick-load resolution labels ---
    // Sync the quick-load bar labels with the actual resolution controls in fetch sections.
    // Fetch section load buttons
    // loadWaterHydrologyBtn and related wired in event-listeners.js
    // loadSatImgBtn wired in app-setup.js (also switches to SatImg mode)
    _asyncBtn('loadDemBtn', () => window.loadDEM?.());
    _asyncBtn('loadEsaBtn', () => window.loadEsaLandCover?.());
    // Satellite clear button
    document.getElementById('clearSatImgBtn')?.addEventListener('click', () => {
        window.appState.satImgSourceCanvas = null;
        window.appState._satImgRawCanvas = null;
        window.events?.emit(window.EV?.STACKED_UPDATE);
        window.showToast?.('Satellite layer cleared', 'info');
    });
};

window._setupBboxListeners = function _setupBboxListeners() {
    /** Read the four bbox input fields. Returns { n, s, e, w } as floats (may be NaN). */
    function _readBboxInputs() {
        return {
            n: parseFloat(document.getElementById('bboxNorth')?.value),
            s: parseFloat(document.getElementById('bboxSouth')?.value),
            e: parseFloat(document.getElementById('bboxEast')?.value),
            w: parseFloat(document.getElementById('bboxWest')?.value),
        };
    }

    const bboxReloadBtn = document.getElementById('bboxReloadBtn');
    bboxReloadBtn?.addEventListener('click', () => {
        const { n, s, e, w } = _readBboxInputs();
        if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
            window.showToast?.('Invalid coordinates', 'error'); return;
        }
        const nc = Math.max(-90, Math.min(90, n));
        const sc = Math.max(-90, Math.min(90, s));
        const ec = Math.max(-180, Math.min(180, e));
        const wc = Math.max(-180, Math.min(180, w));
        if (nc !== n || sc !== s || ec !== e || wc !== w) {
            window.showToast?.('Coordinates clamped to valid range', 'warning');
        }
        window.setBboxInputValues?.(nc, sc, ec, wc);
        if (nc <= sc) {
            window.showToast?.('North must be greater than South', 'error'); return;
        }
        if (ec <= wc) {
            window.showToast?.('East must be greater than West', 'error'); return;
        }
        let selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion) selectedRegion = { name: null };
        selectedRegion = { ...selectedRegion, north: nc, south: sc, east: ec, west: wc };
        window.setSelectedRegion?.(selectedRegion);
        window.appState.selectedRegion = selectedRegion;
        window.appState.currentDemBbox = { north: nc, south: sc, east: ec, west: wc };
        window.setBboxRectangle?.(nc, sc, ec, wc);
        window.clearLayerCache?.();
        window.loadAllLayers?.();
    });

    ['bboxNorth', 'bboxSouth', 'bboxEast', 'bboxWest'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        // Enter key triggers full reload
        el.addEventListener('keydown', ev => {
            if (ev.key === 'Enter') bboxReloadBtn?.click();
        });
        // Live map rectangle update on every keystroke (no DEM reload)
        el.addEventListener('input', () => {
            const { n, s, e, w } = _readBboxInputs();
            // Visual validation feedback on inputs
            const nEl = document.getElementById('bboxNorth');
            const sEl = document.getElementById('bboxSouth');
            const eEl = document.getElementById('bboxEast');
            const wEl = document.getElementById('bboxWest');
            const latOk = !isNaN(n) && !isNaN(s) && n > s
                && n >= -90 && n <= 90 && s >= -90 && s <= 90;
            const lonOk = !isNaN(e) && !isNaN(w) && e > w
                && e >= -180 && e <= 180 && w >= -180 && w <= 180;
            if (nEl) nEl.style.borderColor = latOk ? '' : '#e74c3c';
            if (sEl) sEl.style.borderColor = latOk ? '' : '#e74c3c';
            if (eEl) eEl.style.borderColor = lonOk ? '' : '#e74c3c';
            if (wEl) wEl.style.borderColor = lonOk ? '' : '#e74c3c';
            if (!latOk || !lonOk) return;
            // Updates the rectangle in place, so typing does not churn a layer
            // per keystroke.
            window.setBboxRectangle?.(n, s, e, w);
        });
    });

    // Empty-state Load DEM button — same action as bboxReloadBtn
    document.getElementById('emptyStateLoadBtn')?.addEventListener('click', () => bboxReloadBtn?.click());

    document.getElementById('editBboxOnMapBtn')
        ?.addEventListener('click', () => window.toggleBboxMiniMap?.());

    document.getElementById('saveBboxBtn')?.addEventListener('click', async () => {
        const selectedRegion = window.appState.selectedRegion;
        if (!selectedRegion?.name) { window.showToast?.('No region selected', 'error'); return; }
        const { n, s, e, w } = _readBboxInputs();
        if (isNaN(n) || isNaN(s) || isNaN(e) || isNaN(w)) {
            window.showToast?.('Invalid coordinates', 'error'); return;
        }
        try {
            const { error } = await window.api.regions.update(selectedRegion.name, {
                name: selectedRegion.name,
                label: selectedRegion.label || '',
                north: n, south: s, east: e, west: w,
            });
            if (error) throw new Error(error);
            selectedRegion.north = n; selectedRegion.south = s;
            selectedRegion.east = e; selectedRegion.west = w;
            window.showToast?.('Bbox saved', 'success');
            await window.loadCoordinates?.();
        } catch (err) {
            window.showToast?.('Save failed: ' + err.message, 'error');
        }
    });
};

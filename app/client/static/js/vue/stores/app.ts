import { defineStore } from 'pinia';
import { markRaw } from 'vue';
import type {
    Region, DemData, WaterMaskData, BBox,
    LayerBboxes, LayerStatus, DemParams, LandCoverConfig,
    CurvePoint, OsmCityData,
} from './types';

// Default land-cover config — matches app.js initialisation
const DEFAULT_LAND_COVER: LandCoverConfig = {
    10:  { name: 'Tree Cover',    color: [0,   100,  0],   elevation: 0.10 },
    20:  { name: 'Shrubland',     color: [255, 187, 34],   elevation: 0.05 },
    30:  { name: 'Grassland',     color: [255, 255, 76],   elevation: 0.02 },
    40:  { name: 'Cropland',      color: [240, 150, 255],  elevation: 0.00 },
    50:  { name: 'Built-up',      color: [250,   0,   0],  elevation: 0.15 },
    60:  { name: 'Bare/Sparse',   color: [180, 180, 180],  elevation: 0.00 },
    70:  { name: 'Snow/Ice',      color: [240, 240, 240],  elevation: 0.00 },
    80:  { name: 'Water',         color: [0,   100, 200],  elevation: -0.10 },
    90:  { name: 'Wetland',       color: [0,   150, 160],  elevation: -0.02 },
    95:  { name: 'Mangroves',     color: [0,   207, 117],  elevation: 0.00 },
    100: { name: 'Moss/Lichen',   color: [250, 230, 160],  elevation: 0.00 },
    0:   { name: 'No Data/Ocean', color: [0,    50, 150],  elevation: -0.15 },
};

export const useAppStore = defineStore('app', {
    state: () => ({
        // ── Region ────────────────────────────────────────────────────────────
        selectedRegion:     null as Region | null,
        coordinatesData:    [] as Region[],

        // ── DEM & layers ──────────────────────────────────────────────────────
        lastDemData:        null as DemData | null,
        currentDemBbox:     null as BBox | null,
        lastWaterMaskData:  null as WaterMaskData | null,
        layerBboxes:        { dem: null, water: null, landCover: null } as LayerBboxes,
        layerStatus:        { dem: 'empty', water: 'empty', landCover: 'empty' } as LayerStatus,

        // ── DEM parameters ────────────────────────────────────────────────────
        demParams: {
            dim:           200,
            depthScale:    0.5,
            waterScale:    0.05,
            subtractWater: true,
            satScale:      500,
            height:        10,
            base:          2,
        } as DemParams,

        // ── Appearance ────────────────────────────────────────────────────────
        landCoverConfig:         JSON.parse(JSON.stringify(DEFAULT_LAND_COVER)) as LandCoverConfig,
        landCoverConfigDefaults: JSON.parse(JSON.stringify(DEFAULT_LAND_COVER)) as LandCoverConfig,
        waterOpacity:            0.7,
        curvePoints:             [{ x: 0, y: 0 }, { x: 1, y: 1 }] as CurvePoint[],
        activeCurvePreset:       'linear',
        originalDemValues:       null as Float32Array | null,
        curveDataVmin:           null as number | null,
        curveDataVmax:           null as number | null,

        // ── City / canvas source refs (markRaw — never made deeply reactive) ──
        osmCityData:             null as OsmCityData | null,
        cityRasterSourceCanvas:  null as HTMLCanvasElement | null,
        compositeDemSourceCanvas: null as HTMLCanvasElement | null,
        compositeFeatures:       null as unknown,
        compositeCityRaster:     null as unknown,
        satImgSourceCanvas:      null as HTMLCanvasElement | null,
        _satImgRawCanvas:        null as HTMLCanvasElement | null,
        _satImgBbox:             null as BBox | null,

        // ── 3D viewer ─────────────────────────────────────────────────────────
        generatedModelData:  null as unknown,
        terrainMesh:         null as unknown,
        viewerScene:         null as unknown,

        // ── UI state ──────────────────────────────────────────────────────────
        activeView:   'map'      as 'map' | 'dem' | 'model',
        sidebarMode:  'expanded' as 'expanded' | 'normal' | 'hidden',
        regionThumbnails: {} as Record<string, string>,

        // ── Callbacks registered by modules (non-reactive) ────────────────────
        // These are stored with markRaw to prevent Vue from making them reactive.
        _setDemEmptyState:       null as ((empty: boolean) => void) | null,
        _updateWorkflowStepper:  null as (() => void) | null,
        _applyCurveSettings:     null as ((...args: unknown[]) => void) | null,
        showToast:               null as ((...args: unknown[]) => void) | null,
        haversineDiagKm:         null as ((...args: unknown[]) => number) | null,
    }),

    actions: {
        // ── Compat API — used by the window.appState bridge ───────────────────

        /** Read a key (mirrors existing window.appState.get(key)) */
        get(key: string): unknown {
            return (this as unknown as Record<string, unknown>)[key];
        },

        /** Write a key and notify Pinia watchers (mirrors window.appState.set(key, val)) */
        set(key: string, val: unknown): void {
            // Wrap canvas/Three.js objects so Vue doesn't make them deeply reactive
            if (val instanceof HTMLCanvasElement || (val !== null && typeof val === 'object' && '_isThree' in val)) {
                val = markRaw(val as object);
            }
            (this as unknown as Record<string, unknown>)[key] = val;
        },

        // ── Domain actions ────────────────────────────────────────────────────

        clearLayerCache(): void {
            this.lastDemData       = null;
            this.currentDemBbox    = null;
            this.originalDemValues = null;
            this.curveDataVmin     = null;
            this.curveDataVmax     = null;
            this.layerBboxes  = { dem: null, water: null, landCover: null };
            this.layerStatus  = { dem: 'empty', water: 'empty', landCover: 'empty' };
            this.cityRasterSourceCanvas   = null;
            this.compositeDemSourceCanvas = null;
            this.compositeFeatures        = null;
            this.compositeCityRaster      = null;
            this.satImgSourceCanvas       = null;
            this._satImgRawCanvas         = null;
            this._satImgBbox              = null;
        },
    },
});

// Type export for use in components / composables
export type AppStore = ReturnType<typeof useAppStore>;

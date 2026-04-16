// ─── Domain types ────────────────────────────────────────────────────────────

export interface BBox {
    north: number;
    south: number;
    east: number;
    west: number;
}

export interface Region {
    id?: number;
    name: string;
    label?: string;
    north: number;
    south: number;
    east: number;
    west: number;
    dim?: number;
    notes?: string;
}

export interface DemData {
    values: Float32Array | number[];
    width: number;
    height: number;
    min: number;
    max: number;
    bbox: BBox;
}

export interface WaterMaskData {
    [key: string]: unknown;
}

export interface LayerBboxes {
    dem: BBox | null;
    water: BBox | null;
    landCover: BBox | null;
}

export type LayerState = 'empty' | 'loading' | 'loaded' | 'error';

export interface LayerStatus {
    dem: LayerState;
    water: LayerState;
    landCover: LayerState;
}

export interface DemParams {
    dim: number;
    depthScale: number;
    waterScale: number;
    subtractWater: boolean;
    satScale: number;
    height: number;
    base: number;
}

export interface LandCoverClass {
    name: string;
    color: [number, number, number];
    elevation: number;
}

export type LandCoverConfig = Record<number, LandCoverClass>;

export interface CurvePoint {
    x: number;
    y: number;
}

export interface OsmCityData {
    buildings?: GeoJSON.FeatureCollection;
    roads?: GeoJSON.FeatureCollection;
    waterways?: GeoJSON.FeatureCollection;
    walls?: GeoJSON.FeatureCollection;
}

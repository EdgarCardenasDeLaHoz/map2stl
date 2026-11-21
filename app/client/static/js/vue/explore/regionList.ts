/**
 * explore/regionList.ts - pure logic behind the Explore region list and wide table:
 * continent groups, the "In view / All" scope, search, the continent filter and paging.
 * No DOM, no Vue, no window: tested in tests/js/regionList.test.js. The behaviour is the
 * the old region-ui.js and view-management.js::renderSidebarTable.
 */
import { detectContinent } from '../../modules/regions/continent.js';
import { selectViewportRegions } from '../../modules/regions/viewport-regions.js';

export interface ListRegion {
    name: string;
    label?: string | null;
    description?: string | null;
    north: number;
    south: number;
    east: number;
    west: number;
    notes?: string;
}

export interface ViewBox { north: number; south: number; east: number; west: number }

export const KNOWN_CONTINENTS = [
    'North America', 'South America', 'Europe', 'Africa', 'Asia', 'Oceania', 'Antarctica', 'Other',
];
export const LIST_PAGE_SIZE = 20;

/** A label that names a continent, in its canonical spelling; '' for any other label. */
export function normalizeContinent(label: unknown): string {
    const key = String(label ?? '').trim().toLowerCase();
    return KNOWN_CONTINENTS.find((c) => c.toLowerCase() === key) ?? '';
}

/** The continent a region is grouped under: its label when that names one, else by position. */
export function resolveContinent(region: ListRegion): string {
    const fromLabel = normalizeContinent(region.label);
    if (fromLabel) return fromLabel;
    return detectContinent((region.north + region.south) / 2, (region.east + region.west) / 2);
}

export interface RegionGroup { continent: string; regions: ListRegion[] }

/** Groups in the fixed continent order, custom groups after them (sorted), names sorted inside. */
export function groupByContinent(regions: ListRegion[]): RegionGroup[] {
    const groups = new Map<string, ListRegion[]>();
    for (const r of regions) {
        const c = resolveContinent(r);
        if (!groups.has(c)) groups.set(c, []);
        groups.get(c)!.push(r);
    }
    for (const g of groups.values()) g.sort((a, b) => a.name.localeCompare(b.name));
    const known = KNOWN_CONTINENTS.filter((c) => groups.has(c)).map((c) => ({ continent: c, regions: groups.get(c)! }));
    const custom = [...groups.keys()].filter((c) => !KNOWN_CONTINENTS.includes(c)).sort()
        .map((c) => ({ continent: c, regions: groups.get(c)! }));
    return [...known, ...custom];
}

/** The continents present among the regions, in canonical order ("All continents" is the caller's). */
export function continentOptions(regions: ListRegion[]): string[] {
    const present = new Set(regions.map(resolveContinent));
    return KNOWN_CONTINENTS.filter((c) => present.has(c));
}

/** Group categories for the wide table's filter: each region's label, else its continent. */
export function categoryOf(region: ListRegion): string {
    const label = String(region.label ?? '').trim();
    return label || resolveContinent(region);
}

export function categoryOptions(regions: ListRegion[]): string[] {
    return [...new Set(regions.map(categoryOf).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

export type Scope = 'view' | 'all';

export interface ListInput {
    regions: ListRegion[];
    scope: Scope;
    search: string;
    /** 'all' or a continent name */
    continent: string;
    page: number;
    view: ViewBox | null;
    selectedName: string | null;
}

export interface ListModel {
    /** 'empty' = no saved regions at all; 'no-view' = in-view scope with nothing to show */
    state: 'empty' | 'no-view' | 'list';
    groups: RegionGroup[];
    /** Count shown on the "In view (N)" tab. */
    inView: number;
    inViewTitle: string;
    /** Count shown on the "All (N)" tab. */
    total: number;
    /** The scope the list really uses: a search lists every region, whatever the tab says. */
    effectiveScope: Scope;
    showScopeTabs: boolean;
    page: number;
    pages: number;
    start: number;
    end: number;
    filtered: number;
    /** Names the map draws (the in-view set); null when the list is not the view set. */
    viewNames: string[] | null;
}

/** The regions that pass the continent filter: the pool both the map and the list draw from. */
export function continentPool(regions: ListRegion[], continent: string): ListRegion[] {
    if (!continent || continent === 'all') return regions;
    return regions.filter((r) => resolveContinent(r) === continent);
}

export function buildListModel(inp: ListInput): ListModel {
    const search = inp.search.trim().toLowerCase();
    const pool = continentPool(inp.regions, inp.continent);
    const viewSet = selectViewportRegions(pool, inp.view, inp.selectedName);
    const inViewMode = !search && inp.scope === 'view';

    let shown: ListRegion[];
    let pages = 1;
    let page = 0;
    let filtered: number;
    if (inViewMode) {
        shown = viewSet.regions as ListRegion[];
        filtered = shown.length;
    } else {
        const all = pool.filter((r) => !search || r.name.toLowerCase().includes(search));
        filtered = all.length;
        pages = Math.max(1, Math.ceil(all.length / LIST_PAGE_SIZE));
        page = Math.min(Math.max(0, inp.page), pages - 1);
        shown = all.slice(page * LIST_PAGE_SIZE, (page + 1) * LIST_PAGE_SIZE);
    }
    const start = inViewMode ? 0 : page * LIST_PAGE_SIZE;
    const inViewTitle = viewSet.inViewCount > viewSet.shownInView
        ? `The ${viewSet.shownInView} largest of the ${viewSet.inViewCount} regions in view, as drawn on the map`
        : 'The regions drawn on the map, largest first';
    let state: ListModel['state'] = 'list';
    if (inp.regions.length === 0) state = 'empty';
    else if (inViewMode && shown.length === 0) state = 'no-view';
    return {
        state,
        groups: groupByContinent(shown),
        inView: viewSet.shownInView,
        inViewTitle,
        total: inp.regions.length,
        effectiveScope: inViewMode ? 'view' : 'all',
        showScopeTabs: !search,
        page, pages, start,
        end: Math.min(start + LIST_PAGE_SIZE, filtered),
        filtered,
        viewNames: inViewMode ? viewSet.regions.map((r: ListRegion) => r.name) : null,
    };
}

export interface TableInput { regions: ListRegion[]; search: string; category: string }

/** The wide table's rows: name search plus the group filter, grouped by continent. */
export function buildTableGroups(inp: TableInput): RegionGroup[] {
    const q = inp.search.trim().toLowerCase();
    const rows = inp.regions.filter((r) =>
        (!q || r.name.toLowerCase().includes(q)) && (!inp.category || categoryOf(r) === inp.category));
    return groupByContinent(rows);
}

/** Second line of a row: the non-empty parts joined with a middle dot. */
export function metaLine(parts: Array<string | null | undefined>): string {
    return parts.filter(Boolean).join(' · ');
}

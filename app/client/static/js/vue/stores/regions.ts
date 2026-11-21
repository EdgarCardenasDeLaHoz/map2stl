/**
 * stores/regions.ts - the Explore page's regions: the saved list, the list controls (scope,
 * search, continent filter, paging), hover, the region editor target and the CRUD calls.
 *
 * The region objects themselves live in `useAppStore().coordinatesData` (and the selection in
 * `selectedRegion`), because Edit and Extrude read those through `window.appState`; this store
 * is the one place that writes them (F-UNIFY Phase 6). Selecting still goes through
 * `window.selectCoordinate(index)` (modules/regions/regions.js), which loads the region's
 * settings and fires REGION_SELECTED.
 */
import { defineStore } from 'pinia';
import { useAppStore } from './app';
import type { Region } from './types';
import {
    buildListModel, continentOptions, continentPool, resolveContinent,
    type ListModel, type Scope, type ViewBox,
} from '../explore/regionList';
import { selectViewportRegions } from '../../modules/regions/viewport-regions.js';
import {
    noteFor, planNotesMigration, readLocalNotes, renameLocalNote, writeLocalNote,
} from '../explore/notesMigration';

type ApiResult<T = any> = Promise<{ data: T; error: string | null }>;
export interface RegionsApi {
    list(): ApiResult;
    create(payload: Record<string, unknown>): ApiResult;
    update(name: string, payload: Record<string, unknown>): ApiResult;
    delete(name: string): ApiResult;
    getNotes?(name: string): ApiResult;
    saveNotes(name: string, notes: string): ApiResult;
}

let _api: RegionsApi | null = null;
/** Tests inject a fake; the app uses window.api.regions. */
export function setRegionsApi(api: RegionsApi | null): void { _api = api; }
function api(): RegionsApi {
    return _api ?? (window as any).api.regions;
}
function toast(msg: string, type = 'info'): void {
    (globalThis as any).window?.showToast?.(msg, type);
}

export type SelectFn = (index: number, opts?: Record<string, unknown>) => Promise<void> | void;

export const useRegionsStore = defineStore('regions', {
    state: () => ({
        loading: false,
        loaded: false,
        error: '',
        scope: 'view' as Scope,
        search: '',
        continent: 'all',
        page: 0,
        collapsed: [] as string[],
        /** Saved-region boxes drawn on the map (the "Show boxes" switch, F-UNIFY M13). */
        boxesVisible: true,
        /** The map's visible bounds, pushed by ExploreMap.vue; null until it is laid out. */
        view: null as ViewBox | null,
        /** Region whose row or box the pointer is over (linked both ways). */
        hoverName: null as string | null,
        /** Region the editor is open on; null = list shown. */
        editingName: null as string | null,
        /** Wide table (the panel widened to the full page). */
        tableOpen: false,
        tableSearch: '',
        tableCategory: '',
        /** Bumped when the boxes must be rebuilt (a load, or a changed region). */
        drawVersion: 0,
    }),
    getters: {
        regions(): Region[] {
            return useAppStore().coordinatesData as Region[];
        },
        selectedName(): string | null {
            return (useAppStore().selectedRegion as Region | null)?.name ?? null;
        },
        continents(): string[] {
            return continentOptions(this.regions);
        },
        /** The regions the continent filter lets through: what the map and the list draw from. */
        pool(): Region[] {
            return continentPool(this.regions, this.continent) as Region[];
        },
        /** The regions the map draws: the viewport set (<= 20 that fit the view + the selected one). */
        mapRegions(): Region[] {
            if (!this.boxesVisible) return [];
            return selectViewportRegions(this.pool, this.view, this.selectedName).regions as Region[];
        },
        list(): ListModel {
            return buildListModel({
                regions: this.regions, scope: this.scope, search: this.search,
                continent: this.continent, page: this.page, view: this.view,
                selectedName: this.selectedName,
            });
        },
        editing(): Region | null {
            return this.editingName ? this.regions.find((r) => r.name === this.editingName) ?? null : null;
        },
    },
    actions: {
        indexOf(name: string): number {
            return this.regions.findIndex((r) => r.name === name);
        },

        /** Fetch the saved regions, then upload any browser-local notes the server lacks. */
        async load(): Promise<void> {
            this.loading = true;
            this.error = '';
            try {
                const { data, error } = await api().list();
                if (error) throw new Error(error);
                const regions = (data?.regions || []) as Region[];
                useAppStore().coordinatesData = regions;
                // The legacy `selectedRegion` object belongs to the previous list: re-point it.
                const sel = useAppStore().selectedRegion as Region | null;
                if (sel) {
                    const fresh = regions.find((r) => r.name === sel.name);
                    if (fresh && fresh !== sel) useAppStore().selectedRegion = fresh;
                }
                this.loaded = true;
                this.drawVersion++;
                void this.migrateNotes(regions);
            } catch (e) {
                this.error = e instanceof Error ? e.message : String(e);
                console.error('Error loading regions:', e);
            } finally {
                this.loading = false;
            }
        },

        /** One-time: push notes saved in this browser to the server where it has none. */
        async migrateNotes(regions: Region[]): Promise<void> {
            const plan = planNotesMigration(regions, readLocalNotes());
            for (const { name, notes } of plan) {
                const { error } = await api().saveNotes(name, notes);
                if (!error) {
                    const r = this.regions.find((x) => x.name === name);
                    if (r) r.notes = notes;
                }
            }
        },

        /** The note to show for a region: the server's, else this browser's fallback copy. */
        noteOf(region: Region | null): string {
            return region ? noteFor(region, readLocalNotes()) : '';
        },

        async select(name: string, opts: Record<string, unknown> = {}): Promise<void> {
            const i = this.indexOf(name);
            if (i < 0) return;
            await ((window as any).selectCoordinate as SelectFn)(i, opts);
        },

        /** Open the sidebar editor on a region, selecting it first when it is not already. */
        async openEditor(name: string): Promise<void> {
            if (this.indexOf(name) < 0) return;
            if (this.selectedName !== name) await this.select(name);
            this.editingName = name;
        },

        /** Select a region and open the Edit page on it (the "✏ Edit" button). */
        async goToEdit(name: string): Promise<void> {
            const i = this.indexOf(name);
            if (i >= 0) await (window as any).goToEdit(i);
        },

        async create(payload: Record<string, unknown>): Promise<boolean> {
            const { data, error } = await api().create(payload);
            if (error) {
                toast('Could not save the region: ' + ((data as any)?.error || (data as any)?.detail || error), 'error');
                return false;
            }
            await this.load();
            return true;
        },

        /**
         * Save the editor's changes in one PUT (a different `name` renames, 409 if taken), then
         * the note. Returns the saved name, or null on failure.
         */
        async update(oldName: string, payload: { name: string; label: string; north: number; south: number;
            east: number; west: number }, notes: string): Promise<string | null> {
            const region = this.regions.find((r) => r.name === oldName);
            if (!region) return null;
            const { error } = await api().update(oldName, {
                ...payload, description: (region as any).description ?? null,
            });
            if (error) { toast(`Save failed: ${error}`, 'error'); return null; }
            const name = payload.name;
            if (name !== oldName) renameLocalNote(oldName, name);
            const noteRes = await api().saveNotes(name, notes);
            if (noteRes.error) toast(`Region saved, but its notes were not: ${noteRes.error}`, 'warning');
            writeLocalNote(name, notes);
            // Keep the selection on the saved region: lists and boxes rebuild from fresh objects.
            const app = useAppStore();
            const wasSelected = (app.selectedRegion as Region | null)?.name === oldName;
            if (this.editingName === oldName) this.editingName = name;
            await this.load();
            const fresh = this.regions.find((r) => r.name === name);
            if (fresh && wasSelected) {
                (window as any).setSelectedRegion?.(fresh);
                app.selectedRegion = fresh;
            }
            toast(name !== oldName ? `Renamed to "${name}"` : 'Region saved', 'success');
            return name;
        },

        /** Delete after a confirm; clears the selection and the editor when they were this region. */
        async remove(name: string): Promise<boolean> {
            if (!this.regions.some((r) => r.name === name)) return false;
            if (!confirm(`Delete region "${name}"? Its saved settings are deleted too.`)) return false;
            const { error } = await api().delete(name);
            if (error) { toast(`Failed to delete region: ${error}`, 'error'); return false; }
            const app = useAppStore();
            if ((app.selectedRegion as Region | null)?.name === name) {
                (window as any).setSelectedRegion?.(null);
                app.selectedRegion = null;
            }
            if (this.editingName === name) this.editingName = null;
            toast(`Region "${name}" deleted`, 'success');
            await this.load();
            return true;
        },

        resolveContinent(region: Region): string {
            return resolveContinent(region);
        },
        setScope(scope: Scope): void { this.scope = scope; this.page = 0; },
        setSearch(q: string): void { this.search = q; this.page = 0; },
        setContinent(c: string): void { this.continent = c; this.page = 0; },
        toggleGroup(continent: string): void {
            const i = this.collapsed.indexOf(continent);
            if (i >= 0) this.collapsed.splice(i, 1); else this.collapsed.push(continent);
        },
    },
});

export type RegionsStore = ReturnType<typeof useRegionsStore>;

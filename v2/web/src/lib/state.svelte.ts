/**
 * All application state, in one place, as one object.
 *
 * v1's equivalent was 258 `window` globals plus 464 DOM ids that JavaScript read back with
 * `getElementById` at save time. The id was the interface: renaming one in a template
 * silently broke the module that read it, because there was no import to fail. Here the
 * settings object is the interface, controls bind to its fields, and a rename is a
 * compile error.
 *
 * The pipeline is modelled as an explicit state, not inferred from whether some variable
 * happens to be set. That is what lets the UI say why a button is unavailable instead of
 * just disabling it — v1 could only tell you that export needed a DEM after you pressed
 * the button.
 */

import { api, type DemHandle, type DemSource, type JobSnapshot, type Region, type RegionSettings } from './api';

export type Stage = 'no-region' | 'ready-to-load' | 'loading' | 'loaded' | 'exporting' | 'exported';

export interface Notice {
	id: number;
	kind: 'info' | 'error' | 'success';
	text: string;
}

function defaultSettings(): RegionSettings {
	return {
		schemaVersion: 1,
		dem: {
			source: 'local',
			dim: 600,
			depthScale: 0.5,
			waterScale: 0.05,
			subtractWater: true,
			maintainDimensions: false
		},
		projection: { name: 'none', clipValidRegion: false },
		overlays: { satellite: false, waterMask: false, landCover: false },
		model: {
			modelHeight: 25,
			baseHeight: 5,
			exaggeration: 1,
			mmPerPixel: 1,
			seaLevelCap: false
		},
		export: { format: 'stl', engraveLabel: false, labelText: '' }
	};
}

class AppState {
	regions = $state<Region[]>([]);
	sources = $state<DemSource[]>([]);
	openTopoKeyConfigured = $state(false);

	region = $state<Region | null>(null);
	settings = $state<RegionSettings>(defaultSettings());

	dem = $state<DemHandle | null>(null);
	elevation = $state<Float32Array | null>(null);
	overlayImages = $state<Record<string, string>>({});

	job = $state<JobSnapshot | null>(null);
	notices = $state<Notice[]>([]);

	loadError = $state<string | null>(null);
	dirty = $state(false);
	saving = $state(false);
	regionFilter = $state('');

	#noticeSeq = 0;
	#stopWatching: (() => void) | null = null;
	#saveTimer: ReturnType<typeof setTimeout> | null = null;
	/** Guards the auto-save so that loading a region's settings does not immediately re-save them. */
	#applying = false;

	// -- derived ---------------------------------------------------------

	stage = $derived.by((): Stage => {
		if (!this.region) return 'no-region';
		if (this.job?.state === 'running') return 'exporting';
		if (this.job?.state === 'done') return 'exported';
		if (this.dem) return 'loaded';
		if (this.loadError) return 'ready-to-load';
		return 'ready-to-load';
	});

	loading = $state(false);

	visibleRegions = $derived.by(() => {
		const needle = this.regionFilter.trim().toLowerCase();
		if (!needle) return this.regions;
		return this.regions.filter(
			(r) =>
				r.name.toLowerCase().includes(needle) ||
				(r.label ?? '').toLowerCase().includes(needle) ||
				(r.description ?? '').toLowerCase().includes(needle)
		);
	});

	/**
	 * What the current settings will actually produce, in millimetres and triangles.
	 *
	 * Shown before the export runs. v1 gave no size feedback at all until the file was on
	 * disk, so an exaggeration typo cost a full render to discover.
	 */
	projection = $derived.by(() => {
		if (!this.dem) return null;
		const { width, height } = this.dem;
		const { mmPerPixel, modelHeight, baseHeight } = this.settings.model;
		return {
			gridWidth: width,
			gridHeight: height,
			mmWidth: width * mmPerPixel,
			mmDepth: height * mmPerPixel,
			// Matches the server: the bottom cap sits at z=0 when there is a base, and one
			// millimetre below the surface's lowest point when there is not.
			mmHeight: baseHeight > 0 ? modelHeight + baseHeight : modelHeight + 1,
			// array_to_mesh emits two triangles per grid cell for the surface, plus walls
			// and a floor. The surface term dominates; this is an estimate, and labelled as one.
			estimatedFaces: (width - 1) * (height - 1) * 2
		};
	});

	/** Why the export button cannot be used, or null when it can. */
	exportBlockedReason = $derived.by(() => {
		if (!this.region) return 'Choose a region first.';
		if (!this.dem) return 'Load the terrain first — the mesh is built from it.';
		if (this.stage === 'exporting') return 'An export is already running.';
		return null;
	});

	// -- lifecycle -------------------------------------------------------

	async init() {
		try {
			const [regions, sources] = await Promise.all([api.regions(), api.sources()]);
			this.regions = regions;
			this.sources = sources.sources;
			this.openTopoKeyConfigured = sources.openTopoKeyConfigured;
		} catch (err) {
			this.notify('error', `Could not reach the server: ${message(err)}`);
		}
	}

	async selectRegion(region: Region) {
		this.region = region;
		this.dem = null;
		this.elevation = null;
		this.overlayImages = {};
		this.job = null;
		this.loadError = null;
		this.#applying = true;
		try {
			this.settings = await api.settings(region.name);
			this.#useAvailableSource();
		} catch (err) {
			this.settings = defaultSettings();
			this.notify('error', `Could not load saved settings: ${message(err)}`);
		} finally {
			// Let the binding pass that applies these values settle before arming auto-save,
			// otherwise applying them would itself look like an edit.
			setTimeout(() => {
				this.#applying = false;
				this.dirty = false;
			}, 0);
		}
	}

	/**
	 * Swap a saved source that this machine cannot serve for one it can.
	 *
	 * Settings outlive the machine they were saved on: a region saved when the SRTM tile
	 * store was present still names it after the store is gone. v1 kept the dead source
	 * selected and let the fetch return an array of zeros, which exported as a flat slab.
	 */
	#useAvailableSource() {
		if (!this.sources.length) return;
		const chosen = this.sources.find((s) => s.id === this.settings.dem.source);
		if (chosen?.available) return;
		const fallback = this.sources.find((s) => s.available);
		if (!fallback) {
			this.notify('error', 'No elevation source is available on this machine.');
			return;
		}
		const was = chosen?.label ?? this.settings.dem.source;
		this.settings.dem.source = fallback.id;
		this.notify('info', `${was} is unavailable here — using ${fallback.label} instead.`);
	}

	async loadTerrain() {
		if (!this.region) return;
		this.loading = true;
		this.loadError = null;
		try {
			this.dem = await api.fetchDem(
				this.region.bbox,
				this.settings.dem,
				this.settings.projection
			);
			this.elevation = null; // fetched lazily, only if the 3D view is opened
			this.notify(
				'success',
				`Terrain loaded: ${this.dem.width}x${this.dem.height} from ${this.dem.sourceUsed} in ${this.dem.fetchSeconds.toFixed(2)}s`
			);
			void this.refreshOverlays();
		} catch (err) {
			this.loadError = message(err);
			this.notify('error', this.loadError);
		} finally {
			this.loading = false;
		}
	}

	async refreshOverlays() {
		if (!this.region) return;
		const wanted = (['satellite', 'waterMask', 'landCover'] as const).filter(
			(k) => this.settings.overlays[k]
		);
		for (const kind of wanted) {
			if (this.overlayImages[kind]) continue;
			try {
				const result = await api.overlay(this.region.bbox, kind, this.settings.dem.dim);
				this.overlayImages = {
					...this.overlayImages,
					[kind]: `data:${result.mime};base64,${result.dataBase64}`
				};
			} catch (err) {
				// An overlay is decoration. Say it failed, keep the terrain, untick the box so
				// the UI does not claim to be showing something it is not.
				this.settings.overlays[kind] = false;
				this.notify('error', message(err));
			}
		}
	}

	async ensureElevation(): Promise<Float32Array | null> {
		if (!this.dem) return null;
		if (this.elevation) return this.elevation;
		try {
			this.elevation = await api.elevation(this.dem.demId);
			return this.elevation;
		} catch (err) {
			this.notify('error', `Could not load elevation data: ${message(err)}`);
			return null;
		}
	}

	async startExport() {
		if (!this.dem || !this.region) return;
		this.#stopWatching?.();
		try {
			this.job = await api.startExport(
				this.dem.demId,
				this.settings.model,
				this.settings.export,
				this.region.name
			);
			this.#stopWatching = api.watchJob(
				this.job.jobId,
				(snapshot) => {
					this.job = snapshot;
					if (snapshot.state === 'done') {
						this.notify('success', `Export ready — ${snapshot.result.faceCount?.toLocaleString()} faces`);
					} else if (snapshot.state === 'failed') {
						this.notify('error', snapshot.error ?? 'The export failed.');
					} else if (snapshot.state === 'cancelled') {
						this.notify('info', 'Export cancelled.');
					}
				},
				(err) => this.notify('error', err)
			);
		} catch (err) {
			this.notify('error', message(err));
		}
	}

	async cancelExport() {
		if (!this.job || this.job.state !== 'running') return;
		try {
			await api.cancelJob(this.job.jobId);
		} catch (err) {
			this.notify('error', message(err));
		}
	}

	download() {
		if (this.job?.state !== 'done') return;
		window.location.href = api.downloadUrl(this.job.jobId);
	}

	// -- settings persistence --------------------------------------------

	/** Called by controls after any edit. Debounced; the indicator updates immediately. */
	touch() {
		if (this.#applying) return;
		this.dirty = true;
		if (this.#saveTimer) clearTimeout(this.#saveTimer);
		this.#saveTimer = setTimeout(() => void this.save(), 1200);
	}

	async save() {
		if (!this.region || !this.dirty) return;
		this.saving = true;
		try {
			await api.saveSettings(this.region.name, $state.snapshot(this.settings));
			this.dirty = false;
		} catch (err) {
			// Stay dirty. A failed auto-save that clears the indicator is worse than no
			// auto-save, because it tells the user their work is safe when it is not.
			this.notify('error', `Could not save settings: ${message(err)}`);
		} finally {
			this.saving = false;
		}
	}

	/** Editing anything that feeds the fetch invalidates the loaded terrain. */
	invalidateTerrain() {
		if (this.dem) {
			this.dem = null;
			this.elevation = null;
			this.overlayImages = {};
			this.job = null;
			this.notify('info', 'Those settings change the terrain — load it again.');
		}
		this.touch();
	}

	// -- notices ---------------------------------------------------------

	notify(kind: Notice['kind'], text: string) {
		const notice = { id: ++this.#noticeSeq, kind, text };
		this.notices = [...this.notices, notice];
		// Errors stay until dismissed. v1 hid export failures after two seconds, which
		// meant a failure that happened while the user looked away left no trace at all.
		if (kind !== 'error') {
			setTimeout(() => this.dismiss(notice.id), 4000);
		}
	}

	dismiss(id: number) {
		this.notices = this.notices.filter((n) => n.id !== id);
	}
}

function message(err: unknown): string {
	return err instanceof Error ? err.message : String(err);
}

export const app = new AppState();

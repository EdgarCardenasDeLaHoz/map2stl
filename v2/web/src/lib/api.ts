/**
 * The whole server surface, in one file.
 *
 * Every call goes through `request`, so error handling is uniform: a failing response is
 * turned into an Error carrying the server's own message. v1 spread `fetch` calls across a
 * dozen modules, each inventing its own idea of what a failure looked like, which is why
 * some failures raised a toast, some logged to the console, and one auto-hid after two
 * seconds and left no trace at all.
 */

export interface BBox {
	north: number;
	south: number;
	east: number;
	west: number;
}

export interface Region {
	name: string;
	label: string | null;
	description: string | null;
	bbox: BBox;
}

export interface DemSettings {
	source: string;
	dim: number;
	depthScale: number;
	waterScale: number;
	subtractWater: boolean;
	maintainDimensions: boolean;
}

export interface ProjectionSettings {
	name: string;
	clipValidRegion: boolean;
}

export interface OverlaySettings {
	satellite: boolean;
	waterMask: boolean;
	landCover: boolean;
}

export interface ModelSettings {
	modelHeight: number;
	baseHeight: number;
	exaggeration: number;
	mmPerPixel: number;
	seaLevelCap: boolean;
}

export interface ExportSettings {
	format: 'stl' | 'obj' | '3mf';
	engraveLabel: boolean;
	labelText: string;
}

export interface RegionSettings {
	schemaVersion: number;
	dem: DemSettings;
	projection: ProjectionSettings;
	overlays: OverlaySettings;
	model: ModelSettings;
	export: ExportSettings;
}

export interface DemSource {
	id: string;
	label: string;
	provider: string;
	resolutionM: number;
	available: boolean;
	note: string;
}

/** What the server hands back after a fetch. `demId` is the only part export needs. */
export interface DemHandle {
	demId: string;
	bbox: BBox;
	settings: unknown;
	height: number;
	width: number;
	minElevation: number;
	maxElevation: number;
	meanElevation: number;
	sourceUsed: string;
	fetchSeconds: number;
}

export interface MeshStats {
	faceCount: number;
	vertexCount: number;
	watertight: boolean;
	sizeMm: { x: number; y: number; z: number };
}

export interface JobSnapshot {
	jobId: string;
	kind: string;
	state: 'running' | 'done' | 'failed' | 'cancelled' | 'unknown';
	percent: number;
	message: string;
	error: string | null;
	result: Partial<MeshStats>;
	elapsedSeconds: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(path, {
		...init,
		headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) }
	});
	if (!response.ok) {
		throw new Error(await describeFailure(response));
	}
	return (await response.json()) as T;
}

/** Prefer the server's own explanation; fall back to the status line. */
async function describeFailure(response: Response): Promise<string> {
	try {
		const body = await response.json();
		if (typeof body?.detail === 'string') return body.detail;
		// FastAPI validation errors arrive as a list of per-field objects. Name the field
		// rather than showing the raw structure.
		if (Array.isArray(body?.detail) && body.detail.length) {
			const first = body.detail[0];
			const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : '';
			return field ? `${field}: ${first.msg}` : first.msg;
		}
	} catch {
		// Not JSON. The status line below is the best available description.
	}
	return `${response.status} ${response.statusText}`;
}

export const api = {
	regions: () => request<{ regions: Region[] }>('/api/regions').then((r) => r.regions),

	settings: (name: string) =>
		request<RegionSettings>(`/api/regions/${encodeURIComponent(name)}/settings`),

	saveSettings: (name: string, settings: RegionSettings) =>
		request<{ saved: boolean }>(`/api/regions/${encodeURIComponent(name)}/settings`, {
			method: 'PUT',
			body: JSON.stringify(settings)
		}),

	sources: () =>
		request<{ sources: DemSource[]; openTopoKeyConfigured: boolean; h5Available: boolean }>(
			'/api/sources'
		),

	fetchDem: (bbox: BBox, dem: DemSettings, projection: ProjectionSettings) =>
		request<DemHandle>('/api/dem', {
			method: 'POST',
			body: JSON.stringify({ bbox, dem, projection })
		}),

	overlay: (bbox: BBox, kind: keyof OverlaySettings, dim: number) =>
		request<{ kind: string; mime: string; dataBase64: string }>('/api/overlay', {
			method: 'POST',
			body: JSON.stringify({ bbox, kind, dim })
		}),

	/** Elevation as raw float32 for the 3D view. Bytes, not base64 in JSON. */
	elevation: async (demId: string): Promise<Float32Array> => {
		const response = await fetch(`/api/dem/${demId}/elevation.bin`);
		if (!response.ok) throw new Error(await describeFailure(response));
		return new Float32Array(await response.arrayBuffer());
	},

	previewUrl: (demId: string, colormap = 'terrain') =>
		`/api/dem/${demId}/preview.png?colormap=${encodeURIComponent(colormap)}`,

	startExport: (demId: string, model: ModelSettings, exp: ExportSettings, name: string) =>
		request<JobSnapshot>('/api/export', {
			method: 'POST',
			body: JSON.stringify({ demId, model, export: exp, name })
		}),

	cancelJob: (jobId: string) =>
		request<{ cancelling: boolean }>(`/api/jobs/${jobId}`, { method: 'DELETE' }),

	downloadUrl: (jobId: string) => `/api/jobs/${jobId}/download`,

	/**
	 * Subscribe to a job's progress. Returns an unsubscribe function.
	 *
	 * The server pushes one event per real change. v1 polled every 250 ms for the whole
	 * render, which for a two-minute export is roughly 480 requests to observe about eight
	 * distinct states.
	 */
	watchJob(
		jobId: string,
		onUpdate: (snapshot: JobSnapshot) => void,
		onError: (message: string) => void
	): () => void {
		const source = new EventSource(`/api/jobs/${jobId}/events`);
		let finished = false;

		source.onmessage = (event) => {
			const snapshot = JSON.parse(event.data) as JobSnapshot;
			onUpdate(snapshot);
			if (snapshot.state !== 'running') {
				finished = true;
				source.close();
			}
		};
		source.onerror = () => {
			source.close();
			// EventSource fires onerror on normal stream close too, so only report a
			// failure if the job never reached a terminal state.
			if (!finished) onError('Lost contact with the export. It may still be running.');
		};

		return () => {
			finished = true;
			source.close();
		};
	}
};

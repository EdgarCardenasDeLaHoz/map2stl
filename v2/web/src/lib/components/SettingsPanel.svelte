<script lang="ts">
	/**
	 * Every setting in the pipeline, in four tiers.
	 *
	 * Terrain and Model are open by default because they are what a user changes on almost
	 * every render. Projection, Overlays and Export options start closed. That is the whole
	 * of the tiering, and it is possible only because each control binds to a field of one
	 * object rather than owning a DOM id that some other file reads back later.
	 *
	 * Controls that change what gets fetched call `invalidateTerrain`, which drops the
	 * loaded DEM and says so. Controls that only change how the fetched grid becomes a mesh
	 * call `touch`, which saves without refetching. v1 drew no such distinction, so the
	 * preview and the exported file could silently disagree.
	 */
	import { app } from '$lib/state.svelte';
	import Field from './Field.svelte';
	import Section from './Section.svelte';

	const PROJECTIONS = [
		{ id: 'none', label: 'None (plate carrée)' },
		{ id: 'cosine', label: 'Cosine correction' },
		{ id: 'mercator', label: 'Web Mercator' },
		{ id: 'equidistant', label: 'Equidistant cylindrical' },
		{ id: 'lambert', label: 'Lambert conformal' },
		{ id: 'sinusoidal', label: 'Sinusoidal' }
	];

	let s = $derived(app.settings);
	let selectedSource = $derived(app.sources.find((x) => x.id === s.dem.source));
</script>

<div class="panel">
	<Section title="Terrain" subtitle="What gets fetched. Changing any of these needs a reload.">
		<Field
			label="Elevation source"
			hint={selectedSource?.note ?? ''}
		>
			<select bind:value={s.dem.source} onchange={() => app.invalidateTerrain()}>
				{#each app.sources as source (source.id)}
					<option value={source.id} disabled={!source.available}>
						{source.label}{source.available ? '' : ' — unavailable'}
					</option>
				{/each}
			</select>
		</Field>

		<Field label="Resolution" value="{s.dem.dim} px" hint="Grid width. Cost rises with the square.">
			<input
				type="range"
				min="100"
				max="1600"
				step="50"
				bind:value={s.dem.dim}
				onchange={() => app.invalidateTerrain()}
			/>
		</Field>

		<Field label="Subtract water">
			<label class="check">
				<input
					type="checkbox"
					bind:checked={s.dem.subtractWater}
					onchange={() => app.invalidateTerrain()}
				/>
				<span>Cut lakes and sea below the surface</span>
			</label>
		</Field>
	</Section>

	<Section title="Model" subtitle="How the elevation grid becomes millimetres.">
		<Field label="Model height" value="{s.model.modelHeight} mm">
			<input
				type="range"
				min="2"
				max="120"
				step="1"
				bind:value={s.model.modelHeight}
				onchange={() => app.touch()}
			/>
		</Field>

		<Field label="Base thickness" value="{s.model.baseHeight} mm" hint="Solid plate under the terrain.">
			<input
				type="range"
				min="0"
				max="30"
				step="0.5"
				bind:value={s.model.baseHeight}
				onchange={() => app.touch()}
			/>
		</Field>

		<Field
			label="Vertical exaggeration"
			value="{s.model.exaggeration}x"
			hint="Applied before the height is normalised, so it changes the shape of the relief, not its size."
		>
			<input
				type="range"
				min="0.25"
				max="8"
				step="0.25"
				bind:value={s.model.exaggeration}
				onchange={() => app.touch()}
			/>
		</Field>

		<Field label="Scale" value="{s.model.mmPerPixel} mm/px">
			<input
				type="range"
				min="0.1"
				max="4"
				step="0.1"
				bind:value={s.model.mmPerPixel}
				onchange={() => app.touch()}
			/>
		</Field>

		<Field label="Sea-level cap">
			<label class="check">
				<input type="checkbox" bind:checked={s.model.seaLevelCap} onchange={() => app.touch()} />
				<span>Flatten everything below sea level to zero</span>
			</label>
		</Field>
	</Section>

	<Section title="Projection" advanced count={2} subtitle="Reshapes the fetched grid. Cheap — no refetch.">
		<Field label="Projection">
			<select bind:value={s.projection.name} onchange={() => app.invalidateTerrain()}>
				{#each PROJECTIONS as projection (projection.id)}
					<option value={projection.id}>{projection.label}</option>
				{/each}
			</select>
		</Field>
		<Field label="Clip invalid edges" hint="Trim the empty rows and columns a projection leaves behind.">
			<label class="check">
				<input
					type="checkbox"
					bind:checked={s.projection.clipValidRegion}
					onchange={() => app.invalidateTerrain()}
				/>
				<span>Crop to the valid region</span>
			</label>
		</Field>
	</Section>

	<Section title="Overlays" advanced count={3} subtitle="Preview imagery only. None of it reaches the mesh.">
		<Field label="Satellite">
			<label class="check">
				<input
					type="checkbox"
					bind:checked={s.overlays.satellite}
					onchange={() => {
						app.touch();
						void app.refreshOverlays();
					}}
				/>
				<span>ESRI world imagery</span>
			</label>
		</Field>
		<Field label="Water mask">
			<label class="check">
				<input
					type="checkbox"
					bind:checked={s.overlays.waterMask}
					onchange={() => {
						app.touch();
						void app.refreshOverlays();
					}}
				/>
				<span>Surface water from ESA WorldCover</span>
			</label>
		</Field>
		<Field label="Land cover">
			<label class="check">
				<input
					type="checkbox"
					bind:checked={s.overlays.landCover}
					onchange={() => {
						app.touch();
						void app.refreshOverlays();
					}}
				/>
				<span>ESA WorldCover classes</span>
			</label>
		</Field>
	</Section>

	<Section title="Export options" advanced count={3}>
		<Field label="Format">
			<select bind:value={s.export.format} onchange={() => app.touch()}>
				<option value="stl">STL — printing</option>
				<option value="obj">OBJ — editing</option>
				<option value="3mf">3MF — printing with metadata</option>
			</select>
		</Field>
		<Field label="Engrave a label">
			<label class="check">
				<input type="checkbox" bind:checked={s.export.engraveLabel} onchange={() => app.touch()} />
				<span>Sink text into the front edge</span>
			</label>
		</Field>
		{#if s.export.engraveLabel}
			<Field label="Label text">
				<input
					type="text"
					bind:value={s.export.labelText}
					placeholder={app.region?.label ?? app.region?.name ?? 'Region name'}
					oninput={() => app.touch()}
				/>
			</Field>
		{/if}
	</Section>
</div>

<style>
	.panel {
		display: flex;
		flex-direction: column;
	}
	.check {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		cursor: pointer;
		color: var(--text-dim);
		font-size: 0.75rem;
	}
	.check input {
		accent-color: var(--accent);
	}
</style>

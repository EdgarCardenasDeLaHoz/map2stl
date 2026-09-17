<script lang="ts">
	/**
	 * The fetched elevation grid, drawn as a coloured image, with the overlay stack on top.
	 *
	 * The image is a PNG rendered by the server. v1 shipped the raw float32 grid to the
	 * browser and coloured it in JavaScript, which for a 600x600 grid meant 1.4 MB over the
	 * wire and a per-pixel loop on the main thread just to look at the terrain. The PNG is
	 * roughly 40x smaller and the browser decodes it off-thread.
	 *
	 * Overlays are absolutely positioned siblings with opacity, not composited server-side,
	 * so toggling one costs nothing and never invalidates the terrain.
	 */
	import { app } from '$lib/state.svelte';
	import { api } from '$lib/api';

	const OVERLAY_LABELS: Record<string, string> = {
		satellite: 'Satellite',
		waterMask: 'Water',
		landCover: 'Land cover'
	};

	let opacity = $state(0.6);
	let dem = $derived(app.dem);
	let active = $derived(
		Object.keys(app.overlayImages).filter(
			(kind) => app.settings.overlays[kind as keyof typeof app.settings.overlays]
		)
	);
</script>

<div class="terrain">
	{#if dem}
		<div class="stack">
			<img class="layer base" src={api.previewUrl(dem.demId)} alt="Elevation preview" />
			{#each active as kind (kind)}
				<img
					class="layer overlay"
					style="opacity: {opacity}"
					src={app.overlayImages[kind]}
					alt={OVERLAY_LABELS[kind] ?? kind}
				/>
			{/each}
		</div>

		<div class="readout">
			<span>{dem.width} x {dem.height}</span>
			<span>{dem.minElevation.toFixed(0)} to {dem.maxElevation.toFixed(0)} m</span>
			<span>{dem.sourceUsed}</span>
			<span>{dem.fetchSeconds.toFixed(2)} s</span>
			{#if active.length}
				<label class="opacity">
					Overlay
					<input type="range" min="0" max="1" step="0.05" bind:value={opacity} />
				</label>
			{/if}
		</div>
	{:else}
		<p class="placeholder">No terrain loaded.</p>
	{/if}
</div>

<style>
	.terrain {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.8rem;
		height: 100%;
		padding: 1rem;
		overflow: auto;
	}
	.stack {
		position: relative;
		line-height: 0;
		max-width: 100%;
		max-height: 100%;
	}
	.layer {
		display: block;
		max-width: 100%;
		max-height: 68vh;
		image-rendering: auto;
		border-radius: 6px;
	}
	.overlay {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		pointer-events: none;
	}
	.readout {
		display: flex;
		align-items: center;
		gap: 1rem;
		font-size: 0.72rem;
		color: var(--text-dim);
		font-variant-numeric: tabular-nums;
	}
	.opacity {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.opacity input {
		width: 90px;
	}
	.placeholder {
		color: var(--text-dim);
		font-size: 0.82rem;
	}
</style>

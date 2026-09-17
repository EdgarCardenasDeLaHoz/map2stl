<script lang="ts">
	/**
	 * Leaflet map showing where the selected region is.
	 *
	 * Only the selected region gets a rectangle. v1 drew all 125 at once and attached a
	 * draggable edit marker to each at load; the markers were invisible until hovered, so
	 * the work was pure cost. Here selection drives the map, and the map is a locator
	 * rather than the primary control.
	 */
	import { app } from '$lib/state.svelte';
	import { onMount } from 'svelte';

	let container: HTMLDivElement;
	// Leaflet is untyped here on purpose: it is loaded dynamically so the module never
	// reaches the server-side prerender, where `window` does not exist.
	let map: any = null;
	let rect: any = null;
	let leaflet: any = null;

	onMount(() => {
		let disposed = false;
		(async () => {
			leaflet = (await import('leaflet')).default;
			await import('leaflet/dist/leaflet.css');
			if (disposed) return;
			map = leaflet.map(container, { zoomControl: true, attributionControl: false });
			leaflet
				.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18 })
				.addTo(map);
			map.setView([20, 0], 2);
			draw();
		})();

		return () => {
			disposed = true;
			map?.remove();
			map = null;
		};
	});

	$effect(() => {
		// Re-runs whenever the selected region changes.
		void app.region;
		draw();
	});

	function draw() {
		if (!map || !leaflet) return;
		if (rect) {
			rect.remove();
			rect = null;
		}
		const region = app.region;
		if (!region) return;
		const bounds = [
			[region.bbox.south, region.bbox.west],
			[region.bbox.north, region.bbox.east]
		];
		rect = leaflet
			.rectangle(bounds, { color: '#4ea1ff', weight: 2, fillOpacity: 0.08 })
			.addTo(map);
		map.fitBounds(bounds, { padding: [40, 40] });
	}
</script>

<div class="map" bind:this={container}></div>

<style>
	.map {
		width: 100%;
		height: 100%;
		background: var(--surface-2);
	}
	/* Leaflet injects its own DOM, so these have to escape component scoping. */
	:global(.leaflet-container) {
		background: var(--surface-2);
		font-family: inherit;
	}
</style>

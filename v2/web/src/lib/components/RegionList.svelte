<script lang="ts">
	/**
	 * The 125 saved locations, filterable.
	 *
	 * A plain list, rendered from an array. v1 drew the same regions as Leaflet rectangles
	 * and eagerly built a draggable edit marker for each one at page load — 125 marker
	 * objects nobody had asked for. Here the list is the primary way in and the map follows
	 * it, which is both faster and closer to how the regions are actually chosen.
	 */
	import { app } from '$lib/state.svelte';
	import type { Region } from '$lib/api';

	function span(region: Region): string {
		const ns = region.bbox.north - region.bbox.south;
		const ew = region.bbox.east - region.bbox.west;
		return `${ns.toFixed(2)}° × ${ew.toFixed(2)}°`;
	}
</script>

<div class="regions">
	<div class="search">
		<input
			type="search"
			placeholder="Filter {app.regions.length} regions"
			bind:value={app.regionFilter}
			aria-label="Filter regions by name"
		/>
	</div>

	<ul class="list">
		{#each app.visibleRegions as region (region.name)}
			<li>
				<button
					type="button"
					class="region"
					class:selected={app.region?.name === region.name}
					onclick={() => app.selectRegion(region)}
				>
					<span class="name">{region.name}</span>
					<span class="meta">
						{span(region)}
						<!-- Many v1 rows carry the placeholder label "coorlist", so the name is the
						     only reliable identifier. The label is shown after it when it adds something. -->
						{#if region.label && region.label !== region.name}· {region.label}{/if}
					</span>
				</button>
			</li>
		{:else}
			<li class="empty">Nothing matches “{app.regionFilter}”.</li>
		{/each}
	</ul>
</div>

<style>
	.regions {
		display: flex;
		flex-direction: column;
		min-height: 0;
		height: 100%;
	}
	.search {
		padding: 0.7rem;
		border-bottom: 1px solid var(--line);
	}
	.search input {
		width: 100%;
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0.35rem;
		overflow-y: auto;
		flex: 1;
		min-height: 0;
	}
	.region {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		width: 100%;
		padding: 0.4rem 0.55rem;
		background: none;
		border: none;
		border-radius: 5px;
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.region:hover {
		background: var(--surface-2);
	}
	.region.selected {
		background: var(--accent-soft);
		color: var(--accent);
	}
	.name {
		font-size: 0.82rem;
	}
	.meta {
		font-size: 0.68rem;
		color: var(--text-dim);
		font-variant-numeric: tabular-nums;
	}
	.region.selected .meta {
		color: var(--accent);
		opacity: 0.75;
	}
	.empty {
		padding: 1rem 0.6rem;
		color: var(--text-dim);
		font-size: 0.78rem;
	}
</style>

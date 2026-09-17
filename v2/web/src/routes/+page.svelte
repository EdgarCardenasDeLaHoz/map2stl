<script lang="ts">
	/**
	 * The whole application: regions on the left, a viewport in the middle, settings on the
	 * right, actions along the bottom.
	 *
	 * One screen, three panes, no navigation. v1 spread the same pipeline across tabbed views
	 * that each owned part of the state, so choosing a region, changing a setting and looking
	 * at the result meant moving between views and hoping they agreed. Here they cannot
	 * disagree, because there is one settings object and one DEM handle behind all three
	 * panes.
	 */
	import { onMount } from 'svelte';
	import { app } from '$lib/state.svelte';
	import RegionList from '$lib/components/RegionList.svelte';
	import SettingsPanel from '$lib/components/SettingsPanel.svelte';
	import MapView from '$lib/components/MapView.svelte';
	import TerrainView from '$lib/components/TerrainView.svelte';
	import ModelView from '$lib/components/ModelView.svelte';
	import ExportBar from '$lib/components/ExportBar.svelte';
	import Notices from '$lib/components/Notices.svelte';

	type Tab = 'map' | 'terrain' | 'model';
	let tab = $state<Tab>('map');

	const TABS: { id: Tab; label: string }[] = [
		{ id: 'map', label: 'Map' },
		{ id: 'terrain', label: 'Terrain' },
		{ id: 'model', label: '3D' }
	];

	onMount(() => {
		void app.init();
	});

	// Loading terrain is a good moment to show it. Switching only from the map keeps a user
	// who deliberately opened the 3D view where they put themselves.
	$effect(() => {
		if (app.dem && tab === 'map') tab = 'terrain';
	});
</script>

<svelte:head><title>strm2stl v2</title></svelte:head>

<div class="app">
	<header>
		<h1>strm2stl <span class="version">v2</span></h1>
		<span class="subject">{app.region?.name ?? 'No region selected'}</span>
	</header>

	<div class="body">
		<aside class="left">
			<RegionList />
		</aside>

		<main class="centre">
			<nav class="tabs">
				{#each TABS as entry (entry.id)}
					<button
						type="button"
						class:active={tab === entry.id}
						onclick={() => (tab = entry.id)}
					>
						{entry.label}
					</button>
				{/each}
			</nav>
			<div class="viewport">
				{#if tab === 'map'}
					<MapView />
				{:else if tab === 'terrain'}
					<TerrainView />
				{:else}
					<ModelView />
				{/if}
			</div>
		</main>

		<aside class="right">
			<SettingsPanel />
		</aside>
	</div>

	<ExportBar />
	<Notices />
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		height: 100vh;
		overflow: hidden;
	}
	header {
		display: flex;
		align-items: baseline;
		gap: 0.9rem;
		padding: 0.55rem 0.9rem;
		border-bottom: 1px solid var(--line);
		background: var(--surface-1);
	}
	h1 {
		margin: 0;
		font-size: 0.9rem;
		font-weight: 600;
		letter-spacing: 0.02em;
	}
	.version {
		color: var(--accent);
	}
	.subject {
		color: var(--text-dim);
		font-size: 0.78rem;
	}
	.body {
		flex: 1;
		display: grid;
		grid-template-columns: 250px 1fr 320px;
		min-height: 0;
	}
	.left {
		border-right: 1px solid var(--line);
		min-height: 0;
		background: var(--surface-1);
	}
	.right {
		border-left: 1px solid var(--line);
		overflow-y: auto;
		padding: 0 0.9rem 1.5rem;
		background: var(--surface-1);
	}
	.centre {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
	}
	.tabs {
		display: flex;
		gap: 0.2rem;
		padding: 0.35rem 0.5rem;
		border-bottom: 1px solid var(--line);
	}
	.tabs button {
		padding: 0.3rem 0.7rem;
		border: none;
		border-radius: 5px;
		background: none;
		color: var(--text-dim);
		font: inherit;
		font-size: 0.76rem;
		cursor: pointer;
	}
	.tabs button:hover {
		color: var(--text);
	}
	.tabs button.active {
		background: var(--accent-soft);
		color: var(--accent);
	}
	.viewport {
		flex: 1;
		min-height: 0;
	}

	@media (max-width: 1100px) {
		.body {
			grid-template-columns: 200px 1fr 280px;
		}
	}
</style>

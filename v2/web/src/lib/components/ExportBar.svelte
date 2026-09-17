<script lang="ts">
	/**
	 * The action bar: load, export, cancel, download — plus what the current settings will
	 * produce, in millimetres and triangles, before anything runs.
	 *
	 * Three things here that v1 did not have. The size readout, so an exaggeration typo costs
	 * a glance rather than a full render. A cancel button, because the server can now be told
	 * to stop between pipeline stages. And a reason next to a disabled button instead of a
	 * dead control — "Load the terrain first" rather than nothing happening on click.
	 */
	import { app } from '$lib/state.svelte';

	let job = $derived(app.job);
	let projection = $derived(app.projection);
	let blocked = $derived(app.exportBlockedReason);
</script>

<div class="bar">
	<div class="actions">
		<button
			type="button"
			class="primary"
			disabled={!app.region || app.loading}
			onclick={() => app.loadTerrain()}
		>
			{app.loading ? 'Loading terrain…' : app.dem ? 'Reload terrain' : 'Load terrain'}
		</button>

		<button
			type="button"
			class="primary accent"
			disabled={blocked !== null}
			title={blocked ?? ''}
			onclick={() => app.startExport()}
		>
			Export {app.settings.export.format.toUpperCase()}
		</button>

		{#if job?.state === 'running'}
			<button type="button" onclick={() => app.cancelExport()}>Cancel</button>
		{/if}

		{#if job?.state === 'done'}
			<button type="button" class="primary accent" onclick={() => app.download()}>
				Download
			</button>
		{/if}
	</div>

	<div class="status">
		{#if job?.state === 'running'}
			<div class="progress" role="progressbar" aria-valuenow={job.percent}>
				<div class="fill" style="width: {job.percent}%"></div>
			</div>
			<span class="line">{job.message} — {job.percent}% ({job.elapsedSeconds.toFixed(1)} s)</span>
		{:else if job?.state === 'done' && job.result.faceCount}
			<span class="line">
				{job.result.faceCount.toLocaleString()} faces ·
				{job.result.watertight ? 'watertight' : 'NOT watertight'} ·
				{job.result.sizeMm?.x.toFixed(0)} x {job.result.sizeMm?.y.toFixed(0)} x
				{job.result.sizeMm?.z.toFixed(0)} mm
			</span>
		{:else if job?.state === 'failed'}
			<span class="line error">{job.error}</span>
		{:else if blocked}
			<span class="line dim">{blocked}</span>
		{:else if projection}
			<span class="line dim">
				Will produce {projection.mmWidth.toFixed(0)} x {projection.mmDepth.toFixed(0)} x
				{projection.mmHeight.toFixed(0)} mm, about
				{projection.estimatedFaces.toLocaleString()} faces
			</span>
		{/if}
	</div>

	<div class="save">
		{#if app.saving}
			<span class="dim">Saving…</span>
		{:else if app.dirty}
			<span class="dim">Unsaved</span>
		{:else if app.region}
			<span class="dim">Saved</span>
		{/if}
	</div>
</div>

<style>
	.bar {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.6rem 0.9rem;
		border-top: 1px solid var(--line);
		background: var(--surface-1);
	}
	.actions {
		display: flex;
		gap: 0.45rem;
		flex-shrink: 0;
	}
	button {
		padding: 0.4rem 0.8rem;
		border: 1px solid var(--line);
		border-radius: 5px;
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
		font-size: 0.78rem;
		cursor: pointer;
	}
	button:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	button.accent:not(:disabled) {
		background: var(--accent);
		border-color: var(--accent);
		color: #0d1014;
	}
	button.accent:hover:not(:disabled) {
		color: #0d1014;
		filter: brightness(1.1);
	}
	.status {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.progress {
		flex: 0 0 140px;
		height: 5px;
		border-radius: 999px;
		background: var(--surface-2);
		overflow: hidden;
	}
	.fill {
		height: 100%;
		background: var(--accent);
		transition: width 0.2s ease;
	}
	.line {
		font-size: 0.74rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.dim {
		color: var(--text-dim);
		font-size: 0.74rem;
	}
	.error {
		color: #ff8a80;
	}
	.save {
		flex-shrink: 0;
	}
</style>

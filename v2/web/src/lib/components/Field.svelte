<script lang="ts">
	/**
	 * One labelled control.
	 *
	 * Every setting in v2 goes through this, which is what makes tiering a one-word change:
	 * marking a field `advanced` moves it behind a disclosure without touching its binding,
	 * its label, or the code that reads it. v1 had 306 controls presented at identical
	 * prominence because there was no shared wrapper to tier them with — the split would
	 * have had to be made by hand, in markup, 306 times.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		label: string;
		hint?: string;
		/** Shown next to the label — the unit, or the live value of a slider. */
		value?: string;
		children: Snippet;
	}

	let { label, hint = '', value = '', children }: Props = $props();
</script>

<label class="field">
	<span class="field-label">
		<span class="field-name">{label}</span>
		{#if value}<span class="field-value">{value}</span>{/if}
	</span>
	{@render children()}
	{#if hint}<span class="field-hint">{hint}</span>{/if}
</label>

<style>
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.8rem;
	}
	.field-label {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.5rem;
	}
	.field-name {
		color: var(--text);
		font-weight: 500;
	}
	.field-value {
		color: var(--text-dim);
		font-variant-numeric: tabular-nums;
		font-size: 0.75rem;
	}
	.field-hint {
		color: var(--text-dim);
		font-size: 0.7rem;
		line-height: 1.35;
	}
</style>

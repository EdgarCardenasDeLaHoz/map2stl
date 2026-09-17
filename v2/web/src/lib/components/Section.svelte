<script lang="ts">
	/**
	 * A group of fields, optionally collapsed.
	 *
	 * The `advanced` flag is the tiering mechanism: an advanced section starts closed and
	 * says how many settings it holds, so a user can see that there is more without having
	 * to look at it. Open state is remembered per section for the browser session, because
	 * a user who opens "Projection" once usually wants it open next time too.
	 */
	import { untrack, type Snippet } from 'svelte';

	interface Props {
		title: string;
		subtitle?: string;
		advanced?: boolean;
		count?: number;
		children: Snippet;
	}

	let { title, subtitle = '', advanced = false, count = 0, children }: Props = $props();

	// A section's title identifies it for the lifetime of the component; reading it once is
	// intended, and `untrack` says so rather than leaving a reactivity warning behind.
	const storageKey = `v2.section.${untrack(() => title)}`;
	let open = $state(readOpen());

	function readOpen(): boolean {
		try {
			const stored = sessionStorage.getItem(storageKey);
			if (stored !== null) return stored === '1';
		} catch {
			// Private windows and blocked site data both throw here. The default below is
			// correct on its own; remembering the choice is a convenience, not a requirement.
		}
		return !advanced;
	}

	$effect(() => {
		try {
			sessionStorage.setItem(storageKey, open ? '1' : '0');
		} catch {
			// See above — failing to remember is not worth surfacing.
		}
	});
</script>

<section class="section" class:open>
	<button
		type="button"
		class="section-head"
		aria-expanded={open}
		onclick={() => (open = !open)}
	>
		<span class="chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
		<span class="section-title">{title}</span>
		{#if !open && count}<span class="section-count">{count}</span>{/if}
	</button>
	{#if subtitle && open}
		<p class="section-subtitle">{subtitle}</p>
	{/if}
	{#if open}
		<div class="section-body">
			{@render children()}
		</div>
	{/if}
</section>

<style>
	.section {
		border-top: 1px solid var(--line);
	}
	.section-head {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		width: 100%;
		padding: 0.7rem 0 0.55rem;
		background: none;
		border: none;
		color: var(--text);
		font: inherit;
		font-size: 0.78rem;
		font-weight: 600;
		letter-spacing: 0.02em;
		text-transform: uppercase;
		cursor: pointer;
		text-align: left;
	}
	.section-head:hover {
		color: var(--accent);
	}
	.chevron {
		color: var(--text-dim);
		font-size: 0.7rem;
	}
	.section-title {
		flex: 1;
	}
	.section-count {
		color: var(--text-dim);
		font-weight: 400;
		font-size: 0.7rem;
		background: var(--surface-2);
		border-radius: 999px;
		padding: 0.05rem 0.45rem;
	}
	.section-subtitle {
		margin: 0 0 0.6rem;
		color: var(--text-dim);
		font-size: 0.72rem;
		line-height: 1.4;
	}
	.section-body {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
		padding-bottom: 0.9rem;
	}
</style>

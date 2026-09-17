<script lang="ts">
	/**
	 * Transient messages, stacked in a corner.
	 *
	 * Errors have no timer — they stay until dismissed. v1 faded every message, failures
	 * included, after two seconds, so an export that died while the user looked at something
	 * else left no evidence that it had ever run.
	 */
	import { app } from '$lib/state.svelte';
</script>

<div class="notices" aria-live="polite">
	{#each app.notices as notice (notice.id)}
		<div class="notice {notice.kind}">
			<span class="text">{notice.text}</span>
			<button type="button" aria-label="Dismiss" onclick={() => app.dismiss(notice.id)}>×</button>
		</div>
	{/each}
</div>

<style>
	.notices {
		position: fixed;
		right: 1rem;
		bottom: 3.6rem;
		z-index: 50;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		max-width: 26rem;
	}
	.notice {
		display: flex;
		align-items: flex-start;
		gap: 0.6rem;
		padding: 0.5rem 0.7rem;
		border-radius: 6px;
		border: 1px solid var(--line);
		background: var(--surface-1);
		box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
		font-size: 0.76rem;
		line-height: 1.4;
	}
	.notice.error {
		border-color: #a3423a;
		color: #ffb4ac;
	}
	.notice.success {
		border-color: #3f7d52;
		color: #b5e6c2;
	}
	.text {
		flex: 1;
	}
	button {
		background: none;
		border: none;
		color: inherit;
		opacity: 0.6;
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
	}
	button:hover {
		opacity: 1;
	}
</style>

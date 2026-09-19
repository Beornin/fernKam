<script lang="ts">
	/** Renders the dialog queue. Mounted once in the root layout. */
	import { dialogQueue, settle } from '$lib/dialog.svelte';
	import { AlertTriangle, Info } from '@lucide/svelte';

	const current = $derived(dialogQueue[0] ?? null);

	function onKey(e: KeyboardEvent) {
		if (!current) return;
		if (e.key === 'Escape') { e.preventDefault(); settle(current, false); }
		else if (e.key === 'Enter') { e.preventDefault(); settle(current, true); }
	}

	// Focus the safe action, so Enter confirms and Escape cancels without
	// the person having to reach for the mouse.
	let confirmBtn = $state<HTMLButtonElement | undefined>(undefined);
	$effect(() => { if (current) confirmBtn?.focus(); });
</script>

<svelte:window onkeydown={onKey} />

{#if current}
	<!-- Backdrop click cancels, matching every other dismissable surface here -->
	<div
		class="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4"
		role="presentation"
		onclick={(e) => { if (e.target === e.currentTarget) settle(current, false); }}
	>
		<div
			role="alertdialog"
			aria-modal="true"
			aria-label={current.kind === 'ask' ? 'Confirm' : 'Notice'}
			class="w-full max-w-sm bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl overflow-hidden"
		>
			<div class="p-5 flex gap-3">
				<div class="shrink-0 mt-0.5">
					{#if current.tone === 'danger'}
						<AlertTriangle size={18} class="text-amber-400" />
					{:else}
						<Info size={18} class="text-zinc-400" />
					{/if}
				</div>
				<p class="text-sm text-zinc-200 leading-relaxed whitespace-pre-line">{current.message}</p>
			</div>
			<div class="px-5 py-3 bg-zinc-950/60 border-t border-zinc-800 flex justify-end gap-2">
				{#if current.kind === 'ask'}
					<button
						onclick={() => settle(current, false)}
						class="px-3 py-1.5 text-xs rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
					>Cancel</button>
				{/if}
				<button
					bind:this={confirmBtn}
					onclick={() => settle(current, true)}
					class="px-3 py-1.5 text-xs rounded-lg text-white font-medium transition-colors
						{current.tone === 'danger' ? 'bg-amber-600 hover:bg-amber-500' : 'bg-zinc-700 hover:bg-zinc-600'}"
				>{current.kind === 'ask' ? 'Confirm' : 'OK'}</button>
			</div>
		</div>
	</div>
{/if}

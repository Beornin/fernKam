<script lang="ts">
	import { goto } from '$app/navigation';
	import { api, type OutsideChange } from '$lib/api';
	import { COLOR_LABELS } from '$lib/colorLabels';
	import { ask, notify } from '$lib/dialog.svelte';
	import { FileDiff, RotateCcw, Check, ExternalLink, Loader2 } from '@lucide/svelte';

	let changes = $state<OutsideChange[]>([]);
	let loading = $state(true);
	let showDismissed = $state(false);
	let busy = $state<number | null>(null);

	const FIELD_LABEL: Record<string, string> = {
		rating: 'Rating', color_label: 'Colour label', title: 'Title', caption: 'Caption',
	};

	function show(field: string, v: unknown): string {
		if (v === null || v === undefined || v === '') return '(none)';
		if (field === 'rating') return Number(v) === 0 ? 'no stars' : `${v}★`;
		if (field === 'color_label') return COLOR_LABELS.find(c => c.value === Number(v))?.name ?? 'none';
		const s = String(v);
		return `“${s.length > 60 ? s.slice(0, 60) + '…' : s}”`;
	}

	async function load() {
		loading = true;
		try {
			changes = (await api.outsideChanges.list(showDismissed)).changes;
		} catch (e) {
			notify(`Could not load changes: ${e}`);
		} finally {
			loading = false;
		}
	}

	$effect(() => { void showDismissed; load(); });

	async function restore(c: OutsideChange) {
		busy = c.id;
		try {
			const r = await api.outsideChanges.restore(c.id);
			const what = Object.keys(r.restored).map(f => FIELD_LABEL[f] ?? f).join(', ');
			notify(`Restored your ${what} for ${c.filename}. It will be written to the file on the next write-back.`);
			await load();
		} catch (e) {
			notify(`Could not restore: ${e}`);
		} finally {
			busy = null;
		}
	}

	async function dismiss(c: OutsideChange) {
		busy = c.id;
		try {
			await api.outsideChanges.dismiss({ ids: [c.id] });
			await load();
		} finally {
			busy = null;
		}
	}

	async function dismissAll() {
		const open = changes.filter(c => !c.dismissed);
		const withConflicts = open.filter(c => Object.keys(c.details.conflicts ?? {}).length).length;
		if (withConflicts && !(await ask(`${withConflicts} of these replaced an unsaved fernKam edit. Dismiss all anyway? The file's values stay.`))) return;
		await api.outsideChanges.dismiss({ all: true });
		await load();
	}

	function openPhoto(c: OutsideChange) {
		goto(`/photos?photo_id=${c.photo_id}&back=${encodeURIComponent('/outside-changes')}`);
	}

	const openCount = $derived(changes.filter(c => !c.dismissed).length);
</script>

<div class="p-6 max-w-5xl">
	<div class="flex items-center gap-3 mb-2">
		<FileDiff size={22} class="text-sky-400" />
		<h1 class="text-2xl font-semibold text-zinc-100">Changed outside fernKam</h1>
	</div>
	<p class="text-sm text-zinc-400 mb-5 max-w-3xl">
		When another program edits a photo in your library, fernKam reads the change and keeps it. If you had
		also changed the same thing in fernKam and it wasn't written to the file yet, the outside edit wins and
		your value is shown here so you can restore it.
	</p>

	<div class="flex items-center gap-4 mb-4">
		<button
			onclick={dismissAll}
			disabled={openCount === 0}
			class="px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-200 text-sm hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed"
		>
			Dismiss all
		</button>
		<label class="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer">
			<input type="checkbox" bind:checked={showDismissed} class="accent-sky-500" />
			Show dismissed
		</label>
	</div>

	{#if loading}
		<div class="flex items-center gap-2 text-zinc-500 text-sm"><Loader2 size={16} class="animate-spin" /> Loading…</div>
	{:else if changes.length === 0}
		<div class="text-zinc-500 text-sm">Nothing has changed outside fernKam.</div>
	{:else}
		<div class="space-y-2">
			{#each changes as c (c.id)}
				{@const conflicts = Object.entries(c.details.conflicts ?? {})}
				<div class="flex gap-4 p-3 rounded-xl border {c.dismissed ? 'border-zinc-900 bg-zinc-950 opacity-60' : conflicts.length ? 'border-amber-500/40 bg-amber-500/5' : 'border-zinc-800 bg-zinc-900'}">
					<button onclick={() => openPhoto(c)} class="shrink-0" title="Open photo">
						<img src="/media/thumbnail/{c.photo_id}?size=sm" alt={c.filename} class="w-20 h-20 object-cover rounded-lg bg-zinc-800" loading="lazy" />
					</button>
					<div class="flex-1 min-w-0">
						<div class="flex items-baseline gap-2">
							<span class="text-zinc-100 font-medium truncate">{c.filename}</span>
							<span class="text-xs text-zinc-500 truncate">{c.album_path}</span>
							<span class="ml-auto text-xs text-zinc-500 shrink-0">{new Date(c.detected_at).toLocaleString()}</span>
						</div>

						<div class="mt-1.5 text-sm text-zinc-300 space-y-1">
							{#if c.kind === 'metadata'}
								{#each Object.entries(c.details.fields ?? {}) as [f, v]}
									{#if !c.details.conflicts?.[f]}
										<div>{FIELD_LABEL[f] ?? f}: {show(f, v.from)} → {show(f, v.to)}</div>
									{/if}
								{/each}
								{#if c.details.tags_added?.length || c.details.tags_removed?.length}
									<div class="flex flex-wrap gap-1.5">
										{#each c.details.tags_added ?? [] as t}
											<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-xs">+ {t}</span>
										{/each}
										{#each c.details.tags_removed ?? [] as t}
											<span class="px-1.5 py-0.5 rounded bg-red-500/15 text-red-300 text-xs line-through">{t}</span>
										{/each}
									</div>
								{/if}
								{#each conflicts as [f, v]}
									<div class="text-amber-300">
										{FIELD_LABEL[f] ?? f}: you had {show(f, v.fernkam)} in fernKam (not yet written to the file) —
										replaced by {show(f, v.file)} from the file.
									</div>
								{/each}
								{#if c.details.restored}
									<div class="text-emerald-400 text-xs">Your value was restored.</div>
								{/if}
							{:else if c.kind === 'pixels'}
								<div>The image itself changed. Thumbnails, faces and the search index were refreshed.</div>
							{:else if c.kind === 'moved'}
								<div>Moved or renamed: {c.details.from} → {c.details.to}. Tags, faces and ratings were kept.</div>
							{:else}
								<div class="text-red-300">{c.details.note}</div>
							{/if}
						</div>

						{#if !c.dismissed}
							<div class="mt-2 flex gap-2">
								{#if conflicts.length && !c.details.restored}
									<button
										onclick={() => restore(c)}
										disabled={busy === c.id}
										class="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-500 text-zinc-900 text-xs font-medium hover:bg-amber-400 disabled:opacity-50"
									>
										<RotateCcw size={12} /> Restore fernKam's value
									</button>
								{/if}
								<button
									onclick={() => dismiss(c)}
									disabled={busy === c.id}
									class="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-zinc-800 text-zinc-300 text-xs hover:bg-zinc-700 disabled:opacity-50"
								>
									<Check size={12} /> {conflicts.length ? "Keep the file's value" : 'Dismiss'}
								</button>
								<button
									onclick={() => openPhoto(c)}
									class="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-zinc-400 text-xs hover:text-zinc-200"
								>
									<ExternalLink size={12} /> Open
								</button>
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<script lang="ts">
	import { api } from '$lib/api';
	import { onMount } from 'svelte';
	import { CalendarClock, Check, X, ChevronDown, Info } from '@lucide/svelte';

	type Candidate = {
		id: number;
		filename: string;
		album_path: string;
		inferred_date: string;
		source: string;
	};

	const SOURCE_LABELS: Record<string, string> = {
		filename_ymd:       'Filename YYYY-MM-DD',
		filename_yyyymmdd:  'Filename YYYYMMDD',
		folder_year:        'Folder year',
		folder_year_month:  'Folder year/month',
	};

	let totalUndated = $state(0);
	let candidates = $state<Candidate[]>([]);
	let loading = $state(true);
	let applying = $state(false);
	let applied = $state<{ updated: number; skipped: number } | null>(null);
	let error = $state('');

	let offset = $state(0);
	const LIMIT = 500;
	let hasMore = $state(false);

	let selectedIds = $state(new Set<number>());
	let allSelected = $derived(candidates.length > 0 && selectedIds.size === candidates.length);

	function toggleAll() {
		if (allSelected) selectedIds = new Set();
		else selectedIds = new Set(candidates.map(c => c.id));
	}

	function toggle(id: number) {
		const s = new Set(selectedIds);
		if (s.has(id)) s.delete(id); else s.add(id);
		selectedIds = s;
	}

	async function load(reset = true) {
		loading = true;
		try {
			const data = await api.photos.inferDatesPreview({ limit: LIMIT, offset: reset ? 0 : offset });
			totalUndated = data.total_undated;
			candidates = reset ? data.candidates : [...candidates, ...data.candidates];
			offset = (reset ? 0 : offset) + data.candidates.length;
			hasMore = data.candidates.length === LIMIT;
			if (reset) selectedIds = new Set(data.candidates.map(c => c.id));
		} finally {
			loading = false;
		}
	}

	async function apply() {
		if (!selectedIds.size) return;
		applying = true; error = '';
		try {
			const result = await api.photos.inferDatesApply([...selectedIds]);
			applied = result;
			await load(true);
		} catch (e: any) {
			error = e.message ?? 'Failed';
		} finally {
			applying = false;
		}
	}

	onMount(() => load());
</script>

<div class="flex flex-col h-full overflow-hidden">
	<!-- Header -->
	<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
		<CalendarClock size={16} class="text-amber-400" />
		<span class="text-sm font-semibold text-zinc-200">Date Inference</span>
		<span class="text-xs text-zinc-500">
			{totalUndated.toLocaleString()} undated photos ·
			{candidates.length.toLocaleString()} inferable
		</span>
		<div class="flex-1"></div>

		{#if applied}
			<span class="text-xs text-emerald-400">
				Applied {applied.updated.toLocaleString()} dates
				{#if applied.skipped > 0}· {applied.skipped} skipped{/if}
			</span>
		{/if}
		{#if error}
			<span class="text-xs text-red-400">{error}</span>
		{/if}

		<button onclick={toggleAll}
			class="text-xs px-2 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors">
			{allSelected ? 'Deselect all' : 'Select all'}
		</button>
		<button
			onclick={apply}
			disabled={applying || selectedIds.size === 0}
			class="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-40 transition-colors">
			{#if applying}
				<div class="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin"></div>
				Applying…
			{:else}
				<Check size={12} /> Apply {selectedIds.size.toLocaleString()} dates
			{/if}
		</button>
	</div>

	<!-- Info banner -->
	<div class="shrink-0 mx-4 mt-3 mb-1 flex items-start gap-2 text-[11px] text-zinc-500 bg-zinc-800/50 rounded px-3 py-2">
		<Info size={12} class="mt-0.5 text-zinc-600 shrink-0" />
		<span>
			Dates are inferred from filename patterns (<code class="text-zinc-400">YYYY-MM-DD</code>, <code class="text-zinc-400">YYYYMMDD</code>)
			and folder names containing a 4-digit year. Folder-year inference sets the date to <strong>Jan 1</strong> of that year.
			Review and deselect any rows you disagree with before applying.
		</span>
	</div>

	<!-- Table -->
	<div class="flex-1 overflow-y-auto px-4 py-2">
		{#if loading}
			<div class="flex items-center justify-center h-40 gap-2 text-zinc-500">
				<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
				Scanning…
			</div>
		{:else if candidates.length === 0}
			<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-600">
				<CalendarClock size={40} class="opacity-30" />
				<p class="text-sm">No inferable dates found in remaining {totalUndated.toLocaleString()} undated photos</p>
			</div>
		{:else}
			<table class="w-full text-xs border-collapse">
				<thead class="sticky top-0 bg-zinc-900 z-10">
					<tr class="text-zinc-500 text-left">
						<th class="py-1.5 pr-3 w-6">
							<button onclick={toggleAll} class="text-zinc-400 hover:text-amber-400 transition-colors">
								<Check size={11} class={allSelected ? 'text-amber-400' : 'text-zinc-600'} />
							</button>
						</th>
						<th class="py-1.5 pr-4">Filename</th>
						<th class="py-1.5 pr-4">Album Path</th>
						<th class="py-1.5 pr-4">Inferred Date</th>
						<th class="py-1.5">Source</th>
					</tr>
				</thead>
				<tbody>
					{#each candidates as c (c.id)}
						{@const sel = selectedIds.has(c.id)}
						<tr onclick={() => toggle(c.id)}
							class="border-t border-zinc-800 cursor-pointer transition-colors
								{sel ? 'bg-amber-600/5' : 'hover:bg-zinc-800/40'}">
							<td class="py-1 pr-3">
								<div class="w-3.5 h-3.5 rounded border {sel ? 'bg-amber-500 border-amber-500' : 'border-zinc-600'} flex items-center justify-center">
									{#if sel}<Check size={9} class="text-white" />{/if}
								</div>
							</td>
							<td class="py-1 pr-4 font-mono text-zinc-300 max-w-[260px] truncate" title={c.filename}>{c.filename}</td>
							<td class="py-1 pr-4 text-zinc-500 max-w-[260px] truncate" title={c.album_path}>{c.album_path}</td>
							<td class="py-1 pr-4 text-emerald-400 font-mono whitespace-nowrap">
								{new Date(c.inferred_date).toLocaleDateString(undefined, { year:'numeric', month:'short', day:'numeric' })}
							</td>
							<td class="py-1 text-zinc-500 whitespace-nowrap">{SOURCE_LABELS[c.source] ?? c.source}</td>
						</tr>
					{/each}
				</tbody>
			</table>

			{#if hasMore}
				<div class="flex justify-center py-4">
					<button onclick={() => load(false)} disabled={loading}
						class="flex items-center gap-1.5 px-4 py-2 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors disabled:opacity-50">
						<ChevronDown size={13} /> Load more
					</button>
				</div>
			{/if}
		{/if}
	</div>
</div>

<script lang="ts">
	import { api } from '$lib/api';
	import { onMount } from 'svelte';
	import { Copy, Trash2, RefreshCw, ChevronDown } from '@lucide/svelte';
	import { statusCountStore } from '$lib/stores';

	type DupPhoto = { id: number; filename: string; album_path: string; taken_at: string | null; file_size: number | null };
	type DupGroup = { sha256: string; count: number; photos: DupPhoto[] };

	let stats = $state<{ hashed: number; unhashed: number; total: number; duplicate_groups: number; wasted_bytes: number } | null>(null);
	let groups = $state<DupGroup[]>([]);
	let totalGroups = $state(0);
	let page = $state(1);
	let loading = $state(false);
	let loadingMore = $state(false);
	let computing = $state(false);
	let taskId = $state('');

	const PAGE_SIZE = 50;

	function fmt(bytes: number | null) {
		if (!bytes) return '—';
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	}

	async function loadStats() {
		stats = await api.dedup.stats();
		statusCountStore.set(`${stats.duplicate_groups} dup groups · ${fmt(stats.wasted_bytes)} wasted`);
	}

	async function loadGroups(reset = true) {
		if (reset) { page = 1; groups = []; }
		loading = true;
		try {
			const data = await api.dedup.groups({ page, page_size: PAGE_SIZE });
			groups = reset ? data.groups : [...groups, ...data.groups];
			totalGroups = data.total_groups;
		} finally {
			loading = false;
		}
	}

	async function loadMore() {
		if (loadingMore || groups.length >= totalGroups) return;
		loadingMore = true;
		page += 1;
		try {
			const data = await api.dedup.groups({ page, page_size: PAGE_SIZE });
			groups = [...groups, ...data.groups];
		} finally {
			loadingMore = false;
		}
	}

	async function startCompute() {
		computing = true;
		try {
			const r = await api.dedup.computeHashes();
			taskId = r.task_id;
			alert(`Hash computation started (task ${r.task_id}). Check Tasks page for progress, then reload this page.`);
		} catch (e: any) {
			alert(e.message ?? 'Failed to start');
		} finally {
			computing = false;
		}
	}

	async function trashPhoto(groupIdx: number, photo: DupPhoto) {
		if (!confirm(`Trash "${photo.filename}"? This moves it to the Recycle Bin.`)) return;
		await api.photos.trash(photo.id);
		groups = groups.map((g, i) => i !== groupIdx ? g : {
			...g,
			count: g.count - 1,
			photos: g.photos.filter(p => p.id !== photo.id),
		}).filter(g => g.count > 1);
		totalGroups = groups.length;
		await loadStats();
	}

	onMount(async () => {
		await loadStats();
		if (stats && stats.duplicate_groups > 0) await loadGroups();
	});
</script>

<div class="flex flex-col h-full overflow-hidden">
	<!-- Header -->
	<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
		<Copy size={18} class="text-amber-400 shrink-0" />
		<h1 class="text-base font-semibold text-zinc-100">Duplicate Detection</h1>
		<div class="ml-auto flex items-center gap-3">
			{#if stats}
				<span class="text-xs text-zinc-500">
					{stats.hashed.toLocaleString()} / {stats.total.toLocaleString()} hashed
					{#if stats.unhashed > 0}
						· <span class="text-amber-400">{stats.unhashed.toLocaleString()} pending</span>
					{/if}
				</span>
			{/if}
			{#if stats && stats.unhashed > 0}
				<button onclick={startCompute} disabled={computing}
					class="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50 transition-colors">
					<RefreshCw size={12} class={computing ? 'animate-spin' : ''} />
					{computing ? 'Starting…' : 'Compute missing hashes'}
				</button>
			{/if}
			<button onclick={() => { loadStats(); loadGroups(); }}
				class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors">
				<RefreshCw size={12} /> Refresh
			</button>
		</div>
	</div>

	<!-- Stats bar -->
	{#if stats}
		<div class="shrink-0 px-5 py-2 bg-zinc-900/30 border-b border-zinc-800/50 flex items-center gap-6 text-xs">
			<span><span class="text-zinc-200 font-medium">{stats.duplicate_groups.toLocaleString()}</span> <span class="text-zinc-500">duplicate groups</span></span>
			<span><span class="text-zinc-200 font-medium">{fmt(stats.wasted_bytes)}</span> <span class="text-zinc-500">potentially reclaimable</span></span>
		</div>
	{/if}

	<!-- Groups list -->
	<div class="flex-1 overflow-y-auto p-5" onscroll={(e) => { const el = e.currentTarget; if (el.scrollHeight - el.scrollTop - el.clientHeight < 400) loadMore(); }}>
		{#if loading}
			<div class="flex justify-center py-16">
				<div class="w-6 h-6 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
			</div>
		{:else if groups.length === 0 && stats}
			{#if stats.unhashed > 0}
				<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-500">
					<Copy size={40} class="opacity-30" />
					<p class="text-sm">No hashes computed yet.</p>
					<p class="text-xs text-zinc-600">Click "Compute missing hashes" to scan your library for duplicates.</p>
				</div>
			{:else}
				<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-500">
					<Copy size={40} class="opacity-20 text-emerald-400" />
					<p class="text-sm text-emerald-400">No duplicates found!</p>
				</div>
			{/if}
		{:else}
			<div class="space-y-4">
				{#each groups as group, gi (group.sha256)}
					<div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
						<div class="px-4 py-2 bg-zinc-800/50 flex items-center gap-3">
							<Copy size={13} class="text-amber-400 shrink-0" />
							<span class="text-xs font-mono text-zinc-400 truncate flex-1">{group.sha256}</span>
							<span class="text-xs text-zinc-500 shrink-0">{group.count} copies</span>
						</div>
						<div class="divide-y divide-zinc-800/60">
							{#each group.photos as photo, pi (photo.id)}
								<div class="flex items-center gap-3 px-4 py-2.5 hover:bg-zinc-800/30 transition-colors group">
									<img src="http://localhost:8000/media/thumbnail/{photo.id}?size=sm" alt={photo.filename}
										class="w-10 h-10 object-cover rounded shrink-0 bg-zinc-800" loading="lazy" />
									<div class="flex-1 min-w-0">
										<p class="text-xs text-zinc-200 truncate font-medium">{photo.filename}</p>
										<p class="text-[11px] text-zinc-500 truncate">{photo.album_path}</p>
									</div>
									<div class="shrink-0 text-right">
										<p class="text-[11px] text-zinc-500">{photo.taken_at ? new Date(photo.taken_at).toLocaleDateString() : '—'}</p>
										<p class="text-[11px] text-zinc-600">{fmt(photo.file_size)}</p>
									</div>
									{#if pi > 0}
										<button onclick={() => trashPhoto(gi, photo)}
											class="shrink-0 ml-1 p-1.5 rounded opacity-0 group-hover:opacity-100 hover:bg-red-500/20 text-zinc-500 hover:text-red-400 transition-all"
											title="Trash this duplicate">
											<Trash2 size={14} />
										</button>
									{:else}
										<div class="shrink-0 ml-1 w-7 flex items-center justify-center">
											<span class="text-[10px] text-zinc-600 px-1">keep</span>
										</div>
									{/if}
								</div>
							{/each}
						</div>
					</div>
				{/each}
			</div>

			{#if loadingMore}
				<div class="flex justify-center py-4">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
				</div>
			{:else if groups.length < totalGroups}
				<div class="flex justify-center py-4">
					<button onclick={loadMore}
						class="flex items-center gap-2 px-4 py-2 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors">
						<ChevronDown size={13} /> Load more ({(totalGroups - groups.length).toLocaleString()} remaining)
					</button>
				</div>
			{/if}
		{/if}
	</div>
</div>

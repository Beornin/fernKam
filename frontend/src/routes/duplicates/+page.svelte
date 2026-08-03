<script lang="ts">
	import { api, type DedupPhoto, type DedupAutoCleanPlan } from '$lib/api';
	import { onMount } from 'svelte';
	import { Copy, Trash2, RefreshCw, ChevronDown, Sparkles, X, AlertTriangle, Video } from '@lucide/svelte';
	import { statusCountStore } from '$lib/stores';
	import { throttle } from '$lib/throttle';
	import { formatBytes } from '$lib/format';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';

	type DupGroup = { sha256: string; count: number; photos: DedupPhoto[] };

	let stats = $state<{ hashed: number; unhashed: number; total: number; duplicate_groups: number; wasted_bytes: number } | null>(null);
	let groups = $state<DupGroup[]>([]);
	let totalGroups = $state(0);
	let page = $state(1);
	let loading = $state(false);
	let loadingMore = $state(false);
	let computing = $state(false);
	let taskId = $state('');

	// Fullscreen preview — double-click any thumbnail (main list or the
	// auto-clean review panel) to validate before trashing/confirming.
	let lightboxPhotoId = $state<number | null>(null);

	// Auto-clean: preview-then-confirm, per the folder-priority rule.
	let autoCleanPlan = $state<DedupAutoCleanPlan | null>(null);
	let autoCleanLoading = $state(false);
	let autoCleanPanelOpen = $state(false);
	let autoCleanApplying = $state(false);
	let autoCleanProgress = $state<string | null>(null);

	const PAGE_SIZE = 50;

	const TIER_INFO: Record<number, { label: string; cls: string }> = {
		0: { label: 'Staging', cls: 'bg-zinc-700 text-zinc-300' },
		1: { label: 'Archive', cls: 'bg-sky-700/60 text-sky-200' },
		2: { label: 'Special', cls: 'bg-amber-700/60 text-amber-200' },
	};

	async function loadStats() {
		stats = await api.dedup.stats();
		statusCountStore.set(`${stats.duplicate_groups} dup groups · ${formatBytes(stats.wasted_bytes)} wasted`);
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

	// The threshold check itself is cheap, but a fast scroll/fling fires the
	// onscroll event dozens of times per second — throttle so it runs at most
	// once per 150ms instead of on every tick.
	const onGroupsScroll = throttle((el: HTMLElement) => {
		if (el.scrollHeight - el.scrollTop - el.clientHeight < 400) loadMore();
	}, 150);

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

	async function trashPhoto(groupIdx: number, photo: DedupPhoto) {
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

	async function pollBgTask(id: string, onDone: (message: string) => void) {
		for (;;) {
			await new Promise(r => setTimeout(r, 1500));
			const { tasks } = await api.sync.tasks();
			const t = tasks.find(x => x.id === id);
			if (!t || t.status === 'completed' || t.status === 'failed') {
				onDone(t ? t.message : 'Done');
				break;
			}
			if (t.progress?.total) {
				autoCleanProgress = `Cleaning… ${t.progress.done ?? 0}/${t.progress.total}`;
			}
		}
	}

	async function openAutoCleanPreview() {
		autoCleanLoading = true;
		try {
			autoCleanPlan = await api.dedup.autoCleanPreview();
			autoCleanPanelOpen = true;
		} catch (e: any) {
			alert(`Failed to compute the auto-clean plan: ${e}`);
		} finally {
			autoCleanLoading = false;
		}
	}

	async function confirmAutoClean() {
		if (autoCleanApplying || !autoCleanPlan) return;
		autoCleanApplying = true;
		autoCleanProgress = 'Starting…';
		try {
			const r = await api.dedup.autoCleanApply();
			await pollBgTask(r.task_id, (msg) => {
				autoCleanApplying = false;
				autoCleanProgress = null;
				autoCleanPanelOpen = false;
				autoCleanPlan = null;
				alert(msg);
				loadStats();
				loadGroups();
			});
		} catch (e: any) {
			alert(`Auto-clean failed: ${e}`);
			autoCleanApplying = false;
			autoCleanProgress = null;
		}
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
			{#if stats && stats.duplicate_groups > 0}
				<button onclick={openAutoCleanPreview} disabled={autoCleanLoading}
					class="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-50 transition-colors">
					<Sparkles size={12} class={autoCleanLoading ? 'animate-pulse' : ''} />
					{autoCleanLoading ? 'Scanning…' : 'Auto-Clean'}
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
			<span><span class="text-zinc-200 font-medium">{formatBytes(stats.wasted_bytes)}</span> <span class="text-zinc-500">potentially reclaimable</span></span>
			<span class="text-zinc-600">Double-click any thumbnail to view it full-screen before deleting</span>
		</div>
	{/if}

	<!-- Groups list -->
	<div class="flex-1 overflow-y-auto p-5" onscroll={(e) => onGroupsScroll(e.currentTarget)}>
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
								{@const tier = TIER_INFO[photo.tier] ?? TIER_INFO[2]}
								<div class="flex items-center gap-3 px-4 py-2.5 hover:bg-zinc-800/30 transition-colors group">
									<button
										class="shrink-0 relative"
										ondblclick={() => lightboxPhotoId = photo.id}
										title="Double-click to view full-screen"
									>
										<img src="/media/thumbnail/{photo.id}?size=sm" alt={photo.filename}
											class="w-10 h-10 object-cover rounded bg-zinc-800" loading="lazy" />
										{#if photo.media_type === 'video'}
											<Video size={11} class="absolute bottom-0.5 right-0.5 text-white drop-shadow" />
										{/if}
									</button>
									<div class="flex-1 min-w-0">
										<p class="text-xs text-zinc-200 truncate font-medium">{photo.filename}</p>
										<div class="flex items-center gap-1.5 mt-0.5">
											<span class="text-[9px] px-1 py-0.5 rounded leading-none shrink-0 {tier.cls}">{tier.label}</span>
											<p class="text-[11px] text-zinc-500 truncate">{photo.album_path}</p>
										</div>
									</div>
									<div class="shrink-0 text-right">
										<p class="text-[11px] text-zinc-500">{photo.taken_at ? new Date(photo.taken_at).toLocaleDateString() : '—'}</p>
										<p class="text-[11px] text-zinc-600">{formatBytes(photo.file_size)}</p>
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

<!-- Auto-Clean review panel -->
{#if autoCleanPanelOpen && autoCleanPlan}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-6">
		<button class="absolute inset-0 bg-black/70" aria-label="Close" onclick={() => { if (!autoCleanApplying) autoCleanPanelOpen = false; }}></button>
		<div class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col" role="dialog" aria-modal="true">
			<div class="p-4 border-b border-zinc-800 flex items-center gap-2 shrink-0">
				<Sparkles size={16} class="text-violet-400" />
				<h2 class="text-sm font-semibold text-zinc-100">Auto-Clean review</h2>
				<button onclick={() => autoCleanPanelOpen = false} disabled={autoCleanApplying}
					class="ml-auto p-1 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors disabled:opacity-40">
					<X size={16} />
				</button>
			</div>

			<div class="flex-1 overflow-y-auto p-4 space-y-4">
				{#if autoCleanPlan.groups.length === 0}
					<div class="flex flex-col items-center justify-center h-40 gap-2 text-zinc-500">
						<Sparkles size={32} class="opacity-30" />
						<p class="text-sm">Nothing matches the folder-priority rule right now.</p>
						<p class="text-xs text-zinc-600">Duplicates only auto-clean when copies span different folder tiers (Staging → Archive → Special).</p>
					</div>
				{:else}
					<div class="text-xs text-zinc-400 bg-zinc-800/50 rounded-lg p-3">
						Will trash <span class="text-zinc-100 font-medium">{autoCleanPlan.total_delete_count}</span> file(s),
						freeing <span class="text-zinc-100 font-medium">{formatBytes(autoCleanPlan.total_reclaim_bytes)}</span>.
						Files go to the Recycle Bin (recoverable), and any rating/color-label/tag on a deleted copy is merged onto the copy you're keeping first.
						Double-click a thumbnail to verify it full-screen before confirming.
					</div>

					{#each autoCleanPlan.groups as group (group.sha256)}
						<div class="bg-zinc-800/30 border border-zinc-800 rounded-lg overflow-hidden">
							<div class="divide-y divide-zinc-800/60">
								{#each group.keep as photo (photo.id)}
									{@const tier = TIER_INFO[photo.tier] ?? TIER_INFO[2]}
									<div class="flex items-center gap-3 px-3 py-2 bg-emerald-500/5">
										<button class="shrink-0 relative" ondblclick={() => lightboxPhotoId = photo.id} title="Double-click to view full-screen">
											<img src="/media/thumbnail/{photo.id}?size=sm" alt={photo.filename} class="w-9 h-9 object-cover rounded bg-zinc-800" loading="lazy" />
											{#if photo.media_type === 'video'}<Video size={10} class="absolute bottom-0.5 right-0.5 text-white drop-shadow" />{/if}
										</button>
										<div class="flex-1 min-w-0">
											<p class="text-xs text-zinc-200 truncate">{photo.filename}</p>
											<div class="flex items-center gap-1.5 mt-0.5">
												<span class="text-[9px] px-1 py-0.5 rounded leading-none shrink-0 {tier.cls}">{tier.label}</span>
												<p class="text-[11px] text-zinc-500 truncate">{photo.album_path}</p>
											</div>
										</div>
										<span class="text-[10px] text-emerald-400 shrink-0">keep</span>
									</div>
								{/each}
								{#each group.delete as photo (photo.id)}
									{@const tier = TIER_INFO[photo.tier] ?? TIER_INFO[2]}
									<div class="flex items-center gap-3 px-3 py-2 bg-red-500/5">
										<button class="shrink-0 relative" ondblclick={() => lightboxPhotoId = photo.id} title="Double-click to view full-screen">
											<img src="/media/thumbnail/{photo.id}?size=sm" alt={photo.filename} class="w-9 h-9 object-cover rounded bg-zinc-800" loading="lazy" />
											{#if photo.media_type === 'video'}<Video size={10} class="absolute bottom-0.5 right-0.5 text-white drop-shadow" />{/if}
										</button>
										<div class="flex-1 min-w-0">
											<p class="text-xs text-zinc-200 truncate">{photo.filename}</p>
											<div class="flex items-center gap-1.5 mt-0.5">
												<span class="text-[9px] px-1 py-0.5 rounded leading-none shrink-0 {tier.cls}">{tier.label}</span>
												<p class="text-[11px] text-zinc-500 truncate">{photo.album_path}</p>
											</div>
										</div>
										<span class="text-[10px] text-red-400 shrink-0 flex items-center gap-1"><Trash2 size={11} /> delete</span>
									</div>
								{/each}
							</div>
						</div>
					{/each}
				{/if}

				{#if autoCleanPlan.skipped.length > 0}
					<div class="border border-amber-800/40 bg-amber-500/5 rounded-lg p-3">
						<div class="flex items-center gap-2 text-xs text-amber-400 font-medium mb-2">
							<AlertTriangle size={13} /> {autoCleanPlan.skipped.length} skipped — needs manual review
						</div>
						<div class="space-y-1.5">
							{#each autoCleanPlan.skipped as s (s.id)}
								<button onclick={() => lightboxPhotoId = s.id}
									class="w-full flex items-center gap-2 text-[11px] text-left hover:bg-zinc-800/40 rounded px-1.5 py-1 transition-colors">
									<span class="text-zinc-300 truncate">{s.filename}</span>
									<span class="text-zinc-600 truncate flex-1">{s.album_path}</span>
									<span class="text-amber-500 shrink-0">{s.reason}</span>
								</button>
							{/each}
						</div>
					</div>
				{/if}
			</div>

			<div class="p-4 border-t border-zinc-800 flex items-center gap-3 shrink-0">
				{#if autoCleanProgress}
					<span class="text-xs text-zinc-400">{autoCleanProgress}</span>
				{/if}
				<div class="ml-auto flex items-center gap-2">
					<button onclick={() => autoCleanPanelOpen = false} disabled={autoCleanApplying}
						class="px-3 py-1.5 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors disabled:opacity-40">
						Cancel
					</button>
					<button onclick={confirmAutoClean} disabled={autoCleanApplying || autoCleanPlan.groups.length === 0}
						class="flex items-center gap-1.5 px-4 py-1.5 text-xs rounded bg-violet-600 hover:bg-violet-500 text-white transition-colors disabled:opacity-40">
						<Trash2 size={12} />
						{autoCleanApplying ? 'Cleaning…' : `Confirm & Delete ${autoCleanPlan.total_delete_count}`}
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}

{#if lightboxPhotoId !== null}
	<PhotoLightbox photoId={lightboxPhotoId} onClose={() => lightboxPhotoId = null} />
{/if}

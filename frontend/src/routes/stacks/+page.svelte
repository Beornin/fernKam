<script lang="ts">
	import { onMount } from 'svelte';
	import { Layers, RefreshCw, ChevronRight, Star, Image, Tag, X, Loader, ScanSearch, CheckCircle } from '@lucide/svelte';
	import { api } from '$lib/api';
	import type { AlbumNode, StackSummary, StackDetail } from '$lib/api';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';

	const THUMB = (id: number | null) =>
		id ? `http://localhost:8000/media/thumbnail/${id}?size=sm` : null;

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------
	let tree = $state<AlbumNode[]>([]);
	let albumFilter = $state<string | null>(null);

	let stacks = $state<StackSummary[]>([]);
	let total = $state(0);
	let page = $state(0);
	const PAGE_SIZE = 60;

	let loading = $state(false);
	let detecting = $state(false);
	let detectResult = $state<{ created: number; updated: number; removed: number } | null>(null);

	let selected = $state<StackDetail | null>(null);
	let syncingId = $state<number | null>(null);
	let syncResult = $state<string | null>(null);
	let lightboxPhotoId = $state<number | null>(null);

	// ---------------------------------------------------------------------------
	// Load
	// ---------------------------------------------------------------------------
	async function loadTree() {
		try { tree = await api.stacks.tree(); } catch { tree = []; }
	}

	async function loadStacks() {
		loading = true;
		try {
			const res = await api.stacks.list({
				album_path: albumFilter ?? undefined,
				limit: PAGE_SIZE,
				offset: page * PAGE_SIZE,
			});
			stacks = res.items;
			total = res.total;
		} finally {
			loading = false;
		}
	}

	async function selectStack(id: number) {
		if (selected?.id === id) { selected = null; return; }
		try { selected = await api.stacks.get(id); } catch { selected = null; }
	}

	async function detect() {
		detecting = true;
		detectResult = null;
		try {
			detectResult = await api.stacks.detect(albumFilter ?? undefined);
			await loadTree();
			await loadStacks();
		} finally {
			detecting = false;
		}
	}

	async function syncTags(stackId: number) {
		syncingId = stackId;
		syncResult = null;
		try {
			const r = await api.stacks.syncTags(stackId);
			syncResult = `Synced ${r.synced} / ${r.members} files`;
			if (selected?.id === stackId) selected = await api.stacks.get(stackId);
		} catch (e) {
			syncResult = `Error: ${e}`;
		} finally {
			syncingId = null;
		}
	}

	function selectAlbum(path: string | null) {
		albumFilter = path;
		page = 0;
		selected = null;
		loadStacks();
	}

	onMount(() => {
		loadTree();
		loadStacks();
	});

	// ---------------------------------------------------------------------------
	// Tree helpers
	// ---------------------------------------------------------------------------
	let expanded = $state<Set<string>>(new Set());
	function toggle(path: string) {
		expanded = expanded.has(path)
			? new Set([...expanded].filter(p => p !== path))
			: new Set([...expanded, path]);
	}
</script>

<div class="flex h-screen overflow-hidden bg-zinc-950 text-zinc-100">

	<!-- Left panel: album tree -->
	<aside class="w-56 shrink-0 border-r border-zinc-800 flex flex-col overflow-hidden">
		<div class="px-4 pt-5 pb-3 border-b border-zinc-800">
			<h2 class="text-xs font-semibold text-zinc-400 uppercase tracking-wide">Albums</h2>
		</div>
		<nav class="flex-1 overflow-y-auto py-2 text-sm">
			<button
				onclick={() => selectAlbum(null)}
				class="w-full text-left px-4 py-1.5 rounded-md transition-colors {albumFilter === null ? 'bg-amber-500/15 text-amber-300' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'}"
			>All albums</button>

			{#snippet treeNode(node: AlbumNode, depth: number)}
				{@const isOpen = expanded.has(node.path)}
				{@const active = albumFilter === node.path}
				<div>
					<button
						onclick={() => { selectAlbum(node.path); if (node.children.length) toggle(node.path); }}
						class="w-full text-left flex items-center gap-1 py-1.5 rounded-md transition-colors {active ? 'bg-amber-500/15 text-amber-300' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'}"
						style="padding-left: {0.75 + depth * 0.75}rem; padding-right: 0.75rem"
					>
						{#if node.children.length}
							<ChevronRight size={12} class="shrink-0 transition-transform {isOpen ? 'rotate-90' : ''}" />
						{:else}
							<span class="w-3"></span>
						{/if}
						<span class="truncate flex-1">{node.name}</span>
						<span class="text-xs text-zinc-500">{node.photo_count}</span>
					</button>
					{#if isOpen}
						{#each node.children as child}
							{@render treeNode(child, depth + 1)}
						{/each}
					{/if}
				</div>
			{/snippet}

			{#each tree as node}
				{@render treeNode(node, 0)}
			{/each}
		</nav>
	</aside>

	<!-- Main content -->
	<div class="flex-1 flex flex-col overflow-hidden">

		<!-- Toolbar -->
		<header class="px-6 py-4 border-b border-zinc-800 flex items-center gap-4 shrink-0">
			<Layers size={22} class="text-amber-400" />
			<div>
				<h1 class="text-lg font-semibold leading-tight">RAW / JPG Stacks</h1>
				<p class="text-xs text-zinc-500">{total} stack{total !== 1 ? 's' : ''}{albumFilter ? ` in ${albumFilter}` : ''}</p>
			</div>

			<div class="ml-auto flex items-center gap-3">
				{#if detectResult}
					<span class="text-xs text-emerald-400 flex items-center gap-1">
						<CheckCircle size={12} /> +{detectResult.created} created, {detectResult.updated} updated, {detectResult.removed} removed
					</span>
				{/if}
				{#if syncResult}
					<span class="text-xs text-sky-400">{syncResult}</span>
				{/if}
				<button
					onclick={detect}
					disabled={detecting}
					class="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-sm font-medium text-white transition-colors"
				>
					{#if detecting}
						<Loader size={14} class="animate-spin" /> Detecting…
					{:else}
						<ScanSearch size={14} /> Detect Stacks
					{/if}
				</button>
			</div>
		</header>

		<!-- Body: grid + optional detail panel -->
		<div class="flex flex-1 overflow-hidden">

			<!-- Stack grid -->
			<div class="flex-1 overflow-y-auto p-6">
				{#if loading}
					<div class="flex items-center justify-center h-48 text-zinc-500">
						<Loader size={28} class="animate-spin" />
					</div>
				{:else if stacks.length === 0}
					<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-500">
						<Layers size={48} class="opacity-30" />
						<p class="text-sm">No stacks found. Run <strong class="text-amber-400">Detect Stacks</strong> to build the catalog.</p>
					</div>
				{:else}
					<div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(160px, 1fr))">
						{#each stacks as s}
							{@const active = selected?.id === s.id}
							<button
								onclick={() => selectStack(s.id)}
								class="group relative rounded-xl overflow-hidden border transition-all {active ? 'border-amber-400 ring-2 ring-amber-400/40' : 'border-zinc-800 hover:border-zinc-600'}"
							>
								<!-- Thumbnail -->
								<div class="aspect-square bg-zinc-900 overflow-hidden">
									{#if THUMB(s.cover_photo_id)}
										<img src={THUMB(s.cover_photo_id)} alt={s.stem_key} class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" loading="lazy" />
									{:else}
										<div class="w-full h-full flex items-center justify-center text-zinc-700">
											<Image size={36} />
										</div>
									{/if}
								</div>
								<!-- Footer -->
								<div class="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-2 pb-2 pt-6">
									<p class="text-xs font-medium truncate leading-tight">{s.stem_key}</p>
									<div class="flex items-center gap-1 mt-1">
										<span class="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800/80 text-zinc-300">{s.member_count} files</span>
										{#if s.has_raw}
											<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-700/80 text-amber-200">RAW</span>
										{/if}
									</div>
								</div>
							</button>
						{/each}
					</div>

					<!-- Pagination -->
					{#if total > PAGE_SIZE}
						<div class="flex items-center justify-center gap-4 mt-8">
							<button
								onclick={() => { page = Math.max(0, page - 1); loadStacks(); }}
								disabled={page === 0}
								class="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-sm"
							>Previous</button>
							<span class="text-sm text-zinc-400">{page + 1} / {Math.ceil(total / PAGE_SIZE)}</span>
							<button
								onclick={() => { page += 1; loadStacks(); }}
								disabled={(page + 1) * PAGE_SIZE >= total}
								class="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-sm"
							>Next</button>
						</div>
					{/if}
				{/if}
			</div>

			<!-- Detail panel -->
			{#if selected}
				<aside class="w-80 shrink-0 border-l border-zinc-800 flex flex-col overflow-hidden">
					<div class="px-4 py-3 border-b border-zinc-800 flex items-center justify-between">
						<h3 class="font-medium text-sm truncate flex-1">{selected.stem_key}</h3>
						<div class="flex items-center gap-1">
							<button
								onclick={() => syncTags(selected!.id)}
								disabled={syncingId === selected.id}
								class="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-sky-400 transition-colors"
								title="Sync tags across stack members"
							>
								{#if syncingId === selected.id}
									<Loader size={14} class="animate-spin" />
								{:else}
									<Tag size={14} />
								{/if}
							</button>
							<button onclick={() => selected = null} class="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-zinc-100 transition-colors">
								<X size={14} />
							</button>
						</div>
					</div>

					<div class="flex-1 overflow-y-auto">
						<p class="px-4 py-2 text-xs text-zinc-500">{selected.album_path}</p>

						<div class="px-4 space-y-2 pb-4">
							{#each selected.members as m}
								{@const isRaw = m.stack_role === 'raw'}
								<div class="flex items-center gap-3 rounded-lg p-2 bg-zinc-900 border border-zinc-800 hover:border-zinc-600 cursor-pointer transition-colors" ondblclick={() => lightboxPhotoId = m.id} role="button" tabindex="0" onkeydown={(e) => e.key === 'Enter' && (lightboxPhotoId = m.id)}>
									<div class="w-14 h-14 shrink-0 rounded overflow-hidden bg-zinc-800">
										{#if THUMB(m.id)}
											<img src={THUMB(m.id)} alt={m.filename} class="w-full h-full object-cover" loading="lazy" />
										{:else}
											<div class="w-full h-full flex items-center justify-center text-zinc-600">
												<Image size={20} />
											</div>
										{/if}
									</div>
									<div class="min-w-0 flex-1">
										<p class="text-xs font-medium truncate">{m.filename}</p>
										<div class="flex items-center gap-1 mt-1 flex-wrap">
											{#if m.is_cover}
												<span class="text-[10px] px-1 py-0.5 rounded bg-amber-600/30 text-amber-300">cover</span>
											{/if}
											{#if isRaw}
												<span class="text-[10px] px-1 py-0.5 rounded bg-amber-800/50 text-amber-400">RAW</span>
											{:else}
												<span class="text-[10px] px-1 py-0.5 rounded bg-zinc-700 text-zinc-400">{m.stack_role}</span>
											{/if}
											{#if m.rating > 0}
												<span class="text-[10px] flex items-center gap-0.5 text-yellow-400">
													<Star size={10} />{m.rating}
												</span>
											{/if}
										</div>
									</div>
								</div>
							{/each}
						</div>
					</div>
				</aside>
			{/if}
		</div>
	</div>

{#if lightboxPhotoId !== null}
	{@const allIds = (selected?.members ?? []).map(m => m.id)}
	{@const idx = allIds.indexOf(lightboxPhotoId)}
	<PhotoLightbox
		photoId={lightboxPhotoId}
		onClose={() => lightboxPhotoId = null}
		onPrev={idx > 0 ? () => lightboxPhotoId = allIds[idx - 1] : undefined}
		onNext={idx < allIds.length - 1 ? () => lightboxPhotoId = allIds[idx + 1] : undefined}
	/>
{/if}
</div>

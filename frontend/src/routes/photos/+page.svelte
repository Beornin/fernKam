<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api, type PhotoSummary, type AlbumNode } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { ChevronLeft, ChevronRight, SlidersHorizontal, ChevronDown, ChevronRight as ChevronR, FolderOpen, Folder, PanelLeftClose, PanelLeftOpen, Images, PanelRightOpen, PanelRightClose } from '@lucide/svelte';
	import RightPanel from '$lib/components/RightPanel.svelte';
	import { onMount } from 'svelte';
	import { statusCountStore } from '$lib/stores';

	// Query params
	let albumPath = $derived($page.url.searchParams.get('album_path') ?? '');
	let tagId = $derived(Number($page.url.searchParams.get('tag_id')) || undefined);
	let photoIdParam = $derived(Number($page.url.searchParams.get('photo_id')) || null);
	let referrer = $derived($page.url.searchParams.get('referrer') ?? null);
	let backUrl = $derived($page.url.searchParams.get('back') ?? null);
	let sort = $derived($page.url.searchParams.get('sort') ?? 'taken_at_desc');
	let currentPage = $derived(Number($page.url.searchParams.get('page') ?? 1));
	const PAGE_SIZE = 500;

	// Album tree state
	let albums = $state<AlbumNode[]>([]);
	let treeExpanded = $state(new Set<string>());
	let treeOpen = $state(true);

	// Photos state
	let photos = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let selectedId = $state<number | null>(null);
	let selectedIdx = $state<number>(-1);
	let selectedIds = $state<Set<number>>(new Set());
	let batchDetecting = $state(false);
	let batchResult = $state<string | null>(null);
	let standalonePhoto = $state<any>(null);
	let rightPanelOpen = $state(false);
	let rightPanelPhotoId = $state<number | null>(null);

	onMount(async () => {
		albums = await api.albums.list();
		// Auto-expand path to selected album
		if (albumPath) {
			const parts = albumPath.split('/').filter(Boolean);
			let cur = '';
			for (const part of parts) {
				cur += '/' + part;
				treeExpanded.add(cur);
			}
			treeExpanded = new Set(treeExpanded);
		}
	});

	$effect(() => {
		loading = true;
		standalonePhoto = null;
		api.photos.list({
			album_path: albumPath || undefined,
			tag_id: tagId,
			sort,
			page: currentPage,
			page_size: PAGE_SIZE,
		}).then(data => {
			photos = data.items;
			total = data.total;
			loading = false;
			statusCountStore.set(`${total.toLocaleString()} items`);

			if (photoIdParam) {
				const idx = photos.findIndex(p => p.id === photoIdParam);
				if (idx >= 0) {
					selectedId = photoIdParam;
					selectedIdx = idx;
					const u = new URL($page.url);
					u.searchParams.delete('photo_id');
					goto(u.toString(), { replaceState: true });
				} else {
					api.photos.get(photoIdParam).then(photo => {
						standalonePhoto = photo;
						selectedId = photoIdParam;
						selectedIdx = -1;
						const u = new URL($page.url);
						u.searchParams.delete('photo_id');
						goto(u.toString(), { replaceState: true });
					});
				}
			}
		});
	});

	function setParam(key: string, value: string | null) {
		const u = new URL($page.url);
		if (value) u.searchParams.set(key, value);
		else u.searchParams.delete(key);
		u.searchParams.delete('page');
		goto(u.toString());
	}

	function selectAlbum(path: string) {
		const u = new URL($page.url);
		if (path) u.searchParams.set('album_path', path);
		else u.searchParams.delete('album_path');
		u.searchParams.delete('page');
		goto(u.toString());
	}

	function toggleFolder(path: string) {
		if (treeExpanded.has(path)) treeExpanded.delete(path);
		else treeExpanded.add(path);
		treeExpanded = new Set(treeExpanded);
	}

	function openPhoto(p: PhotoSummary) {
		selectedId = p.id;
		selectedIdx = photos.findIndex(ph => ph.id === p.id);
		rightPanelPhotoId = p.id;
		rightPanelOpen = true;
	}

	function closeLightbox() {
		selectedId = null;
		selectedIdx = -1;
		if (backUrl) goto(decodeURIComponent(backUrl));
		else if (referrer === 'review') goto('/review');
	}

	function prevPhoto() {
		if (selectedIdx > 0) { selectedIdx--; selectedId = photos[selectedIdx].id; }
	}

	function nextPhoto() {
		if (selectedIdx < photos.length - 1) { selectedIdx++; selectedId = photos[selectedIdx].id; }
	}

	const totalPages = $derived(Math.ceil(total / PAGE_SIZE));

	function setPage(p: number) {
		const u = new URL($page.url);
		u.searchParams.set('page', String(p));
		goto(u.toString());
	}

	async function runBatchDetect() {
		if (selectedIds.size === 0) return;
		batchDetecting = true;
		batchResult = null;
		try {
			const result = await api.photos.batchDetect(Array.from(selectedIds));
			batchResult = `Processed ${result.processed}, found ${result.faces_found} faces, ${result.suggested} identified${result.errors > 0 ? `, ${result.errors} errors` : ''}`;
			selectedIds = new Set();
		} catch {
			batchResult = 'Batch detect failed';
		} finally {
			batchDetecting = false;
		}
	}

	// Breadcrumb from albumPath
	function breadcrumbParts(path: string): Array<{label: string, path: string}> {
		if (!path) return [];
		const parts = path.split('/').filter(Boolean);
		return parts.map((p, i) => ({
			label: p,
			path: '/' + parts.slice(0, i + 1).join('/'),
		}));
	}
</script>

<div class="flex h-full overflow-hidden">
	<!-- Album tree left panel -->
	{#if treeOpen}
		<aside class="w-[220px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
			<div class="flex items-center justify-between px-3 py-2 border-b border-zinc-800 shrink-0">
				<span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Albums</span>
				<button onclick={() => treeOpen = false} class="text-zinc-600 hover:text-zinc-300 transition-colors" title="Hide panel">
					<PanelLeftClose size={14} />
				</button>
			</div>
			<div class="flex-1 overflow-y-auto py-1">
				<!-- All Photos entry -->
				<button
					class="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs transition-colors text-left
						{!albumPath ? 'bg-amber-500/20 text-amber-400' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'}"
					onclick={() => selectAlbum('')}
				>
					<Images size={13} class="shrink-0" />
					<span class="truncate">All Photos</span>
				</button>
				{#snippet albumRow(node: AlbumNode, depth: number)}
					{@const expanded = treeExpanded.has(node.path)}
					{@const hasChildren = node.children.length > 0}
					{@const selected = albumPath === node.path}
					<div>
						<div
							class="flex items-center gap-0.5 hover:bg-zinc-800 transition-colors"
							style="padding-left:{depth * 12 + 8}px"
						>
							<!-- Expand toggle -->
							<button
								class="w-4 h-4 flex items-center justify-center text-zinc-600 hover:text-zinc-300 shrink-0"
								onclick={() => hasChildren && toggleFolder(node.path)}
							>
								{#if hasChildren}
									{#if expanded}<ChevronDown size={11} />{:else}<ChevronR size={11} />{/if}
								{/if}
							</button>
							<!-- Folder row -->
							<button
								class="flex-1 flex items-center gap-1.5 py-1 text-xs text-left min-w-0 pr-2
									{selected ? 'text-amber-400' : 'text-zinc-400 hover:text-zinc-200'}"
								onclick={() => { selectAlbum(node.path); if (hasChildren && !expanded) toggleFolder(node.path); }}
							>
								{#if expanded || selected}
									<FolderOpen size={12} class="shrink-0 {selected ? 'text-amber-400' : 'text-zinc-500'}" />
								{:else}
									<Folder size={12} class="shrink-0 text-zinc-600" />
								{/if}
								<span class="truncate">{node.name}</span>
								<span class="ml-auto text-zinc-700 shrink-0 text-[10px]">{node.photo_count}</span>
							</button>
						</div>
						{#if expanded && hasChildren}
							{#each node.children as child}
								{@render albumRow(child, depth + 1)}
							{/each}
						{/if}
					</div>
				{/snippet}
				{#each albums as album}
					{@render albumRow(album, 0)}
				{/each}
			</div>
		</aside>
	{/if}

	<!-- Main area + right panel -->
	<div class="flex flex-1 overflow-hidden">
	<!-- Center column -->
	<div class="flex flex-col flex-1 overflow-hidden">
		<!-- Header / breadcrumb -->
		<div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 shrink-0">
			{#if !treeOpen}
				<button onclick={() => treeOpen = true} class="text-zinc-600 hover:text-zinc-300 transition-colors mr-1" title="Show albums">
					<PanelLeftOpen size={14} />
				</button>
			{/if}
			<!-- Breadcrumb -->
			<div class="flex items-center gap-1 text-xs text-zinc-400 flex-1 min-w-0">
				<button class="hover:text-zinc-200 transition-colors shrink-0" onclick={() => selectAlbum('')}>Albums</button>
				{#each breadcrumbParts(albumPath) as crumb, i}
					<span class="text-zinc-700 shrink-0">/</span>
					<button
						class="hover:text-zinc-200 transition-colors truncate {i === breadcrumbParts(albumPath).length - 1 ? 'text-amber-400' : ''}"
						onclick={() => selectAlbum(crumb.path)}
					>{crumb.label}</button>
				{/each}
			</div>
			<span class="text-xs text-zinc-600 shrink-0">{total.toLocaleString()} items</span>

			<!-- Right panel toggle -->
			<button
				onclick={() => rightPanelOpen = !rightPanelOpen}
				class="p-1 rounded text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 transition-colors shrink-0"
				title="{rightPanelOpen ? 'Hide' : 'Show'} properties"
			>
				{#if rightPanelOpen}<PanelRightClose size={14} />{:else}<PanelRightOpen size={14} />{/if}
			</button>

			<!-- Toolbar right -->
			<div class="flex items-center gap-2 shrink-0">
				{#if selectedIds.size > 0}
					<button
						onclick={runBatchDetect}
						disabled={batchDetecting}
						class="text-xs px-2 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50 transition-colors"
					>
						{batchDetecting ? 'Detecting...' : `Detect (${selectedIds.size})`}
					</button>
					<button
						onclick={() => selectedIds = new Set()}
						class="text-xs px-2 py-1 rounded bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors"
					>Clear</button>
				{/if}
				{#if batchResult}
					<span class="text-xs text-zinc-400 max-w-[200px] truncate">{batchResult}</span>
				{/if}
				<SlidersHorizontal size={13} class="text-zinc-500" />
				<select
					class="text-xs bg-zinc-800 text-zinc-300 border border-zinc-700 rounded px-2 py-1"
					value={sort}
					onchange={(e) => setParam('sort', e.currentTarget.value)}
				>
					<option value="taken_at_desc">Newest first</option>
					<option value="taken_at_asc">Oldest first</option>
					<option value="rating_desc">Highest rated</option>
					<option value="filename_asc">Filename A–Z</option>
					<option value="imported_at_desc">Recently imported</option>
				</select>
			</div>
		</div>

		<!-- Grid -->
		<div class="flex-1 overflow-y-auto">
			{#if loading}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm gap-2">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					Loading…
				</div>
			{:else if photos.length === 0}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm">No photos found</div>
			{:else}
				<PhotoGrid {photos} onSelect={openPhoto} bind:selectedIds={selectedIds} />
			{/if}
		</div>

		<!-- Pagination -->
		{#if totalPages > 1}
			<div class="shrink-0 flex items-center justify-center gap-3 px-4 py-2 border-t border-zinc-800 text-sm">
				<button
					class="p-1 rounded hover:bg-zinc-800 text-zinc-400 disabled:opacity-30"
					disabled={currentPage <= 1}
					onclick={() => setPage(currentPage - 1)}
				><ChevronLeft size={16} /></button>
				<span class="text-zinc-400 text-xs">Page {currentPage} / {totalPages}</span>
				<button
					class="p-1 rounded hover:bg-zinc-800 text-zinc-400 disabled:opacity-30"
					disabled={currentPage >= totalPages}
					onclick={() => setPage(currentPage + 1)}
				><ChevronRight size={16} /></button>
			</div>
		{/if}
	</div><!-- end center column -->

	<!-- Right properties panel -->
	{#if rightPanelOpen}
		<RightPanel photoId={rightPanelPhotoId} onClose={() => rightPanelOpen = false} />
	{/if}
	</div><!-- end main area + right panel -->
</div>

{#if selectedId !== null}
	<PhotoLightbox
		photoId={selectedId}
		photo={standalonePhoto}
		onClose={closeLightbox}
		onPrev={selectedIdx > 0 ? prevPhoto : undefined}
		onNext={selectedIdx < photos.length - 1 ? nextPhoto : undefined}
	/>
{/if}

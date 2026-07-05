<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api, type PhotoSummary, type AlbumNode } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { ChevronLeft, ChevronRight, SlidersHorizontal, ChevronDown, ChevronRight as ChevronR, FolderOpen, Folder, PanelLeftClose, PanelLeftOpen, Images, PanelRightOpen, PanelRightClose, Clapperboard, Trash2, X, ZoomIn, ZoomOut, Maximize2 } from '@lucide/svelte';
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

	// ── Review Mode ──
	let reviewMode = $state(false);
	let reviewIdx = $state(0);
	let reviewPhotos = $state<PhotoSummary[]>([]);
	let reviewTrashedCount = $state(0);
	let reviewTrashing = $state(false);
	let reviewFit = $state(false); // false = 1:1, true = fit-to-screen

	// pan state
	let reviewScroll = $state<HTMLDivElement | undefined>(undefined);
	let panActive = $state(false);
	let panStartX = 0, panStartY = 0, panScrollX = 0, panScrollY = 0;

	// scroll preservation across navigation
	let savedScrollX = 0, savedScrollY = 0, restoreScroll = false;

	// filmstrip
	let filmstripEl = $state<HTMLDivElement | undefined>(undefined);

	$effect(() => {
		// auto-scroll filmstrip to keep active thumb centred
		const idx = reviewIdx;
		if (filmstripEl) {
			const thumb = filmstripEl.querySelector(`[data-fidx="${idx}"]`) as HTMLElement | null;
			if (thumb) thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
		}
	});

	function enterReview() {
		if (photos.length === 0) return;
		reviewPhotos = [...photos];
		reviewIdx = 0;
		reviewTrashedCount = 0;
		reviewFit = false;
		reviewMode = true;
	}

	function exitReview() {
		reviewMode = false;
		if (reviewTrashedCount > 0) {
			// Reload grid to reflect trashed photos
			photos = reviewPhotos;
		}
	}

	function reviewPrev() {
		if (reviewIdx > 0) { saveScroll(); reviewIdx--; }
	}

	function reviewNext() {
		if (reviewIdx < reviewPhotos.length - 1) { saveScroll(); reviewIdx++; }
	}

	function saveScroll() {
		if (reviewScroll) {
			savedScrollX = reviewScroll.scrollLeft;
			savedScrollY = reviewScroll.scrollTop;
			restoreScroll = true;
		}
	}

	function onImgLoad() {
		if (restoreScroll && reviewScroll) {
			reviewScroll.scrollLeft = savedScrollX;
			reviewScroll.scrollTop = savedScrollY;
			restoreScroll = false;
		}
	}

	function centerReview() {
		// Centre view (used only on first entry)
		restoreScroll = false;
		setTimeout(() => {
			if (reviewScroll) {
				reviewScroll.scrollLeft = (reviewScroll.scrollWidth - reviewScroll.clientWidth) / 2;
				reviewScroll.scrollTop = (reviewScroll.scrollHeight - reviewScroll.clientHeight) / 2;
			}
		}, 80);
	}

	async function reviewTrash() {
		if (reviewTrashing || reviewPhotos.length === 0) return;
		reviewTrashing = true;
		const photo = reviewPhotos[reviewIdx];
		try {
			await api.photos.trash(photo.id);
			reviewPhotos = reviewPhotos.filter((_, i) => i !== reviewIdx);
			reviewTrashedCount++;
			if (reviewIdx >= reviewPhotos.length && reviewIdx > 0) reviewIdx--;
		} catch (e) {
			alert(`Failed to trash: ${e}`);
		} finally {
			reviewTrashing = false;
		}
	}

	function onReviewKey(e: KeyboardEvent) {
		if (!reviewMode) return;
		if (e.key === 'ArrowLeft') { e.preventDefault(); reviewPrev(); }
		else if (e.key === 'ArrowRight') { e.preventDefault(); reviewNext(); }
		else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); reviewTrash(); }
		else if (e.key === 'Escape') exitReview();
		else if (e.key === 'f' || e.key === 'F') reviewFit = !reviewFit;
	}

	function panStart(e: MouseEvent) {
		if (reviewFit) return;
		panActive = true;
		panStartX = e.clientX;
		panStartY = e.clientY;
		panScrollX = reviewScroll?.scrollLeft ?? 0;
		panScrollY = reviewScroll?.scrollTop ?? 0;
		e.preventDefault();
	}

	function panMove(e: MouseEvent) {
		if (!panActive || !reviewScroll) return;
		reviewScroll.scrollLeft = panScrollX - (e.clientX - panStartX);
		reviewScroll.scrollTop = panScrollY - (e.clientY - panStartY);
	}

	function panEnd() { panActive = false; }

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

			<!-- Review Mode button -->
			{#if albumPath && photos.length > 0}
				<button
					onclick={enterReview}
					class="text-xs px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white flex items-center gap-1 transition-colors shrink-0"
					title="Review photos at 1:1 zoom"
				>
					<Clapperboard size={12} /> Review
				</button>
			{/if}

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

<svelte:window onkeydown={onReviewKey} />

<!-- ── Review Mode Overlay ── -->
{#if reviewMode}
<div class="fixed inset-0 z-50 bg-black flex flex-col">

	<!-- Top bar -->
	<div class="shrink-0 flex items-center justify-between px-4 py-2 bg-black/80 backdrop-blur-sm z-10">
		<div class="flex items-center gap-3">
			<span class="text-white font-semibold text-sm">{reviewPhotos[reviewIdx]?.filename ?? ''}</span>
			{#if reviewTrashedCount > 0}
				<span class="text-xs px-2 py-0.5 rounded-full bg-red-500/30 text-red-300">{reviewTrashedCount} trashed</span>
			{/if}
		</div>
		<div class="flex items-center gap-2">
			<span class="text-zinc-400 text-sm">{reviewIdx + 1} / {reviewPhotos.length}</span>
			<button
				onclick={() => reviewFit = !reviewFit}
				class="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors"
				title={reviewFit ? '1:1 pixel zoom (F)' : 'Fit to screen (F)'}
			>
				{#if reviewFit}<ZoomIn size={16} />{:else}<Maximize2 size={16} />{/if}
			</button>
			<button
				onclick={exitReview}
				class="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors"
				title="Exit review (Esc)"
			>
				<X size={16} />
			</button>
		</div>
	</div>

	<!-- Image area -->
	<div
		bind:this={reviewScroll}
		role="application"
		aria-label="Photo review area"
		class="flex-1 overflow-auto relative {panActive ? 'cursor-grabbing' : reviewFit ? 'cursor-default' : 'cursor-grab'}"
		onmousedown={panStart}
		onmousemove={panMove}
		onmouseup={panEnd}
		onmouseleave={panEnd}
	>
		{#if reviewPhotos.length > 0}
			<img
				src="http://localhost:8000/media/original/{reviewPhotos[reviewIdx].id}"
				alt={reviewPhotos[reviewIdx].filename}
				draggable="false"
				onload={onImgLoad}
				class="block select-none {reviewFit ? 'max-w-full max-h-full w-auto h-auto m-auto' : ''}"
				style={reviewFit ? 'width:100%;height:100%;object-fit:contain;' : 'width:auto;height:auto;max-width:none;max-height:none;'}
			/>
		{:else}
			<div class="flex items-center justify-center h-full text-zinc-500">No photos left</div>
		{/if}
	</div>

	<!-- Filmstrip -->
	<div
		bind:this={filmstripEl}
		class="shrink-0 h-20 bg-zinc-950 border-t border-zinc-800 flex items-center gap-1 overflow-x-auto overflow-y-hidden px-2"
		style="scrollbar-width:thin;scrollbar-color:#3f3f46 transparent;"
	>
		{#each reviewPhotos as fp, fi (fp.id)}
			<button
				data-fidx={fi}
				onclick={() => { saveScroll(); reviewIdx = fi; }}
				class="shrink-0 w-16 h-16 rounded overflow-hidden border-2 transition-all
					{fi === reviewIdx ? 'border-violet-400 opacity-100' : 'border-transparent opacity-50 hover:opacity-80'}"
				title={fp.filename}
			>
				<img
					src="http://localhost:8000/media/thumbnail/{fp.id}?size=sm"
					alt={fp.filename}
					class="w-full h-full object-cover"
					loading="lazy"
				/>
			</button>
		{/each}
	</div>

	<!-- Bottom bar -->
	<div class="shrink-0 flex items-center justify-center gap-4 px-4 py-3 bg-black/80 backdrop-blur-sm">
		<button
			onclick={reviewPrev}
			disabled={reviewIdx === 0}
			class="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-30 transition-colors"
			title="Previous (←)"
		><ChevronLeft size={20} /></button>

		<button
			onclick={reviewTrash}
			disabled={reviewTrashing || reviewPhotos.length === 0}
			class="flex items-center gap-2 px-5 py-2 rounded-lg bg-red-600 hover:bg-red-500 text-white font-medium disabled:opacity-40 transition-colors"
			title="Trash this photo (Delete)"
		>
			<Trash2 size={16} />
			{reviewTrashing ? 'Trashing…' : 'Trash'}
		</button>

		<button
			onclick={reviewNext}
			disabled={reviewIdx >= reviewPhotos.length - 1}
			class="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-30 transition-colors"
			title="Next (→)"
		><ChevronRight size={20} /></button>
	</div>

</div>
{/if}

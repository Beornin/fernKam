<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api, type PhotoSummary } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import AlbumsTab from '$lib/components/sidebar/AlbumsTab.svelte';
	import TagsTab from '$lib/components/sidebar/TagsTab.svelte';
	import TimelineTab from '$lib/components/sidebar/TimelineTab.svelte';
	import SearchTab from '$lib/components/sidebar/SearchTab.svelte';
	import PeopleTab from '$lib/components/sidebar/PeopleTab.svelte';
	import LabelsTab from '$lib/components/sidebar/LabelsTab.svelte';
	import MapView from '$lib/components/MapView.svelte';
	import { ChevronLeft, ChevronRight, SlidersHorizontal, PanelLeftClose, PanelLeftOpen, PanelRightOpen, PanelRightClose, Clapperboard, Trash2, X, ZoomIn, ZoomOut, Maximize2, Map as MapIcon } from '@lucide/svelte';
	import RightPanel from '$lib/components/RightPanel.svelte';
	import { statusCountStore } from '$lib/stores';
	import { createLightboxNav } from '$lib/lightboxNav.svelte';
	import { inferTab, readListFilterParams, buildFilterUrl, type ShellTab } from '$lib/shellFilters';

	// Query params
	let albumPath = $derived($page.url.searchParams.get('album_path') ?? '');
	let tagIdParam = $derived(Number($page.url.searchParams.get('tag_id')) || undefined);
	let photoIdParam = $derived(Number($page.url.searchParams.get('photo_id')) || null);
	let referrer = $derived($page.url.searchParams.get('referrer') ?? null);
	let backUrl = $derived($page.url.searchParams.get('back') ?? null);
	let sort = $derived($page.url.searchParams.get('sort') ?? 'taken_at_desc');
	let currentPage = $derived(Number($page.url.searchParams.get('page') ?? 1));
	let activeTab = $derived<ShellTab>(inferTab($page.url.searchParams));
	const PAGE_SIZE = 500;

	const TABS: Array<{ key: ShellTab; label: string }> = [
		{ key: 'albums', label: 'Albums' },
		{ key: 'tags', label: 'Tags' },
		{ key: 'timeline', label: 'Timeline' },
		{ key: 'search', label: 'Search' },
		{ key: 'people', label: 'People' },
		{ key: 'labels', label: 'Labels' },
	];

	// Map replaces the center grid entirely (its own layout, no album/tag filters) —
	// a toolbar toggle rather than a sidebar tab, since it has no tree/list content.
	let mapMode = $derived($page.url.searchParams.get('view') === 'map');

	function toggleMapMode() {
		const u = new URL($page.url);
		if (mapMode) u.searchParams.delete('view');
		else u.searchParams.set('view', 'map');
		goto(u.toString());
	}

	// Photos state
	let photos = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let selectedIds = $state<Set<number>>(new Set());
	let batchDetecting = $state(false);
	let batchResult = $state<string | null>(null);
	let treeOpen = $state(true);
	let rightPanelOpen = $state(false);
	let rightPanelPhotoId = $state<number | null>(null);

	const lightbox = createLightboxNav();

	// Aborts the in-flight list request when filters/page/sort change again
	// before it resolves — without this, a slow earlier response could land
	// after a newer one and show stale photos. The .catch() also stops a
	// failed request from leaving the loading spinner stuck forever.
	let listAbort: AbortController | null = null;
	$effect(() => {
		listAbort?.abort();
		const controller = new AbortController();
		listAbort = controller;

		loading = true;
		api.photos.list({
			...readListFilterParams($page.url.searchParams),
			sort,
			page: currentPage,
			page_size: PAGE_SIZE,
		}, controller.signal).then(data => {
			if (controller.signal.aborted) return;
			photos = data.items;
			total = data.total;
			loading = false;
			statusCountStore.set(`${total.toLocaleString()} items`);

			if (photoIdParam) {
				lightbox.openById(photos, photoIdParam).then(() => {
					const u = new URL($page.url);
					u.searchParams.delete('photo_id');
					goto(u.toString(), { replaceState: true });
				});
			}
		}).catch((e: any) => {
			if (e?.name === 'AbortError' || controller.signal.aborted) return;
			loading = false;
			statusCountStore.set('Failed to load photos');
		});
	});

	function setParam(key: string, value: string | null) {
		const u = new URL($page.url);
		if (value) u.searchParams.set(key, value);
		else u.searchParams.delete(key);
		u.searchParams.delete('page');
		goto(u.toString());
	}

	function openPhoto(p: PhotoSummary) {
		lightbox.open(photos, p);
		rightPanelPhotoId = p.id;
		rightPanelOpen = true;
	}

	function closeLightbox() {
		lightbox.close();
		if (backUrl) goto(decodeURIComponent(backUrl));
		else if (referrer === 'review') goto('/review');
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

	// Breadcrumb from albumPath (Albums tab only)
	function breadcrumbParts(path: string): Array<{label: string, path: string}> {
		if (!path) return [];
		const parts = path.split('/').filter(Boolean);
		return parts.map((p, i) => ({
			label: p,
			path: '/' + parts.slice(0, i + 1).join('/'),
		}));
	}

	function selectAlbumFromBreadcrumb(path: string) {
		goto(buildFilterUrl($page.url, 'albums', { album_path: path || undefined }));
	}
</script>

<div class="flex h-full overflow-hidden">
	<!-- Tab content panel -->
	{#if treeOpen}
		<aside class="w-[220px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
			<div class="flex items-center justify-between px-3 py-2 border-b border-zinc-800 shrink-0">
				<span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider">{TABS.find(t => t.key === activeTab)?.label}</span>
				<button onclick={() => treeOpen = false} class="text-zinc-600 hover:text-zinc-300 transition-colors" title="Hide panel">
					<PanelLeftClose size={14} />
				</button>
			</div>
			{#if activeTab === 'albums'}
				<AlbumsTab />
			{:else if activeTab === 'tags'}
				<TagsTab />
			{:else if activeTab === 'timeline'}
				<TimelineTab />
			{:else if activeTab === 'search'}
				<SearchTab />
			{:else if activeTab === 'people'}
				<PeopleTab />
			{:else if activeTab === 'labels'}
				<LabelsTab />
			{:else}
				<div class="flex-1 flex items-center justify-center text-xs text-zinc-600 text-center px-4">
					{TABS.find(t => t.key === activeTab)?.label} tab coming soon
				</div>
			{/if}
		</aside>
	{/if}

	<!-- Main area + right panel -->
	<div class="flex flex-1 overflow-hidden">
	<!-- Center column -->
	<div class="flex flex-col flex-1 overflow-hidden">
		<!-- Header / breadcrumb -->
		<div class="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 shrink-0">
			{#if !treeOpen}
				<button onclick={() => treeOpen = true} class="text-zinc-600 hover:text-zinc-300 transition-colors mr-1" title="Show sidebar">
					<PanelLeftOpen size={14} />
				</button>
			{/if}
			<!-- Breadcrumb (Albums tab) -->
			{#if activeTab === 'albums'}
				<div class="flex items-center gap-1 text-xs text-zinc-400 flex-1 min-w-0">
					<button class="hover:text-zinc-200 transition-colors shrink-0" onclick={() => selectAlbumFromBreadcrumb('')}>Albums</button>
					{#each breadcrumbParts(albumPath) as crumb, i}
						<span class="text-zinc-700 shrink-0">/</span>
						<button
							class="hover:text-zinc-200 transition-colors truncate {i === breadcrumbParts(albumPath).length - 1 ? 'text-amber-400' : ''}"
							onclick={() => selectAlbumFromBreadcrumb(crumb.path)}
						>{crumb.label}</button>
					{/each}
				</div>
			{:else}
				<div class="flex-1"></div>
			{/if}
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
			{#if !mapMode && activeTab === 'albums' && albumPath && photos.length > 0}
				<button
					onclick={enterReview}
					class="text-xs px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white flex items-center gap-1 transition-colors shrink-0"
					title="Review photos at 1:1 zoom"
				>
					<Clapperboard size={12} /> Review
				</button>
			{/if}

			<!-- Map toggle -->
			<button
				onclick={toggleMapMode}
				class="text-xs px-2 py-1 rounded flex items-center gap-1 transition-colors shrink-0
					{mapMode ? 'bg-amber-600 hover:bg-amber-500 text-white' : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300'}"
				title="{mapMode ? 'Back to grid' : 'Browse by location'}"
			>
				<MapIcon size={12} /> Map
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

		{#if mapMode}
			<MapView albumPath={albumPath || undefined} tagId={tagIdParam} />
		{:else}
			<!-- Grid -->
			<div class="flex-1 overflow-hidden">
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
		{/if}
	</div><!-- end center column -->

	<!-- Right properties panel -->
	{#if !mapMode && rightPanelOpen}
		<RightPanel photoId={rightPanelPhotoId} onClose={() => rightPanelOpen = false} />
	{/if}
	</div><!-- end main area + right panel -->
</div>

{#if lightbox.selectedId !== null}
	<PhotoLightbox
		photoId={lightbox.selectedId}
		photo={lightbox.standalonePhoto}
		onClose={closeLightbox}
		onPrev={lightbox.selectedIdx > 0 ? () => lightbox.prev(photos) : undefined}
		onNext={lightbox.selectedIdx >= 0 && lightbox.selectedIdx < photos.length - 1 ? () => lightbox.next(photos) : undefined}
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
				src="/media/original/{reviewPhotos[reviewIdx].id}"
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
					src="/media/thumbnail/{fp.id}?size=sm"
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

<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api, type PhotoSummary } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { ChevronLeft, ChevronRight, SlidersHorizontal } from '@lucide/svelte';

	// Query params
	let albumPath = $derived($page.url.searchParams.get('album_path') ?? '');
	let tagId = $derived(Number($page.url.searchParams.get('tag_id')) || undefined);
	let sort = $derived($page.url.searchParams.get('sort') ?? 'taken_at_desc');
	let currentPage = $derived(Number($page.url.searchParams.get('page') ?? 1));
	const PAGE_SIZE = 100;

	let photos = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let selectedId = $state<number | null>(null);
	let selectedIdx = $state<number>(-1);

	$effect(() => {
		loading = true;
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
		selectedId = p.id;
		selectedIdx = photos.indexOf(p);
	}

	function closeLightbox() { selectedId = null; selectedIdx = -1; }

	function prevPhoto() {
		if (selectedIdx > 0) {
			selectedIdx--;
			selectedId = photos[selectedIdx].id;
		}
	}

	function nextPhoto() {
		if (selectedIdx < photos.length - 1) {
			selectedIdx++;
			selectedId = photos[selectedIdx].id;
		}
	}

	const totalPages = $derived(Math.ceil(total / PAGE_SIZE));

	function setPage(p: number) {
		const u = new URL($page.url);
		u.searchParams.set('page', String(p));
		goto(u.toString());
	}
</script>

<div class="flex flex-col h-full">
	<!-- Toolbar -->
	<div class="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 bg-zinc-900/50 shrink-0">
		{#if albumPath}
			<span class="text-sm text-zinc-300 truncate max-w-xs">{albumPath}</span>
		{:else}
			<span class="text-sm text-zinc-400">All Photos</span>
		{/if}

		<span class="text-xs text-zinc-600 ml-1">({total.toLocaleString()})</span>

		<div class="ml-auto flex items-center gap-2">
			<SlidersHorizontal size={14} class="text-zinc-500" />
			<select
				class="text-xs bg-zinc-800 text-zinc-300 border border-zinc-700 rounded px-2 py-1"
				value={sort}
				onchange={(e) => setParam('sort', (e.target as HTMLSelectElement).value)}
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
				<div class="w-5 h-5 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
				Loading…
			</div>
		{:else if photos.length === 0}
			<div class="flex items-center justify-center h-40 text-zinc-500 text-sm">No photos found</div>
		{:else}
			<PhotoGrid {photos} onSelect={openPhoto} />
		{/if}
	</div>

	<!-- Pagination -->
	{#if totalPages > 1}
		<div class="shrink-0 flex items-center justify-center gap-3 px-4 py-3 border-t border-zinc-800 text-sm">
			<button
				class="p-1 rounded hover:bg-zinc-800 text-zinc-400 disabled:opacity-30"
				disabled={currentPage <= 1}
				onclick={() => setPage(currentPage - 1)}
			>
				<ChevronLeft size={16} />
			</button>
			<span class="text-zinc-400">Page {currentPage} / {totalPages}</span>
			<button
				class="p-1 rounded hover:bg-zinc-800 text-zinc-400 disabled:opacity-30"
				disabled={currentPage >= totalPages}
				onclick={() => setPage(currentPage + 1)}
			>
				<ChevronRight size={16} />
			</button>
		</div>
	{/if}
</div>

{#if selectedId !== null}
	<PhotoLightbox
		photoId={selectedId}
		onClose={closeLightbox}
		onPrev={selectedIdx > 0 ? prevPhoto : undefined}
		onNext={selectedIdx < photos.length - 1 ? nextPhoto : undefined}
	/>
{/if}

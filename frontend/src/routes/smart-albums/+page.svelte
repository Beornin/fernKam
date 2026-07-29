<script lang="ts">
	import { api, type SavedSearchOut, type PhotoSummary } from '$lib/api';
	import { Bookmark, Plus, Pencil, Trash2, Play, X, Check, Pin, Search } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { statusCountStore, thumbSizeStore } from '$lib/stores';
	import { throttle } from '$lib/throttle';
	import { getThumbSize } from '$lib/thumbUtils';

	const thumbSize = $derived($thumbSizeStore);

	let savedSearches = $state<SavedSearchOut[]>([]);
	let loading = $state(true);
	let selectedSearch = $state<SavedSearchOut | null>(null);
	let photos = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let nextCursor = $state<string | null>(null);
	let loadingPhotos = $state(false);
	let loadingMore = $state(false);

	let renamingId = $state<number | null>(null);
	let renameVal = $state('');

	const PAGE_SIZE = 200;

	async function loadList() {
		loading = true;
		try {
			savedSearches = await api.savedSearches.list();
			statusCountStore.set(`${savedSearches.length} smart album${savedSearches.length === 1 ? '' : 's'}`);
		} finally {
			loading = false;
		}
	}

	async function runSearch(ss: SavedSearchOut) {
		selectedSearch = ss;
		loadingPhotos = true;
		photos = [];
		nextCursor = null;
		try {
			const data = await api.savedSearches.photos(ss.id, { page_size: PAGE_SIZE });
			photos = data.items;
			total = data.total;
			nextCursor = data.next_cursor ?? null;
		} finally {
			loadingPhotos = false;
		}
	}

	async function loadMore() {
		if (!selectedSearch || loadingMore || photos.length >= total) return;
		loadingMore = true;
		try {
			const data = await api.savedSearches.photos(selectedSearch.id, nextCursor
				? { cursor: nextCursor, page_size: PAGE_SIZE }
				: { page: Math.floor(photos.length / PAGE_SIZE) + 1, page_size: PAGE_SIZE }
			);
			photos = [...photos, ...data.items];
			nextCursor = data.next_cursor ?? null;
		} finally {
			loadingMore = false;
		}
	}

	// Throttle the scroll-threshold check itself (not just loadMore, which is
	// already re-entrancy-guarded) — a fast scroll/fling fires onscroll dozens
	// of times per second, all doing pointless comparison work.
	const onPhotosScroll = throttle((el: HTMLElement) => {
		if (el.scrollHeight - el.scrollTop - el.clientHeight < 300) loadMore();
	}, 150);

	async function deleteSearch(ss: SavedSearchOut) {
		if (!confirm(`Delete smart album "${ss.name}"?`)) return;
		await api.savedSearches.delete(ss.id);
		savedSearches = savedSearches.filter(s => s.id !== ss.id);
		if (selectedSearch?.id === ss.id) { selectedSearch = null; photos = []; }
	}

	async function togglePin(ss: SavedSearchOut) {
		const updated = await api.savedSearches.update(ss.id, { pin_to_sidebar: !ss.pin_to_sidebar });
		savedSearches = savedSearches.map(s => s.id === ss.id ? updated : s);
		if (selectedSearch?.id === ss.id) selectedSearch = updated;
	}

	function startRename(ss: SavedSearchOut) { renamingId = ss.id; renameVal = ss.name; }
	function cancelRename() { renamingId = null; renameVal = ''; }
	async function commitRename(ss: SavedSearchOut) {
		if (!renameVal.trim() || renameVal === ss.name) { cancelRename(); return; }
		const updated = await api.savedSearches.update(ss.id, { name: renameVal.trim() });
		savedSearches = savedSearches.map(s => s.id === ss.id ? updated : s);
		if (selectedSearch?.id === ss.id) selectedSearch = updated;
		cancelRename();
	}

	onMount(loadList);
</script>

<div class="flex h-full overflow-hidden">
	<!-- Left panel: list of saved searches -->
	<aside class="w-[220px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
		<div class="px-3 py-3 border-b border-zinc-800 flex items-center gap-2">
			<span class="text-xs font-semibold text-zinc-300 flex-1">Smart Albums</span>
			<button
				onclick={() => goto('/photos?tab=search')}
				class="p-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300"
				title="Go to Search to create a new smart album"
			><Plus size={14} /></button>
		</div>

		<div class="flex-1 overflow-y-auto py-1">
			{#if loading}
				<div class="flex justify-center py-8"><div class="w-4 h-4 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div></div>
			{:else if savedSearches.length === 0}
				<div class="px-4 py-6 text-center">
					<Bookmark size={24} class="text-zinc-600 mx-auto mb-2" />
					<p class="text-xs text-zinc-500">No smart albums yet.</p>
					<p class="text-xs text-zinc-600 mt-1">Use Search to build a filter, then save it.</p>
				</div>
			{:else}
				{#each savedSearches as ss (ss.id)}
					<div
						class="group flex items-center gap-1 px-2 py-1 rounded mx-1 cursor-pointer transition-colors
							{selectedSearch?.id === ss.id ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-zinc-800'}"
						onclick={() => runSearch(ss)}
					>
						{#if renamingId === ss.id}
							<input type="text" bind:value={renameVal}
								class="flex-1 bg-zinc-800 border border-emerald-500 rounded px-1 py-0.5 text-xs text-zinc-100 focus:outline-none"
								onclick={(e) => e.stopPropagation()}
								onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Enter') commitRename(ss); if (e.key === 'Escape') cancelRename(); }} />
							<button onclick={(e) => { e.stopPropagation(); commitRename(ss); }} class="p-0.5 text-emerald-400"><Check size={12} /></button>
							<button onclick={(e) => { e.stopPropagation(); cancelRename(); }} class="p-0.5 text-zinc-500"><X size={12} /></button>
						{:else}
							<Bookmark size={13} class="{ss.pin_to_sidebar ? 'text-amber-400' : 'text-zinc-500'} shrink-0" />
							<span class="flex-1 text-xs text-zinc-200 truncate py-1">{ss.name}</span>
							<div class="hidden group-hover:flex items-center gap-0.5">
								<button onclick={(e) => { e.stopPropagation(); togglePin(ss); }}
									class="p-0.5 rounded hover:bg-zinc-600 {ss.pin_to_sidebar ? 'text-amber-400' : 'text-zinc-500'}"
									title="{ss.pin_to_sidebar ? 'Unpin' : 'Pin to sidebar'}"><Pin size={11} /></button>
								<button onclick={(e) => { e.stopPropagation(); startRename(ss); }}
									class="p-0.5 rounded hover:bg-zinc-600 text-zinc-400"><Pencil size={11} /></button>
								<button onclick={(e) => { e.stopPropagation(); deleteSearch(ss); }}
									class="p-0.5 rounded hover:bg-red-600/30 text-zinc-400 hover:text-red-400"><Trash2 size={11} /></button>
							</div>
						{/if}
					</div>
				{/each}
			{/if}
		</div>
	</aside>

	<!-- Right: photo grid -->
	<div class="flex-1 flex flex-col overflow-hidden">
		{#if !selectedSearch}
			<div class="flex-1 flex flex-col items-center justify-center text-zinc-600 gap-3">
				<Bookmark size={48} />
				<p class="text-sm">Select a smart album to view its photos</p>
				<button onclick={() => goto('/photos?tab=search')} class="flex items-center gap-2 text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300">
					<Search size={13} /> Go to Search
				</button>
			</div>
		{:else}
			<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/40 flex items-center gap-3">
				<Bookmark size={16} class="text-amber-400 shrink-0" />
				<div>
					<h2 class="text-base font-semibold text-zinc-100">{selectedSearch.name}</h2>
					{#if selectedSearch.description}
						<p class="text-xs text-zinc-500">{selectedSearch.description}</p>
					{/if}
				</div>
				<span class="ml-auto text-xs text-zinc-500">{total.toLocaleString()} photo{total === 1 ? '' : 's'}</span>
			</div>

			<div class="flex-1 overflow-y-auto p-4" onscroll={(e) => onPhotosScroll(e.currentTarget)}>
				{#if loadingPhotos}
					<div class="flex justify-center py-16"><div class="w-6 h-6 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div></div>
				{:else if photos.length === 0}
					<div class="flex flex-col items-center justify-center h-48 text-zinc-600 gap-2">
						<Bookmark size={32} />
						<p class="text-sm">No photos match this filter</p>
					</div>
				{:else}
					<div class="grid gap-1" style="grid-template-columns: repeat(auto-fill, minmax({thumbSize}px, 1fr))">
						{#each photos as photo (photo.id)}
							<button
								onclick={() => goto(`/photos?photo_id=${photo.id}&back=${encodeURIComponent('/smart-albums')}`)}
								class="group relative aspect-square bg-zinc-900 rounded overflow-hidden"
							>
								<img src="/media/thumbnail/{photo.id}?size={getThumbSize(thumbSize)}" alt={photo.filename}
									class="w-full h-full object-cover" loading="lazy" />
							</button>
						{/each}
					</div>
					{#if loadingMore}
						<div class="flex justify-center py-4"><div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div></div>
					{/if}
				{/if}
			</div>
		{/if}
	</div>
</div>

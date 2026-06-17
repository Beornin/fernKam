<script lang="ts">
	import { api, type PhotoSummary, type TagOut } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { Search, Star } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { statusCountStore } from '$lib/stores';

	let query = $state('');
	let ratingMin = $state<number | undefined>(undefined);
	let mediaType = $state('');
	let tagId = $state<number | undefined>(undefined);
	let personTagId = $state<number | undefined>(undefined);
	let results = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let selectedId = $state<number | null>(null);
	let selectedIdx = $state(-1);
	let searched = $state(false);
	let selectedIds = $state<Set<number>>(new Set());
	let tags = $state<TagOut[]>([]);
	let personTags = $state<TagOut[]>([]);

	onMount(async () => {
		tags = await api.tags.list({ flat: true });
		personTags = tags.filter(t => t.is_person);
		tags = tags.filter(t => !t.is_person);
	});

	async function doSearch() {
		loading = true;
		searched = true;
		const data = await api.photos.list({
			search: query.trim() || undefined,
			rating_min: ratingMin,
			media_type: mediaType || undefined,
			tag_id: tagId || personTagId || undefined,
			page_size: 200,
		});
		results = data.items;
		total = data.total;
		loading = false;
		statusCountStore.set(`${data.total.toLocaleString()} results`);
	}

	function clearSearch() {
		query = '';
		ratingMin = undefined;
		mediaType = '';
		tagId = undefined;
		personTagId = undefined;
		results = [];
		searched = false;
		statusCountStore.set('');
	}

	function openPhoto(p: PhotoSummary) { selectedId = p.id; selectedIdx = results.indexOf(p); }
	function closeLightbox() { selectedId = null; selectedIdx = -1; }
	function prevPhoto() { if (selectedIdx > 0) { selectedIdx--; selectedId = results[selectedIdx].id; } }
	function nextPhoto() { if (selectedIdx < results.length - 1) { selectedIdx++; selectedId = results[selectedIdx].id; } }
</script>

<div class="flex h-full overflow-hidden">
	<!-- Search left panel -->
	<aside class="w-[220px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
		<div class="px-3 py-2 border-b border-zinc-800 shrink-0">
			<span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Search</span>
		</div>

		<form class="flex-1 overflow-y-auto" onsubmit={(e) => { e.preventDefault(); doSearch(); }}>
			<div class="p-3 space-y-3">
				<!-- Filename search -->
				<div>
					<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1">Filename</label>
					<div class="relative">
						<Search size={11} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600" />
						<input
							type="text"
							placeholder="Contains…"
							class="w-full pl-6 pr-2 py-1.5 text-xs bg-zinc-800 border border-zinc-700 rounded text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500"
							bind:value={query}
						/>
					</div>
				</div>

				<!-- Media type -->
				<div>
					<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1">Type</label>
					<select class="w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300" bind:value={mediaType}>
						<option value="">All types</option>
						<option value="image">Photos only</option>
						<option value="video">Videos only</option>
					</select>
				</div>

				<!-- Rating -->
				<div>
					<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1">Rating</label>
					<select class="w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300" bind:value={ratingMin}>
						<option value={undefined}>Any rating</option>
						<option value={1}>★ 1+</option>
						<option value={2}>★★ 2+</option>
						<option value={3}>★★★ 3+</option>
						<option value={4}>★★★★ 4+</option>
						<option value={5}>★★★★★ 5</option>
					</select>
				</div>

				<!-- Tag filter -->
				<div>
					<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1">Tag</label>
					<select class="w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300" bind:value={tagId}>
						<option value={undefined}>All tags</option>
						{#each tags as tag}
							<option value={tag.id}>{tag.path.replace(/\./g, ' › ')}</option>
						{/each}
					</select>
				</div>

				<!-- Person filter -->
				<div>
					<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1">Person</label>
					<select class="w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300" bind:value={personTagId}>
						<option value={undefined}>All people</option>
						{#each personTags as tag}
							<option value={tag.id}>{tag.name}</option>
						{/each}
					</select>
				</div>

				<button
					type="submit"
					class="w-full py-2 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white font-medium transition-colors flex items-center justify-center gap-1.5"
				>
					<Search size={12} /> Search
				</button>

				{#if searched}
					<button
						type="button"
						onclick={clearSearch}
						class="w-full py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400 transition-colors"
					>
						Clear
					</button>
				{/if}
			</div>
		</form>
	</aside>

	<!-- Results -->
	<div class="flex flex-col flex-1 overflow-hidden">
		{#if searched}
			<div class="px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 shrink-0 flex items-center gap-2">
				<span class="text-xs text-zinc-400 flex-1">
					{loading ? 'Searching…' : total > 200 ? `Showing first 200 of ${total.toLocaleString()}` : `${total.toLocaleString()} results`}
				</span>
			</div>
		{/if}
		<div class="flex-1 overflow-y-auto">
			{#if loading}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm gap-2">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					Searching…
				</div>
			{:else if searched && results.length === 0}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm">No results found</div>
			{:else if !searched}
				<div class="flex flex-col items-center justify-center h-full text-zinc-600 gap-3">
					<Search size={40} class="opacity-30" />
					<p class="text-sm">Use the filters to search your library</p>
				</div>
			{:else}
				<PhotoGrid photos={results} onSelect={openPhoto} bind:selectedIds />
			{/if}
		</div>
	</div>
</div>

{#if selectedId !== null}
	<PhotoLightbox
		photoId={selectedId}
		onClose={closeLightbox}
		onPrev={selectedIdx > 0 ? prevPhoto : undefined}
		onNext={selectedIdx < results.length - 1 ? nextPhoto : undefined}
	/>
{/if}

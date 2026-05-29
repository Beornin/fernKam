<script lang="ts">
	import { api, type PhotoSummary, type TagOut } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { Search } from '@lucide/svelte';
	import { onMount } from 'svelte';

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
		if (!query.trim() && !ratingMin && !mediaType && !tagId && !personTagId) return;
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
	}

	function openPhoto(p: PhotoSummary) {
		selectedId = p.id;
		selectedIdx = results.indexOf(p);
	}
	function closeLightbox() { selectedId = null; selectedIdx = -1; }
	function prevPhoto() {
		if (selectedIdx > 0) { selectedIdx--; selectedId = results[selectedIdx].id; }
	}
	function nextPhoto() {
		if (selectedIdx < results.length - 1) { selectedIdx++; selectedId = results[selectedIdx].id; }
	}
</script>

<div class="p-6">
	<h1 class="text-xl font-semibold text-zinc-100 mb-6">Search</h1>

	<form class="flex flex-wrap gap-3 mb-6" onsubmit={(e) => { e.preventDefault(); doSearch(); }}>
		<div class="relative flex-1 min-w-[200px]">
			<Search size={15} class="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
			<input
				type="text"
				placeholder="Filename contains…"
				class="w-full pl-9 pr-3 py-2 text-sm bg-zinc-800 border border-zinc-700 rounded-lg text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
				bind:value={query}
			/>
		</div>

		<select
			class="text-sm bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-300"
			bind:value={mediaType}
		>
			<option value="">All types</option>
			<option value="image">Photos only</option>
			<option value="video">Videos only</option>
		</select>

		<select
			class="text-sm bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-300"
			bind:value={tagId}
		>
			<option value={undefined}>All tags</option>
			{#each tags as tag}
				<option value={tag.id}>{tag.path.replace(/\./g, ' > ')}</option>
			{/each}
		</select>

		<select
			class="text-sm bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-300"
			bind:value={personTagId}
		>
			<option value={undefined}>All people</option>
			{#each personTags as tag}
				<option value={tag.id}>{tag.path.replace(/\./g, ' > ')}</option>
			{/each}
		</select>

		<select
			class="text-sm bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-300"
			bind:value={ratingMin}
		>
			<option value={undefined}>Any rating</option>
			<option value={1}>★ 1+</option>
			<option value={2}>★★ 2+</option>
			<option value={3}>★★★ 3+</option>
			<option value={4}>★★★★ 4+</option>
			<option value={5}>★★★★★ 5</option>
		</select>

		<button
			type="submit"
			class="px-5 py-2 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium transition-colors"
		>
			Search
		</button>
	</form>

	{#if loading}
		<div class="flex items-center gap-2 text-zinc-500 text-sm">
			<div class="w-5 h-5 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
			Searching…
		</div>
	{:else if searched && results.length === 0}
		<p class="text-zinc-500 text-sm">No results.</p>
	{:else if results.length > 0}
		<p class="text-xs text-zinc-500 mb-3">
			{total > 200 ? `Showing first 200 of ${total.toLocaleString()}` : `${total.toLocaleString()} results`}
		</p>
		<PhotoGrid photos={results} onSelect={openPhoto} bind:selectedIds={selectedIds} />
	{/if}
</div>

{#if selectedId !== null}
	<PhotoLightbox
		photoId={selectedId}
		onClose={closeLightbox}
		onPrev={selectedIdx > 0 ? prevPhoto : undefined}
		onNext={selectedIdx < results.length - 1 ? nextPhoto : undefined}
	/>
{/if}

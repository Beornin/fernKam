<script lang="ts">
	import { api, type PhotoSummary, type TagOut } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { Search, X, ChevronDown, Bookmark } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import BatchEditBar from '$lib/components/BatchEditBar.svelte';
	import { onMount } from 'svelte';
	import { statusCountStore } from '$lib/stores';

	const PAGE_SIZE = 200;

	// ── filter state ─────────────────────────────────────────────────────────
	let query = $state('');
	let ratingMin = $state<number | undefined>(undefined);
	let mediaType = $state('');
	let tagId = $state<number | undefined>(undefined);
	let personTagId = $state<number | undefined>(undefined);
	let cameraId = $state<number | undefined>(undefined);
	let lensId = $state<number | undefined>(undefined);
	let dateFrom = $state('');
	let dateTo = $state('');
	let hasGps = $state('');
	let hasFaces = $state('');
	let noDate = $state(false);
	let colorLabel = $state<number | undefined>(undefined);
	let sort = $state('taken_at_desc');

	// tag/person typeahead
	let tagSearch = $state('');
	let personSearch = $state('');

	// ── data ─────────────────────────────────────────────────────────────────
	let results = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let page = $state(1);
	let nextCursor = $state<string | null>(null);
	let loading = $state(false);
	let loadingMore = $state(false);
	let searched = $state(false);
	let selectedId = $state<number | null>(null);
	let selectedIdx = $state(-1);
	let selectedIds = $state<Set<number>>(new Set());

	let allTags = $state<TagOut[]>([]);
	let allPersonTags = $state<TagOut[]>([]);
	let cameras = $state<Array<{ id: number; label: string }>>([]);
	let lenses = $state<Array<{ id: number; label: string }>>([]);

	// ── derived filtered lists for typeahead ─────────────────────────────────
	let filteredTags = $derived(
		tagSearch.trim()
			? allTags.filter(t => t.name.toLowerCase().includes(tagSearch.toLowerCase())).slice(0, 40)
			: allTags.slice(0, 40)
	);
	let filteredPersons = $derived(
		personSearch.trim()
			? allPersonTags.filter(t => t.name.toLowerCase().includes(personSearch.toLowerCase())).slice(0, 40)
			: allPersonTags.slice(0, 40)
	);
	let filteredCameras = $derived(cameras);
	let filteredLenses = $derived(lenses);

	let selectedTagName = $derived(tagId ? allTags.find(t => t.id === tagId)?.name ?? '' : '');
	let selectedPersonName = $derived(personTagId ? allPersonTags.find(t => t.id === personTagId)?.name ?? '' : '');

	const COLOR_LABELS = [
		{ value: 1, name: 'Red',    cls: 'bg-red-500' },
		{ value: 2, name: 'Orange', cls: 'bg-orange-500' },
		{ value: 3, name: 'Yellow', cls: 'bg-yellow-400' },
		{ value: 4, name: 'Green',  cls: 'bg-green-500' },
		{ value: 5, name: 'Blue',   cls: 'bg-blue-500' },
		{ value: 6, name: 'Purple', cls: 'bg-purple-500' },
		{ value: 7, name: 'Gray',   cls: 'bg-zinc-400' },
	];

	onMount(async () => {
		const [tagData, camData, lensData] = await Promise.all([
			api.tags.list({ flat: true }),
			api.photos.cameras(),
			api.photos.lenses(),
		]);
		allPersonTags = tagData.filter(t => t.is_person);
		allTags = tagData.filter(t => !t.is_person);
		cameras = camData;
		lenses = lensData;
	});

	function buildParams(pg = 1) {
		return {
			search: query.trim() || undefined,
			rating_min: ratingMin,
			color_label: colorLabel,
			media_type: mediaType || undefined,
			tag_id: tagId || undefined,
			person_tag_id: personTagId || undefined,
			camera_id: cameraId || undefined,
			lens_id: lensId || undefined,
			date_from: dateFrom || undefined,
			date_to: dateTo || undefined,
			has_gps: hasGps === '' ? undefined : hasGps === 'yes',
			has_faces: hasFaces === '' ? undefined : hasFaces === 'yes',
			no_date: noDate || undefined,
			sort,
			page: pg,
			page_size: PAGE_SIZE,
		};
	}

	async function doSearch() {
		loading = true;
		searched = true;
		page = 1;
		nextCursor = null;
		const data = await api.photos.list(buildParams(1));
		results = data.items;
		total = data.total;
		nextCursor = data.next_cursor ?? null;
		loading = false;
		statusCountStore.set(`${data.total.toLocaleString()} results`);
	}

	async function loadMore() {
		if (loadingMore || results.length >= total) return;
		loadingMore = true;
		const params = nextCursor
			? { ...buildParams(1), cursor: nextCursor, page: undefined }
			: buildParams(page + 1);
		const data = await api.photos.list(params);
		results = [...results, ...data.items];
		nextCursor = data.next_cursor ?? null;
		if (!nextCursor) page += 1;
		loadingMore = false;
	}

	function clearSearch() {
		query = ''; ratingMin = undefined; colorLabel = undefined;
		mediaType = ''; tagId = undefined; personTagId = undefined;
		cameraId = undefined; lensId = undefined;
		dateFrom = ''; dateTo = ''; hasGps = ''; hasFaces = '';
		noDate = false; sort = 'taken_at_desc';
		tagSearch = ''; personSearch = '';
		results = []; total = 0; page = 1; searched = false;
		statusCountStore.set('');
	}

	function openPhoto(p: PhotoSummary) { selectedId = p.id; selectedIdx = results.indexOf(p); }
	function closeLightbox() { selectedId = null; selectedIdx = -1; }
	function prevPhoto() { if (selectedIdx > 0) { selectedIdx--; selectedId = results[selectedIdx].id; } }
	function nextPhoto() { if (selectedIdx < results.length - 1) { selectedIdx++; selectedId = results[selectedIdx].id; } }

	const selectCls = 'w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300 focus:outline-none focus:ring-1 focus:ring-amber-500';
	const inputCls = 'w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500';
	const labelCls = 'text-[10px] text-zinc-500 uppercase tracking-wider block mb-1';

	// ── save search ───────────────────────────────────────────────────────────
	let showSaveModal = $state(false);
	let saveSearchName = $state('');
	let savingSearch = $state(false);
	let saveError = $state('');

	function buildFilters() {
		return {
			search: query.trim() || undefined,
			rating_min: ratingMin,
			color_label: colorLabel,
			media_type: mediaType || undefined,
			tag_id: tagId || undefined,
			person_tag_id: personTagId || undefined,
			camera_id: cameraId || undefined,
			lens_id: lensId || undefined,
			date_from: dateFrom || undefined,
			date_to: dateTo || undefined,
			has_gps: hasGps === '' ? undefined : hasGps === 'yes',
			has_faces: hasFaces === '' ? undefined : hasFaces === 'yes',
			no_date: noDate || undefined,
		};
	}

	async function saveSearch() {
		if (!saveSearchName.trim()) { saveError = 'Name required'; return; }
		savingSearch = true; saveError = '';
		try {
			await api.savedSearches.create({ name: saveSearchName.trim(), filters: buildFilters() as Record<string, unknown>, sort });
			showSaveModal = false; saveSearchName = '';
			goto('/smart-albums');
		} catch (e: any) {
			saveError = e.message ?? 'Save failed';
		} finally {
			savingSearch = false;
		}
	}
</script>

<div class="flex h-full overflow-hidden">
	<!-- ── Filter sidebar ──────────────────────────────────────────────────── -->
	<aside class="w-[230px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
		<div class="px-3 py-2 border-b border-zinc-800 shrink-0 flex items-center gap-2">
			<span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex-1">Search</span>
			{#if searched}
				<button onclick={clearSearch} class="text-zinc-600 hover:text-zinc-400 transition-colors" title="Clear all">
					<X size={13} />
				</button>
			{/if}
		</div>

		<form class="flex-1 overflow-y-auto" onsubmit={(e) => { e.preventDefault(); doSearch(); }}>
			<div class="p-3 space-y-3">

				<!-- Full-text -->
				<div>
					<label class={labelCls}>Keywords</label>
					<div class="relative">
						<Search size={11} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600 pointer-events-none" />
						<input type="text" placeholder="Filename, caption, tag, camera…"
							class="w-full pl-6 pr-2 py-1.5 text-xs bg-zinc-800 border border-zinc-700 rounded text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500"
							bind:value={query} />
					</div>
				</div>

				<!-- Date range -->
				<div>
					<label class={labelCls}>Date taken</label>
					<div class="flex items-center gap-1">
						<input type="date" bind:value={dateFrom} class={inputCls} />
						<span class="text-zinc-600 text-xs shrink-0">–</span>
						<input type="date" bind:value={dateTo} class={inputCls} />
					</div>
					<label class="flex items-center gap-1.5 mt-1.5 cursor-pointer">
						<input type="checkbox" bind:checked={noDate} class="accent-amber-500 w-3 h-3" />
						<span class="text-[10px] text-zinc-500">No date only</span>
					</label>
				</div>

				<!-- Media type -->
				<div>
					<label class={labelCls}>Type</label>
					<select class={selectCls} bind:value={mediaType}>
						<option value="">All types</option>
						<option value="image">Photos only</option>
						<option value="video">Videos only</option>
					</select>
				</div>

				<!-- Rating -->
				<div>
					<label class={labelCls}>Rating</label>
					<select class={selectCls} bind:value={ratingMin}>
						<option value={undefined}>Any rating</option>
						<option value={1}>★ 1+</option>
						<option value={2}>★★ 2+</option>
						<option value={3}>★★★ 3+</option>
						<option value={4}>★★★★ 4+</option>
						<option value={5}>★★★★★ 5 only</option>
					</select>
				</div>

				<!-- Color label -->
				<div>
					<label class={labelCls}>Color label</label>
					<div class="flex flex-wrap gap-1.5 mt-0.5">
						<button type="button"
							onclick={() => colorLabel = undefined}
							class="w-5 h-5 rounded border text-[9px] flex items-center justify-center transition-all
								{colorLabel === undefined ? 'border-amber-400 ring-1 ring-amber-400 bg-zinc-700' : 'border-zinc-600 bg-zinc-800 hover:border-zinc-400'}"
							title="Any">✕</button>
						{#each COLOR_LABELS as cl}
							<button type="button"
								onclick={() => colorLabel = colorLabel === cl.value ? undefined : cl.value}
								class="w-5 h-5 rounded {cl.cls} transition-all {colorLabel === cl.value ? 'ring-2 ring-white ring-offset-1 ring-offset-zinc-900 scale-110' : 'opacity-70 hover:opacity-100'}"
								title={cl.name}></button>
						{/each}
					</div>
				</div>

				<!-- Tag typeahead -->
				<div>
					<label class={labelCls}>Tag {tagId ? `· ${selectedTagName}` : ''}</label>
					<div class="relative">
						<input type="text" placeholder="Search tags…" bind:value={tagSearch} class={inputCls} />
					</div>
					{#if filteredTags.length}
						<div class="mt-1 max-h-28 overflow-y-auto rounded border border-zinc-700 bg-zinc-850">
							<button type="button" onclick={() => { tagId = undefined; tagSearch = ''; }}
								class="w-full text-left px-2 py-1 text-[11px] text-zinc-500 hover:bg-zinc-700 {!tagId ? 'bg-zinc-700/50' : ''}">All tags</button>
							{#each filteredTags as tag}
								<button type="button" onclick={() => { tagId = tag.id; tagSearch = tag.name; }}
									class="w-full text-left px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-700 truncate {tagId === tag.id ? 'bg-zinc-700/80 text-amber-400' : ''}">
									{tag.path.replace(/\./g, ' › ')}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<!-- Person typeahead -->
				<div>
					<label class={labelCls}>Person {personTagId ? `· ${selectedPersonName}` : ''}</label>
					<div class="relative">
						<input type="text" placeholder="Search people…" bind:value={personSearch} class={inputCls} />
					</div>
					{#if filteredPersons.length}
						<div class="mt-1 max-h-28 overflow-y-auto rounded border border-zinc-700">
							<button type="button" onclick={() => { personTagId = undefined; personSearch = ''; }}
								class="w-full text-left px-2 py-1 text-[11px] text-zinc-500 hover:bg-zinc-700 {!personTagId ? 'bg-zinc-700/50' : ''}">All people</button>
							{#each filteredPersons as p}
								<button type="button" onclick={() => { personTagId = p.id; personSearch = p.name; }}
									class="w-full text-left px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-700 truncate {personTagId === p.id ? 'bg-zinc-700/80 text-amber-400' : ''}">
									{p.name}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<!-- Camera -->
				{#if cameras.length}
					<div>
						<label class={labelCls}>Camera</label>
						<select class={selectCls} bind:value={cameraId}>
							<option value={undefined}>Any camera</option>
							{#each filteredCameras as cam}
								<option value={cam.id}>{cam.label}</option>
							{/each}
						</select>
					</div>
				{/if}

				<!-- Lens -->
				{#if lenses.length}
					<div>
						<label class={labelCls}>Lens</label>
						<select class={selectCls} bind:value={lensId}>
							<option value={undefined}>Any lens</option>
							{#each filteredLenses as l}
								<option value={l.id}>{l.label}</option>
							{/each}
						</select>
					</div>
				{/if}

				<!-- Location / faces -->
				<div class="flex gap-2">
					<div class="flex-1">
						<label class={labelCls}>Location</label>
						<select class={selectCls} bind:value={hasGps}>
							<option value="">Any</option>
							<option value="yes">Has GPS</option>
							<option value="no">No GPS</option>
						</select>
					</div>
					<div class="flex-1">
						<label class={labelCls}>Faces</label>
						<select class={selectCls} bind:value={hasFaces}>
							<option value="">Any</option>
							<option value="yes">Has faces</option>
							<option value="no">No faces</option>
						</select>
					</div>
				</div>

				<!-- Sort -->
				<div>
					<label class={labelCls}>Sort</label>
					<select class={selectCls} bind:value={sort}>
						<option value="taken_at_desc">Date ↓ newest first</option>
						<option value="taken_at_asc">Date ↑ oldest first</option>
						<option value="rating_desc">Rating ↓</option>
						<option value="filename_asc">Filename A→Z</option>
						<option value="imported_at_desc">Imported ↓</option>
					</select>
				</div>

				<button type="submit"
					class="w-full py-2 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white font-medium transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
					disabled={loading}>
					<Search size={12} /> {loading ? 'Searching…' : 'Search'}
				</button>

				{#if searched}
					<button type="button" onclick={() => { saveSearchName = ''; showSaveModal = true; }}
						class="w-full py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors flex items-center justify-center gap-1.5">
						<Bookmark size={12} /> Save as Smart Album
					</button>
					<button type="button" onclick={clearSearch}
						class="w-full py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400 transition-colors">
						Clear all filters
					</button>
				{/if}
			</div>
		</form>
	</aside>

	<!-- ── Results panel ───────────────────────────────────────────────────── -->
	<div class="flex flex-col flex-1 overflow-hidden">
		{#if searched}
			<div class="px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 shrink-0 flex items-center gap-3">
				<span class="text-xs text-zinc-400 flex-1">
					{#if loading}
						Searching…
					{:else}
						<strong class="text-zinc-200">{total.toLocaleString()}</strong> result{total !== 1 ? 's' : ''}
						{#if results.length < total}
							· showing {results.length.toLocaleString()}
						{/if}
					{/if}
				</span>
				{#if !loading && results.length < total}
					<button onclick={loadMore} disabled={loadingMore}
						class="text-[11px] px-2.5 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors disabled:opacity-50 flex items-center gap-1">
						{loadingMore ? 'Loading…' : `Load more (${(total - results.length).toLocaleString()} left)`}
					</button>
				{/if}
			</div>
		{/if}

		<div class="flex-1 overflow-y-auto">
			{#if loading}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm gap-2">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					Searching…
				</div>
			{:else if searched && results.length === 0}
				<div class="flex flex-col items-center justify-center h-40 text-zinc-500 gap-2">
					<Search size={32} class="opacity-20" />
					<p class="text-sm">No results for these filters</p>
				</div>
			{:else if !searched}
				<div class="flex flex-col items-center justify-center h-full text-zinc-600 gap-3">
					<Search size={40} class="opacity-30" />
					<p class="text-sm">Use the filters to search your library</p>
					<p class="text-xs text-zinc-700">Searches filename, title, caption, tags, and camera model</p>
				</div>
			{:else}
				<PhotoGrid photos={results} onSelect={openPhoto} bind:selectedIds />
				{#if results.length < total}
					<div class="flex justify-center py-4">
						<button onclick={loadMore} disabled={loadingMore}
							class="px-4 py-2 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors disabled:opacity-50 flex items-center gap-1.5">
							<ChevronDown size={13} />
							{loadingMore ? 'Loading…' : `Load ${Math.min(PAGE_SIZE, total - results.length).toLocaleString()} more`}
						</button>
					</div>
				{/if}
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

<BatchEditBar bind:selectedIds />

{#if showSaveModal}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onclick={() => showSaveModal = false}>
		<div class="bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-80 p-5" onclick={(e) => e.stopPropagation()}>
			<h3 class="text-sm font-semibold text-zinc-200 mb-4 flex items-center gap-2">
				<Bookmark size={15} class="text-amber-400" /> Save as Smart Album
			</h3>
			<input type="text" placeholder="Album name…"
				bind:value={saveSearchName}
				onkeydown={(e) => { if (e.key === 'Enter') saveSearch(); if (e.key === 'Escape') showSaveModal = false; }}
				class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500 mb-3" />
			{#if saveError}
				<p class="text-xs text-red-400 mb-3">{saveError}</p>
			{/if}
			<div class="flex gap-2 justify-end">
				<button onclick={() => showSaveModal = false}
					class="px-3 py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400">Cancel</button>
				<button onclick={saveSearch} disabled={savingSearch}
					class="px-4 py-1.5 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50 flex items-center gap-1.5">
					<Bookmark size={11} /> {savingSearch ? 'Saving…' : 'Save'}
				</button>
			</div>
		</div>
	</div>
{/if}

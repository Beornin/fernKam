<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { api, type TagOut } from '$lib/api';
	import { Search, X, Bookmark } from '@lucide/svelte';
	import { buildFilterUrl } from '$lib/shellFilters';

	// ── filter state (initialized from the URL on mount, see below) ──────────
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

	// tag/person typeahead
	let tagSearch = $state('');
	let personSearch = $state('');

	let allTags = $state<TagOut[]>([]);
	let allPersonTags = $state<TagOut[]>([]);
	let cameras = $state<Array<{ id: number; label: string }>>([]);
	let lenses = $state<Array<{ id: number; label: string }>>([]);

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

	let selectedTagName = $derived(tagId ? allTags.find(t => t.id === tagId)?.name ?? '' : '');
	let selectedPersonName = $derived(personTagId ? allPersonTags.find(t => t.id === personTagId)?.name ?? '' : '');

	let hasActiveFilters = $derived(!!(
		query || ratingMin || colorLabel || mediaType || tagId || personTagId ||
		cameraId || lensId || dateFrom || dateTo || hasGps || hasFaces || noDate
	));

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
		const sp = $page.url.searchParams;
		query = sp.get('search') ?? '';
		ratingMin = sp.get('rating_min') ? Number(sp.get('rating_min')) : undefined;
		colorLabel = sp.get('color_label') ? Number(sp.get('color_label')) : undefined;
		mediaType = sp.get('media_type') ?? '';
		tagId = sp.get('tag_id') ? Number(sp.get('tag_id')) : undefined;
		personTagId = sp.get('person_tag_id') ? Number(sp.get('person_tag_id')) : undefined;
		cameraId = sp.get('camera_id') ? Number(sp.get('camera_id')) : undefined;
		lensId = sp.get('lens_id') ? Number(sp.get('lens_id')) : undefined;
		dateFrom = sp.get('date_from') ?? '';
		dateTo = sp.get('date_to') ?? '';
		const gps = sp.get('has_gps');
		hasGps = gps === null ? '' : (gps === '1' || gps === 'true' ? 'yes' : 'no');
		const faces = sp.get('has_faces');
		hasFaces = faces === null ? '' : (faces === '1' || faces === 'true' ? 'yes' : 'no');
		noDate = sp.get('no_date') === '1' || sp.get('no_date') === 'true';

		const [tagData, camData, lensData] = await Promise.all([
			api.tags.list({ flat: true }),
			api.photos.cameras(),
			api.photos.lenses(),
		]);
		allPersonTags = tagData.filter(t => t.is_person);
		allTags = tagData.filter(t => !t.is_person);
		cameras = camData;
		lenses = lensData;
		if (tagId) tagSearch = allTags.find(t => t.id === tagId)?.name ?? '';
		if (personTagId) personSearch = allPersonTags.find(t => t.id === personTagId)?.name ?? '';
	});

	function buildFilters() {
		return {
			search: query.trim() || undefined,
			rating_min: ratingMin,
			color_label: colorLabel,
			media_type: mediaType || undefined,
			tag_id: tagId,
			person_tag_id: personTagId,
			camera_id: cameraId,
			lens_id: lensId,
			date_from: dateFrom || undefined,
			date_to: dateTo || undefined,
			has_gps: hasGps === '' ? undefined : (hasGps === 'yes' ? '1' : '0'),
			has_faces: hasFaces === '' ? undefined : (hasFaces === 'yes' ? '1' : '0'),
			no_date: noDate ? '1' : undefined,
		};
	}

	function doSearch() {
		goto(buildFilterUrl($page.url, 'search', buildFilters()));
	}

	function clearSearch() {
		query = ''; ratingMin = undefined; colorLabel = undefined;
		mediaType = ''; tagId = undefined; personTagId = undefined;
		cameraId = undefined; lensId = undefined;
		dateFrom = ''; dateTo = ''; hasGps = ''; hasFaces = '';
		noDate = false; tagSearch = ''; personSearch = '';
		goto(buildFilterUrl($page.url, 'search', {}));
	}

	const selectCls = 'w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300 focus:outline-none focus:ring-1 focus:ring-amber-500';
	const inputCls = 'w-full text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500';
	const labelCls = 'text-[10px] text-zinc-500 uppercase tracking-wider block mb-1';

	// ── save search ───────────────────────────────────────────────────────────
	let showSaveModal = $state(false);
	let saveSearchName = $state('');
	let savingSearch = $state(false);
	let saveError = $state('');

	async function saveSearch() {
		if (!saveSearchName.trim()) { saveError = 'Name required'; return; }
		savingSearch = true; saveError = '';
		try {
			await api.savedSearches.create({
				name: saveSearchName.trim(),
				filters: buildFilters() as Record<string, unknown>,
				sort: $page.url.searchParams.get('sort') ?? 'taken_at_desc',
			});
			showSaveModal = false; saveSearchName = '';
			goto('/smart-albums');
		} catch (e: any) {
			saveError = e.message ?? 'Save failed';
		} finally {
			savingSearch = false;
		}
	}
</script>

<div class="px-3 py-2 border-b border-zinc-800 shrink-0 flex items-center gap-2">
	<span class="text-[10px] text-zinc-600 flex-1">Filters</span>
	{#if hasActiveFilters}
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
					{#each cameras as cam}
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
					{#each lenses as l}
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

		<button type="submit"
			class="w-full py-2 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white font-medium transition-colors flex items-center justify-center gap-1.5">
			<Search size={12} /> Search
		</button>

		{#if hasActiveFilters}
			<button type="button" onclick={() => { saveSearchName = ''; showSaveModal = true; }}
				class="w-full py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors flex items-center justify-center gap-1.5">
				<Bookmark size={12} /> Save as Smart Album
			</button>
		{/if}
	</div>
</form>

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

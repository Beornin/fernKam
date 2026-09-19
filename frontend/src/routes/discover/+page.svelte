<script lang="ts">
	// Phase 2 UI: semantic search, visual near-duplicates, and kNN tag
	// suggestions. All three read the one CLIP embedding column, so they share
	// a results grid — PhotoGrid is reused as-is rather than reimplemented.
	import { api, type PhotoSummary, type SemanticHit, type TagSuggestion } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { Search, Sparkles, Copy, Tag, Check, X, Loader } from '@lucide/svelte';
	import { onMount } from 'svelte';

	let query = $state('');
	let results = $state<PhotoSummary[]>([]);
	let searching = $state(false);
	let searched = $state(false);
	let error = $state<string | null>(null);
	let mode = $state<'text' | 'similar'>('text');
	let similarTo = $state<number | null>(null);

	let selectedIds = $state(new Set<number>());
	let lightboxPhoto = $state<PhotoSummary | null>(null);

	let status = $state<{ embedded: number; total: number; remaining: number } | null>(null);
	let embedding = $state(false);

	// Tag suggestions for the single selected photo.
	let suggestFor = $state<number | null>(null);
	let suggestions = $state<TagSuggestion[]>([]);
	let suggestLoading = $state(false);
	let accepted = $state(new Set<number>());
	let applyMsg = $state<string | null>(null);

	const toSummary = (h: SemanticHit): PhotoSummary => ({
		id: h.id, digikam_id: null, filename: h.filename, album_path: h.album_path,
		taken_at: h.taken_at, rating: h.rating ?? 0, color_label: 0,
		media_type: h.media_type ?? 'image', width: null, height: null,
		file_size: h.file_size ?? null, duration_secs: null,
	});

	function ingest(hits: SemanticHit[]) {
		results = hits.map(toSummary);
		selectedIds = new Set();
	}

	onMount(async () => {
		try { status = await api.semantic.status(); } catch { /* status is advisory */ }
		// Arrived from the grid's "Find visually similar" context action.
		const sim = new URL(window.location.href).searchParams.get('similar');
		if (sim) await findSimilar(Number(sim));
	});

	async function runSearch() {
		const q = query.trim();
		if (!q || searching) return;
		searching = true; error = null; mode = 'text'; similarTo = null;
		try {
			const res = await api.semantic.search({ q, limit: 200 });
			ingest(res.results);
			searched = true;
		} catch (e) {
			error = `Search failed: ${e}`;
		} finally {
			searching = false;
		}
	}

	async function findSimilar(photoId: number) {
		if (searching) return;
		searching = true; error = null; mode = 'similar'; similarTo = photoId;
		try {
			const res = await api.semantic.similar(photoId, { limit: 100, min_score: 0.75 });
			ingest(res.results);
			searched = true;
		} catch (e) {
			error = `Similar lookup failed: ${e}`;
		} finally {
			searching = false;
		}
	}

	async function loadSuggestions(photoId: number) {
		suggestFor = photoId; suggestions = []; accepted = new Set();
		applyMsg = null; suggestLoading = true;
		try {
			const res = await api.semantic.suggestTags(photoId, { k: 25, limit: 10 });
			suggestions = res.suggestions;
		} catch (e) {
			error = `Tag suggestions failed: ${e}`;
		} finally {
			suggestLoading = false;
		}
	}

	function toggleAccepted(tagId: number) {
		const next = new Set(accepted);
		if (next.has(tagId)) next.delete(tagId); else next.add(tagId);
		accepted = next;
	}

	async function applyAccepted() {
		if (!suggestFor || accepted.size === 0) return;
		// Apply to every selected photo when a selection exists, so one
		// confirmed suggestion can cover a whole batch of similar shots.
		const targets = selectedIds.size > 0 ? [...selectedIds] : [suggestFor];
		try {
			const res = await api.semantic.applyTags({ photo_ids: targets, tag_ids: [...accepted] });
			applyMsg = `Added ${res.linked} tag link${res.linked === 1 ? '' : 's'} across ${targets.length} photo${targets.length === 1 ? '' : 's'}.`;
			accepted = new Set();
			await loadSuggestions(suggestFor);
		} catch (e) {
			error = `Apply failed: ${e}`;
		}
	}

	async function startEmbedding() {
		embedding = true;
		try {
			await api.semantic.embed();
			const poll = setInterval(async () => {
				try {
					status = await api.semantic.status();
					if (status.remaining === 0) { clearInterval(poll); embedding = false; }
				} catch { /* keep polling */ }
			}, 4000);
		} catch (e) {
			error = `Could not start embedding: ${e}`;
			embedding = false;
		}
	}

	const pct = $derived(status && status.total > 0
		? Math.round((status.embedded / status.total) * 100) : 0);
	const selectedOne = $derived(selectedIds.size === 1 ? [...selectedIds][0] : null);

	$effect(() => { if (selectedOne !== null) loadSuggestions(selectedOne); });
</script>

<svelte:head><title>Discover · fernKam</title></svelte:head>

<div class="h-full flex flex-col bg-zinc-950">
	<header class="px-4 py-3 border-b border-zinc-800 shrink-0">
		<div class="flex items-center gap-2 mb-3">
			<Sparkles size={16} class="text-amber-400" />
			<h1 class="text-sm font-semibold text-zinc-200">Discover</h1>
			{#if status}
				<span class="text-[11px] text-zinc-500 ml-2">
					{status.embedded.toLocaleString()} / {status.total.toLocaleString()} photos indexed ({pct}%)
				</span>
				{#if status.remaining > 0}
					<button onclick={startEmbedding} disabled={embedding}
						class="text-[11px] px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-50">
						{embedding ? 'Indexing…' : `Index ${status.remaining.toLocaleString()} more`}
					</button>
				{/if}
			{/if}
		</div>

		<form onsubmit={(e) => { e.preventDefault(); runSearch(); }} class="flex gap-2">
			<div class="relative flex-1">
				<Search size={13} class="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
				<input bind:value={query} placeholder="Describe what you're looking for — “otter in snow”, “red barn at sunset”"
					class="w-full pl-8 pr-3 py-2 text-xs bg-zinc-900 border border-zinc-800 rounded text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-amber-600" />
			</div>
			<button type="submit" disabled={searching || !query.trim()}
				class="px-4 py-2 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50 flex items-center gap-1.5">
				{#if searching}<Loader size={12} class="animate-spin" />{:else}<Search size={12} />{/if}
				Search
			</button>
		</form>
		<p class="text-[11px] text-zinc-600 mt-1.5">
			Searches what the photo <em>looks like</em>, so it finds untagged photos too.
		</p>
	</header>

	{#if error}
		<div class="mx-4 mt-3 px-3 py-2 text-xs rounded bg-red-950/60 border border-red-900 text-red-300">{error}</div>
	{/if}

	<div class="flex-1 flex min-h-0">
		<div class="flex-1 min-w-0 flex flex-col">
			{#if searched}
				<div class="px-4 py-1.5 text-[11px] text-zinc-500 border-b border-zinc-800/60 flex items-center gap-2">
					{#if mode === 'similar'}
						<Copy size={11} /> Visually similar to #{similarTo}
					{:else}
						<Search size={11} /> “{query}”
					{/if}
					<span>· {results.length} result{results.length === 1 ? '' : 's'}</span>
					{#if selectedIds.size > 0}<span class="text-amber-500">· {selectedIds.size} selected</span>{/if}
				</div>
			{/if}

			{#if results.length > 0}
				<div class="flex-1 min-h-0">
					<PhotoGrid photos={results} bind:selectedIds onSelect={(p) => lightboxPhoto = p} />
				</div>
			{:else if searched && !searching}
				<div class="flex-1 flex items-center justify-center text-xs text-zinc-600 px-6 text-center">
					No matches.{#if status && status.remaining > 0}<br />Only {pct}% of the library is indexed so far.{/if}
				</div>
			{:else}
				<div class="flex-1 flex items-center justify-center text-xs text-zinc-600 px-6 text-center">
					Search by description, then select a photo to see suggested tags.
				</div>
			{/if}
		</div>

		<!-- Suggestions panel: kNN over your own taxonomy, not a generic label set -->
		{#if selectedOne !== null}
			<aside class="w-72 border-l border-zinc-800 flex flex-col shrink-0 bg-zinc-950">
				<div class="px-3 py-2 border-b border-zinc-800 flex items-center gap-1.5">
					<Tag size={13} class="text-amber-400" />
					<span class="text-xs font-semibold text-zinc-300">Suggested tags</span>
				</div>
				<div class="px-3 py-2 border-b border-zinc-800/60">
					<button onclick={() => findSimilar(selectedOne)}
						class="w-full py-1.5 text-[11px] rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center justify-center gap-1.5">
						<Copy size={11} /> Find visually similar
					</button>
				</div>

				<div class="flex-1 overflow-y-auto p-3 space-y-1.5">
					{#if suggestLoading}
						<p class="text-[11px] text-zinc-600">Looking at similar photos…</p>
					{:else if suggestions.length === 0}
						<p class="text-[11px] text-zinc-600">
							No confident suggestions. Its nearest neighbours are untagged, or it already has their tags.
						</p>
					{:else}
						{#each suggestions as s (s.tag_id)}
							<button onclick={() => toggleAccepted(s.tag_id)}
								class="w-full text-left px-2 py-1.5 rounded border transition-colors
									{accepted.has(s.tag_id)
										? 'bg-amber-950/40 border-amber-700 text-amber-200'
										: 'bg-zinc-900 border-zinc-800 text-zinc-300 hover:border-zinc-700'}">
								<div class="flex items-center justify-between gap-2">
									<span class="text-[11px] truncate">{s.name}</span>
									{#if accepted.has(s.tag_id)}<Check size={11} class="shrink-0 text-amber-400" />{/if}
								</div>
								<div class="text-[10px] text-zinc-600 truncate">{s.path} · {s.votes} of nearest</div>
							</button>
						{/each}
					{/if}
				</div>

				{#if applyMsg}
					<p class="px-3 pb-1 text-[11px] text-emerald-400">{applyMsg}</p>
				{/if}
				<div class="p-3 border-t border-zinc-800 flex gap-2">
					<button onclick={applyAccepted} disabled={accepted.size === 0}
						class="flex-1 py-1.5 text-[11px] rounded bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-40 flex items-center justify-center gap-1.5">
						<Check size={11} /> Apply {accepted.size || ''}
					</button>
					<button onclick={() => { accepted = new Set(); applyMsg = null; }}
						class="px-2 py-1.5 text-[11px] rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400">
						<X size={11} />
					</button>
				</div>
			</aside>
		{/if}
	</div>
</div>

{#if lightboxPhoto}
	<PhotoLightbox
		photoId={lightboxPhoto.id}
		onClose={() => lightboxPhoto = null}
		onPrev={() => {
			const i = results.findIndex(p => p.id === lightboxPhoto?.id);
			if (i > 0) lightboxPhoto = results[i - 1];
		}}
		onNext={() => {
			const i = results.findIndex(p => p.id === lightboxPhoto?.id);
			if (i >= 0 && i < results.length - 1) lightboxPhoto = results[i + 1];
		}}
	/>
{/if}

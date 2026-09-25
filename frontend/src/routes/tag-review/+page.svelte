<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page as pageStore } from '$app/state';
	import {
		api,
		type TagReviewDetail,
		type TagReviewPhoto,
		type TagReviewState,
		type TagReviewSummary,
		type TagReviewTag,
	} from '$lib/api';
	import { notify } from '$lib/dialog.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import TagModelsPanel from '$lib/components/TagModelsPanel.svelte';
	import SpeciesLinkDialog from '$lib/components/SpeciesLinkDialog.svelte';
	import type { GbifTaxon } from '$lib/api';
	import { Tags, Search, Check, X, Brain, RefreshCw, AlertTriangle, History, ChevronLeft, ChevronRight, Cpu, Eye, Type, Globe } from '@lucide/svelte';

	const PAGE = 60;

	let summary = $state<TagReviewSummary | null>(null);
	let tags = $state<TagReviewTag[]>([]);
	let search = $state('');
	let selectedId = $state<number | null>(null);
	let detail = $state<TagReviewDetail | null>(null);
	let tab = $state<TagReviewState>('unverified');
	let sort = $state('date');
	let photos = $state<TagReviewPhoto[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let loading = $state(false);
	let busy = $state(false);
	let learning = $state(false);
	let focused = $state(0);
	let previewIndex = $state<number | null>(null);
	let result = $state('');
	let modelsOpen = $state(false);
	let vision = $state<{ ready: boolean; model: string | null }>({ ready: false, model: null });
	let learnAllCheck = $state(true);
	let checking = $state(false);
	let finding = $state(false);
	let speciesOpen = $state(false);
	// Tiles the user clicked. On every tab but Rejected that means "wrong";
	// on Rejected it means "right after all".
	let marked = $state(new Set<number>());

	const filteredTags = $derived(
		search.trim()
			? tags.filter(t => t.path.toLowerCase().includes(search.trim().toLowerCase()))
			: tags
	);
	const markMeansRight = $derived(tab === 'rejected');
	const unmarkedCount = $derived(photos.length - marked.size);
	const sortOptions = $derived<Record<TagReviewState, [string, string][]>>({
		unverified: [
			['date', 'By date'],
			...(detail?.model ? [['doubtful', 'Most doubtful first'], ['score', 'Most likely first']] as [string, string][] : []),
			...(vision.ready ? [['vision_no', 'Vision model disputes first']] as [string, string][] : []),
		],
		suggested: [['score', 'Best first'], ['vision_yes', 'Vision model agrees first'], ['date', 'By date']],
		approved: [['date', 'By date'], ['recent', 'Recently approved']],
		rejected: [['recent', 'Recently rejected']],
	});

	function pretty(path: string) { return path.replace(/\./g, ' › '); }
	function parentOf(path: string) { const i = path.lastIndexOf('.'); return i > 0 ? pretty(path.slice(0, i)) : ''; }
	function pct(x: number | null | undefined) { return x == null ? '–' : `${Math.round(x * 100)}%`; }
	function scoreClass(s: number) {
		return s >= 0.8 ? 'bg-emerald-600/90 text-white' : s >= 0.5 ? 'bg-amber-600/90 text-white' : 'bg-red-700/90 text-white';
	}

	async function loadSummary() {
		try { summary = await api.tagReview.summary(); } catch { /* shown as empty */ }
	}
	async function loadTags() {
		try { tags = await api.tagReview.tags(); } catch (e) { notify(`Could not load tags: ${e}`, 'danger'); }
	}
	async function loadDetail() {
		if (selectedId === null) return;
		detail = await api.tagReview.tag(selectedId);
	}
	async function loadPhotos(newOffset = 0) {
		if (selectedId === null) return;
		loading = true;
		try {
			const r = await api.tagReview.photos(selectedId, { state: tab, sort, limit: PAGE, offset: newOffset });
			photos = r.photos;
			total = r.total;
			offset = newOffset;
			marked = new Set();
			focused = 0;
		} finally {
			loading = false;
		}
	}

	async function selectTag(id: number) {
		selectedId = id;
		result = '';
		tab = 'unverified';
		sort = 'date';
		goto(`/tag-review?tag=${id}`, { replaceState: true, keepFocus: true, noScroll: true });
		await loadDetail();
		// Open on whatever there is to do.
		if (detail && detail.counts.unverified === 0 && detail.counts.suggested > 0) {
			tab = 'suggested';
			sort = 'score';
		}
		await loadPhotos(0);
	}

	async function selectTab(t: TagReviewState) {
		tab = t;
		sort = sortOptions[t][0][0];
		await loadPhotos(0);
	}

	function toggle(id: number) {
		const next = new Set(marked);
		if (next.has(id)) next.delete(id); else next.add(id);
		marked = next;
	}

	/** Commit the page: what each tab's button says it will do. */
	async function commit(opts: { rejectAll?: boolean } = {}) {
		if (selectedId === null || busy || !photos.length) return;
		const ids = photos.map(p => p.photo_id);
		let approve: number[] = [];
		let reject: number[] = [];
		if (tab === 'unverified' || tab === 'suggested') {
			if (opts.rejectAll) reject = ids;
			else { approve = ids.filter(id => !marked.has(id)); reject = ids.filter(id => marked.has(id)); }
		} else if (tab === 'approved') {
			reject = [...marked];
		} else {
			approve = [...marked];
		}
		if (!approve.length && !reject.length) return;
		busy = true;
		try {
			const r = await api.tagReview.decide(selectedId, { approve, reject });
			const parts: string[] = [];
			if (r.approved) parts.push(`${r.approved} approved`);
			if (r.rejected) parts.push(`${r.rejected} rejected`);
			if (r.files_to_update) parts.push(`${r.files_to_update} file${r.files_to_update === 1 ? '' : 's'} to update at the next write-back`);
			if (r.retrained) parts.push(`relearned: ${r.retrained.suggestions} suggestions`);
			result = parts.join(' · ');
			await Promise.all([loadDetail(), loadTags(), loadSummary()]);
			await loadPhotos(tab === 'approved' || tab === 'rejected' ? offset : 0);
		} catch (e) {
			notify(`Could not save: ${e instanceof Error ? e.message : e}`, 'danger');
		} finally {
			busy = false;
		}
	}

	async function learnNow() {
		if (selectedId === null) return;
		learning = true;
		try {
			const r = await api.tagReview.train(selectedId);
			result = `Learned: ${r.suggestions} suggestions · finds ${pct(r.cv_recall)} of approved photos in testing`;
			await Promise.all([loadDetail(), loadTags(), loadSummary()]);
			if (tab === 'suggested' || tab === 'unverified') await loadPhotos(0);
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		} finally {
			learning = false;
		}
	}

	async function loadVision() {
		try {
			const m = await api.tagReview.models();
			vision = { ready: m.vision.reachable && !!m.vision.model, model: m.vision.model };
		} catch { vision = { ready: false, model: null }; }
	}

	/** Ask the local vision model about this tab's photos (background task),
	 * then refresh as answers arrive. */
	async function doubleCheck() {
		if (selectedId === null || (tab !== 'suggested' && tab !== 'unverified')) return;
		checking = true;
		try {
			const r = await api.tagReview.check(selectedId, { state: tab });
			if (!r.task_id) { notify(r.message); return; }
			result = `${vision.model} is checking ${r.queued} photos. Answers appear as they come in.`;
			const id = selectedId, t = tab;
			for (let i = 0; i < 600 && selectedId === id && tab === t; i++) {
				await new Promise(res => setTimeout(res, 3000));
				const task = (await api.sync.tasks()).tasks.find(x => x.id === r.task_id);
				await loadPhotosKeepMarks();
				if (!task || task.status !== 'running') { result = task?.message ?? result; break; }
			}
			await loadDetail();
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		} finally {
			checking = false;
		}
	}

	/** Reload the current page's badges without losing the user's marks. */
	async function loadPhotosKeepMarks() {
		if (selectedId === null) return;
		const r = await api.tagReview.photos(selectedId, { state: tab, sort, limit: PAGE, offset });
		const byId = new Map(r.photos.map(x => [x.photo_id, x]));
		photos = photos.map(x => byId.get(x.photo_id) ?? x);
	}

	async function findByName() {
		if (selectedId === null) return;
		finding = true;
		try {
			const r = await api.tagReview.findByName(selectedId);
			result = `Found ${r.suggestions} photos by name (${r.models.join(', ')}). Approve the right ones to start learning.`;
			await loadDetail();
			tab = 'suggested';
			sort = 'score';
			await loadPhotos(0);
			loadTags();
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		} finally {
			finding = false;
		}
	}

	/** Follow a background task, then refresh this tag's panel and photos. */
	async function followTask(taskId: string) {
		const id = selectedId;
		for (let i = 0; i < 400 && selectedId === id; i++) {
			await new Promise(res => setTimeout(res, 1500));
			const task = (await api.sync.tasks()).tasks.find(x => x.id === taskId);
			if (!task || task.status !== 'running') {
				if (task) result = task.message;
				break;
			}
			result = task.message;
		}
		if (selectedId === id) await Promise.all([loadDetail(), loadPhotosKeepMarks()]);
	}

	async function linkSpecies(t: GbifTaxon) {
		if (selectedId === null) return;
		try {
			const r = await api.tagReview.linkSpecies(selectedId, t);
			result = `Linked to ${t.common_name ?? t.scientific_name}. Fetching its GBIF range for your places…`;
			await loadDetail();
			await followTask(r.task_id);
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		}
	}

	async function refreshSpecies() {
		if (selectedId === null) return;
		try {
			const r = await api.tagReview.refreshSpecies(selectedId);
			result = 'Fetching GBIF range data for new places…';
			await followTask(r.task_id);
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		}
	}

	async function unlinkSpecies() {
		if (selectedId === null) return;
		await api.tagReview.unlinkSpecies(selectedId);
		await loadDetail();
		result = 'Unlinked. Relearn the tag to drop the range prior.';
	}

	async function learnAll() {
		learning = true;
		try {
			const r = await api.tagReview.trainAll(vision.ready && learnAllCheck);
			notify(r.task_id ? `Learning ${r.tags} tags in the background. Progress is on the Tasks page.` : r.message);
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		} finally {
			learning = false;
		}
	}

	function onKeydown(e: KeyboardEvent) {
		if (previewIndex !== null || selectedId === null || !photos.length) return;
		const t = e.target as HTMLElement;
		if (t.closest('input, textarea, select, [contenteditable]')) return;
		const k = e.key.toLowerCase();
		if (e.key === 'ArrowRight') { e.preventDefault(); focused = Math.min(photos.length - 1, focused + 1); }
		else if (e.key === 'ArrowLeft') { e.preventDefault(); focused = Math.max(0, focused - 1); }
		else if (k === 'x' || e.key === 'Delete') { e.preventDefault(); toggle(photos[focused].photo_id); focused = Math.min(photos.length - 1, focused + 1); }
		else if (e.key === ' ') { e.preventDefault(); previewIndex = focused; }
		else if (e.key === 'Enter') { e.preventDefault(); commit(); }
	}

	onMount(async () => {
		await Promise.all([loadSummary(), loadTags(), loadVision()]);
		const fromUrl = Number(pageStore.url.searchParams.get('tag'));
		if (fromUrl && tags.some(t => t.id === fromUrl)) await selectTag(fromUrl);
	});
</script>

<svelte:window onkeydown={onKeydown} />

{#if detail}
	<SpeciesLinkDialog bind:open={speciesOpen} tagName={detail.name} onLink={linkSpecies} />
{/if}

<TagModelsPanel bind:open={modelsOpen} onChanged={() => { loadVision(); loadSummary(); if (selectedId !== null) loadDetail(); }} />

{#if previewIndex !== null && photos[previewIndex]}
	<PhotoLightbox
		photoId={photos[previewIndex].photo_id}
		onClose={() => { focused = previewIndex ?? focused; previewIndex = null; }}
		onPrev={previewIndex > 0 ? () => { previewIndex = (previewIndex ?? 1) - 1; } : undefined}
		onNext={previewIndex < photos.length - 1 ? () => { previewIndex = (previewIndex ?? 0) + 1; } : undefined}
	/>
{/if}

<div class="flex flex-col h-full bg-zinc-950">
	<!-- Header -->
	<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3 flex-wrap">
		<Tags size={20} class="text-emerald-400" />
		<h1 class="text-base font-semibold text-zinc-100">Tag Review</h1>
		{#if summary}
			<span class="text-xs text-amber-300 bg-amber-400/10 px-2 py-0.5 rounded-full">{summary.unverified.toLocaleString()} to verify</span>
			<span class="text-xs text-violet-300 bg-violet-400/10 px-2 py-0.5 rounded-full">{summary.suggested.toLocaleString()} suggestions</span>
			<span class="text-xs text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded-full">{summary.approved.toLocaleString()} approved · {summary.models} {summary.models === 1 ? 'tag' : 'tags'} learning</span>
		{/if}
		<div class="ml-auto flex items-center gap-2">
			<button onclick={() => modelsOpen = true}
				class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 flex items-center gap-1"
				title="Image models and the local vision model">
				<Cpu size={12} /> Models
			</button>
			{#if vision.ready}
				<label class="text-xs text-zinc-400 flex items-center gap-1" title="After learning, {vision.model} double-checks each tag's best new suggestions">
					<input type="checkbox" bind:checked={learnAllCheck} class="accent-emerald-500" /> + vision check
				</label>
			{/if}
			<button onclick={learnAll} disabled={learning}
				class="text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white flex items-center gap-1 disabled:opacity-50"
				title="Relearn every tag with enough approved photos, and look for suggestions among new photos">
				<Brain size={12} /> Learn all tags
			</button>
		</div>
	</div>

	{#if summary && summary.photos > 0 && summary.embedded < summary.photos * 0.9}
		<div class="shrink-0 px-5 py-2 text-xs text-amber-300 bg-amber-950/30 border-b border-amber-900/40 flex items-center gap-2">
			<AlertTriangle size={12} />
			Only {summary.embedded.toLocaleString()} of {summary.photos.toLocaleString()} photos are in the search index. Suggestions can only come from indexed photos.
			<a href="/discover" class="underline hover:text-amber-200">Build the index on Discover</a>
		</div>
	{/if}

	<div class="flex flex-1 overflow-hidden">
		<!-- Tags -->
		<aside class="w-64 shrink-0 border-r border-zinc-800 flex flex-col overflow-hidden">
			<div class="p-2 border-b border-zinc-800">
				<div class="relative">
					<Search size={12} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
					<input type="text" bind:value={search} placeholder="Search tags…"
						class="w-full bg-zinc-800 border border-zinc-700 rounded pl-6 pr-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-emerald-500" />
				</div>
			</div>
			<nav class="flex-1 overflow-y-auto py-1">
				{#if filteredTags.length === 0}
					<p class="text-xs text-zinc-600 px-3 py-3">{tags.length ? 'No matching tags' : 'No tags on any photo yet'}</p>
				{/if}
				{#each filteredTags as t (t.id)}
					{@const checked = t.approved / Math.max(1, t.approved + t.unverified)}
					<button onclick={() => selectTag(t.id)}
						class="w-full text-left px-3 py-1.5 transition-colors {selectedId === t.id ? 'bg-emerald-600/20' : 'hover:bg-zinc-800'}">
						<div class="flex items-center gap-1.5">
							<span class="flex-1 min-w-0">
								<span class="block text-sm truncate {selectedId === t.id ? 'text-emerald-300' : t.unverified + t.approved + t.suggested + t.rejected ? 'text-zinc-200' : 'text-zinc-500'}">{t.name}</span>
								{#if parentOf(t.path)}<span class="block text-[10px] text-zinc-500 truncate">{parentOf(t.path)}</span>{/if}
							</span>
							{#if t.learning}<span title="Learning from your reviews"><Brain size={11} class="text-emerald-500 shrink-0" /></span>{/if}
							{#if t.unverified}<span class="text-[10px] text-amber-300" title="To verify">{t.unverified}</span>{/if}
							{#if t.suggested}<span class="text-[10px] text-violet-300" title="Suggestions">+{t.suggested}</span>{/if}
						</div>
						<div class="mt-1 h-0.5 rounded bg-zinc-800 overflow-hidden" title="{Math.round(checked * 100)}% of this tag's photos checked">
							<div class="h-full bg-emerald-600" style="width: {checked * 100}%"></div>
						</div>
					</button>
				{/each}
			</nav>
		</aside>

		<!-- Review pane -->
		<div class="flex-1 overflow-y-auto">
			{#if selectedId === null || !detail}
				<div class="flex flex-col items-center justify-center h-full gap-3 text-zinc-500 px-8 text-center">
					<Tags size={48} class="text-zinc-700" />
					<p class="text-sm font-medium text-zinc-300">Pick a tag on the left</p>
					<p class="text-xs max-w-md">
						Tags from files and imports start unverified. Check them here: approve the right ones, click the wrong ones.
						Once a tag has {summary?.min_positives ?? 8} approved photos, fernKam starts learning it from your decisions and
						suggests it on other photos. Only approved and rejected tags are used for learning.
					</p>
				</div>
			{:else}
				<div class="p-5 pb-24">
					<div class="flex items-center gap-3 flex-wrap mb-3">
						<h2 class="text-sm font-semibold text-zinc-100">{pretty(detail.path)}</h2>
						<div class="flex items-center rounded-lg bg-zinc-800 p-0.5 text-xs">
							{#each [['unverified', 'To verify'], ['suggested', 'Suggestions'], ['approved', 'Approved'], ['rejected', 'Rejected']] as [key, label]}
								<button onclick={() => selectTab(key as TagReviewState)}
									class="px-2.5 py-1 rounded-md transition-colors {tab === key ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200'}">
									{label} {detail.counts[key as TagReviewState]}
								</button>
							{/each}
						</div>
						{#if sortOptions[tab].length > 1}
							<select bind:value={sort} onchange={() => loadPhotos(0)}
								class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-300">
								{#each sortOptions[tab] as [value, label]}<option {value}>{label}</option>{/each}
							</select>
						{/if}
					</div>

					<!-- What the model knows -->
					<div class="mb-4 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-xs text-zinc-400 flex items-center gap-3 flex-wrap">
						<Brain size={14} class={detail.model ? 'text-emerald-400' : 'text-zinc-600'} />
						{#if detail.model}
							<span>
								Learning from <b class="text-zinc-200">{detail.learning_positives}</b> approved and
								<b class="text-zinc-200">{detail.learning_negatives}</b> rejected photos.
								In testing it finds <b class="text-zinc-200">{pct(detail.model.cv_recall)}</b> of your approved photos
								and agrees with <b class="text-zinc-200">{pct(detail.model.cv_agreement)}</b> of your decisions.
								{#if detail.model.hit_rate !== null}
									Suggestions you judged: <b class="text-zinc-200">{detail.model.reviewed_accepted} of {detail.model.reviewed_accepted + detail.model.reviewed_rejected}</b> right ({pct(detail.model.hit_rate)}).
								{/if}
							</span>
						{:else if detail.learning_positives < detail.min_positives}
							<span>Approve <b class="text-zinc-200">{detail.min_positives - detail.learning_positives}</b> more photos and fernKam starts learning this tag.</span>
							{#if detail.name_models.length}
								<button onclick={findByName} disabled={finding}
									class="px-2 py-1 rounded bg-violet-700/80 hover:bg-violet-600 text-white flex items-center gap-1 disabled:opacity-50"
									title="Search the library for this tag's name with {detail.name_models.join(', ')}">
									<Type size={11} /> {finding ? 'Searching…' : detail.found_by_name ? 'Find by name again' : 'Find by name'}
								</button>
							{/if}
						{:else}
							<span>Ready to learn from {detail.learning_positives} approved photos.</span>
						{/if}
						{#if detail.learning_positives >= detail.min_positives}
							<button onclick={learnNow} disabled={learning}
								class="ml-auto px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1 disabled:opacity-50">
								<RefreshCw size={11} class={learning ? 'animate-spin' : ''} /> {detail.model ? 'Relearn now' : 'Learn now'}
							</button>
						{/if}
						{#if detail.model && detail.model.experts.length > 1}
							{@const sum = detail.model.experts.reduce((a, e) => a + e.weight, 0) || 1}
							<div class="w-full flex items-center gap-2 pt-1">
								<span class="text-zinc-500 shrink-0">Trusts</span>
								<div class="flex-1 flex h-2 rounded overflow-hidden bg-zinc-800">
									{#each detail.model.experts as e, i}
										<div class="h-full {['bg-emerald-600', 'bg-sky-600', 'bg-amber-600', 'bg-violet-600'][i % 4]}"
											style="width: {(e.weight / sum) * 100}%"
											title="{e.label}: weight {Math.round((e.weight / sum) * 100)}%, alone finds {pct(e.cv_recall)} of approved photos"></div>
									{/each}
								</div>
								<span class="shrink-0 flex gap-2">
									{#each detail.model.experts as e, i}
										<span class="flex items-center gap-1" title="Alone: finds {pct(e.cv_recall)}, agrees with {pct(e.cv_agreement)} of your decisions">
											<span class="w-2 h-2 rounded-sm {['bg-emerald-600', 'bg-sky-600', 'bg-amber-600', 'bg-violet-600'][i % 4]}"></span>
											{e.label} {Math.round((e.weight / sum) * 100)}%
										</span>
									{/each}
								</span>
							</div>
						{/if}
						<div class="w-full flex items-center gap-1 text-zinc-400">
							<Globe size={12} class={detail.species ? 'text-emerald-400' : 'text-zinc-600'} />
							{#if detail.species}
								<span>
									Range prior: <b class="text-zinc-200">{detail.species.common_name ?? detail.species.scientific_name}</b>
									{#if detail.species.common_name}<i class="text-zinc-400">{detail.species.scientific_name}</i>{/if}
									({detail.species.class_name}) · GBIF data for {detail.species.places_with_data} of {detail.species.places} places
								</span>
								{#if detail.species.places_with_data < detail.species.places}
									<button onclick={refreshSpecies} class="px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300">Update</button>
								{/if}
								<button onclick={() => speciesOpen = true} class="px-1.5 py-0.5 rounded text-zinc-500 hover:text-zinc-200">Change</button>
								<button onclick={unlinkSpecies} class="px-1.5 py-0.5 rounded text-zinc-500 hover:text-red-400">Unlink</button>
							{:else}
								<span class="text-zinc-500">A species? Link it to GBIF so where and when it is recorded becomes a prior.</span>
								<button onclick={() => speciesOpen = true} class="px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300">Link species…</button>
							{/if}
						</div>
						{#if detail.vision.checked}
							<div class="w-full flex items-center gap-1 text-zinc-400">
								<Eye size={12} class="text-sky-400" />
								Vision model: checked {detail.vision.checked} photos{#if detail.vision.judged}; agreed with your decisions
									<b class="text-zinc-200">{detail.vision.agreed} of {detail.vision.judged}</b> times ({pct(detail.vision.rate)}){/if}.
							</div>
						{/if}
					</div>

					{#if vision.ready && (tab === 'suggested' || tab === 'unverified') && photos.length}
						<button onclick={doubleCheck} disabled={checking}
							class="float-right -mt-1 text-xs px-2 py-1 rounded bg-sky-800/70 hover:bg-sky-700 text-sky-100 flex items-center gap-1 disabled:opacity-50"
							title="Ask {vision.model} whether each photo here shows this tag">
							<Eye size={12} class={checking ? 'animate-pulse' : ''} /> {checking ? 'Checking…' : 'Double-check'}
						</button>
					{/if}
					<p class="text-[11px] text-zinc-500 mb-3">
						{#if tab === 'rejected'}
							Click the photos that do have this tag after all, then Restore. Space previews.
						{:else if tab === 'approved'}
							Click any photo that should not have this tag, then Reject. Space previews.
						{:else}
							Click the photos that are wrong (or ← → and X), then Enter approves the rest. Space previews.
						{/if}
					</p>
					{#if result}<p class="text-xs text-emerald-400 mb-3">{result}</p>{/if}

					{#if loading && photos.length === 0}
						<div class="flex items-center justify-center h-48">
							<div class="w-6 h-6 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
						</div>
					{:else if photos.length === 0}
						<div class="flex flex-col items-center justify-center h-48 gap-2 text-zinc-600">
							<Check size={32} />
							<p class="text-sm">
								{tab === 'unverified' ? 'Every photo with this tag is checked'
									: tab === 'suggested' ? (detail.model ? 'No suggestions right now'
										: detail.name_models.length ? 'Use "Find by name" above to look for this tag' : 'Suggestions appear once this tag is learning')
									: tab === 'approved' ? 'Nothing approved yet' : 'Nothing rejected'}
							</p>
						</div>
					{:else}
						<div class="grid gap-2" style="grid-template-columns: repeat(auto-fill, minmax(150px, 1fr))">
							{#each photos as p, i (p.photo_id)}
								{@const isMarked = marked.has(p.photo_id)}
								<button
									onclick={() => { focused = i; toggle(p.photo_id); }}
									ondblclick={() => { previewIndex = i; }}
									onmouseenter={() => focused = i}
									title="{p.album_path}/{p.filename}"
									class="relative rounded-lg overflow-hidden border-2 transition-all text-left
										{isMarked ? (markMeansRight ? 'border-emerald-500' : 'border-red-500') : i === focused ? 'border-emerald-400/70' : 'border-transparent'}"
								>
									<div class="aspect-square bg-zinc-800">
										<img src="/media/thumbnail/{p.photo_id}?size=md" alt={p.filename}
											class="w-full h-full object-cover transition-opacity {isMarked && !markMeansRight ? 'opacity-40' : ''}" loading="lazy" />
									</div>
									{#if p.source === 'name'}
										<span class="absolute top-1 left-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-violet-700/90 text-white"
											title="Found by the tag's name, not learned yet: {pct(p.score)} match">name</span>
									{:else if p.score !== null}
										<span class="absolute top-1 left-1 text-[10px] font-semibold px-1.5 py-0.5 rounded {scoreClass(p.score)}"
											title={tab === 'unverified' ? 'How sure the models are that this tag is right' : 'Model score'}>
											{pct(p.score)}
										</span>
									{/if}
									{#if p.vision !== null || p.vision_p !== null}
										<span class="absolute bottom-1 left-1 flex items-center gap-0.5 text-[10px] font-semibold px-1 py-0.5 rounded
											{p.vision === 1 ? 'bg-sky-600/90 text-white' : p.vision === 0 ? 'bg-zinc-900/90 text-red-300' : 'bg-zinc-800/90 text-zinc-300'}"
											title="Vision model: {p.vision === 1 ? 'yes' : p.vision === 0 ? 'no' : 'unsure'}{p.vision_p !== null ? ` (${pct(p.vision_p)} yes)` : ''}">
											<Eye size={10} />{p.vision === 1 ? '✓' : p.vision === 0 ? '✗' : '?'}
										</span>
									{/if}
									{#if p.range}
										<span class="absolute bottom-1 right-1 flex items-center gap-0.5 text-[10px] font-semibold px-1 py-0.5 rounded
											{p.range.status === 'absent' ? 'bg-red-800/90 text-white' : 'bg-amber-700/90 text-white'}"
											title={p.range.status === 'absent'
												? `GBIF: not recorded around here in ${p.range.when} (0 of ${p.range.class.toLocaleString()} records of its class)`
												: `GBIF: rare around here in ${p.range.when} (${p.range.species} of ${p.range.class.toLocaleString()} records of its class)`}>
											<Globe size={10} />{p.range.status === 'absent' ? 'not here' : 'rare'}
										</span>
									{/if}
									{#if p.rejected_before}
										<span class="absolute top-1 right-1 p-0.5 rounded bg-amber-900/80" title="You rejected this tag on this photo before; the file brought it back">
											<History size={10} class="text-amber-300" />
										</span>
									{/if}
									{#if isMarked}
										<span class="absolute inset-0 flex items-center justify-center">
											<span class="rounded-full p-2 {markMeansRight ? 'bg-emerald-600' : 'bg-red-600'}">
												{#if markMeansRight}<Check size={20} class="text-white" />{:else}<X size={20} class="text-white" />{/if}
											</span>
										</span>
									{/if}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<!-- Actions -->
				{#if photos.length}
					<div class="sticky bottom-0 border-t border-zinc-800 bg-zinc-900/95 backdrop-blur px-5 py-2.5 flex items-center gap-2 flex-wrap">
						{#if tab === 'unverified' || tab === 'suggested'}
							<button onclick={() => commit()} disabled={busy}
								class="text-xs px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1 disabled:opacity-50">
								<Check size={12} /> {tab === 'suggested' ? 'Accept' : 'Approve'} {unmarkedCount}{marked.size ? ` · reject ${marked.size}` : ''}
							</button>
							{#if tab === 'suggested'}
								<button onclick={() => commit({ rejectAll: true })} disabled={busy}
									class="text-xs px-3 py-1.5 rounded bg-zinc-700 hover:bg-red-700 text-zinc-200 flex items-center gap-1 disabled:opacity-50">
									<X size={12} /> Reject all {photos.length}
								</button>
							{/if}
						{:else if tab === 'approved'}
							<button onclick={() => commit()} disabled={busy || !marked.size}
								class="text-xs px-3 py-1.5 rounded bg-red-700 hover:bg-red-600 text-white flex items-center gap-1 disabled:opacity-40">
								<X size={12} /> Reject {marked.size}
							</button>
						{:else}
							<button onclick={() => commit()} disabled={busy || !marked.size}
								class="text-xs px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1 disabled:opacity-40">
								<Check size={12} /> Restore {marked.size}
							</button>
						{/if}
						{#if marked.size}
							<button onclick={() => marked = new Set()} class="text-xs px-2 py-1.5 rounded text-zinc-400 hover:text-zinc-200">Clear marks</button>
						{/if}
						<div class="ml-auto flex items-center gap-2 text-xs text-zinc-500">
							<span>{offset + 1}–{offset + photos.length} of {total.toLocaleString()}</span>
							<button onclick={() => loadPhotos(Math.max(0, offset - PAGE))} disabled={offset === 0 || loading}
								class="p-1 rounded hover:bg-zinc-800 disabled:opacity-30" aria-label="Previous page"><ChevronLeft size={14} /></button>
							<button onclick={() => loadPhotos(offset + PAGE)} disabled={offset + photos.length >= total || loading}
								class="p-1 rounded hover:bg-zinc-800 disabled:opacity-30" aria-label="Skip this page"><ChevronRight size={14} /></button>
						</div>
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>

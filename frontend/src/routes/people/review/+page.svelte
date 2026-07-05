<script lang="ts">
	import { api, type PersonOut, type FaceCluster } from '$lib/api';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import {
		ArrowLeft, RefreshCw, UserPlus, EyeOff, Trash2, SkipForward,
		ChevronLeft, ChevronRight, X, Search, CheckSquare, Square, Sparkles
	} from '@lucide/svelte';

	const BASE = 'http://localhost:8000';
	const PAGE = 20;

	let clusters = $state<FaceCluster[]>([]);
	let total = $state(0);
	let idx = $state(0);            // index within loaded clusters
	let offset = $state(0);         // server offset of the loaded page
	let loading = $state(true);
	let acting = $state(false);
	let done = $state(0);           // clusters processed this session

	// selection mode (deselect outliers)
	let selectMode = $state(false);
	let deselected = $state<Set<string>>(new Set());

	// photo lightbox (double-click a face to verify)
	let previewPhotoId = $state<number | null>(null);

	// rebuild task state
	let rebuilding = $state(false);
	let rebuildMsg = $state('');

	// auto-assign state
	let autoAssigning = $state(false);
	let autoAssignMsg = $state('');
	let autoAssignThresh = $state(0.82);
	let autoAssignOpen = $state(false);

	// new-person dialog
	let newPersonOpen = $state(false);
	let newPersonName = $state('');
	let creatingPerson = $state(false);

	// people picker
	let people = $state<PersonOut[]>([]);
	let pickerOpen = $state(false);
	let pickerSearch = $state('');
	let filteredPeople = $derived(
		pickerSearch.trim()
			? people.filter(p => p.name.toLowerCase().includes(pickerSearch.toLowerCase()))
			: people
	);

	let current = $derived(clusters[idx] ?? null);

	function activeFaceIds(): string[] {
		if (!current) return [];
		if (!selectMode) return current.faces.map(f => f.id);
		return current.faces.filter(f => !deselected.has(f.id)).map(f => f.id);
	}

	async function loadClusters(newOffset = 0) {
		loading = true;
		try {
			const res = await api.faces.clusters({ offset: newOffset, limit: PAGE });
			clusters = res.clusters;
			total = res.total;
			offset = newOffset;
			idx = 0;
			deselected = new Set();
		} finally {
			loading = false;
		}
	}

	async function advance() {
		deselected = new Set();
		selectMode = false;
		if (idx < clusters.length - 1) {
			idx++;
		} else {
			// Advance to next server page so Skip never loops back to cluster 1.
			const nextOffset = offset + PAGE;
			await loadClusters(nextOffset < total ? nextOffset : 0);
		}
	}

	function removeCurrentFromView() {
		clusters = clusters.filter((_, i) => i !== idx);
		if (idx >= clusters.length && idx > 0) idx = clusters.length - 1;
		deselected = new Set();
		selectMode = false;
	}

	async function assignTo(personTagId: number) {
		const ids = activeFaceIds();
		if (!ids.length || acting) return;
		acting = true;
		try {
			await api.faces.batchAssign({ face_ids: ids, person_tag_id: personTagId, status: 'confirmed' });
			done += ids.length;
			total = Math.max(0, total - 1);
			pickerOpen = false;
			removeCurrentFromView();
			if (clusters.length === 0) await loadClusters(0);
		} catch (e) {
			alert(`Failed to assign: ${e}`);
		} finally {
			acting = false;
		}
	}

	async function ignoreCluster() {
		const ids = activeFaceIds();
		if (!ids.length || acting) return;
		acting = true;
		try {
			await api.faces.batchAssign({ face_ids: ids, person_tag_id: null, status: 'ignored' });
			done += ids.length;
			total = Math.max(0, total - 1);
			removeCurrentFromView();
			if (clusters.length === 0) await loadClusters(0);
		} catch (e) {
			alert(`Failed to ignore: ${e}`);
		} finally {
			acting = false;
		}
	}

	async function deleteCluster() {
		const ids = activeFaceIds();
		if (!ids.length || acting) return;
		if (!confirm(`Permanently delete ${ids.length} face record(s)?`)) return;
		acting = true;
		try {
			await api.faces.batchDelete(ids);
			done += ids.length;
			total = Math.max(0, total - 1);
			removeCurrentFromView();
			if (clusters.length === 0) await loadClusters(0);
		} catch (e) {
			alert(`Failed to delete: ${e}`);
		} finally {
			acting = false;
		}
	}

	function toggleFace(id: string) {
		if (!selectMode) return;
		const next = new Set(deselected);
		if (next.has(id)) next.delete(id); else next.add(id);
		deselected = next;
	}

	function skip() { advance(); }
	function back() { if (idx > 0) { idx--; deselected = new Set(); selectMode = false; } }

	async function autoAssign() {
		if (autoAssigning) return;
		autoAssigning = true;
		autoAssignMsg = 'Starting…';
		try {
			const { task_id } = await api.faces.clustersAutoAssign({ min_score: autoAssignThresh });
			const poll = setInterval(async () => {
				try {
					const { tasks } = await api.sync.tasks();
					const t = tasks.find(t => t.id === task_id);
					if (!t) return;
					autoAssignMsg = t.message;
					if (t.status === 'completed' || t.status === 'failed') {
						clearInterval(poll);
						autoAssigning = false;
						if (t.status === 'completed') {
							people = await api.people.list({ limit: 500 });
							await loadClusters(0);
						}
					}
				} catch { /* ignore poll errors */ }
			}, 1500);
		} catch (e) {
			autoAssigning = false;
			autoAssignMsg = '';
			alert(`Auto-assign failed: ${e}`);
		}
	}

	async function createAndAssign() {
		const name = newPersonName.trim();
		if (!name || creatingPerson) return;
		creatingPerson = true;
		try {
			const person = await api.people.create(name);
			people = [...people, person].sort((a, b) => a.name.localeCompare(b.name));
			newPersonOpen = false;
			newPersonName = '';
			await assignTo(person.id);
		} catch (e) {
			alert(`Failed to create person: ${e}`);
		} finally {
			creatingPerson = false;
		}
	}

	async function rebuild() {
		if (rebuilding) return;
		rebuilding = true;
		rebuildMsg = 'Starting…';
		try {
			const { task_id } = await api.faces.clustersRebuild({});
			// poll task status
			const poll = setInterval(async () => {
				try {
					const { tasks } = await api.sync.tasks();
					const t = tasks.find(t => t.id === task_id);
					if (!t) return;
					rebuildMsg = t.message;
					if (t.status === 'completed' || t.status === 'failed') {
						clearInterval(poll);
						rebuilding = false;
						if (t.status === 'completed') await loadClusters(0);
					}
				} catch { /* ignore poll errors */ }
			}, 1500);
		} catch (e) {
			rebuilding = false;
			rebuildMsg = '';
			alert(`Rebuild failed: ${e}`);
		}
	}

	async function ignoreTiny() {
		if (acting) return;
		if (!confirm('Ignore all unconfirmed faces smaller than 50px? This clears junk crops.')) return;
		acting = true;
		try {
			const { ignored } = await api.faces.ignoreTiny(50);
			alert(`Ignored ${ignored} tiny faces. Rebuild clusters to refresh.`);
		} catch (e) {
			alert(`Failed: ${e}`);
		} finally {
			acting = false;
		}
	}

	function onKey(e: KeyboardEvent) {
		// Never fire single-letter shortcuts while the user is typing into any
		// text field (new-person name, people-picker search, etc.).
		const target = e.target as HTMLElement | null;
		const isTyping = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
		if (previewPhotoId !== null) {
			if (e.key === 'Escape') previewPhotoId = null;
			return;
		}
		if (newPersonOpen) {
			if (e.key === 'Escape') newPersonOpen = false;
			return;
		}
		if (pickerOpen) {
			if (e.key === 'Escape') pickerOpen = false;
			return;
		}
		if (isTyping) return;
		if (!current) return;
		const k = e.key.toLowerCase();
		if (k === 'a' && current.suggested_person) { e.preventDefault(); assignTo(current.suggested_person.person_id); }
		else if (k === 'n') { e.preventDefault(); newPersonOpen = true; newPersonName = ''; }
		else if (k === 'o') { e.preventDefault(); pickerOpen = true; }
		else if (k === 'i') { e.preventDefault(); ignoreCluster(); }
		else if (k === 'd') { e.preventDefault(); deleteCluster(); }
		else if (k === 's' || e.key === 'ArrowRight') { e.preventDefault(); skip(); }
		else if (e.key === 'ArrowLeft') { e.preventDefault(); back(); }
		else if (k === 'x') { e.preventDefault(); selectMode = !selectMode; }
	}

	onMount(async () => {
		people = await api.people.list({ limit: 500 });
		await loadClusters(0);
	});
</script>

<svelte:window onkeydown={onKey} />

<div class="flex flex-col h-full overflow-hidden bg-zinc-950">
	<!-- Header -->
	<div class="shrink-0 flex items-center gap-3 px-4 py-2.5 border-b border-zinc-800 bg-zinc-900">
		<button onclick={() => goto('/people')} class="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white" title="Back to People">
			<ArrowLeft size={16} />
		</button>
		<h1 class="text-sm font-semibold text-zinc-100 flex items-center gap-2">
			<Sparkles size={15} class="text-violet-400" /> Cluster Review
		</h1>
		<div class="flex-1"></div>
		{#if total > 0}
			<span class="text-xs text-zinc-400">{total} clusters left · {done} faces done</span>
		{/if}
		<button onclick={ignoreTiny} disabled={acting}
			class="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40 flex items-center gap-1" title="Ignore all faces under 50px">
			<EyeOff size={12} /> Ignore tiny
		</button>
		<button onclick={rebuild} disabled={rebuilding}
			class="text-xs px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-50 flex items-center gap-1" title="Rebuild clusters">
			<RefreshCw size={12} class={rebuilding ? 'animate-spin' : ''} /> {rebuilding ? 'Building…' : 'Rebuild'}
		</button>
		<button onclick={() => autoAssignOpen = !autoAssignOpen} disabled={autoAssigning}
			class="text-xs px-2 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-50 flex items-center gap-1" title="Auto-assign high-confidence clusters">
			<Sparkles size={12} class={autoAssigning ? 'animate-pulse' : ''} /> {autoAssigning ? 'Running…' : 'Auto-assign'}
		</button>
	</div>
	{#if autoAssignOpen}
	<div class="shrink-0 flex items-center gap-3 px-4 py-2 bg-emerald-900/30 border-b border-emerald-700/30 text-xs text-emerald-200">
		<span class="text-emerald-400">Min confidence:</span>
		<input type="range" min="0.70" max="0.95" step="0.01" bind:value={autoAssignThresh}
			class="w-28 accent-emerald-400" />
		<span class="w-8 text-right font-mono">{autoAssignThresh.toFixed(2)}</span>
		<span class="text-zinc-500">— clusters scoring above this are auto-confirmed</span>
		<div class="flex-1"></div>
		<button onclick={autoAssign} disabled={autoAssigning}
			class="px-3 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 font-medium">
			Run
		</button>
		<button onclick={() => autoAssignOpen = false} class="px-2 py-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={12} /></button>
	</div>
	{/if}

	{#if rebuilding && rebuildMsg}
		<div class="shrink-0 px-4 py-1.5 bg-violet-600/10 text-violet-300 text-xs border-b border-violet-600/20">{rebuildMsg}</div>
	{/if}
	{#if autoAssigning && autoAssignMsg}
		<div class="shrink-0 px-4 py-1.5 bg-emerald-600/10 text-emerald-300 text-xs border-b border-emerald-600/20">{autoAssignMsg}</div>
	{/if}

	<!-- Body -->
	<div class="flex-1 overflow-y-auto p-6">
		{#if loading}
			<div class="flex justify-center py-20">
				<div class="w-6 h-6 border-2 border-zinc-700 border-t-violet-400 rounded-full animate-spin"></div>
			</div>
		{:else if !current}
			<div class="flex flex-col items-center justify-center py-20 text-center gap-3">
				<p class="text-zinc-400 text-sm">No clusters to review.</p>
				<p class="text-zinc-600 text-xs max-w-md">Click <span class="text-violet-300">Rebuild</span> to group the remaining unconfirmed faces, then review each group here.</p>
			</div>
		{:else}
			<div class="max-w-5xl mx-auto">
				<!-- Cluster meta + nav -->
				<div class="flex items-center justify-between mb-4">
					<div class="flex items-center gap-2">
						<button onclick={back} disabled={idx === 0} class="p-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-30"><ChevronLeft size={16} /></button>
						<span class="text-xs text-zinc-400">Cluster {idx + 1} · {total} remaining</span>
						<button onclick={skip} class="p-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300"><ChevronRight size={16} /></button>
					</div>
					<div class="flex items-center gap-3">
						<span class="text-xs text-zinc-500">{activeFaceIds().length} / {current.size} selected</span>
						<button onclick={() => { selectMode = !selectMode; deselected = new Set(); }}
							class="text-xs px-2 py-1 rounded flex items-center gap-1 transition-colors {selectMode ? 'bg-violet-600 text-white' : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300'}"
							title="Toggle select mode (X)">
							{#if selectMode}<CheckSquare size={12} />{:else}<Square size={12} />{/if} Select faces
						</button>
					</div>
				</div>

				<!-- Suggested person banner -->
				{#if current.suggested_person && current.suggested_person.person_name}
					<button onclick={() => assignTo(current.suggested_person!.person_id)} disabled={acting}
						class="w-full mb-4 flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-emerald-600/20 border border-emerald-600/40 hover:bg-emerald-600/30 text-emerald-200 font-medium disabled:opacity-50 transition-colors">
						<UserPlus size={16} />
						Assign all to <span class="font-semibold">{current.suggested_person.person_name}</span>
						<span class="text-xs opacity-70">({current.suggested_person.score})</span>
						<kbd class="ml-1 text-[10px] bg-black/30 px-1.5 py-0.5 rounded">A</kbd>
					</button>
				{/if}

				<!-- Face grid -->
				<div class="grid gap-2 mb-6" style="grid-template-columns: repeat(auto-fill, minmax(120px, 1fr))">
					{#each current.faces as face (face.id)}
						{@const off = selectMode && deselected.has(face.id)}
						<button
							onclick={() => toggleFace(face.id)}
							ondblclick={() => previewPhotoId = face.photo_id}
							class="group relative aspect-square bg-zinc-900 rounded overflow-hidden border-2 transition-all {selectMode ? (off ? 'border-transparent opacity-30' : 'border-violet-400') : 'border-transparent cursor-default'}"
							title={selectMode ? 'Click to include/exclude' : 'Double-click to view full photo'}
						>
							<img src="{BASE}/media/face/{face.id}?size=200" alt="" class="w-full h-full object-cover" loading="lazy" />
							{#if selectMode && off}
								<div class="absolute inset-0 flex items-center justify-center bg-black/40"><X size={20} class="text-zinc-300" /></div>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		{/if}
	</div>

	<!-- Action bar -->
	{#if current && !loading}
		<div class="shrink-0 flex items-center justify-center gap-3 px-4 py-3 border-t border-zinc-800 bg-zinc-900">
			<button onclick={() => { newPersonOpen = true; newPersonName = ''; }} disabled={acting}
				class="flex items-center gap-2 px-4 py-2 rounded-lg bg-zinc-600 hover:bg-zinc-500 text-zinc-100 font-medium disabled:opacity-50">
				<UserPlus size={15} /> New person <kbd class="text-[10px] bg-black/20 px-1 rounded">N</kbd>
			</button>
			<button onclick={() => pickerOpen = true} disabled={acting}
				class="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50">
				<UserPlus size={15} /> Assign to… <kbd class="text-[10px] bg-black/20 px-1 rounded">O</kbd>
			</button>
			<button onclick={ignoreCluster} disabled={acting}
				class="flex items-center gap-2 px-4 py-2 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-100 font-medium disabled:opacity-50">
				<EyeOff size={15} /> Ignore <kbd class="text-[10px] bg-black/20 px-1 rounded">I</kbd>
			</button>
			<button onclick={deleteCluster} disabled={acting}
				class="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600 hover:bg-red-500 text-white font-medium disabled:opacity-50">
				<Trash2 size={15} /> Delete <kbd class="text-[10px] bg-black/20 px-1 rounded">D</kbd>
			</button>
			<button onclick={skip} disabled={acting}
				class="flex items-center gap-2 px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-50">
				<SkipForward size={15} /> Skip <kbd class="text-[10px] bg-black/20 px-1 rounded">S</kbd>
			</button>
		</div>
	{/if}
</div>

<!-- Photo lightbox -->
{#if previewPhotoId !== null}
<div class="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true">
	<button class="absolute inset-0 bg-black/80" aria-label="Close" onclick={() => previewPhotoId = null}></button>
	<div class="relative max-w-4xl max-h-[90vh] flex items-center justify-center">
		<img src="{BASE}/media/thumbnail/{previewPhotoId}?size=xxl" alt="" class="max-w-full max-h-[90vh] rounded-lg shadow-2xl object-contain" />
		<button onclick={() => previewPhotoId = null} class="absolute top-2 right-2 p-1.5 rounded-full bg-black/60 hover:bg-black/80 text-white"><X size={16} /></button>
	</div>
</div>
{/if}

<!-- New person dialog -->
{#if newPersonOpen}
<div class="fixed inset-0 z-50 flex items-center justify-center">
	<button class="absolute inset-0 bg-black/70" aria-label="Close" onclick={() => newPersonOpen = false}></button>
	<div class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-72 flex flex-col" role="dialog" aria-modal="true">
		<div class="p-4 border-b border-zinc-800 flex items-center gap-2">
			<UserPlus size={14} class="text-amber-400" />
			<span class="text-sm font-medium text-zinc-200">New person</span>
			<button onclick={() => newPersonOpen = false} class="ml-auto p-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={14} /></button>
		</div>
		<div class="p-4 flex flex-col gap-3">
			<!-- svelte-ignore a11y_autofocus -->
			<input type="text" bind:value={newPersonName} placeholder="Full name…" autofocus
				class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500"
				onkeydown={(e) => { if (e.key === 'Enter') createAndAssign(); if (e.key === 'Escape') newPersonOpen = false; }} />
			<button onclick={createAndAssign} disabled={!newPersonName.trim() || creatingPerson}
				class="w-full py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-40 flex items-center justify-center gap-2">
				{creatingPerson ? 'Creating…' : 'Create & assign cluster'}
			</button>
		</div>
	</div>
</div>
{/if}

<!-- People picker -->
{#if pickerOpen}
<div class="fixed inset-0 z-50 flex items-center justify-center">
	<button class="absolute inset-0 bg-black/70" aria-label="Close" onclick={() => pickerOpen = false}></button>
	<div class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-80 flex flex-col max-h-[70vh]" role="dialog" aria-modal="true">
		<div class="p-3 border-b border-zinc-800 flex items-center gap-2">
			<div class="relative flex-1">
				<Search size={13} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
				<!-- svelte-ignore a11y_autofocus -->
				<input type="text" bind:value={pickerSearch} placeholder="Search people…" autofocus
					class="w-full bg-zinc-800 border border-zinc-700 rounded pl-7 pr-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500" />
			</div>
			<button onclick={() => pickerOpen = false} class="p-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={16} /></button>
		</div>
		<div class="overflow-y-auto flex-1 p-1">
			{#if filteredPeople.length === 0}
				<p class="text-xs text-zinc-500 px-3 py-3">No people found</p>
			{:else}
				{#each filteredPeople as person (person.id)}
					<button onclick={() => assignTo(person.id)}
						class="w-full flex items-center gap-2 px-3 py-1.5 rounded hover:bg-amber-500/15 text-left">
						<span class="flex-1 text-sm text-zinc-200 truncate">{person.name}</span>
						<span class="text-[10px] text-zinc-500">{person.face_count}</span>
					</button>
				{/each}
			{/if}
		</div>
	</div>
</div>
{/if}

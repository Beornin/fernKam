<script lang="ts">
	import { api, type FaceWithSuggestions, type PersonOut } from '$lib/api';
	import { ScanFace, Check, X, User, ChevronRight, RefreshCw, Scan, AlertCircle, AlertTriangle, Plus, CheckSquare, Square, Filter } from '@lucide/svelte';
	import { goto } from '$app/navigation';

	let items = $state<FaceWithSuggestions[]>([]);
	let people = $state<PersonOut[]>([]);
	let loading = $state(true);
	let scanning = $state(false);
	let scanProgress = $state<{ processed: number; total: number; faces: number } | null>(null);
	let unscannedCount = $state(0);
	let unassignedCount = $state(0);
	let totalSuggestions = $state(0);
	let page = $state(0);
	let hasMore = $state(false);
	const PAGE = 200;
	let offset = $derived(page * PAGE);
	let totalPages = $derived(Math.max(1, Math.ceil(totalSuggestions / PAGE)));
	let jumpToInput = $state('');

	// Background face-scan task state
	let faceScanTaskId = $state<string | null>(null);
	let faceScanMessage = $state<string>('');
	let faceScanRunning = $state(false);

	// Filter + sort state (sort/status_filter = server-side; hideConflicts/minScore = client-side)
	let statusFilter = $state<'all' | 'suggested' | 'unconfirmed'>('all');
	let sortBy = $state<'score_desc' | 'score_asc' | 'newest' | 'status'>('score_desc');
	let hideConflicts = $state(false);
	let minScore = $state(0);

	let filteredItems = $derived(
		items.filter(item => {
			if (hideConflicts && item.suggestions.length > 0) {
				const actionable = item.suggestions.filter(s => !s.conflict);
				if (actionable.length === 0) return false;
			}
			if (minScore > 0) {
				const topScore = item.suggestions[0]?.score ?? item.face.score ?? 0;
				if (topScore * 100 < minScore) return false;
			}
			return true;
		})
	);

	let buildingCentroids = $state(false);
	let centroidsMessage = $state('');

	async function buildCentroids() {
		buildingCentroids = true;
		centroidsMessage = '';
		try {
			const r = await api.faces.buildCentroids();
			centroidsMessage = `Centroids built for ${r.updated} people`;
		} catch {
			centroidsMessage = 'Build failed';
		} finally {
			buildingCentroids = false;
			setTimeout(() => { centroidsMessage = ''; }, 4000);
		}
	}

	// multi-select state
	let selectedIds = $state<Set<string>>(new Set());
	let bulkAssigning = $state(false);
	let bulkPickerOpen = $state(false);
	let bulkPickerSearch = $state('');
	let bulkPickerCreating = $state(false);
	let bulkPickerNewName = $state('');
	let bulkPickerShowCreate = $state(false);
	let bulkFilteredPeople = $derived(
		bulkPickerSearch.trim()
			? people.filter(p => p.name.toLowerCase().includes(bulkPickerSearch.toLowerCase()))
			: people
	);

	// person-picker overlay state (single face)
	let pickerFaceId = $state<string | null>(null);
	let pickerSearch = $state('');
	let pickerCreating = $state(false);
	let pickerNewName = $state('');
	let pickerShowCreate = $state(false);
	let filteredPeople = $derived(
		pickerSearch.trim()
			? people.filter(p => p.name.toLowerCase().includes(pickerSearch.toLowerCase()))
			: people
	);

	async function loadCounts() {
		const [u, s, t] = await Promise.all([
			api.faces.unassignedCount(),
			api.photos.unscannedCount(),
			api.faces.suggestionsCount({ status_filter: statusFilter }),
		]);
		unassignedCount = u.count;
		unscannedCount = s.count;
		totalSuggestions = t.count;
	}

	async function loadPage(p = page) {
		loading = true;
		try {
			const batch = await api.faces.suggestions({ limit: PAGE, offset: p * PAGE, sort: sortBy, status_filter: statusFilter });
			hasMore = batch.length >= PAGE;
			items = batch;
			page = p;
		} finally {
			loading = false;
		}
	}

	function setFilter(sf: typeof statusFilter) {
		statusFilter = sf;
		loadCounts();
		loadPage(0);
	}

	function setSort(s: typeof sortBy) {
		sortBy = s;
		loadPage(0);
	}

	function jumpToPage(target: number) {
		const t = Math.max(0, Math.min(totalPages - 1, target));
		if (t !== page) loadPage(t);
	}

	async function startFaceScan() {
		const r = await api.sync.scanFaces({});
		faceScanTaskId = r.task_id;
		faceScanRunning = !!r.task_id;
		faceScanMessage = r.message;
		// Begin polling
		if (faceScanRunning) pollFaceScan();
	}

	async function cancelFaceScan() {
		if (!faceScanTaskId) return;
		await api.sync.cancelTask(faceScanTaskId);
	}

	async function pollFaceScan() {
		if (!faceScanTaskId) return;
		try {
			const r = await api.sync.tasks();
			const t = r.tasks.find(x => x.id === faceScanTaskId);
			if (!t) return;
			faceScanMessage = t.message;
			if (t.status === 'running') {
				setTimeout(pollFaceScan, 2000);
			} else {
				faceScanRunning = false;
				// Refresh counts/items so the user sees fresh suggestions
				await loadCounts();
				await loadPage(0);
			}
		} catch (_e) {
			setTimeout(pollFaceScan, 5000);
		}
	}

	async function loadPeople() {
		people = await api.people.list({ limit: 500 });
	}

	async function batchAssign() {
		scanning = true;
		try {
			const { task_id } = await api.faces.autoConfirmAll();
			// Poll task until complete
			while (true) {
				await new Promise(r => setTimeout(r, 1500));
				const { tasks } = await api.sync.tasks();
				const task = tasks.find(t => t.id === task_id);
				if (!task || task.status === 'completed' || task.status === 'failed') break;
			}
			await loadCounts();
			await loadPage(0);
		} finally {
			scanning = false;
		}
	}

	function toggleSelect(faceId: string, e: MouseEvent) {
		e.stopPropagation();
		const next = new Set(selectedIds);
		if (next.has(faceId)) next.delete(faceId);
		else next.add(faceId);
		selectedIds = next;
	}

	function clearSelection() { selectedIds = new Set(); }

	function selectAll() {
		selectedIds = new Set(filteredItems.map(i => i.face.id));
	}

	async function bulkIgnore() {
		bulkAssigning = true;
		try {
			await api.faces.batchAssign({ face_ids: [...selectedIds], person_tag_id: null, status: 'ignored' });
			items = items.filter(i => !selectedIds.has(i.face.id));
			unassignedCount = Math.max(0, unassignedCount - selectedIds.size);
			selectedIds = new Set();
		} finally { bulkAssigning = false; }
	}

	async function bulkAssignToPerson(personId: number) {
		bulkAssigning = true;
		try {
			await api.faces.batchAssign({ face_ids: [...selectedIds], person_tag_id: personId, status: 'confirmed' });
			items = items.filter(i => !selectedIds.has(i.face.id));
			unassignedCount = Math.max(0, unassignedCount - selectedIds.size);
			selectedIds = new Set();
			bulkPickerOpen = false;
		} finally { bulkAssigning = false; }
	}

	async function bulkCreateAndAssign() {
		if (!bulkPickerNewName.trim()) return;
		bulkPickerCreating = true;
		try {
			const p = await api.people.create(bulkPickerNewName.trim());
			people = [...people, p];
			await bulkAssignToPerson(p.id);
			bulkPickerNewName = '';
			bulkPickerShowCreate = false;
		} finally { bulkPickerCreating = false; }
	}

	async function acceptAllSuggested() {
		// Respect active filters; skip items where the top suggestion conflicts
		const withSuggestions = filteredItems.filter(i =>
			i.suggestions.length > 0 && !i.suggestions[0].conflict
		);
		if (!withSuggestions.length) return;
		bulkAssigning = true;
		try {
			// Group by the highest suggestion's person_id
			const groups = new Map<number, string[]>();
			for (const i of withSuggestions) {
				const top = i.suggestions[0];
				if (!groups.has(top.person_id)) groups.set(top.person_id, []);
				groups.get(top.person_id)!.push(i.face.id);
			}
			await Promise.all([...groups.entries()].map(([personId, ids]) =>
				api.faces.batchAssign({ face_ids: ids, person_tag_id: personId, status: 'confirmed' })
			));
			await loadCounts();
			await loadPage(page);
			selectedIds = new Set();
		} finally { bulkAssigning = false; }
	}

	let suggestedOnPageCount = $derived(
		filteredItems.filter(i => i.suggestions.length > 0 && !i.suggestions[0].conflict).length
	);

	function markSiblingConflicts(confirmedItem: FaceWithSuggestions, personId: number) {
		items = items.map(i => {
			if (i.face.photo_id !== confirmedItem.face.photo_id) return i;
			return {
				...i,
				suggestions: i.suggestions.map(s =>
					s.person_id === personId ? { ...s, conflict: true } : s
				)
			};
		});
	}

	async function confirm(item: FaceWithSuggestions, personId: number) {
		try {
			const res = await fetch(`/api/faces/${item.face.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ person_tag_id: personId, status: 'confirmed' }) });
			if (!res.ok) {
				if (res.status === 409) {
					// Person already confirmed in this photo — remove this face, mark siblings
					items = items.filter(i => i.face.id !== item.face.id);
					markSiblingConflicts(item, personId);
					return;
				}
				return;
			}
		} catch { return; }
		items = items.filter(i => i.face.id !== item.face.id);
		markSiblingConflicts(item, personId);
		unassignedCount = Math.max(0, unassignedCount - 1);
		const p = people.find(x => x.id === personId);
		if (p) people = people.map(x => x.id === personId ? { ...x, face_count: x.face_count + 1 } : x);
	}

	async function ignore(item: FaceWithSuggestions) {
		await api.faces.update(item.face.id, { status: 'ignored', person_tag_id: null });
		items = items.filter(i => i.face.id !== item.face.id);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	async function unIgnore(item: FaceWithSuggestions) {
		await api.faces.update(item.face.id, { status: 'unconfirmed', person_tag_id: null });
		items = items.map(i => i.face.id === item.face.id
			? { ...i, face: { ...i.face, status: 'unconfirmed', person_tag_id: null, person_name: null } }
			: i);
	}

	async function confirmSuggested(item: FaceWithSuggestions) {
		if (!item.face.person_tag_id) return;
		const personId = item.face.person_tag_id;
		try {
			const res = await fetch(`/api/faces/${item.face.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ person_tag_id: personId, status: 'confirmed' }) });
			if (!res.ok) {
				if (res.status === 409) {
					// Sweep already confirmed this person in photo — just remove the card
					items = items.filter(i => i.face.id !== item.face.id);
					markSiblingConflicts(item, personId);
					return;
				}
				return;
			}
		} catch { return; }
		items = items.filter(i => i.face.id !== item.face.id);
		markSiblingConflicts(item, personId);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	async function rejectSuggestion(item: FaceWithSuggestions) {
		await api.faces.update(item.face.id, { person_tag_id: null, status: 'unconfirmed' });
		// Update the item in-place so it becomes a plain unconfirmed face
		items = items.map(i => i.face.id === item.face.id
			? { ...i, face: { ...i.face, person_tag_id: null, person_name: null, status: 'unconfirmed' } }
			: i);
	}

	async function deleteFace(item: FaceWithSuggestions) {
		await api.faces.delete(item.face.id);
		items = items.filter(i => i.face.id !== item.face.id);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	function openPicker(faceId: string) {
		pickerFaceId = faceId;
		pickerSearch = '';
		pickerNewName = '';
		pickerShowCreate = false;
	}

	async function createAndAssign() {
		if (!pickerNewName.trim() || !pickerFaceId) return;
		pickerCreating = true;
		try {
			const p = await api.people.create(pickerNewName.trim());
			people = [...people, p];
			await assignFromPicker(p.id);
			pickerShowCreate = false;
			pickerNewName = '';
		} finally {
			pickerCreating = false;
		}
	}

	async function assignFromPicker(personId: number) {
		if (!pickerFaceId) return;
		const item = items.find(i => i.face.id === pickerFaceId);
		if (item) await confirm(item, personId);
		pickerFaceId = null;
	}

	function scoreColor(score: number) {
		if (score >= 0.75) return 'bg-emerald-500';
		if (score >= 0.55) return 'bg-amber-400';
		return 'bg-zinc-500';
	}

	function scoreLabel(score: number) {
		if (score >= 0.75) return 'Strong match';
		if (score >= 0.55) return 'Possible match';
		return 'Weak match';
	}

	$effect(() => {
		loadCounts();
		loadPage(0);
		loadPeople();
		// Resume display of any in-flight face-scan task
		(async () => {
			try {
				const r = await api.sync.tasks();
				const t = r.tasks.find(x => x.task_type === 'scan_faces' && x.status === 'running');
				if (t) {
					faceScanTaskId = t.id;
					faceScanRunning = true;
					faceScanMessage = t.message;
					pollFaceScan();
				}
			} catch {}
		})();
	});

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && selectedIds.size > 0) clearSelection();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- bulk person picker overlay -->
{#if bulkPickerOpen}
<div class="fixed inset-0 z-50 flex items-center justify-center">
	<button class="absolute inset-0 bg-black/70" aria-label="Close" onclick={() => bulkPickerOpen = false}></button>
	<div class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-72 flex flex-col max-h-[70vh]" role="dialog" aria-modal="true">
		<div class="p-3 border-b border-zinc-800 flex items-center gap-2">
			<span class="text-xs text-amber-400 font-medium shrink-0">{selectedIds.size} faces</span>
			<input
				type="text"
				bind:value={bulkPickerSearch}
				placeholder="Assign all to…"
				class="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500"
			/>
			<button onclick={() => bulkPickerOpen = false} class="p-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={16} /></button>
		</div>
		<div class="overflow-y-auto flex-1 p-1">
			{#if bulkPickerShowCreate}
				<div class="px-3 py-2 flex flex-col gap-2">
					<input
						type="text"
						bind:value={bulkPickerNewName}
						placeholder="New person name…"
						class="w-full bg-zinc-800 border border-zinc-600 rounded px-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500"
						onkeydown={(e) => e.key === 'Enter' && bulkCreateAndAssign()}
					/>
					<div class="flex gap-2">
						<button
							onclick={bulkCreateAndAssign}
							disabled={bulkPickerCreating || !bulkPickerNewName.trim()}
							class="flex-1 py-1 rounded bg-amber-600 hover:bg-amber-500 text-xs text-white disabled:opacity-50"
						>{bulkPickerCreating ? 'Creating…' : 'Create & Assign All'}</button>
						<button onclick={() => bulkPickerShowCreate = false} class="px-3 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-xs text-zinc-300">Cancel</button>
					</div>
				</div>
			{:else}
				<button
					onclick={() => { bulkPickerShowCreate = true; bulkPickerSearch = ''; }}
					class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left border-b border-zinc-800 mb-1"
				>
					<div class="w-6 h-6 rounded-full bg-amber-700/50 flex items-center justify-center text-amber-400 shrink-0"><Plus size={12} /></div>
					<span class="text-sm text-amber-400">New person…</span>
				</button>
				{#each bulkFilteredPeople as person (person.id)}
					<button
						onclick={() => bulkAssignToPerson(person.id)}
						class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left"
					>
						<div class="w-6 h-6 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0"><User size={12} /></div>
						<span class="text-sm text-zinc-200 flex-1 truncate">{person.name}</span>
						<span class="text-xs text-zinc-500">{person.face_count}</span>
					</button>
				{/each}
			{/if}
		</div>
	</div>
</div>
{/if}

<!-- person picker overlay -->
{#if pickerFaceId}
<div class="fixed inset-0 z-50 flex items-center justify-center">
	<button
		class="absolute inset-0 bg-black/70"
		aria-label="Close dialog"
		onclick={() => pickerFaceId = null}
	></button>
	<div
		class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-72 flex flex-col max-h-[70vh]"
		role="dialog" aria-modal="true"
	>
		<div class="p-3 border-b border-zinc-800 flex items-center gap-2">
			<input
				type="text"
				bind:value={pickerSearch}
				placeholder="Search people…"
				class="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-emerald-500"
			/>
			<button onclick={() => pickerFaceId = null} class="p-1 rounded hover:bg-zinc-700 text-zinc-400">
				<X size={16} />
			</button>
		</div>
		<div class="overflow-y-auto flex-1 p-1">
			{#if pickerShowCreate}
				<div class="px-3 py-2 flex flex-col gap-2">
					<input
						type="text"
						bind:value={pickerNewName}
						placeholder="New person name…"
						class="w-full bg-zinc-800 border border-zinc-600 rounded px-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-emerald-500"
						onkeydown={(e) => e.key === 'Enter' && createAndAssign()}
					/>
					<div class="flex gap-2">
						<button
							onclick={createAndAssign}
							disabled={pickerCreating || !pickerNewName.trim()}
							class="flex-1 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-xs text-white disabled:opacity-50"
						>{pickerCreating ? 'Creating…' : 'Create & Assign'}</button>
						<button
							onclick={() => pickerShowCreate = false}
							class="px-3 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-xs text-zinc-300"
						>Cancel</button>
					</div>
				</div>
			{:else}
				<button
					onclick={() => { pickerShowCreate = true; pickerSearch = ''; }}
					class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left border-b border-zinc-800 mb-1"
				>
					<div class="w-6 h-6 rounded-full bg-emerald-700/50 flex items-center justify-center text-emerald-400 shrink-0">
						<Plus size={12} />
					</div>
					<span class="text-sm text-emerald-400">New person…</span>
				</button>
				{#if filteredPeople.length === 0}
					<p class="text-xs text-zinc-500 px-3 py-3">No people found</p>
				{:else}
					{#each filteredPeople as person (person.id)}
						<button
							onclick={() => assignFromPicker(person.id)}
							class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left"
						>
							<div class="w-6 h-6 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0">
								<User size={12} />
							</div>
							<span class="text-sm text-zinc-200 flex-1 truncate">{person.name}</span>
							<span class="text-xs text-zinc-500">{person.face_count}</span>
						</button>
					{/each}
				{/if}
			{/if}
		</div>
	</div>
</div>
{/if}

<div class="flex flex-col h-full bg-zinc-950">
	<!-- Header -->
	<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
		<ScanFace size={20} class="text-emerald-400" />
		<h1 class="text-base font-semibold text-zinc-100">Face Review</h1>
		<div class="flex items-center gap-3 ml-4">
			<span class="text-xs text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded-full">
				{unassignedCount} to review
			</span>
			{#if unscannedCount > 0}
				<span class="text-xs text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded-full flex items-center gap-1">
					<AlertCircle size={11} />
					{unscannedCount} unscanned photos
				</span>
			{/if}
		</div>
		<div class="ml-auto flex items-center gap-2">
			<button
				onclick={() => loadPage(0)}
				disabled={loading}
				class="text-xs px-2 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1 disabled:opacity-50"
			>
				<RefreshCw size={12} class={loading ? 'animate-spin' : ''} />
				Refresh
			</button>
			{#if faceScanRunning}
				<button
					onclick={cancelFaceScan}
					class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-red-700 text-zinc-300 hover:text-white flex items-center gap-1"
					title={faceScanMessage}
				>
					<X size={12} />
					Cancel scan
				</button>
				<span class="text-xs text-zinc-400 max-w-xs truncate" title={faceScanMessage}>{faceScanMessage}</span>
			{:else if unscannedCount > 0}
				<button
					onclick={startFaceScan}
					class="text-xs px-3 py-1.5 rounded bg-amber-700 hover:bg-amber-600 text-white flex items-center gap-1"
					title={`Detect faces on ${unscannedCount} unscanned photos`}
				>
					<ScanFace size={12} />
					Scan {unscannedCount}
				</button>
			{/if}
			{#if suggestedOnPageCount > 0}
				<button
					onclick={acceptAllSuggested}
					disabled={bulkAssigning}
					class="text-xs px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white flex items-center gap-1 disabled:opacity-50"
					title="Confirm all {suggestedOnPageCount} suggested faces on this page"
				>
					<Check size={12} />
					Accept {suggestedOnPageCount} suggested
				</button>
			{/if}
			<button
				onclick={buildCentroids}
				disabled={buildingCentroids}
				class="text-xs px-3 py-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 flex items-center gap-1 disabled:opacity-50"
				title="Recompute per-person centroid embeddings from all confirmed faces"
			>
				<RefreshCw size={12} class={buildingCentroids ? 'animate-spin' : ''} />
				{buildingCentroids ? 'Building…' : 'Build Centroids'}
			</button>
			{#if centroidsMessage}
				<span class="text-xs text-emerald-400">{centroidsMessage}</span>
			{/if}
			<button
				onclick={batchAssign}
				disabled={scanning || unassignedCount === 0}
				class="text-xs px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1 disabled:opacity-50"
				title={unassignedCount === 0 ? 'No faces to review' : `Auto-assign ${unassignedCount} faces`}
			>
				<Scan size={12} class={scanning ? 'animate-spin' : ''} />
				{scanning ? 'Assigning…' : `Batch Assign ${unassignedCount}`}
			</button>
		</div>
	</div>


	<!-- Filter / sort bar -->
	<div class="shrink-0 px-5 py-2 border-b border-zinc-800 bg-zinc-950 flex items-center gap-2 flex-wrap text-xs">
		<Filter size={12} class="text-zinc-500 shrink-0" />
		<span class="text-zinc-500">Show:</span>
		{#each [['all','All'],['suggested','Suggested'],['unconfirmed','Unconfirmed']] as [val, label]}
			<button
				onclick={() => setFilter(val as 'all' | 'suggested' | 'unconfirmed')}
				class="px-2 py-0.5 rounded transition-colors {statusFilter === val ? 'bg-emerald-700 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'}"
			>{label}</button>
		{/each}
		<div class="w-px h-4 bg-zinc-700 mx-1"></div>
		<span class="text-zinc-500">Sort:</span>
		{#each [['score_desc','Best ↓'],['score_asc','Weakest ↑'],['status','Status first'],['newest','Newest']] as [sval, slabel]}
			<button
				onclick={() => setSort(sval as 'score_desc' | 'score_asc' | 'status' | 'newest')}
				class="px-2 py-0.5 rounded transition-colors {sortBy === sval ? 'bg-emerald-700 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'}"
			>{slabel}</button>
		{/each}
		<div class="w-px h-4 bg-zinc-700 mx-1"></div>
		<span class="text-zinc-500">Min:</span>
		{#each [[0,'Any'],[50,'≥50%'],[65,'≥65%'],[80,'≥80%']] as [mval, mlabel]}
			<button
				onclick={() => { minScore = mval as number; }}
				class="px-2 py-0.5 rounded transition-colors {minScore === mval ? 'bg-emerald-700 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'}"
			>{mlabel}</button>
		{/each}
		<div class="w-px h-4 bg-zinc-700 mx-1"></div>
		<button
			onclick={() => { hideConflicts = !hideConflicts; }}
			class="flex items-center gap-1 px-2 py-0.5 rounded border transition-colors {hideConflicts ? 'bg-amber-900/40 border-amber-700/50 text-amber-300' : 'bg-zinc-800 border-transparent text-zinc-400 hover:text-zinc-200'}"
			title="Hide faces where every suggestion is already confirmed in that photo"
		>
			{#if hideConflicts}<Check size={11} />{/if}
			Hide all-conflicts
		</button>
		{#if filteredItems.length !== items.length}
			<span class="ml-auto text-zinc-500">Showing {filteredItems.length} of {items.length}</span>
		{/if}
	</div>

	<!-- Content -->
	<div class="flex-1 overflow-y-auto p-5">
		{#if loading && items.length === 0}
			<div class="flex flex-col items-center justify-center h-48 gap-3 text-zinc-500">
				<div class="w-8 h-8 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
				<p class="text-sm">Loading face suggestions…</p>
			</div>
		{:else if items.length === 0}
			<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-600">
				<ScanFace size={48} />
				<p class="text-sm font-medium text-zinc-400">No faces to review</p>
				{#if unscannedCount > 0}
					<p class="text-xs text-zinc-500">Run a scan to detect faces in new photos</p>
				{:else if people.length === 0}
					<p class="text-xs text-zinc-500">Add people first, then scan photos to get suggestions</p>
				{:else}
					<p class="text-xs text-zinc-500">All detected faces have been reviewed</p>
				{/if}
			</div>
		{:else}
			{#if selectedIds.size > 0}
				<div class="mb-3 flex items-center gap-2 text-xs text-zinc-300">
					<button onclick={selectAll} class="hover:text-amber-400 flex items-center gap-1">
						<CheckSquare size={13} /> Select all {filteredItems.length}
					</button>
					<span class="text-zinc-600">·</span>
					<button onclick={clearSelection} class="hover:text-zinc-100 flex items-center gap-1">
						<Square size={13} /> Clear ({selectedIds.size})
					</button>
					<span class="text-zinc-500 ml-1 italic">Ctrl+click to toggle · Esc to cancel</span>
				</div>
			{/if}
			<div class="grid gap-4" style="grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))">
				{#each filteredItems as item (item.face.id)}
					{@const isSelected = selectedIds.has(item.face.id)}
					<div class="bg-zinc-900 border rounded-xl overflow-hidden flex flex-col transition-all {isSelected ? 'border-amber-500 ring-2 ring-amber-500/40' : 'border-zinc-800'}">
						<!-- Face image -->
						<div
							role="button"
							tabindex="0"
							class="relative bg-zinc-800 aspect-square cursor-pointer group"
							onclick={(e) => { if (e.ctrlKey || e.metaKey) toggleSelect(item.face.id, e); }}
							ondblclick={(e) => { if (!e.ctrlKey && !e.metaKey) goto(`/photos?photo_id=${item.face.photo_id}&back=${encodeURIComponent(window.location.href)}`); }}
							onkeydown={(e) => e.key === 'Enter' && goto(`/photos?photo_id=${item.face.photo_id}&back=${encodeURIComponent(window.location.href)}`)}
							title="Ctrl+click to select · double-click to open photo"
						>
							<img
								src="/media/face/{item.face.id}?size=300"
								alt="Detected face"
								class="w-full h-full object-cover {isSelected ? 'opacity-75' : ''}"
								loading="lazy"
							/>
							<!-- Checkbox: always visible when selected, hover-only otherwise -->
							<button
								onclick={(e) => toggleSelect(item.face.id, e)}
								class="absolute top-1.5 left-1.5 w-5 h-5 rounded flex items-center justify-center transition-opacity {isSelected ? 'opacity-100 bg-amber-500 text-white' : 'opacity-0 group-hover:opacity-80 bg-black/50 text-zinc-300'}"
								title="Select"
							>
								{#if isSelected}<Check size={12} />{:else}<Square size={12} />{/if}
							</button>
							<button
								onclick={(e) => { e.stopPropagation(); goto(`/photos?photo_id=${item.face.photo_id}&back=${encodeURIComponent(window.location.href)}`); }}
								class="absolute top-1.5 right-1.5 p-1 rounded bg-black/50 hover:bg-black/70 text-white opacity-0 group-hover:opacity-100 transition-opacity"
								title="Open source photo"
							>
								<ChevronRight size={12} />
							</button>
						</div>

						<!-- Suggestions -->
						<div class="flex-1 p-2.5 flex flex-col gap-1.5">
							{#if item.face.status === 'suggested' && item.face.person_tag_id && item.suggestions.length > 0}
								<!-- Auto-suggested: one-click confirm or reject -->
								<button
									onclick={() => confirmSuggested(item)}
									class="group/sug w-full flex items-center gap-2 px-2 py-1.5 rounded-lg bg-amber-900/30 hover:bg-emerald-900/40 border border-amber-700/40 hover:border-emerald-700 transition-colors text-left"
									title="{scoreLabel(item.suggestions[0].score)}: {Math.round(item.suggestions[0].score * 100)}%"
								>
									<div class="w-5 h-5 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0">
										<User size={10} />
									</div>
									<span class="flex-1 text-xs text-zinc-200 truncate">{item.face.person_name ?? '?'}</span>
									<div class="flex items-center gap-1 shrink-0">
										<div class="w-12 h-1 rounded-full bg-zinc-700 overflow-hidden">
											<div class="h-full {scoreColor(item.suggestions[0].score)}" style="width: {Math.round(item.suggestions[0].score * 100)}%"></div>
										</div>
										<span class="text-[10px] text-zinc-500 w-7 text-right">{Math.round(item.suggestions[0].score * 100)}%</span>
										<Check size={12} class="text-amber-400 shrink-0" />
									</div>
								</button>
								<button
									onclick={() => rejectSuggestion(item)}
									class="w-full text-[11px] text-zinc-500 hover:text-red-400 py-0.5 flex items-center justify-center gap-1 hover:bg-zinc-800 rounded transition-colors"
								>
									<X size={10} /> Not {item.face.person_name}
								</button>
							{:else if item.suggestions.length === 0}
								<p class="text-[11px] text-zinc-500 text-center py-1">No matches — assign manually</p>
							{:else}
								{#each item.suggestions as sug (sug.person_id)}
									<button
										onclick={() => !sug.conflict && confirm(item, sug.person_id)}
										disabled={sug.conflict}
										class="group/sug w-full flex items-center gap-2 px-2 py-1.5 rounded-lg border transition-colors text-left {sug.conflict ? 'bg-amber-950/30 border-amber-700/40 cursor-not-allowed opacity-70' : 'bg-zinc-800 hover:bg-emerald-900/40 hover:border-emerald-700 border-transparent'}"
										title={sug.conflict ? `⚠ ${sug.person_name} already confirmed in this photo` : `${scoreLabel(sug.score)}: ${Math.round(sug.score * 100)}%`}
									>
										<div class="w-5 h-5 rounded-full bg-zinc-700 flex items-center justify-center shrink-0 {sug.conflict ? 'text-amber-400' : 'text-zinc-400'}">
											{#if sug.conflict}<AlertTriangle size={10} />{:else}<User size={10} />{/if}
										</div>
										<span class="flex-1 text-xs truncate {sug.conflict ? 'text-amber-300' : 'text-zinc-200'}">{sug.person_name ?? '?'}</span>
										<div class="flex items-center gap-1 shrink-0">
											{#if sug.conflict}
												<span class="text-[10px] text-amber-500 font-medium">in photo</span>
											{:else}
												<div class="w-12 h-1 rounded-full bg-zinc-700 overflow-hidden">
													<div class="h-full {scoreColor(sug.score)}" style="width: {Math.round(sug.score * 100)}%"></div>
												</div>
												<span class="text-[10px] text-zinc-500 w-7 text-right">{Math.round(sug.score * 100)}%</span>
												<Check size={12} class="text-zinc-600 group-hover/sug:text-emerald-400 transition-colors ml-0.5" />
											{/if}
										</div>
									</button>
								{/each}
							{/if}

							<!-- Manual assign -->
							<button
								onclick={() => openPicker(item.face.id)}
								class="w-full text-[11px] text-zinc-500 hover:text-zinc-300 py-1 flex items-center justify-center gap-1 hover:bg-zinc-800 rounded transition-colors"
							>
								<User size={11} /> Assign to someone…
							</button>
						</div>

						<!-- Action row -->
						<div class="flex border-t border-zinc-800">
							<button
								onclick={() => ignore(item)}
								class="flex-1 py-2 text-[11px] text-zinc-500 hover:text-amber-400 hover:bg-amber-400/5 transition-colors flex items-center justify-center gap-1"
								title="Add to ignored pool — similar faces will be auto-ignored on next scan"
							>
								<X size={12} /> Ignore
							</button>
							<div class="w-px bg-zinc-800"></div>
							<button
								onclick={() => deleteFace(item)}
								class="flex-1 py-2 text-[11px] text-zinc-500 hover:text-red-400 hover:bg-red-400/5 transition-colors flex items-center justify-center gap-1"
								title="Delete face record permanently"
							>
								<X size={12} /> Delete
							</button>
						</div>
					</div>
				{/each}
			</div>

			<!-- Pagination -->
			{#if page > 0 || hasMore || totalSuggestions > PAGE}
				<div class="mt-6 flex items-center justify-center gap-2 flex-wrap">
					<button
						onclick={() => loadPage(0)}
						disabled={page === 0 || loading}
						class="px-2.5 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed"
					>« First</button>
					<button
						onclick={() => loadPage(page - 1)}
						disabled={page === 0 || loading}
						class="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-sm text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed"
					>← Prev</button>

					<div class="flex items-center gap-1.5 px-2">
						<span class="text-xs text-zinc-500">Page</span>
						<input
							type="number"
							min="1"
							max={totalPages}
							value={page + 1}
							onkeydown={(e) => {
								if (e.key === 'Enter') {
									const n = parseInt((e.currentTarget as HTMLInputElement).value || '1', 10);
									if (!isNaN(n)) jumpToPage(n - 1);
								}
							}}
							onchange={(e) => {
								const n = parseInt((e.currentTarget as HTMLInputElement).value || '1', 10);
								if (!isNaN(n)) jumpToPage(n - 1);
							}}
							class="w-16 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 text-center focus:outline-none focus:border-amber-500"
						/>
						<span class="text-xs text-zinc-500">of {totalPages}</span>
					</div>

					<button
						onclick={() => loadPage(page + 1)}
						disabled={!hasMore || loading}
						class="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-sm text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed"
					>Next →</button>
					<button
						onclick={() => loadPage(totalPages - 1)}
						disabled={page >= totalPages - 1 || loading}
						class="px-2.5 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed"
					>Last »</button>

					<span class="ml-2 text-xs text-zinc-500">{totalSuggestions} faces</span>
				</div>
			{/if}
		{/if}
	</div>
</div>

<!-- Floating selection action bar -->
{#if selectedIds.size > 0}
<div class="fixed bottom-10 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 px-5 py-3 bg-zinc-900 border border-amber-500/40 rounded-2xl shadow-2xl">
	<span class="text-sm font-medium text-amber-400">{selectedIds.size} selected</span>
	<div class="w-px h-5 bg-zinc-700"></div>
	<button
		onclick={() => { bulkPickerOpen = true; bulkPickerSearch = ''; bulkPickerShowCreate = false; }}
		disabled={bulkAssigning}
		class="text-xs px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white flex items-center gap-1.5 disabled:opacity-50"
	>
		<User size={12} /> Assign to…
	</button>
	<button
		onclick={bulkIgnore}
		disabled={bulkAssigning}
		class="text-xs px-3 py-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 flex items-center gap-1.5 disabled:opacity-50"
	>
		<X size={12} /> Ignore all
	</button>
	<button onclick={clearSelection} class="text-xs px-2 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-500 flex items-center gap-1">
		<X size={11} />
	</button>
</div>
{/if}

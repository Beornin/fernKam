<script lang="ts">
	import { api, type FaceWithSuggestions, type PersonOut } from '$lib/api';
	import { ScanFace, Check, X, User, ChevronRight, RefreshCw, Scan, AlertCircle } from '@lucide/svelte';
	import { goto } from '$app/navigation';

	let items = $state<FaceWithSuggestions[]>([]);
	let people = $state<PersonOut[]>([]);
	let loading = $state(true);
	let scanning = $state(false);
	let scanProgress = $state<{ processed: number; total: number; faces: number } | null>(null);
	let unscannedCount = $state(0);
	let unassignedCount = $state(0);
	let offset = $state(0);
	const PAGE = 40;

	// person-picker overlay state
	let pickerFaceId = $state<string | null>(null);
	let pickerSearch = $state('');
	let filteredPeople = $derived(
		pickerSearch.trim()
			? people.filter(p => p.name.toLowerCase().includes(pickerSearch.toLowerCase()))
			: people
	);

	async function loadCounts() {
		const [u, s] = await Promise.all([
			api.faces.unassignedCount(),
			api.photos.unscannedCount(),
		]);
		unassignedCount = u.count;
		unscannedCount = s.count;
	}

	async function loadItems(reset = false) {
		if (reset) { offset = 0; items = []; }
		loading = true;
		try {
			const batch = await api.faces.suggestions({ limit: PAGE, offset });
			items = reset ? batch : [...items, ...batch];
		} finally {
			loading = false;
		}
	}

	async function loadPeople() {
		people = await api.people.list({ limit: 500 });
	}

	async function scanAll() {
		scanning = true;
		scanProgress = { processed: 0, total: unscannedCount, faces: 0 };
		try {
			const result = await api.photos.batchDetectAll();
			scanProgress = { processed: result.processed, total: result.processed, faces: result.faces_found };
			await loadCounts();
			await loadItems(true);
		} finally {
			scanning = false;
		}
	}

	async function confirm(item: FaceWithSuggestions, personId: number) {
		await api.faces.update(item.face.id, { person_tag_id: personId, status: 'confirmed' });
		items = items.filter(i => i.face.id !== item.face.id);
		unassignedCount = Math.max(0, unassignedCount - 1);
		const p = people.find(x => x.id === personId);
		if (p) people = people.map(x => x.id === personId ? { ...x, face_count: x.face_count + 1 } : x);
	}

	async function ignore(item: FaceWithSuggestions) {
		await api.faces.update(item.face.id, { status: 'ignored' });
		items = items.filter(i => i.face.id !== item.face.id);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	async function deleteFace(item: FaceWithSuggestions) {
		await api.faces.delete(item.face.id);
		items = items.filter(i => i.face.id !== item.face.id);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	function openPicker(faceId: string) {
		pickerFaceId = faceId;
		pickerSearch = '';
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
		loadItems(true);
		loadPeople();
	});
</script>

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
				onclick={() => loadItems(true)}
				disabled={loading}
				class="text-xs px-2 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1 disabled:opacity-50"
			>
				<RefreshCw size={12} class={loading ? 'animate-spin' : ''} />
				Refresh
			</button>
			<button
				onclick={scanAll}
				disabled={scanning || unscannedCount === 0}
				class="text-xs px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1 disabled:opacity-50"
				title={unscannedCount === 0 ? 'All photos already scanned' : `Scan ${unscannedCount} unscanned photos`}
			>
				<Scan size={12} class={scanning ? 'animate-spin' : ''} />
				{scanning ? 'Scanning…' : `Scan ${unscannedCount} photos`}
			</button>
		</div>
	</div>

	<!-- Scan progress -->
	{#if scanProgress && scanning}
	<div class="shrink-0 px-5 py-2 border-b border-zinc-800 bg-zinc-900/30 flex items-center gap-3">
		<div class="flex-1 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
			<div
				class="h-full bg-emerald-500 transition-all"
				style="width: {scanProgress.total > 0 ? (scanProgress.processed / scanProgress.total) * 100 : 0}%"
			></div>
		</div>
		<span class="text-xs text-zinc-400 shrink-0">
			{scanProgress.processed} / {scanProgress.total} — {scanProgress.faces} faces found
		</span>
	</div>
	{/if}
	{#if scanProgress && !scanning}
	<div class="shrink-0 px-5 py-2 border-b border-zinc-800 bg-emerald-900/20 text-emerald-400 text-xs flex items-center gap-2">
		<Check size={13} />
		Scan complete — processed {scanProgress.processed} photos, found {scanProgress.faces} faces
	</div>
	{/if}

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
			<div class="grid gap-4" style="grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))">
				{#each items as item (item.face.id)}
					<div class="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden flex flex-col">
						<!-- Face image -->
						<div class="relative bg-zinc-800 aspect-square">
							<img
								src="/media/face/{item.face.id}?size=300"
								alt="Detected face"
								class="w-full h-full object-cover"
								loading="lazy"
							/>
							<button
								onclick={() => goto(`/photos?photo_id=${item.face.photo_id}`)}
								class="absolute top-1.5 right-1.5 p-1 rounded bg-black/50 hover:bg-black/70 text-white opacity-0 group-hover:opacity-100 transition-opacity"
								title="Open source photo"
							>
								<ChevronRight size={12} />
							</button>
						</div>

						<!-- Suggestions -->
						<div class="flex-1 p-2.5 flex flex-col gap-1.5">
							{#if item.suggestions.length === 0}
								<p class="text-[11px] text-zinc-500 text-center py-1">No matches — assign manually</p>
							{:else}
								{#each item.suggestions as sug (sug.person_id)}
									<button
										onclick={() => confirm(item, sug.person_id)}
										class="group/sug w-full flex items-center gap-2 px-2 py-1.5 rounded-lg bg-zinc-800 hover:bg-emerald-900/40 hover:border-emerald-700 border border-transparent transition-colors text-left"
										title="{scoreLabel(sug.score)}: {Math.round(sug.score * 100)}%"
									>
										<div class="w-5 h-5 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0">
											<User size={10} />
										</div>
										<span class="flex-1 text-xs text-zinc-200 truncate">{sug.person_name ?? '?'}</span>
										<div class="flex items-center gap-1 shrink-0">
											<div class="w-12 h-1 rounded-full bg-zinc-700 overflow-hidden">
												<div class="h-full {scoreColor(sug.score)}" style="width: {Math.round(sug.score * 100)}%"></div>
											</div>
											<span class="text-[10px] text-zinc-500 w-7 text-right">{Math.round(sug.score * 100)}%</span>
											<Check size={12} class="text-zinc-600 group-hover/sug:text-emerald-400 transition-colors ml-0.5" />
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
								title="Not a recognisable face — skip"
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

			<!-- Load more -->
			{#if items.length >= PAGE && !loading}
				<div class="mt-6 flex justify-center">
					<button
						onclick={() => { offset += PAGE; loadItems(false); }}
						class="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-sm text-zinc-300"
					>
						Load more
					</button>
				</div>
			{/if}
		{/if}
	</div>
</div>

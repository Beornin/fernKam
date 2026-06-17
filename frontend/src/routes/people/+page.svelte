<script lang="ts">
	import { api, type PersonOut, type FaceOut } from '$lib/api';
	import { Plus, Search, Trash2, Pencil, Check, X, User, ExternalLink, EyeOff } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { page as pageStore } from '$app/state';
	import { onMount } from 'svelte';
	import { statusCountStore } from '$lib/stores';

	let people = $state<PersonOut[]>([]);
	let faces = $state<FaceOut[]>([]);
	let loadingPeople = $state(true);
	let loadingFaces = $state(false);
	let search = $state('');
	let newName = $state('');
	let creating = $state(false);
	let showCreate = $state(false);
	let selectedPerson = $state<PersonOut | null>(null);
	let showIgnored = $state(false);
	let ignoredFaces = $state<FaceOut[]>([]);
	let ignoredCount = $state(0);
	let loadingIgnored = $state(false);
	let ignoredPage = $state(0);
	let ignoredHasMore = $state(false);
	let renamingId = $state<number | null>(null);
	let renameVal = $state('');
	let facePage = $state(0);
	let faceHasMore = $state(false);
	const PAGE = 200;

	// person picker for assigning ignored faces
	let ignoredPickerFaceId = $state<string | null>(null);
	let ignoredPickerSearch = $state('');
	let filteredPeopleForPicker = $derived(
		ignoredPickerSearch.trim()
			? people.filter(p => p.name.toLowerCase().includes(ignoredPickerSearch.toLowerCase()))
			: people
	);

	async function loadPeople() {
		loadingPeople = true;
		try {
			people = await api.people.list({ search: search || undefined, limit: 500 });
			return people;
		} finally {
			loadingPeople = false;
		}
	}

	async function loadIgnoredFaces() {
		loadingIgnored = true;
		try {
			const batch = await api.faces.list({ status: 'ignored', limit: PAGE, offset: ignoredPage * PAGE });
			ignoredHasMore = batch.length >= PAGE;
			ignoredFaces = batch;
		} finally {
			loadingIgnored = false;
		}
	}

	async function _restoreIgnored() {
		selectedPerson = null;
		showIgnored = true;
		ignoredPage = 0;
		await loadIgnoredFaces();
	}

	async function selectIgnored() {
		await _restoreIgnored();
		api.faces.list({ status: 'ignored', limit: 500 }).then(r => { ignoredCount = r.length; });
		goto('/people?ignored=1', { replaceState: true });
	}

	async function restoreIgnored(face: FaceOut) {
		await api.faces.update(face.id, { status: 'unconfirmed', person_tag_id: null });
		ignoredFaces = ignoredFaces.filter(f => f.id !== face.id);
		ignoredCount = Math.max(0, ignoredCount - 1);
	}

	async function assignIgnoredToPerson(face: FaceOut, personId: number) {
		await api.faces.update(face.id, { person_tag_id: personId, status: 'confirmed' });
		ignoredFaces = ignoredFaces.filter(f => f.id !== face.id);
		ignoredCount = Math.max(0, ignoredCount - 1);
		people = people.map(p => p.id === personId ? { ...p, face_count: p.face_count + 1 } : p);
		ignoredPickerFaceId = null;
	}

	async function deleteIgnoredFace(face: FaceOut) {
		await api.faces.delete(face.id);
		ignoredFaces = ignoredFaces.filter(f => f.id !== face.id);
		ignoredCount = Math.max(0, ignoredCount - 1);
	}

	async function loadFaces(person: PersonOut) {
		loadingFaces = true;
		faces = [];
		try {
			faces = await api.people.faces(person.id, { limit: PAGE, offset: facePage * PAGE });
			faceHasMore = faces.length >= PAGE;
		} finally {
			loadingFaces = false;
		}
	}

	async function selectPerson(person: PersonOut, page = 0) {
		showIgnored = false;
		selectedPerson = person;
		facePage = page;
		await loadFaces(person);
		goto(`/people?person_id=${person.id}&page=${page}`, { replaceState: true });
	}

	async function createPerson() {
		if (!newName.trim()) return;
		creating = true;
		try {
			const p = await api.people.create(newName.trim());
			people = [...people, p];
			newName = '';
			showCreate = false;
			await selectPerson(p);
		} catch (e: any) {
			alert(e.message ?? 'Failed to create person');
		} finally {
			creating = false;
		}
	}

	async function deletePerson(person: PersonOut) {
		if (!confirm(`Delete "${person.name}"? All their faces will be unassigned.`)) return;
		await api.people.delete(person.id);
		people = people.filter(p => p.id !== person.id);
		if (selectedPerson?.id === person.id) {
			selectedPerson = null;
			faces = [];
		}
	}

	function startRename(person: PersonOut) {
		renamingId = person.id;
		renameVal = person.name;
	}

	async function commitRename(person: PersonOut) {
		if (!renameVal.trim() || renameVal === person.name) { cancelRename(); return; }
		const updated = await api.people.rename(person.id, renameVal.trim());
		people = people.map(p => p.id === person.id ? { ...p, name: updated.name } : p);
		if (selectedPerson?.id === person.id) selectedPerson = { ...selectedPerson, name: updated.name };
		cancelRename();
	}

	function cancelRename() {
		renamingId = null;
		renameVal = '';
	}

	async function unassignFace(face: FaceOut) {
		await api.faces.update(face.id, { person_tag_id: null, status: 'unconfirmed' });
		faces = faces.filter(f => f.id !== face.id);
		if (selectedPerson) {
			people = people.map(p => p.id === selectedPerson!.id ? { ...p, face_count: p.face_count - 1 } : p);
			selectedPerson = { ...selectedPerson, face_count: selectedPerson.face_count - 1 };
		}
	}

	async function deleteFace(face: FaceOut) {
		await api.faces.delete(face.id);
		faces = faces.filter(f => f.id !== face.id);
		if (selectedPerson) {
			people = people.map(p => p.id === selectedPerson!.id ? { ...p, face_count: p.face_count - 1 } : p);
			selectedPerson = { ...selectedPerson, face_count: selectedPerson.face_count - 1 };
		}
	}

	let searchDebounce: ReturnType<typeof setTimeout>;
	function onSearchInput() {
		clearTimeout(searchDebounce);
		searchDebounce = setTimeout(() => loadPeople(), 300);
	}

	onMount(async () => {
		const params = pageStore.url.searchParams;
		const pidParam = params.get('person_id');
		const pageParam = Number(params.get('page') ?? '0');
		const ignoredParam = params.get('ignored');

		const ppl = await loadPeople();
		api.faces.list({ status: 'ignored', limit: 500 }).then(r => {
			ignoredCount = r.length;
			statusCountStore.set(`${ppl.length} people`);
		});

		if (pidParam && ppl) {
			const p = ppl.find((x: PersonOut) => x.id === Number(pidParam));
			if (p) {
				showIgnored = false;
				selectedPerson = p;
				facePage = pageParam;
				await loadFaces(p);
			}
		} else if (ignoredParam === '1') {
			await _restoreIgnored();
		}
	});
</script>

<div class="flex h-full overflow-hidden">
	<!-- ── Left panel: people list ── -->
	<aside class="w-[220px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
		<div class="px-3 py-3 border-b border-zinc-800 flex items-center gap-2">
			<div class="relative flex-1">
				<Search size={13} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
				<input
					type="text"
					bind:value={search}
					oninput={onSearchInput}
					placeholder="Search people…"
					class="w-full bg-zinc-800 border border-zinc-700 rounded pl-7 pr-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500"
				/>
			</div>
			<button
				onclick={() => showCreate = !showCreate}
				class="p-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white shrink-0"
				title="Add person"
			>
				<Plus size={14} />
			</button>
		</div>

		{#if showCreate}
		<div class="px-3 py-2 border-b border-zinc-800 bg-zinc-800/50">
			<div class="flex gap-1">
				<input
					type="text"
					bind:value={newName}
					placeholder="Full name"
					class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-amber-500"
					onkeydown={(e) => { if (e.key === 'Enter') createPerson(); if (e.key === 'Escape') { showCreate = false; newName = ''; } }}
				/>
				<button
					onclick={createPerson}
					disabled={creating || !newName.trim()}
					class="px-2 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white text-xs disabled:opacity-50"
				>
					{creating ? '…' : 'Add'}
				</button>
				<button
					onclick={() => { showCreate = false; newName = ''; }}
					class="p-1 rounded hover:bg-zinc-700 text-zinc-400"
				>
					<X size={13} />
				</button>
			</div>
		</div>
		{/if}

		<div class="flex-1 overflow-y-auto py-1">
			{#if loadingPeople}
				<div class="flex justify-center py-8">
					<div class="w-4 h-4 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
				</div>
			{:else if people.length === 0}
				<p class="text-xs text-zinc-500 px-4 py-4">No people yet. Add someone above.</p>
			{:else}
				{#each people as person (person.id)}
					<div
						class="group flex items-center gap-1 px-2 py-0.5 rounded mx-1 cursor-pointer transition-colors
							{selectedPerson?.id === person.id ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-zinc-800'}" 
						onclick={() => selectPerson(person)}
					>
						{#if renamingId === person.id}
							<input
								type="text"
								bind:value={renameVal}
								class="flex-1 bg-zinc-800 border border-emerald-500 rounded px-1 py-0.5 text-xs text-zinc-100 focus:outline-none"
								onclick={(e) => e.stopPropagation()}
								onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Enter') commitRename(person); if (e.key === 'Escape') cancelRename(); }}
							/>
							<button onclick={(e) => { e.stopPropagation(); commitRename(person); }} class="p-0.5 text-emerald-400 hover:text-emerald-300"><Check size={12} /></button>
							<button onclick={(e) => { e.stopPropagation(); cancelRename(); }} class="p-0.5 text-zinc-500 hover:text-zinc-300"><X size={12} /></button>
						{:else}
							<div class="w-7 h-7 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0">
								<User size={14} />
							</div>
							<span class="flex-1 text-xs text-zinc-200 truncate py-1">{person.name}</span>
							<span class="text-[10px] text-zinc-500 shrink-0">{person.face_count}</span>
							<div class="hidden group-hover:flex items-center gap-0.5 ml-1">
								<button
									onclick={(e) => { e.stopPropagation(); startRename(person); }}
									class="p-0.5 rounded hover:bg-zinc-600 text-zinc-400 hover:text-zinc-200"
									title="Rename"
								><Pencil size={11} /></button>
								<button
									onclick={(e) => { e.stopPropagation(); deletePerson(person); }}
									class="p-0.5 rounded hover:bg-red-600/30 text-zinc-400 hover:text-red-400"
									title="Delete"
								><Trash2 size={11} /></button>
							</div>
						{/if}
					</div>
				{/each}
			{/if}
			<!-- Ignored faces entry -->
			<div class="border-t border-zinc-800 mt-1 pt-1">
				<div
					class="flex items-center gap-2 px-2 py-1.5 rounded mx-1 cursor-pointer transition-colors
						{showIgnored ? 'bg-amber-500/15' : 'hover:bg-zinc-800'}"
					onclick={selectIgnored}
				>
					<div class="w-6 h-6 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-500 shrink-0">
						<EyeOff size={14} />
					</div>
					<span class="flex-1 text-xs text-zinc-400 truncate">Ignored Faces</span>
					{#if ignoredCount > 0}
						<span class="text-[10px] text-zinc-500 shrink-0">{ignoredCount}</span>
					{/if}
				</div>
			</div>
		</div>
	</aside>

	<!-- person picker overlay for ignored faces -->
	{#if ignoredPickerFaceId}
	<div class="fixed inset-0 z-50 flex items-center justify-center">
		<button class="absolute inset-0 bg-black/70" aria-label="Close" onclick={() => ignoredPickerFaceId = null}></button>
		<div class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-72 flex flex-col max-h-[70vh]" role="dialog" aria-modal="true">
			<div class="p-3 border-b border-zinc-800 flex items-center gap-2">
				<input type="text" bind:value={ignoredPickerSearch} placeholder="Search people…"
					class="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-emerald-500" />
				<button onclick={() => ignoredPickerFaceId = null} class="p-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={16} /></button>
			</div>
			<div class="overflow-y-auto flex-1 p-1">
				{#if filteredPeopleForPicker.length === 0}
					<p class="text-xs text-zinc-500 px-3 py-3">No people found</p>
				{:else}
					{#each filteredPeopleForPicker as person (person.id)}
						<button onclick={() => { const f = ignoredFaces.find(f => f.id === ignoredPickerFaceId); if (f) assignIgnoredToPerson(f, person.id); }}
							class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left">
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

	<!-- ── Right: face grid ── -->
	<div class="flex-1 flex flex-col overflow-hidden">
		{#if showIgnored}
			<!-- Ignored faces panel -->
			<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/40 flex items-center gap-3">
				<div class="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-500">
					<EyeOff size={16} />
				</div>
				<div>
					<h2 class="text-base font-semibold text-zinc-100">Ignored Faces</h2>
					<p class="text-xs text-zinc-500">{ignoredCount} face{ignoredCount === 1 ? '' : 's'} — auto-ignored or manually ignored</p>
				</div>
			</div>
			<div class="flex-1 overflow-y-auto p-4">
				{#if loadingIgnored && ignoredFaces.length === 0}
					<div class="flex justify-center py-16"><div class="w-6 h-6 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div></div>
				{:else if ignoredFaces.length === 0}
					<div class="flex flex-col items-center justify-center h-48 text-zinc-600 gap-2">
						<EyeOff size={32} />
						<p class="text-sm">No ignored faces yet</p>
						<p class="text-xs">Click Ignore on a face in Face Review to add it to this pool</p>
					</div>
				{:else}
					<div class="grid gap-2" style="grid-template-columns: repeat(auto-fill, minmax(110px, 1fr))">
						{#each ignoredFaces as face (face.id)}
							<div class="group relative aspect-square bg-zinc-900 rounded overflow-hidden">
								<img src="http://localhost:8000/media/face/{face.id}?size=200" alt="" class="w-full h-full object-cover" loading="lazy" />
								<div class="absolute inset-0 bg-black/0 group-hover:bg-black/60 transition-colors flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100">
									<button onclick={() => goto(`/photos?photo_id=${face.photo_id}&back=${encodeURIComponent(pageStore.url.href)}`)}
										class="p-1.5 rounded bg-white/10 hover:bg-white/20 text-white" title="Open photo">
										<ExternalLink size={13} />
									</button>
									<button onclick={() => { ignoredPickerFaceId = face.id; ignoredPickerSearch = ''; }}
										class="p-1.5 rounded bg-emerald-500/20 hover:bg-emerald-500/40 text-emerald-300" title="Assign to person">
										<User size={13} />
									</button>
									<button onclick={() => restoreIgnored(face)}
										class="p-1.5 rounded bg-amber-500/20 hover:bg-amber-500/40 text-amber-300" title="Restore to review queue">
										<X size={13} />
									</button>
									<button onclick={() => deleteIgnoredFace(face)}
										class="p-1.5 rounded bg-red-500/20 hover:bg-red-500/40 text-red-300" title="Delete permanently">
										<Trash2 size={13} />
									</button>
								</div>
							</div>
						{/each}
					</div>
					{#if ignoredPage > 0 || ignoredHasMore}
						<div class="mt-4 flex items-center justify-center gap-3">
							<button onclick={() => { ignoredPage -= 1; loadIgnoredFaces(); }}
								disabled={ignoredPage === 0 || loadingIgnored}
								class="px-3 py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40">← Prev</button>
							<span class="text-xs text-zinc-500">Page {ignoredPage + 1}</span>
							<button onclick={() => { ignoredPage += 1; loadIgnoredFaces(); }}
								disabled={!ignoredHasMore || loadingIgnored}
								class="px-3 py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40">Next →</button>
						</div>
					{/if}
				{/if}
			</div>
		{:else if !selectedPerson}
			<div class="flex-1 flex flex-col items-center justify-center text-zinc-600 gap-3">
				<User size={48} />
				<p class="text-sm">Select a person to view their faces</p>
			</div>
		{:else}
			<!-- Person header -->
			<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/40 flex items-center gap-3">
				<div class="w-8 h-8 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-300">
					<User size={16} />
				</div>
				<div>
					<h2 class="text-base font-semibold text-zinc-100">{selectedPerson.name}</h2>
					<p class="text-xs text-zinc-500">{selectedPerson.face_count} confirmed face{selectedPerson.face_count === 1 ? '' : 's'}</p>
				</div>
				<div class="ml-auto flex items-center gap-2">
					<button
						onclick={() => startRename(selectedPerson!)}
						class="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1"
					>
						<Pencil size={11} /> Rename
					</button>
					<button
						onclick={() => deletePerson(selectedPerson!)}
						class="text-xs px-2 py-1 rounded bg-red-600/20 hover:bg-red-600/40 text-red-400 flex items-center gap-1"
					>
						<Trash2 size={11} /> Delete
					</button>
				</div>
			</div>

			<!-- Face grid -->
			<div class="flex-1 overflow-y-auto p-4">
				{#if loadingFaces}
					<div class="flex justify-center py-16">
						<div class="w-6 h-6 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					</div>
				{:else if faces.length === 0}
					<div class="flex flex-col items-center justify-center h-48 text-zinc-600 gap-2">
						<User size={32} />
						<p class="text-sm">No confirmed faces for {selectedPerson.name}</p>
						<p class="text-xs">Use Face Review to assign faces to this person</p>
					</div>
				{:else}
					<div class="grid gap-2" style="grid-template-columns: repeat(auto-fill, minmax(110px, 1fr))">
						{#each faces as face (face.id)}
							<div class="group relative aspect-square bg-zinc-900 rounded overflow-hidden">
								<img
									src="http://localhost:8000/media/face/{face.id}?size=200"
									alt=""
									class="w-full h-full object-cover"
									loading="lazy"
								/>
								<div class="absolute inset-0 bg-black/0 group-hover:bg-black/50 transition-colors flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100">
									<button
										onclick={() => goto(`/photos?photo_id=${face.photo_id}&back=${encodeURIComponent(pageStore.url.href)}`)}
										class="p-1.5 rounded bg-white/10 hover:bg-white/20 text-white"
										title="Open photo"
									>
										<ExternalLink size={13} />
									</button>
									<button
										onclick={() => unassignFace(face)}
										class="p-1.5 rounded bg-amber-500/20 hover:bg-amber-500/40 text-amber-300"
										title="Unassign face"
									>
										<X size={13} />
									</button>
									<button
										onclick={() => deleteFace(face)}
										class="p-1.5 rounded bg-red-500/20 hover:bg-red-500/40 text-red-300"
										title="Delete face"
									>
										<Trash2 size={13} />
									</button>
								</div>
							</div>
						{/each}
					</div>
					{#if facePage > 0 || faceHasMore}
						<div class="mt-4 flex items-center justify-center gap-3">
							<button
								onclick={() => { facePage -= 1; loadFaces(selectedPerson!); goto(`/people?person_id=${selectedPerson!.id}&page=${facePage}`, { replaceState: true }); }}
								disabled={facePage === 0 || loadingFaces}
								class="px-3 py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40"
							>← Prev</button>
							<span class="text-xs text-zinc-500">Page {facePage + 1}</span>
							<button
								onclick={() => { facePage += 1; loadFaces(selectedPerson!); goto(`/people?person_id=${selectedPerson!.id}&page=${facePage}`, { replaceState: true }); }}
								disabled={!faceHasMore || loadingFaces}
								class="px-3 py-1.5 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40"
							>Next →</button>
						</div>
					{/if}
				{/if}
			</div>
		{/if}
	</div>
</div>

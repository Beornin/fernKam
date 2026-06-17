<script lang="ts">
	import { api, type TagOut, type PhotoSummary } from '$lib/api';
	import { Tag, ChevronRight, ChevronDown, User, Edit2, Trash2, Move, Unlink, Search, Plus } from '@lucide/svelte';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { statusCountStore } from '$lib/stores';

	let tags = $state<TagOut[]>([]);
	let loading = $state(true);
	let search = $state('');
	let expanded = $state(new Set<number>());
	let editingId = $state<number | null>(null);
	let editName = $state('');
	let movingId = $state<number | null>(null);
	let moveParentId = $state<number | null>(null);
	let allTags = $state<TagOut[]>([]);
	let selectedTagIds = $state(new Set<number>());
	let bulkWorking = $state(false);

	// Photo grid state
	let selectedTagId = $state<number | null>(null);
	let selectedTagName = $state('');
	let photos = $state<PhotoSummary[]>([]);
	let photosLoading = $state(false);
	let photoTotal = $state(0);
	let selectedIds = $state(new Set<number>());
	let lightboxId = $state<number | null>(null);
	let lightboxIdx = $state(-1);
	let newTagName = $state('');
	let creatingTag = $state(false);

	async function loadTags() {
		loading = true;
		if (search.trim()) {
			tags = await api.tags.list({ flat: true, search: search.trim() });
		} else {
			tags = await api.tags.list();
		}
		allTags = await api.tags.list({ flat: true });
		loading = false;
	}

	$effect(() => { loadTags(); });

	async function selectTag(tag: TagOut) {
		selectedTagId = tag.id;
		selectedTagName = tag.name;
		photosLoading = true;
		photos = [];
		const data = await api.photos.list({ tag_id: tag.id, page_size: 200 });
		photos = data.items;
		photoTotal = data.total;
		photosLoading = false;
		statusCountStore.set(`${data.total.toLocaleString()} items`);
	}

	function toggle(id: number) {
		if (expanded.has(id)) expanded.delete(id);
		else expanded.add(id);
		expanded = new Set(expanded);
	}

	async function startEdit(tag: TagOut) { editingId = tag.id; editName = tag.name; }

	async function saveEdit() {
		if (!editingId || !editName.trim()) return;
		try {
			await api.tags.update(editingId, { name: editName });
			editingId = null;
			await loadTags();
		} catch (e) { console.error(e); }
	}

	async function startMove(tag: TagOut) { movingId = tag.id; moveParentId = tag.parent_id; }

	async function saveMove() {
		if (!movingId) return;
		try {
			await api.tags.update(movingId, { parent_id: moveParentId });
			movingId = null;
			await loadTags();
		} catch (e) { console.error(e); }
	}

	async function deleteTag(id: number) {
		if (!confirm('Delete this tag and all its children?')) return;
		try {
			await api.tags.delete(id);
			selectedTagIds.delete(id);
			selectedTagIds = new Set(selectedTagIds);
			if (selectedTagId === id) { selectedTagId = null; photos = []; }
			await loadTags();
		} catch (e) { console.error(e); }
	}

	async function removeFromPhotos(id: number) {
		if (!confirm('Remove this tag from all photos?')) return;
		try {
			const res = await api.tags.removeFromPhotos(id);
			alert(`Removed from ${res.removed} photo(s).`);
		} catch (e) { console.error(e); }
	}

	async function bulkRemoveFromPhotos() {
		if (selectedTagIds.size === 0) return;
		if (!confirm(`Remove ${selectedTagIds.size} selected tag(s) from all photos?`)) return;
		bulkWorking = true;
		try {
			let total = 0;
			for (const id of selectedTagIds) {
				const res = await api.tags.removeFromPhotos(id);
				total += res.removed;
			}
			selectedTagIds = new Set();
			alert(`Removed ${total} photo association(s).`);
		} catch (e) { console.error(e); } finally { bulkWorking = false; }
	}

	function toggleSelect(id: number) {
		if (selectedTagIds.has(id)) selectedTagIds.delete(id);
		else selectedTagIds.add(id);
		selectedTagIds = new Set(selectedTagIds);
	}

	function getAvailableParents(tag: TagOut): TagOut[] {
		return allTags.filter(t => t.id !== tag.id && !isDescendant(tag.id, t.id));
	}

	function isDescendant(parentId: number, childId: number): boolean {
		const child = allTags.find(t => t.id === childId);
		if (!child) return false;
		if (child.parent_id === parentId) return true;
		return child.parent_id ? isDescendant(parentId, child.parent_id) : false;
	}

	async function createTag() {
		if (!newTagName.trim()) return;
		creatingTag = true;
		try {
			await api.tags.create({ name: newTagName.trim() });
			newTagName = '';
			await loadTags();
		} catch (e) { console.error(e); } finally { creatingTag = false; }
	}

	function openPhoto(p: PhotoSummary) {
		lightboxId = p.id;
		lightboxIdx = photos.indexOf(p);
	}
</script>

<div class="flex h-full overflow-hidden">
	<!-- Tag tree left panel -->
	<aside class="w-[220px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
		<div class="px-3 py-2 border-b border-zinc-800 shrink-0">
			<span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Tags</span>
		</div>

		<!-- Search + new tag -->
		<div class="px-2 py-2 border-b border-zinc-800 flex gap-1 shrink-0">
			<div class="relative flex-1">
				<Search size={11} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600" />
				<input
					type="search"
					placeholder="Filter…"
					class="w-full pl-6 pr-2 py-1 text-xs bg-zinc-800 border border-zinc-700 rounded text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500"
					bind:value={search}
				/>
			</div>
		</div>

		<!-- New tag row -->
		<div class="px-2 py-1.5 border-b border-zinc-800 flex gap-1 shrink-0">
			<input
				type="text"
				placeholder="New tag…"
				class="flex-1 px-2 py-1 text-xs bg-zinc-800 border border-zinc-700 rounded text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500"
				bind:value={newTagName}
				onkeydown={(e) => e.key === 'Enter' && createTag()}
			/>
			<button
				onclick={createTag}
				disabled={creatingTag || !newTagName.trim()}
				class="p-1 rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-40 transition-colors"
				title="Create tag"
			><Plus size={12} /></button>
		</div>

		{#if selectedTagIds.size > 0}
			<div class="px-2 py-1.5 border-b border-zinc-800 flex items-center gap-1 shrink-0">
				<span class="text-[10px] text-zinc-400 flex-1">{selectedTagIds.size} selected</span>
				<button
					onclick={bulkRemoveFromPhotos}
					disabled={bulkWorking}
					class="text-[10px] px-2 py-0.5 rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50 transition-colors"
				>Remove from photos</button>
				<button onclick={() => selectedTagIds = new Set()} class="text-[10px] text-zinc-500 hover:text-zinc-300">×</button>
			</div>
		{/if}

		<!-- Tag tree -->
		<div class="flex-1 overflow-y-auto py-1">
			{#if loading}
				<div class="flex items-center justify-center py-8 text-zinc-600 text-xs gap-1.5">
					<div class="w-3 h-3 border border-zinc-600 border-t-amber-400 rounded-full animate-spin"></div>
					Loading…
				</div>
			{:else}
				{#snippet tagRow(tag: TagOut, depth: number)}
					{@const hasChildren = tag.children.length > 0}
					{@const isOpen = expanded.has(tag.id)}
					{@const isSelected = selectedTagId === tag.id}
					<div style="padding-left: {depth * 14}px">
						<div class="flex items-center gap-0.5 rounded mx-1 group
							{isSelected ? 'bg-amber-500/15' : 'hover:bg-zinc-800'}">
							{#if hasChildren}
								<button class="w-4 h-5 flex items-center justify-center text-zinc-600 hover:text-zinc-300 shrink-0" onclick={() => toggle(tag.id)}>
									{#if isOpen}<ChevronDown size={11} />{:else}<ChevronRight size={11} />{/if}
								</button>
							{:else}
								<span class="w-4 shrink-0"></span>
							{/if}

							{#if tag.is_person}
								<User size={12} class="text-sky-400 shrink-0" />
							{:else}
								<Tag size={12} class="shrink-0 {isSelected ? 'text-amber-400' : 'text-zinc-500'}" />
							{/if}

							<button
								class="flex-1 text-xs py-1 text-left truncate px-1 min-w-0
									{isSelected ? 'text-amber-300' : 'text-zinc-300 hover:text-white'}"
								onclick={() => selectTag(tag)}
								title={tag.path}
							>{tag.name}</button>

							<div class="flex items-center gap-0 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
								<input type="checkbox" checked={selectedTagIds.has(tag.id)} onchange={() => toggleSelect(tag.id)}
									class="w-3 h-3 rounded border-zinc-600 bg-zinc-800 accent-amber-500 mr-0.5" />
								<button onclick={() => startEdit(tag)} class="p-0.5 text-zinc-600 hover:text-zinc-300 rounded" title="Rename"><Edit2 size={11} /></button>
								<button onclick={() => startMove(tag)} class="p-0.5 text-zinc-600 hover:text-zinc-300 rounded" title="Move"><Move size={11} /></button>
								<button onclick={() => deleteTag(tag.id)} class="p-0.5 text-zinc-600 hover:text-red-400 rounded" title="Delete"><Trash2 size={11} /></button>
							</div>
						</div>

						{#if isOpen && hasChildren}
							{#each tag.children as child}
								{@render tagRow(child, depth + 1)}
							{/each}
						{/if}
					</div>
				{/snippet}

				{#each tags as tag}
					{@render tagRow(tag, 0)}
				{/each}
			{/if}
		</div>
	</aside>

	<!-- Main: photo grid for selected tag -->
	<div class="flex flex-col flex-1 overflow-hidden">
		{#if selectedTagId !== null}
			<div class="px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 shrink-0 flex items-center gap-2">
				<span class="text-xs font-medium text-amber-400">{selectedTagName}</span>
				<span class="text-xs text-zinc-600">— {photoTotal.toLocaleString()} photos</span>
			</div>
		{/if}
		<div class="flex-1 overflow-y-auto">
			{#if photosLoading}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm gap-2">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					Loading…
				</div>
			{:else if selectedTagId === null}
				<div class="flex flex-col items-center justify-center h-full text-zinc-600 gap-3">
					<Tag size={40} class="opacity-30" />
					<p class="text-sm">Select a tag to view its photos</p>
				</div>
			{:else if photos.length === 0}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm">No photos with this tag</div>
			{:else}
				<PhotoGrid {photos} onSelect={openPhoto} bind:selectedIds />
			{/if}
		</div>
	</div>
</div>

{#if lightboxId !== null}
	<PhotoLightbox
		photoId={lightboxId}
		onClose={() => { lightboxId = null; lightboxIdx = -1; }}
		onPrev={lightboxIdx > 0 ? () => { lightboxIdx--; lightboxId = photos[lightboxIdx].id; } : undefined}
		onNext={lightboxIdx < photos.length - 1 ? () => { lightboxIdx++; lightboxId = photos[lightboxIdx].id; } : undefined}
	/>
{/if}

<!-- Edit modal -->
{#if editingId !== null}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
		<div class="bg-zinc-900 rounded-lg p-5 w-80 border border-zinc-800">
			<h2 class="text-sm font-semibold text-zinc-100 mb-3">Rename Tag</h2>
			<input type="text" bind:value={editName}
				class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-amber-500 mb-3"
				placeholder="Tag name" />
			<div class="flex gap-2 justify-end">
				<button onclick={() => editingId = null} class="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm transition-colors">Cancel</button>
				<button onclick={saveEdit} class="px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white text-sm transition-colors">Save</button>
			</div>
		</div>
	</div>
{/if}

<!-- Move modal -->
{#if movingId !== null}
	{@const movingTag = allTags.find(t => t.id === movingId)}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
		<div class="bg-zinc-900 rounded-lg p-5 w-80 border border-zinc-800">
			<h2 class="text-sm font-semibold text-zinc-100 mb-3">Move "{movingTag?.name}"</h2>
			<select bind:value={moveParentId}
				class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-amber-500 mb-3">
				<option value={null}>Root (no parent)</option>
				{#each getAvailableParents(movingTag!) as parent}
					<option value={parent.id}>{parent.path}</option>
				{/each}
			</select>
			<div class="flex gap-2 justify-end">
				<button onclick={() => movingId = null} class="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm transition-colors">Cancel</button>
				<button onclick={saveMove} class="px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white text-sm transition-colors">Move</button>
			</div>
		</div>
	</div>
{/if}

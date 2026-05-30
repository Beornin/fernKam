<script lang="ts">
	import { api, type TagOut } from '$lib/api';
	import { Tag, ChevronRight, ChevronDown, User, Edit2, Trash2, Move } from '@lucide/svelte';

	let tags = $state<TagOut[]>([]);
	let loading = $state(true);
	let search = $state('');
	let expanded = $state(new Set<number>());
	let editingId = $state<number | null>(null);
	let editName = $state('');
	let movingId = $state<number | null>(null);
	let moveParentId = $state<number | null>(null);
	let allTags = $state<TagOut[]>([]);

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

	$effect(() => {
		loadTags();
	});

	function toggle(id: number) {
		if (expanded.has(id)) expanded.delete(id);
		else expanded.add(id);
		expanded = new Set(expanded);
	}

	async function startEdit(tag: TagOut) {
		editingId = tag.id;
		editName = tag.name;
	}

	async function saveEdit() {
		if (!editingId || !editName.trim()) return;
		try {
			await api.tags.update(editingId, { name: editName });
			editingId = null;
			await loadTags();
		} catch (e) {
			console.error('Failed to update tag:', e);
		}
	}

	async function startMove(tag: TagOut) {
		movingId = tag.id;
		moveParentId = tag.parent_id;
	}

	async function saveMove() {
		if (!movingId) return;
		try {
			await api.tags.update(movingId, { parent_id: moveParentId });
			movingId = null;
			await loadTags();
		} catch (e) {
			console.error('Failed to move tag:', e);
		}
	}

	async function deleteTag(id: number) {
		if (!confirm('Delete this tag and all its children?')) return;
		try {
			await api.tags.delete(id);
			await loadTags();
		} catch (e) {
			console.error('Failed to delete tag:', e);
		}
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
</script>

<div class="p-6">
	<div class="flex items-center justify-between mb-6">
		<h1 class="text-xl font-semibold text-zinc-100">Tags</h1>
		<input
			type="search"
			placeholder="Search tags…"
			class="text-sm bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 w-52"
			bind:value={search}
		/>
	</div>

	{#if loading}
		<div class="flex items-center gap-2 text-zinc-500 text-sm">
			<div class="w-4 h-4 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
			Loading…
		</div>
	{:else}
		<div class="space-y-0.5">
			{#snippet tagRow(tag: TagOut, depth: number)}
				{@const hasChildren = tag.children.length > 0}
				{@const isOpen = expanded.has(tag.id)}
				<div style="padding-left: {depth * 18}px">
					<div class="flex items-center gap-1.5 rounded hover:bg-zinc-900 px-2 py-1 group">
						{#if hasChildren}
							<button class="text-zinc-600 hover:text-zinc-300" onclick={() => toggle(tag.id)}>
								{#if isOpen}<ChevronDown size={13} />{:else}<ChevronRight size={13} />{/if}
							</button>
						{:else}
							<span class="w-[13px]"></span>
						{/if}

						{#if tag.is_person}
							<User size={14} class="text-sky-400 shrink-0" />
						{:else}
							<Tag size={14} class="text-zinc-500 shrink-0" />
						{/if}

						<a
							href="/photos?tag_id={tag.id}"
							class="flex-1 text-sm text-zinc-300 hover:text-white truncate"
							title={tag.path}
						>
							{tag.name}
						</a>

						<div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
							<button
								onclick={() => startEdit(tag)}
								class="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded transition-colors"
								title="Edit name"
							>
								<Edit2 size={14} />
							</button>
							<button
								onclick={() => startMove(tag)}
								class="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded transition-colors"
								title="Move to different parent"
							>
								<Move size={14} />
							</button>
							<button
								onclick={() => deleteTag(tag.id)}
								class="p-1 text-zinc-500 hover:text-red-400 hover:bg-zinc-800 rounded transition-colors"
								title="Delete tag"
							>
								<Trash2 size={14} />
							</button>
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
		</div>
	{/if}
</div>

<!-- Edit modal -->
{#if editingId !== null}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
		<div class="bg-zinc-900 rounded-lg p-6 w-96 border border-zinc-800">
			<h2 class="text-lg font-semibold text-zinc-100 mb-4">Edit Tag Name</h2>
			<input
				type="text"
				bind:value={editName}
				class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-emerald-500 mb-4"
				placeholder="Tag name"
			/>
			<div class="flex gap-2 justify-end">
				<button
					onclick={() => { editingId = null; }}
					class="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition-colors"
				>
					Cancel
				</button>
				<button
					onclick={saveEdit}
					class="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition-colors"
				>
					Save
				</button>
			</div>
		</div>
	</div>
{/if}

<!-- Move modal -->
{#if movingId !== null}
	{@const movingTag = allTags.find(t => t.id === movingId)}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
		<div class="bg-zinc-900 rounded-lg p-6 w-96 border border-zinc-800">
			<h2 class="text-lg font-semibold text-zinc-100 mb-4">Move "{movingTag?.name}" to Parent</h2>
			<select
				bind:value={moveParentId}
				class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-emerald-500 mb-4"
			>
				<option value={null}>Root (no parent)</option>
				{#each getAvailableParents(movingTag!) as parent}
					<option value={parent.id}>{parent.path}</option>
				{/each}
			</select>
			<div class="flex gap-2 justify-end">
				<button
					onclick={() => { movingId = null; }}
					class="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition-colors"
				>
					Cancel
				</button>
				<button
					onclick={saveMove}
					class="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition-colors"
				>
					Move
				</button>
			</div>
		</div>
	</div>
{/if}

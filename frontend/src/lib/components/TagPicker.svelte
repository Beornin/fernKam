<script lang="ts">
	import { api, type TagOut } from '$lib/api';
	import { X, Plus, Tag, ChevronRight, Search } from '@lucide/svelte';

	let {
		photoId,
		currentTags = $bindable([]),
	}: {
		photoId: number;
		currentTags: TagOut[];
	} = $props();

	let allTags = $state<TagOut[]>([]);
	let query = $state('');
	let open = $state(false);
	let creating = $state(false);
	let newTagName = $state('');
	let newTagParentId = $state<number | null>(null);
	let inputEl: HTMLInputElement;

	$effect(() => {
		api.tags.list({ flat: true }).then(t => { allTags = t; });
	});

	const currentIds = $derived(new Set(currentTags.map(t => t.id)));

	const filtered = $derived(
		query.trim().length > 0
			? allTags.filter(t => t.name.toLowerCase().includes(query.toLowerCase()) && !currentIds.has(t.id))
			: allTags.filter(t => !currentIds.has(t.id)).slice(0, 60)
	);

	// Build parent name lookup
	const tagById = $derived<Record<number, TagOut>>(Object.fromEntries(allTags.map((t: TagOut) => [t.id, t])));

	function parentChain(tag: TagOut): string {
		const parts: string[] = [];
		let cur: TagOut | undefined = tag;
		while (cur?.parent_id) {
			const p: TagOut | undefined = tagById[cur.parent_id];
			if (!p) break;
			parts.unshift(p.name);
			cur = p;
		}
		return parts.length ? parts.join(' › ') + ' › ' : '';
	}

	async function addTag(tag: TagOut) {
		await api.photoTags.add(photoId, tag.id);
		currentTags = [...currentTags, tag];
		query = '';
		inputEl?.focus();
	}

	async function removeTag(tag: TagOut) {
		await api.photoTags.remove(photoId, tag.id);
		currentTags = currentTags.filter(t => t.id !== tag.id);
	}

	async function createAndAdd() {
		if (!newTagName.trim()) return;
		const created = await api.tags.create({
			name: newTagName.trim(),
			parent_id: newTagParentId,
		});
		allTags = [...allTags, created];
		await addTag(created);
		newTagName = '';
		newTagParentId = null;
		creating = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') { open = false; query = ''; }
		if (e.key === 'Enter' && filtered.length === 1) addTag(filtered[0]);
	}
</script>

<div class="space-y-2">
	<!-- Current tags -->
	<div class="flex flex-wrap gap-1 min-h-[24px]">
		{#each currentTags as tag (tag.id)}
			<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-zinc-700 text-zinc-200 group">
				<Tag size={10} class="shrink-0 text-zinc-400" />
				{tag.name}
				<button
					class="text-zinc-500 hover:text-red-400 transition-colors"
					onclick={() => removeTag(tag)}
					aria-label="Remove {tag.name}"
				>
					<X size={10} />
				</button>
			</span>
		{/each}
	</div>

	<!-- Search input -->
	<div class="relative">
		<div class="flex items-center gap-1.5 bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 focus-within:ring-1 focus-within:ring-emerald-500">
			<Search size={12} class="text-zinc-500 shrink-0" />
			<input
				bind:this={inputEl}
				type="text"
				placeholder="Add tag…"
				class="flex-1 bg-transparent text-xs text-zinc-200 placeholder:text-zinc-500 focus:outline-none min-w-0"
				bind:value={query}
				onfocus={() => { open = true; }}
				onblur={() => setTimeout(() => { open = false; }, 150)}
				onkeydown={handleKeydown}
			/>
			<button
				class="text-zinc-500 hover:text-emerald-400 transition-colors"
				onmousedown={(e) => { e.preventDefault(); creating = !creating; }}
				title="Create new tag"
			>
				<Plus size={14} />
			</button>
		</div>

		<!-- Dropdown -->
		{#if open && filtered.length > 0}
			<div class="absolute z-10 top-full mt-1 left-0 right-0 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl max-h-52 overflow-y-auto">
				{#each filtered as tag (tag.id)}
					<button
						class="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-zinc-700 transition-colors"
						onmousedown={(e) => { e.preventDefault(); addTag(tag); }}
					>
						<Tag size={11} class="text-zinc-500 shrink-0" />
						<span class="text-xs text-zinc-400 truncate">{parentChain(tag)}</span>
						<span class="text-xs text-zinc-200 font-medium truncate">{tag.name}</span>
					</button>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Create new tag form -->
	{#if creating}
		<div class="bg-zinc-800/80 border border-zinc-700 rounded-lg p-3 space-y-2">
			<p class="text-xs text-zinc-400 font-medium">New tag</p>
			<input
				type="text"
				placeholder="Tag name"
				class="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-xs text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
				bind:value={newTagName}
				onkeydown={(e) => { if (e.key === 'Enter') createAndAdd(); }}
			/>
			<select
				class="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-xs text-zinc-300"
				bind:value={newTagParentId}
			>
				<option value={null}>— No parent (root tag) —</option>
				{#each allTags as t (t.id)}
					<option value={t.id}>{t.path.replace(/\./g, ' › ')}</option>
				{/each}
			</select>
			<div class="flex gap-2">
				<button
					class="flex-1 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white font-medium transition-colors"
					onclick={createAndAdd}
				>
					Create &amp; add
				</button>
				<button
					class="px-3 py-1.5 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors"
					onclick={() => { creating = false; }}
				>
					Cancel
				</button>
			</div>
		</div>
	{/if}
</div>

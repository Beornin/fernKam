<script lang="ts">
	import { api, type TagOut } from '$lib/api';
	import { Tag, ChevronRight, ChevronDown, User } from '@lucide/svelte';

	let tags = $state<TagOut[]>([]);
	let loading = $state(true);
	let search = $state('');
	let expanded = $state(new Set<number>());

	$effect(() => {
		if (search.trim()) {
			api.tags.list({ flat: true, search: search.trim() }).then(data => { tags = data; loading = false; });
		} else {
			api.tags.list().then(data => {
				tags = data;
				loading = false;
			});
		}
	});

	function toggle(id: number) {
		if (expanded.has(id)) expanded.delete(id);
		else expanded.add(id);
		expanded = new Set(expanded);
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

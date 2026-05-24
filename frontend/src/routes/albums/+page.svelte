<script lang="ts">
	import { api, type AlbumNode } from '$lib/api';
	import { FolderOpen, Folder, ChevronRight, ChevronDown, Images } from '@lucide/svelte';

	let albums = $state<AlbumNode[]>([]);
	let loading = $state(true);
	let expanded = $state(new Set<string>());

	$effect(() => {
		api.albums.list().then(data => {
			albums = data;
			// Auto-expand first level
			data.forEach(a => expanded.add(a.path));
			loading = false;
		});
	});

	function toggle(path: string) {
		if (expanded.has(path)) expanded.delete(path);
		else expanded.add(path);
		expanded = new Set(expanded);
	}
</script>

<div class="p-6">
	<h1 class="text-xl font-semibold text-zinc-100 mb-6">Albums</h1>

	{#if loading}
		<div class="flex items-center gap-2 text-zinc-500 text-sm">
			<div class="w-4 h-4 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
			Loading…
		</div>
	{:else}
		<div class="space-y-0.5">
			{#snippet albumRow(node: AlbumNode, depth: number)}
				{@const hasChildren = node.children.length > 0}
				{@const isOpen = expanded.has(node.path)}
				<div style="padding-left: {depth * 20}px">
					<div class="flex items-center group rounded-lg hover:bg-zinc-900 px-2 py-1.5 gap-1.5">
						{#if hasChildren}
							<button class="text-zinc-500 hover:text-zinc-300" onclick={() => toggle(node.path)}>
								{#if isOpen}<ChevronDown size={14} />{:else}<ChevronRight size={14} />{/if}
							</button>
						{:else}
							<span class="w-[14px]"></span>
						{/if}

						{#if isOpen && hasChildren}
							<FolderOpen size={16} class="text-amber-400 shrink-0" />
						{:else}
							<Folder size={16} class="text-amber-400 shrink-0" />
						{/if}

						<a
							href="/photos?album_path={encodeURIComponent(node.path)}"
							class="flex-1 text-sm text-zinc-200 hover:text-white truncate"
						>
							{node.name}
						</a>

						<span class="text-xs text-zinc-600 flex items-center gap-1">
							<Images size={11} />
							{node.photo_count.toLocaleString()}
						</span>
					</div>

					{#if isOpen && hasChildren}
						{#each node.children as child}
							{@render albumRow(child, depth + 1)}
						{/each}
					{/if}
				</div>
			{/snippet}

			{#each albums as album}
				{@render albumRow(album, 0)}
			{/each}
		</div>
	{/if}
</div>

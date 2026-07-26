<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { api, type AlbumNode } from '$lib/api';
	import { buildFilterUrl } from '$lib/shellFilters';
	import { ChevronDown, ChevronRight, FolderOpen, Folder, Images } from '@lucide/svelte';

	let albumPath = $derived($page.url.searchParams.get('album_path') ?? '');

	let albums = $state<AlbumNode[]>([]);
	let expanded = $state(new Set<string>());

	onMount(async () => {
		albums = await api.albums.list();
		if (albumPath) {
			const parts = albumPath.split('/').filter(Boolean);
			let cur = '';
			for (const part of parts) {
				cur += '/' + part;
				expanded.add(cur);
			}
			expanded = new Set(expanded);
		}
	});

	function selectAlbum(path: string) {
		goto(buildFilterUrl($page.url, 'albums', { album_path: path || undefined }));
	}

	function toggleFolder(path: string) {
		if (expanded.has(path)) expanded.delete(path);
		else expanded.add(path);
		expanded = new Set(expanded);
	}
</script>

<div class="flex-1 overflow-y-auto py-1">
	<button
		class="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs transition-colors text-left
			{!albumPath ? 'bg-amber-500/20 text-amber-400' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'}"
		onclick={() => selectAlbum('')}
	>
		<Images size={13} class="shrink-0" />
		<span class="truncate">All Photos</span>
	</button>
	{#snippet albumRow(node: AlbumNode, depth: number)}
		{@const isExpanded = expanded.has(node.path)}
		{@const hasChildren = node.children.length > 0}
		{@const selected = albumPath === node.path}
		<div>
			<div class="flex items-center gap-0.5 hover:bg-zinc-800 transition-colors" style="padding-left:{depth * 12 + 8}px">
				<button
					class="w-4 h-4 flex items-center justify-center text-zinc-600 hover:text-zinc-300 shrink-0"
					onclick={() => hasChildren && toggleFolder(node.path)}
				>
					{#if hasChildren}
						{#if isExpanded}<ChevronDown size={11} />{:else}<ChevronRight size={11} />{/if}
					{/if}
				</button>
				<button
					class="flex-1 flex items-center gap-1.5 py-1 text-xs text-left min-w-0 pr-2
						{selected ? 'text-amber-400' : 'text-zinc-400 hover:text-zinc-200'}"
					onclick={() => { selectAlbum(node.path); if (hasChildren && !isExpanded) toggleFolder(node.path); }}
				>
					{#if isExpanded || selected}
						<FolderOpen size={12} class="shrink-0 {selected ? 'text-amber-400' : 'text-zinc-500'}" />
					{:else}
						<Folder size={12} class="shrink-0 text-zinc-600" />
					{/if}
					<span class="truncate">{node.name}</span>
					<span class="ml-auto text-zinc-700 shrink-0 text-[10px]">{node.photo_count}</span>
				</button>
			</div>
			{#if isExpanded && hasChildren}
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

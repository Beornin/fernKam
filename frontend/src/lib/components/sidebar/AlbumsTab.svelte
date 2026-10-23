<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { api, type AlbumNode } from '$lib/api';
	import { buildFilterUrl } from '$lib/shellFilters';
	import { libraryVersionStore } from '$lib/stores';
	import { notify } from '$lib/dialog.svelte';
	import ContextMenu from '$lib/components/ContextMenu.svelte';
	import { ChevronDown, ChevronRight, FolderOpen, Folder, Images } from '@lucide/svelte';

	let albumPath = $derived($page.url.searchParams.get('album_path') ?? '');

	let albums = $state<AlbumNode[]>([]);
	let expanded = $state(new Set<string>());

	// A scan that changed the library may have made new folders (a PureRAW
	// jpg/, a new shoot): reload the tree. Expanded folders stay expanded.
	let libraryVersionSeen: number | null = null;
	$effect(() => {
		const v = $libraryVersionStore;
		if (v === null) return;
		if (libraryVersionSeen !== null && v !== libraryVersionSeen) api.albums.list().then(a => (albums = a));
		libraryVersionSeen = v;
	});

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

	// Right-click a folder: a scan or a metadata reread of just that folder (and the
	// folders inside it). Progress shows in the status bar like any other scan.
	let menu = $state<{ x: number; y: number; node: AlbumNode } | null>(null);
	const menuItem = 'w-full text-left px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 transition-colors';

	async function refreshAlbum() {
		const m = menu; menu = null;
		if (!m) return;
		try {
			await api.sync.scanLibrary({ custom_path: m.node.path.replace(/^\//, '') });
		} catch (e) {
			notify(`Could not refresh ${m.node.name}: ${e instanceof Error ? e.message : e}`);
		}
	}

	/** Only photos with changes not yet in their files: a whole folder of 70 MB TIFFs would take hours. */
	async function writeAlbum() {
		const m = menu; menu = null;
		if (!m) return;
		try {
			const r = await api.sync.writeMetadata({ album_path: m.node.path, dirty_only: true });
			if (!r.task_id) notify(`Nothing in ${m.node.name} is waiting to be written to its files.`);
		} catch (e) {
			notify(`Could not write ${m.node.name}: ${e instanceof Error ? e.message : e}`);
		}
	}

	async function revealAlbum() {
		const m = menu; menu = null;
		if (!m) return;
		try {
			await api.albums.reveal(m.node.path);
		} catch (e) {
			notify(`Could not open ${m.node.name}: ${e instanceof Error ? e.message : e}`);
		}
	}

	async function rereadAlbum() {
		const m = menu; menu = null;
		if (!m) return;
		try {
			const r = await api.sync.refreshMetadata({ album_path: m.node.path });
			if (!r.task_id) notify(r.message);
		} catch (e) {
			notify(`Could not reread ${m.node.name}: ${e instanceof Error ? e.message : e}`);
		}
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
			<div class="flex items-center gap-0.5 hover:bg-zinc-800 transition-colors" style="padding-left:{depth * 12 + 8}px"
				role="treeitem" aria-selected={selected} tabindex="-1"
				oncontextmenu={(e) => { e.preventDefault(); menu = { x: e.clientX, y: e.clientY, node }; }}>
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

{#if menu}
	<ContextMenu x={menu.x} y={menu.y} onClose={() => menu = null}>
		<div class="px-3 py-1 text-[10px] uppercase tracking-wider text-zinc-500 truncate max-w-[260px]">{menu.node.name}</div>
		<button class={menuItem} onclick={refreshAlbum}
			title="Scan this folder and the folders inside it for new, changed, moved and removed files">Refresh this album</button>
		<button class={menuItem} onclick={rereadAlbum}
			title="Read tags, ratings and captions back from the files, for photos changed outside fernKam">Reread metadata from files</button>
		<button class={menuItem} onclick={writeAlbum}
			title="Write fernKam's tags, ratings and captions into the files, for photos here with changes not yet written (DB → Files for this folder)">Write metadata to files</button>
		<div class="my-1 border-t border-zinc-800"></div>
		<button class={menuItem} onclick={revealAlbum}>Show in File Explorer</button>
	</ContextMenu>
{/if}

<script lang="ts">
import '../app.css';
import { page } from '$app/stores';
import { Images, Tag, Users, FolderOpen, Search, Activity, MapPin, RefreshCw, Power, ScanFace, ZoomIn, Bug, Workflow } from '@lucide/svelte';
import { onMount, onDestroy } from 'svelte';
import { thumbSizeStore, statusCountStore } from '$lib/stores';

let { children } = $props();

let thumbSize = $state(180);
const unsubThumb = thumbSizeStore.subscribe(v => { thumbSize = v; });

let taskMessage = $state('No active process');
let taskRunning = $state(false);
let taskInterval: ReturnType<typeof setInterval>;

async function pollTasks() {
	try {
		const res = await fetch('http://localhost:8000/api/sync/tasks?running_only=1');
		if (!res.ok) return;
		const data = await res.json();
		const running = data.tasks ?? [];
		taskRunning = running.length > 0;
		taskMessage = running.length > 0 ? running[0].message : 'No active process';
	} catch { /* ignore */ }
}

onMount(() => {
	pollTasks();
	taskInterval = setInterval(pollTasks, 3000);
});

onDestroy(() => {
	clearInterval(taskInterval);
	unsubThumb();
});

async function shutdown() {
	if (confirm('Shutdown fernKam?')) {
		try { await fetch('http://localhost:8000/api/shutdown', { method: 'POST' }); } catch { /* expected */ }
	}
}

const navItems = [
	{ href: '/', label: 'Home', icon: Activity, exact: true },
	{ href: '/photos', label: 'Albums', icon: FolderOpen, exact: false },
	{ href: '/tags', label: 'Tags', icon: Tag, exact: false },
	{ href: '/search', label: 'Search', icon: Search, exact: false },
	{ href: '/people', label: 'People', icon: Users, exact: false },
	{ href: '/review', label: 'Face Review', icon: ScanFace, exact: false },
	{ href: '/map', label: 'Map', icon: MapPin, exact: false },
	{ href: '/tasks', label: 'Tasks', icon: RefreshCw, exact: false },
	{ href: '/logs', label: 'Logs', icon: Bug, exact: false },
	{ href: '/workflows', label: 'Workflows', icon: Workflow, exact: false },
];

function isActive(item: typeof navItems[0]) {
	if (item.exact) return $page.url.pathname === item.href;
	return $page.url.pathname.startsWith(item.href);
}
</script>

<div class="flex h-screen overflow-hidden bg-zinc-950">
	<!-- Slim nav rail -->
	<aside class="w-14 shrink-0 bg-zinc-900 border-r border-zinc-800 flex flex-col items-center py-2 gap-0.5 z-10">
		<!-- Logo -->
		<a href="/" class="w-10 h-10 flex items-center justify-center mb-1 text-amber-400 font-bold text-sm tracking-tight hover:text-amber-300 transition-colors" title="fernKam">
			fK
		</a>

		<!-- Nav icons -->
		{#each navItems as item}
			{@const active = isActive(item)}
			{@const Icon = item.icon}
			<a
				href={item.href}
				title={item.label}
				class="relative w-10 h-10 flex items-center justify-center rounded-lg transition-colors group
					{active ? 'bg-amber-500/20 text-amber-400' : 'text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800'}"
			>
				<Icon size={18} />
				<span class="pointer-events-none absolute left-full ml-2 px-2 py-1 rounded bg-zinc-800 text-zinc-200 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity z-50 shadow-lg">
					{item.label}
				</span>
			</a>
		{/each}

		<!-- Spacer -->
		<div class="flex-1"></div>

		<!-- Shutdown -->
		<button
			onclick={shutdown}
			title="Shutdown fernKam"
			class="w-10 h-10 flex items-center justify-center rounded-lg text-zinc-600 hover:text-red-400 hover:bg-zinc-800 transition-colors"
		>
			<Power size={16} />
		</button>
	</aside>

	<!-- Content + status bar -->
	<div class="flex flex-col flex-1 overflow-hidden">
		<main class="flex-1 overflow-hidden bg-zinc-950">
			{@render children()}
		</main>

		<!-- Bottom status bar -->
		<div class="shrink-0 h-8 bg-zinc-900 border-t border-zinc-800 flex items-center px-3 gap-3 text-xs text-zinc-500 select-none">
			<!-- Item count from page context -->
			<span class="text-zinc-600 min-w-0 truncate">{$statusCountStore || ''}</span>

			<!-- Task status (center) -->
			<div class="flex-1 flex items-center justify-center gap-2 min-w-0">
				{#if taskRunning}
					<div class="w-3 h-3 border border-amber-500 border-t-transparent rounded-full animate-spin shrink-0"></div>
					<span class="text-amber-400 truncate">{taskMessage}</span>
				{:else}
					<span class="text-zinc-600 truncate">{taskMessage}</span>
				{/if}
			</div>

			<!-- Thumb size slider (right) -->
			<div class="flex items-center gap-1.5 shrink-0">
				<ZoomIn size={11} class="text-zinc-600" />
				<input
					type="range" min="80" max="400" step="20"
					value={thumbSize}
					oninput={(e) => thumbSizeStore.set(Number(e.currentTarget.value))}
					class="w-20 accent-amber-500 cursor-pointer h-1"
					title="Thumbnail size"
				/>
				<span class="text-zinc-600 w-8 text-right">{thumbSize}px</span>
			</div>
		</div>
	</div>
</div>

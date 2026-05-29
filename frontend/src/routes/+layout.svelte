<script lang="ts">
import '../app.css';
import { page } from '$app/stores';
import { Images, Tag, Users, FolderOpen, Search, Activity, MapPin, RefreshCw, Power, ScanFace } from '@lucide/svelte';

let { children } = $props();

let scanning = $state(false);

async function scanLibrary() {
scanning = true;
try {
await fetch('/api/sync/scan-library', { method: 'POST' });
} catch (e) {
console.error('Scan failed:', e);
} finally {
scanning = false;
}
}

async function shutdown() {
if (confirm('Are you sure you want to shutdown fernKam?')) {
try {
await fetch('/api/shutdown', { method: 'POST' });
} catch (e) {
// Connection will close, ignore error
}
}
}

const navItems = [
{ href: '/', label: 'Home', icon: Activity },
{ href: '/albums', label: 'Albums', icon: FolderOpen },
{ href: '/photos', label: 'All Photos', icon: Images },
{ href: '/tags', label: 'Tags', icon: Tag },
{ href: '/people', label: 'People', icon: Users },
{ href: '/review', label: 'Face Review', icon: ScanFace },
{ href: '/search', label: 'Search', icon: Search },
{ href: '/map', label: 'Map', icon: MapPin },
{ href: '/tasks', label: 'Tasks', icon: RefreshCw },
];
</script>

<div class="flex h-screen overflow-hidden">
<!-- Sidebar -->
<aside class="w-[260px] shrink-0 bg-zinc-900 border-r border-zinc-800 flex flex-col">
<div class="px-5 py-4 border-b border-zinc-800 flex items-center justify-between">
<div class="flex items-center gap-2">
<button
onclick={scanLibrary}
disabled={scanning}
class="p-1.5 rounded hover:bg-zinc-800 transition-colors {scanning ? 'text-zinc-500 animate-spin' : 'text-zinc-400 hover:text-emerald-400'}"
title="Scan library for new photos"
>
<RefreshCw size={16} />
</button>
<a href="/" class="flex items-center gap-2 text-emerald-400 font-semibold text-lg tracking-tight">
<Activity size={20} />
fernKam
</a>
</div>
<button
onclick={shutdown}
class="p-1.5 rounded hover:bg-zinc-800 transition-colors text-zinc-400 hover:text-red-400"
title="Shutdown fernKam"
>
<Power size={16} />
</button>
</div>

<nav class="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
{#each navItems as item}
{@const active = $page.url.pathname.startsWith(item.href)}
{@const Icon = item.icon}
<a
href={item.href}
class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors
{active ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/50'}"
>
<Icon size={16} />
{item.label}
</a>
{/each}
</nav>
<div class="px-5 py-3 border-t border-zinc-800 text-xs text-zinc-600">
fernKam v0.2
</div>
</aside>

<!-- Main content -->
<main class="flex-1 overflow-y-auto bg-zinc-950">
{@render children()}
</main>
</div>

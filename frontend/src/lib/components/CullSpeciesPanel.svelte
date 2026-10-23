<script lang="ts">
	// Cull mode's species picker: what the photo looks like (BioCLIP's nearest tagged photos),
	// a search across every species, and Wildlife's groups to browse (Birds › Herons › ...).
	// Picking applies the tag to the scope chosen here; the page does the applying.
	import { onMount } from 'svelte';
	import { api, type CullPhoto, type CullTag } from '$lib/api';
	import { X, ChevronLeft, Loader } from '@lucide/svelte';

	let {
		info,
		loading = false,
		scope = $bindable<'photo' | 'burst' | 'kept'>('photo'),
		last = null,
		searchEl = $bindable<HTMLInputElement | undefined>(undefined),
		onApply,
		onRemove,
	}: {
		info: CullPhoto | null;
		loading?: boolean;
		scope?: 'photo' | 'burst' | 'kept';
		last?: { id: number; name: string } | null;
		searchEl?: HTMLInputElement;
		onApply: (tag: { id: number; name: string }) => void;
		onRemove: (tagId: number) => void;
	} = $props();

	let groups = $state<CullTag[]>([]);
	let trail = $state<CullTag[]>([]);          // groups opened, outermost first
	let list = $state<CullTag[]>([]);
	let filter = $state('');
	let q = $state('');
	let hits = $state<CullTag[]>([]);
	let searchAbort: AbortController | null = null;

	onMount(async () => { groups = await api.cull.groups(); });

	async function open(t: CullTag) {
		trail = [...trail, t];
		filter = '';
		list = await api.cull.species({ group_id: t.id });
	}
	async function back() {
		trail = trail.slice(0, -1);
		filter = '';
		list = trail.length ? await api.cull.species({ group_id: trail[trail.length - 1].id }) : [];
	}

	let timer: ReturnType<typeof setTimeout>;
	$effect(() => {
		const term = q.trim();
		clearTimeout(timer);
		if (!term) { hits = []; return; }
		timer = setTimeout(async () => {
			searchAbort?.abort();
			searchAbort = new AbortController();
			try { hits = await api.cull.species({ q: term }, searchAbort.signal); } catch { /* superseded */ }
		}, 200);
	});

	const shown = $derived(filter.trim()
		? list.filter(t => t.name.toLowerCase().includes(filter.trim().toLowerCase())) : list);

	function pick(t: CullTag) {
		if (t.kids) { open(t); return; }
		onApply({ id: t.id, name: t.name });
	}

	const btn = 'w-full text-left px-2 py-1 rounded hover:bg-zinc-800 text-zinc-200 truncate';
</script>

<aside class="w-80 shrink-0 border-l border-zinc-800 bg-zinc-950 flex flex-col text-xs text-zinc-300 min-h-0">
	<div class="px-3 pt-2 pb-1 flex items-center justify-between">
		<span class="font-semibold text-zinc-100 text-sm">Species</span>
		<span class="flex rounded overflow-hidden border border-zinc-700" title="What a pick applies to">
			{#each [['photo', 'This photo'], ['burst', 'Burst'], ['kept', 'All kept']] as [k, label] (k)}
				<button onclick={() => scope = k as typeof scope}
					class="px-1.5 py-0.5 text-[10px] {scope === k ? 'bg-violet-600 text-white' : 'text-zinc-400 hover:bg-zinc-800'}">{label}</button>
			{/each}
		</span>
	</div>

	<!-- On this photo now -->
	<div class="px-3 py-1 flex flex-wrap gap-1 min-h-[1.75rem] items-center">
		{#if loading}<Loader size={12} class="animate-spin text-zinc-500" />{/if}
		{#each info?.species ?? [] as s (s.id)}
			<span class="flex items-center gap-1 rounded-full pl-2 pr-1 py-0.5 {s.approved ? 'bg-emerald-700/40 text-emerald-200' : 'bg-zinc-800 text-zinc-400'}"
				title={s.approved ? 'Approved' : 'Unverified (from the file or a workflow)'}>
				{s.name}
				<button onclick={() => onRemove(s.id)} class="rounded-full hover:bg-black/30 p-0.5" title="Remove from this shot"><X size={10} /></button>
			</span>
		{:else}
			{#if !loading}<span class="text-zinc-600">No species yet</span>{/if}
		{/each}
	</div>

	<!-- Looks like -->
	{#if info?.suggestions.length || last}
		<div class="px-3 pt-1 text-[10px] uppercase tracking-wider text-zinc-500">Looks like</div>
		<div class="px-1.5 pb-1">
			{#each info?.suggestions ?? [] as s (s.id)}
				<button class={btn} onclick={() => onApply(s)} title="{s.group} · {Math.round(s.share * 100)}% of the nearest tagged photos">
					{s.name} <span class="text-zinc-500">{Math.round(s.share * 100)}%</span>
				</button>
			{/each}
			{#if last && !info?.suggestions.some(s => s.id === last.id)}
				<button class={btn} onclick={() => last && onApply(last)} title="The species you picked last (key A)">
					<span class="text-zinc-500">Last:</span> {last.name}
				</button>
			{/if}
		</div>
	{/if}

	<!-- Search everything -->
	<div class="px-3 py-1">
		<input bind:this={searchEl} bind:value={q} placeholder="Search species… (S)"
			class="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-violet-500" />
	</div>

	<div class="flex-1 overflow-y-auto px-1.5 pb-2 min-h-0">
		{#if q.trim()}
			{#each hits as t (t.id)}
				<button class={btn} onclick={() => pick(t)}>{t.name} <span class="text-zinc-500">{t.group}</span></button>
			{:else}
				<p class="px-2 py-1 text-zinc-600">No species match. Add new ones in the Tags tab.</p>
			{/each}
		{:else if trail.length}
			<div class="flex items-center gap-1 px-1 py-1 text-zinc-400">
				<button onclick={back} class="p-0.5 rounded hover:bg-zinc-800" title="Back"><ChevronLeft size={14} /></button>
				<span class="truncate">{trail.map(t => t.name).join(' › ')}</span>
			</div>
			{#if list.length > 12}
				<input bind:value={filter} placeholder="Filter {trail[trail.length - 1].name}…"
					class="w-full mb-1 bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none" />
			{/if}
			{#each shown as t (t.id)}
				<button class={btn} onclick={() => pick(t)}>{t.name}{#if t.kids}<span class="text-zinc-500"> › {t.kids}</span>{/if}</button>
			{/each}
		{:else}
			<div class="grid grid-cols-2 gap-1">
				{#each groups as g (g.id)}
					<button class="text-left px-2 py-1 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-200 truncate" onclick={() => open(g)}>
						{g.name} <span class="text-zinc-500">{g.n}</span>
					</button>
				{/each}
			</div>
		{/if}
	</div>
</aside>

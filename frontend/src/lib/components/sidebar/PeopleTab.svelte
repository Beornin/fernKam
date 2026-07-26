<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api, type TagOut } from '$lib/api';
	import { User, Search } from '@lucide/svelte';
	import { buildFilterUrl } from '$lib/shellFilters';

	let selectedPersonTagId = $derived(Number($page.url.searchParams.get('person_tag_id')) || null);

	let people = $state<TagOut[]>([]);
	let loading = $state(true);
	let search = $state('');

	let filtered = $derived(
		search.trim()
			? people.filter(p => p.name.toLowerCase().includes(search.toLowerCase()))
			: people
	);

	async function loadPeople() {
		loading = true;
		const tags = await api.tags.list({ flat: true });
		people = tags.filter(t => t.is_person).sort((a, b) => a.name.localeCompare(b.name));
		loading = false;
	}

	loadPeople();

	function selectPerson(tag: TagOut) {
		goto(buildFilterUrl($page.url, 'people', { person_tag_id: tag.id }));
	}
</script>

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

<div class="flex-1 overflow-y-auto py-1">
	{#if loading}
		<div class="flex items-center justify-center py-8 text-zinc-600 text-xs gap-1.5">
			<div class="w-3 h-3 border border-zinc-600 border-t-amber-400 rounded-full animate-spin"></div>
			Loading…
		</div>
	{:else if filtered.length === 0}
		<div class="flex items-center justify-center py-8 text-zinc-600 text-xs">No people found</div>
	{:else}
		{#each filtered as p (p.id)}
			{@const isSelected = selectedPersonTagId === p.id}
			<button
				onclick={() => selectPerson(p)}
				class="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs transition-colors text-left
					{isSelected ? 'bg-amber-500/15 text-amber-300' : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'}"
			>
				<User size={12} class="shrink-0 {isSelected ? 'text-amber-400' : 'text-sky-400'}" />
				<span class="flex-1 truncate">{p.name}</span>
			</button>
		{/each}
	{/if}
</div>

<script lang="ts">
	import { api, type PersonOut } from '$lib/api';
	import { Search, X, User, Plus } from '@lucide/svelte';

	let {
		open = $bindable(false),
		onPick,
		title = 'Assign to…',
	}: {
		open: boolean;
		onPick: (personId: number) => void | Promise<void>;
		title?: string;
	} = $props();

	let people = $state<PersonOut[]>([]);
	let loaded = $state(false);
	let search = $state('');
	let creating = $state(false);
	let inputEl = $state<HTMLInputElement | undefined>(undefined);

	let filtered = $derived(
		search.trim()
			? people.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()))
			: people
	);

	$effect(() => {
		if (open && !loaded) {
			api.people.list({ limit: 500 }).then((list) => {
				people = list;
				loaded = true;
			});
		}
		if (open) queueMicrotask(() => inputEl?.focus());
	});

	function close() {
		open = false;
		search = '';
	}

	async function pick(id: number) {
		close();
		await onPick(id);
	}

	async function createAndPick() {
		const name = search.trim();
		if (!name || creating) return;
		creating = true;
		try {
			const person = await api.people.create(name);
			people = [...people, person];
			await pick(person.id);
		} catch (e) {
			alert(`Failed to create person: ${e}`);
		} finally {
			creating = false;
		}
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') { e.preventDefault(); close(); }
		else if (e.key === 'Enter' && filtered.length === 0 && search.trim()) { e.preventDefault(); createAndPick(); }
		else if (e.key === 'Enter' && filtered.length > 0) { e.preventDefault(); pick(filtered[0].id); }
	}
</script>

{#if open}
<div class="fixed inset-0 z-50 flex items-center justify-center">
	<button class="absolute inset-0 bg-black/70" aria-label="Close" onclick={close}></button>
	<div class="relative bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-72 flex flex-col max-h-[70vh]" role="dialog" aria-modal="true">
		<div class="p-3 border-b border-zinc-800 flex items-center gap-2">
			<Search size={14} class="text-zinc-500 shrink-0" />
			<input
				bind:this={inputEl}
				type="text"
				bind:value={search}
				placeholder={title}
				onkeydown={onKeydown}
				class="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500"
			/>
			<button onclick={close} class="p-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={16} /></button>
		</div>
		<div class="overflow-y-auto flex-1 p-1">
			{#if !loaded}
				<div class="flex justify-center py-6"><div class="w-4 h-4 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div></div>
			{:else if search.trim() && filtered.length === 0}
				<button onclick={createAndPick} disabled={creating}
					class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left text-amber-400 disabled:opacity-50">
					<Plus size={14} class="shrink-0" />
					<span class="text-sm truncate">{creating ? 'Creating…' : `Create "${search.trim()}"`}</span>
				</button>
			{:else if filtered.length === 0}
				<p class="text-xs text-zinc-500 px-3 py-3">No people yet — type a name to create one</p>
			{:else}
				{#each filtered as person (person.id)}
					<button onclick={() => pick(person.id)} class="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-zinc-800 text-left">
						<div class="w-6 h-6 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0"><User size={12} /></div>
						<span class="text-sm text-zinc-200 flex-1 truncate">{person.name}</span>
						<span class="text-xs text-zinc-500 shrink-0">{person.face_count}</span>
					</button>
				{/each}
			{/if}
		</div>
	</div>
</div>
{/if}

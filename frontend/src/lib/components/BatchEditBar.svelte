<script lang="ts">
	import { api } from '$lib/api';
	import { Star, Tag, X, Check, Pencil, Calendar, MapPin } from '@lucide/svelte';

	let {
		selectedIds = $bindable(new Set<number>()),
		onDone = () => {},
	}: {
		selectedIds?: Set<number>;
		onDone?: () => void;
	} = $props();

	const COLORS = [
		{ val: 0, label: 'None', cls: 'bg-zinc-600' },
		{ val: 1, label: 'Red',    cls: 'bg-red-500' },
		{ val: 2, label: 'Orange', cls: 'bg-orange-500' },
		{ val: 3, label: 'Yellow', cls: 'bg-yellow-400' },
		{ val: 4, label: 'Green',  cls: 'bg-green-500' },
		{ val: 5, label: 'Blue',   cls: 'bg-blue-500' },
		{ val: 6, label: 'Purple', cls: 'bg-purple-500' },
	];

	let saving = $state(false);
	let editRating = $state<number | null>(null);
	let editColor = $state<number | null>(null);
	let editCaption = $state('');
	let showCaption = $state(false);
	let showDate = $state(false);
	let editDate = $state('');
	let showGps = $state(false);
	let editLat = $state('');
	let editLon = $state('');
	let error = $state('');

	async function apply(fields: Record<string, unknown>) {
		if (selectedIds.size === 0) return;
		saving = true; error = '';
		try {
			const r = await api.photos.batchEdit([...selectedIds], fields as any);
			selectedIds = new Set();
			onDone();
		} catch (e: any) {
			error = e.message ?? 'Failed';
		} finally {
			saving = false;
		}
	}

	function setRating(r: number) { editRating = r; apply({ rating: r }); }
	function setColor(c: number) { editColor = c; apply({ color_label: c }); }
	async function applyCaption() { if (editCaption) await apply({ caption: editCaption }); editCaption = ''; showCaption = false; }
	async function applyDate() {
		if (!editDate) return;
		await apply({ taken_at: new Date(editDate).toISOString() });
		editDate = ''; showDate = false;
	}
	async function applyGps() {
		const lat = parseFloat(editLat), lon = parseFloat(editLon);
		if (isNaN(lat) || isNaN(lon)) { error = 'Invalid GPS'; return; }
		await apply({ latitude: lat, longitude: lon });
		editLat = ''; editLon = ''; showGps = false;
	}
</script>

{#if selectedIds.size > 0}
	<div class="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2
		bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl px-4 py-2.5 text-xs">
		<span class="text-amber-400 font-semibold shrink-0">{selectedIds.size} selected</span>
		<span class="w-px h-5 bg-zinc-700 mx-1 shrink-0"></span>

		<!-- Rating -->
		<div class="flex items-center gap-0.5">
			{#each [1,2,3,4,5] as r (r)}
				<button onclick={() => setRating(r)} disabled={saving}
					class="text-zinc-600 hover:text-amber-400 transition-colors disabled:opacity-40"
					title="Set rating {r}">
					<Star size={13} fill={editRating !== null && r <= editRating ? 'currentColor' : 'none'}
						class={editRating !== null && r <= editRating ? 'text-amber-400' : ''} />
				</button>
			{/each}
			<button onclick={() => setRating(0)} disabled={saving} title="Clear rating"
				class="ml-0.5 text-[10px] text-zinc-600 hover:text-zinc-400 transition-colors">✕</button>
		</div>

		<span class="w-px h-5 bg-zinc-700 mx-1 shrink-0"></span>

		<!-- Color label -->
		<div class="flex items-center gap-1">
			{#each COLORS as c (c.val)}
				<button onclick={() => setColor(c.val)} disabled={saving}
					class="w-3.5 h-3.5 rounded-full {c.cls} ring-offset-zinc-900 hover:ring-2 hover:ring-amber-400 ring-offset-1 transition-all disabled:opacity-40"
					title={c.label}></button>
			{/each}
		</div>

		<span class="w-px h-5 bg-zinc-700 mx-1 shrink-0"></span>

		<!-- Caption -->
		{#if showCaption}
			<input type="text" placeholder="Caption for all…" bind:value={editCaption}
				onkeydown={(e) => { if (e.key === 'Enter') applyCaption(); if (e.key === 'Escape') { showCaption = false; editCaption = ''; } }}
				class="w-36 bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-amber-500" />
			<button onclick={applyCaption} disabled={saving || !editCaption}
				class="p-1 text-zinc-400 hover:text-amber-400 disabled:opacity-40 transition-colors">
				<Check size={13} />
			</button>
		{:else}
			<button onclick={() => { showCaption = true; showDate = false; showGps = false; }}
				class="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 transition-colors">
				<Pencil size={12} /> Caption
			</button>
		{/if}

		<span class="w-px h-5 bg-zinc-700 mx-1 shrink-0"></span>

		<!-- Date -->
		{#if showDate}
			<input type="date" bind:value={editDate}
				onkeydown={(e) => { if (e.key === 'Enter') applyDate(); if (e.key === 'Escape') { showDate = false; editDate = ''; } }}
				class="bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-amber-500" />
			<button onclick={applyDate} disabled={saving || !editDate}
				class="p-1 text-zinc-400 hover:text-amber-400 disabled:opacity-40 transition-colors">
				<Check size={13} />
			</button>
		{:else}
			<button onclick={() => { showDate = true; showCaption = false; showGps = false; }}
				class="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 transition-colors">
				<Calendar size={12} /> Date
			</button>
		{/if}

		<span class="w-px h-5 bg-zinc-700 mx-1 shrink-0"></span>

		<!-- GPS -->
		{#if showGps}
			<input type="text" placeholder="lat" bind:value={editLat}
				class="w-20 bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-amber-500" />
			<input type="text" placeholder="lon" bind:value={editLon}
				onkeydown={(e) => { if (e.key === 'Enter') applyGps(); if (e.key === 'Escape') { showGps = false; } }}
				class="w-20 bg-zinc-800 border border-zinc-600 rounded px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-amber-500" />
			<button onclick={applyGps} disabled={saving || !editLat || !editLon}
				class="p-1 text-zinc-400 hover:text-amber-400 disabled:opacity-40 transition-colors">
				<Check size={13} />
			</button>
		{:else}
			<button onclick={() => { showGps = true; showCaption = false; showDate = false; }}
				class="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 transition-colors">
				<MapPin size={12} /> GPS
			</button>
		{/if}

		{#if error}
			<span class="text-red-400 text-[10px] ml-1">{error}</span>
		{/if}

		<span class="w-px h-5 bg-zinc-700 mx-1 shrink-0"></span>

		<button onclick={() => selectedIds = new Set()}
			class="text-zinc-500 hover:text-zinc-300 transition-colors">
			<X size={14} />
		</button>
	</div>
{/if}

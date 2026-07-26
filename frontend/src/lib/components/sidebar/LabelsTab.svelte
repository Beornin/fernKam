<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { Star, X } from '@lucide/svelte';
	import { buildFilterUrl } from '$lib/shellFilters';

	let ratingMin = $derived(Number($page.url.searchParams.get('rating_min')) || null);
	let colorLabel = $derived(Number($page.url.searchParams.get('color_label')) || null);

	const COLOR_LABELS = [
		{ value: 1, name: 'Red',    cls: 'bg-red-500' },
		{ value: 2, name: 'Orange', cls: 'bg-orange-500' },
		{ value: 3, name: 'Yellow', cls: 'bg-yellow-400' },
		{ value: 4, name: 'Green',  cls: 'bg-green-500' },
		{ value: 5, name: 'Blue',   cls: 'bg-blue-500' },
		{ value: 6, name: 'Purple', cls: 'bg-purple-500' },
		{ value: 7, name: 'Gray',   cls: 'bg-zinc-400' },
	];

	function setRating(n: number) {
		const next = ratingMin === n ? undefined : n;
		goto(buildFilterUrl($page.url, 'labels', { rating_min: next, color_label: colorLabel ?? undefined }));
	}

	function setColor(v: number) {
		const next = colorLabel === v ? undefined : v;
		goto(buildFilterUrl($page.url, 'labels', { rating_min: ratingMin ?? undefined, color_label: next }));
	}

	function clearAll() {
		goto(buildFilterUrl($page.url, 'labels', {}));
	}
</script>

<div class="flex-1 overflow-y-auto p-3 space-y-4">
	<div class="flex items-center justify-between">
		<span class="text-[10px] text-zinc-600 uppercase tracking-wider">Quick filters</span>
		{#if ratingMin || colorLabel}
			<button onclick={clearAll} class="text-zinc-600 hover:text-zinc-400 transition-colors" title="Clear">
				<X size={13} />
			</button>
		{/if}
	</div>

	<div>
		<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1.5">Rating (and above)</label>
		<div class="flex items-center gap-1">
			{#each [1, 2, 3, 4, 5] as n}
				<button
					onclick={() => setRating(n)}
					class="p-1 rounded transition-colors {ratingMin && n <= ratingMin ? 'text-amber-400' : 'text-zinc-600 hover:text-zinc-400'}"
					title="{n}+ stars"
				>
					<Star size={18} fill={ratingMin && n <= ratingMin ? 'currentColor' : 'none'} />
				</button>
			{/each}
		</div>
	</div>

	<div>
		<label class="text-[10px] text-zinc-500 uppercase tracking-wider block mb-1.5">Color label</label>
		<div class="flex flex-wrap gap-2">
			{#each COLOR_LABELS as cl}
				<button
					onclick={() => setColor(cl.value)}
					class="w-7 h-7 rounded {cl.cls} transition-all {colorLabel === cl.value ? 'ring-2 ring-white ring-offset-1 ring-offset-zinc-900 scale-110' : 'opacity-70 hover:opacity-100'}"
					title={cl.name}
				></button>
			{/each}
		</div>
	</div>
</div>

<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { onMount } from 'svelte';
	import { CalendarDays, ChevronDown, ChevronRight } from '@lucide/svelte';
	import { buildFilterUrl } from '$lib/shellFilters';

	type MonthEntry = { month: number; count: number };
	type YearEntry = { year: number; count: number; months: MonthEntry[] };

	const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

	let years = $state<YearEntry[]>([]);
	let expandedYears = $state<Set<number>>(new Set());

	let dateFrom = $derived($page.url.searchParams.get('date_from'));
	let dateTo = $derived($page.url.searchParams.get('date_to'));
	let selectedYear = $derived(dateFrom ? Number(dateFrom.slice(0, 4)) : null);
	let selectedMonth = $derived.by(() => {
		if (!dateFrom || !dateTo) return null;
		const isMonthRange = dateFrom.slice(5, 7) === dateTo.slice(5, 7) && dateFrom.slice(8, 10) === '01';
		return isMonthRange ? Number(dateFrom.slice(5, 7)) : null;
	});

	function selectYear(y: number) {
		expandedYears = new Set([...expandedYears, y]);
		goto(buildFilterUrl($page.url, 'timeline', { date_from: `${y}-01-01`, date_to: `${y}-12-31` }));
	}

	function selectMonth(y: number, m: number) {
		const from = `${y}-${String(m).padStart(2, '0')}-01`;
		const last = new Date(y, m, 0).getDate();
		const to = `${y}-${String(m).padStart(2, '0')}-${last}`;
		goto(buildFilterUrl($page.url, 'timeline', { date_from: from, date_to: to }));
	}

	function toggleExpand(y: number) {
		const s = new Set(expandedYears);
		if (s.has(y)) s.delete(y); else s.add(y);
		expandedYears = s;
	}

	onMount(async () => {
		const data = await api.photos.timeline();
		years = data.years;
		if (years.length > 0) {
			expandedYears = new Set([years[0].year]);
			if (!dateFrom) selectYear(years[0].year);
		}
	});
</script>

<div class="flex-1 overflow-y-auto py-1">
	{#each years as yr (yr.year)}
		{@const isSelYear = selectedYear === yr.year && selectedMonth === null}
		{@const expanded = expandedYears.has(yr.year)}
		<div>
			<button
				onclick={() => { toggleExpand(yr.year); selectYear(yr.year); }}
				class="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs transition-colors
					{isSelYear ? 'bg-amber-600/20 text-amber-300' : 'text-zinc-300 hover:bg-zinc-800'}">
				{#if expanded}
					<ChevronDown size={11} class="text-zinc-500 shrink-0" />
				{:else}
					<ChevronRight size={11} class="text-zinc-500 shrink-0" />
				{/if}
				<span class="font-medium flex-1 text-left">{yr.year}</span>
				<span class="text-[10px] text-zinc-500">{yr.count.toLocaleString()}</span>
			</button>
			{#if expanded}
				<div class="pl-5">
					{#each yr.months as mo (mo.month)}
						{@const isSel = selectedYear === yr.year && selectedMonth === mo.month}
						<button
							onclick={() => selectMonth(yr.year, mo.month)}
							class="w-full flex items-center justify-between px-2 py-1 text-[11px] transition-colors
								{isSel ? 'bg-amber-600/20 text-amber-300' : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'}">
							<span>{MONTH_NAMES[mo.month - 1]}</span>
							<span class="text-[10px] text-zinc-600">{mo.count.toLocaleString()}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{/each}
</div>

<script lang="ts">
	import { api, type PhotoSummary } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { onMount } from 'svelte';
	import { CalendarDays, ChevronDown, ChevronRight } from '@lucide/svelte';
	import BatchEditBar from '$lib/components/BatchEditBar.svelte';
	import { statusCountStore } from '$lib/stores';

	type MonthEntry = { month: number; count: number };
	type YearEntry  = { year: number; count: number; months: MonthEntry[] };

	const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

	let years = $state<YearEntry[]>([]);
	let expandedYears = $state<Set<number>>(new Set());
	let selectedYear = $state<number | null>(null);
	let selectedMonth = $state<number | null>(null);

	let photos = $state<PhotoSummary[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let loadingMore = $state(false);
	let cursor = $state<string | null>(null);
	let selectedIds = $state(new Set<number>());
	let lightboxId = $state<number | null>(null);
	let lightboxIdx = $state(-1);
	const PAGE_SIZE = 200;

	function label() {
		if (selectedMonth !== null && selectedYear !== null)
			return `${MONTH_NAMES[selectedMonth - 1]} ${selectedYear}`;
		if (selectedYear !== null) return `${selectedYear}`;
		return null;
	}

	function dateRange() {
		if (selectedMonth !== null && selectedYear !== null) {
			const from = `${selectedYear}-${String(selectedMonth).padStart(2,'0')}-01`;
			const last = new Date(selectedYear, selectedMonth, 0).getDate();
			const to = `${selectedYear}-${String(selectedMonth).padStart(2,'0')}-${last}`;
			return { date_from: from, date_to: to };
		}
		if (selectedYear !== null) {
			return { date_from: `${selectedYear}-01-01`, date_to: `${selectedYear}-12-31` };
		}
		return {};
	}

	async function loadPhotos(reset = true) {
		if (selectedYear === null) return;
		if (reset) { photos = []; total = 0; cursor = null; }
		loading = reset;
		loadingMore = !reset;
		try {
			const range = dateRange();
			const data = await api.photos.list({
				...range,
				sort: 'taken_at_asc',
				page_size: PAGE_SIZE,
				cursor: cursor ?? undefined,
			});
			photos = reset ? data.items : [...photos, ...data.items];
			total = data.total;
			cursor = data.next_cursor ?? null;
			statusCountStore.set(`${total.toLocaleString()} photos · ${label()}`);
		} finally {
			loading = false;
			loadingMore = false;
		}
	}

	async function selectYear(y: number) {
		selectedYear = y;
		selectedMonth = null;
		expandedYears = new Set([...expandedYears, y]);
		await loadPhotos();
	}

	async function selectMonth(y: number, m: number) {
		selectedYear = y;
		selectedMonth = m;
		await loadPhotos();
	}

	function toggleExpand(y: number) {
		const s = new Set(expandedYears);
		if (s.has(y)) s.delete(y); else s.add(y);
		expandedYears = s;
	}

	async function loadMore() {
		if (!cursor || loadingMore) return;
		await loadPhotos(false);
	}

	function openPhoto(p: PhotoSummary) { lightboxId = p.id; lightboxIdx = photos.indexOf(p); }

	onMount(async () => {
		const data = await api.photos.timeline();
		years = data.years;
		if (years.length > 0) {
			await selectYear(years[0].year);
		}
	});
</script>

<div class="flex h-full overflow-hidden">
	<!-- Left: year/month nav -->
	<aside class="w-[180px] shrink-0 border-r border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
		<div class="px-3 py-2 border-b border-zinc-800 shrink-0">
			<span class="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
				<CalendarDays size={12} class="text-amber-400" /> Timeline
			</span>
		</div>
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
	</aside>

	<!-- Right: photo grid -->
	<div class="flex flex-col flex-1 overflow-hidden">
		<div class="shrink-0 px-4 py-2 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
			<span class="text-sm font-semibold text-zinc-200">{label() ?? 'Select a period'}</span>
			{#if !loading && total > 0}
				<span class="text-xs text-zinc-500">
					{total.toLocaleString()} photo{total !== 1 ? 's' : ''}
					{#if photos.length < total}· showing {photos.length.toLocaleString()}{/if}
				</span>
			{/if}
		</div>

		<div class="flex-1 overflow-y-auto" onscroll={(e) => {
			const el = e.currentTarget;
			if (el.scrollHeight - el.scrollTop - el.clientHeight < 600 && cursor && !loadingMore) loadMore();
		}}>
			{#if loading}
				<div class="flex items-center justify-center h-40 gap-2 text-zinc-500">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					Loading…
				</div>
			{:else if photos.length === 0}
				<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-600">
					<CalendarDays size={40} class="opacity-30" />
					<p class="text-sm">Select a year or month</p>
				</div>
			{:else}
				<PhotoGrid {photos} onSelect={openPhoto} bind:selectedIds />
				{#if cursor}
					<div class="flex justify-center py-4">
						<button onclick={loadMore} disabled={loadingMore}
							class="flex items-center gap-2 px-4 py-2 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 disabled:opacity-50 transition-colors">
							<ChevronDown size={13} />
							{loadingMore ? 'Loading…' : `Load more (${(total - photos.length).toLocaleString()} left)`}
						</button>
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>

<BatchEditBar bind:selectedIds />

{#if lightboxId !== null}
	<PhotoLightbox
		photoId={lightboxId}
		onClose={() => { lightboxId = null; lightboxIdx = -1; }}
		onPrev={lightboxIdx > 0 ? () => { lightboxIdx--; lightboxId = photos[lightboxIdx].id; } : undefined}
		onNext={lightboxIdx < photos.length - 1 ? () => { lightboxIdx++; lightboxId = photos[lightboxIdx].id; } : undefined}
	/>
{/if}

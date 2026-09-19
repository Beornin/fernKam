<script lang="ts">
	import type { PhotoSummary } from '$lib/api';
	import { Star, Video, Check } from '@lucide/svelte';
	import { thumbSizeStore } from '$lib/stores';
	import { getThumbSize } from '$lib/thumbUtils';
	import { formatBytes, formatDuration } from '$lib/format';
	import { COLOR_LABEL_CLASS } from '$lib/colorLabels';

	let { photos, onSelect, onContext, selectedIds = $bindable(new Set<number>()) }: {
		photos: PhotoSummary[];
		onSelect?: (p: PhotoSummary) => void;
		onContext?: (p: PhotoSummary, e: MouseEvent) => void;
		selectedIds?: Set<number>;
	} = $props();

	const thumbSize = $derived($thumbSizeStore);

	// ── Row virtualization ──
	// A page can hold up to 500 photos; rendering every tile at once was
	// ~5-8k DOM nodes (and 500 <img> requests) even when only a couple dozen
	// rows are actually on screen. Only the visible row range (+ overscan) is
	// rendered; a full-height spacer keeps the scrollbar/scroll-position correct.
	const GAP = 4; // px — matches `gap-1` (0.25rem)
	const CAPTION_HEIGHT = 44; // px — matches the caption strip's min-height
	const OVERSCAN_ROWS = 3;

	let containerWidth = $state(0);
	let containerHeight = $state(0);
	let scrollTop = $state(0);
	let scrollerEl = $state<HTMLDivElement | undefined>(undefined);

	const imgHeight = $derived(Math.max(60, thumbSize));
	const rowHeight = $derived(imgHeight + CAPTION_HEIGHT + GAP);

	// Layout (columns/row height above) tracks the slider live for a smooth
	// drag. The actual thumbnail *fetch* bucket ('sm'/'md'/'lg'/'xl') is
	// debounced separately — without this, dragging back and forth across a
	// bucket threshold (150/200/300px) re-requests every visible thumbnail on
	// each crossing instead of once after the user settles on a size.
	let debouncedThumbSize = $state(thumbSize);
	let thumbDebounceTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		const size = thumbSize;
		clearTimeout(thumbDebounceTimer);
		thumbDebounceTimer = setTimeout(() => { debouncedThumbSize = size; }, 200);
	});
	// Mirrors the CSS `repeat(auto-fill, minmax(thumbSize, 1fr))` track-fitting
	// formula, since the visible rows below use an explicit column count instead.
	const columns = $derived(Math.max(1, Math.floor((containerWidth + GAP) / (thumbSize + GAP))));
	const totalRows = $derived(columns > 0 ? Math.ceil(photos.length / columns) : 0);
	const totalHeight = $derived(totalRows * rowHeight);

	const firstVisibleRow = $derived(Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN_ROWS));
	const lastVisibleRow = $derived(
		Math.min(totalRows, Math.ceil((scrollTop + containerHeight) / rowHeight) + OVERSCAN_ROWS)
	);

	const visiblePhotos = $derived.by(() => {
		const startIdx = firstVisibleRow * columns;
		const endIdx = Math.min(photos.length, lastVisibleRow * columns);
		return photos.slice(startIdx, endIdx);
	});

	// Reset scroll when the photo list itself changes (new page/filter) so a
	// leftover scrollTop from a longer previous list doesn't leave the grid
	// looking blank.
	$effect(() => {
		void photos;
		scrollTop = 0;
		if (scrollerEl) scrollerEl.scrollTop = 0;
	});

	function handleScroll(e: Event) {
		scrollTop = (e.currentTarget as HTMLDivElement).scrollTop;
	}

	// Select instantly on click; open on dblclick. The previous version deferred
	// selection by 250ms to disambiguate the two, which put visible lag on the
	// single most common action in the app. Shift-click extends a range over the
	// full `photos` array (not just the rendered window), matching every other
	// catalog app.
	let lastClickedId: number | null = null;

	function handlePhotoClick(photo: PhotoSummary, e: MouseEvent) {
		e.stopPropagation();
		if (e.detail > 1) return; // second click of a dblclick — ondblclick opens it
		const next = new Set(selectedIds);
		if (e.shiftKey && lastClickedId !== null) {
			const a = photos.findIndex(p => p.id === lastClickedId);
			const b = photos.findIndex(p => p.id === photo.id);
			if (a !== -1 && b !== -1) {
				for (let i = Math.min(a, b); i <= Math.max(a, b); i++) next.add(photos[i].id);
			}
		} else if (next.has(photo.id)) {
			next.delete(photo.id);
		} else {
			next.add(photo.id);
		}
		selectedIds = next;
		lastClickedId = photo.id;
	}

	function formatDate(dt: string | null): string {
		if (!dt) return '';
		const d = new Date(dt);
		return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
	}

	function getExt(filename: string): string {
		return filename.split('.').pop()?.toUpperCase() ?? '';
	}

	function extBadgeClass(ext: string): string {
		const raw = ['NEF', 'CR2', 'CR3', 'ARW', 'ORF', 'RAF', 'RW2', 'PEF', 'SRW'];
		const video = ['MP4', 'MOV', 'AVI', 'MKV', 'M4V', 'WMV', 'MTS'];
		const dng = ['DNG'];
		if (raw.includes(ext)) return 'bg-blue-700 text-blue-100';
		if (dng.includes(ext)) return 'bg-teal-700 text-teal-100';
		if (video.includes(ext)) return 'bg-purple-700 text-purple-100';
		return 'bg-amber-700 text-amber-100';
	}

</script>

<div
	bind:this={scrollerEl}
	bind:clientWidth={containerWidth}
	bind:clientHeight={containerHeight}
	onscroll={handleScroll}
	class="h-full overflow-y-auto"
>
	<div class="relative" style="height:{totalHeight}px">
		<div
			class="grid gap-1 p-1 absolute left-0 right-0"
			style="grid-template-columns: repeat({columns}, 1fr); top:{firstVisibleRow * rowHeight}px"
		>
			{#each visiblePhotos as photo (photo.id)}
				{@const ext = getExt(photo.filename)}
				{@const selected = selectedIds.has(photo.id)}
				<button
					class="flex flex-col bg-zinc-900 rounded overflow-hidden group focus:outline-none transition-all
						{selected ? 'ring-2 ring-amber-400' : 'ring-1 ring-transparent hover:ring-zinc-700'}"
					onclick={(e) => handlePhotoClick(photo, e)}
					ondblclick={() => onSelect?.(photo)}
					oncontextmenu={(e) => {
						e.preventDefault();
						// Right-clicking outside the selection acts on that one photo,
						// which is what every file manager does.
						if (!selectedIds.has(photo.id)) selectedIds = new Set([photo.id]);
						onContext?.(photo, e);
					}}
				>
					<!-- Thumbnail -->
					<div class="relative overflow-hidden shrink-0" style="height:{imgHeight}px">
						<img
							src="/media/thumbnail/{photo.id}?size={getThumbSize(debouncedThumbSize)}"
							alt={photo.filename}
							class="w-full h-full object-cover transition-transform duration-200 group-hover:scale-105"
							loading="lazy"
							onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display='none'; }}
						/>

						<!-- Amber overlay on selection -->
						{#if selected}
							<div class="absolute inset-0 bg-amber-400/10 pointer-events-none"></div>
						{/if}

						<!-- Selection checkbox -->
						<div class="absolute top-1 left-1 w-4 h-4 rounded bg-black/60 border border-white/30 flex items-center justify-center pointer-events-none">
							{#if selected}
								<Check size={10} class="text-amber-400" />
							{/if}
						</div>

						<!-- Ext badge -->
						<span class="absolute bottom-1 right-1 text-[9px] font-bold px-1 py-0.5 rounded {extBadgeClass(ext)} leading-none">
							{ext}
						</span>

						<!-- Color label dot -->
						{#if photo.color_label > 0}
							<span class="absolute top-1 right-1 w-2 h-2 rounded-full {COLOR_LABEL_CLASS[photo.color_label] ?? 'bg-zinc-500'}"></span>
						{/if}

						<!-- Video icon / duration badge -->
						{#if photo.media_type === 'video'}
							{@const dur = formatDuration(photo.duration_secs)}
							{#if dur}
								<span class="absolute top-1 right-1 flex items-center gap-0.5 bg-black/70 text-white/90 text-[9px] font-mono px-1 py-0.5 rounded leading-none">
									<Video size={9} />{dur}
								</span>
							{:else}
								<span class="absolute top-1 right-1 text-white/80">
									<Video size={12} />
								</span>
							{/if}
						{/if}

						<!-- Rating -->
						{#if photo.rating > 0}
							<div class="absolute bottom-4 left-0 right-0 px-1 flex gap-0.5 pointer-events-none">
								{#each Array(photo.rating) as _}
									<Star size={8} class="fill-yellow-400 text-yellow-400" />
								{/each}
							</div>
						{/if}
					</div>

					<!-- Caption strip -->
					<div class="px-1.5 py-1 bg-zinc-900 text-left" style="min-height:44px">
						<p class="text-[11px] text-zinc-300 truncate leading-tight">{photo.filename}</p>
						<p class="text-[10px] text-zinc-500 leading-tight mt-0.5">{formatDate(photo.taken_at)}</p>
						{#if photo.file_size}
							<p class="text-[10px] text-zinc-600 leading-tight">{formatBytes(photo.file_size)}</p>
						{/if}
					</div>
				</button>
			{/each}
		</div>
	</div>
</div>

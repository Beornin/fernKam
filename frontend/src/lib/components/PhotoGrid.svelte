<script lang="ts">
	import type { PhotoSummary } from '$lib/api';
	import { Star, Video, Check } from '@lucide/svelte';

	let { photos, onSelect, thumbSize = 180, selectedIds = $bindable(new Set<number>()) }: { photos: PhotoSummary[]; onSelect?: (p: PhotoSummary) => void; thumbSize?: number; selectedIds?: Set<number> } = $props();

	const clickTimers = new Map<number, ReturnType<typeof setTimeout>>();

	function handlePhotoClick(photo: PhotoSummary, e: MouseEvent) {
		e.stopPropagation();
		if (e.detail === 2) {
			// Double-click: open photo
			clearTimeout(clickTimers.get(photo.id));
			clickTimers.delete(photo.id);
			onSelect?.(photo);
		} else if (e.detail === 1) {
			// Single-click: debounce to avoid toggling on double-click
			clearTimeout(clickTimers.get(photo.id));
			const timer = setTimeout(() => {
				const newSet = new Set(selectedIds);
				if (newSet.has(photo.id)) {
					newSet.delete(photo.id);
				} else {
					newSet.add(photo.id);
				}
				selectedIds = newSet;
				clickTimers.delete(photo.id);
			}, 250);
			clickTimers.set(photo.id, timer);
		}
	}

	// Select thumbnail size based on display size for better quality
	function getThumbSize(size: number): string {
		if (size >= 300) return 'xl';
		if (size >= 200) return 'lg';
		if (size >= 150) return 'md';
		return 'sm';
	}

	const COLOR_LABELS: Record<number, string> = {
		1: 'bg-red-500',
		2: 'bg-orange-500',
		3: 'bg-yellow-500',
		4: 'bg-green-500',
		5: 'bg-sky-500',
		6: 'bg-indigo-500',
		7: 'bg-fuchsia-500',
	};
</script>

<div class="grid gap-1 p-1" style="grid-template-columns: repeat(auto-fill, minmax({thumbSize}px, 1fr))">
	{#each photos as photo (photo.id)}
		<button
			class="relative aspect-square bg-zinc-900 overflow-hidden rounded group focus:outline-none focus:ring-2 focus:ring-emerald-500 {selectedIds.has(photo.id) ? 'ring-2 ring-emerald-500' : ''}"
			onclick={(e) => handlePhotoClick(photo, e)}
		>
			<img
				src="/media/thumbnail/{photo.id}?size={getThumbSize(thumbSize)}"
				alt={photo.filename}
				class="w-full h-full object-cover transition-transform duration-200 group-hover:scale-105"
				loading="lazy"
				onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display='none'; }}
			/>

			<!-- Selection checkbox -->
			<div
				class="absolute top-1.5 left-1.5 w-5 h-5 rounded bg-black/60 border border-white/40 flex items-center justify-center pointer-events-none"
			>
				{#if selectedIds.has(photo.id)}
					<Check size={12} class="text-emerald-400" />
				{/if}
			</div>

			<!-- Color label dot -->
			{#if photo.color_label > 0}
				<span class="absolute top-1.5 right-1.5 w-2.5 h-2.5 rounded-full {COLOR_LABELS[photo.color_label] ?? 'bg-zinc-500'}"></span>
			{/if}

			<!-- Video badge -->
			{#if photo.media_type === 'video'}
				<span class="absolute bottom-1.5 right-1.5 text-white/80">
					<Video size={14} />
				</span>
			{/if}

			<!-- Rating stars (only if rated) -->
			{#if photo.rating > 0}
				<div class="absolute bottom-0 left-0 right-0 px-1.5 py-1 bg-gradient-to-t from-black/70 to-transparent flex gap-0.5">
					{#each Array(photo.rating) as _}
						<Star size={10} class="fill-yellow-400 text-yellow-400" />
					{/each}
				</div>
			{/if}
		</button>
	{/each}
</div>

<script lang="ts">
	import type { PhotoSummary } from '$lib/api';
	import { Star, Video } from '@lucide/svelte';

	let { photos, onSelect }: { photos: PhotoSummary[]; onSelect?: (p: PhotoSummary) => void } = $props();

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

<div class="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-1 p-1">
	{#each photos as photo (photo.id)}
		<button
			class="relative aspect-square bg-zinc-900 overflow-hidden rounded group focus:outline-none focus:ring-2 focus:ring-emerald-500"
			onclick={() => onSelect?.(photo)}
		>
			<img
				src="/media/thumbnail/{photo.id}?size=sm"
				alt={photo.filename}
				class="w-full h-full object-cover transition-transform duration-200 group-hover:scale-105"
				loading="lazy"
				onerror={(e) => { (e.target as HTMLImageElement).style.display='none'; }}
			/>

			<!-- Color label dot -->
			{#if photo.color_label > 0}
				<span class="absolute top-1.5 left-1.5 w-2.5 h-2.5 rounded-full {COLOR_LABELS[photo.color_label] ?? 'bg-zinc-500'}"></span>
			{/if}

			<!-- Video badge -->
			{#if photo.media_type === 'video'}
				<span class="absolute top-1.5 right-1.5 text-white/80">
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

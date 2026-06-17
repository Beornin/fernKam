<script lang="ts">
	import type { PhotoSummary } from '$lib/api';
	import { Star, Video, Check } from '@lucide/svelte';
	import { thumbSizeStore } from '$lib/stores';

	let { photos, onSelect, selectedIds = $bindable(new Set<number>()) }: {
		photos: PhotoSummary[];
		onSelect?: (p: PhotoSummary) => void;
		selectedIds?: Set<number>;
	} = $props();

	const thumbSize = $derived($thumbSizeStore);

	const clickTimers = new Map<number, ReturnType<typeof setTimeout>>();

	function handlePhotoClick(photo: PhotoSummary, e: MouseEvent) {
		e.stopPropagation();
		if (e.detail === 2) {
			clearTimeout(clickTimers.get(photo.id));
			clickTimers.delete(photo.id);
			onSelect?.(photo);
		} else if (e.detail === 1) {
			clearTimeout(clickTimers.get(photo.id));
			const timer = setTimeout(() => {
				const newSet = new Set(selectedIds);
				if (newSet.has(photo.id)) newSet.delete(photo.id);
				else newSet.add(photo.id);
				selectedIds = newSet;
				clickTimers.delete(photo.id);
			}, 250);
			clickTimers.set(photo.id, timer);
		}
	}

	function getThumbSize(size: number): string {
		if (size >= 300) return 'xl';
		if (size >= 200) return 'lg';
		if (size >= 150) return 'md';
		return 'sm';
	}

	function formatDate(dt: string | null): string {
		if (!dt) return '';
		const d = new Date(dt);
		return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
	}

	function formatSize(bytes: number | null): string {
		if (!bytes) return '';
		if (bytes >= 1_048_576) return (bytes / 1_048_576).toFixed(1) + ' MB';
		if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
		return bytes + ' B';
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

	const COLOR_LABELS: Record<number, string> = {
		1: 'bg-red-500', 2: 'bg-orange-500', 3: 'bg-yellow-500',
		4: 'bg-green-500', 5: 'bg-sky-500', 6: 'bg-indigo-500', 7: 'bg-fuchsia-500',
	};

	const imgHeight = $derived(Math.max(60, thumbSize));
</script>

<div class="grid gap-1 p-1" style="grid-template-columns: repeat(auto-fill, minmax({thumbSize}px, 1fr))">
	{#each photos as photo (photo.id)}
		{@const ext = getExt(photo.filename)}
		{@const selected = selectedIds.has(photo.id)}
		<button
			class="flex flex-col bg-zinc-900 rounded overflow-hidden group focus:outline-none transition-all
				{selected ? 'ring-2 ring-amber-400' : 'ring-1 ring-transparent hover:ring-zinc-700'}"
			onclick={(e) => handlePhotoClick(photo, e)}
		>
			<!-- Thumbnail -->
			<div class="relative overflow-hidden shrink-0" style="height:{imgHeight}px">
				<img
					src="http://localhost:8000/media/thumbnail/{photo.id}?size={getThumbSize(thumbSize)}"
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
					<span class="absolute top-1 right-1 w-2 h-2 rounded-full {COLOR_LABELS[photo.color_label] ?? 'bg-zinc-500'}"></span>
				{/if}

				<!-- Video icon -->
				{#if photo.media_type === 'video'}
					<span class="absolute top-1 right-1 text-white/80">
						<Video size={12} />
					</span>
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
					<p class="text-[10px] text-zinc-600 leading-tight">{formatSize(photo.file_size)}</p>
				{/if}
			</div>
		</button>
	{/each}
</div>

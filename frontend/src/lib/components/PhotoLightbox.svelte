<script lang="ts">
	import { api, type PhotoDetail, type TagOut } from '$lib/api';
	import { X, ChevronLeft, ChevronRight, Star, MapPin, Camera } from '@lucide/svelte';
	import TagPicker from './TagPicker.svelte';

	let {
		photoId,
		onClose,
		onPrev,
		onNext,
	}: {
		photoId: number;
		onClose: () => void;
		onPrev?: () => void;
		onNext?: () => void;
	} = $props();

	let detail = $state<PhotoDetail | null>(null);
	let loading = $state(true);
	let liveTags = $state<TagOut[]>([]);

	$effect(() => {
		if (photoId) {
			loading = true;
			detail = null;
			api.photos.get(photoId).then(d => { detail = d; liveTags = d.tags; loading = false; });
		}
	});

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
		if (e.key === 'ArrowLeft') onPrev?.();
		if (e.key === 'ArrowRight') onNext?.();
	}

	function fmtDate(s: string | null) {
		if (!s) return '—';
		return new Date(s).toLocaleString();
	}

	function fmtSize(bytes: number | null) {
		if (!bytes) return '—';
		return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e3).toFixed(0)} KB`;
	}
</script>

<svelte:window onkeydown={handleKey} />

<!-- Backdrop -->
<div
	class="fixed inset-0 z-[9999] bg-black/90 flex"
	role="dialog"
	aria-modal="true"
>
	<!-- Close -->
	<button
		class="absolute top-4 right-4 text-zinc-400 hover:text-white z-10 p-1 rounded hover:bg-zinc-800"
		onclick={onClose}
	>
		<X size={22} />
	</button>

	<!-- Prev -->
	{#if onPrev}
		<button
			class="absolute left-2 top-1/2 -translate-y-1/2 z-10 p-2 rounded-full bg-black/40 hover:bg-black/70 text-white"
			onclick={onPrev}
		>
			<ChevronLeft size={28} />
		</button>
	{/if}

	<!-- Next -->
	{#if onNext}
		<button
			class="absolute right-[340px] top-1/2 -translate-y-1/2 z-10 p-2 rounded-full bg-black/40 hover:bg-black/70 text-white"
			onclick={onNext}
		>
			<ChevronRight size={28} />
		</button>
	{/if}

	<!-- Image area -->
	<div class="flex-1 flex items-center justify-center p-8 min-w-0">
		{#if loading}
			<div class="w-10 h-10 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
		{:else if detail}
			{#if detail.media_type === 'video'}
				<video
					src={api.media.original(detail.id)}
					controls
					class="max-h-full max-w-full rounded shadow-2xl"
				></video>
			{:else}
				<img
					src={api.media.original(detail.id)}
					alt={detail.filename}
					class="max-h-full max-w-full object-contain rounded shadow-2xl"
				/>
			{/if}
		{/if}
	</div>

	<!-- Details panel -->
	<aside class="w-[320px] shrink-0 bg-zinc-900 border-l border-zinc-800 overflow-y-auto flex flex-col gap-0">
		{#if detail}
			<div class="p-4 border-b border-zinc-800">
				<p class="text-sm font-medium text-zinc-100 break-all">{detail.filename}</p>
				<p class="text-xs text-zinc-500 mt-0.5">{detail.album_path}</p>
			</div>

			<!-- Rating -->
			<div class="px-4 py-3 border-b border-zinc-800 flex items-center gap-2">
				{#each [1,2,3,4,5] as n}
					<button onclick={() => api.photos.patch(detail!.id, { rating: n })}>
						<Star
							size={18}
							class="{detail.rating >= n ? 'fill-yellow-400 text-yellow-400' : 'text-zinc-600'}"
						/>
					</button>
				{/each}
			</div>

			<!-- Metadata rows -->
			<dl class="px-4 py-3 space-y-2 text-sm border-b border-zinc-800">
				<div class="flex justify-between">
					<dt class="text-zinc-500">Taken</dt>
					<dd class="text-zinc-200 text-right">{fmtDate(detail.taken_at)}</dd>
				</div>
				<div class="flex justify-between">
					<dt class="text-zinc-500">Size</dt>
					<dd class="text-zinc-200">{fmtSize(detail.file_size)}</dd>
				</div>
				{#if detail.width && detail.height}
					<div class="flex justify-between">
						<dt class="text-zinc-500">Dimensions</dt>
						<dd class="text-zinc-200">{detail.width} × {detail.height}</dd>
					</div>
				{/if}
				{#if detail.camera}
					<div class="flex justify-between gap-2">
						<dt class="text-zinc-500 flex items-center gap-1"><Camera size={12} /> Camera</dt>
						<dd class="text-zinc-200 text-right">{[detail.camera.make, detail.camera.model].filter(Boolean).join(' ')}</dd>
					</div>
				{/if}
				{#if detail.latitude !== null && detail.longitude !== null}
					<div class="flex justify-between">
						<dt class="text-zinc-500 flex items-center gap-1"><MapPin size={12} /> GPS</dt>
						<dd class="text-zinc-200">{detail.latitude?.toFixed(5)}, {detail.longitude?.toFixed(5)}</dd>
					</div>
				{/if}
			</dl>

			<!-- Tags (editable) -->
			<div class="px-4 py-3 border-b border-zinc-800">
				<p class="text-xs text-zinc-500 mb-2">Tags</p>
				<TagPicker photoId={detail.id} bind:currentTags={liveTags} />
			</div>

			<!-- Caption -->
			{#if detail.caption}
				<div class="px-4 py-3">
					<p class="text-xs text-zinc-500 mb-1">Caption</p>
					<p class="text-sm text-zinc-200">{detail.caption}</p>
				</div>
			{/if}
		{/if}
	</aside>
</div>

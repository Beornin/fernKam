<script lang="ts">
	import { api, type PhotoDetail } from '$lib/api';
	import { Info, Tag, X, ChevronRight, Star, MapPin } from '@lucide/svelte';
	import { goto } from '$app/navigation';

	let { photoId, onClose }: {
		photoId: number | null;
		onClose?: () => void;
	} = $props();

	let detail = $state<PhotoDetail | null>(null);
	let loading = $state(false);
	let activeTab = $state<'info' | 'tags'>('info');

	$effect(() => {
		if (photoId === null) { detail = null; return; }
		loading = true;
		detail = null;
		api.photos.get(photoId).then(d => { detail = d; loading = false; });
	});

	function fmt(v: unknown, unit = '') {
		if (v === null || v === undefined) return '—';
		return String(v) + (unit ? ' ' + unit : '');
	}

	function fmtDate(dt: string | null) {
		if (!dt) return '—';
		return new Date(dt).toLocaleString(undefined, {
			year: 'numeric', month: 'short', day: 'numeric',
			hour: '2-digit', minute: '2-digit',
		});
	}

	function fmtSize(bytes: number | null) {
		if (!bytes) return '—';
		if (bytes >= 1_048_576) return (bytes / 1_048_576).toFixed(2) + ' MB';
		if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
		return bytes + ' B';
	}

	const EXIF_LABELS: Record<string, string> = {
		ExposureTime: 'Shutter',
		FNumber: 'Aperture',
		ISO: 'ISO',
		FocalLength: 'Focal Length',
		FocalLengthIn35mmFormat: '35mm Equiv',
		WhiteBalance: 'White Balance',
		ExposureBiasValue: 'Exp. Bias',
		MeteringMode: 'Metering',
		Flash: 'Flash',
	};

	function fmtShutter(v: unknown): string {
		const n = Number(v);
		if (isNaN(n) || n <= 0) return '—';
		if (n >= 1) return n % 1 === 0 ? `${n}s` : `${n.toFixed(1)}s`;
		const denom = Math.round(1 / n);
		return `1/${denom}`;
	}

	function fmtAperture(v: unknown): string {
		const n = Number(v);
		if (isNaN(n)) return '—';
		return `f/${n % 1 === 0 ? n : n.toFixed(1)}`;
	}

	const WB_NAMES: Record<number, string> = {
		0: 'Auto', 1: 'Manual', 3: 'Incandescent',
		4: 'Fluorescent', 5: 'Flash', 6: 'Cloudy',
		9: 'Fine Weather', 10: 'Shade',
	};

	function fmtWB(v: unknown): string {
		const n = Number(v);
		return WB_NAMES[n] ?? String(v);
	}

	function fmtFocal(v: unknown): string {
		const n = Number(v);
		if (isNaN(n)) return '—';
		return `${n % 1 === 0 ? n : n.toFixed(1)} mm`;
	}

	function fmtExif(key: string, value: unknown): string {
		if (value === null || value === undefined) return '—';
		switch (key) {
			case 'ExposureTime': return fmtShutter(value);
			case 'FNumber': return fmtAperture(value);
			case 'WhiteBalance': return fmtWB(value);
			case 'FocalLength':
			case 'FocalLengthIn35mmFormat': return fmtFocal(value);
			default: return String(value);
		}
	}
</script>

<aside class="w-[270px] shrink-0 border-l border-zinc-800 bg-zinc-900 flex flex-col overflow-hidden">
	<!-- Header -->
	<div class="flex items-center justify-between px-3 py-2 border-b border-zinc-800 shrink-0">
		<div class="flex gap-1">
			<button
				onclick={() => activeTab = 'info'}
				class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
					{activeTab === 'info' ? 'bg-amber-500/20 text-amber-400' : 'text-zinc-500 hover:text-zinc-300'}"
			>
				<Info size={12} /> Info
			</button>
			<button
				onclick={() => activeTab = 'tags'}
				class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
					{activeTab === 'tags' ? 'bg-amber-500/20 text-amber-400' : 'text-zinc-500 hover:text-zinc-300'}"
			>
				<Tag size={12} /> Tags
			</button>
		</div>
		{#if onClose}
			<button onclick={onClose} class="text-zinc-600 hover:text-zinc-300 transition-colors p-1">
				<X size={13} />
			</button>
		{/if}
	</div>

	<div class="flex-1 overflow-y-auto">
		{#if photoId === null}
			<div class="flex flex-col items-center justify-center h-40 text-zinc-700 gap-2">
				<Info size={28} class="opacity-40" />
				<p class="text-xs">Select a photo</p>
			</div>
		{:else if loading}
			<div class="flex items-center justify-center py-12">
				<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
			</div>
		{:else if detail}
			{#if activeTab === 'info'}
				<!-- Thumbnail preview -->
				<div class="p-2 border-b border-zinc-800">
					<img
						src="http://localhost:8000/media/thumbnail/{detail.id}?size=lg"
						alt={detail.filename}
						class="w-full rounded object-cover max-h-40"
					/>
				</div>

				<!-- Rating -->
				<div class="px-3 py-2 border-b border-zinc-800 flex items-center gap-1">
					{#each Array(5) as _, i}
						<Star size={14} class={i < detail.rating ? 'fill-yellow-400 text-yellow-400' : 'text-zinc-700'} />
					{/each}
				</div>

				<!-- File info -->
				<div class="px-3 py-2 border-b border-zinc-800">
					<p class="text-[10px] text-zinc-500 uppercase tracking-wider mb-1.5">File</p>
					<table class="w-full text-xs text-zinc-400">
						<tbody>
							<tr><td class="text-zinc-600 py-0.5 pr-3 w-1/2">Name</td><td class="truncate text-zinc-300 max-w-0 w-1/2" title={detail.filename}>{detail.filename}</td></tr>
							<tr><td class="text-zinc-600 py-0.5 pr-3">Size</td><td class="text-zinc-300">{fmtSize(detail.file_size)}</td></tr>
							<tr><td class="text-zinc-600 py-0.5 pr-3">Dimensions</td><td class="text-zinc-300">{detail.width && detail.height ? `${detail.width} × ${detail.height}` : '—'}</td></tr>
							<tr><td class="text-zinc-600 py-0.5 pr-3">Type</td><td class="text-zinc-300 uppercase">{detail.filename.split('.').pop()}</td></tr>
							<tr><td class="text-zinc-600 py-0.5 pr-3">Taken</td><td class="text-zinc-300">{fmtDate(detail.taken_at)}</td></tr>
						</tbody>
					</table>
				</div>

				<!-- Camera -->
				{#if detail.camera || detail.lens}
					<div class="px-3 py-2 border-b border-zinc-800">
						<p class="text-[10px] text-zinc-500 uppercase tracking-wider mb-1.5">Camera</p>
						<table class="w-full text-xs text-zinc-400">
							<tbody>
								{#if detail.camera}
									<tr><td class="text-zinc-600 py-0.5 pr-3 w-1/2">Make</td><td class="text-zinc-300">{detail.camera.make ?? '—'}</td></tr>
									<tr><td class="text-zinc-600 py-0.5 pr-3">Model</td><td class="text-zinc-300">{detail.camera.model ?? '—'}</td></tr>
								{/if}
								{#if detail.lens?.model}
									<tr><td class="text-zinc-600 py-0.5 pr-3">Lens</td><td class="text-zinc-300">{detail.lens.model}</td></tr>
								{/if}
							</tbody>
						</table>
					</div>
				{/if}

				<!-- EXIF -->
				{#if detail.exif && Object.keys(detail.exif).length > 0}
					<div class="px-3 py-2 border-b border-zinc-800">
						<p class="text-[10px] text-zinc-500 uppercase tracking-wider mb-1.5">EXIF</p>
						<table class="w-full text-xs text-zinc-400">
							<tbody>
								{#each Object.entries(EXIF_LABELS) as [key, label]}
									{#if detail.exif[key] !== undefined}
										<tr><td class="text-zinc-600 py-0.5 pr-3 w-1/2">{label}</td><td class="text-zinc-300">{fmtExif(key, detail.exif[key])}</td></tr>
									{/if}
								{/each}
							</tbody>
						</table>
					</div>
				{/if}

				<!-- GPS -->
				{#if detail.latitude !== null && detail.latitude !== undefined}
					<div class="px-3 py-2">
						<p class="text-[10px] text-zinc-500 uppercase tracking-wider mb-1.5">GPS</p>
						<button
							onclick={() => goto(`/map?lat=${detail!.latitude}&lon=${detail!.longitude}&zoom=14`)}
							class="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300 hover:underline transition-colors"
							title="Show on map"
						>
							<MapPin size={11} />
							{detail.latitude?.toFixed(6)}, {detail.longitude?.toFixed(6)}
						</button>
						{#if detail.altitude}
							<p class="text-xs text-zinc-600 mt-0.5">{Math.round(detail.altitude)}m altitude</p>
						{/if}
					</div>
				{/if}

			{:else if activeTab === 'tags'}
				<div class="px-3 py-2">
					{#if detail.tags.length === 0}
						<p class="text-xs text-zinc-600 py-4 text-center">No tags on this photo</p>
					{:else}
						<div class="flex flex-wrap gap-1.5 mt-1">
							{#each detail.tags as tag}
								<a
									href="/tags"
									class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 transition-colors"
								>
									<Tag size={10} class="text-zinc-500" />
									{tag.name}
								</a>
							{/each}
						</div>
					{/if}
				</div>

				{#if detail.faces.length > 0}
					<div class="px-3 py-2 border-t border-zinc-800">
						<p class="text-[10px] text-zinc-500 uppercase tracking-wider mb-2">People</p>
						<div class="flex flex-wrap gap-2">
							{#each detail.faces as face}
								{#if face.person_name}
									<a href="/people?person_id={face.person_tag_id}" class="flex items-center gap-1.5 text-xs text-zinc-300 hover:text-amber-300 transition-colors">
										<img src="http://localhost:8000/media/face/{face.id}?size=sm" alt={face.person_name} class="w-7 h-7 rounded-full object-cover bg-zinc-800" />
										{face.person_name}
									</a>
								{/if}
							{/each}
						</div>
					</div>
				{/if}
			{/if}
		{/if}
	</div>
</aside>

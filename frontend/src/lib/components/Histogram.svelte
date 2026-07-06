<script lang="ts">
	import { onMount } from 'svelte';

	let { photoId, showClipping = true }: { photoId: number; showClipping?: boolean } = $props();

	let canvas: HTMLCanvasElement;
	let loading = $state(true);
	let error = $state(false);

	// Clipping stats (0–100 percentage)
	let shadowPct = $state(0);
	let highlightPct = $state(0);

	onMount(() => drawHistogram());

	$effect(() => {
		photoId;
		if (canvas) {
			loading = true; error = false;
			shadowPct = 0; highlightPct = 0;
			drawHistogram();
		}
	});

	async function drawHistogram() {
		if (!canvas) return;
		try {
			const img = new Image();
			img.crossOrigin = 'anonymous';
			await new Promise<void>((resolve, reject) => {
				img.onload = () => resolve();
				img.onerror = () => reject();
				img.src = `http://localhost:8000/media/thumbnail/${photoId}?size=sm&_t=${Date.now()}`;
			});

			const off = document.createElement('canvas');
			off.width = img.naturalWidth;
			off.height = img.naturalHeight;
			const ctx = off.getContext('2d')!;
			ctx.drawImage(img, 0, 0);
			const data = ctx.getImageData(0, 0, off.width, off.height).data;

			const buckets = 256;
			const r = new Uint32Array(buckets);
			const g = new Uint32Array(buckets);
			const b = new Uint32Array(buckets);
			const lum = new Uint32Array(buckets);
			let totalPixels = 0;

			for (let i = 0; i < data.length; i += 4) {
				const ri = data[i], gi = data[i+1], bi = data[i+2];
				r[ri]++; g[gi]++; b[bi]++;
				const l = Math.round(0.299 * ri + 0.587 * gi + 0.114 * bi);
				lum[l]++;
				totalPixels++;
			}

			// Clipping: pixels where all channels are clipped (shadow=0, highlight=255)
			const shadowCount = Math.min(r[0], g[0], b[0]);
			const highlightCount = Math.min(r[255], g[255], b[255]);
			shadowPct = totalPixels > 0 ? (shadowCount / totalPixels) * 100 : 0;
			highlightPct = totalPixels > 0 ? (highlightCount / totalPixels) * 100 : 0;

			const W = canvas.width, H = canvas.height;
			const outCtx = canvas.getContext('2d')!;
			outCtx.clearRect(0, 0, W, H);

			// Exclude endpoints from scaling so clipping spikes don't compress the rest
			const maxVal = Math.max(...Array.from(lum).slice(1, 255), 1);

			function drawChannel(arr: Uint32Array, color: string, alpha: number) {
				outCtx.beginPath();
				outCtx.fillStyle = color.replace(')', `, ${alpha})`).replace('rgb', 'rgba');
				for (let i = 0; i < buckets; i++) {
					const x = (i / buckets) * W;
					const h = Math.min((arr[i] / maxVal) * H, H);
					if (i === 0) outCtx.moveTo(x, H);
					outCtx.lineTo(x, H - h);
				}
				outCtx.lineTo(W, H);
				outCtx.closePath();
				outCtx.fill();
			}

			drawChannel(r, 'rgb(239,68,68)', 0.5);
			drawChannel(g, 'rgb(34,197,94)', 0.5);
			drawChannel(b, 'rgb(59,130,246)', 0.5);

			// Luminance line
			outCtx.beginPath();
			outCtx.strokeStyle = 'rgba(255,255,255,0.6)';
			outCtx.lineWidth = 0.8;
			for (let i = 0; i < buckets; i++) {
				const x = (i / buckets) * W;
				const h = Math.min((lum[i] / maxVal) * H, H);
				if (i === 0) outCtx.moveTo(x, H - h);
				else outCtx.lineTo(x, H - h);
			}
			outCtx.stroke();

			// Clipping spikes: draw a thin colored vertical bar at 0 / 255 if clipping exists
			if (showClipping) {
				if (shadowPct > 0.05) {
					const intensity = Math.min(shadowPct / 5, 1);
					outCtx.fillStyle = `rgba(96,165,250,${0.4 + intensity * 0.6})`;
					outCtx.fillRect(0, 0, 3, H);
				}
				if (highlightPct > 0.05) {
					const intensity = Math.min(highlightPct / 5, 1);
					outCtx.fillStyle = `rgba(251,146,60,${0.4 + intensity * 0.6})`;
					outCtx.fillRect(W - 3, 0, 3, H);
				}
			}

			loading = false;
		} catch {
			error = true;
			loading = false;
		}
	}

	function fmtPct(v: number) {
		return v < 0.1 ? '' : v < 1 ? '<1%' : `${Math.round(v)}%`;
	}
</script>

<div class="relative w-full rounded overflow-hidden bg-zinc-950" style="height:64px">
	{#if loading && !error}
		<div class="absolute inset-0 flex items-center justify-center text-zinc-600 text-[10px]">…</div>
	{/if}
	{#if error}
		<div class="absolute inset-0 flex items-center justify-center text-zinc-700 text-[10px]">no data</div>
	{/if}
	<canvas bind:this={canvas} width={256} height={64} class="w-full h-full"></canvas>

	{#if showClipping && !loading && !error}
		{#if shadowPct > 0.05}
			<span
				class="absolute bottom-1 left-1 text-[9px] font-mono leading-none px-1 py-0.5 rounded bg-blue-900/70 text-blue-300"
				title="Shadow clipping: {shadowPct.toFixed(1)}% of pixels"
			>{fmtPct(shadowPct)}</span>
		{/if}
		{#if highlightPct > 0.05}
			<span
				class="absolute bottom-1 right-1 text-[9px] font-mono leading-none px-1 py-0.5 rounded bg-orange-900/70 text-orange-300"
				title="Highlight clipping: {highlightPct.toFixed(1)}% of pixels"
			>{fmtPct(highlightPct)}</span>
		{/if}
	{/if}
</div>

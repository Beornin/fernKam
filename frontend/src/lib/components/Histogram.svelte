<script lang="ts">
	import { onMount } from 'svelte';

	let { photoId }: { photoId: number } = $props();

	let canvas: HTMLCanvasElement;
	let loading = $state(true);
	let error = $state(false);

	onMount(() => drawHistogram());

	$effect(() => {
		photoId;
		if (canvas) {
			loading = true; error = false;
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

			for (let i = 0; i < data.length; i += 4) {
				const ri = data[i], gi = data[i+1], bi = data[i+2];
				r[ri]++; g[gi]++; b[bi]++;
				const l = Math.round(0.299 * ri + 0.587 * gi + 0.114 * bi);
				lum[l]++;
			}

			const W = canvas.width, H = canvas.height;
			const outCtx = canvas.getContext('2d')!;
			outCtx.clearRect(0, 0, W, H);

			const maxVal = Math.max(
				...Array.from(lum).slice(1, 255),
				1
			);

			function drawChannel(arr: Uint32Array, color: string, alpha: number) {
				outCtx.beginPath();
				outCtx.fillStyle = color.replace(')', `, ${alpha})`).replace('rgb', 'rgba');
				for (let i = 0; i < buckets; i++) {
					const x = (i / buckets) * W;
					const h = (arr[i] / maxVal) * H;
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

			outCtx.beginPath();
			outCtx.strokeStyle = 'rgba(255,255,255,0.6)';
			outCtx.lineWidth = 0.8;
			for (let i = 0; i < buckets; i++) {
				const x = (i / buckets) * W;
				const h = (lum[i] / maxVal) * H;
				if (i === 0) outCtx.moveTo(x, H - h);
				else outCtx.lineTo(x, H - h);
			}
			outCtx.stroke();

			loading = false;
		} catch {
			error = true;
			loading = false;
		}
	}
</script>

<div class="relative w-full h-16 rounded overflow-hidden bg-zinc-950">
	{#if loading && !error}
		<div class="absolute inset-0 flex items-center justify-center text-zinc-600 text-[10px]">…</div>
	{/if}
	{#if error}
		<div class="absolute inset-0 flex items-center justify-center text-zinc-700 text-[10px]">no data</div>
	{/if}
	<canvas bind:this={canvas} width={256} height={64} class="w-full h-full"></canvas>
</div>

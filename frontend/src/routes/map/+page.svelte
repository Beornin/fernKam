<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api } from '$lib/api';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';

	let mapEl: HTMLDivElement;
	let loading = $state(true);
	let pointCount = $state(0);
	let selectedId = $state<number | null>(null);
	let leafletMap: import('leaflet').Map | null = null;

	onDestroy(() => { leafletMap?.remove(); });

	// Leaflet is loaded client-side only (no SSR)
	onMount(async () => {
		const L = (await import('leaflet')).default;

		// Fix default marker icon path broken by bundlers
		delete (L.Icon.Default.prototype as any)._getIconUrl;
		L.Icon.Default.mergeOptions({
			iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
			iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
			shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
		});

		leafletMap = L.map(mapEl, { preferCanvas: true }).setView([20, 0], 2);
		const map = leafletMap;

		L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
			attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
			maxZoom: 19,
		}).addTo(map);

		const points = await api.map.points({ limit: 10000 });
		pointCount = points.length;
		loading = false;

		// Use CircleMarker for performance at scale
		const markers = L.featureGroup();
		for (const pt of points) {
			const m = L.circleMarker([pt.lat, pt.lon], {
				radius: 5,
				color: '#10b981',
				fillColor: '#10b981',
				fillOpacity: 0.7,
				weight: 1,
			});
			m.bindTooltip(pt.filename, { direction: 'top', offset: [0, -4] });
			m.on('click', () => { selectedId = pt.id; });
			markers.addLayer(m);
		}
		markers.addTo(map);

		if (points.length > 0) {
			map.fitBounds(markers.getBounds(), { padding: [40, 40], maxZoom: 10 });
		}
	});
</script>

<svelte:head>
	<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
</svelte:head>

<div class="flex flex-col h-full">
	<div class="shrink-0 px-4 py-3 border-b border-zinc-800 flex items-center gap-3">
		<h1 class="text-base font-semibold text-zinc-100">Map</h1>
		{#if loading}
			<span class="text-xs text-zinc-500 flex items-center gap-1.5">
				<span class="w-3 h-3 border border-zinc-600 border-t-emerald-400 rounded-full animate-spin inline-block"></span>
				Loading GPS points…
			</span>
		{:else}
			<span class="text-xs text-zinc-500">{pointCount.toLocaleString()} geotagged photos</span>
		{/if}
	</div>

	<div class="flex-1 relative">
		<div bind:this={mapEl} class="absolute inset-0"></div>
	</div>
</div>

{#if selectedId !== null}
	<PhotoLightbox photoId={selectedId} onClose={() => { selectedId = null; }} />
{/if}

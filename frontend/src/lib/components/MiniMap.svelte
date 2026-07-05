<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	let { lat, lon }: { lat: number; lon: number } = $props();

	let mapEl: HTMLDivElement;
	let leafletMap: import('leaflet').Map | null = null;

	async function init() {
		const L = (await import('leaflet')).default;
		if (!mapEl || leafletMap) return;

		delete (L.Icon.Default.prototype as any)._getIconUrl;
		L.Icon.Default.mergeOptions({
			iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
			iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
			shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
		});

		leafletMap = L.map(mapEl, {
			center: [lat, lon],
			zoom: 13,
			zoomControl: false,
			attributionControl: false,
			dragging: false,
			scrollWheelZoom: false,
			doubleClickZoom: false,
			keyboard: false,
		});

		L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(leafletMap);
		L.marker([lat, lon]).addTo(leafletMap);
	}

	onMount(init);
	onDestroy(() => { leafletMap?.remove(); leafletMap = null; });

	$effect(() => {
		if (leafletMap) {
			leafletMap.setView([lat, lon], 13);
		}
	});
</script>

<svelte:head>
	<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
</svelte:head>

<div bind:this={mapEl} class="w-full h-32 rounded overflow-hidden"></div>

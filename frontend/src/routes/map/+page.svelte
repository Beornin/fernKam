<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	import { api, type PhotoSummary } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { MapPin } from '@lucide/svelte';
	import { statusCountStore } from '$lib/stores';

	let mapEl: HTMLDivElement;
	let loading = $state(true);
	let pointCount = $state(0);
	let leafletMap: import('leaflet').Map | null = null;

	// Panel state
	let clusterPhotos = $state<PhotoSummary[]>([]);
	let clusterLabel = $state('');
	let panelLoading = $state(false);
	let lightboxId = $state<number | null>(null);
	let lightboxIdx = $state(-1);
	let selectedIds = $state(new Set<number>());

	onDestroy(() => { leafletMap?.remove(); });

	onMount(async () => {
		const L = (await import('leaflet')).default;
		await import('leaflet.markercluster');

		delete (L.Icon.Default.prototype as any)._getIconUrl;
		L.Icon.Default.mergeOptions({
			iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
			iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
			shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
		});

		leafletMap = L.map(mapEl, { preferCanvas: false }).setView([20, 0], 2);
		const map = leafletMap;

		L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
			attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
			maxZoom: 19,
		}).addTo(map);

		const points = await api.map.points({ limit: 50000 });
		pointCount = points.length;
		loading = false;
		statusCountStore.set(`${pointCount.toLocaleString()} geotagged photos`);

		// Group points by rounded lat/lon (same location)
		const locationMap = new Map<string, typeof points>();
		for (const pt of points) {
			const key = `${pt.lat.toFixed(4)},${pt.lon.toFixed(4)}`;
			if (!locationMap.has(key)) locationMap.set(key, []);
			locationMap.get(key)!.push(pt);
		}

		// MarkerClusterGroup with amber styling
		const clusterGroup = (L as any).markerClusterGroup({
			maxClusterRadius: 60,
			iconCreateFunction: (cluster: any) => {
				const count = cluster.getChildCount();
				const size = count > 100 ? 40 : count > 10 ? 34 : 28;
				return L.divIcon({
					html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:rgba(245,158,11,0.85);border:2px solid #f59e0b;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#1c1917;box-shadow:0 2px 6px rgba(0,0,0,0.4)">${count}</div>`,
					className: '',
					iconSize: [size, size],
					iconAnchor: [size/2, size/2],
				});
			},
		});

		for (const [, pts] of locationMap) {
			const pt = pts[0];
			const marker = L.circleMarker([pt.lat, pt.lon], {
				radius: pts.length > 1 ? 7 : 5,
				color: '#f59e0b',
				fillColor: '#f59e0b',
				fillOpacity: 0.8,
				weight: 1.5,
			});
			marker.bindTooltip(pts.length > 1 ? `${pts.length} photos here` : pt.filename, { direction: 'top', offset: [0, -4] });
			marker.on('click', async () => {
				const ids = pts.map(p => p.id);
				clusterLabel = pts.length > 1 ? `${pts.length} photos at this location` : pt.filename;
				panelLoading = true;
				clusterPhotos = [];
				try {
					const fetched = await Promise.all(ids.slice(0, 50).map(id => api.photos.get(id)));
					clusterPhotos = fetched as unknown as PhotoSummary[];
				} catch {
					clusterPhotos = [];
				}
				panelLoading = false;
				statusCountStore.set(`${ids.length} photo${ids.length !== 1 ? 's' : ''} at location`);
			});
			clusterGroup.addLayer(marker);
		}

		map.addLayer(clusterGroup);

		const urlParams = new URLSearchParams(window.location.search);
		const urlLat = urlParams.get('lat');
		const urlLon = urlParams.get('lon');
		const urlZoom = urlParams.get('zoom');
		if (urlLat && urlLon) {
			map.setView([parseFloat(urlLat), parseFloat(urlLon)], urlZoom ? parseInt(urlZoom) : 14);
		} else if (points.length > 0) {
			const bounds = L.latLngBounds(points.map(p => [p.lat, p.lon] as [number, number]));
			map.fitBounds(bounds, { padding: [40, 40], maxZoom: 10 });
		}
	});

	function openPhoto(p: PhotoSummary) {
		lightboxId = p.id;
		lightboxIdx = clusterPhotos.indexOf(p);
	}
</script>

<svelte:head>
	<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
	<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
	<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
</svelte:head>

<div class="flex h-full overflow-hidden">
	<!-- Map (left ~55%) -->
	<div class="flex flex-col overflow-hidden" style="flex: 0 0 58%">
		<div class="shrink-0 px-3 py-2 border-b border-zinc-800 flex items-center gap-2 bg-zinc-900/50">
			<MapPin size={13} class="text-amber-400" />
			{#if loading}
				<span class="text-xs text-zinc-500 flex items-center gap-1.5">
					<span class="w-3 h-3 border border-zinc-600 border-t-amber-400 rounded-full animate-spin inline-block"></span>
					Loading GPS points…
				</span>
			{:else}
				<span class="text-xs text-zinc-400">{pointCount.toLocaleString()} geotagged photos</span>
			{/if}
			<span class="ml-auto text-[10px] text-zinc-600">Click a pin to view photos</span>
		</div>
		<div class="flex-1 relative">
			<div bind:this={mapEl} class="absolute inset-0"></div>
		</div>
	</div>

	<!-- Photo panel (right ~45%) -->
	<div class="flex flex-col flex-1 border-l border-zinc-800 overflow-hidden">
		{#if clusterLabel}
			<div class="px-3 py-2 border-b border-zinc-800 bg-zinc-900/50 shrink-0">
				<span class="text-xs text-amber-400 font-medium">{clusterLabel}</span>
			</div>
		{/if}
		<div class="flex-1 overflow-y-auto">
			{#if panelLoading}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm gap-2">
					<div class="w-5 h-5 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
					Loading…
				</div>
			{:else if clusterPhotos.length === 0 && !clusterLabel}
				<div class="flex flex-col items-center justify-center h-full text-zinc-600 gap-3">
					<MapPin size={40} class="opacity-30" />
					<p class="text-sm">Click a pin on the map to view photos</p>
				</div>
			{:else if clusterPhotos.length === 0}
				<div class="flex items-center justify-center h-40 text-zinc-500 text-sm">No photos loaded</div>
			{:else}
				<PhotoGrid photos={clusterPhotos} onSelect={openPhoto} bind:selectedIds />
			{/if}
		</div>
	</div>
</div>

{#if lightboxId !== null}
	<PhotoLightbox
		photoId={lightboxId}
		onClose={() => { lightboxId = null; lightboxIdx = -1; }}
		onPrev={lightboxIdx > 0 ? () => { lightboxIdx--; lightboxId = clusterPhotos[lightboxIdx].id; } : undefined}
		onNext={lightboxIdx < clusterPhotos.length - 1 ? () => { lightboxIdx++; lightboxId = clusterPhotos[lightboxIdx].id; } : undefined}
	/>
{/if}

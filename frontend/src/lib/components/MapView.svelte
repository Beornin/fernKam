<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	import { api, type PhotoSummary } from '$lib/api';
	import PhotoGrid from '$lib/components/PhotoGrid.svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import { MapPin, Globe } from '@lucide/svelte';
	import { statusCountStore } from '$lib/stores';

	let { albumPath = undefined, tagId = undefined }: { albumPath?: string; tagId?: number } = $props();

	let mapEl: HTMLDivElement;
	let loading = $state(true);
	let pointCount = $state(0);
	let leafletMap: import('leaflet').Map | null = null;
	let mapReady = $state(false);
	let leafletLib: any = null;
	let clusterGroup: any = null;
	let accentColor = '#f59e0b';

	// Panel state
	let clusterPhotos = $state<PhotoSummary[]>([]);
	let clusterLabel = $state('');
	let panelLoading = $state(false);
	let lightboxId = $state<number | null>(null);
	let lightboxIdx = $state(-1);
	let selectedIds = $state(new Set<number>());

	let geoStats = $state<{ with_gps: number; geocoded: number; pending: number } | null>(null);
	let geocoding = $state(false);

	async function runGeocode() {
		geocoding = true;
		try {
			const r = await api.geocode.run();
			alert(`Geocoding started (task ${r.task_id}). Check Tasks page for progress.`);
			geoStats = await api.geocode.stats();
		} catch (e: any) {
			alert(e.message ?? 'Failed');
		} finally {
			geocoding = false;
		}
	}

	onDestroy(() => { leafletMap?.remove(); });

	async function loadAndDrawPoints(isInitial: boolean) {
		const L = leafletLib;
		const map = leafletMap;
		if (!L || !map) return;

		loading = true;
		const points = await api.map.points({ album_path: albumPath, tag_id: tagId, limit: 50000 });
		pointCount = points.length;
		loading = false;
		statusCountStore.set(`${pointCount.toLocaleString()} geotagged photos`);

		if (clusterGroup) {
			map.removeLayer(clusterGroup);
			clusterGroup = null;
		}

		// Group points by rounded lat/lon (same location)
		const locationMap = new Map<string, typeof points>();
		for (const pt of points) {
			const key = `${pt.lat.toFixed(4)},${pt.lon.toFixed(4)}`;
			if (!locationMap.has(key)) locationMap.set(key, []);
			locationMap.get(key)!.push(pt);
		}

		// MarkerClusterGroup with theme-accent styling
		const newClusterGroup = (L as any).markerClusterGroup({
			maxClusterRadius: 60,
			iconCreateFunction: (cluster: any) => {
				const count = cluster.getChildCount();
				const size = count > 100 ? 40 : count > 10 ? 34 : 28;
				return L.divIcon({
					html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:color-mix(in oklch, ${accentColor} 85%, transparent);border:2px solid ${accentColor};display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#1c1917;box-shadow:0 2px 6px rgba(0,0,0,0.4)">${count}</div>`,
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
				color: accentColor,
				fillColor: accentColor,
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
			newClusterGroup.addLayer(marker);
		}

		map.addLayer(newClusterGroup);
		clusterGroup = newClusterGroup;

		const urlParams = new URLSearchParams(window.location.search);
		const urlLat = isInitial ? urlParams.get('lat') : null;
		const urlLon = isInitial ? urlParams.get('lon') : null;
		const urlZoom = urlParams.get('zoom');
		if (urlLat && urlLon) {
			map.setView([parseFloat(urlLat), parseFloat(urlLon)], urlZoom ? parseInt(urlZoom) : 14);
		} else if (points.length > 0) {
			const bounds = L.latLngBounds(points.map(p => [p.lat, p.lon] as [number, number]));
			map.fitBounds(bounds, { padding: [40, 40], maxZoom: 10 });
		}
	}

	onMount(async () => {
		const L = (await import('leaflet')).default;
		await import('leaflet.markercluster');
		leafletLib = L;

		delete (L.Icon.Default.prototype as any)._getIconUrl;
		L.Icon.Default.mergeOptions({
			iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
			iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
			shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
		});

		leafletMap = L.map(mapEl, { preferCanvas: false }).setView([20, 0], 2);

		L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
			attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
			maxZoom: 19,
		}).addTo(leafletMap);

		// Read the current theme's accent color live so map markers follow it too.
		accentColor = getComputedStyle(document.documentElement).getPropertyValue('--color-amber-500').trim() || '#f59e0b';

		geoStats = await api.geocode.stats();
		mapReady = true;
	});

	let hasLoadedOnce = false;
	$effect(() => {
		// Re-runs whenever albumPath/tagId change, once the map is ready.
		const _dep = [albumPath, tagId];
		if (mapReady) {
			const isInitial = !hasLoadedOnce;
			hasLoadedOnce = true;
			loadAndDrawPoints(isInitial);
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

<div class="flex flex-1 overflow-hidden">
	<!-- Map (left ~58%) -->
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
			<div class="ml-auto flex items-center gap-2">
				{#if geoStats && geoStats.pending > 0}
					<span class="text-[10px] text-amber-500">{geoStats.pending.toLocaleString()} pending geocode</span>
					<button onclick={runGeocode} disabled={geocoding}
						class="flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50 transition-colors">
						<Globe size={10} /> {geocoding ? 'Starting…' : 'Geocode'}
					</button>
				{:else if geoStats}
					<span class="text-[10px] text-zinc-600">{geoStats.geocoded.toLocaleString()} geocoded</span>
				{/if}
				<span class="text-[10px] text-zinc-600">Click a pin to view photos</span>
			</div>
		</div>
		<div class="flex-1 relative">
			<div bind:this={mapEl} class="absolute inset-0"></div>
		</div>
	</div>

	<!-- Photo panel (right ~42%) -->
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

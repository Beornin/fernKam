<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	// Map now lives as a center-view toggle inside the unified /photos shell —
	// preserve lat/lon/zoom (used to re-center on a specific GPS point) if present.
	onMount(() => {
		const u = new URL('/photos', window.location.origin);
		u.searchParams.set('view', 'map');
		for (const key of ['lat', 'lon', 'zoom']) {
			const v = $page.url.searchParams.get(key);
			if (v) u.searchParams.set(key, v);
		}
		goto(u.toString());
	});
</script>

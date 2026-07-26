<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	// Tags now lives as a sidebar tab inside the unified /photos shell.
	onMount(() => { goto('/photos?tab=tags'); });
</script>

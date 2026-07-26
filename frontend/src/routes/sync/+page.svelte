<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	// Sync/backfill/optimize tools now live together on the Maintenance page.
	onMount(() => { goto('/maintenance'); });
</script>

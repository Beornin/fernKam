<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	// Cluster Review has been folded into the unified Face Review page
	// (select "Unknown faces" in the left sidebar there).
	onMount(() => { goto('/review'); });
</script>

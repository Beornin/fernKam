<script lang="ts">
	import { api, type TagOut } from '$lib/api';

	interface PersonTag extends TagOut { face_count?: number }

	let people = $state<PersonTag[]>([]);
	let loading = $state(true);

	$effect(() => {
		api.tags.list({ flat: true }).then(all => {
			people = all.filter(t => t.is_person);
			loading = false;
		});
	});
</script>

<div class="p-6">
	<h1 class="text-xl font-semibold text-zinc-100 mb-6">People</h1>

	{#if loading}
		<div class="flex items-center gap-2 text-zinc-500 text-sm">
			<div class="w-4 h-4 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
			Loading…
		</div>
	{:else if people.length === 0}
		<p class="text-zinc-500 text-sm">No person tags found. Tag faces in DigiKam to populate this page.</p>
	{:else}
		<div class="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-4">
			{#each people as person (person.id)}
				<a
					href="/photos?tag_id={person.id}"
					class="flex flex-col items-center gap-2 p-4 rounded-xl bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 hover:border-zinc-700 transition-colors group"
				>
					<!-- Avatar placeholder — face thumbnail once embeddings land -->
					<div class="w-16 h-16 rounded-full bg-zinc-800 border-2 border-zinc-700 group-hover:border-emerald-500 transition-colors flex items-center justify-center text-2xl text-zinc-400 font-semibold select-none">
						{person.name[0]?.toUpperCase() ?? '?'}
					</div>
					<span class="text-sm text-zinc-200 text-center font-medium leading-tight line-clamp-2">{person.name}</span>
				</a>
			{/each}
		</div>
	{/if}
</div>

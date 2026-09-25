<script lang="ts">
	import { api, type GbifTaxon } from '$lib/api';
	import { X, Globe, Search } from '@lucide/svelte';

	let {
		open = $bindable(false),
		tagName,
		onLink,
	}: { open: boolean; tagName: string; onLink: (t: GbifTaxon) => void } = $props();

	let q = $state('');
	let results = $state<GbifTaxon[]>([]);
	let loading = $state(false);
	let error = $state('');
	let timer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		if (open) {
			q = tagName;
			run();
		}
	});

	async function run() {
		if (q.trim().length < 2) { results = []; return; }
		loading = true;
		error = '';
		try {
			results = await api.tagReview.speciesSearch(q.trim());
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			results = [];
		} finally {
			loading = false;
		}
	}

	function onInput() {
		clearTimeout(timer);
		timer = setTimeout(run, 350);
	}
</script>

{#if open}
	<div class="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="presentation" onclick={() => open = false}>
		<div class="w-[34rem] max-w-full bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl" role="dialog" aria-label="Link species"
			tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => { if (e.key === 'Escape') open = false; }}>
			<div class="px-4 py-3 border-b border-zinc-800 flex items-center gap-2">
				<Globe size={15} class="text-emerald-400" />
				<h2 class="text-sm font-semibold text-zinc-100">Link “{tagName}” to a species</h2>
				<button class="ml-auto text-zinc-500 hover:text-zinc-200" onclick={() => open = false} aria-label="Close"><X size={15} /></button>
			</div>
			<div class="p-4 space-y-3 text-xs">
				<p class="text-zinc-400">
					fernKam then fetches from GBIF how often this species is recorded around the places you photograph, by month,
					and uses it as a prior: a photo taken where and when it is not recorded is doubted, and flagged.
				</p>
				<div class="relative">
					<Search size={12} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
					<input bind:value={q} oninput={onInput} placeholder="Common or scientific name"
						class="w-full bg-zinc-800 border border-zinc-700 rounded pl-7 pr-2 py-1.5 text-zinc-200 focus:outline-none focus:border-emerald-500" />
				</div>
				{#if error}<p class="text-red-400">{error}</p>{/if}
				{#if loading}<p class="text-zinc-500">Searching GBIF…</p>{/if}
				<div class="max-h-80 overflow-y-auto space-y-1">
					{#each results as t (t.taxon_key)}
						<button onclick={() => { onLink(t); open = false; }}
							class="w-full text-left px-3 py-2 rounded-lg border border-zinc-800 hover:border-emerald-600 hover:bg-zinc-800/60">
							<div class="flex items-baseline gap-2">
								<span class="text-zinc-100 font-medium">{t.common_name ?? t.scientific_name}</span>
								{#if t.common_name}<span class="italic text-zinc-400">{t.scientific_name}</span>{/if}
								<span class="ml-auto text-[10px] uppercase text-zinc-500">{t.rank.toLowerCase()}</span>
							</div>
							<div class="text-zinc-500">{[t.class_name, t.family].filter(Boolean).join(' · ')}{t.synonym ? ' · synonym, uses the accepted name' : ''}</div>
						</button>
					{/each}
					{#if !loading && !error && q.trim().length >= 2 && !results.length}
						<p class="text-zinc-500">Nothing found. Try the scientific name.</p>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}

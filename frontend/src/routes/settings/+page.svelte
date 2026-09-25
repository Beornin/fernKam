<script lang="ts">
	import { notify } from '$lib/dialog.svelte';
	import { onMount } from 'svelte';
	import { Settings as SettingsIcon, Sparkles, Loader2, Palette, FolderSync } from '@lucide/svelte';
	import { api } from '$lib/api';
	import { themeStore, THEMES } from '$lib/stores';

	let sensitivity = $state(0.85);
	let loading = $state(true);
	let saving = $state(false);
	let derived = $state<{ auto_confirm_thresh: number; knn_margin: number; adaptive_floor: number } | null>(null);

	let saveTimer: ReturnType<typeof setTimeout> | undefined;

	async function loadSensitivity() {
		loading = true;
		try {
			const s = await api.faces.getSensitivity();
			sensitivity = s.sensitivity;
			derived = { auto_confirm_thresh: s.auto_confirm_thresh, knn_margin: s.knn_margin, adaptive_floor: s.adaptive_floor };
		} catch (e) {
			// Previously swallowed: the spinner stopped and nothing said why.
			notify(`Could not load settings: ${e}`);
		} finally {
			loading = false;
		}
	}

	function onSlide() {
		if (saveTimer) clearTimeout(saveTimer);
		saveTimer = setTimeout(async () => {
			saving = true;
			try {
				const s = await api.faces.setSensitivity(sensitivity);
				derived = { auto_confirm_thresh: s.auto_confirm_thresh, knn_margin: s.knn_margin, adaptive_floor: s.adaptive_floor };
			} catch (e) {
				// Previously swallowed: the spinner stopped and nothing said why.
				notify(`Could not load on slide: ${e}`);
			} finally {
				saving = false;
			}
		}, 400);
	}

	// Minutes between scans for changes made outside fernKam; 0 = off.
	let watchMinutes = $state<number | null>(null);

	async function saveWatchInterval() {
		if (watchMinutes === null || !(watchMinutes >= 0)) return;
		try {
			watchMinutes = (await api.outsideChanges.setWatchInterval(watchMinutes)).minutes;
		} catch (e) {
			notify(`Could not save the interval: ${e}`);
		}
	}

	onMount(() => {
		loadSensitivity();
		api.outsideChanges.watchInterval().then(r => (watchMinutes = r.minutes)).catch(e => notify(`Could not load the interval: ${e}`));
	});
</script>

<div class="p-8 max-w-3xl mx-auto">
	<div class="mb-8">
		<h1 class="text-3xl font-bold text-zinc-100 mb-2 flex items-center gap-3">
			<SettingsIcon size={32} class="text-zinc-400" />
			Settings
		</h1>
		<p class="text-zinc-400">App-wide preferences.</p>
	</div>

	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 mb-6">
		<div class="flex items-center gap-3 mb-2">
			<div class="p-2 bg-amber-500/10 rounded-lg">
				<Palette size={20} class="text-amber-400" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100">Appearance</h2>
		</div>
		<p class="text-sm text-zinc-400 mb-5">
			Pick an accent color for the whole app — nav highlights, buttons, and links all switch together.
		</p>
		<div class="grid grid-cols-5 gap-3">
			{#each THEMES as t}
				<button
					onclick={() => $themeStore = t.id}
					class="flex flex-col items-center gap-2 p-3 rounded-lg border transition-colors
						{$themeStore === t.id ? 'border-amber-500/60 bg-amber-500/5' : 'border-zinc-800 hover:border-zinc-700 hover:bg-zinc-800/50'}"
				>
					<div data-theme={t.id} class="w-8 h-8 rounded-full bg-amber-500"></div>
					<span class="text-xs text-zinc-300">{t.label}</span>
				</button>
			{/each}
		</div>
	</div>

	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 mb-6">
		<div class="flex items-center gap-3 mb-2">
			<div class="p-2 bg-sky-500/10 rounded-lg">
				<FolderSync size={20} class="text-sky-400" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100">Changes made outside fernKam</h2>
		</div>
		<p class="text-sm text-zinc-400 mb-5">
			fernKam notices files added, edited, moved or deleted by other programs while it runs, and catches up
			with a scan. Changes are collected as they happen; the scan waits this long after the first one, so
			copying a memory card in costs one scan, not dozens. It also scans once at every start.
		</p>
		<label class="flex items-center gap-3 text-sm text-zinc-300">
			Scan for outside changes every
			<input
				type="number" min="0" max="1440" step="1"
				bind:value={watchMinutes}
				onchange={saveWatchInterval}
				disabled={watchMinutes === null}
				class="w-20 bg-zinc-800 border border-zinc-700 rounded-md px-2 py-1 text-zinc-100 font-mono text-right"
			/>
			minutes
			<span class="text-xs text-zinc-500">(0 = off)</span>
		</label>
	</div>

	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
		<div class="flex items-center gap-3 mb-2">
			<div class="p-2 bg-emerald-500/10 rounded-lg">
				<Sparkles size={20} class="text-emerald-400" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100">Face auto-confirm sensitivity</h2>
		</div>
		<p class="text-sm text-zinc-400 mb-5">
			How aggressive the background auto-confirm sweep is when tagging faces. Higher means more faces get
			auto-confirmed with less certainty — you can always review and undo a wrong match from the
			<a href="/review" class="text-emerald-400 hover:text-emerald-300">Face Review</a> audit queue. This is the
			same value the bulk-confirm sliders on that page start from.
		</p>

		{#if loading}
			<div class="flex items-center gap-2 text-zinc-500 text-sm">
				<Loader2 size={16} class="animate-spin" /> Loading…
			</div>
		{:else}
			<div class="flex items-center gap-4">
				<span class="text-xs text-zinc-500 shrink-0">Cautious</span>
				<input
					type="range" min="0" max="1" step="0.01"
					bind:value={sensitivity}
					oninput={onSlide}
					class="flex-1 accent-emerald-400 cursor-pointer"
				/>
				<span class="text-xs text-zinc-500 shrink-0">Aggressive</span>
				<span class="w-14 text-right font-mono text-sm text-zinc-200">{Math.round(sensitivity * 100)}%</span>
			</div>

			<div class="mt-4 flex items-center gap-2 text-xs text-zinc-500 h-4">
				{#if saving}
					<Loader2 size={12} class="animate-spin" /> Saving…
				{:else}
					Saved
				{/if}
			</div>

			{#if derived}
				<div class="mt-4 grid grid-cols-3 gap-3 text-xs">
					<div class="bg-zinc-800/50 rounded-lg p-3">
						<div class="text-zinc-500 mb-1">Confirm threshold</div>
						<div class="font-mono text-zinc-200">{derived.auto_confirm_thresh.toFixed(2)}</div>
					</div>
					<div class="bg-zinc-800/50 rounded-lg p-3">
						<div class="text-zinc-500 mb-1">k-NN margin</div>
						<div class="font-mono text-zinc-200">{derived.knn_margin.toFixed(2)}</div>
					</div>
					<div class="bg-zinc-800/50 rounded-lg p-3">
						<div class="text-zinc-500 mb-1">Adaptive floor</div>
						<div class="font-mono text-zinc-200">{derived.adaptive_floor.toFixed(2)}</div>
					</div>
				</div>
			{/if}
		{/if}
	</div>
</div>

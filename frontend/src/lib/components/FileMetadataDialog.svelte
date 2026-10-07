<script lang="ts">
	/** Rights, credit and usage notes, written straight into the file(s).
	 * Shows the first photo's current values; only fields you change are
	 * written, a blank field is never cleared, and a usage note is added under
	 * the earlier ones. Title, caption, rating and tags are edited elsewhere. */
	import { onMount } from 'svelte';
	import { api, type FileFieldKey, type FileFields } from '$lib/api';
	import { notify } from '$lib/dialog.svelte';

	let { photoIds, onClose, onSaved }: { photoIds: number[]; onClose: () => void; onSaved?: (msg: string) => void } = $props();

	const FIELDS: [FileFieldKey, string, string][] = [
		['creator', 'Creator / photographer', 'Jane Doe'],
		['copyright', 'Copyright', '© Jane Doe'],
		['credit', 'Credit line', 'Photo: Jane Doe'],
		['source', 'Source', 'Original 35 mm slide'],
		['web_statement', 'Rights web page (URL)', 'https://…'],
		['instructions', 'Instructions', 'Do not use without permission'],
	];
	let current = $state<FileFields | null>(null);
	let values = $state<Record<string, string>>({});
	let when = $state(new Date().toISOString().slice(0, 10));
	let who = $state('');
	let usage = $state('');
	let saving = $state(false);

	onMount(async () => {
		try {
			current = await api.photos.fileFields(photoIds[0]);
			values = Object.fromEntries(FIELDS.map(([k]) => [k, current?.[k] ?? '']));
		} catch (e) {
			notify(`Could not read the file: ${e instanceof Error ? e.message : e}`, 'danger');
			onClose();
		}
	});

	async function save() {
		if (!current || saving) return;
		const fields = Object.fromEntries(FIELDS.map(([k]) => [k, values[k].trim()])
			.filter(([k, v]) => v && v !== current![k as FileFieldKey]));
		const note = who.trim() && usage.trim() ? { when, who: who.trim(), usage: usage.trim() } : undefined;
		if (!Object.keys(fields).length && !note) { onClose(); return; }
		saving = true;
		try {
			const r = await api.photos.editFileFields({ photo_ids: photoIds, fields, usage: note });
			const n = `${r.written} file${r.written === 1 ? '' : 's'}`;
			if (r.errors.length) notify(`Written to ${n}; ${r.errors.length} failed: ${r.errors[0]}`, 'danger');
			onSaved?.(`File metadata written to ${n}${note ? ' (usage note added)' : ''}`);
			onClose();
		} catch (e) {
			notify(`Could not write: ${e instanceof Error ? e.message : e}`, 'danger');
		} finally {
			saving = false;
		}
	}
</script>

<div class="fixed inset-0 z-[95] bg-black/60 flex items-center justify-center" role="presentation" onclick={onClose}>
	<div class="w-[620px] max-w-[94vw] max-h-[86vh] overflow-y-auto bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl p-4 flex flex-col gap-3"
		role="dialog" tabindex="-1" onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => { e.stopPropagation(); if (e.key === 'Escape') onClose(); }}>
		<div class="flex items-center gap-3">
			<img src="/media/thumbnail/{photoIds[0]}?size=sm" alt="" class="w-14 h-14 object-cover rounded" />
			<div>
				<p class="text-sm text-zinc-100 font-medium">File metadata{photoIds.length > 1 ? ` · ${photoIds.length} photos` : ''}</p>
				<p class="text-[11px] text-zinc-500">Written into the file. Blank fields are left as they are; nothing is removed.
					{photoIds.length > 1 ? 'Values shown are the first photo\'s; changed ones go to all.' : ''}</p>
			</div>
		</div>
		{#if !current}
			<p class="text-xs text-zinc-500">Reading the file…</p>
		{:else}
			<div class="grid grid-cols-[150px_1fr] gap-x-3 gap-y-1.5 items-center">
				{#each FIELDS as [k, label, ph] (k)}
					<label for="ff-{k}" class="text-xs text-zinc-400">{label}</label>
					<input id="ff-{k}" bind:value={values[k]} placeholder={ph}
						class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-amber-500" />
				{/each}
			</div>

			<div class="border-t border-zinc-800 pt-3">
				<p class="text-xs text-zinc-300 font-medium mb-1">Usage permissions</p>
				{#each current.usage as line}
					<p class="text-[11px] text-zinc-400 leading-snug">• {line}</p>
				{:else}
					<p class="text-[11px] text-zinc-600">None recorded yet.</p>
				{/each}
				<div class="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1.5 items-center mt-2">
					<label for="ff-when" class="text-xs text-zinc-400">Date approved</label>
					<input id="ff-when" type="date" bind:value={when}
						class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 w-40" />
					<label for="ff-who" class="text-xs text-zinc-400">Approved for</label>
					<input id="ff-who" bind:value={who} placeholder="University / person (also becomes a Usage › tag)"
						class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-amber-500" />
					<label for="ff-usage" class="text-xs text-zinc-400">Use approved</label>
					<input id="ff-usage" bind:value={usage} placeholder="Web page https://… and PDF publication “…”"
						class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-amber-500" />
				</div>
			</div>
		{/if}
		<div class="flex justify-end gap-2 pt-1">
			<button onclick={onClose} class="text-xs px-3 py-1.5 rounded text-zinc-400 hover:text-zinc-200">Cancel</button>
			<button onclick={save} disabled={!current || saving}
				class="text-xs px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-zinc-950 font-medium disabled:opacity-50">
				{saving ? 'Writing…' : 'Write to file'}
			</button>
		</div>
	</div>
</div>

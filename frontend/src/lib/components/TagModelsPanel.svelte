<script lang="ts">
	import { api, type TagModelsInfo, type ImageModelInfo } from '$lib/api';
	import { ask, notify } from '$lib/dialog.svelte';
	import { X, Cpu, Eye, Download, Hammer, RefreshCw, Trash2, Type, AlertTriangle, Check, Copy } from '@lucide/svelte';

	let { open = $bindable(false), onChanged }: { open: boolean; onChanged?: () => void } = $props();

	let info = $state<TagModelsInfo | null>(null);
	let loading = $state(false);
	let urlDraft = $state('');
	let started = $state<Record<string, string>>({});

	async function load() {
		loading = true;
		try {
			info = await api.tagReview.models();
			if (!urlDraft) urlDraft = info.vision.url;
		} catch (e) {
			notify(`Could not load models: ${e instanceof Error ? e.message : e}`, 'danger');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		if (!open) return;
		load();
		// Indexing runs in the background; keep the counts moving while open.
		const t = setInterval(load, 4000);
		return () => clearInterval(t);
	});

	function pct(n: number, d: number) { return d ? Math.round((n / d) * 100) : 0; }
	function gb(mb?: number) { return mb ? (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`) : ''; }

	async function install(m: ImageModelInfo) {
		try {
			await api.tagReview.installModel(m.key);
			started = { ...started, [m.key]: m.installed ? 'Indexing new photos…' : (m.source === 'export' ? 'Building from the official weights…' : 'Downloading…') };
			load();
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		}
	}

	async function addText(m: ImageModelInfo) {
		try {
			await api.tagReview.installText(m.key);
			started = { ...started, [m.key]: 'Downloading the text tower…' };
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		}
	}

	async function remove(m: ImageModelInfo) {
		if (!(await ask(`Remove ${m.label}? Its files and its ${m.indexed.toLocaleString()} photo vectors are deleted. Tags relearn without it.`))) return;
		await api.tagReview.removeModel(m.key);
		load();
		onChanged?.();
	}

	async function setVision(body: { model?: string; url?: string }) {
		try {
			await api.tagReview.setVision(body);
			load();
			onChanged?.();
		} catch (e) {
			notify(e instanceof Error ? e.message : String(e), 'danger');
		}
	}

	function copy(textToCopy: string) {
		navigator.clipboard?.writeText(textToCopy).then(() => notify('Copied'));
	}
</script>

{#if open}
	<div class="fixed inset-0 z-40 bg-black/50" role="presentation" onclick={() => open = false}></div>
	<aside class="fixed right-0 top-0 bottom-0 z-50 w-[30rem] max-w-full bg-zinc-900 border-l border-zinc-800 shadow-2xl flex flex-col">
		<div class="px-5 py-3 border-b border-zinc-800 flex items-center gap-2">
			<Cpu size={16} class="text-emerald-400" />
			<h2 class="text-sm font-semibold text-zinc-100">Models</h2>
			{#if info}
				<span class="text-[11px] px-2 py-0.5 rounded-full {info.gpu ? 'bg-emerald-500/10 text-emerald-300' : 'bg-zinc-800 text-zinc-400'}">
					{info.gpu ? 'GPU (CUDA)' : 'CPU only'}
				</span>
			{/if}
			<button class="ml-auto text-zinc-500 hover:text-zinc-200" onclick={() => open = false} aria-label="Close"><X size={16} /></button>
		</div>

		<div class="flex-1 overflow-y-auto p-5 space-y-6 text-xs">
			<section>
				<h3 class="text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Image models</h3>
				<p class="text-zinc-500 mb-3">
					Each model looks at every photo. For each tag, fernKam learns from your decisions how much to trust each one:
					a wildlife model ends up carrying species tags, a general one scenes. Relearn tags after adding one.
				</p>
				{#if !info && loading}
					<p class="text-zinc-500">Loading…</p>
				{/if}
				{#each info?.models ?? [] as m (m.key)}
					{@const covered = pct(m.indexed, info?.photos ?? 0)}
					<div class="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 mb-2">
						<div class="flex items-center gap-2">
							<span class="text-sm text-zinc-100 font-medium">{m.label}</span>
							{#if m.indexed}
								<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300">{covered}% indexed</span>
							{:else if m.installed}
								<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300">installed, not indexed</span>
							{:else}
								<span class="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">not installed</span>
							{/if}
							{#if m.text_installed && m.source !== 'builtin'}
								<span class="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-300" title="Can find photos by a tag's name">finds by name</span>
							{/if}
						</div>
						<p class="text-zinc-400 mt-1">{m.purpose}</p>
						{#if m.indexed}
							<div class="mt-2 h-1 rounded bg-zinc-800 overflow-hidden"><div class="h-full bg-emerald-600" style="width: {covered}%"></div></div>
							<p class="text-zinc-500 mt-1">{m.indexed.toLocaleString()} of {(info?.photos ?? 0).toLocaleString()} photos</p>
						{/if}
						{#if m.gpu_recommended && info && !info.gpu && !m.installed}
							<p class="mt-1 text-amber-300 flex items-center gap-1"><AlertTriangle size={11} /> Slow without a GPU: expect hours for a large library.</p>
						{/if}
						{#if started[m.key]}
							<p class="mt-1 text-emerald-400">{started[m.key]} Progress is on the Tasks page.</p>
						{/if}
						{#if m.source === 'builtin'}
							{#if !m.installed}<p class="mt-1 text-zinc-500">Index it on <a href="/discover" class="underline">Discover</a>.</p>{/if}
						{:else}
							<div class="mt-2 flex flex-wrap gap-2">
								{#if !m.installed}
									{#if m.source === 'download'}
										<button onclick={() => install(m)} class="px-2 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white flex items-center gap-1">
											<Download size={11} /> Install and index ({gb(m.size_mb)})
										</button>
									{:else if info?.uv_available}
										<button onclick={() => install(m)} class="px-2 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white flex items-center gap-1"
											title="Downloads the official weights and PyTorch into a throwaway environment, exports to ONNX once, then indexes">
											<Hammer size={11} /> Build and index (~{gb(m.size_mb)} + PyTorch, once)
										</button>
									{/if}
								{:else if m.indexed < (info?.photos ?? 0)}
									<button onclick={() => install(m)} class="px-2 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-100 flex items-center gap-1">
										<RefreshCw size={11} /> {m.indexed ? 'Index the rest' : 'Index library'}
									</button>
								{/if}
								{#if m.source === 'download' && m.installed && !m.text_installed}
									<button onclick={() => addText(m)} class="px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1"
										title="Lets this model find photos by a tag's name before the tag has approved photos">
										<Type size={11} /> Find by name ({gb(m.text_size_mb)})
									</button>
								{/if}
								{#if m.installed}
									<button onclick={() => remove(m)} class="px-2 py-1 rounded text-zinc-500 hover:text-red-400 flex items-center gap-1 ml-auto">
										<Trash2 size={11} /> Remove
									</button>
								{/if}
							</div>
							{#if m.source === 'export' && !m.installed && m.export_command}
								<div class="mt-2 text-zinc-500">
									{info?.uv_available ? 'Or from a terminal in backend/:' : 'Build it once from a terminal in backend/ (needs uv):'}
									<div class="mt-1 flex items-center gap-1 font-mono text-[11px] bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300">
										<span class="truncate">{m.export_command}</span>
										<button class="ml-auto text-zinc-500 hover:text-zinc-200" onclick={() => copy(m.export_command ?? '')} aria-label="Copy"><Copy size={11} /></button>
									</div>
								</div>
							{/if}
						{/if}
					</div>
				{/each}
			</section>

			<section>
				<h3 class="text-[11px] uppercase tracking-wide text-zinc-500 mb-1 flex items-center gap-1"><Eye size={12} /> Vision model (double-check)</h3>
				<p class="text-zinc-500 mb-3">
					A local vision-language model looks at a photo and answers "does this show a heron?". Its answers appear on each
					photo and are compared with your decisions. They never train anything: only your decisions do.
				</p>
				{#if info}
					<div class="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 space-y-2">
						<div class="flex items-center gap-2">
							<input bind:value={urlDraft} class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 font-mono text-[11px]" />
							<button onclick={() => setVision({ url: urlDraft })} disabled={urlDraft === info.vision.url}
								class="px-2 py-1 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-200 disabled:opacity-40">Save</button>
						</div>
						{#if !info.vision.reachable}
							<p class="text-amber-300 flex items-center gap-1"><AlertTriangle size={11} /> Not reachable. Is Ollama running? (<span class="font-mono">ollama serve</span>)</p>
						{:else if !info.vision.models.length}
							<p class="text-amber-300">Ollama is running but has no vision model. Pull one, e.g. <span class="font-mono">ollama pull qwen2.5vl:7b</span></p>
						{:else}
							<div class="flex items-center gap-2">
								<Check size={12} class="text-emerald-400" />
								<select value={info.vision.model} onchange={(e) => setVision({ model: (e.target as HTMLSelectElement).value })}
									class="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200">
									{#each info.vision.models as vm}
										<option value={vm.name}>{vm.name}{vm.size_gb ? ` · ${vm.size_gb} GB` : ''}</option>
									{/each}
								</select>
							</div>
							{#if info.vision.agreement.judged}
								<p class="text-zinc-400">
									Agreed with your decisions <b class="text-zinc-200">{info.vision.agreement.agreed} of {info.vision.agreement.judged}</b>
									times ({pct(info.vision.agreement.agreed, info.vision.agreement.judged)}%).
								</p>
							{:else}
								<p class="text-zinc-500">Use "Double-check" on a tag's Suggestions to try it.</p>
							{/if}
						{/if}
					</div>
				{/if}
			</section>
		</div>
	</aside>
{/if}

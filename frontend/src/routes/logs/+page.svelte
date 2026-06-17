<script lang="ts">
	import { api, type LogEntry } from '$lib/api';
	import { RefreshCw, Search, Bug, AlertTriangle, X, ChevronDown, ChevronRight, Trash2, Pause, Play } from '@lucide/svelte';

	let items = $state<LogEntry[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let level = $state<string>('WARNING');
	let source = $state<string>('');
	let q = $state<string>('');
	let limit = $state(200);
	let offset = $state(0);
	let tail = $state(true);
	let expanded = $state<Set<number>>(new Set());
	let levelCounts = $state<Array<{ level_name: string; count: number }>>([]);
	let sourceCounts = $state<Array<{ source: string; count: number }>>([]);
	let stats = $state<any>(null);
	let pollHandle: ReturnType<typeof setTimeout> | null = null;

	async function load() {
		loading = true;
		try {
			const params: any = { limit, offset };
			if (level) params.level = level;
			if (source) params.source = source;
			if (q.trim()) params.q = q.trim();
			const [r, c] = await Promise.all([
				api.logs.list(params),
				api.logs.count(params),
			]);
			items = r.items;
			total = c.count;
		} finally {
			loading = false;
		}
	}

	async function loadMeta() {
		try {
			const [lv, src, st] = await Promise.all([
				api.logs.levels(),
				api.logs.sources(),
				api.logs.sinkStats(),
			]);
			levelCounts = lv.levels;
			sourceCounts = src.sources;
			stats = st;
		} catch (_e) {}
	}

	function schedulePoll() {
		if (pollHandle) clearTimeout(pollHandle);
		if (!tail) return;
		pollHandle = setTimeout(async () => {
			if (offset === 0) {
				await load();
				await loadMeta();
			}
			schedulePoll();
		}, 2500);
	}

	$effect(() => {
		// re-run on filter change
		offset = 0;
		load();
		loadMeta();
		schedulePoll();
		return () => { if (pollHandle) clearTimeout(pollHandle); };
	});

	function toggle(id: number) {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id); else next.add(id);
		expanded = next;
	}

	function levelClass(l: string) {
		switch (l) {
			case 'CRITICAL': return 'bg-red-700 text-white';
			case 'ERROR':    return 'bg-red-600/80 text-white';
			case 'WARNING':  return 'bg-amber-600/80 text-white';
			case 'INFO':     return 'bg-zinc-700 text-zinc-200';
			case 'DEBUG':    return 'bg-zinc-800 text-zinc-400';
			default:         return 'bg-zinc-700 text-zinc-200';
		}
	}

	function sourceClass(s: string) {
		switch (s) {
			case 'stderr':  return 'bg-purple-700/40 text-purple-300';
			case 'python':  return 'bg-emerald-700/30 text-emerald-300';
			case 'request': return 'bg-blue-700/30 text-blue-300';
			default:        return 'bg-zinc-700 text-zinc-300';
		}
	}

	function relTime(iso: string) {
		const d = new Date(iso);
		const ms = Date.now() - d.getTime();
		const s = Math.round(ms / 1000);
		if (s < 60) return `${s}s ago`;
		if (s < 3600) return `${Math.round(s / 60)}m ago`;
		if (s < 86400) return `${Math.round(s / 3600)}h ago`;
		return d.toLocaleString();
	}

	async function clearAll() {
		if (!confirm('Delete ALL log rows? This cannot be undone.')) return;
		await api.logs.clear();
		await load();
		await loadMeta();
	}
</script>

<div class="flex flex-col h-full bg-zinc-950">
	<!-- Header -->
	<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900 flex items-center gap-3 flex-wrap">
		<Bug size={16} class="text-amber-400" />
		<span class="text-sm font-medium text-zinc-200">Logs</span>
		<span class="text-xs text-zinc-500">{total.toLocaleString()} matching</span>

		<div class="flex items-center gap-1.5">
			<select bind:value={level} class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-amber-500">
				<option value="">All levels</option>
				<option value="DEBUG">DEBUG</option>
				<option value="INFO">INFO+</option>
				<option value="WARNING">WARNING+</option>
				<option value="ERROR">ERROR+</option>
				<option value="CRITICAL">CRITICAL</option>
			</select>
			<select bind:value={source} class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-amber-500">
				<option value="">All sources</option>
				{#each sourceCounts as sc (sc.source)}
					<option value={sc.source}>{sc.source} ({sc.count})</option>
				{/each}
			</select>
			<div class="relative">
				<Search size={12} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
				<input
					type="text"
					placeholder="Search messages…"
					bind:value={q}
					class="pl-7 pr-2 py-1 w-56 bg-zinc-800 border border-zinc-700 rounded text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500"
				/>
			</div>
		</div>

		<div class="ml-auto flex items-center gap-2">
			<button
				onclick={() => { tail = !tail; schedulePoll(); }}
				class="text-xs px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1"
				title={tail ? 'Pause auto-refresh' : 'Resume auto-refresh'}
			>
				{#if tail}<Pause size={12} /> Tailing{:else}<Play size={12} /> Paused{/if}
			</button>
			<button
				onclick={() => { load(); loadMeta(); }}
				class="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1"
			>
				<RefreshCw size={12} class={loading ? 'animate-spin' : ''} />
				Refresh
			</button>
			<button
				onclick={clearAll}
				class="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-red-700 text-zinc-300 hover:text-white flex items-center gap-1"
				title="Delete all log rows"
			>
				<Trash2 size={12} />
				Clear
			</button>
		</div>
	</div>

	<!-- Stats strip -->
	{#if stats}
		<div class="shrink-0 px-5 py-1.5 border-b border-zinc-800 bg-zinc-900/40 text-[11px] text-zinc-500 flex gap-4">
			<span>queue: <span class="text-zinc-300">{stats.queued}</span></span>
			<span>inserted: <span class="text-zinc-300">{stats.inserted_total}</span></span>
			<span>coalesced: <span class="text-zinc-300">{stats.updated_total}</span></span>
			<span>dropped: <span class="text-zinc-300">{stats.dropped_total}</span></span>
			<span>last flush: <span class="text-zinc-300">{stats.last_flush_ms} ms</span></span>
			<span class={stats.running ? 'text-emerald-400' : 'text-red-400'}>{stats.running ? '● running' : '○ stopped'}</span>
		</div>
	{/if}

	<!-- Table -->
	<div class="flex-1 overflow-y-auto">
		{#if items.length === 0 && !loading}
			<div class="flex flex-col items-center justify-center h-64 gap-3 text-zinc-600">
				<AlertTriangle size={48} />
				<p class="text-sm font-medium text-zinc-400">No log rows match</p>
			</div>
		{/if}
		<table class="w-full text-xs font-mono">
			<thead class="sticky top-0 bg-zinc-900 border-b border-zinc-800 text-zinc-500">
				<tr>
					<th class="text-left px-3 py-2 w-32">Time</th>
					<th class="text-left px-2 py-2 w-24">Level</th>
					<th class="text-left px-2 py-2 w-20">Source</th>
					<th class="text-left px-2 py-2 w-48">Logger</th>
					<th class="text-left px-3 py-2">Message</th>
					<th class="text-right px-3 py-2 w-12">×</th>
				</tr>
			</thead>
			<tbody>
				{#each items as it (it.id)}
					{@const isOpen = expanded.has(it.id)}
					<tr
						class="border-b border-zinc-900 hover:bg-zinc-900/60 cursor-pointer"
						onclick={() => toggle(it.id)}
					>
						<td class="px-3 py-1.5 text-zinc-400" title={it.last_seen_at}>{relTime(it.last_seen_at)}</td>
						<td class="px-2 py-1.5">
							<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold {levelClass(it.level_name)}">{it.level_name}</span>
						</td>
						<td class="px-2 py-1.5">
							<span class="px-1.5 py-0.5 rounded text-[10px] {sourceClass(it.source)}">{it.source}</span>
						</td>
						<td class="px-2 py-1.5 text-zinc-500 truncate max-w-[200px]" title={it.logger_name ?? ''}>{it.logger_name ?? '—'}</td>
						<td class="px-3 py-1.5 text-zinc-200">
							<div class="flex items-start gap-1">
								{#if isOpen}<ChevronDown size={12} class="text-zinc-500 mt-0.5 shrink-0" />{:else}<ChevronRight size={12} class="text-zinc-500 mt-0.5 shrink-0" />{/if}
								<span class="break-all whitespace-pre-wrap">{it.message}</span>
							</div>
						</td>
						<td class="px-3 py-1.5 text-right text-zinc-500">{it.occurrences > 1 ? `×${it.occurrences}` : ''}</td>
					</tr>
					{#if isOpen}
						<tr class="border-b border-zinc-900 bg-zinc-900/30">
							<td colspan="6" class="px-6 py-3 text-zinc-300">
								<div class="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[11px]">
									<div><span class="text-zinc-500">First seen:</span> {it.ts}</div>
									<div><span class="text-zinc-500">Last seen:</span> {it.last_seen_at}</div>
									{#if it.file}<div class="col-span-2"><span class="text-zinc-500">File:</span> {it.file}{#if it.line}:{it.line}{/if}</div>{/if}
									{#if it.func}<div><span class="text-zinc-500">Function:</span> {it.func}</div>{/if}
									<div><span class="text-zinc-500">Fingerprint:</span> {it.fingerprint.slice(0, 16)}…</div>
								</div>
								{#if it.context}
									<div class="mt-2">
										<div class="text-zinc-500 text-[11px] mb-1">Context</div>
										<pre class="bg-zinc-950 border border-zinc-800 rounded p-2 text-[11px] text-emerald-300 overflow-x-auto">{JSON.stringify(it.context, null, 2)}</pre>
									</div>
								{/if}
								{#if it.exc_info}
									<div class="mt-2">
										<div class="text-zinc-500 text-[11px] mb-1">Exception</div>
										<pre class="bg-zinc-950 border border-zinc-800 rounded p-2 text-[11px] text-red-300 overflow-x-auto whitespace-pre-wrap">{it.exc_info}</pre>
									</div>
								{/if}
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>

		<!-- Pagination -->
		{#if total > limit}
			<div class="flex items-center justify-center gap-2 py-4">
				<button
					onclick={() => { offset = Math.max(0, offset - limit); load(); }}
					disabled={offset === 0 || loading}
					class="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 disabled:opacity-40"
				>← Prev</button>
				<span class="text-xs text-zinc-500">{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
				<button
					onclick={() => { offset = offset + limit; load(); }}
					disabled={offset + limit >= total || loading}
					class="px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 disabled:opacity-40"
				>Next →</button>
			</div>
		{/if}
	</div>
</div>

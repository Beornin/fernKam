<script lang="ts">
	import { ask } from '$lib/dialog.svelte';
	import { Workflow, Play, CheckCircle, XCircle, Loader, ChevronDown, ChevronUp, Eye, Layers, HeartPulse } from '@lucide/svelte';
	import { onMount } from 'svelte';

	// ---------------------------------------------------------------------------
	// Types
	// ---------------------------------------------------------------------------
	type WorkflowStatus = 'idle' | 'running' | 'completed' | 'failed';

	interface WorkflowCard {
		id: string;
		label: string;
		description: string;
		color: string;
		fields: { key: string; label: string; placeholder: string; type?: 'checkbox' | 'select'; options?: [string, string][] }[];
		values: Record<string, string | boolean>;
		status: WorkflowStatus;
		taskId: string | null;
		lines: string[];
		expanded: boolean;
		lastDryRun?: boolean;
	}

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------
	let workflows = $state<WorkflowCard[]>([
		{
			id: 'develop-pureraw',
			label: 'Develop with DxO PureRAW',
			description: "Turns RAWs that have no JPG yet into JPGs in each shoot's jpg/ folder, with PureRAW's plugin profile; PureRAW doesn't need to be open. Blank folder = every fresh shoot in AA_RAW (no JPGs yet, finished copying). Settings can do this automatically after each scan.",
			color: 'sky',
			fields: [
				{ key: 'folder', label: 'Shoot folder (blank = every fresh shoot in AA_RAW)', placeholder: 'D:\\Pictures and Videos\\AA_RAW\\20260925_00001' },
				{ key: 'preview', label: "Open PureRAW's settings first (to set or change the profile)", placeholder: '', type: 'checkbox' },
			],
			values: { folder: '', preview: false },
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
		{
			id: 'finish-shoot',
			label: 'Finish Shoot',
			description: "After the cull: a RAW whose JPG you deleted or rejected (X) goes to the Recycle Bin with it. Keepers go to the destination below, except photos you marked P (Portfolio) or L (LLC) in Review Mode. Portfolio: JPG in the folder, RAW in its RAW/. Dates: Ordered by Dates/YYYY/MM. LLC: the JPG leaves the library for CLIENT_FOLDER, and the RAW is filed by date. Nothing is overwritten; clashes and undated files stay in the shoot.",
			color: 'emerald',
			fields: [
				{ key: 'shoot', label: 'Shoot folder', placeholder: 'D:\\Pictures and Videos\\AA_RAW\\20260925_00001' },
				{ key: 'destination', label: 'Keepers go to', placeholder: '', type: 'select',
				  options: [['dates', 'Ordered by Dates (by date taken)'], ['portfolio', 'Portfolio'], ['client', 'LLC folder (CLIENT_FOLDER)']] },
				{ key: 'portfolio_folder', label: 'Portfolio folder (for Portfolio, and photos marked P)', placeholder: 'Portfolio/Frogs' },
			],
			values: { shoot: '', destination: 'dates', portfolio_folder: '' },
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
		{
			id: 'sorting',
			label: 'Sort into Ordered by Dates',
			description: "Moves everything in SORT ME, plus camera videos in AA_RAW, into Ordered by Dates/YYYY/MM by the date taken (Pixel file names, else the camera date via exiftool). A file with no camera date stays in SORT ME and is listed, so it can be dated by hand. Blank fields use the library's folders.",
			color: 'violet',
			fields: [
				{ key: 'raw_dir',      label: 'RAW intake (videos)',  placeholder: 'blank = <library>\\AA_RAW' },
				{ key: 'sort_me_dir',  label: 'Sort me',              placeholder: 'blank = <library>\\AB_TO_SORT\\SORT ME' },
				{ key: 'export_root',  label: 'Sorted into',          placeholder: 'blank = <library>\\Ordered by Dates' },
			],
			values: { raw_dir: '', sort_me_dir: '', export_root: '' },
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
		{
			id: 'remove-nonkeep-raw',
			label: 'Remove Non-Keep RAW',
			description: 'Walks the RAW folder, finds RAW files that have no matching picture (JPG/TIF/etc.), and moves them to the system Trash.',
			color: 'amber',
			fields: [
				{ key: 'starting_folder', label: 'Starting Folder', placeholder: 'D:\\Pictures and Videos\\AA_RAW' },
			],
			values: {
				starting_folder: 'D:\\Pictures and Videos\\AA_RAW',
			},
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
		{
			id: 'move-raws-to-folders',
			label: 'Move RAWs to Folders',
			description: 'Finds RAW files not yet inside a RAW/ subfolder and moves them there (e.g. Album/photo.NEF → Album/RAW/photo.NEF). Updates the catalog automatically.',
			color: 'emerald',
			fields: [
				{ key: 'starting_folder', label: 'Starting Folder (blank = full library)', placeholder: '' },
			],
			values: { starting_folder: '' },
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
		{
			id: 'sync-stack-tags',
			label: 'Sync Stack Tags',
			description: "Detects RAW+JPG stacks across the library, then unions tags, rating, and color label across every stack's members and writes the merged metadata back to each file.",
			color: 'sky',
			fields: [
				{ key: 'album_path', label: 'Album Path (blank = full library)', placeholder: 'Portfolio/Birds' },
			],
			values: { album_path: '' },
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
	]);

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------
	function colorClasses(color: string, part: 'border' | 'icon-bg' | 'icon' | 'btn' | 'badge') {
		const map: Record<string, Record<string, string>> = {
			violet: {
				border:  'border-violet-500/50',
				'icon-bg': 'bg-violet-500/10',
				icon:    'text-violet-400',
				btn:     'bg-violet-600 hover:bg-violet-500',
				badge:   'bg-violet-500/20 text-violet-300',
			},
			amber: {
				border:  'border-amber-500/50',
				'icon-bg': 'bg-amber-500/10',
				icon:    'text-amber-400',
				btn:     'bg-amber-600 hover:bg-amber-500',
				badge:   'bg-amber-500/20 text-amber-300',
			},
			emerald: {
				border:  'border-emerald-500/50',
				'icon-bg': 'bg-emerald-500/10',
				icon:    'text-emerald-400',
				btn:     'bg-emerald-600 hover:bg-emerald-500',
				badge:   'bg-emerald-500/20 text-emerald-300',
			},
			sky: {
				border:  'border-sky-500/50',
				'icon-bg': 'bg-sky-500/10',
				icon:    'text-sky-400',
				btn:     'bg-sky-600 hover:bg-sky-500',
				badge:   'bg-sky-500/20 text-sky-300',
			},
		};
		return map[color]?.[part] ?? '';
	}

	let pollTimers: Record<string, ReturnType<typeof setInterval>> = {};


	// ── Pipeline stages (3.1) + RAW health (3.4) ────────────────────────────
	interface Stage {
		folder: string; kind: string; blurb: string;
		catalogued: number; videos: number; rated: number; bytes: number;
		on_disk: number | null;
	}
	let stages = $state<Stage[]>([]);
	let health = $state<{ raw_total: number; orphan_raw: number; unstacked_pairs: number; lone_pics: number } | null>(null);
	let loadingOverview = $state(true);

	const fmtGB = (b: number) => b >= 1e12 ? `${(b / 1e12).toFixed(1)} TB` : `${(b / 1e9).toFixed(0)} GB`;

	onMount(async () => {
		try {
			const [p, h] = await Promise.all([
				fetch('/api/workflows/pipeline').then(r => r.json()),
				fetch('/api/workflows/raw-health').then(r => r.json()),
			]);
			stages = p.stages ?? [];
			health = h;
		} catch { /* overview is advisory; the workflows below still work */ }
		finally { loadingOverview = false; }
	});

	async function runWorkflow(wf: WorkflowCard, dryRun = true) {
		// These workflows trash and relocate originals, so Apply is a separate,
		// explicit action and Preview is what the plain button does.
		if (!dryRun && !(await ask(`${wf.label}: this modifies files on disk. Run for real?`))) return;
		wf.status = 'running';
		wf.lines = [];
		wf.taskId = null;
		wf.expanded = true;
		wf.lastDryRun = dryRun;

		const endpoint = `/api/workflows/run/${wf.id}`;
		try {
			const res = await fetch(endpoint, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ ...wf.values, dry_run: dryRun }),
			});
			const data = await res.json();
			if (!res.ok) throw new Error(data.detail ?? JSON.stringify(data));
			wf.taskId = data.task_id;
			// Start polling
			pollTimers[wf.id] = setInterval(() => pollTask(wf), 1500);
		} catch (e) {
			wf.status = 'failed';
			wf.lines = [`ERROR: ${e}`];
		}
	}

	// Finish shoot: suggest where the keepers go, from the photos they look like.
	let suggesting = $state(false);
	async function suggestFinish(wf: WorkflowCard) {
		const shoot = String(wf.values.shoot ?? '').trim();
		if (!shoot) { wf.lines = ['Enter the shoot folder first.']; wf.expanded = true; return; }
		suggesting = true;
		try {
			const r = await fetch(`/api/workflows/shoot-suggestion?shoot=${encodeURIComponent(shoot)}`);
			const d = await r.json();
			if (!r.ok) throw new Error(d.detail ?? r.statusText);
			wf.values.destination = d.destination;
			if (d.portfolio_folder) wf.values.portfolio_folder = d.portfolio_folder;
			wf.lines = [`Suggested ${d.destination === 'portfolio' ? 'Portfolio' : 'Ordered by Dates'}: ` +
				`${Math.round(d.portfolio_share * 100)}% of the filed photos these ${d.sampled} look like are in Portfolio` +
				(d.portfolio_folder ? `, most in ${d.portfolio_folder}` : '') + '. LLC work: pick it yourself.'];
			wf.expanded = true;
		} catch (e) {
			wf.lines = [`ERROR: ${e}`]; wf.expanded = true;
		} finally { suggesting = false; }
	}

	async function pollTask(wf: WorkflowCard) {
		if (!wf.taskId) return;
		try {
			const res = await fetch(`/api/workflows/task/${wf.taskId}`);
			if (!res.ok) return;
			const data = await res.json();
			// While it runs, show the task's live message (e.g. "PureRAW: 40/114 developed").
			wf.lines = data.lines?.length ? data.lines : data.message ? [data.message] : [];
			if (data.status === 'completed' || data.status === 'failed') {
				wf.status = data.status;
				clearInterval(pollTimers[wf.id]);
				delete pollTimers[wf.id];
			}
		} catch { /* ignore transient errors */ }
	}
</script>

<div class="p-8 max-w-4xl mx-auto">
	<div class="mb-8">
		<h1 class="text-3xl font-bold text-zinc-100 mb-2 flex items-center gap-3">
			<Workflow size={32} class="text-violet-400" />
			Workflows
		</h1>
		<p class="text-zinc-400">File-system automation workflows</p>
	</div>

	<!-- 3.1 pipeline stages: where everything currently sits -->
	<section class="mb-6">
		<h2 class="text-sm font-semibold text-zinc-300 flex items-center gap-2 mb-3">
			<Layers size={15} class="text-amber-400" /> Pipeline
		</h2>
		{#if loadingOverview}
			<p class="text-xs text-zinc-600">Loading…</p>
		{:else}
			<div class="grid gap-2" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr))">
				{#each stages as st}
					<!-- Clicking a stage opens its album, which is where the cull
					     keys live — otherwise you read a count here and then go
					     hunt for the folder in the tree. -->
					<a
						href="/photos?tab=albums&album_path={encodeURIComponent(st.folder)}"
						title="Open {st.folder} to review and cull"
						class="block text-left bg-zinc-900 border rounded-lg p-3 hover:border-amber-600 transition-colors
						{st.catalogued > 0 && (st.kind === 'intake' || st.kind === 'staging')
							? 'border-amber-700/60' : 'border-zinc-800'}">
						<div class="text-[11px] uppercase tracking-wider text-zinc-500">{st.kind}</div>
						<div class="text-sm font-semibold text-zinc-200 truncate" title={st.folder}>{st.folder}</div>
						<div class="text-lg font-semibold text-zinc-100 mt-1">{st.catalogued.toLocaleString()}</div>
						<div class="text-[11px] text-zinc-500">
							{fmtGB(st.bytes)}{#if st.videos > 0} · {st.videos.toLocaleString()} video{/if}
						</div>
						{#if st.on_disk !== null && st.on_disk !== st.catalogued}
							<div class="text-[11px] text-amber-500 mt-1">{st.on_disk.toLocaleString()} on disk — catalogue drift</div>
						{/if}
					</a>
				{/each}
			</div>
		{/if}
	</section>

	<!-- 3.4 RAW/JPEG pairing health -->
	{#if health}
		<section class="mb-8">
			<h2 class="text-sm font-semibold text-zinc-300 flex items-center gap-2 mb-3">
				<HeartPulse size={15} class="text-amber-400" /> RAW health
			</h2>
			<div class="grid gap-2" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr))">
				<div class="bg-zinc-900 border border-zinc-800 rounded-lg p-3">
					<div class="text-lg font-semibold text-zinc-100">{health.raw_total.toLocaleString()}</div>
					<div class="text-[11px] text-zinc-500">RAW files</div>
				</div>
				<div class="bg-zinc-900 border {health.orphan_raw > 0 ? 'border-amber-700/60' : 'border-zinc-800'} rounded-lg p-3">
					<div class="text-lg font-semibold text-zinc-100">{health.orphan_raw.toLocaleString()}</div>
					<div class="text-[11px] text-zinc-500">orphan RAW — derivative already culled</div>
				</div>
				<div class="bg-zinc-900 border {health.unstacked_pairs > 0 ? 'border-amber-700/60' : 'border-zinc-800'} rounded-lg p-3">
					<div class="text-lg font-semibold text-zinc-100">{health.unstacked_pairs.toLocaleString()}</div>
					<div class="text-[11px] text-zinc-500">unstacked RAW+JPEG pairs</div>
				</div>
				<div class="bg-zinc-900 border border-zinc-800 rounded-lg p-3">
					<div class="text-lg font-semibold text-zinc-100">{health.lone_pics.toLocaleString()}</div>
					<div class="text-[11px] text-zinc-500">pictures in RAW/ with no RAW</div>
				</div>
			</div>
		</section>
	{/if}

	<div class="space-y-6">
		{#each workflows as wf}
			<div class="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden hover:{colorClasses(wf.color, 'border')} transition-all">
				<!-- Header -->
				<div class="p-6">
					<div class="flex items-start justify-between gap-4">
						<div class="flex items-start gap-4 flex-1 min-w-0">
							<div class="p-3 {colorClasses(wf.color, 'icon-bg')} rounded-lg shrink-0">
								<Workflow size={20} class={colorClasses(wf.color, 'icon')} />
							</div>
							<div class="min-w-0">
								<div class="flex items-center gap-2 mb-1">
									<h2 class="text-lg font-semibold text-zinc-100">{wf.label}</h2>
									{#if wf.status === 'running'}
										<span class="text-xs px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 flex items-center gap-1">
											<Loader size={10} class="animate-spin" /> Running
										</span>
									{:else if wf.status === 'completed'}
										<span class="text-xs px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 flex items-center gap-1">
											<CheckCircle size={10} /> Done
										</span>
									{:else if wf.status === 'failed'}
										<span class="text-xs px-2 py-0.5 rounded-full bg-red-500/20 text-red-300 flex items-center gap-1">
											<XCircle size={10} /> Failed
										</span>
									{/if}
								</div>
								<p class="text-sm text-zinc-400">{wf.description}</p>
							</div>
						</div>

						<div class="flex items-center gap-2 shrink-0">
							<button
								onclick={() => runWorkflow(wf, true)}
								disabled={wf.status === 'running'}
								class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
							>
								{#if wf.status === 'running'}
									<Loader size={14} class="animate-spin" />
									Running…
								{:else}
									<Eye size={14} />
									Preview
								{/if}
							</button>
							<button
								onclick={() => runWorkflow(wf, false)}
								disabled={wf.status === 'running'}
								title="Modifies files on disk"
								class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed {colorClasses(wf.color, 'btn')}"
							>
								<Play size={14} />
								Apply
							</button>
							{#if wf.lines.length > 0}
								<button
									onclick={() => wf.expanded = !wf.expanded}
									class="p-2 rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
									title={wf.expanded ? 'Collapse output' : 'Expand output'}
								>
									{#if wf.expanded}
										<ChevronUp size={16} />
									{:else}
										<ChevronDown size={16} />
									{/if}
								</button>
							{/if}
						</div>
					</div>

					<!-- Config fields -->
					<div class="mt-5 grid gap-3 {wf.fields.length > 1 ? 'grid-cols-1' : 'grid-cols-1'}">
						{#each wf.fields as field}
							{#if field.type === 'select'}
								<div>
									<label for="{wf.id}-{field.key}" class="block text-xs text-zinc-500 mb-1 font-medium">{field.label}</label>
									<div class="flex gap-2">
										<select id="{wf.id}-{field.key}" bind:value={wf.values[field.key]} disabled={wf.status === 'running'}
											class="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 disabled:opacity-50">
											{#each field.options ?? [] as [value, text]}<option {value}>{text}</option>{/each}
										</select>
										{#if wf.id === 'finish-shoot'}
											<button onclick={() => suggestFinish(wf)} disabled={suggesting || wf.status === 'running'}
												class="px-3 py-2 rounded-lg text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-200 disabled:opacity-40"
												title="From the photos this shoot looks like">{suggesting ? 'Suggesting…' : 'Suggest'}</button>
										{/if}
									</div>
								</div>
							{:else if field.type === 'checkbox'}
								<label class="flex items-center gap-2 text-sm text-zinc-300">
									<input type="checkbox" bind:checked={wf.values[field.key] as boolean} disabled={wf.status === 'running'} />
									{field.label}
								</label>
							{:else}
								<div>
									<label for="{wf.id}-{field.key}" class="block text-xs text-zinc-500 mb-1 font-medium">{field.label}</label>
									<input id="{wf.id}-{field.key}"
										type="text"
										bind:value={wf.values[field.key]}
										placeholder={field.placeholder}
										disabled={wf.status === 'running'}
										class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 font-mono focus:outline-none focus:border-violet-500 disabled:opacity-50"
									/>
								</div>
							{/if}
						{/each}
					</div>
				</div>

				<!-- Output log -->
				{#if wf.lines.length > 0 && wf.expanded}
					<div class="border-t border-zinc-800 bg-zinc-950 px-6 py-4">
						<div class="font-mono text-xs text-zinc-300 space-y-0.5 max-h-64 overflow-y-auto">
							{#each wf.lines as line}
								<div class="{line.startsWith('ERROR') || line.startsWith('Failed') ? 'text-red-400' : line.startsWith('Moving') || line.startsWith('Video') || line.startsWith('Total') || line.startsWith('Process') ? 'text-emerald-400' : ''}">{line}</div>
							{/each}
						</div>
					</div>
				{/if}
			</div>
		{/each}
	</div>
</div>

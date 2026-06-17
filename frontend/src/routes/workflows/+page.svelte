<script lang="ts">
	import { Workflow, Play, CheckCircle, XCircle, Loader, ChevronDown, ChevronUp } from '@lucide/svelte';

	// ---------------------------------------------------------------------------
	// Types
	// ---------------------------------------------------------------------------
	type WorkflowStatus = 'idle' | 'running' | 'completed' | 'failed';

	interface WorkflowCard {
		id: string;
		label: string;
		description: string;
		color: string;
		fields: { key: string; label: string; placeholder: string }[];
		values: Record<string, string>;
		status: WorkflowStatus;
		taskId: string | null;
		lines: string[];
		expanded: boolean;
	}

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------
	let workflows = $state<WorkflowCard[]>([
		{
			id: 'sorting',
			label: 'Sort Videos',
			description: 'Moves video files from AA_RAW to a staging folder, then copies them into AC_SORTED/YYYY/MM/ based on EXIF or filename date.',
			color: 'violet',
			fields: [
				{ key: 'raw_dir',      label: 'Raw Dir',      placeholder: 'D:\\Pictures and Videos\\AA_RAW' },
				{ key: 'sort_me_dir',  label: 'Sort Me Dir',  placeholder: 'D:\\Pictures and Videos\\AB_TO_SORT\\SORT ME' },
				{ key: 'export_root',  label: 'Export Root',  placeholder: 'D:\\Pictures and Videos\\AC_SORTED' },
			],
			values: {
				raw_dir:     'D:\\Pictures and Videos\\AA_RAW',
				sort_me_dir: 'D:\\Pictures and Videos\\AB_TO_SORT\\SORT ME',
				export_root: 'D:\\Pictures and Videos\\AC_SORTED',
			},
			status: 'idle',
			taskId: null,
			lines: [],
			expanded: false,
		},
		{
			id: 'remove-nonkeep-raw',
			label: 'Remove Non-Keep RAW',
			description: 'Walks the RAW folder, finds NEF files that have no matching JPG/JPEG, and moves them to the system Trash.',
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
		};
		return map[color]?.[part] ?? '';
	}

	let pollTimers: Record<string, ReturnType<typeof setInterval>> = {};

	async function runWorkflow(wf: WorkflowCard) {
		wf.status = 'running';
		wf.lines = [];
		wf.taskId = null;
		wf.expanded = true;

		const endpoint = `/api/workflows/run/${wf.id}`;
		try {
			const res = await fetch(endpoint, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(wf.values),
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

	async function pollTask(wf: WorkflowCard) {
		if (!wf.taskId) return;
		try {
			const res = await fetch(`/api/workflows/task/${wf.taskId}`);
			if (!res.ok) return;
			const data = await res.json();
			wf.lines = data.lines ?? [];
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

	<div class="space-y-6">
		{#each workflows as wf}
			<div class="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden hover:{colorClasses(wf.color, 'border')} transition-all">
				<!-- Header -->
				<div class="p-6">
					<div class="flex items-start justify-between gap-4">
						<div class="flex items-start gap-4 flex-1 min-w-0">
							<div class="p-3 {colorClasses(wf.color, 'icon-bg')} rounded-lg shrink-0">
								<Workflow size={22} class={colorClasses(wf.color, 'icon')} />
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
								onclick={() => runWorkflow(wf)}
								disabled={wf.status === 'running'}
								class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed {colorClasses(wf.color, 'btn')}"
							>
								{#if wf.status === 'running'}
									<Loader size={14} class="animate-spin" />
									Running…
								{:else}
									<Play size={14} />
									Run
								{/if}
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
							<div>
								<label class="block text-xs text-zinc-500 mb-1 font-medium">{field.label}</label>
								<input
									type="text"
									bind:value={wf.values[field.key]}
									placeholder={field.placeholder}
									disabled={wf.status === 'running'}
									class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 font-mono focus:outline-none focus:border-violet-500 disabled:opacity-50"
								/>
							</div>
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

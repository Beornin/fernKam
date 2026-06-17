<script lang="ts">
	import { Activity, CheckCircle, XCircle, Clock } from '@lucide/svelte';
	import { onMount } from 'svelte';

	let tasks = $state<any[]>([]);
	let loading = $state(true);
	let interval: ReturnType<typeof setInterval>;

	async function loadTasks() {
		try {
			const res = await fetch('/api/sync/tasks');
			const data = await res.json();
			tasks = data.tasks || [];
		} catch (e) {
			console.error('Failed to load tasks:', e);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadTasks();
		// Refresh every 2 seconds
		interval = setInterval(loadTasks, 2000);
		return () => {
			if (interval) clearInterval(interval);
		};
	});

	function getStatusIcon(status: string) {
		switch (status) {
			case 'running':
				return Activity;
			case 'completed':
				return CheckCircle;
			case 'failed':
				return XCircle;
			default:
				return Clock;
		}
	}

	function getStatusColor(status: string) {
		switch (status) {
			case 'running':
				return 'text-blue-500';
			case 'completed':
				return 'text-green-500';
			case 'failed':
				return 'text-red-500';
			default:
				return 'text-gray-500';
		}
	}

	function formatTime(isoString: string | null) {
		if (!isoString) return '-';
		const date = new Date(isoString);
		return date.toLocaleTimeString();
	}
</script>

<div class="p-6">
	<h1 class="text-2xl font-bold mb-6">Background Tasks</h1>

	{#if loading}
		<div class="text-gray-500">Loading tasks...</div>
	{:else if tasks.length === 0}
		<div class="text-gray-500">No background tasks running.</div>
	{:else}
		<div class="space-y-4">
			{#each tasks as task}
				{@const Icon = getStatusIcon(task.status)}
				<div class="bg-white rounded-lg shadow p-4 border border-gray-200">
					<div class="flex items-start justify-between">
						<div class="flex items-start gap-3">
							<Icon class={getStatusColor(task.status)} />
							<div>
								<div class="font-semibold">{task.task_type}</div>
								<div class="text-sm text-gray-600">{task.message}</div>
								{#if task.progress && task.task_type === 'auto_confirm'}
									<div class="text-xs text-gray-500 mt-1.5 flex flex-wrap gap-3">
										<span>Pass <strong>{task.progress.pass}</strong></span>
										<span>&#10003; <strong>{task.progress.confirmed}</strong> confirmed</span>
										<span>&#10007; <strong>{task.progress.ignored}</strong> ignored</span>
										<span>? <strong>{task.progress.suggested}</strong> queued</span>
										<span class="text-gray-400">{task.progress.scored} scored</span>
									</div>
								{:else if task.progress}
									<div class="text-sm text-gray-500 mt-1">
										Added: {task.progress.added || 0}, Updated: {task.progress.updated || 0},
										Skipped: {task.progress.skipped || 0}, Errors: {task.progress.errors || 0}
									</div>
								{/if}
							</div>
						</div>
						<div class="text-right text-sm text-gray-500">
							<div>Started: {formatTime(task.started_at)}</div>
							{#if task.completed_at}
								<div>Completed: {formatTime(task.completed_at)}</div>
							{/if}
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

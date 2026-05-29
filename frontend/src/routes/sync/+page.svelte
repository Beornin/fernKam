<script lang="ts">
	import { onMount } from 'svelte';
	import { Database, HardDrive, RefreshCw, ArrowRight, CheckCircle, AlertCircle, Loader2 } from '@lucide/svelte';

	let status = $state<{
		dirty_count: number;
		never_synced_count: number;
		last_sync: string | null;
	} | null>(null);

	let loadingStatus = $state(false);

	let dbToFileSyncing = $state(false);
	let dbToFileResult = $state<string | null>(null);

	let fileToDbSyncing = $state(false);
	let fileToDbResult = $state<string | null>(null);

	let backfillThumbs = $state(false);
	let backfillThumbsResult = $state<string | null>(null);

	let backfillCrops = $state(false);
	let backfillCropsResult = $state<string | null>(null);

	async function loadStatus() {
		loadingStatus = true;
		try {
			const res = await fetch('http://localhost:8000/api/sync/status');
			status = await res.json();
		} catch (e) {
			console.error('Failed to load status', e);
		} finally {
			loadingStatus = false;
		}
	}

	async function syncDbToFile() {
		dbToFileSyncing = true;
		dbToFileResult = null;
		try {
			const res = await fetch('/api/sync/db-to-file', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ limit: 100 })
			});
			const data = await res.json();
			dbToFileResult = `Synced: ${data.synced}, Errors: ${data.errors}, Total: ${data.total}`;
			await loadStatus();
		} catch (e) {
			dbToFileResult = `Sync failed: ${e}`;
		} finally {
			dbToFileSyncing = false;
		}
	}

	async function syncFileToDb() {
		fileToDbSyncing = true;
		fileToDbResult = null;
		try {
			const res = await fetch('/api/sync/file-to-db', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ limit: 100 })
			});
			const data = await res.json();
			fileToDbResult = `Synced: ${data.synced}, Tags created: ${data.tags_created}, Errors: ${data.errors}, Total: ${data.total}`;
			await loadStatus();
		} catch (e) {
			fileToDbResult = `Sync failed: ${e}`;
		} finally {
			fileToDbSyncing = false;
		}
	}

	async function runBackfillThumbs() {
		backfillThumbs = true;
		backfillThumbsResult = null;
		try {
			const res = await fetch('/api/sync/backfill-thumbnails?limit=500', {
				method: 'POST'
			});
			const data = await res.json();
			backfillThumbsResult = `Processed: ${data.processed}, Errors: ${data.errors}, Remaining: ${data.remaining}`;
		} catch (e) {
			backfillThumbsResult = `Backfill failed: ${e}`;
		} finally {
			backfillThumbs = false;
		}
	}

	async function runBackfillCrops() {
		backfillCrops = true;
		backfillCropsResult = null;
		try {
			const res = await fetch('/api/sync/backfill-crops?limit=500', {
				method: 'POST'
			});
			const data = await res.json();
			backfillCropsResult = `Processed: ${data.processed}, Errors: ${data.errors}`;
		} catch (e) {
			backfillCropsResult = `Backfill failed: ${e}`;
		} finally {
			backfillCrops = false;
		}
	}

	onMount(() => {
		loadStatus();
	});
</script>

<div class="p-8 max-w-6xl mx-auto">
	<div class="mb-8">
		<h1 class="text-3xl font-bold text-zinc-100 mb-2 flex items-center gap-3">
			<Database size={32} class="text-blue-400" />
			Sync Data
		</h1>
		<p class="text-zinc-400">Synchronize data between database and files</p>
	</div>

	<!-- Status Card -->
	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 mb-6">
		<h2 class="text-lg font-semibold text-zinc-100 mb-4 flex items-center gap-2">
			{#if loadingStatus}
				<Loader2 size={20} class="animate-spin text-zinc-400" />
			{:else}
				<CheckCircle size={20} class="text-emerald-400" />
			{/if}
			Sync Status
		</h2>
		{#if status}
			<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Pending DB→File</div>
					<div class="text-2xl font-bold text-zinc-100">{status.dirty_count}</div>
				</div>
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Never Synced</div>
					<div class="text-2xl font-bold text-zinc-100">{status.never_synced_count}</div>
				</div>
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Last Sync</div>
					<div class="text-sm font-medium text-zinc-100">
						{status.last_sync ? new Date(status.last_sync).toLocaleString() : 'Never'}
					</div>
				</div>
			</div>
		{/if}
	</div>

	<div class="grid grid-cols-1 md:grid-cols-2 gap-6">
		<!-- DB to File -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-blue-500/10 rounded-lg">
					<Database size={20} class="text-blue-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600" />
				<div class="p-2 bg-orange-500/10 rounded-lg">
					<HardDrive size={20} class="text-orange-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">DB → Files</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">Write database changes (tags, ratings, faces) to image files via XMP.</p>
			<button
				onclick={syncDbToFile}
				disabled={dbToFileSyncing}
				class="w-full px-4 py-2 rounded-lg bg-blue-500 text-zinc-900 font-medium hover:bg-blue-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if dbToFileSyncing}
					<Loader2 size={16} class="animate-spin" />
					Syncing...
				{:else}
					<RefreshCw size={16} />
					Sync to Files
				{/if}
			</button>
			{#if dbToFileResult}
				<div class="mt-3 text-xs {dbToFileResult.startsWith('Sync failed') ? 'text-red-400' : 'text-emerald-400'}">
					{dbToFileResult}
				</div>
			{/if}
		</div>

		<!-- File to DB -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-orange-500/10 rounded-lg">
					<HardDrive size={20} class="text-orange-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600" />
				<div class="p-2 bg-blue-500/10 rounded-lg">
					<Database size={20} class="text-blue-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">Files → DB</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">Read XMP from image files and update database (pick up external edits).</p>
			<button
				onclick={syncFileToDb}
				disabled={fileToDbSyncing}
				class="w-full px-4 py-2 rounded-lg bg-orange-500 text-zinc-900 font-medium hover:bg-orange-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if fileToDbSyncing}
					<Loader2 size={16} class="animate-spin" />
					Syncing...
				{:else}
					<RefreshCw size={16} />
					Sync to DB
				{/if}
			</button>
			{#if fileToDbResult}
				<div class="mt-3 text-xs {fileToDbResult.startsWith('Sync failed') ? 'text-red-400' : 'text-emerald-400'}">
					{fileToDbResult}
				</div>
			{/if}
		</div>

		<!-- Backfill Thumbnails -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-emerald-500/10 rounded-lg">
					<Database size={20} class="text-emerald-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">Backfill Thumbnails</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">Generate DB thumbnails for existing photos that don't have them yet.</p>
			<button
				onclick={runBackfillThumbs}
				disabled={backfillThumbs}
				class="w-full px-4 py-2 rounded-lg bg-emerald-500 text-zinc-900 font-medium hover:bg-emerald-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if backfillThumbs}
					<Loader2 size={16} class="animate-spin" />
					Backfilling...
				{:else}
					<RefreshCw size={16} />
					Backfill Thumbnails
				{/if}
			</button>
			{#if backfillThumbsResult}
				<div class="mt-3 text-xs {backfillThumbsResult.startsWith('Backfill failed') ? 'text-red-400' : 'text-emerald-400'}">
					{backfillThumbsResult}
				</div>
			{/if}
		</div>

		<!-- Backfill Face Crops -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-purple-500/10 rounded-lg">
					<Database size={20} class="text-purple-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">Backfill Face Crops</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">Generate DB face crops for existing faces that don't have them yet.</p>
			<button
				onclick={runBackfillCrops}
				disabled={backfillCrops}
				class="w-full px-4 py-2 rounded-lg bg-purple-500 text-zinc-900 font-medium hover:bg-purple-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if backfillCrops}
					<Loader2 size={16} class="animate-spin" />
					Backfilling...
				{:else}
					<RefreshCw size={16} />
					Backfill Crops
				{/if}
			</button>
			{#if backfillCropsResult}
				<div class="mt-3 text-xs {backfillCropsResult.startsWith('Backfill failed') ? 'text-red-400' : 'text-emerald-400'}">
					{backfillCropsResult}
				</div>
			{/if}
		</div>
	</div>
</div>

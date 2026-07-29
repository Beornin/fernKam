<script lang="ts">
	import { onMount } from 'svelte';
	import { Database, HardDrive, RefreshCw, ArrowRight, CheckCircle, AlertCircle, Loader2, FolderSearch, Gauge, Wrench } from '@lucide/svelte';
	import { api } from '$lib/api';
	import { formatBytes } from '$lib/format';

	let status = $state<{
		dirty_count: number;
		never_synced_count: number;
		last_sync: string | null;
	} | null>(null);

	let loadingStatus = $state(false);

	// ── Rescan Library ──
	let rescanning = $state(false);
	let rescanResult = $state<string | null>(null);

	async function pollRescanTask(taskId: string) {
		for (;;) {
			await new Promise(r => setTimeout(r, 1500));
			const { tasks } = await api.sync.tasks();
			const t = tasks.find(x => x.id === taskId);
			if (!t) break;
			if (t.status === 'completed') { rescanResult = t.message; break; }
			if (t.status === 'failed') { rescanResult = `Failed: ${t.message}`; break; }
			rescanResult = t.message;
		}
		rescanning = false;
	}

	async function runRescanLibrary() {
		rescanning = true;
		rescanResult = null;
		try {
			const r = await api.sync.scanLibrary();
			rescanResult = r.message;
			if (r.task_id) pollRescanTask(r.task_id);
			else rescanning = false;
		} catch (e) {
			rescanResult = `Failed: ${e}`;
			rescanning = false;
		}
	}

	// ── Database stats + optimization ──
	let dbStats = $state<Awaited<ReturnType<typeof api.sync.dbStats>> | null>(null);
	let loadingDbStats = $state(false);
	let vacuuming = $state(false);
	let vacuumResult = $state<string | null>(null);
	let reindexing = $state(false);
	let reindexResult = $state<string | null>(null);
	let rebuildingIndexes = $state(false);
	let rebuildIndexesResult = $state<string | null>(null);

	async function loadDbStats() {
		loadingDbStats = true;
		try {
			dbStats = await api.sync.dbStats();
		} catch (e) {
			console.error('Failed to load db stats', e);
		} finally {
			loadingDbStats = false;
		}
	}

	async function pollBgTask(taskId: string, onDone: (message: string) => void) {
		for (;;) {
			await new Promise(r => setTimeout(r, 1500));
			const { tasks } = await api.sync.tasks();
			const t = tasks.find(x => x.id === taskId);
			if (!t || t.status === 'completed' || t.status === 'failed') {
				onDone(t ? t.message : 'Done');
				break;
			}
		}
	}

	async function runVacuumAnalyze() {
		vacuuming = true;
		vacuumResult = null;
		try {
			const r = await api.sync.vacuumAnalyze();
			await pollBgTask(r.task_id, async (msg) => {
				vacuumResult = msg;
				vacuuming = false;
				await loadDbStats();
			});
		} catch (e) {
			vacuumResult = `Failed: ${e}`;
			vacuuming = false;
		}
	}

	async function runReindex() {
		reindexing = true;
		reindexResult = null;
		try {
			const r = await api.sync.reindex();
			await pollBgTask(r.task_id, (msg) => { reindexResult = msg; reindexing = false; });
		} catch (e) {
			reindexResult = `Failed: ${e}`;
			reindexing = false;
		}
	}

	async function runRebuildIndexes() {
		rebuildingIndexes = true;
		rebuildIndexesResult = null;
		try {
			await api.sync.rebuildIndexes();
			rebuildIndexesResult = 'Done';
		} catch (e) {
			rebuildIndexesResult = `Failed: ${e}`;
		} finally {
			rebuildingIndexes = false;
		}
	}

	let dbToFileSyncing = $state(false);
	let dbToFileResult = $state<string | null>(null);

	let fileToDbSyncing = $state(false);
	let fileToDbResult = $state<string | null>(null);

	let backfillThumbs = $state(false);
	let backfillThumbsResult = $state<string | null>(null);

	let backfillCrops = $state(false);
	let backfillCropsResult = $state<string | null>(null);

	let backfillDuration = $state(false);
	let backfillDurationResult = $state<string | null>(null);

	let archivingLowQuality = $state(false);
	let archiveLowQualityResult = $state<string | null>(null);

	let incrementalSweeping = $state(false);
	let incrementalSweepResult = $state<string | null>(null);

	async function loadStatus() {
		loadingStatus = true;
		try {
			const res = await fetch('/api/sync/status');
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
			const r = await api.sync.writeMetadata({ dirty_only: false });
			if (!r.task_id) { dbToFileResult = r.message; dbToFileSyncing = false; return; }
			await pollBgTask(r.task_id, async (msg) => {
				dbToFileResult = msg;
				dbToFileSyncing = false;
				await loadStatus();
			});
		} catch (e) {
			dbToFileResult = `Failed: ${e}`;
			dbToFileSyncing = false;
		}
	}

	async function syncFileToDb() {
		fileToDbSyncing = true;
		fileToDbResult = null;
		try {
			const r = await api.sync.refreshMetadata();
			if (!r.task_id) { fileToDbResult = r.message; fileToDbSyncing = false; return; }
			await pollBgTask(r.task_id, async (msg) => {
				fileToDbResult = msg;
				fileToDbSyncing = false;
				await loadStatus();
			});
		} catch (e) {
			fileToDbResult = `Failed: ${e}`;
			fileToDbSyncing = false;
		}
	}

	async function runBackfillThumbs() {
		backfillThumbs = true;
		backfillThumbsResult = null;
		try {
			const r = await api.sync.backfillThumbnails();
			if (!r.task_id) { backfillThumbsResult = r.message; backfillThumbs = false; return; }
			await pollBgTask(r.task_id, (msg) => { backfillThumbsResult = msg; backfillThumbs = false; });
		} catch (e) {
			backfillThumbsResult = `Backfill failed: ${e}`;
			backfillThumbs = false;
		}
	}

	async function runBackfillDuration() {
		backfillDuration = true;
		backfillDurationResult = null;
		try {
			const res = await fetch('/api/sync/backfill-video-duration', { method: 'POST' });
			const data = await res.json();
			backfillDurationResult = `Started probing ${data.total} videos (task: ${data.task_id})`;
		} catch (e) {
			backfillDurationResult = `Failed: ${e}`;
		} finally {
			backfillDuration = false;
		}
	}

	async function runBackfillCrops() {
		backfillCrops = true;
		backfillCropsResult = null;
		try {
			const r = await api.sync.backfillCrops();
			if (!r.task_id) { backfillCropsResult = r.message; backfillCrops = false; return; }
			await pollBgTask(r.task_id, (msg) => { backfillCropsResult = msg; backfillCrops = false; });
		} catch (e) {
			backfillCropsResult = `Backfill failed: ${e}`;
			backfillCrops = false;
		}
	}

	onMount(() => {
		loadStatus();
		loadDbStats();
	});
</script>

<div class="p-8 max-w-6xl mx-auto">
	<div class="mb-8">
		<h1 class="text-3xl font-bold text-zinc-100 mb-2 flex items-center gap-3">
			<Wrench size={32} class="text-blue-400" />
			Maintenance
		</h1>
		<p class="text-zinc-400">Rescan the library, sync data, and keep the database healthy</p>
	</div>

	<!-- Rescan Library -->
	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 mb-6">
		<div class="flex items-center gap-3 mb-4">
			<div class="p-2 bg-amber-500/10 rounded-lg">
				<FolderSearch size={20} class="text-amber-400" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100">Rescan Library</h2>
		</div>
		<p class="text-sm text-zinc-400 mb-4">
			Walks the full library folder and reconciles it with the database: imports new files, refreshes
			changed ones, and removes entries for files that no longer exist on disk.
		</p>
		<button
			onclick={runRescanLibrary}
			disabled={rescanning}
			class="w-full px-4 py-2 rounded-lg bg-amber-500 text-zinc-900 font-medium hover:bg-amber-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
		>
			{#if rescanning}
				<Loader2 size={16} class="animate-spin" /> Scanning…
			{:else}
				<FolderSearch size={16} /> Rescan Library
			{/if}
		</button>
		{#if rescanResult}
			<div class="mt-3 text-xs {rescanResult.startsWith('Failed') ? 'text-red-400' : 'text-emerald-400'}">
				{rescanResult}
			</div>
		{/if}
	</div>

	<!-- Database Health -->
	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 mb-6">
		<div class="flex items-center gap-3 mb-4">
			<div class="p-2 bg-indigo-500/10 rounded-lg">
				<Gauge size={20} class="text-indigo-400" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100">Database</h2>
		</div>

		{#if loadingDbStats && !dbStats}
			<div class="flex items-center gap-2 text-zinc-500 text-sm mb-4">
				<Loader2 size={16} class="animate-spin" /> Loading stats…
			</div>
		{:else if dbStats}
			<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Database size</div>
					<div class="text-2xl font-bold text-zinc-100">{formatBytes(dbStats.db_size_bytes)}</div>
				</div>
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Dead rows</div>
					<div class="text-2xl font-bold {dbStats.dead_row_pct > 10 ? 'text-amber-400' : 'text-zinc-100'}">{dbStats.dead_row_pct}%</div>
				</div>
			</div>
			<div class="overflow-x-auto mb-4">
				<table class="w-full text-xs">
					<thead>
						<tr class="text-zinc-500 text-left">
							<th class="pb-2 pr-4">Table</th>
							<th class="pb-2 pr-4">Live rows</th>
							<th class="pb-2 pr-4">Dead rows</th>
							<th class="pb-2 pr-4">Size</th>
							<th class="pb-2">Last vacuum</th>
						</tr>
					</thead>
					<tbody>
						{#each dbStats.tables as t}
							<tr class="border-t border-zinc-800 text-zinc-300">
								<td class="py-1.5 pr-4 font-mono">{t.table_name}</td>
								<td class="py-1.5 pr-4">{t.live_rows.toLocaleString()}</td>
								<td class="py-1.5 pr-4 {t.dead_pct > 10 ? 'text-amber-400' : ''}">{t.dead_rows.toLocaleString()} ({t.dead_pct}%)</td>
								<td class="py-1.5 pr-4">{formatBytes(t.total_size)}</td>
								<td class="py-1.5 text-zinc-500">{t.last_vacuum ? new Date(t.last_vacuum).toLocaleString() : 'never'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		<div class="grid grid-cols-1 md:grid-cols-3 gap-3">
			<div>
				<button
					onclick={runVacuumAnalyze}
					disabled={vacuuming}
					title="VACUUM (ANALYZE) — reclaims space from deleted/updated rows and refreshes the query planner's statistics. Safe to run anytime, doesn't lock tables for reads."
					class="w-full px-3 py-2 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 text-sm"
				>
					{#if vacuuming}<Loader2 size={14} class="animate-spin" /> Running…{:else}Optimize (VACUUM){/if}
				</button>
				{#if vacuumResult}<div class="mt-2 text-xs {vacuumResult.startsWith('Failed') ? 'text-red-400' : 'text-indigo-400'}">{vacuumResult}</div>{/if}
			</div>
			<div>
				<button
					onclick={runReindex}
					disabled={reindexing}
					title="Rebuilds the largest/heaviest-churn indexes (face embedding search, EXIF, hash lookup) concurrently, without locking the tables."
					class="w-full px-3 py-2 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 text-sm"
				>
					{#if reindexing}<Loader2 size={14} class="animate-spin" /> Running…{:else}Reindex{/if}
				</button>
				{#if reindexResult}<div class="mt-2 text-xs {reindexResult.startsWith('Failed') ? 'text-red-400' : 'text-indigo-400'}">{reindexResult}</div>{/if}
			</div>
			<div>
				<button
					onclick={runRebuildIndexes}
					disabled={rebuildingIndexes}
					title="Re-runs the same idempotent index setup that happens at every app startup — creates any missing indexes."
					class="w-full px-3 py-2 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 text-sm"
				>
					{#if rebuildingIndexes}<Loader2 size={14} class="animate-spin" /> Running…{:else}Rebuild Missing Indexes{/if}
				</button>
				{#if rebuildIndexesResult}<div class="mt-2 text-xs {rebuildIndexesResult.startsWith('Failed') ? 'text-red-400' : 'text-indigo-400'}">{rebuildIndexesResult}</div>{/if}
			</div>
		</div>
	</div>

	<!-- Status Card -->
	<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 mb-6">
		<h2 class="text-lg font-semibold text-zinc-100 mb-4 flex items-center gap-2">
			{#if loadingStatus}
				<Loader2 size={20} class="animate-spin text-zinc-400" />
			{:else}
				<CheckCircle size={20} class="text-emerald-400" />
			{/if}
			DB → File Write-Back Status
		</h2>
		<p class="text-xs text-zinc-500 mb-4">
			These numbers only track the DB → Files direction (writing tags/ratings/faces out to XMP). Files → DB
			just re-reads whatever's currently on disk each time it runs, so there's nothing to track as "pending" for it.
		</p>
		{#if status}
			<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Pending (changed since last write)</div>
					<div class="text-2xl font-bold text-zinc-100">{status.dirty_count}</div>
				</div>
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Never Written to Files</div>
					<div class="text-2xl font-bold text-zinc-100">{status.never_synced_count}</div>
				</div>
				<div class="bg-zinc-800/50 rounded-lg p-4">
					<div class="text-sm text-zinc-400 mb-1">Last Write-Back</div>
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
			<p class="text-sm text-zinc-400 mb-4">Write database changes (tags, ratings, faces) to image files via XMP. Runs in the background over every photo — no need to click repeatedly.</p>
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
				<div class="mt-3 text-xs {dbToFileResult.startsWith('Failed') ? 'text-red-400' : 'text-emerald-400'}">
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
			<p class="text-sm text-zinc-400 mb-4">Re-reads full metadata (EXIF, GPS, tags, rating, faces) from every image file and updates the database. Runs in the background over every photo — no need to click repeatedly.</p>
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
				<div class="mt-3 text-xs {fileToDbResult.startsWith('Failed') ? 'text-red-400' : 'text-emerald-400'}">
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
			<p class="text-sm text-zinc-400 mb-4">
				Generates sm/md/lg/xl WebP thumbnails stored in the database for any photos that are missing them.
				Only processes photos without existing thumbnails — safe to re-run. Runs in the background over the whole backlog.
			</p>
			<button
				onclick={runBackfillThumbs}
				disabled={backfillThumbs}
				title="Generates missing DB thumbnails (sm/md/lg/xl WebP) for photos imported before thumbnailing was added, or after failures. Skips photos that already have thumbnails."
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
			<p class="text-sm text-zinc-400 mb-4">
				Re-reads source images to generate 200×200 WebP face crop thumbnails for detected faces that are missing them.
				Requires source images on disk. Only processes faces without crops — safe to re-run. Runs in the background over the whole backlog.
			</p>
			<button
				onclick={runBackfillCrops}
				disabled={backfillCrops}
				title="Generates missing 200×200 WebP face crop thumbnails for faces detected before crop storage was added, or after failures. Reads source images from disk — file must still exist. Skips faces that already have crops."
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

		<!-- Backfill Video Duration -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-sky-500/10 rounded-lg">
					<Database size={20} class="text-sky-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">Backfill Video Duration</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">
				Probes all videos missing a <code class="text-sky-300">duration_secs</code> value using ffprobe. Runs as a background task — check Tasks for progress.
			</p>
			<button
				onclick={runBackfillDuration}
				disabled={backfillDuration}
				class="w-full px-4 py-2 rounded-lg bg-sky-600 text-white font-medium hover:bg-sky-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if backfillDuration}
					<Loader2 size={16} class="animate-spin" /> Starting…
				{:else}
					<RefreshCw size={16} /> Probe Video Durations
				{/if}
			</button>
			{#if backfillDurationResult}
				<div class="mt-3 text-xs {backfillDurationResult.startsWith('Failed') ? 'text-red-400' : 'text-sky-400'}">
					{backfillDurationResult}
				</div>
			{/if}
		</div>

		<!-- Archive Low-Quality Faces -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-rose-500/10 rounded-lg">
					<Database size={20} class="text-rose-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">Archive Low-Quality Faces</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">
				Marks unconfirmed faces with low detection score (&lt;0.5) or tiny bounding box (&lt;30 px) as ignored.
				Reduces review queue noise. Configurable via <code class="text-rose-300">FERNKAM_MIN_DET_SCORE</code> / <code class="text-rose-300">FERNKAM_MIN_FACE_PX</code>.
			</p>
			<button
				onclick={async () => {
					archivingLowQuality = true; archiveLowQualityResult = null;
					try {
						const { api } = await import('$lib/api');
						const r = await api.faces.archiveLowQuality();
						archiveLowQualityResult = `Archived ${r.archived.toLocaleString()} faces (det<${r.min_det_score}, px<${r.min_face_px})`;
					} catch(e: any) { archiveLowQualityResult = 'Failed: ' + (e.message ?? e); }
					finally { archivingLowQuality = false; }
				}}
				disabled={archivingLowQuality}
				class="w-full px-4 py-2 rounded-lg bg-rose-700 text-white font-medium hover:bg-rose-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if archivingLowQuality}
					<Loader2 size={16} class="animate-spin" /> Working…
				{:else}
					<RefreshCw size={16} /> Archive Low-Quality Faces
				{/if}
			</button>
			{#if archiveLowQualityResult}
				<div class="mt-3 text-xs {archiveLowQualityResult.startsWith('Failed') ? 'text-red-400' : 'text-emerald-400'}">
					{archiveLowQualityResult}
				</div>
			{/if}
		</div>

		<!-- Incremental Face Sweep -->
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-violet-500/10 rounded-lg">
					<Database size={20} class="text-violet-400" />
				</div>
				<h2 class="text-lg font-semibold text-zinc-100">Incremental Face Sweep</h2>
			</div>
			<p class="text-sm text-zinc-400 mb-4">
				Runs the auto-confirm sweep only over faces added since the last completed sweep — much faster than a full pass after routine imports.
			</p>
			<button
				onclick={async () => {
					incrementalSweeping = true; incrementalSweepResult = null;
					try {
						const { api } = await import('$lib/api');
						const r = await api.faces.autoConfirmIncremental();
						incrementalSweepResult = r.since ? `Started since ${new Date(r.since).toLocaleString()}` : 'Started (full sweep — no prior run)';
					} catch(e: any) { incrementalSweepResult = 'Failed: ' + (e.message ?? e); }
					finally { incrementalSweeping = false; }
				}}
				disabled={incrementalSweeping}
				class="w-full px-4 py-2 rounded-lg bg-violet-700 text-white font-medium hover:bg-violet-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
			>
				{#if incrementalSweeping}
					<Loader2 size={16} class="animate-spin" /> Starting…
				{:else}
					<RefreshCw size={16} /> Incremental Sweep
				{/if}
			</button>
			{#if incrementalSweepResult}
				<div class="mt-3 text-xs {incrementalSweepResult.startsWith('Failed') ? 'text-red-400' : 'text-violet-400'}">
					{incrementalSweepResult}
				</div>
			{/if}
		</div>
	</div>
</div>

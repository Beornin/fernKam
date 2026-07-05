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

	let backfillDuration = $state(false);
	let backfillDurationResult = $state<string | null>(null);

	let archivingLowQuality = $state(false);
	let archiveLowQualityResult = $state<string | null>(null);

	let incrementalSweeping = $state(false);
	let incrementalSweepResult = $state<string | null>(null);

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
			<p class="text-sm text-zinc-400 mb-4">
				Generates sm/md/lg/xl WebP thumbnails stored in the database for any photos that are missing them.
				Only processes photos without existing thumbnails — safe to re-run. Processes up to 500 per click.
			</p>
			<button
				onclick={runBackfillThumbs}
				disabled={backfillThumbs}
				title="Generates missing DB thumbnails (sm/md/lg/xl WebP) for photos imported before thumbnailing was added, or after failures. Runs 500 at a time — click again for more. Skips photos that already have thumbnails."
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
				Requires source images on disk. Only processes faces without crops — safe to re-run. Processes up to 500 per click.
			</p>
			<button
				onclick={runBackfillCrops}
				disabled={backfillCrops}
				title="Generates missing 200×200 WebP face crop thumbnails for faces detected before crop storage was added, or after failures. Reads source images from disk — file must still exist. Runs 500 at a time — click again for more. Skips faces that already have crops."
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

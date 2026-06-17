<script lang="ts">
	import { Download, RefreshCw, ArrowRight, Database, HardDrive, Activity, Trash2, Save, RotateCcw, Workflow } from '@lucide/svelte';

	let showImportModal = $state(false);
	let importPath = $state('');
	let importing = $state(false);
	let importResult = $state<string | null>(null);
	let quickScanning = $state(false);
	let quickScanResult = $state<string | null>(null);
	let showResetModal = $state(false);
	let resetConfirmText = $state('');
	let resetting = $state(false);
	let resetResult = $state<string | null>(null);

	// Backup state
	let showBackupModal = $state(false);
	let backingUp = $state(false);
	let backupResult = $state<string | null>(null);
	let backups = $state<any[]>([]);
	let backupDir = $state('');
	let loadingBackups = $state(false);
	let restoringPath = $state<string | null>(null);
	let restoreResult = $state<string | null>(null);

	async function startImport() {
		if (!importPath) return;
		importing = true;
		importResult = null;
		try {
			// Convert backslashes to forward slashes for JSON
			const normalizedPath = importPath.replace(/\\/g, '/');
			const res = await fetch('/api/sync/scan-library', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ custom_path: normalizedPath })
			});
			const data = await res.json();
			if (data.error) {
				importResult = `Error: ${data.error}`;
			} else if (data.status === "running") {
				importResult = `Scan started in background. Check Tasks page for progress.`;
				showImportModal = false; // Close modal after starting
			} else {
				importResult = `Added: ${data.added}, Updated: ${data.updated}, Skipped: ${data.skipped}, Errors: ${data.errors}`;
				showImportModal = false; // Close modal after completion
			}
		} catch (e) {
			importResult = `Import failed: ${e}`;
		} finally {
			importing = false;
		}
	}

	async function quickScan() {
		quickScanning = true;
		quickScanResult = null;
		try {
			const res = await fetch('/api/sync/scan-library', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({})
			});
			const data = await res.json();
			if (data.error) {
				quickScanResult = `Error: ${data.error}`;
			} else if (data.status === "running") {
				quickScanResult = `Scan started in background. Check the Tasks page for progress.`;
			} else {
				quickScanResult = `Added: ${data.added}, Updated: ${data.updated}, Skipped: ${data.skipped}, Errors: ${data.errors}`;
			}
		} catch (e) {
			quickScanResult = `Scan failed: ${e}`;
		} finally {
			quickScanning = false;
		}
	}

	function openImport() {
		importPath = '';
		importResult = null;
		showImportModal = true;
	}

	function openResetModal() {
		resetConfirmText = '';
		resetResult = null;
		showResetModal = true;
	}

	async function openBackupModal() {
		backupResult = null;
		restoreResult = null;
		showBackupModal = true;
		loadingBackups = true;
		try {
			const res = await fetch('/api/backup/list');
			const data = await res.json();
			backups = data.backups || [];
			backupDir = data.backup_dir || '';
		} catch (e) {
			backupResult = `Failed to load backups: ${e}`;
		} finally {
			loadingBackups = false;
		}
	}

	async function createBackup() {
		backingUp = true;
		backupResult = null;
		try {
			const res = await fetch('/api/backup/create', { method: 'POST' });
			const data = await res.json();
			backupResult = data.message;
			if (data.ok) {
				// Refresh list
				const r2 = await fetch('/api/backup/list');
				const d2 = await r2.json();
				backups = d2.backups || [];
			}
		} catch (e) {
			backupResult = `Backup failed: ${e}`;
		} finally {
			backingUp = false;
		}
	}

	async function restoreBackup(path: string) {
		if (!confirm(`Restore from this backup? This will overwrite the current database.`)) return;
		restoringPath = path;
		restoreResult = null;
		try {
			const res = await fetch('/api/backup/restore', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ backup_path: path })
			});
			const data = await res.json();
			restoreResult = data.message;
		} catch (e) {
			restoreResult = `Restore failed: ${e}`;
		} finally {
			restoringPath = null;
		}
	}

	async function resetDatabase() {
		if (resetConfirmText !== 'DELETE') return;
		resetting = true;
		resetResult = null;
		try {
			const res = await fetch('/api/sync/reset-db', { method: 'POST' });
			const data = await res.json();
			if (data.ok) {
				resetResult = `Done — cleared: ${data.tables_cleared.join(', ')}`;
			} else {
				resetResult = `Error: ${JSON.stringify(data)}`;
			}
		} catch (e) {
			resetResult = `Failed: ${e}`;
		} finally {
			resetting = false;
		}
	}
</script>

<div class="p-8 max-w-6xl mx-auto">
	<div class="mb-8">
		<h1 class="text-3xl font-bold text-zinc-100 mb-2 flex items-center gap-3">
			<Activity size={32} class="text-emerald-400" />
			fernKam
		</h1>
		<p class="text-zinc-400">Manage your photo library</p>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
		<!-- Import Workflow -->
		<button
			onclick={openImport}
			class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-left hover:border-emerald-500/50 hover:bg-zinc-800/50 transition-all group"
		>
			<div class="flex items-start justify-between mb-4">
				<div class="p-3 bg-emerald-500/10 rounded-lg">
					<Download size={24} class="text-emerald-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600 group-hover:text-emerald-400 transition-colors" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100 mb-1">Import Photos</h2>
			<p class="text-sm text-zinc-400">Scan library and import new photos</p>
		</button>

		<!-- Sync Data -->
		<a
			href="/sync"
			class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-left hover:border-emerald-500/50 hover:bg-zinc-800/50 transition-all group block"
		>
			<div class="flex items-start justify-between mb-4">
				<div class="p-3 bg-blue-500/10 rounded-lg">
					<Database size={24} class="text-blue-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600 group-hover:text-emerald-400 transition-colors" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100 mb-1">Sync Data</h2>
			<p class="text-sm text-zinc-400">Sync database to files or vice versa</p>
		</a>

		<!-- Quick Scan -->
		<button
			onclick={quickScan}
			disabled={quickScanning}
			class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-left hover:border-emerald-500/50 hover:bg-zinc-800/50 transition-all group disabled:opacity-50 disabled:cursor-not-allowed"
		>
			<div class="flex items-start justify-between mb-4">
				<div class="p-3 bg-orange-500/10 rounded-lg">
					<RefreshCw size={24} class="text-orange-400 {quickScanning ? 'animate-spin' : ''}" />
				</div>
				<ArrowRight size={20} class="text-zinc-600 group-hover:text-emerald-400 transition-colors" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100 mb-1">Quick Scan</h2>
			<p class="text-sm text-zinc-400">Scan entire library for changes</p>
			{#if quickScanResult}
				<div class="mt-3 text-xs {quickScanResult.startsWith('Error') ? 'text-red-400' : 'text-emerald-400'}">
					{quickScanResult}
				</div>
			{/if}
		</button>

		<!-- Backup Database -->
		<button
			onclick={openBackupModal}
			class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-left hover:border-amber-500/50 hover:bg-zinc-800/50 transition-all group"
		>
			<div class="flex items-start justify-between mb-4">
				<div class="p-3 bg-amber-500/10 rounded-lg">
					<Save size={24} class="text-amber-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600 group-hover:text-amber-400 transition-colors" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100 mb-1">Backup Database</h2>
			<p class="text-sm text-zinc-400">Create or restore a pg_dump backup</p>
		</button>

		<!-- Workflows -->
		<a
			href="/workflows"
			class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-left hover:border-violet-500/50 hover:bg-zinc-800/50 transition-all group block"
		>
			<div class="flex items-start justify-between mb-4">
				<div class="p-3 bg-violet-500/10 rounded-lg">
					<Workflow size={24} class="text-violet-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600 group-hover:text-violet-400 transition-colors" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100 mb-1">Workflows</h2>
			<p class="text-sm text-zinc-400">Run file-system workflows (sort, clean RAW)</p>
		</a>

		<!-- Reset Database -->
		<button
			onclick={openResetModal}
			class="bg-zinc-900 border border-red-900/40 rounded-xl p-6 text-left hover:border-red-500/60 hover:bg-red-950/20 transition-all group"
		>
			<div class="flex items-start justify-between mb-4">
				<div class="p-3 bg-red-500/10 rounded-lg">
					<Trash2 size={24} class="text-red-400" />
				</div>
				<ArrowRight size={20} class="text-zinc-600 group-hover:text-red-400 transition-colors" />
			</div>
			<h2 class="text-lg font-semibold text-zinc-100 mb-1">Reset Database</h2>
			<p class="text-sm text-zinc-500">Delete all records from all tables. Irreversible.</p>
		</button>

	</div>
</div>

<!-- Backup Modal -->
{#if showBackupModal}
	<div class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
		<div class="bg-zinc-900 border border-zinc-700 rounded-xl p-6 w-full max-w-lg max-h-[90vh] flex flex-col">
			<div class="flex items-center justify-between mb-4">
				<div class="flex items-center gap-3">
					<div class="p-2 bg-amber-500/10 rounded-lg">
						<Save size={20} class="text-amber-400" />
					</div>
					<h2 class="text-xl font-semibold text-zinc-100">Database Backup</h2>
				</div>
				<button onclick={() => showBackupModal = false} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">&times;</button>
			</div>

			{#if backupDir}
				<p class="text-xs text-zinc-500 mb-4 font-mono truncate">{backupDir}</p>
			{/if}

			<button
				onclick={createBackup}
				disabled={backingUp}
				class="w-full py-2.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2 mb-3"
			>
				<Save size={15} />
				{backingUp ? 'Creating backup…' : 'Create Backup Now'}
			</button>

			{#if backupResult}
				<div class="mb-3 p-3 rounded-lg text-xs {backupResult.includes('saved') ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}">
					{backupResult}
				</div>
			{/if}

			{#if restoreResult}
				<div class="mb-3 p-3 rounded-lg text-xs {restoreResult.includes('success') || restoreResult.includes('Restored') ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}">
					{restoreResult}
				</div>
			{/if}

			<div class="flex-1 overflow-y-auto">
				<h3 class="text-sm font-medium text-zinc-400 mb-2">Available Backups</h3>
				{#if loadingBackups}
					<p class="text-xs text-zinc-500">Loading…</p>
				{:else if backups.length === 0}
					<p class="text-xs text-zinc-500">No backups found.</p>
				{:else}
					<div class="space-y-1.5">
						{#each backups as b}
							<div class="flex items-center justify-between bg-zinc-800 rounded-lg px-3 py-2">
								<div>
									<span class="text-sm text-zinc-200">{b.label}</span>
									<span class="ml-2 text-xs text-zinc-500">{b.size_kb.toLocaleString()} KB</span>
								</div>
								<button
									onclick={() => restoreBackup(b.path)}
									disabled={restoringPath === b.path}
									class="text-xs px-2.5 py-1 rounded bg-zinc-700 hover:bg-amber-700 text-zinc-300 hover:text-white transition-colors flex items-center gap-1 disabled:opacity-50"
								>
									<RotateCcw size={11} />
									{restoringPath === b.path ? 'Restoring…' : 'Restore'}
								</button>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}

{#if showResetModal}
	<div class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
		<div class="bg-zinc-900 border border-red-900/60 rounded-xl p-6 w-full max-w-md">
			<div class="flex items-center gap-3 mb-4">
				<div class="p-2 bg-red-500/10 rounded-lg">
					<Trash2 size={20} class="text-red-400" />
				</div>
				<h2 class="text-xl font-semibold text-red-400">Reset Database</h2>
			</div>

			<p class="text-sm text-zinc-400 mb-2">
				This will permanently delete <strong class="text-zinc-100">all photos, faces, tags, cameras, and logs</strong> from the database. The files on disk are untouched.
			</p>
			<p class="text-sm text-zinc-500 mb-4">Type <span class="font-mono font-bold text-red-400">DELETE</span> to confirm.</p>

			<input
				type="text"
				bind:value={resetConfirmText}
				placeholder="DELETE"
				class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-zinc-100 font-mono focus:outline-none focus:border-red-500 mb-4"
			/>

			{#if resetResult}
				<div class="mb-4 p-3 rounded-lg {resetResult.startsWith('Done') ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'} text-sm">
					{resetResult}
				</div>
			{/if}

			<div class="flex gap-3 justify-end">
				<button
					onclick={() => { showResetModal = false; }}
					disabled={resetting}
					class="px-4 py-2 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors disabled:opacity-50"
				>
					Cancel
				</button>
				<button
					onclick={resetDatabase}
					disabled={resetting || resetConfirmText !== 'DELETE'}
					class="px-4 py-2 rounded-lg bg-red-600 text-white font-medium hover:bg-red-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
				>
					{resetting ? 'Resetting…' : 'Reset Database'}
				</button>
			</div>
		</div>
	</div>
{/if}

{#if showImportModal}
	<div class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
		<div class="bg-zinc-900 border border-zinc-800 rounded-xl p-6 w-full max-w-md">
			<h2 class="text-xl font-semibold text-zinc-100 mb-4">Import Photos</h2>

			<div class="mb-4">
				<label for="import-path" class="block text-sm text-zinc-400 mb-2">Library Path</label>
				<input
					id="import-path"
					type="text"
					bind:value={importPath}
					placeholder="D:/Pictures and Videos"
					class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2 text-zinc-100 focus:outline-none focus:border-emerald-500"
				/>
			</div>

			{#if importResult}
				<div class="mb-4 p-3 rounded-lg {importResult.startsWith('Error') ? 'bg-red-500/10 text-red-400' : 'bg-emerald-500/10 text-emerald-400'} text-sm">
					{importResult}
				</div>
			{/if}

			<div class="flex gap-3 justify-end">
				<button
					onclick={() => showImportModal = false}
					disabled={importing}
					class="px-4 py-2 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors disabled:opacity-50"
				>
					Cancel
				</button>
				<button
					onclick={startImport}
					disabled={importing || !importPath}
					class="px-4 py-2 rounded-lg bg-emerald-500 text-zinc-900 font-medium hover:bg-emerald-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
				>
					{importing ? 'Importing...' : 'Start Import'}
				</button>
			</div>
		</div>
	</div>
{/if}

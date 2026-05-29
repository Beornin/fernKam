<script lang="ts">
	import { Download, RefreshCw, ArrowRight, Database, HardDrive, Activity } from '@lucide/svelte';

	let showImportModal = $state(false);
	let importPath = $state('');
	let importing = $state(false);
	let importResult = $state<string | null>(null);
	let quickScanning = $state(false);
	let quickScanResult = $state<string | null>(null);

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
				quickScanResult = `Scan started in background. Check debug_scan.log for progress.`;
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

	</div>
</div>

<!-- Import Modal -->
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

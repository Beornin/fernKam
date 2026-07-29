<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type PersonOut, type FaceCluster, type FaceOut } from '$lib/api';
	import {
		ScanFace, Check, X, User, RefreshCw, Scan, AlertCircle, AlertTriangle,
		Sparkles, UserPlus, EyeOff, ChevronLeft, ChevronRight, Search, HelpCircle, Eye, History, Undo2
	} from '@lucide/svelte';
	import PhotoLightbox from '$lib/components/PhotoLightbox.svelte';
	import PersonPicker from '$lib/components/PersonPicker.svelte';
	import FaceCloseup from '$lib/components/FaceCloseup.svelte';
	import { scoreBadgeClass } from '$lib/faceConfidence';

	// ---------------------------------------------------------------------
	// Global maintenance state (top bar)
	// ---------------------------------------------------------------------
	let unscannedCount = $state(0);
	let unassignedCount = $state(0);

	let faceScanTaskId = $state<string | null>(null);
	let faceScanMessage = $state('');
	let faceScanRunning = $state(false);

	let buildingCentroids = $state(false);
	let centroidsMessage = $state('');

	let droppingPreBirth = $state(false);
	let preBirthMessage = $state('');

	let sweeping = $state(false);
	let sweepMessage = $state('');

	async function loadCounts() {
		const [u, s] = await Promise.all([
			api.faces.unassignedCount(),
			api.photos.unscannedCount(),
		]);
		unassignedCount = u.count;
		unscannedCount = s.count;
	}

	async function startFaceScan() {
		const r = await api.sync.scanFaces({});
		faceScanTaskId = r.task_id;
		faceScanRunning = !!r.task_id;
		faceScanMessage = r.message;
		if (faceScanRunning) pollFaceScan();
	}

	async function cancelFaceScan() {
		if (!faceScanTaskId) return;
		await api.sync.cancelTask(faceScanTaskId);
	}

	async function pollFaceScan() {
		if (!faceScanTaskId) return;
		try {
			const r = await api.sync.tasks();
			const t = r.tasks.find(x => x.id === faceScanTaskId);
			if (!t) return;
			faceScanMessage = t.message;
			if (t.status === 'running') {
				setTimeout(pollFaceScan, 2000);
			} else {
				faceScanRunning = false;
				await loadCounts();
				await loadSidebar();
			}
		} catch (_e) {
			setTimeout(pollFaceScan, 5000);
		}
	}

	async function buildCentroids() {
		buildingCentroids = true;
		centroidsMessage = '';
		try {
			const r = await api.faces.buildCentroids();
			centroidsMessage = `Centroids built for ${r.updated} people`;
			await loadSidebar();
		} catch {
			centroidsMessage = 'Build failed';
		} finally {
			buildingCentroids = false;
			setTimeout(() => { centroidsMessage = ''; }, 4000);
		}
	}

	async function dropPreBirth(silent = false) {
		if (!silent) droppingPreBirth = true;
		try {
			const r = await api.faces.dropPreBirthSuggestions();
			if (r.cleared > 0) {
				await loadCounts();
				await loadSidebar();
				if (typeof selectedPersonId === 'number') await loadCandidates();
				if (!silent) preBirthMessage = `Cleared ${r.cleared} pre-birth suggestion${r.cleared !== 1 ? 's' : ''}`;
			} else if (!silent) {
				preBirthMessage = 'No pre-birth suggestions found';
			}
		} catch {
			if (!silent) preBirthMessage = 'Clear failed';
		} finally {
			droppingPreBirth = false;
			if (!silent) setTimeout(() => { preBirthMessage = ''; }, 4000);
		}
	}

	async function runAutoConfirmSweep() {
		sweeping = true;
		sweepMessage = '';
		try {
			const { task_id } = await api.faces.autoConfirmAll();
			while (true) {
				await new Promise(r => setTimeout(r, 1500));
				const { tasks } = await api.sync.tasks();
				const task = tasks.find(t => t.id === task_id);
				if (!task) break;
				sweepMessage = task.message || '';
				if (task.status === 'completed' || task.status === 'failed') break;
			}
			await loadCounts();
			await loadSidebar();
			if (typeof selectedPersonId === 'number') await loadCandidates();
		} finally {
			sweeping = false;
			sweepMessage = '';
		}
	}

	// ---------------------------------------------------------------------
	// Person sidebar
	// ---------------------------------------------------------------------
	interface SidebarPerson { person_id: number; person_name: string; count: number }
	let sidebarPeople = $state<SidebarPerson[]>([]);
	let selectedPersonId = $state<number | 'unknown' | 'auto' | null>(null);
	let sidebarSearch = $state('');
	let filteredSidebarPeople = $derived(
		sidebarSearch.trim()
			? sidebarPeople.filter(p => p.person_name.toLowerCase().includes(sidebarSearch.toLowerCase()))
			: sidebarPeople
	);

	async function loadSidebar() {
		try { sidebarPeople = await api.faces.suggestionsPeople(); }
		catch { sidebarPeople = []; }
	}

	function selectPerson(id: number | 'unknown' | 'auto') {
		if (selectedPersonId === id) return;
		selectedPersonId = id;
		excludedFaceIds = new Set();
		focusedIndex = 0;
		personTab = 'candidates';
		confirmedFaces = [];
		confirmedHasMore = true;
		if (id === 'unknown') loadClusters(0);
		else if (id === 'auto') loadAutoConfirmed(0);
		else loadCandidates();
	}

	// ---------------------------------------------------------------------
	// "Recently Auto-Confirmed" — audit queue for the aggressive sweep.
	// Least-confident-first (server-sorted); one click sends a mistake back
	// to the normal review pool instead of requiring you to hunt for it.
	// ---------------------------------------------------------------------
	const AUTO_PAGE = 60;
	let autoConfirmedFaces = $state<FaceOut[]>([]);
	let autoConfirmedTotal = $state(0);
	let autoConfirmedOffset = $state(0);
	let loadingAutoConfirmed = $state(false);

	async function loadAutoConfirmedCount() {
		try { autoConfirmedTotal = (await api.faces.recentAutoCount()).count; } catch { /* ignore */ }
	}

	async function loadAutoConfirmed(newOffset = 0) {
		loadingAutoConfirmed = true;
		try {
			const batch = await api.faces.recentAuto({ limit: AUTO_PAGE, offset: newOffset });
			autoConfirmedFaces = newOffset === 0 ? batch : [...autoConfirmedFaces, ...batch];
			autoConfirmedOffset = newOffset;
		} finally {
			loadingAutoConfirmed = false;
		}
	}

	async function sendBackToReview(face: FaceOut) {
		await api.faces.update(face.id, { status: 'unconfirmed', person_tag_id: null });
		autoConfirmedFaces = autoConfirmedFaces.filter(f => f.id !== face.id);
		autoConfirmedTotal = Math.max(0, autoConfirmedTotal - 1);
		unassignedCount++;
	}

	// ---------------------------------------------------------------------
	// Candidate grid (per-person)
	// ---------------------------------------------------------------------
	interface Candidate { face_id: string; photo_id: number; status: string; score: number; conflict: boolean }
	let candidates = $state<Candidate[]>([]);
	let loadingCandidates = $state(false);
	let confidenceThreshold = $state(0.5);
	let excludedFaceIds = $state<Set<string>>(new Set());
	let focusedIndex = $state(0);
	let candidateActionBusy = $state(false);

	let eligibleCandidates = $derived(
		candidates.filter(c => c.score >= confidenceThreshold && !c.conflict && !excludedFaceIds.has(c.face_id))
	);

	async function loadCandidates() {
		if (typeof selectedPersonId !== 'number') return;
		loadingCandidates = true;
		try {
			candidates = await api.faces.candidates({ person_tag_id: selectedPersonId, limit: 500, min_score: 0.35 });
			focusedIndex = 0;
		} finally {
			loadingCandidates = false;
		}
	}

	// ---------------------------------------------------------------------
	// Confirmed faces (per-person) — browse what's already tagged for this
	// person, close-up, to catch and fix wrong assignments.
	// ---------------------------------------------------------------------
	const CONFIRMED_PAGE = 60;
	let personTab = $state<'candidates' | 'confirmed'>('candidates');
	let confirmedFaces = $state<FaceOut[]>([]);
	let confirmedOffset = $state(0);
	let loadingConfirmed = $state(false);
	let confirmedHasMore = $state(true);
	let closeupStartIndex = $state<number | null>(null);

	async function loadConfirmedFaces(newOffset = 0) {
		if (typeof selectedPersonId !== 'number') return;
		loadingConfirmed = true;
		try {
			const batch = await api.faces.list({ person_tag_id: selectedPersonId, status: 'confirmed', limit: CONFIRMED_PAGE, offset: newOffset });
			confirmedFaces = newOffset === 0 ? batch : [...confirmedFaces, ...batch];
			confirmedOffset = newOffset;
			confirmedHasMore = batch.length === CONFIRMED_PAGE;
		} finally {
			loadingConfirmed = false;
		}
	}

	function selectPersonTab(tab: 'candidates' | 'confirmed') {
		if (personTab === tab) return;
		personTab = tab;
		if (tab === 'confirmed' && confirmedFaces.length === 0) loadConfirmedFaces(0);
	}

	function onCloseupMutate(faceId: string, action: 'reassigned' | 'deleted' | 'unconfirmed') {
		if (action === 'unconfirmed') unassignedCount++;
	}

	async function confirmFace(faceId: string) {
		if (typeof selectedPersonId !== 'number') return;
		try {
			const res = await fetch(`/api/faces/${faceId}`, {
				method: 'PATCH', headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ person_tag_id: selectedPersonId, status: 'confirmed' }),
			});
			if (!res.ok && res.status !== 409) return;
		} catch { return; }
		candidates = candidates.filter(c => c.face_id !== faceId);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	function skipFace(faceId: string) {
		// Client-side dismiss only — live embedding search, so the face may
		// resurface next visit. Use Ignore for a permanent removal.
		candidates = candidates.filter(c => c.face_id !== faceId);
	}

	async function ignoreFace(faceId: string) {
		await api.faces.update(faceId, { status: 'ignored', person_tag_id: null });
		candidates = candidates.filter(c => c.face_id !== faceId);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	async function deleteFaceCandidate(faceId: string) {
		await api.faces.delete(faceId);
		candidates = candidates.filter(c => c.face_id !== faceId);
		unassignedCount = Math.max(0, unassignedCount - 1);
	}

	async function confirmAllEligible() {
		if (!eligibleCandidates.length || candidateActionBusy || typeof selectedPersonId !== 'number') return;
		candidateActionBusy = true;
		try {
			const ids = eligibleCandidates.map(c => c.face_id);
			await api.faces.batchAssign({ face_ids: ids, person_tag_id: selectedPersonId, status: 'confirmed' });
			const idSet = new Set(ids);
			candidates = candidates.filter(c => !idSet.has(c.face_id));
			unassignedCount = Math.max(0, unassignedCount - ids.length);
			await loadSidebar();
		} finally {
			candidateActionBusy = false;
		}
	}

	function skipAllShown() { candidates = []; }

	function toggleExcluded(faceId: string) {
		const next = new Set(excludedFaceIds);
		if (next.has(faceId)) next.delete(faceId); else next.add(faceId);
		excludedFaceIds = next;
	}

	function candidateKeydown(e: KeyboardEvent) {
		if (typeof selectedPersonId !== 'number' || !candidates.length) return;
		const target = e.target as HTMLElement | null;
		if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;
		const k = e.key.toLowerCase();
		if (e.key === 'ArrowRight') { e.preventDefault(); focusedIndex = Math.min(candidates.length - 1, focusedIndex + 1); }
		else if (e.key === 'ArrowLeft') { e.preventDefault(); focusedIndex = Math.max(0, focusedIndex - 1); }
		else if (k === 'enter' || k === 'a') { e.preventDefault(); const c = candidates[focusedIndex]; if (c) confirmFace(c.face_id); }
		else if (k === 'r' || k === 'x') { e.preventDefault(); const c = candidates[focusedIndex]; if (c) skipFace(c.face_id); }
		else if (k === 'i') { e.preventDefault(); const c = candidates[focusedIndex]; if (c) ignoreFace(c.face_id); }
		else if (k === 'd') { e.preventDefault(); const c = candidates[focusedIndex]; if (c) deleteFaceCandidate(c.face_id); }
	}

	// ---------------------------------------------------------------------
	// "Unknown" bucket — cluster browsing (ported from Cluster Review)
	// ---------------------------------------------------------------------
	const CLUSTER_PAGE = 20;
	let clusters = $state<FaceCluster[]>([]);
	let clusterTotal = $state(0);
	let clusterIdx = $state(0);
	let clusterOffset = $state(0);
	let loadingClusters = $state(true);
	let clusterActing = $state(false);
	let clusterDone = $state(0);

	// Exclusion is always live (click a thumbnail to drop it from the batch
	// action) — no separate "select mode" to toggle first. Only the shown
	// (representative) subset can be individually excluded; clusters can run
	// into the thousands of faces, so we never render them all.
	const DISPLAY_LIMIT = 24;
	let deselected = $state<Set<string>>(new Set());
	let previewPhotoId = $state<number | null>(null);

	let rebuilding = $state(false);
	let rebuildMsg = $state('');
	let autoAssigning = $state(false);
	let autoAssignMsg = $state('');
	let autoAssignThresh = $state(0.82);
	let autoAssignOpen = $state(false);

	let currentCluster = $derived(clusters[clusterIdx] ?? null);
	let displayedFaces = $derived(currentCluster?.faces.slice(0, DISPLAY_LIMIT) ?? []);

	function activeClusterFaceIds(): string[] {
		if (!currentCluster) return [];
		if (deselected.size === 0) return currentCluster.faces.map(f => f.id);
		return currentCluster.faces.filter(f => !deselected.has(f.id)).map(f => f.id);
	}

	async function loadClusters(newOffset = 0) {
		loadingClusters = true;
		try {
			const res = await api.faces.clusters({ offset: newOffset, limit: CLUSTER_PAGE });
			clusters = res.clusters;
			clusterTotal = res.total;
			clusterOffset = newOffset;
			clusterIdx = 0;
			deselected = new Set();
		} finally {
			loadingClusters = false;
		}
	}

	async function advanceCluster() {
		deselected = new Set();
		if (clusterIdx < clusters.length - 1) {
			clusterIdx++;
		} else {
			const nextOffset = clusterOffset + CLUSTER_PAGE;
			await loadClusters(nextOffset < clusterTotal ? nextOffset : 0);
		}
	}

	function removeCurrentClusterFromView() {
		clusters = clusters.filter((_, i) => i !== clusterIdx);
		if (clusterIdx >= clusters.length && clusterIdx > 0) clusterIdx = clusters.length - 1;
		deselected = new Set();
	}

	async function assignClusterTo(personTagId: number) {
		const ids = activeClusterFaceIds();
		if (!ids.length || clusterActing) return;
		clusterActing = true;
		try {
			await api.faces.batchAssign({ face_ids: ids, person_tag_id: personTagId, status: 'confirmed' });
			clusterDone += ids.length;
			clusterTotal = Math.max(0, clusterTotal - 1);
			pickerOpen = false;
			removeCurrentClusterFromView();
			if (clusters.length === 0) await loadClusters(0);
			await loadSidebar();
			await loadCounts();
		} catch (e) {
			alert(`Failed to assign: ${e}`);
		} finally {
			clusterActing = false;
		}
	}

	async function ignoreCluster() {
		const ids = activeClusterFaceIds();
		if (!ids.length || clusterActing) return;
		clusterActing = true;
		try {
			await api.faces.batchAssign({ face_ids: ids, person_tag_id: null, status: 'ignored' });
			clusterDone += ids.length;
			clusterTotal = Math.max(0, clusterTotal - 1);
			removeCurrentClusterFromView();
			if (clusters.length === 0) await loadClusters(0);
			await loadCounts();
		} catch (e) {
			alert(`Failed to ignore: ${e}`);
		} finally {
			clusterActing = false;
		}
	}

	async function deleteCluster() {
		const ids = activeClusterFaceIds();
		if (!ids.length || clusterActing) return;
		if (!confirm(`Permanently delete ${ids.length} face record(s)?`)) return;
		clusterActing = true;
		try {
			await api.faces.batchDelete(ids);
			clusterDone += ids.length;
			clusterTotal = Math.max(0, clusterTotal - 1);
			removeCurrentClusterFromView();
			if (clusters.length === 0) await loadClusters(0);
			await loadCounts();
		} catch (e) {
			alert(`Failed to delete: ${e}`);
		} finally {
			clusterActing = false;
		}
	}

	function toggleClusterFace(id: string) {
		const next = new Set(deselected);
		if (next.has(id)) next.delete(id); else next.add(id);
		deselected = next;
	}

	function skipCluster() { advanceCluster(); }
	function backCluster() { if (clusterIdx > 0) { clusterIdx--; deselected = new Set(); } }

	async function autoAssignClusters() {
		if (autoAssigning) return;
		autoAssigning = true;
		autoAssignMsg = 'Starting…';
		try {
			const { task_id } = await api.faces.clustersAutoAssign({ min_score: autoAssignThresh });
			const poll = setInterval(async () => {
				try {
					const { tasks } = await api.sync.tasks();
					const t = tasks.find(t => t.id === task_id);
					if (!t) return;
					autoAssignMsg = t.message;
					if (t.status === 'completed' || t.status === 'failed') {
						clearInterval(poll);
						autoAssigning = false;
						if (t.status === 'completed') {
							people = await api.people.list({ limit: 500 });
							await loadClusters(0);
							await loadSidebar();
							await loadCounts();
						}
					}
				} catch { /* ignore poll errors */ }
			}, 1500);
		} catch (e) {
			autoAssigning = false;
			autoAssignMsg = '';
			alert(`Auto-assign failed: ${e}`);
		}
	}

	async function rebuildClusters() {
		if (rebuilding) return;
		rebuilding = true;
		rebuildMsg = 'Starting…';
		try {
			const { task_id } = await api.faces.clustersRebuild({});
			const poll = setInterval(async () => {
				try {
					const { tasks } = await api.sync.tasks();
					const t = tasks.find(t => t.id === task_id);
					if (!t) return;
					rebuildMsg = t.message;
					if (t.status === 'completed' || t.status === 'failed') {
						clearInterval(poll);
						rebuilding = false;
						if (t.status === 'completed') await loadClusters(0);
					}
				} catch { /* ignore poll errors */ }
			}, 1500);
		} catch (e) {
			rebuilding = false;
			rebuildMsg = '';
			alert(`Rebuild failed: ${e}`);
		}
	}

	async function ignoreTinyFaces() {
		if (clusterActing) return;
		if (!confirm('Ignore all unconfirmed faces smaller than 50px? This clears junk crops.')) return;
		clusterActing = true;
		try {
			const { ignored } = await api.faces.ignoreTiny(50);
			alert(`Ignored ${ignored} tiny faces. Rebuild clusters to refresh.`);
		} catch (e) {
			alert(`Failed: ${e}`);
		} finally {
			clusterActing = false;
		}
	}

	function clusterKeydown(e: KeyboardEvent) {
		const target = e.target as HTMLElement | null;
		const isTyping = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
		if (previewPhotoId !== null) {
			if (e.key === 'Escape') previewPhotoId = null;
			return;
		}
		if (pickerOpen) return; // PersonPicker handles its own Escape/Enter
		if (isTyping) return;
		if (!currentCluster) return;
		const k = e.key.toLowerCase();
		if ((k === 'a' || e.key === 'Enter') && currentCluster.suggested_person) { e.preventDefault(); assignClusterTo(currentCluster.suggested_person.person_id); }
		else if (k === 'n' || k === 'o') { e.preventDefault(); pickerOpen = true; }
		else if (k === 'i') { e.preventDefault(); ignoreCluster(); }
		else if (k === 'd') { e.preventDefault(); deleteCluster(); }
		else if (k === 's' || e.key === 'ArrowRight') { e.preventDefault(); skipCluster(); }
		else if (e.key === 'ArrowLeft') { e.preventDefault(); backCluster(); }
	}

	// ---------------------------------------------------------------------
	// Shared: people list (for the header name lookup) + assign picker
	// ---------------------------------------------------------------------
	let people = $state<PersonOut[]>([]);
	let pickerOpen = $state(false);

	function handleKeydown(e: KeyboardEvent) {
		if (selectedPersonId === 'unknown') clusterKeydown(e);
		else if (typeof selectedPersonId === 'number') candidateKeydown(e);
	}

	onMount(() => {
		loadCounts();
		loadSidebar();
		loadAutoConfirmedCount();
		(async () => { people = await api.people.list({ limit: 500 }); })();
		dropPreBirth(true);
		// Seed both bulk-confirm sliders from the one shared sensitivity setting
		// (same value the background sweep uses) so "aggressive automation" means
		// the same thing here as it does unattended — instead of two unrelated
		// hardcoded defaults the user has to rediscover per view.
		api.faces.getSensitivity().then(s => {
			confidenceThreshold = s.auto_confirm_thresh;
			autoAssignThresh = s.auto_confirm_thresh;
		}).catch(() => {});
		(async () => {
			try {
				const r = await api.sync.tasks();
				const t = r.tasks.find(x => x.task_type === 'scan_faces' && x.status === 'running');
				if (t) {
					faceScanTaskId = t.id;
					faceScanRunning = true;
					faceScanMessage = t.message;
					pollFaceScan();
				}
			} catch {}
		})();
	});
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- unified person picker (search existing or create new — cluster "Unknown" flow) -->
<PersonPicker bind:open={pickerOpen} onPick={assignClusterTo} title="Search or create person…" />

{#if previewPhotoId !== null}
	<PhotoLightbox photoId={previewPhotoId} onClose={() => previewPhotoId = null} />
{/if}

<div class="flex flex-col h-full bg-zinc-950">
	<!-- Header -->
	<div class="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3 flex-wrap">
		<ScanFace size={20} class="text-emerald-400" />
		<h1 class="text-base font-semibold text-zinc-100">Face Review</h1>
		<span class="text-xs text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded-full">{unassignedCount} to review</span>
		{#if unscannedCount > 0}
			<span class="text-xs text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded-full flex items-center gap-1">
				<AlertCircle size={11} /> {unscannedCount} unscanned photos
			</span>
		{/if}
		<div class="ml-auto flex items-center gap-2">
			{#if faceScanRunning}
				<button onclick={cancelFaceScan} class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-red-700 text-zinc-300 hover:text-white flex items-center gap-1" title={faceScanMessage}>
					<X size={12} /> Cancel scan
				</button>
				<span class="text-xs text-zinc-400 max-w-xs truncate" title={faceScanMessage}>{faceScanMessage}</span>
			{:else if unscannedCount > 0}
				<button onclick={startFaceScan} class="text-xs px-3 py-1.5 rounded bg-amber-700 hover:bg-amber-600 text-white flex items-center gap-1" title={`Detect faces on ${unscannedCount} unscanned photos`}>
					<ScanFace size={12} /> Scan {unscannedCount}
				</button>
			{/if}
			<button onclick={buildCentroids} disabled={buildingCentroids} class="text-xs px-3 py-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 flex items-center gap-1 disabled:opacity-50" title="Recompute per-person centroid embeddings from all confirmed faces">
				<RefreshCw size={12} class={buildingCentroids ? 'animate-spin' : ''} /> {buildingCentroids ? 'Building…' : 'Build Centroids'}
			</button>
			{#if centroidsMessage}<span class="text-xs text-emerald-400">{centroidsMessage}</span>{/if}
			<button onclick={() => dropPreBirth(false)} disabled={droppingPreBirth} class="text-xs px-3 py-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 flex items-center gap-1 disabled:opacity-50" title="Clear pre-birth-date suggestions for configured people">
				<X size={12} /> {droppingPreBirth ? 'Clearing…' : 'Drop Pre-Birth'}
			</button>
			{#if preBirthMessage}<span class="text-xs text-amber-400">{preBirthMessage}</span>{/if}
			<button onclick={runAutoConfirmSweep} disabled={sweeping || unassignedCount === 0} class="text-xs px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1 disabled:opacity-50" title={unassignedCount === 0 ? 'No faces to review' : `Run auto-confirm sweep on ${unassignedCount} faces`}>
				<Scan size={12} class={sweeping ? 'animate-spin' : ''} /> {sweeping ? 'Sweeping…' : `Auto Sweep ${unassignedCount}`}
			</button>
			{#if sweeping}
				<span class="text-xs text-zinc-400 max-w-md truncate" title={sweepMessage}>{sweepMessage}</span>
			{/if}
		</div>
	</div>

	<!-- Body: person sidebar + right pane -->
	<div class="flex flex-1 overflow-hidden">
		<aside class="w-60 shrink-0 border-r border-zinc-800 flex flex-col overflow-hidden bg-zinc-950">
			<div class="p-2 border-b border-zinc-800">
				<div class="relative">
					<Search size={12} class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
					<input type="text" bind:value={sidebarSearch} placeholder="Search people…"
						class="w-full bg-zinc-800 border border-zinc-700 rounded pl-6 pr-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-emerald-500" />
				</div>
			</div>
			<nav class="flex-1 overflow-y-auto py-1">
				<button
					onclick={() => selectPerson('unknown')}
					class="w-full text-left flex items-center gap-2 px-3 py-2 transition-colors {selectedPersonId === 'unknown' ? 'bg-violet-600/20 text-violet-300' : 'text-zinc-300 hover:bg-zinc-800'}"
				>
					<HelpCircle size={14} class="shrink-0" />
					<span class="flex-1 text-sm truncate">Unknown faces</span>
				</button>
				<button
					onclick={() => selectPerson('auto')}
					class="w-full text-left flex items-center gap-2 px-3 py-2 transition-colors {selectedPersonId === 'auto' ? 'bg-amber-600/20 text-amber-300' : 'text-zinc-300 hover:bg-zinc-800'}"
				>
					<History size={14} class="shrink-0" />
					<span class="flex-1 text-sm truncate">Recently auto-confirmed</span>
					{#if autoConfirmedTotal > 0}<span class="text-xs text-zinc-500">{autoConfirmedTotal}</span>{/if}
				</button>
				<div class="border-t border-zinc-800 my-1"></div>
				{#if filteredSidebarPeople.length === 0}
					<p class="text-xs text-zinc-600 px-3 py-3">No candidate faces found</p>
				{:else}
					{#each filteredSidebarPeople as p (p.person_id)}
						<button
							onclick={() => selectPerson(p.person_id)}
							class="w-full text-left flex items-center gap-2 px-3 py-1.5 transition-colors {selectedPersonId === p.person_id ? 'bg-emerald-600/20 text-emerald-300' : 'text-zinc-300 hover:bg-zinc-800'}"
						>
							<div class="w-5 h-5 rounded-full bg-zinc-700 flex items-center justify-center text-zinc-400 shrink-0"><User size={10} /></div>
							<span class="flex-1 text-sm truncate">{p.person_name}</span>
							<span class="text-xs text-zinc-500">{p.count}</span>
						</button>
					{/each}
				{/if}
			</nav>
		</aside>

		<div class="flex-1 overflow-y-auto">
			{#if selectedPersonId === null}
				<div class="flex flex-col items-center justify-center h-full gap-3 text-zinc-600">
					<ScanFace size={48} />
					<p class="text-sm font-medium text-zinc-400">Pick a person on the left</p>
					<p class="text-xs text-zinc-500">Or browse "Unknown faces" to triage unclustered faces, or check "Recently auto-confirmed" to audit what the sweep did</p>
				</div>
			{:else if selectedPersonId === 'unknown'}
				<div class="p-5">
					<div class="flex items-center justify-between mb-4 flex-wrap gap-2">
						<div class="flex items-center gap-2">
							<Sparkles size={16} class="text-violet-400" />
							<h2 class="text-sm font-semibold text-zinc-200">Unknown faces</h2>
							{#if clusterTotal > 0}<span class="text-xs text-zinc-400">{clusterTotal} clusters left · {clusterDone} faces done</span>{/if}
						</div>
						<div class="flex items-center gap-2">
							<button onclick={ignoreTinyFaces} disabled={clusterActing} class="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40 flex items-center gap-1">
								<EyeOff size={12} /> Ignore tiny
							</button>
							<button onclick={rebuildClusters} disabled={rebuilding} class="text-xs px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-50 flex items-center gap-1">
								<RefreshCw size={12} class={rebuilding ? 'animate-spin' : ''} /> {rebuilding ? 'Building…' : 'Rebuild'}
							</button>
							<button onclick={() => autoAssignOpen = !autoAssignOpen} disabled={autoAssigning} class="text-xs px-2 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-50 flex items-center gap-1">
								<Sparkles size={12} class={autoAssigning ? 'animate-pulse' : ''} /> {autoAssigning ? 'Running…' : 'Auto-assign'}
							</button>
						</div>
					</div>

					{#if autoAssignOpen}
					<div class="flex items-center gap-3 px-4 py-2 mb-3 bg-emerald-900/30 border border-emerald-700/30 rounded-lg text-xs text-emerald-200">
						<span class="text-emerald-400">Min confidence:</span>
						<input type="range" min="0.70" max="0.95" step="0.01" bind:value={autoAssignThresh} class="w-28 accent-emerald-400" />
						<span class="w-8 text-right font-mono">{autoAssignThresh.toFixed(2)}</span>
						<span class="text-zinc-500">— clusters scoring above this are auto-confirmed</span>
						<div class="flex-1"></div>
						<button onclick={autoAssignClusters} disabled={autoAssigning} class="px-3 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 font-medium">Run</button>
						<button onclick={() => autoAssignOpen = false} class="px-2 py-1 rounded hover:bg-zinc-700 text-zinc-400"><X size={12} /></button>
					</div>
					{/if}

					{#if rebuilding && rebuildMsg}<div class="px-4 py-1.5 mb-2 bg-violet-600/10 text-violet-300 text-xs rounded">{rebuildMsg}</div>{/if}
					{#if autoAssigning && autoAssignMsg}<div class="px-4 py-1.5 mb-2 bg-emerald-600/10 text-emerald-300 text-xs rounded">{autoAssignMsg}</div>{/if}

					{#if loadingClusters}
						<div class="flex justify-center py-20"><div class="w-6 h-6 border-2 border-zinc-700 border-t-violet-400 rounded-full animate-spin"></div></div>
					{:else if !currentCluster}
						<div class="flex flex-col items-center justify-center py-20 text-center gap-3">
							<p class="text-zinc-400 text-sm">No clusters to review.</p>
							<p class="text-zinc-600 text-xs max-w-md">Click <span class="text-violet-300">Rebuild</span> to group the remaining unconfirmed faces, then review each group here.</p>
						</div>
					{:else}
						<div class="max-w-3xl mx-auto">
							<div class="flex items-center justify-between mb-4">
								<div class="flex items-center gap-2">
									<button onclick={backCluster} disabled={clusterIdx === 0} class="p-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-30"><ChevronLeft size={16} /></button>
									<span class="text-xs text-zinc-400">Cluster {clusterIdx + 1} · {clusterTotal} remaining</span>
									<button onclick={skipCluster} class="p-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300"><ChevronRight size={16} /></button>
								</div>
								<span class="text-xs text-zinc-500">{activeClusterFaceIds().length} / {currentCluster.size} will be included</span>
							</div>

							<!-- Hero: big suggested-person card, single keystroke to confirm the whole cluster -->
							{#if currentCluster.suggested_person && currentCluster.suggested_person.person_name}
								<button onclick={() => assignClusterTo(currentCluster.suggested_person!.person_id)} disabled={clusterActing}
									class="w-full mb-4 flex items-center gap-4 px-5 py-4 rounded-xl bg-emerald-600/15 border border-emerald-600/40 hover:bg-emerald-600/25 disabled:opacity-50 transition-colors text-left">
									{#if displayedFaces[0]}
										<img src="/media/face/{displayedFaces[0].id}?size=150" alt="" class="w-16 h-16 rounded-lg object-cover shrink-0 border border-emerald-500/40" />
									{/if}
									<div class="flex-1 min-w-0">
										<p class="text-[10px] uppercase tracking-wider text-emerald-400/80 mb-0.5">Looks like</p>
										<p class="text-lg font-semibold text-emerald-100 truncate">{currentCluster.suggested_person.person_name}</p>
									</div>
									<span class="text-xs font-semibold px-2 py-1 rounded {scoreBadgeClass(currentCluster.suggested_person.score)} shrink-0">
										{Math.round(currentCluster.suggested_person.score * 100)}%
									</span>
									<kbd class="text-[10px] bg-black/30 text-emerald-200 px-2 py-1 rounded shrink-0">Enter / A</kbd>
								</button>
							{:else}
								<div class="w-full mb-4 px-5 py-4 rounded-xl bg-zinc-900 border border-zinc-800 text-center text-sm text-zinc-500">
									No confident match — pick a person or ignore this group
								</div>
							{/if}

							<!-- Representative faces (bounded — clusters can run into the thousands) -->
							<div class="grid gap-2 mb-1" style="grid-template-columns: repeat(auto-fill, minmax(110px, 1fr))">
								{#each displayedFaces as f (f.id)}
									{@const isOut = deselected.has(f.id)}
									<div class="relative group">
										<button
											onclick={() => toggleClusterFace(f.id)}
											class="relative aspect-square w-full rounded-lg overflow-hidden border transition-all {isOut ? 'border-zinc-800 opacity-30' : 'border-zinc-700 hover:border-violet-500'}"
											title={isOut ? 'Excluded — click to include' : 'Click to exclude from batch action'}
										>
											<img src="/media/face/{f.id}?size=150" alt="Cluster face" class="w-full h-full object-cover" loading="lazy" />
											{#if f.score !== undefined}
												<span class="absolute top-1 left-1 text-[9px] font-semibold px-1 rounded {scoreBadgeClass(f.score)}">{Math.round(f.score * 100)}%</span>
											{/if}
											<div class="absolute bottom-1 right-1 w-4 h-4 rounded flex items-center justify-center {isOut ? 'bg-black/50 text-zinc-500' : 'bg-violet-500 text-white'}">
												{#if !isOut}<Check size={10} />{/if}
											</div>
										</button>
										<button
											onclick={() => previewPhotoId = f.photo_id}
											class="absolute top-1 right-1 p-1 rounded bg-black/60 text-zinc-300 opacity-0 group-hover:opacity-100 hover:text-white transition-opacity"
											title="View full photo"
										><Eye size={11} /></button>
									</div>
								{/each}
							</div>
							{#if currentCluster.size > DISPLAY_LIMIT}
								<p class="text-[11px] text-zinc-600 mb-3">Showing {DISPLAY_LIMIT} of {currentCluster.size} — the action below still applies to all of them (minus anything excluded above)</p>
							{:else}
								<div class="mb-3"></div>
							{/if}

							<div class="flex items-center gap-2 flex-wrap">
								<button onclick={() => pickerOpen = true} class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1">
									<Search size={12} /> Assign to… <kbd class="text-[10px] bg-black/30 px-1 rounded">O</kbd> / <kbd class="text-[10px] bg-black/30 px-1 rounded">N</kbd>
								</button>
								<button onclick={ignoreCluster} disabled={clusterActing} class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-amber-700 text-zinc-300 hover:text-white flex items-center gap-1 disabled:opacity-50">
									<EyeOff size={12} /> Ignore <kbd class="text-[10px] bg-black/30 px-1 rounded">I</kbd>
								</button>
								<button onclick={deleteCluster} disabled={clusterActing} class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-red-700 text-zinc-300 hover:text-white flex items-center gap-1 disabled:opacity-50">
									<X size={12} /> Delete <kbd class="text-[10px] bg-black/30 px-1 rounded">D</kbd>
								</button>
								<button onclick={skipCluster} class="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1 ml-auto">
									Skip <kbd class="text-[10px] bg-black/30 px-1 rounded">S</kbd>
								</button>
							</div>
						</div>
					{/if}
				</div>
			{:else if selectedPersonId === 'auto'}
				<div class="p-5">
					<div class="flex items-center gap-2 mb-1">
						<History size={16} class="text-amber-400" />
						<h2 class="text-sm font-semibold text-zinc-200">Recently auto-confirmed</h2>
						<span class="text-xs text-zinc-400">{autoConfirmedTotal} total</span>
					</div>
					<p class="text-[11px] text-zinc-500 mb-3">Sorted least-confident-first — the ones most likely to be wrong are at the top. Click <Undo2 size={10} class="inline" /> to send a mistake back to review.</p>

					{#if loadingAutoConfirmed && autoConfirmedFaces.length === 0}
						<div class="flex items-center justify-center h-48 text-zinc-500">
							<div class="w-6 h-6 border-2 border-zinc-700 border-t-amber-400 rounded-full animate-spin"></div>
						</div>
					{:else if autoConfirmedFaces.length === 0}
						<div class="flex flex-col items-center justify-center h-48 gap-2 text-zinc-600">
							<Check size={32} />
							<p class="text-sm">Nothing auto-confirmed yet</p>
						</div>
					{:else}
						<div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(130px, 1fr))">
							{#each autoConfirmedFaces as f (f.id)}
								<div class="relative group rounded-lg overflow-hidden border border-zinc-800">
									<div class="aspect-square bg-zinc-800">
										<img src="/media/face/{f.id}?size=200" alt="Auto-confirmed face" class="w-full h-full object-cover" loading="lazy" />
									</div>
									{#if f.score !== null}
										<div class="absolute top-1 left-1 text-[10px] font-semibold px-1.5 py-0.5 rounded {scoreBadgeClass(f.score)}">
											{Math.round(f.score * 100)}%
										</div>
									{/if}
									<div class="absolute bottom-0 inset-x-0 bg-black/70 text-white text-[10px] text-center py-0.5 truncate px-1">
										{f.person_name ?? '?'}
									</div>
									<button
										onclick={() => sendBackToReview(f)}
										class="absolute top-1 right-1 p-1 rounded bg-black/60 text-zinc-300 opacity-0 group-hover:opacity-100 hover:bg-red-600 hover:text-white transition-all"
										title="Wrong — send back to review"
									><Undo2 size={12} /></button>
								</div>
							{/each}
						</div>
						{#if autoConfirmedFaces.length < autoConfirmedTotal}
							<div class="flex justify-center mt-4">
								<button onclick={() => loadAutoConfirmed(autoConfirmedOffset + AUTO_PAGE)} disabled={loadingAutoConfirmed}
									class="text-xs px-4 py-2 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-50">
									{loadingAutoConfirmed ? 'Loading…' : `Load more (${autoConfirmedTotal - autoConfirmedFaces.length} left)`}
								</button>
							</div>
						{/if}
					{/if}
				</div>
			{:else}
				<div class="p-5">
					<div class="flex items-center gap-3 mb-2 flex-wrap">
						<h2 class="text-sm font-semibold text-zinc-200">
							{sidebarPeople.find(p => p.person_id === selectedPersonId)?.person_name ?? people.find(p => p.id === selectedPersonId)?.name ?? 'Person'}
						</h2>
						<div class="flex items-center rounded-lg bg-zinc-800 p-0.5 text-xs">
							<button onclick={() => selectPersonTab('candidates')}
								class="px-2.5 py-1 rounded-md transition-colors {personTab === 'candidates' ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200'}">
								Candidates {candidates.length}
							</button>
							<button onclick={() => selectPersonTab('confirmed')}
								class="px-2.5 py-1 rounded-md transition-colors {personTab === 'confirmed' ? 'bg-emerald-600 text-white' : 'text-zinc-400 hover:text-zinc-200'}">
								Confirmed
							</button>
						</div>
						{#if personTab === 'candidates'}
							<div class="flex items-center gap-2 ml-2">
								<span class="text-xs text-zinc-500">Confidence ≥</span>
								<input type="range" min="0.35" max="0.95" step="0.01" bind:value={confidenceThreshold} class="w-32 accent-emerald-400" />
								<span class="text-xs text-zinc-300 w-10 font-mono">{Math.round(confidenceThreshold * 100)}%</span>
							</div>
							<div class="ml-auto flex items-center gap-2">
								<button onclick={confirmAllEligible} disabled={!eligibleCandidates.length || candidateActionBusy}
									class="text-xs px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1 disabled:opacity-50">
									<Check size={12} /> Confirm all {eligibleCandidates.length}
								</button>
								<button onclick={skipAllShown} disabled={!candidates.length}
									class="text-xs px-3 py-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-300 flex items-center gap-1 disabled:opacity-50">
									<X size={12} /> Skip all
								</button>
							</div>
						{/if}
					</div>

					{#if personTab === 'candidates'}
						<p class="text-[11px] text-zinc-500 mb-3">← → move focus · Enter/A confirm · R/X skip · I ignore · D delete</p>

						{#if loadingCandidates}
							<div class="flex items-center justify-center h-48 text-zinc-500">
								<div class="w-6 h-6 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
							</div>
						{:else if candidates.length === 0}
							<div class="flex flex-col items-center justify-center h-48 gap-2 text-zinc-600">
								<Check size={32} />
								<p class="text-sm">No candidate faces for this person</p>
							</div>
						{:else}
							<div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))">
								{#each candidates as c, i (c.face_id)}
									{@const eligible = c.score >= confidenceThreshold && !c.conflict && !excludedFaceIds.has(c.face_id)}
									<div
										class="relative group rounded-lg overflow-hidden border transition-all {i === focusedIndex ? 'border-emerald-400 ring-2 ring-emerald-400/40' : eligible ? 'border-emerald-700/40' : 'border-zinc-800'}"
										onmouseenter={() => focusedIndex = i}
										role="group"
									>
										<div class="aspect-square bg-zinc-800">
											<img src="/media/face/{c.face_id}?size=200" alt="Face candidate" class="w-full h-full object-cover" loading="lazy" />
										</div>
										<div class="absolute top-1 left-1 text-[10px] font-semibold px-1.5 py-0.5 rounded {scoreBadgeClass(c.score)}">
											{Math.round(c.score * 100)}%
										</div>
										{#if c.conflict}
											<div class="absolute top-1 right-1 p-0.5 rounded bg-amber-900/80" title="Already confirmed for someone else in this photo">
												<AlertTriangle size={10} class="text-amber-300" />
											</div>
										{/if}
										<div class="absolute inset-x-0 bottom-0 flex bg-gradient-to-t from-black/80 to-transparent pt-4 transition-opacity {i === focusedIndex ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}">
											<button onclick={() => confirmFace(c.face_id)} disabled={c.conflict}
												class="flex-1 py-1.5 flex items-center justify-center text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-30 disabled:cursor-not-allowed" title="Confirm">
												<Check size={14} />
											</button>
											<button onclick={() => skipFace(c.face_id)}
												class="flex-1 py-1.5 flex items-center justify-center text-zinc-300 hover:bg-zinc-700/50" title="Skip">
												<X size={14} />
											</button>
										</div>
									</div>
								{/each}
							</div>
						{/if}
					{:else}
						<p class="text-[11px] text-zinc-500 mb-3">Click a face to review it close-up — reassign, mark unknown, or delete the tag.</p>

						{#if loadingConfirmed && confirmedFaces.length === 0}
							<div class="flex items-center justify-center h-48 text-zinc-500">
								<div class="w-6 h-6 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
							</div>
						{:else if confirmedFaces.length === 0}
							<div class="flex flex-col items-center justify-center h-48 gap-2 text-zinc-600">
								<User size={32} />
								<p class="text-sm">No confirmed faces for this person yet</p>
							</div>
						{:else}
							<div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))">
								{#each confirmedFaces as f, i (f.id)}
									<button
										onclick={() => closeupStartIndex = i}
										class="relative group rounded-lg overflow-hidden border border-zinc-800 hover:border-emerald-500/60 transition-colors"
									>
										<div class="aspect-square bg-zinc-800">
											<img src="/media/face/{f.id}?size=200" alt="Confirmed face" class="w-full h-full object-cover" loading="lazy" />
										</div>
										{#if f.score !== null}
											<div class="absolute top-1 left-1 text-[10px] font-semibold px-1.5 py-0.5 rounded {scoreBadgeClass(f.score)}">
												{Math.round(f.score * 100)}%
											</div>
										{/if}
									</button>
								{/each}
							</div>
							{#if confirmedHasMore}
								<div class="flex justify-center mt-4">
									<button onclick={() => loadConfirmedFaces(confirmedOffset + CONFIRMED_PAGE)} disabled={loadingConfirmed}
										class="text-xs px-4 py-2 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-50">
										{loadingConfirmed ? 'Loading…' : 'Load more'}
									</button>
								</div>
							{/if}
						{/if}
					{/if}
				</div>
			{/if}
		</div>
	</div>
</div>

{#if closeupStartIndex !== null}
	<FaceCloseup
		bind:faces={confirmedFaces}
		startIndex={closeupStartIndex}
		onClose={() => closeupStartIndex = null}
		onMutate={onCloseupMutate}
	/>
{/if}

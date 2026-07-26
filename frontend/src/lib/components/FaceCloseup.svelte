<script lang="ts">
	import { api, type FaceOut } from '$lib/api';
	import PersonPicker from './PersonPicker.svelte';
	import { X, ChevronLeft, ChevronRight, UserX, Trash2, HelpCircle, Loader2 } from '@lucide/svelte';
	import { scoreBadgeClass } from '$lib/faceConfidence';

	let {
		faces = $bindable(),
		startIndex,
		onClose,
		onMutate,
	}: {
		faces: FaceOut[];
		startIndex: number;
		onClose: () => void;
		onMutate?: (faceId: string, action: 'reassigned' | 'deleted' | 'unconfirmed') => void;
	} = $props();

	let index = $state(startIndex);
	let face = $derived(faces[index] ?? null);
	let pickerOpen = $state(false);
	let busy = $state(false);

	function prev() { if (index > 0) index--; }
	function next() { if (index < faces.length - 1) index++; }

	function afterMutate(faceId: string, action: 'reassigned' | 'deleted' | 'unconfirmed') {
		faces = faces.filter(f => f.id !== faceId);
		onMutate?.(faceId, action);
		if (faces.length === 0) { onClose(); return; }
		if (index >= faces.length) index = faces.length - 1;
	}

	async function handleDelete() {
		if (!face || busy) return;
		if (!confirm('Delete this face tag permanently? This removes the detection, not the photo.')) return;
		busy = true;
		try {
			await api.faces.delete(face.id);
			afterMutate(face.id, 'deleted');
		} catch (e) {
			alert(`Failed to delete: ${e}`);
		} finally {
			busy = false;
		}
	}

	async function handleUnknown() {
		if (!face || busy) return;
		busy = true;
		try {
			await api.faces.update(face.id, { status: 'unconfirmed', person_tag_id: null });
			afterMutate(face.id, 'unconfirmed');
		} catch (e) {
			alert(`Failed to send back to review: ${e}`);
		} finally {
			busy = false;
		}
	}

	async function handleReassign(personId: number) {
		if (!face) return;
		busy = true;
		try {
			const res = await fetch(`/api/faces/${face.id}`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ person_tag_id: personId, status: 'confirmed' }),
			});
			if (!res.ok && res.status !== 409) {
				alert(res.status === 409 ? 'That person is already tagged in this photo' : 'Failed to reassign');
				return;
			}
			afterMutate(face.id, 'reassigned');
		} catch (e) {
			alert(`Failed to reassign: ${e}`);
		} finally {
			busy = false;
		}
	}

	function onKeydown(e: KeyboardEvent) {
		if (pickerOpen) return;
		if (e.key === 'Escape') { e.preventDefault(); onClose(); }
		else if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
		else if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
		else if (e.key === 'w' || e.key === 'W') { e.preventDefault(); pickerOpen = true; }
		else if (e.key === 'u' || e.key === 'U') { e.preventDefault(); handleUnknown(); }
		else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); handleDelete(); }
	}
</script>

<svelte:window onkeydown={onKeydown} />

{#if face}
	<div class="fixed inset-0 z-50 bg-black/90 backdrop-blur-sm flex flex-col">
		<div class="shrink-0 flex items-center justify-between px-4 py-3">
			<span class="text-sm text-zinc-400">{index + 1} / {faces.length}</span>
			<button onclick={onClose} class="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors" title="Close (Esc)">
				<X size={20} />
			</button>
		</div>

		<div class="flex-1 flex items-center justify-center relative px-16">
			<button
				onclick={prev} disabled={index === 0}
				class="absolute left-4 p-3 rounded-full bg-zinc-800/80 text-zinc-300 hover:bg-zinc-700 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
				title="Previous (←)"
			><ChevronLeft size={24} /></button>

			<div class="flex flex-col items-center gap-4">
				<div class="relative">
					{#key face.id}
						<img
							src="/media/face/{face.id}?size=480"
							alt="Face close-up"
							class="w-[420px] h-[420px] max-w-[60vw] max-h-[55vh] object-cover rounded-2xl border border-zinc-700 shadow-2xl"
						/>
					{/key}
					{#if busy}
						<div class="absolute inset-0 flex items-center justify-center bg-black/50 rounded-2xl">
							<Loader2 size={28} class="animate-spin text-white" />
						</div>
					{/if}
				</div>
				<div class="text-center">
					<div class="text-lg font-semibold text-zinc-100">{face.person_name ?? 'Unknown'}</div>
					{#if face.score !== null}
						<div class="text-xs mt-1.5 inline-block px-2 py-0.5 rounded {scoreBadgeClass(face.score)}">
							{Math.round(face.score * 100)}% match
						</div>
					{/if}
				</div>
			</div>

			<button
				onclick={next} disabled={index === faces.length - 1}
				class="absolute right-4 p-3 rounded-full bg-zinc-800/80 text-zinc-300 hover:bg-zinc-700 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
				title="Next (→)"
			><ChevronRight size={24} /></button>
		</div>

		<div class="shrink-0 flex items-center justify-center gap-3 px-4 py-5">
			<button
				onclick={() => pickerOpen = true} disabled={busy}
				class="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium disabled:opacity-50 transition-colors"
				title="Wrong person — reassign (W)"
			><UserX size={16} /> Wrong person</button>
			<button
				onclick={handleUnknown} disabled={busy}
				class="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-200 font-medium disabled:opacity-50 transition-colors"
				title="Send back to the review queue (U)"
			><HelpCircle size={16} /> Mark unknown</button>
			<button
				onclick={handleDelete} disabled={busy}
				class="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-red-700 hover:bg-red-600 text-white font-medium disabled:opacity-50 transition-colors"
				title="Delete this face tag permanently (Del)"
			><Trash2 size={16} /> Delete</button>
		</div>
		<p class="text-center text-[11px] text-zinc-600 pb-3">← → navigate · W wrong person · U mark unknown · Del delete · Esc close</p>
	</div>

	<PersonPicker bind:open={pickerOpen} title="Reassign to…" onPick={handleReassign} />
{/if}

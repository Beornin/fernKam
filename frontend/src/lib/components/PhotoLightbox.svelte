<script lang="ts">
import { api, type PhotoDetail, type TagOut, type FaceOut } from '$lib/api';
import { X, ChevronLeft, ChevronRight, Star, MapPin, Camera, Aperture, User, Eye, EyeOff, Info, Trash2, ScanFace, Check, XCircle, Ban } from '@lucide/svelte';
import TagPicker from './TagPicker.svelte';

let {
photoId,
photo: initialPhoto,
onClose,
onPrev,
onNext,
}: {
photoId: number;
photo?: PhotoDetail;
onClose: () => void;
onPrev?: () => void;
onNext?: () => void;
} = $props();

let detail = $state<PhotoDetail | null>(null);
let loading = $state(true);
let liveTags = $state<TagOut[]>([]);
let liveFaces = $state<FaceOut[]>([]);
let selectedFace = $state<FaceOut | null>(null);
let allPersonTags = $state<TagOut[]>([]);
let imgEl = $state<HTMLImageElement | null>(null);
let imgRenderedW = $state(0);
let imgRenderedH = $state(0);
let imgNaturalW = $state(0);
let imgNaturalH = $state(0);
let showFaceBoxes = $state(true);
let showMetadata = $state(true);
let detecting = $state(false);
let detectResult = $state<string | null>(null);
let zoom = $state(1);
let panX = $state(0);
let panY = $state(0);
let isPanning = $state(false);
let panStartX = $state(0);
let panStartY = $state(0);

$effect(() => {
if (photoId) {
loading = true;
detail = null;
selectedFace = null;
resetZoom();

// Use initialPhoto if provided, otherwise fetch
if (initialPhoto) {
detail = initialPhoto;
liveTags = initialPhoto.tags;
liveFaces = initialPhoto.faces;
loading = false;
} else {
api.photos.get(photoId).then(d => {
detail = d;
liveTags = d.tags;
liveFaces = d.faces;
loading = false;
});
}

api.tags.list({ flat: true }).then(tags => {
allPersonTags = tags.filter(t => t.is_person);
});
}
});

function handleKey(e: KeyboardEvent) {
if (e.key === 'Escape') { if (selectedFace) { selectedFace = null; } else { onClose(); } }
if (e.key === 'ArrowLeft' && !selectedFace) onPrev?.();
if (e.key === 'ArrowRight' && !selectedFace) onNext?.();
if (e.key === '+' || e.key === '=') zoomIn();
if (e.key === '-' || e.key === '_') zoomOut();
if (e.key === '0') resetZoom();
}

function zoomIn() {
zoom = Math.min(zoom * 1.25, 10);
}

function zoomOut() {
zoom = Math.max(zoom / 1.25, 0.5);
}

function resetZoom() {
zoom = 1;
panX = 0;
panY = 0;
}

function handleWheel(e: WheelEvent) {
if (e.ctrlKey || e.metaKey) {
e.preventDefault();
if (e.deltaY < 0) zoomIn();
else zoomOut();
}
}

function handlePanStart(e: MouseEvent) {
if (zoom > 1) {
isPanning = true;
panStartX = e.clientX - panX;
panStartY = e.clientY - panY;
}
}

function handlePanMove(e: MouseEvent) {
if (isPanning) {
panX = e.clientX - panStartX;
panY = e.clientY - panStartY;
}
}

function handlePanEnd() {
isPanning = false;
}

function onImgLoad() {
if (imgEl) {
imgRenderedW = imgEl.clientWidth;
imgRenderedH = imgEl.clientHeight;
imgNaturalW = imgEl.naturalWidth;
imgNaturalH = imgEl.naturalHeight;
console.log(`[PhotoLightbox] Image loaded: rendered=${imgRenderedW}x${imgRenderedH} natural=${imgNaturalW}x${imgNaturalH} faces=${liveFaces.length} showFaceBoxes=${showFaceBoxes}`);
}
}

// Compute face rect in rendered-image coordinates (uses natural image dims, not DB dims)
function faceRect(f: FaceOut) {
if (!imgNaturalW || !imgNaturalH) { console.log('[face] no natural dims', imgNaturalW, imgNaturalH); return null; }
if (f.x == null || f.y == null || f.w == null || f.h == null) { console.log('[face] null coords', f); return null; }
const scaleX = imgRenderedW / imgNaturalW;
const scaleY = imgRenderedH / imgNaturalH;
const rect = { left: f.x * scaleX, top: f.y * scaleY, width: f.w * scaleX, height: f.h * scaleY };
console.log(`[face] rect for ${f.person_name}:`, rect);
return rect;
}

async function assignFaceName(face: FaceOut, tagId: number | null) {
const updated = await api.faces.update(face.id, { person_tag_id: tagId, status: tagId ? 'confirmed' : 'unconfirmed' });
// Add person_name from allPersonTags if not included in response
if (tagId && !updated.person_name) {
const person = allPersonTags.find(pt => pt.id === tagId);
if (person) {
updated.person_name = person.name;
}
}
liveFaces = liveFaces.map(f => f.id === updated.id ? updated : f);
selectedFace = updated;
}

async function deleteFace(face: FaceOut) {
await fetch(`/api/faces/${face.id}`, { method: 'DELETE' });
liveFaces = liveFaces.filter(f => f.id !== face.id);
if (selectedFace?.id === face.id) selectedFace = null;
}

async function detectFaces() {
if (!detail) return;
detecting = true;
detectResult = null;
try {
  const newFaces = await api.photos.detectFaces(detail.id);
  // Only add faces that don't already exist at the same location (within 5px tolerance)
  const facesToAdd = newFaces.filter(nf => {
    return !liveFaces.some(lf => 
      Math.abs((nf.x ?? 0) - (lf.x ?? 0)) < 5 && 
      Math.abs((nf.y ?? 0) - (lf.y ?? 0)) < 5
    );
  });
  liveFaces = [...liveFaces, ...facesToAdd];
  const sug = facesToAdd.filter(f => f.status === 'suggested').length;
  detectResult = facesToAdd.length === 0
    ? 'No new faces found'
    : `Found ${facesToAdd.length} face${facesToAdd.length !== 1 ? 's' : ''}${sug > 0 ? `, ${sug} identified` : ''}`;
} catch {
  detectResult = 'Detection failed';
} finally {
  detecting = false;
}
}

async function confirmFace(face: FaceOut) {
const updated = await api.faces.update(face.id, { status: 'confirmed' });
liveFaces = liveFaces.map(f => f.id === updated.id ? updated : f);
selectedFace = updated;
}

async function rejectSuggestion(face: FaceOut) {
const updated = await api.faces.update(face.id, { person_tag_id: null, status: 'unconfirmed' });
liveFaces = liveFaces.map(f => f.id === updated.id ? updated : f);
selectedFace = updated;
}

async function markNotFace(face: FaceOut) {
const updated = await api.faces.update(face.id, { status: 'ignored' });
liveFaces = liveFaces.map(f => f.id === updated.id ? updated : f);
if (selectedFace?.id === face.id) selectedFace = null;
}

function faceBoxClass(face: FaceOut, isSelected: boolean): string {
if (isSelected) return 'border-emerald-400 bg-emerald-400/10';
if (face.status === 'confirmed') return 'border-emerald-500/70 bg-transparent';
if (face.status === 'suggested') return 'border-amber-400/80 bg-amber-400/5';
if (face.status === 'ignored') return 'border-zinc-600/50 bg-transparent opacity-50';
return 'border-white/50 bg-transparent'; // unconfirmed
}

async function updateRating(n: number) {
if (!detail) return;
// If clicking the same rating, clear it
const newRating = detail.rating === n ? 0 : n;
await api.photos.patch(detail.id, { rating: newRating });
detail = { ...detail, rating: newRating };
}

function fmtDate(s: string | null) {
if (!s) return '—';
return new Date(s).toLocaleString();
}

function fmtSize(bytes: number | null) {
if (!bytes) return '—';
return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e3).toFixed(0)} KB`;
}
</script>

<svelte:window onkeydown={handleKey} />

<!-- Backdrop -->
<div
class="fixed inset-0 z-[9999] bg-black/90 flex"
role="dialog"
aria-modal="true"
>
<!-- Toolbar: close + toggles + detect -->
<div class="absolute top-3 right-3 z-10 flex items-center gap-1">
	<button
		onclick={detectFaces}
		disabled={detecting}
		class="p-1.5 rounded hover:bg-zinc-700 transition-colors {detecting ? 'text-zinc-500 animate-pulse' : 'text-zinc-400 hover:text-emerald-400'}"
		title="Detect & identify faces"
	>
		<ScanFace size={18} />
	</button>
	<button
		onclick={() => showFaceBoxes = !showFaceBoxes}
		class="p-1.5 rounded hover:bg-zinc-700 transition-colors {showFaceBoxes ? 'text-emerald-400' : 'text-zinc-500'}"
		title="{showFaceBoxes ? 'Hide' : 'Show'} face boxes"
	>
		{#if showFaceBoxes}<Eye size={18} />{:else}<EyeOff size={18} />{/if}
	</button>
	<button
		onclick={() => showMetadata = !showMetadata}
		class="p-1.5 rounded hover:bg-zinc-700 transition-colors {showMetadata ? 'text-emerald-400' : 'text-zinc-500'}"
		title="{showMetadata ? 'Hide' : 'Show'} metadata panel"
	>
		<Info size={18} />
	</button>
	<button
		class="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white"
		onclick={onClose}
	>
		<X size={18} />
	</button>
</div>

<!-- Detect result toast -->
{#if detectResult}
<div class="absolute top-3 left-3 z-10 bg-zinc-800/90 text-zinc-200 text-xs px-3 py-1.5 rounded shadow-lg border border-zinc-700">
	{detectResult}
</div>
{/if}

<!-- Prev -->
{#if onPrev}
<button
class="absolute left-2 top-1/2 -translate-y-1/2 z-10 p-2 rounded-full bg-black/40 hover:bg-black/70 text-white"
onclick={onPrev}
>
<ChevronLeft size={28} />
</button>
{/if}

<!-- Next -->
{#if onNext}
<button
class="absolute right-[340px] top-1/2 -translate-y-1/2 z-10 p-2 rounded-full bg-black/40 hover:bg-black/70 text-white"
onclick={onNext}
>
<ChevronRight size={28} />
</button>
{/if}

<!-- Image area with face overlays -->
<div
class="flex-1 flex items-center justify-center p-8 min-w-0 overflow-hidden relative"
onwheel={handleWheel}
onmousedown={handlePanStart}
onmousemove={handlePanMove}
onmouseup={handlePanEnd}
onmouseleave={handlePanEnd}
style="cursor: {zoom > 1 ? 'grab' : 'default'}; {isPanning ? 'cursor: grabbing;' : ''}; min-height: 400px;"
>
{#if loading}
<div class="w-10 h-10 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
{:else if detail}
{#if detail.media_type === 'video'}
<div class="flex items-center justify-center w-full h-full">
<video
src={api.media.video(detail.id)}
controls
playsinline
preload="auto"
style="max-width: 100%; max-height: calc(100vh - 4rem);"
class="rounded shadow-2xl"
></video>
</div>
{:else}
<div class="relative inline-flex" style="transform: scale({zoom}) translate({panX}px, {panY}px); transition: transform 0.1s ease-out;">
<img
bind:this={imgEl}
src={api.media.original(detail.id)}
alt={detail.filename}
class="max-h-[calc(100vh-4rem)] max-w-full object-contain rounded shadow-2xl block"
onload={onImgLoad}
draggable={false}
/>
<!-- Detection progress overlay -->
{#if detecting}
<div class="absolute inset-0 bg-black/50 flex flex-col items-center justify-center rounded">
<div class="w-16 h-16 border-4 border-zinc-700 border-t-emerald-400 rounded-full animate-spin mb-4"></div>
<p class="text-white text-sm font-medium">Detecting faces...</p>
</div>
{/if}
<!-- Face bounding boxes (relative to image) -->
{#if showFaceBoxes && imgNaturalW > 0 && imgNaturalH > 0}
{#each liveFaces as face (face.id)}
{@const rect = faceRect(face)}
{#if rect}
<button
class="absolute border-2 transition-colors {faceBoxClass(face, selectedFace?.id === face.id)} hover:border-emerald-400"
style="left:{rect.left}px;top:{rect.top}px;width:{rect.width}px;height:{rect.height}px;box-sizing:border-box;"
onclick={() => { selectedFace = selectedFace?.id === face.id ? null : face; }}
title={face.person_name ?? face.status + (face.score ? ` (${face.score.toFixed(2)})` : '')}
>
{#if face.person_name}
<span class="absolute bottom-0 left-0 right-0 text-center text-[10px] font-medium bg-black/70 text-white py-0.5 leading-tight truncate px-1">
{face.person_name}
</span>
{/if}
</button>
{/if}
{/each}
{/if}
</div>
{/if}
{/if}
</div>

<!-- Details panel -->
<aside class="{showMetadata ? 'w-[320px]' : 'w-0 overflow-hidden'} shrink-0 bg-zinc-900 border-l border-zinc-800 overflow-y-auto flex flex-col gap-0 transition-all duration-200">
{#if detail}
<!-- Rating -->
<div class="px-4 py-3 border-b border-zinc-800 flex items-center gap-2">
{#each [1,2,3,4,5] as n}
<button onclick={() => updateRating(n)}>
<Star
size={18}
class="{detail.rating >= n ? 'fill-yellow-400 text-yellow-400' : 'text-zinc-600'}"
/>
</button>
{/each}
</div>

<div class="p-4 border-b border-zinc-800">
<p class="text-sm font-medium text-zinc-100 truncate" title={detail.filename}>{detail.filename}</p>
<p class="text-xs text-zinc-500 mt-0.5 truncate" title={detail.album_path}>{detail.album_path}</p>
</div>

<!-- Metadata rows -->
<dl class="px-4 py-3 space-y-2 text-sm border-b border-zinc-800">
<div class="flex justify-between">
<dt class="text-zinc-500">Taken</dt>
<dd class="text-zinc-200 text-right">{fmtDate(detail.taken_at)}</dd>
</div>
<div class="flex justify-between">
<dt class="text-zinc-500">Created</dt>
<dd class="text-zinc-200 text-right">{fmtDate(detail.imported_at)}</dd>
</div>
<div class="flex justify-between">
<dt class="text-zinc-500">Modified</dt>
<dd class="text-zinc-200 text-right">{fmtDate(detail.modified_at)}</dd>
</div>
<div class="flex justify-between">
<dt class="text-zinc-500">Size</dt>
<dd class="text-zinc-200">{fmtSize(detail.file_size)}</dd>
</div>
{#if detail.width && detail.height}
<div class="flex justify-between">
<dt class="text-zinc-500">Dimensions</dt>
<dd class="text-zinc-200">{detail.width} × {detail.height}</dd>
</div>
{/if}
{#if detail.camera}
<div class="flex justify-between gap-2">
<dt class="text-zinc-500 flex items-center gap-1"><Camera size={12} /> Camera</dt>
<dd class="text-zinc-200 text-right">{[detail.camera.make, detail.camera.model].filter(Boolean).join(' ')}</dd>
</div>
{/if}
{#if detail.lens}
<div class="flex justify-between gap-2">
<dt class="text-zinc-500 flex items-center gap-1"><Aperture size={12} /> Lens</dt>
<dd class="text-zinc-200 text-right">{[detail.lens.make, detail.lens.model].filter(Boolean).join(' ')}</dd>
</div>
{/if}
{#if detail.latitude !== null && detail.longitude !== null}
<div class="flex justify-between">
<dt class="text-zinc-500 flex items-center gap-1"><MapPin size={12} /> GPS</dt>
<dd class="text-zinc-200">{detail.latitude?.toFixed(5)}, {detail.longitude?.toFixed(5)}</dd>
</div>
{/if}
</dl>

<!-- Tags (editable) -->
<div class="px-4 py-3 border-b border-zinc-800">
<p class="text-xs text-zinc-500 mb-2">Tags</p>
<TagPicker photoId={detail.id} bind:currentTags={liveTags} />
</div>

<!-- Faces panel -->
{#if liveFaces.length > 0}
<div class="px-4 py-3 border-b border-zinc-800">
<p class="text-xs text-zinc-500 mb-2">Faces ({liveFaces.length})</p>

<!-- Selected face: name assignment -->
{#if selectedFace}
<div class="bg-zinc-800 rounded-lg p-3 mb-2 space-y-2">
<p class="text-xs font-medium text-zinc-200 flex items-center gap-1.5">
<User size={12} />
{selectedFace.person_name ?? 'Unidentified'}
</p>
<select
class="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-xs text-zinc-300"
value={selectedFace.person_tag_id ?? ''}
onchange={async (e) => {
const sf = selectedFace;
if (!sf) return;
const val = e.currentTarget.value;
await assignFaceName(sf, val ? Number(val) : null);
}}
>
<option value="">— Unidentified —</option>
{#each allPersonTags as pt (pt.id)}
<option value={pt.id}>{pt.name}</option>
{/each}
</select>

<!-- Review actions based on status -->
{#if selectedFace.status === 'suggested'}
<div class="flex gap-1">
	<button
		class="flex-1 text-xs bg-emerald-600 hover:bg-emerald-500 text-white py-1 rounded flex items-center justify-center gap-1"
		onclick={() => selectedFace && confirmFace(selectedFace)}
		title="Accept suggestion"
	><Check size={11} /> Accept</button>
	<button
		class="flex-1 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 py-1 rounded flex items-center justify-center gap-1"
		onclick={() => selectedFace && rejectSuggestion(selectedFace)}
		title="Wrong person"
	><XCircle size={11} /> Wrong</button>
	<button
		class="flex-1 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 py-1 rounded flex items-center justify-center gap-1"
		onclick={() => selectedFace && markNotFace(selectedFace)}
		title="Not a face"
	><Ban size={11} /> Not face</button>
</div>
{:else}
<div class="flex gap-2">
	<button class="text-xs text-zinc-500 hover:text-zinc-300" onclick={() => { selectedFace = null; }}>Done</button>
	<button
		class="text-xs text-red-500 hover:text-red-400 flex items-center gap-1"
		onclick={() => selectedFace && deleteFace(selectedFace)}
		title="Delete this face region"
	><Trash2 size={11} /> Delete face</button>
</div>
{/if}
</div>
{/if}

<!-- Face list -->
<div class="flex flex-wrap gap-1.5">
{#each liveFaces as face (face.id)}
<button
class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
{selectedFace?.id === face.id
? 'bg-emerald-600 text-white'
: 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300'}"
onclick={() => { selectedFace = selectedFace?.id === face.id ? null : face; }}
>
<User size={10} />
{face.person_name ?? '?'}
</button>
{/each}
</div>
</div>
{/if}

<!-- Caption -->
{#if detail.caption}
<div class="px-4 py-3">
<p class="text-xs text-zinc-500 mb-1">Caption</p>
<p class="text-sm text-zinc-200">{detail.caption}</p>
</div>
{/if}
{/if}
</aside>
</div>

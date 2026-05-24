<script lang="ts">
import { api, type PhotoDetail, type TagOut, type FaceOut } from '$lib/api';
import { X, ChevronLeft, ChevronRight, Star, MapPin, Camera, User } from '@lucide/svelte';
import TagPicker from './TagPicker.svelte';

let {
photoId,
onClose,
onPrev,
onNext,
}: {
photoId: number;
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
let imgEl: HTMLImageElement | null = null;
let imgRenderedW = $state(0);
let imgRenderedH = $state(0);

$effect(() => {
if (photoId) {
loading = true;
detail = null;
selectedFace = null;
api.photos.get(photoId).then(d => {
detail = d;
liveTags = d.tags;
liveFaces = d.faces;
loading = false;
});
api.tags.list({ flat: true }).then(tags => {
allPersonTags = tags.filter(t => t.is_person);
});
}
});

function handleKey(e: KeyboardEvent) {
if (e.key === 'Escape') { if (selectedFace) { selectedFace = null; } else { onClose(); } }
if (e.key === 'ArrowLeft' && !selectedFace) onPrev?.();
if (e.key === 'ArrowRight' && !selectedFace) onNext?.();
}

function onImgLoad() {
if (imgEl) {
imgRenderedW = imgEl.clientWidth;
imgRenderedH = imgEl.clientHeight;
}
}

// Compute face rect in rendered-image coordinates
function faceRect(f: FaceOut) {
if (!detail?.width || !detail?.height || !f.x || !f.y || !f.w || !f.h) return null;
const scaleX = imgRenderedW / detail.width;
const scaleY = imgRenderedH / detail.height;
return {
left: f.x * scaleX,
top: f.y * scaleY,
width: f.w * scaleX,
height: f.h * scaleY,
};
}

async function assignFaceName(face: FaceOut, tagId: number | null) {
const updated = await api.faces.update(face.id, { person_tag_id: tagId });
liveFaces = liveFaces.map(f => f.id === updated.id ? updated : f);
selectedFace = updated;
}

async function updateRating(n: number) {
if (!detail) return;
await api.photos.patch(detail.id, { rating: n });
detail = { ...detail, rating: n };
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
<!-- Close -->
<button
class="absolute top-4 right-4 text-zinc-400 hover:text-white z-10 p-1 rounded hover:bg-zinc-800"
onclick={onClose}
>
<X size={22} />
</button>

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
<div class="flex-1 flex items-center justify-center p-8 min-w-0">
{#if loading}
<div class="w-10 h-10 border-2 border-zinc-700 border-t-emerald-400 rounded-full animate-spin"></div>
{:else if detail}
{#if detail.media_type === 'video'}
<video
src={api.media.original(detail.id)}
controls
class="max-h-full max-w-full rounded shadow-2xl"
></video>
{:else}
<div class="relative inline-flex">
<img
bind:this={imgEl}
src={api.media.original(detail.id)}
alt={detail.filename}
class="max-h-[calc(100vh-4rem)] max-w-full object-contain rounded shadow-2xl block"
onload={onImgLoad}
/>
<!-- Face bounding boxes -->
{#each liveFaces as face (face.id)}
{@const rect = faceRect(face)}
{#if rect}
<button
class="absolute border-2 transition-colors
{selectedFace?.id === face.id
? 'border-emerald-400 bg-emerald-400/10'
: 'border-white/60 hover:border-emerald-400 bg-transparent'}"
style="left:{rect.left}px;top:{rect.top}px;width:{rect.width}px;height:{rect.height}px"
onclick={() => { selectedFace = selectedFace?.id === face.id ? null : face; }}
title={face.person_name ?? 'Unknown'}
>
{#if face.person_name}
<span class="absolute bottom-0 left-0 right-0 text-center text-[10px] font-medium bg-black/70 text-white py-0.5 leading-tight truncate px-1">
{face.person_name}
</span>
{/if}
</button>
{/if}
{/each}
</div>
{/if}
{/if}
</div>

<!-- Details panel -->
<aside class="w-[320px] shrink-0 bg-zinc-900 border-l border-zinc-800 overflow-y-auto flex flex-col gap-0">
{#if detail}
<div class="p-4 border-b border-zinc-800">
<p class="text-sm font-medium text-zinc-100 break-all">{detail.filename}</p>
<p class="text-xs text-zinc-500 mt-0.5">{detail.album_path}</p>
</div>

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

<!-- Metadata rows -->
<dl class="px-4 py-3 space-y-2 text-sm border-b border-zinc-800">
<div class="flex justify-between">
<dt class="text-zinc-500">Taken</dt>
<dd class="text-zinc-200 text-right">{fmtDate(detail.taken_at)}</dd>
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
const val = (e.target as HTMLSelectElement).value;
await assignFaceName(selectedFace!, val ? Number(val) : null);
}}
>
<option value="">— Unidentified —</option>
{#each allPersonTags as pt (pt.id)}
<option value={pt.id}>{pt.name}</option>
{/each}
</select>
<button
class="text-xs text-zinc-500 hover:text-zinc-300"
onclick={() => { selectedFace = null; }}
>
Done
</button>
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

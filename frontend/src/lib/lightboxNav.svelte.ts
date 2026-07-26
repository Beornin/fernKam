// Centralizes the open/prev/next/close wiring around <PhotoLightbox> that used
// to be copy-pasted (selectedId/selectedIdx/standalonePhoto state + the same
// three handler functions) in every route that opens a photo detail view.
import { api, type PhotoDetail, type PhotoSummary } from '$lib/api';

export function createLightboxNav() {
	let selectedId = $state<number | null>(null);
	let selectedIdx = $state<number>(-1);
	let standalonePhoto = $state<PhotoDetail | undefined>(undefined);

	function open(photos: PhotoSummary[], photo: PhotoSummary) {
		selectedId = photo.id;
		selectedIdx = photos.findIndex((p) => p.id === photo.id);
		standalonePhoto = undefined;
	}

	/** Open by id even if it's not in `photos` (e.g. deep-linked via ?photo_id=). */
	async function openById(photos: PhotoSummary[], id: number) {
		const idx = photos.findIndex((p) => p.id === id);
		if (idx >= 0) {
			selectedId = id;
			selectedIdx = idx;
			standalonePhoto = undefined;
		} else {
			const photo = await api.photos.get(id);
			standalonePhoto = photo;
			selectedId = id;
			selectedIdx = -1;
		}
	}

	function close() {
		selectedId = null;
		selectedIdx = -1;
		standalonePhoto = undefined;
	}

	function prev(photos: PhotoSummary[]) {
		if (selectedIdx > 0) {
			selectedIdx--;
			selectedId = photos[selectedIdx].id;
			standalonePhoto = undefined;
		}
	}

	function next(photos: PhotoSummary[]) {
		if (selectedIdx >= 0 && selectedIdx < photos.length - 1) {
			selectedIdx++;
			selectedId = photos[selectedIdx].id;
			standalonePhoto = undefined;
		}
	}

	return {
		get selectedId() {
			return selectedId;
		},
		get selectedIdx() {
			return selectedIdx;
		},
		get standalonePhoto() {
			return standalonePhoto;
		},
		open,
		openById,
		close,
		prev,
		next,
	};
}

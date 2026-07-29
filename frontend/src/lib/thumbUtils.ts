/** Maps a pixel grid-tile size (from thumbSizeStore) to the closest thumbnail
 * bucket the backend actually generates ('sm'/'md'/'lg'/'xl'). */
export function getThumbSize(size: number): 'sm' | 'md' | 'lg' | 'xl' {
	if (size >= 300) return 'xl';
	if (size >= 200) return 'lg';
	if (size >= 150) return 'md';
	return 'sm';
}

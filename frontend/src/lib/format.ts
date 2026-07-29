/** Human-readable byte size (binary/1024-based units, one decimal place). */
export function formatBytes(bytes: number | null | undefined): string {
	if (bytes === null || bytes === undefined) return '—';
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let v = bytes, i = 0;
	while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
	return i === 0 ? `${v} B` : `${v.toFixed(1)} ${units[i]}`;
}

/** Human-readable duration from seconds, e.g. "1:23:45" or "3:07". */
export function formatDuration(secs: number | null | undefined): string | null {
	if (!secs) return null;
	const h = Math.floor(secs / 3600);
	const m = Math.floor((secs % 3600) / 60);
	const s = Math.floor(secs % 60);
	if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
	return `${m}:${String(s).padStart(2, '0')}`;
}

/** EXIF shutter speed (seconds) as e.g. "1/250" or "2s". */
export function formatShutter(v: unknown): string {
	const n = Number(v);
	if (isNaN(n) || n <= 0) return '—';
	if (n >= 1) return n % 1 === 0 ? `${n}s` : `${n.toFixed(1)}s`;
	return `1/${Math.round(1 / n)}`;
}

/** EXIF aperture as e.g. "f/2.8". */
export function formatAperture(v: unknown): string {
	const n = Number(v);
	if (isNaN(n)) return '—';
	return `f/${n % 1 === 0 ? n : n.toFixed(1)}`;
}

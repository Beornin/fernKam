import { writable } from 'svelte/store';

export const thumbSizeStore = writable<number>(180);

export const statusCountStore = writable<string>('');

/** Bumped per photo when it is rotated or flipped, and added to its thumbnail
 * URL, so the grid fetches the redrawn thumbnail instead of reusing the old one. */
export const thumbVersion = writable<Record<number, number>>({});

/** Bumped per photo when an action (a right-click menu item, a Cull key) changed its tags, rating
 * or label, so an open side panel or viewer showing it reads it again. */
export const detailVersion = writable<Record<number, number>>({});

export function photosChanged(ids: number[]): void {
	detailVersion.update(v => { for (const id of ids) v[id] = (v[id] ?? 0) + 1; return v; });
}

/** The server's library version (see sync/library.py), updated by the status
 * bar's task poll. It changes when a scan adds, removes, moves or refreshes
 * photos, and views that list photos reload then. null until the first poll. */
export const libraryVersionStore = writable<number | null>(null);

const THEME_KEY = 'fernkam-theme';

export const THEMES = [
	{ id: 'amber', label: 'Amber' },
	{ id: 'ocean', label: 'Ocean' },
	{ id: 'forest', label: 'Forest' },
	{ id: 'grape', label: 'Grape' },
	{ id: 'sunset', label: 'Sunset' },
	{ id: 'lagoon', label: 'Lagoon' },
	{ id: 'crimson', label: 'Crimson' },
	{ id: 'twilight', label: 'Twilight' },
	{ id: 'citrus', label: 'Citrus' },
	{ id: 'copper', label: 'Copper' },
] as const;

export type Theme = typeof THEMES[number]['id'];

function loadTheme(): Theme {
	if (typeof localStorage === 'undefined') return 'amber';
	const v = localStorage.getItem(THEME_KEY);
	return THEMES.some(t => t.id === v) ? (v as Theme) : 'amber';
}

export const themeStore = writable<Theme>(loadTheme());

themeStore.subscribe(v => {
	if (typeof document === 'undefined') return;
	document.documentElement.setAttribute('data-theme', v);
	localStorage.setItem(THEME_KEY, v);
});

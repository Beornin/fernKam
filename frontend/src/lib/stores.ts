import { writable } from 'svelte/store';

export const thumbSizeStore = writable<number>(180);

export const statusCountStore = writable<string>('');

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

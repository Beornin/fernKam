import { writable } from 'svelte/store';

export const thumbSizeStore = writable<number>(180);

export const statusCountStore = writable<string>('');

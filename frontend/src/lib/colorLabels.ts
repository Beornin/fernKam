/** digiKam-style color labels (1-7). Single source of truth — PhotoGrid's dot
 * previously used a different set of Tailwind colors (sky/indigo/fuchsia)
 * than the SearchTab/LabelsTab swatches (blue/purple/gray) for the same
 * values, so the same color_label showed as a different color depending on
 * where you looked. */
export const COLOR_LABELS = [
	{ value: 1, name: 'Red', cls: 'bg-red-500' },
	{ value: 2, name: 'Orange', cls: 'bg-orange-500' },
	{ value: 3, name: 'Yellow', cls: 'bg-yellow-400' },
	{ value: 4, name: 'Green', cls: 'bg-green-500' },
	{ value: 5, name: 'Blue', cls: 'bg-blue-500' },
	{ value: 6, name: 'Purple', cls: 'bg-purple-500' },
	{ value: 7, name: 'Gray', cls: 'bg-zinc-400' },
] as const;

export const COLOR_LABEL_CLASS: Record<number, string> = Object.fromEntries(
	COLOR_LABELS.map((c) => [c.value, c.cls])
);

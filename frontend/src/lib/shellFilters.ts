// Shared query-param plumbing for the /photos shell's left sidebar tabs.
// Every tab (Albums/Tags/Timeline/Search/People/Labels) selects a node and
// wants the URL to reflect *only* that tab's filter, clearing whatever the
// previously active tab had set, without disturbing view prefs like sort.

export const FILTER_PARAM_KEYS = [
	'album_path',
	'tag_id',
	'person_tag_id',
	'rating_min',
	'color_label',
	'date_from',
	'date_to',
	'media_type',
	'camera_id',
	'lens_id',
	'has_gps',
	'has_faces',
	'no_date',
	'search',
] as const;

export type FilterKey = (typeof FILTER_PARAM_KEYS)[number];
export type FilterParams = Partial<Record<FilterKey, string | number | boolean | undefined>>;

export type ShellTab = 'albums' | 'tags' | 'timeline' | 'search' | 'people' | 'labels';

/** Infer the active tab from whichever filter param is present, when the URL has no explicit `tab`. */
export function inferTab(sp: URLSearchParams): ShellTab {
	const explicit = sp.get('tab');
	if (explicit) return explicit as ShellTab;
	if (sp.get('tag_id')) return 'tags';
	if (sp.get('person_tag_id')) return 'people';
	if (sp.get('date_from') || sp.get('date_to')) return 'timeline';
	if (sp.get('rating_min') || sp.get('color_label')) return 'labels';
	if (sp.get('search') || sp.get('media_type') || sp.get('camera_id') || sp.get('lens_id') || sp.get('has_gps') || sp.get('has_faces') || sp.get('no_date')) return 'search';
	return 'albums';
}

/** Tabs whose filter the map endpoint (`/api/photos/map/points`) can actually apply — selecting
 * one of these while already in map view filters the map in place instead of leaving it. */
const MAP_COMPATIBLE_TABS: ReadonlySet<ShellTab> = new Set(['albums', 'tags']);

/** New URL: clear all filter params + page, set `tab`, apply `patch`. Leaves sort/other params untouched. */
export function buildFilterUrl(current: URL, tab: ShellTab, patch: FilterParams): string {
	const u = new URL(current);
	const staysInMap = u.searchParams.get('view') === 'map' && MAP_COMPATIBLE_TABS.has(tab);
	for (const key of FILTER_PARAM_KEYS) u.searchParams.delete(key);
	u.searchParams.delete('page');
	if (!staysInMap) u.searchParams.delete('view'); // leave map view — this tab's filter isn't supported there
	u.searchParams.set('tab', tab);
	for (const [key, value] of Object.entries(patch)) {
		if (value !== undefined && value !== null && value !== '') {
			u.searchParams.set(key, String(value));
		}
	}
	return u.toString();
}

const NUM_PARAMS = ['tag_id', 'person_tag_id', 'rating_min', 'color_label', 'camera_id', 'lens_id'] as const;
const STR_PARAMS = ['album_path', 'date_from', 'date_to', 'media_type', 'search'] as const;
const BOOL_PARAMS = ['has_gps', 'has_faces', 'no_date'] as const;

/** Read whichever filter params are present in the URL into an api.photos.list()-shaped object. */
export function readListFilterParams(sp: URLSearchParams): Record<string, unknown> {
	const params: Record<string, unknown> = {};
	for (const k of STR_PARAMS) {
		const v = sp.get(k);
		if (v) params[k] = v;
	}
	for (const k of NUM_PARAMS) {
		const v = sp.get(k);
		if (v) params[k] = Number(v);
	}
	for (const k of BOOL_PARAMS) {
		const v = sp.get(k);
		if (v) params[k] = v === '1' || v === 'true';
	}
	return params;
}

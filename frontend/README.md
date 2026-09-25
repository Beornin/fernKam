# fernKam frontend

SvelteKit + Svelte 5 (runes) + Tailwind CSS 4, built as a static single-page app with
`adapter-static`. There is no Node server in production: the backend serves `build/` on port
8000 and falls back to `index.html` for client-side routes.

## Commands

```sh
npm ci            # install exact versions from package-lock.json (Node 22 LTS)
npm run dev       # dev server on :5173 with hot reload; proxies /api and /media to :8000
npm run build     # production bundle → build/ (what the backend and fernKam.exe serve)
npm run check     # svelte-check type checking
```

Start the backend first (`cd ../backend && uv run fernkam serve`). The dev server proxies to it,
so the app is same-origin in both setups and needs no CORS configuration.

## Layout

```
src/
├── routes/                 one folder per page
│   ├── +layout.svelte      app shell: icon rail, tools menu, status bar, task poller
│   ├── +page.svelte        Home: import / quick scan / backup shortcuts
│   ├── photos/             main grid. Sidebar tabs (albums, tags, search, timeline,
│   │                       people, labels), map view, lightbox, Review Mode (culling)
│   ├── review/             Face Review: per-person candidates, unknown-face clusters
│   ├── discover/           semantic search, similar photos, tag suggestions (CLIP)
│   ├── smart-albums/       saved searches (keyset-paginated)
│   ├── duplicates/  stacks/  date-inference/  workflows/
│   ├── maintenance/        rescan, DB stats/VACUUM/REINDEX, XMP write-back/refresh, backfills
│   ├── tasks/  logs/  settings/
│   ├── albums/ people/     older standalone pages, no longer linked from the nav
│   └── tags/ map/ timeline/ search/ sync/   legacy URLs that redirect into photos/ or maintenance/
└── lib/
    ├── api.ts              typed client for every backend endpoint (relative URLs)
    ├── components/         PhotoGrid, PhotoLightbox, MapView (Leaflet), pickers, sidebar tabs…
    ├── dialog.svelte.ts    notify()/ask(): in-app replacements for alert()/confirm()
    ├── stores.ts, shellFilters.ts, lightboxNav.svelte.ts, …
    └── colorLabels.ts, format.ts, thumbUtils.ts, throttle.ts
```

## Conventions

- Use `api.ts` for every call, with relative URLs (`/api/...`, `/media/...`), so the same code
  works through the dev proxy and when served by the backend.
- Destructive calls go through `okJson`/`okVoid`. They throw on non-2xx, because `fetch` only
  rejects on network failure; that's how a failed delete once looked like a success.
- Use `notify()`/`ask()` from `dialog.svelte.ts`, never `alert()`/`confirm()`.
- Keyboard shortcuts must not fire inside inputs, the lightbox, or other modes that own the
  keyboard (see the guards in `photos/+page.svelte`).

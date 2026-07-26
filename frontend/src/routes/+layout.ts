// Pure client-side SPA — no server runtime once packaged, all data comes from
// the FastAPI backend at runtime, not from SvelteKit load functions.
export const ssr = false;
export const prerender = false;

import type { RequestHandler } from '@sveltejs/kit';

const BACKEND = 'http://localhost:8000';

async function proxy(request: Request, path: string): Promise<Response> {
	const url = new URL(request.url);
	const backendUrl = `${BACKEND}/api/sync/${path}${url.search}`;
	const init: RequestInit = { method: request.method };
	if (request.method !== 'GET' && request.method !== 'HEAD') {
		const body = await request.text();
		if (body) {
			init.body = body;
			init.headers = { 'Content-Type': 'application/json' };
		}
	}
	const res = await fetch(backendUrl, init);
	if (res.status === 204 || res.status === 205) {
		return new Response(null, { status: res.status });
	}
	const data = await res.text();
	return new Response(data, {
		status: res.status,
		headers: { 'Content-Type': res.headers.get('Content-Type') ?? 'application/json' },
	});
}

export const GET: RequestHandler = ({ request, params }) => proxy(request, params.path ?? '');
export const POST: RequestHandler = ({ request, params }) => proxy(request, params.path ?? '');
export const PATCH: RequestHandler = ({ request, params }) => proxy(request, params.path ?? '');
export const DELETE: RequestHandler = ({ request, params }) => proxy(request, params.path ?? '');

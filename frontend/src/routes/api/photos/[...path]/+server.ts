import type { RequestHandler } from '@sveltejs/kit';

const BACKEND = 'http://localhost:8000';

async function proxy(request: Request, path: string): Promise<Response> {
	const url = new URL(request.url);
	const backendUrl = `${BACKEND}/api/photos/${path}${url.search}`;
	const init: RequestInit = {
		method: request.method,
		headers: { 'Content-Type': 'application/json' },
	};
	if (request.method !== 'GET' && request.method !== 'HEAD') {
		const body = await request.text();
		if (body) init.body = body;
	}
	const res = await fetch(backendUrl, init);
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

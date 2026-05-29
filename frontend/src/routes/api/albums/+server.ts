import { json } from '@sveltejs/kit';

export async function GET({ url }) {
	if (url.searchParams.has('debug')) {
		console.log('[ALBUMS PROXY] Fetching debug endpoint...');
		try {
			const response = await fetch('http://localhost:8000/api/albums/debug');
			console.log('[ALBUMS PROXY] Debug response status:', response.status);
			const data = await response.json();
			console.log('[ALBUMS PROXY] Debug data:', data);
			return json(data);
		} catch (e) {
			console.log('[ALBUMS PROXY] Debug error:', e);
			return json({ error: String(e) }, { status: 500 });
		}
	}
	console.log('[ALBUMS PROXY] Fetching from backend...');
	try {
		const response = await fetch('http://localhost:8000/api/albums');
		console.log('[ALBUMS PROXY] Response status:', response.status);
		const data = await response.json();
		console.log('[ALBUMS PROXY] Data:', data);
		return json(data);
	} catch (e) {
		console.log('[ALBUMS PROXY] Error:', e);
		return json({ error: String(e) }, { status: 500 });
	}
}

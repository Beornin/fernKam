import { json } from '@sveltejs/kit';

export async function POST({ request }) {
	const body = await request.json();
	console.log('[PROXY] Received body:', body);
	
	try {
		console.log('[PROXY] Calling backend...');
		const controller = new AbortController();
		const timeoutId = setTimeout(() => {
			console.error('[PROXY] Request timeout after 120s');
			controller.abort();
		}, 120000); // 2 minute timeout
		
		const response = await fetch('http://localhost:8000/api/sync/scan-library', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
			signal: controller.signal
		});
		
		clearTimeout(timeoutId);
		console.log('[PROXY] Response status:', response.status);
		const text = await response.text();
		console.log('[PROXY] Response text:', text);
		const data = JSON.parse(text);
		console.log('[PROXY] Response data:', data);
		return json(data);
	} catch (e) {
		console.error('[PROXY] Error:', e instanceof Error ? e.message : String(e));
		return json({ error: String(e) }, { status: 502 });
	}
}

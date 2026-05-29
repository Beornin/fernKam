import { json } from '@sveltejs/kit';

export async function GET() {
	try {
		const response = await fetch('http://localhost:8000/api/sync/tasks');
		const data = await response.json();
		return json(data);
	} catch (e) {
		return json({ error: String(e) }, { status: 500 });
	}
}

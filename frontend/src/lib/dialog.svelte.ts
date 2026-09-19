/** In-app replacements for window.alert / window.confirm.
 *
 * digiKam ships 833 modal dialogs, so modals are not the problem — browser
 * chrome is. Every destructive action here used to pop a box titled
 * "localhost:5173 says:", which is the single loudest tell that this is a web
 * page rather than an application.
 *
 * Deliberately the same shape as the natives so call sites swap one-for-one:
 *   alert(msg)              -> notify(msg)
 *   if (!confirm(msg))      -> if (!(await ask(msg)))
 *
 * `ask` is async because a real dialog cannot block the event loop the way
 * window.confirm does. Every existing call site is already inside an async
 * function.
 */

export interface DialogRequest {
	id: number;
	kind: 'notify' | 'ask';
	message: string;
	tone: 'default' | 'danger';
	resolve: (ok: boolean) => void;
}

let nextId = 1;

/** The queue the mounted <AppDialog /> renders. One at a time, in order. */
export const dialogQueue = $state<DialogRequest[]>([]);

function push(kind: DialogRequest['kind'], message: string, tone: DialogRequest['tone']) {
	return new Promise<boolean>((resolve) => {
		// Collapse duplicate notices. When the backend is down every page's
		// loader fails at once, and eleven identical dialogs stacked behind one
		// another is worse than no error at all. Confirmations are never
		// collapsed — each one is a distinct decision.
		if (kind === 'notify') {
			const dup = dialogQueue.find((d) => d.kind === 'notify' && d.message === message);
			if (dup) { resolve(true); return; }
		}
		dialogQueue.push({ id: nextId++, kind, message, tone, resolve });
	});
}

/** Replaces alert(). Fire-and-forget; await it if you want to continue after dismissal. */
export function notify(message: string, tone: DialogRequest['tone'] = 'default') {
	return push('notify', message, tone);
}

/** Replaces confirm(). Resolves true when the person confirms. */
export function ask(message: string, tone: DialogRequest['tone'] = 'danger') {
	return push('ask', message, tone);
}

export function settle(req: DialogRequest, ok: boolean) {
	const i = dialogQueue.findIndex((d) => d.id === req.id);
	if (i !== -1) dialogQueue.splice(i, 1);
	req.resolve(ok);
}

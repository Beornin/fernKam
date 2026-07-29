/** Leading+trailing throttle: runs immediately, then at most once per `waitMs`
 * while calls keep coming, with a final trailing call so the last invocation
 * (e.g. the scroll position that crosses a load-more threshold) is never lost. */
export function throttle<T extends (...args: any[]) => void>(fn: T, waitMs: number): T {
	let lastCall = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let pendingArgs: Parameters<T> | null = null;

	return function throttled(...args: Parameters<T>) {
		const now = Date.now();
		const remaining = waitMs - (now - lastCall);
		if (remaining <= 0) {
			lastCall = now;
			fn(...args);
		} else {
			pendingArgs = args;
			if (!timer) {
				timer = setTimeout(() => {
					lastCall = Date.now();
					timer = undefined;
					const a = pendingArgs;
					pendingArgs = null;
					if (a) fn(...a);
				}, remaining);
			}
		}
	} as T;
}

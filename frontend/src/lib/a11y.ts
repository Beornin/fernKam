/** Keyboard activation for a clickable row (role="button" tabindex="0"): Enter or Space does what a
 *  click does. Ignored when the key was pressed on something inside the row (its own rename or
 *  delete button), so that button's key press isn't also taken as a click on the row. */
export function activate(e: KeyboardEvent, action: () => void) {
	if (e.target !== e.currentTarget) return;
	if (e.key === 'Enter' || e.key === ' ') {
		e.preventDefault();
		action();
	}
}

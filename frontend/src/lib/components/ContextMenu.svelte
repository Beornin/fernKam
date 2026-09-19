<script lang="ts">
	/** Right-click menu. digiKam's primary interaction idiom (35 files use one);
	 * fernKam had none, which is a large part of why it read as a web page.
	 *
	 * Positioning is clamped to the viewport so a click near the right or
	 * bottom edge does not open a menu that runs off screen. */
	import type { Snippet } from 'svelte';

	let { x, y, onClose, children }: {
		x: number;
		y: number;
		onClose: () => void;
		children: Snippet;
	} = $props();

	let el = $state<HTMLDivElement | undefined>(undefined);
	// Starts off-screen so the first paint never flashes at the wrong place;
	// the effect below places it once the rendered size is known.
	let pos = $state({ left: -9999, top: -9999 });

	$effect(() => {
		if (!el) return;
		const r = el.getBoundingClientRect();
		pos = {
			left: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)),
			top: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)),
		};
	});
</script>

<svelte:window
	onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
	onresize={onClose}
/>

<!-- Full-screen catcher: any click or scroll outside dismisses, and a
     right-click elsewhere closes this one before opening the next. -->
<div
	class="fixed inset-0 z-[90]"
	role="presentation"
	onclick={onClose}
	oncontextmenu={(e) => { e.preventDefault(); onClose(); }}
	onwheel={onClose}
>
	<div
		bind:this={el}
		role="menu"
		tabindex="-1"
		style="left:{pos.left}px; top:{pos.top}px"
		class="fixed min-w-[190px] py-1 bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl"
		onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => e.stopPropagation()}
	>
		{@render children()}
	</div>
</div>

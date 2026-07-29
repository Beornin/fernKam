// One shared color scheme for face-match confidence, used everywhere a score
// is shown (lightbox face boxes, candidate grid, cluster triage) so "high
// confidence" always looks the same regardless of which view you're in.
export function scoreBadgeClass(score: number): string {
	if (score >= 0.75) return 'bg-emerald-500/90 text-emerald-950';
	if (score >= 0.5) return 'bg-amber-400/90 text-amber-950';
	return 'bg-zinc-600/90 text-zinc-100';
}


export function researchScrollKey(section: string, selectedTicker: string | null, analysisTicker: string | undefined): string {
  const readyTicker = selectedTicker && selectedTicker === analysisTicker ? selectedTicker : null;
  return JSON.stringify([section, readyTicker]);
}

export function scrollToResearchHeading(
  heading: Pick<HTMLElement, "scrollIntoView" | "focus"> | null,
  prefersReducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
) {
  if (!heading) return;
  // Keep navigation stationary during the next click, including rapid taps.
  // Explicit instant also avoids inheriting the page's CSS smooth behavior.
  void prefersReducedMotion;
  heading.scrollIntoView({ behavior: "instant", block: "start" });
  heading.focus({ preventScroll: true });
}

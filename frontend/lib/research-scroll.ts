export function researchScrollKey(section: string, selectedTicker: string | null, analysisTicker: string | undefined): string {
  const readyTicker = selectedTicker && selectedTicker === analysisTicker ? selectedTicker : null;
  return JSON.stringify([section, readyTicker]);
}

export function scrollToResearchHeading(
  heading: Pick<HTMLElement, "scrollIntoView" | "focus"> | null,
  prefersReducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
) {
  if (!heading) return;
  heading.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "start" });
  heading.focus({ preventScroll: true });
}

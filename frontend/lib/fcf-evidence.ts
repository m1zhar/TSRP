export function freeCashFlowMethodNote(kind: string | null | undefined): string {
  if (kind === "unavailable") {
    return "Free cash flow is unavailable from the source.";
  }
  if (kind?.startsWith("calculated:")) {
    return "Calculated from operating cash flow less capital spending for the same period.";
  }
  if (kind?.startsWith("provider-reported free cash flow")) {
    return "Provider-reported free cash flow; operating cash flow or capital-spending detail was unavailable.";
  }
  return "The available data does not identify how free cash flow was determined.";
}

export function freeCashFlowSourceLabel(source: string | null | undefined, kind: string | null | undefined): string {
  if (!source) return "Unavailable";
  return kind ? `${source} · ${freeCashFlowMethodNote(kind)}` : source;
}

export function freeCashFlowEvidenceNote(
  margin: number | null,
  method: string | null | undefined,
  formattedMargin: string,
): string {
  const methodNote = freeCashFlowMethodNote(method);
  if (margin === null) {
    return `Cash-flow margin is unavailable for this analysis. ${methodNote}`;
  }
  return `Cash-flow margin ${formattedMargin}. ${methodNote}`;
}

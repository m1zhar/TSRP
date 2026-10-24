export type ExportFormatLabel = "CSV" | "JSON";
export type ExportFeedbackState = { format: ExportFormatLabel; status: "started" | "failed" };

export function attemptResearchExport(format: "csv" | "json", action: () => void): ExportFeedbackState {
  const label: ExportFormatLabel = format === "csv" ? "CSV" : "JSON";
  try {
    action();
    return { format: label, status: "started" };
  } catch {
    return { format: label, status: "failed" };
  }
}

export function exportFeedbackMessage(state: ExportFeedbackState): string {
  return state.status === "started"
    ? `${state.format} download started.`
    : `${state.format} export could not be prepared. Please try again.`;
}

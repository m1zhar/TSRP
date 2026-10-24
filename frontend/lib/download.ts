export type DownloadEnvironment = {
  createObjectURL: (blob: Blob) => string;
  revokeObjectURL: (url: string) => void;
  createAnchor: () => HTMLAnchorElement;
  appendAnchor: (anchor: HTMLAnchorElement) => void;
  schedule: (callback: () => void, delay: number) => unknown;
};

const browserEnvironment: DownloadEnvironment = {
  createObjectURL: (blob) => URL.createObjectURL(blob),
  revokeObjectURL: (url) => URL.revokeObjectURL(url),
  createAnchor: () => document.createElement("a"),
  appendAnchor: (anchor) => document.body.append(anchor),
  schedule: (callback, delay) => window.setTimeout(callback, delay),
};

/** Keep the object URL alive briefly so browsers can begin consuming the download. */
export function downloadResearchFile(
  content: string,
  format: "json" | "csv",
  ticker: string,
  environment: DownloadEnvironment = browserEnvironment,
): void {
  const safeTicker = ticker.toLowerCase().replace(/[^a-z0-9.-]+/gu, "-").replace(/^-+|-+$/gu, "") || "research";
  const mimeType = format === "json" ? "application/json" : "text/csv;charset=utf-8";
  const url = environment.createObjectURL(new Blob([content], { type: mimeType }));
  let anchor: HTMLAnchorElement | null = null;
  try {
    anchor = environment.createAnchor();
    anchor.href = url;
    anchor.download = `tsrp-${safeTicker}-research.${format}`;
    environment.appendAnchor(anchor);
    anchor.click();
  } finally {
    try {
      anchor?.remove();
    } finally {
      environment.schedule(() => environment.revokeObjectURL(url), 1000);
    }
  }
}

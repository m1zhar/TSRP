"use client";

import { useState } from "react";
import { clearBrowserResearchData, type BrowserResearchClearStatus } from "@/lib/browser-research-data";

const STATUS_COPY: Record<BrowserResearchClearStatus, string> = {
  cleared: "All TSRP snapshots, plus this tab’s scenario drafts, were removed.",
  partial: "Some TSRP browser data could not be cleared. Use your browser’s site-data settings to remove the remaining data.",
  unavailable: "Browser storage is unavailable. Use your browser’s site-data settings to remove TSRP data.",
};

function availableStorage(kind: "localStorage" | "sessionStorage") {
  try {
    return window[kind];
  } catch {
    return null;
  }
}

export function PrivacyDataControls() {
  const [status, setStatus] = useState<BrowserResearchClearStatus | null>(null);

  function clearData() {
    if (!window.confirm("Remove all saved TSRP analyses from this browser and scenario drafts from this tab? This cannot be undone.")) return;
    setStatus(clearBrowserResearchData(availableStorage("localStorage"), availableStorage("sessionStorage")));
  }

  return (
    <div className="privacy-data-controls">
      <p id="privacy-clear-description">Removes saved TSRP analyses from this browser, including legacy snapshots, and scenario drafts from this tab. It does not remove downloaded exports or data held by TSRP’s hosting or market-data providers.</p>
      <button className="button button-secondary" type="button" aria-describedby="privacy-clear-description" onClick={clearData}>Clear saved research</button>
      {status && <p className="privacy-data-status" role={status === "cleared" ? "status" : "alert"}>{STATUS_COPY[status]}</p>}
    </div>
  );
}

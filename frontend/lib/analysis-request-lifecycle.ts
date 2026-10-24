type ControllerRef = { current: AbortController | null };
type RequestIdRef = { current: number };
type AutomaticAnalysisTickerRef = { current: string | null };

export function claimAutomaticAnalysisAttempt(
  attemptedTicker: AutomaticAnalysisTickerRef,
  selectedTicker: string,
): boolean {
  const normalized = selectedTicker.trim().toUpperCase();
  if (!normalized || attemptedTicker.current === normalized) return false;
  attemptedTicker.current = normalized;
  return true;
}

export function releaseAutomaticAnalysisAttempt(
  attemptedTicker: AutomaticAnalysisTickerRef,
  ticker: string,
): void {
  if (attemptedTicker.current === ticker.trim().toUpperCase()) attemptedTicker.current = null;
}

export function isAnalysisTickerStillSelected(search: string, ticker: string): boolean {
  const selected = new URLSearchParams(search).get("ticker")?.trim().toUpperCase();
  return selected === ticker.trim().toUpperCase();
}

export function cancelPendingAnalysisForDifferentTicker(
  analysisController: ControllerRef,
  requestId: RequestIdRef,
  loadingTicker: string,
  selectedTicker: string,
): boolean {
  const selected = selectedTicker.trim().toUpperCase();
  if (!selected || loadingTicker.trim().toUpperCase() === selected || !analysisController.current) return false;

  analysisController.current.abort();
  analysisController.current = null;
  requestId.current += 1;
  return true;
}

export function cancelRequestsOnHome(
  analysisController: ControllerRef,
  scenarioController: ControllerRef,
  requestId: RequestIdRef,
  scenarioRequestId: RequestIdRef,
) {
  const analysisRequest = analysisController.current;
  const scenarioRequest = scenarioController.current;
  if (analysisRequest) {
    analysisRequest.abort();
    analysisController.current = null;
    requestId.current += 1;
  }
  if (scenarioRequest) {
    scenarioRequest.abort();
    scenarioController.current = null;
    scenarioRequestId.current += 1;
  }
  return { cancelAnalysis: analysisRequest !== null, cancelScenario: scenarioRequest !== null };
}

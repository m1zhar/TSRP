export function shouldReloadSubmittedTicker(currentTicker: string, submittedTicker: string): boolean {
  return currentTicker.trim().toUpperCase() === submittedTicker.trim().toUpperCase();
}

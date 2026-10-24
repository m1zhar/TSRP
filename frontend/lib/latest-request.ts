export class LatestRequestGate {
  private current: AbortController | null = null;

  begin(): AbortController {
    this.cancel();
    const controller = new AbortController();
    this.current = controller;
    return controller;
  }

  isCurrent(controller: AbortController): boolean {
    return this.current === controller && !controller.signal.aborted;
  }

  cancel(): void {
    this.current?.abort();
    this.current = null;
  }
}

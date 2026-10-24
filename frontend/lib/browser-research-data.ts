import { ACCOUNT_SNAPSHOT_STORAGE_PREFIX, SNAPSHOT_STORAGE_KEY } from "./snapshot-store.ts";

export const SCENARIO_DRAFT_KEY_PREFIX = "tsrp.scenario-draft.";

export type BrowserDataStorage = Pick<Storage, "length" | "key" | "removeItem">;
export type BrowserResearchClearStatus = "cleared" | "partial" | "unavailable";

export function isTsrpSnapshotStorageChange(key: string | null): boolean {
  return key === null || key === SNAPSHOT_STORAGE_KEY || key?.startsWith(ACCOUNT_SNAPSHOT_STORAGE_PREFIX) === true;
}

export function clearBrowserResearchData(
  localStorage: BrowserDataStorage | null,
  sessionStorage: BrowserDataStorage | null,
): BrowserResearchClearStatus {
  let completedStores = 0;
  let failedStores = 0;

  if (localStorage) {
    try {
      const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
        .filter((key): key is string => key === SNAPSHOT_STORAGE_KEY || key?.startsWith(ACCOUNT_SNAPSHOT_STORAGE_PREFIX) === true);
      let localCleared = true;
      for (const key of keys) {
        try {
          localStorage.removeItem(key);
        } catch {
          localCleared = false;
        }
      }
      if (localCleared) completedStores += 1;
      else failedStores += 1;
    } catch {
      failedStores += 1;
    }
  } else {
    failedStores += 1;
  }

  if (sessionStorage) {
    try {
      const keys = Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index))
        .filter((key): key is string => key?.startsWith(SCENARIO_DRAFT_KEY_PREFIX) === true);
      let sessionCleared = true;
      for (const key of keys) {
        try {
          sessionStorage.removeItem(key);
        } catch {
          sessionCleared = false;
        }
      }
      if (sessionCleared) completedStores += 1;
      else failedStores += 1;
    } catch {
      failedStores += 1;
    }
  } else {
    failedStores += 1;
  }

  if (failedStores === 0) return "cleared";
  return completedStores > 0 ? "partial" : "unavailable";
}

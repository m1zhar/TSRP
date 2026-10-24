import assert from "node:assert/strict";
import test from "node:test";
import { clearBrowserResearchData, isTsrpSnapshotStorageChange, SCENARIO_DRAFT_KEY_PREFIX, type BrowserDataStorage } from "./browser-research-data.ts";
import { ACCOUNT_SNAPSHOT_STORAGE_PREFIX, SNAPSHOT_STORAGE_KEY } from "./snapshot-store.ts";

class MemoryStorage implements BrowserDataStorage {
  values = new Map<string, string>();
  failRemove = new Set<string>();
  failRead = false;

  get length() {
    if (this.failRead) throw new Error("storage denied");
    return this.values.size;
  }

  key(index: number) {
    if (this.failRead) throw new Error("storage denied");
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string) {
    if (this.failRemove.has(key)) throw new Error("storage denied");
    this.values.delete(key);
  }
}

test("clear removes only TSRP saved snapshots and scenario drafts", () => {
  const local = new MemoryStorage();
  const session = new MemoryStorage();
  local.values.set(SNAPSHOT_STORAGE_KEY, "saved research");
  local.values.set(`${ACCOUNT_SNAPSHOT_STORAGE_PREFIX}${"a".repeat(32)}`, "account-scoped research");
  local.values.set("other-app.preferences", "keep");
  session.values.set(`${SCENARIO_DRAFT_KEY_PREFIX}v1:AAPL`, "draft");
  session.values.set("other-app.draft", "keep");

  assert.equal(clearBrowserResearchData(local, session), "cleared");
  assert.deepEqual([...local.values], [["other-app.preferences", "keep"]]);
  assert.deepEqual([...session.values], [["other-app.draft", "keep"]]);
});

test("a blocked storage area reports partial clearing while clearing the accessible area", () => {
  const local = new MemoryStorage();
  local.values.set(SNAPSHOT_STORAGE_KEY, "saved research");
  const session = new MemoryStorage();
  session.values.set(`${SCENARIO_DRAFT_KEY_PREFIX}v1:MSFT`, "draft");
  session.failRead = true;

  assert.equal(clearBrowserResearchData(local, session), "partial");
  assert.equal(local.values.has(SNAPSHOT_STORAGE_KEY), false);
  assert.equal(session.values.has(`${SCENARIO_DRAFT_KEY_PREFIX}v1:MSFT`), true);
});

test("a per-key deletion failure is reported without hiding successful cleanup", () => {
  const local = new MemoryStorage();
  local.values.set(SNAPSHOT_STORAGE_KEY, "saved research");
  const session = new MemoryStorage();
  const blockedKey = `${SCENARIO_DRAFT_KEY_PREFIX}v1:NVDA`;
  session.values.set(blockedKey, "draft");
  session.failRemove.add(blockedKey);

  assert.equal(clearBrowserResearchData(local, session), "partial");
  assert.equal(local.values.has(SNAPSHOT_STORAGE_KEY), false);
  assert.equal(session.values.has(blockedKey), true);
});

test("unavailable storage areas return an unavailable state", () => {
  assert.equal(clearBrowserResearchData(null, null), "unavailable");
});

test("snapshot UI refreshes only for TSRP snapshot changes or a full local-storage clear", () => {
  assert.equal(isTsrpSnapshotStorageChange(SNAPSHOT_STORAGE_KEY), true);
  assert.equal(isTsrpSnapshotStorageChange(`${ACCOUNT_SNAPSHOT_STORAGE_PREFIX}${"a".repeat(32)}`), true);
  assert.equal(isTsrpSnapshotStorageChange(null), true);
  assert.equal(isTsrpSnapshotStorageChange("other-app.preferences"), false);
});

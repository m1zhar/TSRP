import assert from "node:assert/strict";
import test from "node:test";
import {
  ACCOUNT_SNAPSHOT_STORAGE_PREFIX,
  clearSnapshotStore,
  LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY,
  MAX_SNAPSHOTS_PER_TICKER,
  readSnapshotStore,
  readSnapshots,
  SNAPSHOT_STORAGE_KEY,
  SnapshotStoreError,
  writeSnapshots,
  type SnapshotStorage,
  type StoredSnapshot,
} from "./snapshot-store.ts";

class MemoryStorage implements SnapshotStorage {
  value: string | null = null;
  otherItems = new Map<string, string>();
  failRead = false;
  failWrite = false;

  getItem(key: string) {
    if (this.failRead) throw Object.assign(new Error("denied"), { name: "SecurityError" });
    return key === SNAPSHOT_STORAGE_KEY ? this.value : this.otherItems.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    if (this.failWrite) throw Object.assign(new Error("full"), { name: "QuotaExceededError" });
    if (key === SNAPSHOT_STORAGE_KEY) this.value = value;
    else this.otherItems.set(key, value);
  }

  removeItem(key: string) {
    if (key === SNAPSHOT_STORAGE_KEY) this.value = null;
    else this.otherItems.delete(key);
  }
}

function snapshot(ticker: string, savedAt = "2026-10-10T12:00:00Z"): StoredSnapshot {
  return { ticker, saved_at: savedAt, retrieved_at: savedAt, methodology_version: "score-v10", analysis_schema_version: "analysis-v5" };
}

test("snapshot store retains old schema records for an explicit incompatible comparison", () => {
  const storage = new MemoryStorage();
  const older = { ticker: "AAPL", saved_at: "2026-10-09T12:00:00Z", retrieved_at: "2026-10-09T12:00:00Z" };
  storage.value = JSON.stringify([older]);

  assert.deepEqual(readSnapshots("AAPL", storage), [older]);
  assert.deepEqual(readSnapshotStore(storage), [older]);
});

test("snapshot store rejects malformed present score and timestamp fields without overwriting them", () => {
  const malformed = [
    { ticker: "AAPL", saved_at: { unexpected: true }, score: 80 },
    { ticker: "AAPL", saved_at: "2026-10-10T12:00:00Z", score: "not-a-number" },
    { ticker: "AAPL", retrieved_at: "not-a-date", score: 80 },
  ];

  for (const item of malformed) {
    const storage = new MemoryStorage();
    storage.value = JSON.stringify([item]);
    const original = storage.value;

    assert.throws(
      () => readSnapshotStore(storage),
      (error) => error instanceof SnapshotStoreError && error.issue === "corrupt",
    );
    assert.equal(storage.value, original);
  }
});

test("snapshot retention caps one ticker without deleting another ticker's records", () => {
  const storage = new MemoryStorage();
  const all = Array.from({ length: MAX_SNAPSHOTS_PER_TICKER + 2 }, (_, index) => snapshot("AAPL", `2026-10-${String(index + 1).padStart(2, "0")}T12:00:00Z`));
  storage.value = JSON.stringify([snapshot("MSFT")]);

  writeSnapshots("AAPL", all, storage);
  const result = readSnapshotStore(storage);
  assert.equal(result.length, MAX_SNAPSHOTS_PER_TICKER + 1);
  const retained = readSnapshots("AAPL", storage);
  assert.equal(retained.length, MAX_SNAPSHOTS_PER_TICKER);
  assert.deepEqual(retained.map((item) => item.saved_at), all.slice(-MAX_SNAPSHOTS_PER_TICKER).map((item) => item.saved_at));
  assert.equal(readSnapshots("MSFT", storage).length, 1);
});

test("removing one ticker's snapshots preserves saved research for other tickers", () => {
  const storage = new MemoryStorage();
  storage.value = JSON.stringify([snapshot("AAPL"), snapshot("MSFT"), snapshot("AAPL")]);

  writeSnapshots("aapl", [], storage);

  assert.deepEqual(readSnapshots("AAPL", storage), []);
  assert.equal(readSnapshots("MSFT", storage).length, 1);
});

test("snapshot writes reject cross-ticker records without changing existing research", () => {
  const storage = new MemoryStorage();
  storage.value = JSON.stringify([snapshot("AAPL"), snapshot("MSFT")]);
  const previous = storage.value;

  assert.throws(
    () => writeSnapshots("AAPL", [snapshot("MSFT")], storage),
    (error) => error instanceof SnapshotStoreError && error.issue === "invalid",
  );
  assert.equal(storage.value, previous);
  assert.throws(
    () => writeSnapshots("../MSFT", [snapshot("../MSFT")], storage),
    (error) => error instanceof SnapshotStoreError && error.issue === "invalid",
  );
  assert.equal(storage.value, previous);
});

test("corrupt data is preserved until the explicit reset operation", () => {
  const storage = new MemoryStorage();
  storage.value = "{not-json";

  assert.throws(() => readSnapshotStore(storage), (error) => error instanceof SnapshotStoreError && error.issue === "corrupt");
  assert.equal(storage.value, "{not-json");
  clearSnapshotStore(storage);
  assert.equal(storage.value, null);
  assert.equal(storage.getItem(SNAPSHOT_STORAGE_KEY), null);
});

test("clearing TSRP snapshots preserves unrelated browser storage keys", () => {
  const storage = new MemoryStorage();
  storage.value = JSON.stringify([snapshot("AAPL")]);
  storage.otherItems.set("unrelated-app.preferences", "keep-me");

  clearSnapshotStore(storage);

  assert.equal(storage.getItem(SNAPSHOT_STORAGE_KEY), null);
  assert.equal(storage.getItem("unrelated-app.preferences"), "keep-me");
});

test("quota failures are reported without replacing existing stored snapshots", () => {
  const storage = new MemoryStorage();
  storage.value = JSON.stringify([snapshot("MSFT")]);
  const original = storage.value;
  storage.failWrite = true;

  assert.throws(() => writeSnapshots("AAPL", [snapshot("AAPL")], storage), (error) => error instanceof SnapshotStoreError && error.issue === "quota");
  assert.equal(storage.value, original);
});

test("unavailable browser storage has a typed safe error", () => {
  const storage = new MemoryStorage();
  storage.failRead = true;
  assert.throws(() => readSnapshotStore(storage), (error) => error instanceof SnapshotStoreError && error.issue === "unavailable");
});

test("anonymous snapshots use the shared browser-profile store and migrate the former public key", () => {
  const storage = new MemoryStorage();
  const previous = snapshot("AAPL");
  storage.otherItems.set(LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY, JSON.stringify([previous]));

  assert.deepEqual(readSnapshots("AAPL", storage), [previous]);
  writeSnapshots("MSFT", [snapshot("MSFT")], storage);
  assert.deepEqual(readSnapshots("AAPL", storage), [previous]);
  assert.equal(storage.getItem(LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY), null);
  assert.equal(readSnapshotStore(storage).length, 2);
  clearSnapshotStore(storage);
  assert.deepEqual(readSnapshotStore(storage), []);
});

test("public snapshot migration does not import another legacy account namespace", () => {
  const storage = new MemoryStorage();
  storage.otherItems.set(`${ACCOUNT_SNAPSHOT_STORAGE_PREFIX}${"a".repeat(32)}`, JSON.stringify([snapshot("AAPL")]));

  assert.deepEqual(readSnapshotStore(storage), []);
  assert.equal(storage.getItem(`${ACCOUNT_SNAPSHOT_STORAGE_PREFIX}${"a".repeat(32)}`), JSON.stringify([snapshot("AAPL")]));
});

import assert from "node:assert/strict";
import test from "node:test";
import {
  readScenarioDraft,
  scenarioDraftValidationErrors,
  SCENARIO_DRAFT_STORAGE_PREFIX,
  writeScenarioDraft,
  type ScenarioDraft,
  type ScenarioDraftStorage,
} from "./scenario-draft.ts";

const defaults: ScenarioDraft = {
  starting_growth: "2.0",
  fcf_margin: "18.3",
  discount_rate: "9.4",
  terminal_growth: "2.4",
};
const LEGACY_PUBLIC_KEY = `tsrp.scenario-draft.v2:${"0".repeat(32)}:AAPL`;

function memoryStorage(initial: Record<string, string> = {}): ScenarioDraftStorage & { values: Map<string, string> } {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, value); },
    removeItem(key) { values.delete(key); },
  };
}

test("scenario drafts survive reloads within the tab and are isolated by ticker", () => {
  const storage = memoryStorage();
  const edited = { ...defaults, starting_growth: "4.0" };

  assert.equal(writeScenarioDraft(" aapl ", edited, storage), "stored");
  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage), { draft: edited, storageAvailable: true });
  assert.deepEqual(readScenarioDraft("MSFT", defaults, storage), { draft: defaults, storageAvailable: true });
});

test("draft validation accepts blank fields but bounds each numeric input", () => {
  const storage = memoryStorage();
  const partial = { ...defaults, starting_growth: "", fcf_margin: "1.25e1" };

  assert.equal(writeScenarioDraft("AAPL", partial, storage), "stored");
  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage).draft, partial);
  assert.equal(writeScenarioDraft("AAPL", { ...defaults, discount_rate: "20.1" }, storage), "invalid");
  assert.equal(writeScenarioDraft("AAPL", { ...defaults, terminal_growth: "Infinity" }, storage), "invalid");
});

test("scenario calculation validation matches server ranges and rate ordering", () => {
  assert.deepEqual(scenarioDraftValidationErrors(defaults), {});
  assert.deepEqual(scenarioDraftValidationErrors({ ...defaults, fcf_margin: "0" }), {
    fcf_margin: "Enter a value from 1% to 50%.",
  });
  assert.deepEqual(scenarioDraftValidationErrors({ ...defaults, discount_rate: "2.9" }), {
    discount_rate: "Enter a value from 3% to 20%.",
  });
  assert.deepEqual(scenarioDraftValidationErrors({ ...defaults, fcf_margin: "" }), {
    fcf_margin: "Enter a value from 1% to 50%.",
  });
  assert.deepEqual(scenarioDraftValidationErrors({ ...defaults, discount_rate: "3.0", terminal_growth: "3.0" }), {
    discount_rate: "Must be greater than long-run growth.",
  });
});

test("scenario draft persistence rejects values that the server will reject", () => {
  const storage = memoryStorage();
  assert.equal(writeScenarioDraft("AAPL", { ...defaults, fcf_margin: "0.9" }, storage), "invalid");
  assert.equal(writeScenarioDraft("AAPL", { ...defaults, discount_rate: "2.9" }, storage), "invalid");
});

test("malformed or invalid stored drafts fall back without overwriting browser data", () => {
  const key = `${SCENARIO_DRAFT_STORAGE_PREFIX}AAPL`;
  const storage = memoryStorage({ [key]: JSON.stringify({ ...defaults, starting_growth: "100" }) });

  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage), { draft: defaults, storageAvailable: true });
  assert.equal(storage.values.has(key), true);
  storage.values.set(key, "{");
  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage), { draft: defaults, storageAvailable: true });
  assert.equal(storage.values.get(key), "{");
});

test("unavailable storage does not block editing or analysis defaults", () => {
  assert.deepEqual(readScenarioDraft("AAPL", defaults, null), { draft: defaults, storageAvailable: false });
  assert.equal(writeScenarioDraft("AAPL", defaults, null), "unavailable");
  assert.equal(writeScenarioDraft("AAPL", { ...defaults, discount_rate: "100" }, memoryStorage()), "invalid");
  assert.equal(writeScenarioDraft("not a ticker", defaults, memoryStorage()), "invalid");
  const throwingStorage: ScenarioDraftStorage = {
    getItem() { throw new Error("Storage denied"); },
    setItem() { throw new Error("Storage denied"); },
  };
  assert.deepEqual(readScenarioDraft("AAPL", defaults, throwingStorage), { draft: defaults, storageAvailable: false });
  assert.equal(writeScenarioDraft("AAPL", defaults, throwingStorage), "unavailable");
});

test("scenario drafts in the public app are shared only within this browser profile and ticker", () => {
  const storage = memoryStorage();
  const edited = { ...defaults, starting_growth: "4.0" };

  assert.equal(writeScenarioDraft("AAPL", edited, storage), "stored");
  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage).draft, edited);
  assert.deepEqual(readScenarioDraft("MSFT", defaults, storage).draft, defaults);
});

test("public scenario drafts migrate the former zero-scope draft key", () => {
  const storage = memoryStorage({ [LEGACY_PUBLIC_KEY]: JSON.stringify({ ...defaults, starting_growth: "6.0" }) });
  const migrated = { ...defaults, starting_growth: "6.0" };

  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage), { draft: migrated, storageAvailable: true });
  assert.equal(storage.values.has(LEGACY_PUBLIC_KEY), false);
  assert.equal(storage.values.get(`${SCENARIO_DRAFT_STORAGE_PREFIX}AAPL`), JSON.stringify(migrated));
  assert.equal(writeScenarioDraft("MSFT", defaults, memoryStorage()), "stored");
});

test("scenario migration preserves the legacy draft and warns if the new key cannot be written", () => {
  const storage = memoryStorage({ [LEGACY_PUBLIC_KEY]: JSON.stringify({ ...defaults, starting_growth: "6.0" }) });
  storage.setItem = () => { throw new Error("Storage full"); };

  assert.deepEqual(readScenarioDraft("AAPL", defaults, storage), {
    draft: { ...defaults, starting_growth: "6.0" },
    storageAvailable: false,
  });
  assert.equal(storage.values.has(LEGACY_PUBLIC_KEY), true);
});

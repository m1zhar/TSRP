import assert from "node:assert/strict";
import test from "node:test";
import { safeReturnPath } from "./safe-return-path.ts";

test("safe return paths preserve only same-origin paths and research state", () => {
  assert.equal(safeReturnPath("/?ticker=AAPL&section=Valuation"), "/?ticker=AAPL&section=Valuation");
  assert.equal(safeReturnPath("/privacy#data"), "/privacy#data");
  assert.equal(safeReturnPath("/"), "/");
});

test("unsafe, malformed, or non-string return targets fall back to home", () => {
  for (const value of [
    null,
    undefined,
    new File(["x"], "return.txt"),
    "https://attacker.example/path",
    "//attacker.example/path",
    "///attacker.example/path",
    "/\\attacker.example/path",
    "relative/path",
    "http://[",
  ]) {
    assert.equal(safeReturnPath(value), "/", `unexpectedly accepted ${String(value)}`);
  }
});

test("encoded separators remain a local path rather than becoming an external redirect", () => {
  const path = safeReturnPath("/%2f%2fattacker.example/path");
  assert.equal(path, "/%2f%2fattacker.example/path");
  assert.equal(new URL(path, "https://tsrp.example").origin, "https://tsrp.example");
});

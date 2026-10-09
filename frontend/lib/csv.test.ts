import assert from "node:assert/strict";
import test from "node:test";
import { toCsv } from "./csv.ts";

test("CSV quotes separators, quotes, and line breaks", () => {
  assert.equal(toCsv([["A,B", 'say "hi"', "two\nlines"]]), '"A,B","say ""hi""","two\nlines"\r\n');
});

test("CSV neutralizes formula-like external text but preserves numeric negatives", () => {
  assert.equal(toCsv([["=1+1", "\t@SUM(A1:A2)", -12.5]]), '"\'=1+1","\'\t@SUM(A1:A2)",-12.5\r\n');
});

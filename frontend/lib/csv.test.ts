import assert from "node:assert/strict";
import test from "node:test";
import { toCsv } from "./csv.ts";

test("CSV quotes separators, quotes, and line breaks", () => {
  assert.equal(toCsv([["A,B", 'say "hi"', "two\nlines"]]), '"A,B","say ""hi""","two\nlines"\r\n');
});

test("CSV prefixes formula-like text but preserves numeric negatives", () => {
  assert.equal(toCsv([["=1+1", "\t@SUM(A1:A2)", -12.5]]), '"\t=1+1","\t\t@SUM(A1:A2)",-12.5\r\n');
});

test("CSV prefixes full-width formula-like text after Unicode whitespace", () => {
  const cells = ["\u3000＝1+1", "＋1+1", "－1+1", "＠SUM(1,1)", "\uFEFF=1+1", "\r=1+1", "\n=1+1"];
  const expected = ["\t\u3000＝1+1", "\t＋1+1", "\t－1+1", "\t＠SUM(1,1)", "\t\uFEFF=1+1", "\t\r=1+1", "\t\n=1+1"]
    .map((cell) => `"${cell}"`)
    .join(",");
  assert.equal(toCsv([cells]), `${expected}\r\n`);
});

test("CSV keeps injected separators and quotes inside a neutralized cell", () => {
  assert.equal(toCsv([[`=1+2";=1+2`]]), '"\t=1+2"";=1+2"\r\n');
});

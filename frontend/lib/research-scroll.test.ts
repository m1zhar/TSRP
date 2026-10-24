import assert from "node:assert/strict";
import test from "node:test";
import { researchScrollKey, scrollToResearchHeading } from "./research-scroll.ts";

test("pending analysis becoming ready retries section focus without a section change", () => {
  const pending = researchScrollKey("Valuation", "AAPL", undefined);
  const resolved = researchScrollKey("Valuation", "AAPL", "AAPL");

  assert.notEqual(pending, resolved);
  assert.equal(researchScrollKey("Valuation", "AAPL", "MSFT"), pending);
});

test("changing section still creates a distinct scroll revision for the ready company", () => {
  assert.notEqual(
    researchScrollKey("Valuation", "AAPL", "AAPL"),
    researchScrollKey("Catalysts & Risks", "AAPL", "AAPL"),
  );
});

test("section navigation uses instant scrolling when reduced motion is requested", () => {
  const calls: unknown[] = [];
  const heading = {
    scrollIntoView: (options: unknown) => calls.push(["scroll", options]),
    focus: (options: unknown) => calls.push(["focus", options]),
  } as unknown as HTMLElement;

  scrollToResearchHeading(heading, true);

  assert.deepEqual(calls, [
    ["scroll", { behavior: "instant", block: "start" }],
    ["focus", { preventScroll: true }],
  ]);
});

test("normal navigation also focuses instantly so subsequent controls do not move under a pointer", () => {
  const calls: unknown[] = [];
  const heading = {
    scrollIntoView: (options: unknown) => calls.push(["scroll", options]),
    focus: (options: unknown) => calls.push(["focus", options]),
  } as unknown as HTMLElement;

  scrollToResearchHeading(heading, false);

  assert.deepEqual(calls, [
    ["scroll", { behavior: "instant", block: "start" }],
    ["focus", { preventScroll: true }],
  ]);
});

test("missing section headings are a no-op", () => {
  assert.doesNotThrow(() => scrollToResearchHeading(null, true));
});

import assert from "node:assert/strict";
import test from "node:test";
import { researchUrl, sectionFromQuery } from "./research-navigation.ts";
import { SECTIONS } from "./types.ts";

test("every research section round-trips in a ticker-specific shareable URL", () => {
  for (const section of SECTIONS) {
    const url = researchUrl(" aapl ", section);
    const parsed = new URL(url, "https://tsrp.example");

    assert.equal(parsed.searchParams.get("ticker"), "AAPL");
    assert.equal(sectionFromQuery(parsed.searchParams.get("section")), section);
  }
});

test("the home score omits its redundant section parameter", () => {
  assert.equal(researchUrl(null, "TSRP Score"), "/");
  assert.equal(researchUrl("MSFT", "TSRP Score"), "/?ticker=MSFT");
});

test("unsupported section query values safely select the score home", () => {
  for (const value of [null, "unknown", "valuation", "TSRP Score "]) {
    assert.equal(sectionFromQuery(value), "TSRP Score");
  }
});

test("section labels are URL-encoded without losing their exact display name", () => {
  const url = researchUrl("BRK.B", "Expectations vs Reality");
  assert.equal(url, "/?ticker=BRK.B&section=Expectations+vs+Reality");
  const parsed = new URL(url, "https://tsrp.example");
  assert.equal(sectionFromQuery(parsed.searchParams.get("section")), "Expectations vs Reality");
});

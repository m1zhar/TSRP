import assert from "node:assert/strict";
import test from "node:test";
import { freeCashFlowEvidenceNote, freeCashFlowMethodNote } from "./fcf-evidence.ts";

test("FCF evidence explains when the amount is calculated from aligned cash-flow components", () => {
  assert.equal(
    freeCashFlowEvidenceNote(0.12, "calculated: annual SEC operating cash flow less capital spending", "12.0%"),
    "Cash-flow margin 12.0%. Calculated from operating cash flow less capital spending for the same period.",
  );
});

test("FCF evidence identifies a provider-reported fallback instead of implying an unsupported calculation", () => {
  assert.equal(
    freeCashFlowEvidenceNote(-0.04, "provider-reported free cash flow; OCF or capex unavailable", "−4.0%"),
    "Cash-flow margin −4.0%. Provider-reported free cash flow; operating cash flow or capital-spending detail was unavailable.",
  );
});

test("FCF evidence gives a cautious method note for unknown provenance", () => {
  assert.match(freeCashFlowMethodNote("reported"), /available data does not identify/u);
  assert.match(freeCashFlowMethodNote(null), /available data does not identify/u);
  assert.equal(freeCashFlowMethodNote("unavailable"), "Free cash flow is unavailable from the source.");
});

test("missing FCF margin does not blame a single unknown input", () => {
  assert.equal(
    freeCashFlowEvidenceNote(null, "provider-reported free cash flow; OCF or capex unavailable", ""),
    "Cash-flow margin is unavailable for this analysis. Provider-reported free cash flow; operating cash flow or capital-spending detail was unavailable.",
  );
});

test("missing FCF value does not describe it as an amount with unknown provenance", () => {
  assert.equal(
    freeCashFlowEvidenceNote(null, "unavailable", ""),
    "Cash-flow margin is unavailable for this analysis. Free cash flow is unavailable from the source.",
  );
});

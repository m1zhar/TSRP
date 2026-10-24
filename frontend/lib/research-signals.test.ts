import assert from "node:assert/strict";
import test from "node:test";
import {
  analystEstimateDescription,
  analystEstimateDirection,
  analystEstimateHorizon,
  analystEstimateLabel,
  analystEstimateSignal,
  analystEstimateShortLabel,
  missingBalanceSource,
} from "./research-signals.ts";

test("balance evidence gaps cite only the field that is missing", () => {
  const sourceFor = (field: "cash" | "debt") => `${field} source · 2025-12-31 · USD`;
  assert.equal(missingBalanceSource(null, 40, sourceFor), "Cash · cash source · 2025-12-31 · USD");
  assert.equal(missingBalanceSource(10, null, sourceFor), "Debt · debt source · 2025-12-31 · USD");
});

test("multiple balance evidence gaps keep their sources distinct", () => {
  const sourceFor = (field: "cash" | "debt") => field === "cash" ? "Source unavailable" : "SEC EDGAR · 2025-12-31 · USD";
  assert.equal(
    missingBalanceSource(null, null, sourceFor),
    "Cash · Source unavailable; Debt · SEC EDGAR · 2025-12-31 · USD",
  );
});

test("analyst estimate labels and explanations follow the period actually supplied", () => {
  const current = "Current-year analyst revenue estimate";
  const next = "Next-year analyst revenue estimate";

  assert.equal(analystEstimateLabel(current), current);
  assert.equal(analystEstimateShortLabel(current), "Current-year analyst estimate");
  assert.equal(analystEstimateHorizon(current), "current fiscal year");
  assert.match(analystEstimateDescription(current, true), /current fiscal year/u);

  assert.equal(analystEstimateLabel(next), next);
  assert.equal(analystEstimateShortLabel(next), "Next-year analyst estimate");
  assert.equal(analystEstimateHorizon(next), "next fiscal year");
  assert.match(analystEstimateDescription(next, true), /next fiscal year/u);
});

test("analyst estimate copy stays cautious when its value or period is unavailable", () => {
  assert.equal(analystEstimateLabel(null), "Analyst revenue estimate");
  assert.equal(analystEstimateShortLabel(null), "Analyst estimate");
  assert.equal(analystEstimateHorizon(null), "an unspecified fiscal period");
  assert.match(analystEstimateDescription(null, false), /does not provide a usable/u);
  assert.match(analystEstimateDescription(null, true), /period is unavailable/u);
});

test("analyst estimates classify negative growth as contraction without treating zero or missing as a risk", () => {
  assert.equal(analystEstimateDirection(0.12), "growth");
  assert.equal(analystEstimateDirection(-0.08), "contraction");
  assert.equal(analystEstimateDirection(0), "unchanged");
  assert.equal(analystEstimateDirection(null), "unavailable");
  assert.equal(analystEstimateDirection(Number.NaN), "unavailable");
});

test("analyst estimate evidence uses its period and routes contraction to a risk", () => {
  const risk = analystEstimateSignal(-0.08, "Current-year analyst revenue estimate");
  assert.equal(risk?.effect, "risk");
  assert.equal(risk?.title, "Analyst estimate signals revenue contraction");
  assert.match(risk?.description ?? "", /current fiscal year/u);
  assert.match(risk?.description ?? "", /-8\.0%/u);

  const support = analystEstimateSignal(0.12, "Next-year analyst revenue estimate");
  assert.equal(support?.effect, "support");
  assert.equal(support?.title, "Next-year analyst revenue estimate");
  assert.match(support?.description ?? "", /next fiscal year/u);
  assert.equal(analystEstimateSignal(0, "Next-year analyst revenue estimate"), null);
  assert.equal(analystEstimateSignal(null, null), null);

  const unspecifiedSupport = analystEstimateSignal(0.1, "FY2026 (test fixture)");
  assert.match(unspecifiedSupport?.description ?? "", /forecast period is unavailable/u);
  assert.match(unspecifiedSupport?.description ?? "", /10\.0% revenue growth/u);
  assert.doesNotMatch(unspecifiedSupport?.description ?? "", /\ba an\b/u);

  const unspecifiedRisk = analystEstimateSignal(-0.08, null);
  assert.equal(unspecifiedRisk?.effect, "risk");
  assert.match(unspecifiedRisk?.description ?? "", /forecast period is unavailable/u);
  assert.match(unspecifiedRisk?.description ?? "", /-8\.0% revenue growth/u);
  assert.doesNotMatch(unspecifiedRisk?.description ?? "", /\ba an\b/u);
});

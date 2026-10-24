# TSRP methodology — score v10 / analysis v5

TSRP is an educational research index and an assumption-dependent valuation tool. It is not a probability, recommendation, price target, forecast or validated expected-return model. Python is the single calculation authority; UI and exports use its returned results.

## Changes from v9

- Removed exact repeated factors. Business quality uses growth, operating margin and return on average equity. Financial strength uses cash/debt, reported cash-flow margin and debt/assets. Groups now have 50% each; each factor has one-third of its group. These are equal-weight policy choices, not fitted investment weights.
- Retained prior sector saturation thresholds as declared policy thresholds, not measured peer averages.
- Replaced the valuation's financing-dependent CFO-minus-capex proxy with a separate estimated pre-debt operating cash flow. Reported FCF remains visible and in the score; it is never relabeled as unlevered FCF.
- Replaced generic risk-free + ERP + sector spread in the production discount-rate path with an issuer-beta equity cost and a financing-weighted borrowing-cost proxy. No missing beta, tax rate, debt component or currency reference is invented.
- EV adds identifiable same-date preferred/non-controlling book-value interests. The scenario common-equity bridge deducts them. Unreported additional claims and lease conventions remain disclosed limitations.
- Added factor-weight sensitivity, annual forecast cash flows, terminal-value concentration and rate/growth sensitivity. Low-score labels now describe operating evidence, not price expectations.
- Signed scenario contexts are v3. Old snapshots remain stored but are incompatible with v10/v5 comparisons. Historical v8 parity fixtures are retained, not claimed as v10 parity.

## Score: exact formulas

Clamp observed factors to 0–100. Missing, boolean, non-finite and undefined inputs are unavailable, not neutral or zero. Reweight only observed factors. Each group needs at least 50% factor coverage; the final score needs 70% weighted evidence and 70% available group weight, so both groups are required.

| Group | Factor | Normalization before clamping |
| --- | --- | --- |
| Business | Revenue CAGR | 50 + 250 × annual growth |
| Business | Operating margin | 100 × margin / sector policy threshold |
| Business | Return on average equity | 100 × ROE / sector policy threshold |
| Financial | Cash/debt | 50 + 35 × cash/debt; positive cash and explicit zero debt = 100; 0/0 unavailable |
| Financial | Reported cash-flow margin | 100 × (CFO − capex)/revenue / sector policy threshold |
| Financial | Debt/assets | 100 − 125 × debt/assets |

Operating-margin / ROE / cash-flow-margin saturation thresholds:

| Sector | Operating | ROE | Cash flow |
| --- | ---: | ---: | ---: |
| Technology | 30% | 25% | 22% |
| Communication Services | 25% | 22% | 18% |
| Consumer Cyclical | 18% | 22% | 10% |
| Consumer Defensive | 16% | 22% | 11% |
| Industrials | 18% | 20% | 10% |
| Healthcare | 22% | 20% | 14% |
| Energy | 20% | 18% | 13% |
| Basic Materials | 18% | 18% | 10% |
| Utilities | 22% | 14% | 9% |
| Real Estate | 35% | 14% | 15% |

These are not statistically calibrated peer percentiles. Factor correlation, equity buybacks, cyclicality, regulated returns and property depreciation remain limitations. Financial Services and unknown sectors receive facts, not a generic score/enterprise valuation. Real-estate scoring is a generic operating index, not an FFO/AFFO or property valuation.

Weight sensitivity varies one observed factor's effective weight by ±25%, renormalizes, and reports the resulting range. It is not a confidence interval, simultaneous stress, backtest or predictive validation. Valuation and analyst assumptions cannot change the score. Data Confidence is a separate availability/warning heuristic, not statistical accuracy. Different-coverage scores are not directly comparable.

## Accounting and data

- SEC annual duration facts must span 330–400 days in eligible 10-K, 20-F or 40-F filings, including amendments. Balance facts must be instant observations. Latest eligible filing is preferred within a fiscal year. Provenance retains taxonomy, tag, currency, period, form and filing date. A real monitored SEC_USER_AGENT is required; missing configuration is explicit, and SEC is not claimed for Yahoo-only calculations.
- Yahoo annual statements retain dates. Unperioded quote-summary revenue is not an annual substitute. Ratios need matching period/currency and positive denominator. ROE needs positive beginning and ending equity with an annual gap.
- Ownership scope is explicit: ROE pairs parent net income with average parent equity, including preferred equity but excluding non-controlling interests. It is not return on common equity. SEC `ProfitLoss` and equity including non-controlling interests are not aliases of parent facts; common-shareholder earnings are not parent earnings. Annual P/E uses specifically identified earnings available to common shareholders, not unadjusted parent or consolidated profit. If that earnings basis is absent, stale or non-positive, P/E is unavailable. See [XBRL US's taxonomy guidance](https://xbrl.us/guidance/specific-non-controlling-interest-elements/). EV/EBITDA does not silently substitute normalized EBITDA for reported EBITDA.
- CAGR requires consecutive annual gaps of 330–430 days and uses actual elapsed days / 365.2425; missing years are not bridged. Statements older than 550 days, future-dated or undated are excluded from current calculations. The cutoff does not prove a filing is the latest available.
- Reported FCF is same-period CFO minus absolute capital spending. Provider-reported FCF is a labeled fallback only for the reported-FCF view, never the valuation bridge. Negative cash flow stays negative. Incomplete periods are never represented as TTM.
- Market capitalization follows the listing currency. Known subunits use exact scaling. Other conversions require fresh FX aligned to the market timestamp, with pair, direction, date and rate disclosed. Missing FX means N/A, not relabeling. Price-based valuation requires a verified quote within 48 hours and no more than five minutes in the future.
- Price units do not by themselves identify market-cap units. With a pence/cents listing, Yahoo's fast path may calculate capitalization in quote subunits while its company-profile or fallback total is already in major currency. TSRP cross-checks each candidate against positive price × shares observations in an explicitly identified currency, allowing at most 5% magnitude difference for asynchronous inputs. Only a unique supported scale is accepted, normalized to the quote unit before the existing FX conversion. Original total/unit and normalization are retained in provenance. This checks scale, not share-class completeness, ADR conversion or share-count freshness; if the scale cannot be verified, market cap and dependent valuation are unavailable while the operating score remains separate. [yfinance's source](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/quote.py) distinguishes its price × shares fast path from the profile fallback; [LSE's BP quote](https://www.londonstockexchange.com/en/quote/equity/BP~/bp-plc) distinguishes GBX prices from company capitalization in pounds.
- Quote collection reads only currency, price, prior close, market capitalization and shares, rather than eagerly evaluating all lazy quote statistics. Failure of one optional field does not discard the other quote fields. If fast and profile sources declare different trading units, fast amounts are excluded instead of attaching the profile's currency to them.
- Cash and debt need the same balance date. Current plus long-term debt is combined only when comparable; long-term debt alone is not total debt. Restricted-cash aggregates are not unrestricted cash. Identified additional ownership claims are book-value proxies, not market valuations or proof all claims were captured.

## Estimated pre-debt cash flow

Annual Yahoo operating income, pretax income, tax provision, D&A, capex and cash-flow-signed working-capital movement must share the selected revenue period/currency. If SEC revenue disagrees with matching Yahoo revenue by more than 0.01%, the bridge is withheld.

    effective_tax = tax_provision / positive_pretax_income  [0%–50%]
    estimated_FCFF = EBIT − max(EBIT, 0) × effective_tax + D&A − abs(capex) + signed_WC_cash_movement

This is calculated, not a reported subtotal. Stock compensation stays expensed in EBIT. No tax-loss benefit, missing D&A or missing working capital is invented. Effective tax approximates operating/marginal tax; provider working-capital classifications, acquisitions, leases and noncash adjustments can require refinement. A non-positive or above-50% latest margin receives no automatic profitable baseline. An explicit turnaround scenario may assume a positive mature margin while preserving a negative starting margin.

## Estimated financing cost and DCF

    Ke = risk_free + provider_beta × ERP
    Kd_proxy = annual_interest_expense / average(opening_debt, closing_debt)
    WACC_estimate = Ke × E/(E+D) + Kd_proxy × (1−effective_tax) × D/(E+D)

E is fresh FX-comparable market equity, D is book debt, a market-debt proxy. Opening debt must be from the prior annual date. Explicit zero debt needs no fabricated borrowing cost. Positive debt with missing/zero interest has no automatic debt-cost estimate. No sector spread is added. Supported beta is 0–5 and WACC 3–20%; out-of-range values are withheld, not clamped.

The dated USD reference is 2026-01-01: the 2025 year-end row of [Damodaran's January 2026 implied ERP table](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histimpl.html), Treasury reference 4.18% and ERP 4.23%. These are not live and expire after 366 days. Other reporting currencies have no sourced automatic WACC; USD is not substituted. Users may enter an explicit scenario discount assumption. Yahoo beta's estimation window is not independently established. Historical interest cost is not a current spread; effective tax is not a verified marginal rate.

The ten-year model holds initial growth for five years then fades to terminal growth. The pre-debt margin transitions to the mature assumption. Terminal growth is a declared currency policy assumption, not consensus. The perpetual margin embeds reinvestment; sustainable terminal ROIC and capital turnover are not independently estimated. Gordon terminal value requires discount > terminal growth. Annual cash flows, terminal-value concentration and nine rate/growth sensitivities are inspectable and in JSON exports.

Before accepting analysis data, the web client validates the engine's returned ledger: ten sequential finite rows, revenue and cash-flow identities, present values, terminal value, total enterprise value, terminal share, and a unique nine-cell sensitivity grid with a reconciling central case. It does not compute a replacement score or valuation. Negative early cash flows and terminal shares above 100% remain valid when the totals support them. Invalid or inconsistent results use the existing safe validation error, never a plausible-looking substitute.

The reverse solver returns exact, below-range, above-range or unavailable. A relative endpoint spread within 1e-12 cannot identify growth. Bounds are never exact estimates. Required growth is what this model needs, not a literal observation of market beliefs. See [CFA Institute's valuation guidance](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/free-cash-flow-valuation) and [NYU's matching cash-flow/discount-rate framework](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/val.html).

Scenarios use the same DCF implementation and HMAC-signed ticker, periods, currencies, pre-debt starting margin and EV bridge including other claims. The API checks bounds, freshness, method/schema and reconciliation without re-fetching data. Assumptions remain distinct from facts. Negative equity residuals are not clamped to zero. If the starting margin is unavailable, a scenario explicitly assumes its user-entered margin throughout.

## Expectations, snapshots and verification

Historical CAGR, next-year analyst estimates and ten-year model growth have different horizons and are not described as an earnings beat/miss. Guidance, catalyst dates and probabilities are not fabricated.

The estimate adapter preserves an explicitly supplied fiscal end date and forecast currency. For pinned yfinance it reads fiscal dates from the already-fetched earnings-trend cache because the public revenue-estimate dataframe drops that metadata; it makes no additional private request. An absent or changed cache shape yields unknown metadata, not an inferred year. Public dataframe `endDate` metadata is also supported. Contributor counts, when provided, must be positive integers; missing counts remain unknown. Growth below −100%, non-finite/malformed values and values that overflow percentage display are unavailable. Known next-year ends must be between retrieval date and 800 days ahead. Known current-year ends may be up to 120 days before retrieval pending results, or 400 days ahead. These are conservative availability policies, not proof of consensus freshness; the provider does not establish each estimate's revision/publication time.

Estimate changes require matching verified fiscal ends, relative horizons and forecast currencies. Two snapshots both labeled “next year” are not comparable if they refer to different fiscal years. Unknown periods/currencies omit only that change, retaining other valid research comparisons. Snapshot proofs sign the new metadata. Older proofs remain readable but cannot acquire comparison eligibility from unsigned browser-added fiscal metadata. Exports retain dates, currencies and unknown-period limitations.

Snapshots compare the same ticker, currency, methodology and schema. Changed reporting periods/source methods are not like-for-like amount changes. Browser snapshots are not durable cloud records or an audited track record. Exports preserve displayed values, assumptions, provenance and warnings.

Retrieval timestamps must include timezone information; reverse chronological comparisons are rejected using signed retrieval instants, not editable save dates or lexical timezone strings. Rate differences are displayed in percentage points: 12% to 18% is +6 percentage points, not a 6% relative increase (the relative increase would be 50%). Small changes are labeled below display precision rather than rounded into a false zero. Score-point and currency-amount changes retain their own units. The common percentage formatter never displays an overflow as infinity.

Deterministic tests cover mapping, accounting, DCF, signed scenarios and mathematical invariants. The reported-FCF fixture from [Microsoft FY2025](https://www.microsoft.com/investor/reports/ar25/index.html) is CFO $136,162m − capex $64,551m = $71,611m; it is deliberately not described as reconstructed FCFF. Tests and cross-sector live smoke checks do not establish predictive performance. No investment-performance backtest or independent professional model sign-off has been completed.

Provider coverage, delays and terms still apply. SEC process-local pacing is not a deployment-wide rate guarantee. Passing tests is not a legal/compliance or institutional model-validation claim.

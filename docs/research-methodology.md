# TSRP research calculations

This note describes the calculations served by the Next.js application as of `score-v3`. The legacy Streamlit source remains historical and may still contain the earlier missing-evidence behavior; it is not the active web API calculation path.

## Evidence and periods

- Revenue and earnings flows from SEC company facts are selected only from annual 10-K, 20-F, or 40-F observations whose start/end span is 330–400 days. Balance-sheet values are selected only as instant facts. Within a fiscal year, the latest filed eligible fact is preferred, including an amendment when present.
- Yahoo statement values are used as a separate fallback and retain their reported statement date. TSRP does not use the unperioded `info.totalRevenue` quote-summary field as annual revenue.
- A ratio is calculated only when its numerator and denominator have the same period and currency basis, and its denominator is positive. Return on equity uses net income divided by average beginning/end equity; both year-end equity observations are required.
- Yahoo market capitalization is treated in the listing quote currency (price × share count), not the financial-statement currency. Converting it to the reporting currency requires a valid, timestamped FX rate. EV/sales uses annual reported revenue; EV/EBITDA uses positive same-period annual EBITDA; the calculated P/E uses annual net income and is explicitly not a trailing-twelve-month P/E.
- Historical revenue CAGR uses dated annual observations, requires positive values and consecutive gaps of 330–430 days, uses actual elapsed days divided by 365.2425, and stops at a missing annual period. It is backward-looking, not a forecast.
- Free cash flow is operating cash flow less absolute capital spending when both are available for the same period. Provider-reported free cash flow is a fallback only when one input is absent and is labeled as such. A TTM result is available only for four distinct consecutive quarters spanning 250–400 days.

## TSRP Score v3

The score is a 0–100 heuristic research signal, not a calibrated probability, investment rating, expected return, or forecast. The top-level weights are unchanged from the prior baseline:

| Component | Base weight | Comparison |
| --- | ---: | --- |
| Business quality | 35% | Growth, gross/operating/FCF margins, return on average equity, and debt/assets against static sector benchmarks |
| Financial strength | 30% | Cash relative to debt, operating/FCF margins, and debt/assets against static sector benchmarks |
| Historical growth fit | 20% | Historical revenue CAGR compared with growth required by the current enterprise value |
| Analyst growth fit | 15% | Provider-supplied next-year analyst revenue-growth estimate compared with growth required by enterprise value |

Growth-fit sub-scores use `clamp(75 − (required growth − comparison growth) × 160, 0, 100)`. Business and financial sub-score formulas and sector-specific factor weights/benchmarks are implemented in `engine.py` and `methodology.py`.

Missing inputs are excluded; they are not assigned the old fixed score of 40. Available factor weights are renormalized within a subscore. A subscore requires at least 50% of its factor weight. The overall score requires both at least 70% evidence coverage and at least 70% of original top-level component weight available; available top-level weights are then renormalized. Coverage is separate from the product’s Data Confidence label. Scores with different evidence coverage are not directly comparable. A clipped reverse-DCF bound is not treated as an exact growth observation.

Currentness guard: annual statement observations older than 550 days, future-dated, or without a parseable period are excluded from current valuation and score inputs. This is an explicit availability cutoff, not a claim that every filing is current; the source period is still shown.

Market-multiple context (EV/sales, P/E, and EV/EBITDA) is not included in the final score. There is no independent “market expectations” score in the active API.

## Reverse DCF

Enterprise value is TSRP’s approximation: market capitalization plus reported debt less reported cash, only when the quote is fresh and the balance-sheet cash/debt periods align. It excludes preferred equity, minority interests, and any other claim TSRP does not separately capture. If the required data, currency, date alignment, or positive cash-flow conditions are not met, the solve is unavailable.

The 10-year model holds starting revenue growth through year 5, then linearly fades it to terminal growth by year 10. The free-cash-flow margin moves linearly from the latest positive reported margin toward the modeled margin. The model discounts annual free cash flow and a Gordon-growth terminal value. The reverse solver searches a bounded starting-growth range and returns exact, below-range, above-range, or unavailable; it never converts a bound into an exact point estimate.

The baseline modeled FCF margin is a blend of the latest positive aligned annual margin and the arithmetic mean of all finite aligned historical margins, including negative/zero years. If the latest margin is non-positive, the reverse DCF is refused. A user scenario uses only the four validated user assumptions and is clearly distinguished from the baseline.

Discount-rate, terminal-growth, risk-free-rate, equity-risk-premium, and sector-spread values are static illustrative TSRP inputs copied from the inspected prior baseline. Their sources, current calibration, and market validity have not been independently verified. They are not live yields. Unsupported reporting currencies block the baseline valuation rather than silently applying the USD fallback.

## Currency and FX

Reported and market values are kept in identified currencies. FX conversion uses Yahoo Finance daily closes, checks quote age and same-date comparability for cross-rates, and returns N/A when a valid rate is unavailable. The result includes pair(s), rate, and observed timestamp in the `sources.fx` metadata. Quote subunits (for example GBp) are not treated as whole-currency amounts. The DCF only runs when the reporting currency has an explicit TSRP model profile.

## Snapshots and change attribution

Snapshots compare only when ticker, methodology version, analysis schema version, and reporting currency match. Revenue and free-cash-flow amount deltas are omitted if their reporting period changed; a period change is not treated as a comparable amount movement. Snapshots remain in browser local storage and are not a durable or synchronized record.

## Known limitations requiring review

- Yahoo Finance/yfinance field definitions, freshness, coverage, permitted use, and redistribution rights have not been cleared for public or commercial use.
- Static sector/discount-rate calibrations need an independently sourced and reviewed basis before being described as financially validated.
- No distributed rate limiter or abuse protection is configured in the repository.
- The SEC adapter can validate only the format of `SEC_USER_AGENT`, not whether its contact identity is actually monitored.
- The Next.js frontend and Python analysis service are built separately through Vercel Services and share the public `/api/*` route through an explicit rewrite. The Python entry point wraps the existing analysis functions; it does not implement a second score engine. Vercel documents Services and the Python runtime as Beta, so verify Preview routing, limits, environment, and plan behavior before release.

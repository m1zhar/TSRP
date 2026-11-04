# TSRP Score validation: what the evidence supports

TSRP Score v10 summarizes operating evidence. It is experimental, not an investment recommendation, probability, forecast, or validated return model. A score of 80 does not mean an 80% chance of success. Data Confidence describes data availability and warnings; it is separate from the score.

## Checks you can repeat

Run the complete backend suite with `PYTHONPATH=backend backend/.venv/bin/python -W error -m unittest -q`. The focused `tests/test_score_research_validation.py` uses synthetic inputs and a fixed random seed; it never requests market data.

It checks all ten configured sectors against a separate set of calculations (40 cases per sector), every combination of six present or missing factors (64 per sector), seven beneficial input changes per sector, nominal currency scaling, zero or negative cash flow versus missing cash flow, unique factor membership, and the weight-sensitivity range. Existing tests cover invalid numbers, accounting periods, ownership, provider mappings, supported currencies, and source failures. Frontend tests confirm that the takeaway uses evidence returned by the server, preserves missing-data states, and does not calculate a second score.

These checks show that the implementation produces repeatable results. They do not independently verify Yahoo or SEC data, prove that the chosen weights are economically sound, or show that the score predicts returns. Passing tests is not the same as validating an investment thesis.

## What has not been proven

- No independent professional financial-methodology review has been completed.
- No historical return study has been completed using only the information available at the time, including companies that were later delisted.
- Score caps and weights are research choices, not measurements from peer companies.
- Sensitivity ranges depend on the model assumptions. They are not statistical confidence intervals.
- A sector label does not mean that every company in the sector fits one generic model. Real-estate property values, bank capital rules, and commodity cycles need their own methods.

## Protocol required before any predictive claim

Freeze and version the method before measuring performance. Build the historical dataset from filings as they were published, include later restatements and companies that were delisted, and use only information available on each analysis date after a stated reporting delay. Keep companies with different data coverage in separate groups.

Choose the research period, total-return method (including dividends, splits, and other corporate actions), sector, country and size comparisons, benchmark, portfolio turnover (how often holdings change), and transaction-cost assumptions before testing. Set aside a period for final testing and do not use it to tune the method. Report sample sizes, exclusions, missing-data rates, uncertainty, and negative results. Compare TSRP with simple baseline factors and test different weights and timing. Have an independent reviewer reproduce the results before calling the method validated.

Current provider snapshots cannot replace this historical dataset. TSRP must not use today’s revised statements to recreate a backtest and present them as information investors had at the time.

## Sector guidance references

- [CFA Institute: Free Cash Flow Valuation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/free-cash-flow-valuation): free cash flow must be calculated with an appropriate financing and accounting basis; forecasting requires company and industry judgment.
- [Aswath Damodaran: Valuing Financial Service Firms](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/finfirm09.pdf): financial-company debt, regulatory capital and reinvestment complicate generic enterprise valuation; equity-focused approaches require separate inputs.
- [Aswath Damodaran: Commodity Companies, Value Drivers](https://pages.stern.nyu.edu/adamodar/New_Home_Page/littlebook/commodityvaluedrivers.htm): current-year profits may not represent cash generation through a commodity cycle. TSRP does not implement price-cycle normalization.
- [Society of Actuaries in Ireland: Institutional Investment in Real Estate](https://web.actuaries.ie/sites/default/files/2024-07/Institutional%20Investment%20in%20Real%20Estate_paper.pdf), section 16.2.1: net asset value and funds-from-operations measures require property-specific assumptions and consistent definitions. TSRP does not calculate them.

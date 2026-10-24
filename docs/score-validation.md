# Score validation: what the evidence supports

TSRP Score v10 is an experimental operating-evidence index, not an investment recommendation, probability, forecast or validated return model. A score of 80 does not mean an 80% chance of success. Data Confidence is separate.

## Reproducible implementation checks

Run the complete backend suite with `PYTHONPATH=backend backend/.venv/bin/python -W error -m unittest -q`. The focused `tests/test_score_research_validation.py` uses synthetic inputs and a fixed random seed; it never requests market data.

It checks all ten configured sectors against an independently written arithmetic oracle (40 cases per sector), every combination of six present/missing factors (64 per sector), seven beneficial input perturbations per sector, nominal currency scaling, zero/negative versus missing cash flow, unique factor membership and the weight-sensitivity envelope. Existing tests cover non-finite values, accounting periods, ownership, provider mappings, supported currencies and source failures. Frontend tests confirm that the takeaway uses the server-returned evidence, preserves missing states and never calculates a replacement score.

These checks demonstrate reproducible implementation behavior only. They do not independently verify Yahoo or SEC observations, prove that the policy weights are economically optimal, or establish predictive usefulness. Passing a test and validating an investment thesis are different tasks.

## What is not established

- No independent professional financial-methodology review has been completed.
- No point-in-time, survivorship-aware historical return study has been completed.
- Saturation thresholds and weights are declared research policies, not empirically estimated peer percentiles.
- Sensitivity ranges are conditional model outputs, not statistical confidence intervals.
- A configured sector does not imply every business within it fits a generic model. REIT property values, bank regulatory capital and commodity cycles need specialist treatment.

## Protocol required before any predictive claim

Freeze and version the methodology before measuring performance. Use original filing-publication timestamps, restatement vintages and the historical security universe, including delisted firms. Generate each score using only information available at that time, with a declared reporting lag. Keep score-coverage cohorts separate. Predeclare the research horizon, corporate-action-adjusted total-return convention, sector/country/size controls, benchmark, turnover and transaction-cost treatment. Reserve a genuinely held-out period; do not tune thresholds to that period. Report sample sizes, exclusions, missing-data rates, uncertainty and negative results. Compare against simple baseline factors and test sensitivity to weights and timing. Arrange independent replication before describing the result as validated.

Current provider snapshots are not that dataset. TSRP must not reconstruct a historical backtest from today’s revised statements and present it as information investors actually had.

## Sector guidance references

- [CFA Institute: Free Cash Flow Valuation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/free-cash-flow-valuation): free cash flow must be calculated with an appropriate financing and accounting basis; forecasting requires company and industry judgment.
- [Aswath Damodaran: Valuing Financial Service Firms](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/finfirm09.pdf): financial-company debt, regulatory capital and reinvestment complicate generic enterprise valuation; equity-focused approaches require separate inputs.
- [Aswath Damodaran: Commodity Companies, Value Drivers](https://pages.stern.nyu.edu/adamodar/New_Home_Page/littlebook/commodityvaluedrivers.htm): current-year profits may not represent cash generation through a commodity cycle. TSRP does not implement price-cycle normalization.
- [Society of Actuaries in Ireland: Institutional Investment in Real Estate](https://web.actuaries.ie/sites/default/files/2024-07/Institutional%20Investment%20in%20Real%20Estate_paper.pdf), section 16.2.1: net asset value and funds-from-operations measures require property-specific assumptions and consistent definitions. TSRP does not calculate them.

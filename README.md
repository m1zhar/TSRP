# The Saleh Research Project (TSRP)

TSRP is an educational equity-research application for listed company stocks. Its five research sections are:

1. TSRP Score
2. Expectations vs Reality
3. Valuation
4. Catalysts & Risks
5. What Changed?

The app compares what a current company value appears to require with historical revenue growth, near-term analyst estimates, operating quality, free-cash-flow conversion, and balance-sheet evidence. Indexes, commodities, currencies, funds, and other non-company instruments are excluded.

> Educational research only. TSRP is not financial, investment, tax, accounting, or legal advice. It does not make recommendations, predict returns, or guarantee data accuracy. Market data may be delayed, incomplete, or incorrect. Do your own research and consult a qualified professional before making financial decisions.

## Run locally

Use a supported Python environment and install the tested dependency ranges:

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

The Streamlit entrypoint is `app.py`; deterministic reverse-DCF and score logic lives in `engine.py`.

## SEC EDGAR configuration

SEC EDGAR requests require a real monitored contact identity in the HTTP `User-Agent`. Copy `.env.example` as a reference and configure the value in the process environment; TSRP does not load `.env` files automatically and never stores the value in session state or exports.

```bash
export SEC_USER_AGENT="TSRP your-monitored-email@example.com"
python3 -m streamlit run app.py
```

If the variable is missing or the provider is unavailable, SEC-derived figures remain unavailable and the app labels the exact filing-data state. It does not fabricate a fallback contact identity.

## Data sources and limitations

- Yahoo Finance, accessed through `yfinance` and its search/quote endpoints, supplies market quotes, history, estimates, and supplemental fundamentals. Availability, field definitions, delay, rate limits, and redistribution rights are controlled by that provider and should be reviewed before public or commercial use.
- SEC EDGAR supplies annual company facts when a ticker can be mapped to a CIK and the request is configured correctly. SEC company-facts coverage is not universal for every international listing.
- FX conversion uses Yahoo Finance currency symbols. If conversion is unavailable, dependent values stay N/A instead of being relabeled in another currency.
- Market data is cached for up to 15 minutes, SEC facts for up to 24 hours, and FX rates for up to 1 hour. The interface shows the analysis timestamp and these freshness limits.
- TSRP currently has no AI text-generation provider, news feed, account system, analytics, hidden telemetry, or server-side user research database.

The reverse DCF is a scenario model, not a target price. It uses the reporting-currency value, a currency-aware discount-rate profile, a ten-year revenue path that fades toward terminal growth, and a positive evidence-based free-cash-flow margin. Negative or missing cash flow blocks the reverse solve. Scores are deterministic heuristic dashboards, not probabilities or recommendations.

## Privacy and security

TSRP uses the company name/ticker and display-currency selection for the current Streamlit session. The selected ticker may appear in the page URL. Search terms and ticker-derived provider requests are sent to Yahoo Finance; SEC requests send the configured contact identity and ticker-derived CIK to SEC endpoints. TSRP does not send personal notes or exported files to those providers, does not add analytics, and does not create user profiles or collect personal names.

Session state and standard infrastructure/provider logs are controlled by the deployment environment and may have retention policies outside this repository. Review the hosting and provider terms before treating the hosted app as a private workspace. The repository ignores `.env`, Streamlit secrets, and common key files; never commit real credentials.

## Tests and release checks

Run the deterministic test suite and source checks before release:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile app.py engine.py test_engine.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q
git diff --check
python3 -m pip check
```

The automated tests cover currency-rate selection, reverse-DCF monotonicity and bounds, negative free-cash-flow refusal, score reconciliation, non-finite inputs, and snapshot comparability. A release still requires a manual smoke test with provider credentials: analyze a US company, analyze a non-US company with FX conversion, verify unavailable-provider states, test downloads, and inspect the hosted service’s secret configuration and provider terms.

## Release boundary

This repository does not claim regulatory compliance, investment-adviser status, data-license clearance, security certification, or GDPR compliance. Those require a separate legal, provider-contract, and deployment review before public or commercial release.

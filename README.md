# The Saleh Research Project (TSRP)

TSRP is an educational equity-research application for listed company stocks. It compares what a current market price appears to require with historical revenue growth, analyst expectations, financial quality, free-cash-flow conversion, and balance-sheet evidence. Indexes, commodities, currencies, funds, and other non-company instruments are excluded.

> Educational research only. Not financial, investment, tax, accounting, or legal advice. TSRP does not make recommendations, predict returns, or guarantee data accuracy. Market data may be delayed, incomplete, or incorrect. Do your own research and consult a qualified professional before making financial decisions.

## Run

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

SEC EDGAR is always disclosed in the header, Data view, Methodology view, and exports. To enable live annual filing facts, set `SEC_USER_AGENT` to a monitored contact identity before starting Streamlit:

```bash
export SEC_USER_AGENT="Your App Name your-monitored-email@example.com"
```

Without it, TSRP keeps SEC-derived facts as N/A and clearly labels the live status as unavailable. The app never uses a fabricated default identity or substitutes invented filing data.

## Tests

```bash
python3 -m unittest test_engine.py -v
```

Tests use deterministic inputs and do not depend on live Yahoo Finance, SEC, or FX responses. The Data view exports CSV and JSON with source metadata, assumptions, confidence, quality flags, and this disclaimer.

"""Traceable operating cash flow and capital-cost estimates, never silent defaults.

FCFF is reconstructed from operating profit, not a financing-dependent CFO
subtotal. All arguments must already share an annual period and currency.
"""

from datetime import date
import math


CAPITAL_MARKET_REFERENCE = {
    "date": "2026-01-01",
    "currency": "USD",
    "risk_free": .0418,
    "equity_risk_premium": .0423,
    "source": "NYU Stern / Aswath Damodaran, January 2026 historical implied ERP table, 2025 year-end row",
    "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histimpl.html",
}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and abs(result) <= 1e16 else None


def operating_cash_flow(operating_income, pretax_income, tax_expense, depreciation, capex, working_capital_change):
    """Estimated FCFF = EBIT - tax on EBIT + D&A - capex + CFO-signed WC change.

    Stock compensation is not added back: it remains an expense in EBIT.
    Effective tax is an explicit approximation to an operating/marginal rate.
    Negative EBIT receives no assumed tax-loss benefit. Missing is never zero.
    """
    fields = dict(zip(
        ("operating_income", "pretax_income", "tax_expense", "depreciation", "capex", "working_capital_change"),
        map(number, (operating_income, pretax_income, tax_expense, depreciation, capex, working_capital_change)),
    ))
    pretax, tax = fields["pretax_income"], fields["tax_expense"]
    rate = tax / pretax if pretax is not None and pretax > 0 and tax is not None else None
    if rate is not None and not 0 <= rate <= .50:
        rate = None
    missing = [key for key, value in fields.items() if value is None]
    if missing:
        return {"value": None, "tax_rate": rate, "inputs": fields, "reason": "Missing comparable annual inputs: " + ", ".join(missing)}
    op, pretax, tax, da, spend, wc = fields.values()
    if rate is None or da < 0:
        return {"value": None, "tax_rate": None, "inputs": fields, "reason": "A positive pretax profit, an effective tax rate from 0% to 50%, and non-negative D&A are required; no normalized tax rate is invented."}
    value = number(op - max(op, 0) * rate + da - abs(spend) + wc)
    return {
        "value": value, "tax_rate": rate, "inputs": fields,
        "reason": "Estimated pre-debt cash flow: operating profit after estimated tax + depreciation/amortization − capital spending + signed working-capital cash movement. Stock compensation remains expensed. Effective tax and working-capital classifications are modeling approximations.",
    }


def capital_cost(currency, beta, equity_value, debt, opening_debt, interest_expense, tax_rate, *, today=None):
    """CAPM equity cost and weighted financing cost with fully disclosed proxies.

    Book debt approximates market debt. Interest/average debt is a historical
    borrowing-cost proxy, NOT a live credit spread. No sector spread is added.
    """
    beta, equity, debt, opening, interest, tax = map(number, (beta, equity_value, debt, opening_debt, interest_expense, tax_rate))
    reference = dict(CAPITAL_MARKET_REFERENCE)
    age = ((today or date.today()) - date.fromisoformat(reference["date"])).days
    result = {"value": None, "cost_of_equity": None, "cost_of_debt": None,
              "equity_weight": None, "debt_weight": None, "beta": beta,
              "tax_rate": tax, "reference": reference,
              "inputs": {"equity_value": equity, "debt": debt, "opening_debt": opening, "interest_expense": interest}}
    if currency != reference["currency"]:
        return {**result, "reason": "No sourced capital-market reference for this reporting currency. USD rates are not substituted; enter an explicit scenario discount rate."}
    if not 0 <= age <= 366:
        return {**result, "reason": "The dated capital-market reference is future-dated or over one year old; a new reference is required."}
    if beta is None or not 0 <= beta <= 5 or equity is None or equity <= 0 or debt is None or debt < 0 or tax is None or not 0 <= tax <= .5:
        return {**result, "reason": "Issuer beta, positive comparable market equity, non-negative debt, and an observed usable tax rate are required."}
    ke = reference["risk_free"] + beta * reference["equity_risk_premium"]
    kd = None
    if debt > 0:
        if opening is None or opening < 0 or interest is None or interest <= 0 or opening + debt <= 0:
            return {**result, "reason": "Comparable opening debt and annual interest expense are required for the borrowing-cost proxy."}
        kd = interest / ((opening + debt) / 2)
        if not 0 <= kd <= .5:
            return {**result, "reason": "The observed interest/average-debt ratio is outside the supported 0%–50% range; no replacement cost is invented."}
    ew, dw = equity / (equity + debt), debt / (equity + debt)
    value = ke * ew + (kd or 0) * (1 - tax) * dw
    if not .03 <= value <= .20:
        return {**result, "reason": "Estimated financing cost is outside the supported 3%–20% scenario range; review inputs rather than clamping the rate."}
    return {**result, "value": value, "cost_of_equity": ke, "cost_of_debt": kd,
            "equity_weight": ew, "debt_weight": dw,
            "reason": "Estimated WACC: CAPM equity cost weighted by market equity, plus historical interest/average-book-debt cost after estimated tax weighted by book debt. Dated USD inputs and provider beta are assumptions/proxies, not live issuer financing quotes."}

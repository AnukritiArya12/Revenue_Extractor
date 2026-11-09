"""
Derived Financial Ratios Engine.
Calculates financial ratios (margins, tax rates, expense ratios)
from raw extracted metrics without altering the core extraction engine.
"""

from src.extract.engine import extract_financial_metric

# Supported derived ratios and their formulas
DERIVED_METRICS = {
    "net_profit_margin": {
        "label": "Net Profit Margin",
        "numerator": "profit_after_tax",
        "denominator": "revenue",
        "formula": "(Profit After Tax / Revenue) * 100",
        "unit": "%",
        "description": "Measures percentage of revenue remaining after all operating expenses, interest, and taxes.",
    },
    "operating_margin": {
        "label": "Operating Margin",
        "numerator": "operating_profit",
        "denominator": "revenue",
        "formula": "(Operating Profit / Revenue) * 100",
        "unit": "%",
        "description": "Measures percentage of revenue remaining after paying operating expenses.",
    },
    "effective_tax_rate": {
        "label": "Effective Tax Rate",
        "numerator": "tax_expense",
        "denominator": "profit_before_tax",
        "formula": "(Tax Expense / Profit Before Tax) * 100",
        "unit": "%",
        "description": "Calculates average rate at which pre-tax profits are taxed.",
    },
    "total_expense_ratio": {
        "label": "Total Expense Ratio",
        "numerator": "total_expenses",
        "denominator": "revenue",
        "formula": "(Total Expenses / Revenue) * 100",
        "unit": "%",
        "description": "Measures proportion of revenue spent on overall operating and non-operating costs.",
    },
}


def calculate_derived_metric(
    company: str,
    report: str,
    derived_metric_key: str,
    scope: str = "consolidated",
) -> dict:
    """Calculates a derived financial ratio given a company, report, and ratio key."""
    if derived_metric_key not in DERIVED_METRICS:
        return {
            "status": "ERROR",
            "message": f"Unknown ratio key: '{derived_metric_key}'",
        }

    config = DERIVED_METRICS[derived_metric_key]
    num_key = config["numerator"]
    den_key = config["denominator"]

    num_res = extract_financial_metric(company, report, num_key, scope=scope)
    den_res = extract_financial_metric(company, report, den_key, scope=scope)

    if num_res["status"] not in ("OK", "VERIFY"):
        return {
            "status": num_res["status"],
            "message": f"Numerator '{num_key}' could not be extracted ({num_res['status']}: {num_res.get('message', '')})",
        }

    if den_res["status"] not in ("OK", "VERIFY"):
        return {
            "status": den_res["status"],
            "message": f"Denominator '{den_key}' could not be extracted ({den_res['status']}: {den_res.get('message', '')})",
        }

    try:
        num_val = float(num_res["value_float"])
        den_val = float(den_res["value_float"])

        if den_val == 0:
            return {
                "status": "ERROR",
                "message": f"Denominator '{den_key}' is zero, cannot divide by zero.",
            }

        ratio_val = (num_val / den_val) * 100
        ratio_rounded = round(ratio_val, 2)

        conf = "HIGH"
        if num_res["confidence"] == "LOW" or den_res["confidence"] == "LOW":
            conf = "LOW"
        elif num_res["confidence"] == "MEDIUM" or den_res["confidence"] == "MEDIUM":
            conf = "MEDIUM"

        return {
            "status": "OK" if conf in ("HIGH", "MEDIUM") else "VERIFY",
            "company": company,
            "report": report,
            "ratio_key": derived_metric_key,
            "label": config["label"],
            "value": ratio_rounded,
            "unit": config["unit"],
            "confidence": conf,
            "scope": scope,
            "formula": config["formula"],
            "description": config["description"],
            "components": {
                "numerator": num_res,
                "denominator": den_res,
            },
        }
    except (ValueError, TypeError) as e:
        return {
            "status": "ERROR",
            "message": f"Error calculating ratio numeric values: {e}",
        }
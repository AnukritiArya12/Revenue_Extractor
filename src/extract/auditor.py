"""
Portfolio Integrity Auditor Module.
Scans ingested reports for line item inconsistencies, low confidence flags,
and business sanity warnings across all extracted metrics.
"""

import pandas as pd
from src.extract.engine import extract_financial_metric
from src.extract.metrics import METRICS
from src.extract.query import list_reports


def run_portfolio_audit() -> list:
    """
    Scans all ingested annual reports for extraction anomalies, low confidence values,
    and potential statement discrepancies.
    """
    findings = []

    try:
        available_reports = list_reports()
    except Exception as err:
        return [{
            "Company": "SYSTEM",
            "Report": "N/A",
            "Metric": "N/A",
            "Severity": "HIGH",
            "Issue": f"Failed to retrieve report list: {err}",
        }]

    for company, report in available_reports:
        for metric_key in METRICS.keys():
            try:
                res = extract_financial_metric(company, report, metric_key)

                status = res.get("status")
                confidence = res.get("confidence")
                val_float = res.get("value_float")

                # Check 1: Verification required status
                if status == "VERIFY":
                    findings.append({
                        "Company": company,
                        "Report": report,
                        "Metric": metric_key,
                        "Severity": "MEDIUM",
                        "Issue": f"Verification required: {res.get('message', 'Ambiguous candidate lines found')}",
                    })

                # Check 2: Low confidence extraction
                if confidence == "LOW" and status not in ("NOT_FOUND", "YEAR_NOT_IN_REPORT"):
                    findings.append({
                        "Company": company,
                        "Report": report,
                        "Metric": metric_key,
                        "Severity": "HIGH",
                        "Issue": "Extracted value has LOW confidence rating.",
                    })

                # Check 3: Unexpected negative top-line metric
                if metric_key in ("revenue", "total_income") and val_float is not None and val_float < 0:
                    findings.append({
                        "Company": company,
                        "Report": report,
                        "Metric": metric_key,
                        "Severity": "HIGH",
                        "Issue": f"Negative value detected for top-line metric ({val_float}).",
                    })

            except Exception as ex:
                findings.append({
                    "Company": company,
                    "Report": report,
                    "Metric": metric_key,
                    "Severity": "HIGH",
                    "Issue": f"Exception during extraction: {ex}",
                })

    return findings


if __name__ == "__main__":
    print("Running Portfolio Audit Scan...")
    results = run_portfolio_audit()
    print(f"Audit completed with {len(results)} item(s) flagged.")
    if results:
        df = pd.DataFrame(results)
        print(df.to_string())
import sys
import os

# Add project root directory to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import logging
import pandas as pd
from src.extract.engine import extract_financial_metric
from src.extract.query import list_reports
from src.observability.tracing import setup_tracing

logger = logging.getLogger("pipeline")


def run_pipeline(metric: str = "revenue", scope: str = "consolidated"):
    """
    Runs extraction across all ingested reports for a specified financial metric.
    """
    setup_tracing()
    print(f"\n--- Running Batch Extraction Pipeline for Metric: '{metric}' ({scope}) ---")
    
    try:
        reports = list_reports()
    except Exception as err:
        print(f"Error reading report list: {err}")
        return []

    if not reports:
        print("No ingested reports found in data/processed/chunks.jsonl.")
        print("Please run ingestion first: python -m src.ingest.run_ingest")
        return []

    results = []
    for company, report in reports:
        try:
            res = extract_financial_metric(company, report, metric, scope=scope)
            results.append(res)
            
            val = f"{res.get('value', 'N/A')} {res.get('unit', '')}".strip()
            status = res.get("status", "UNKNOWN")
            conf = res.get("confidence", "N/A")
            page = res.get("page", "N/A")
            
            print(f"[{company} - {report}] Status: {status} | Value: {val} | Conf: {conf} | Page: {page}")
        except Exception as e:
            print(f"[{company} - {report}] Error: {e}")

    out_dir = os.path.join("data", "processed")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"results_{metric}.csv")
    
    if results:
        df = pd.DataFrame(results)
        df.to_csv(out_path, index=False)
        print(f"\nPipeline finished. Saved summary CSV to: {out_path}\n")

    return results


if __name__ == "__main__":
    target_metric = sys.argv[1] if len(sys.argv) > 1 else "revenue"
    run_pipeline(metric=target_metric)
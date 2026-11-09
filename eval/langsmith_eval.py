"""LangSmith dataset upload + evaluate(), plus a local run that always works.

    python -m eval.langsmith_eval              # local metrics on eval/dataset.jsonl
    python -m eval.langsmith_eval --upload     # also create dataset + Client.evaluate
    python -m eval.langsmith_eval --all-reports  # local evidence audit for every ingested report
    python -m eval.langsmith_eval --all-reports --upload  # run that audit as a LangSmith experiment

Remote evaluate needs:
  - pip install langsmith
  - LANGSMITH_API_KEY
  - LANGSMITH_TRACING=true  (optional; evaluate() traces the target anyway)
  - ingested reports matching dataset company/report pairs

Follows langsmith Client.create_dataset / create_examples / Client.evaluate.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval.metrics_lib import (
    calibration_by_level,
    exact_match,
    normalized_numeric_match,
)
from src.extract.engine import extract_financial_metric
from src.extract.query import CHUNKS_PATH, label_year, list_reports
from src.observability.tracing import setup_tracing, tracing_enabled
from src.retrieve.bm25_search import ReportNotFound, _name_key, _report_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "eval" / "dataset.jsonl"
DATASET_NAME = "revenue-extractor-eval"
REMOTE_NOTE = (
    "LangSmith remote evaluate was not run. Need: (1) pip install langsmith  "
    "(2) environment variable LANGSMITH_API_KEY  "
    "(3) optionally LANGSMITH_PROJECT=revenue-extractor and LANGSMITH_TRACING=true. "
    "Do not put the key in source files."
)
_REPORTS = None


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_all_report_examples(reports: list[tuple[str, str]] | None = None) -> list[dict]:
    """Build one unlabeled audit example for each distinct ingested company/report pair."""
    reports = list_reports() if reports is None else reports
    examples, seen = [], set()
    for company, report in reports:
        key = (_name_key(company), _report_key(report))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        examples.append({
            "question": f"What was the latest consolidated revenue of {company} in {report}?",
            "company": company,
            "report": report,
            "metric": "revenue",
            "year": None,
            "scope": "consolidated",
            "evaluation_mode": "source_evidence_audit",
        })
    return examples


def run_one(example: dict) -> dict:
    setup_tracing()
    company, report, metric = example["company"], example["report"], example["metric"]
    year = example.get("year")
    scope = example.get("scope") or "consolidated"
    candidates = [(company, report)]
    try:
        r = extract_financial_metric(company, report, metric, year=year, scope=scope)
    except ReportNotFound as exc:
        global _REPORTS
        if _REPORTS is None:
            _REPORTS = list_reports()
        candidates = [(stored_company, stored_report) for stored_company, stored_report in _REPORTS
                      if _name_key(stored_company) == _name_key(company)]
        candidates.sort(key=lambda item: (label_year(item[1]) != year, -(label_year(item[1]) or 0)))
        r = None
        for stored_company, stored_report in candidates:
            try:
                candidate = extract_financial_metric(
                    stored_company, stored_report, metric, year=year, scope=scope
                )
            except ReportNotFound:
                continue
            if candidate.get("status") in ("OK", "VERIFY"):
                r = candidate
                break
            r = r or candidate
        if r is None:
            return {
                "status": "NO_REPORT",
                "value": None,
                "value_float": None,
                "unit": None,
                "year": None,
                "page": None,
                "confidence": "NONE",
                "evidence": [],
                "source_evidence_verified": False,
                "error": str(exc),
            }
    except Exception as exc:
        return {
            "status": "ERROR",
            "value": None,
            "value_float": None,
            "unit": None,
            "year": None,
            "page": None,
            "confidence": "NONE",
            "evidence": [],
            "source_evidence_verified": False,
            "error": str(exc),
        }
    return {
        "report": r.get("report"),
        "status": r.get("status"),
        "value": r.get("value"),
        "value_float": r.get("value_float"),
        "unit": r.get("unit"),
        "currency": r.get("currency"),
        "year": r.get("year"),
        "year_columns": r.get("year_columns") or [],
        "scope": r.get("scope"),
        "requested_scope": scope,
        "page": r.get("page"),
        "confidence": r.get("confidence"),
        "confidence_score": r.get("confidence_score"),
        "confidence_baseline": r.get("confidence_baseline"),
        "evidence": r.get("evidence") or [],
        "evidence_row": r.get("evidence_row"),
        "source_evidence_verified": bool(r.get("source_evidence_verified")),
        "warnings": r.get("warnings") or [],
        "label": r.get("label"),
        "error": r.get("message") or None,
    }


def report_run_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Score whether a report finished with a classified result, not its truth."""
    status = outputs.get("status")
    completed = status in ("OK", "VERIFY", "NOT_FOUND", "YEAR_NOT_IN_REPORT")
    return {
        "key": "report_run_completed",
        "score": float(completed),
        "comment": f"Extraction status: {status or 'missing'}",
    }


def source_evidence_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Check that an extracted value/label was verified in the displayed source row."""
    if outputs.get("status") not in ("OK", "VERIFY"):
        return {"key": "source_evidence_supported", "score": None,
                "comment": "No extracted revenue row to assess."}
    verified = bool(outputs.get("source_evidence_verified"))
    return {
        "key": "source_evidence_supported",
        "score": float(verified),
        "comment": "Exact label and numeric cell found in evidence row." if verified
        else "Selected label/value pair was not verified in the displayed evidence rows.",
    }


def statement_year_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Check internal year consistency; this does not compare with a gold label."""
    if outputs.get("status") not in ("OK", "VERIFY"):
        return {"key": "year_in_statement_columns", "score": None,
                "comment": "No extracted revenue row to assess."}
    year, columns = outputs.get("year"), outputs.get("year_columns") or []
    matches = year is not None and any(str(year) == str(item) for item in columns)
    return {"key": "year_in_statement_columns", "score": float(matches),
            "comment": f"Selected year {year}; statement columns: {columns}."}


def requested_scope_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Check that extraction honored the requested scope when it was identifiable."""
    if outputs.get("status") not in ("OK", "VERIFY"):
        return {"key": "requested_scope_match", "score": None,
                "comment": "No extracted revenue row to assess."}
    requested, found = outputs.get("requested_scope"), outputs.get("scope")
    match = found == requested and found in ("consolidated", "standalone")
    return {"key": "requested_scope_match", "score": float(match),
            "comment": f"Requested {requested}; extracted scope is {found}."}


def metadata_completeness_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    """Score presence of report citation and financial context, not numeric accuracy."""
    if outputs.get("status") not in ("OK", "VERIFY"):
        return {"key": "source_metadata_completeness", "score": None,
                "comment": "No extracted revenue row to assess."}
    fields = {
        "report": outputs.get("report"),
        "year": outputs.get("year"),
        "unit": outputs.get("unit"),
        "currency": outputs.get("currency"),
        "scope": outputs.get("scope"),
        "page": outputs.get("page"),
        "matched label": outputs.get("label"),
    }
    missing = [name for name, value in fields.items() if value in (None, "", "unknown")]
    score = (len(fields) - len(missing)) / len(fields)
    return {"key": "source_metadata_completeness", "score": score,
            "comment": "Complete source context." if not missing
            else "Missing or unknown: " + ", ".join(missing)}


ALL_REPORT_EVALUATORS = [
    report_run_evaluator,
    source_evidence_evaluator,
    statement_year_evaluator,
    requested_scope_evaluator,
    metadata_completeness_evaluator,
]


def print_all_report_audit(examples: list[dict], preds: list[dict]) -> None:
    """Print a no-ground-truth audit summary without writing local output files."""
    completed = sum(report_run_evaluator(p, {})["score"] == 1 for p in preds)
    found = [p for p in preds if p.get("status") in ("OK", "VERIFY")]
    evidence_ok = sum(bool(p.get("source_evidence_verified")) for p in found)
    scope_ok = sum(requested_scope_evaluator(p, {})["score"] == 1 for p in found)
    metadata = [metadata_completeness_evaluator(p, {})["score"] for p in found]
    metadata = [score for score in metadata if score is not None]
    print(f"\nAll-report audit: {len(examples)} distinct ingested reports")
    print(f"Completed extraction runs: {completed}/{len(examples)}")
    print(f"Revenue row returned: {len(found)}/{len(examples)}")
    print(f"Exact source evidence verified: {evidence_ok}/{len(found)} extracted rows")
    print(f"Requested consolidated scope matched: {scope_ok}/{len(found)} extracted rows")
    print(f"Average source metadata completeness: "
          f"{sum(metadata) / len(metadata):.0%}" if metadata else "Average source metadata completeness: n/a")
    print("This is a source/evidence audit, not revenue accuracy: no independent expected values were supplied.")
    print(f"\n{'company':<22}{'report':<10}{'value':<18}{'unit':<10}{'year':<8}"
          f"{'scope':<14}{'status':<18}evidence")
    for ex, pred in zip(examples, preds):
        print(f"{ex['company']:<22}{ex['report']:<10}{str(pred.get('value') or '-'):<18}"
              f"{str(pred.get('unit') or '-'):<10}{str(pred.get('year') or '-'):<8}"
              f"{str(pred.get('scope') or '-'):<14}{str(pred.get('status') or '-'):<18}"
              f"{'VERIFIED' if pred.get('source_evidence_verified') else 'NO'}")


def upload_all_report_audit(examples: list[dict]) -> None:
    """Create an unlabeled LangSmith dataset and run source-quality evaluators."""
    try:
        from langsmith import Client
    except ImportError:
        print(REMOTE_NOTE)
        return
    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        print(REMOTE_NOTE)
        return
    setup_tracing()
    client = Client()
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dataset_name = f"{DATASET_NAME}-all-report-audit-{run_stamp}"
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description=("Automatically generated from ingested reports. Unlabeled source/evidence "
                     "audit only; this dataset does not measure reference-based revenue accuracy."),
    )
    payload = [{
        "inputs": {
            "question": ex["question"],
            "company": ex["company"],
            "report": ex["report"],
            "metric": ex["metric"],
            "year": ex["year"],
            "scope": ex["scope"],
        },
        "outputs": {},
    } for ex in examples]
    client.create_examples(dataset_id=dataset.id, examples=payload)
    print(f"Created unlabeled LangSmith audit dataset '{dataset_name}' with {len(payload)} reports.")
    results = client.evaluate(
        target,
        data=dataset.id,
        evaluators=ALL_REPORT_EVALUATORS,
        experiment_prefix=f"revenue-all-report-audit-{run_stamp}",
        max_concurrency=1,
    )
    print(f"LangSmith evaluated {len(payload)} report examples with source/evidence evaluators.")
    print("These scores are not reference-based revenue accuracy.")
    print("LangSmith experiment:", results)
    url = getattr(results, "experiment_url", None) or getattr(results, "url", None)
    if url:
        print("View:", url)


def evaluate_all_reports(upload: bool = False) -> None:
    reports = list_reports()
    examples = build_all_report_examples(reports)
    print(f"Ingested report pairs: {len(reports)}; unique normalized reports: {len(examples)}")
    if upload:
        upload_all_report_audit(examples)
        return
    preds = [run_one(example) for example in examples]
    print_all_report_audit(examples, preds)


def score_example(example: dict, pred: dict) -> dict:
    none_expected = bool(example.get("none_expected"))
    found = pred.get("status") in ("OK", "VERIFY")
    if pred.get("status") == "NO_REPORT":
        numeric_ok = exact_ok = None
    elif none_expected:
        numeric_ok = exact_ok = (not found)
    elif not found:
        numeric_ok = exact_ok = False
    else:
        numeric_ok = normalized_numeric_match(
            pred.get("value_float"), pred.get("unit"),
            example.get("expected_value"), example.get("unit"),
        )
        exact_ok = exact_match(pred.get("value"), example.get("expected_value"))
    year_ok = None
    if example.get("year") and pred.get("year"):
        year_ok = int(pred["year"]) == int(example["year"])
    unit_ok = None
    if example.get("unit") and pred.get("unit"):
        unit_ok = str(pred["unit"]).lower() == str(example["unit"]).lower()
    return {
        "numeric_ok": numeric_ok,
        "exact_ok": exact_ok,
        "year_ok": year_ok,
        "unit_ok": unit_ok,
        "correct": numeric_ok,
    }


def print_local(examples: list[dict], preds: list[dict], scores: list[dict]) -> None:
    print(f"{'company':<16}{'requested':<11}{'used report':<13}{'metric':<10}"
          f"{'expected':<14}{'system':<14}{'num':<8}{'exact':<8}{'conf':<8}status")
    cal_rows = []
    n_num = n_ok = n_exact = n_exact_ok = 0
    skipped = 0
    for ex, pred, sc in zip(examples, preds, scores):
        note = pred.get("status") or ""
        if pred.get("status") == "NO_REPORT":
            skipped += 1
            note = "NO_REPORT"
        exp = "NONE" if ex.get("none_expected") else ex.get("expected_value")
        print(f"{ex['company']:<16}{str(ex.get('report') or '-'):<11}"
              f"{str(pred.get('report') or '-'):<13}{ex['metric']:<10}{str(exp):<14}"
              f"{str(pred.get('value') or '-'):<14}{str(sc['numeric_ok']):<8}"
              f"{str(sc['exact_ok']):<8}{str(pred.get('confidence') or '-'):<8}{note}")
        if sc["numeric_ok"] is None:
            continue
        n_num += 1
        n_ok += int(sc["numeric_ok"])
        n_exact += 1
        n_exact_ok += int(bool(sc["exact_ok"]))
        cal_rows.append({"confidence": pred.get("confidence"), "correct": sc["numeric_ok"]})
    print(f"\nNormalized numeric accuracy: {n_ok}/{n_num}" if n_num else "\nNormalized numeric accuracy: no scored rows")
    print(f"Exact match: {n_exact_ok}/{n_exact}" if n_exact else "Exact match: no scored rows")
    print(f"Skipped (report not ingested): {skipped}/{len(examples)}")
    for ex, pred in zip(examples, preds):
        if pred.get("status") == "NO_REPORT":
            print(f"  Unscored: {ex['company']} {ex.get('report')} — {pred.get('error')}")
    cal = calibration_by_level(cal_rows, min_n=8)
    print("Confidence calibration (accuracy by HIGH/MEDIUM/LOW; not a probability):")
    print(f"  labeled={cal['n_labeled']} enough_data={cal['enough_data']} "
          f"insufficient_variation={cal['insufficient_variation']}")
    for level, info in cal["by_level"].items():
        print(f"  {level:<8} n={info['n']}  accuracy={info['accuracy']:.2f}")
    if cal["high_at_least_as_accurate_as_lower"] is not None:
        print(f"  HIGH >= lower levels: {cal['high_at_least_as_accurate_as_lower']}")
    print("Rows above are hand-checked examples from the evaluation dataset; this is not the 50-report batch run.")


def _unscored_result(key: str, outputs: dict) -> dict | None:
    """Missing reports and runtime errors are not correctness judgments."""
    status = outputs.get("status")
    if status not in ("NO_REPORT", "ERROR"):
        return None
    return {
        "key": key,
        "score": None,
        "comment": f"Skipped: prediction status is {status}",
    }

def numeric_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    unscored = _unscored_result("normalized_numeric_accuracy", outputs)
    if unscored:
        return unscored
    none_expected = bool(reference_outputs.get("none_expected"))
    if none_expected:
        score = 1.0 if outputs.get("status") not in ("OK", "VERIFY") else 0.0
    else:
        score = float(normalized_numeric_match(
            outputs.get("value_float"), outputs.get("unit"),
            reference_outputs.get("expected_value"), reference_outputs.get("unit"),
        ))
    return {"key": "normalized_numeric_accuracy", "score": score}


def exact_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    unscored = _unscored_result("exact_match", outputs)
    if unscored:
        return unscored
    none_expected = bool(reference_outputs.get("none_expected"))
    if none_expected:
        score = 1.0 if outputs.get("status") not in ("OK", "VERIFY") else 0.0
    else:
        score = float(exact_match(outputs.get("value"), reference_outputs.get("expected_value")))
    return {"key": "exact_match", "score": score}


def year_evaluator(outputs: dict, reference_outputs: dict) -> dict:
    exp = reference_outputs.get("year")
    got = outputs.get("year")
    if not exp or not got:
        return {"key": "year_match", "score": None, "comment": "year missing on one side"}
    return {"key": "year_match", "score": float(int(got) == int(exp))}


def target(inputs: dict) -> dict:
    return run_one(inputs)


def upload_and_evaluate(examples: list[dict]) -> None:
    try:
        from langsmith import Client
    except ImportError:
        print(REMOTE_NOTE)
        return
    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        print(REMOTE_NOTE)
        return
    setup_tracing()
    client = Client()
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dataset_name = f"{DATASET_NAME}-{run_stamp}"
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Revenue extraction: question, expected value, year, unit, evidence",
    )
    payload = []
    for ex in examples:
        payload.append({
            "inputs": {
                "question": ex.get("question"),
                "company": ex["company"],
                "report": ex["report"],
                "metric": ex["metric"],
                "year": ex.get("year"),
                "scope": ex.get("scope") or "consolidated",
            },
            "outputs": {
                "expected_value": ex.get("expected_value"),
                "unit": ex.get("unit"),
                "currency": ex.get("currency"),
                "year": ex.get("year"),
                "expected_page": ex.get("expected_page"),
                "evidence": ex.get("evidence"),
                "none_expected": ex.get("none_expected", False),
            },
        })
    client.create_examples(dataset_id=dataset.id, examples=payload)
    print(f"Created clean LangSmith dataset '{dataset_name}' with {len(payload)} examples.")
    results = client.evaluate(
        target,
        data=dataset.id,
        evaluators=[numeric_evaluator, exact_evaluator, year_evaluator],
        experiment_prefix=f"revenue-extractor-{run_stamp}",
        max_concurrency=1,
    )
    print(f"LangSmith ran this dataset only: {len(payload)} examples; "
          f"{sum(not ex.get('none_expected') for ex in examples)} expect numeric values.")
    print("LangSmith experiment:", results)
    url = getattr(results, "experiment_url", None) or getattr(results, "url", None)
    if url:
        print("View:", url)


def main():
    reports = list_reports()
    if "--all-reports" in sys.argv:
        print(f"Project root: {PROJECT_ROOT}")
        print(f"Ingested report index: {CHUNKS_PATH} ({len(reports)} raw report pairs)")
        evaluate_all_reports(upload="--upload" in sys.argv)
        print(f"\nLangSmith tracing active: {tracing_enabled()}")
        if tracing_enabled():
            print("Traces go to project:", os.environ.get("LANGSMITH_PROJECT", "revenue-extractor"))
        return

    examples = load_dataset()
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Reference dataset: {DATASET_PATH} ({len(examples)} examples)")
    print(f"Ingested report index: {CHUNKS_PATH} ({len(reports)} reports)")
    preds = [run_one(ex) for ex in examples]
    scores = [score_example(ex, pred) for ex, pred in zip(examples, preds)]
    print_local(examples, preds, scores)
    print(f"\nLangSmith tracing active: {tracing_enabled()}")
    if tracing_enabled():
        print("Traces go to project:", os.environ.get("LANGSMITH_PROJECT", "revenue-extractor"))
    else:
        print(REMOTE_NOTE)
    if "--upload" in sys.argv:
        upload_and_evaluate(examples)


if __name__ == "__main__":
    main()

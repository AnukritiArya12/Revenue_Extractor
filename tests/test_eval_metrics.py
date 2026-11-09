"""Run:  python -m tests.test_eval_metrics

Unit checks for confidence quality index and local eval metrics.
Does not need ingested reports.
"""
from src.extract.confidence import build_signals, finalize_confidence, quality_index
from eval.metrics_lib import (
    calibration_by_level,
    exact_match,
    normalized_numeric_match,
    recall_at_k,
)


failures = []


def check(name, got, expected):
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name:<70} {got!s}" + ("" if ok else f" expected {expected}"))
    if not ok:
        failures.append(name)


def main():
    def evidence_signal(token, evidence_row):
        signals = build_signals(
            tier=0,
            evidence=["» Revenue from operations | 50,000 | 45,000"],
            evidence_row=evidence_row,
            token=token,
            year_used=2026,
            year_status="ok",
            report_year_match=True,
            unit="crore",
            currency="INR",
            section="income_statement",
            page_in_retrieved=True,
            n_rows=3,
            is_total=False,
            identity_status=None,
            confirm_pages=[],
            n_rivals=0,
            problems=[],
        )
        return signals

    exact_evidence = evidence_signal("50,000", "Revenue from operations 50,000")
    check("exact source value and label count as evidence",
          exact_evidence["evidence_quality"], 1.0)
    check("exact source row is marked verified", exact_evidence["source_evidence_verified"], True)
    substring_evidence = evidence_signal("50", "Revenue from operations 50")
    check("numeric substring without an exact source cell is not evidence",
          substring_evidence["evidence_quality"], 0.45)
    check("missing exact source row is marked unverified",
          substring_evidence["source_evidence_verified"], False)

    check("crore vs printed digits (normalized)",
          normalized_numeric_match(267022, "crore", "2,67,022", "crore"), True)
    check("million vs crore is not a match",
          normalized_numeric_match(267022, "million", 267022, "crore"), False)
    check("exact match ignores commas", exact_match("2,67,022", "267022"), True)
    check("exact match none vs not found", exact_match("NOT FOUND", "NONE"), True)
    check("recall@1", recall_at_k([[True, False], [False, True]], 1), 0.5)
    check("recall@5", recall_at_k([[True, False], [False, True]], 5), 1.0)

    cal = calibration_by_level(
        [{"confidence": "HIGH", "correct": True}] * 6
        + [{"confidence": "LOW", "correct": False}] * 4,
        min_n=8,
    )
    check("calibration enough_data", cal["enough_data"], True)
    check("calibration variation present", cal["insufficient_variation"], False)
    check("HIGH at least as accurate as LOW", cal["high_at_least_as_accurate_as_lower"], True)

    thin = calibration_by_level([{"confidence": "HIGH", "correct": True}] * 3, min_n=8)
    check("thin sample not enough_data", thin["enough_data"], False)
    check("single level is insufficient_variation", thin["insufficient_variation"], True)

    signals = {
        "evidence_quality": 1.0,
        "metric_year_match": 1.0,
        "currency_unit": 1.0,
        "source_page": 1.0,
        "extraction_reliability": 1.0,
        "supporting_evidence": 1.0,
        "no_conflict": 1.0,
        "rerank_score": 0.99,
    }
    check("quality_index ignores unknown rerank_score", quality_index(signals), 1.0)

    result = {"confidence": "HIGH", "warnings": [], "reasons": []}
    finalize_confidence(result, points=9, caps=[], problems=[], signals=dict(signals))
    check("finalize drops rerank_score from stored signals",
          "rerank_score" not in result["confidence_signals"], True)
    check("quality index is not a probability flag in reasons",
          any("not a probability" in x for x in result["reasons"]), True)
    check("HIGH quality + HIGH points stays HIGH", result["confidence"], "HIGH")

    mixed = {"confidence": "HIGH", "warnings": [], "reasons": []}
    weak = {k: 0.2 for k in (
        "evidence_quality", "metric_year_match", "currency_unit", "source_page",
        "extraction_reliability", "supporting_evidence", "no_conflict",
    )}
    finalize_confidence(mixed, points=9, caps=[], problems=[], signals=weak)
    check("conservative grade uses the lower of points vs quality", mixed["confidence"], "LOW")


if __name__ == "__main__":
    main()
    print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
    raise SystemExit(1 if failures else 0)

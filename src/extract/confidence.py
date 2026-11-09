"""Evidence-based confidence for extracted financial metrics.

The 0-1 `confidence_score` is a quality index from independent signals.
It is NOT a probability, and Cross-Encoder / rerank scores are never used here.
"""
from __future__ import annotations

LEVELS = ["NONE", "LOW", "MEDIUM", "HIGH"]

# Weights sum to 1.0. conflict is encoded as no_conflict in [0, 1].
WEIGHTS = {
    "evidence_quality": 0.18,
    "metric_year_match": 0.18,
    "currency_unit": 0.16,
    "source_page": 0.14,
    "extraction_reliability": 0.12,
    "supporting_evidence": 0.12,
    "no_conflict": 0.10,
}


def grade_points(points: int) -> str:
    """Original point-table grade (baseline)."""
    return "HIGH" if points >= 8 else "MEDIUM" if points >= 5 else "LOW"


def grade_quality(index: float) -> str:
    if index >= 0.72:
        return "HIGH"
    if index >= 0.48:
        return "MEDIUM"
    return "LOW"


def more_conservative(a: str, b: str) -> str:
    order = {name: i for i, name in enumerate(LEVELS)}
    return LEVELS[min(order.get(a, 0), order.get(b, 0))]


def quality_index(signals: dict) -> float:
    """Weighted quality index in [0, 1]. Unknown keys (including rerank_score) are ignored."""
    total = 0.0
    for name, weight in WEIGHTS.items():
        raw = signals.get(name, 0.0)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        total += weight * max(0.0, min(1.0, value))
    return round(total, 4)


def build_signals(
    *,
    tier: int | None,
    evidence: list,
    evidence_row: str | None,
    token: str | None,
    year_used: int | None,
    year_status: str | None,
    report_year_match: bool,
    unit: str | None,
    currency: str | None,
    section: str | None,
    page_in_retrieved: bool,
    n_rows: int,
    is_total: bool,
    identity_status: str | None,
    confirm_pages: list,
    n_rivals: int,
    problems: list,
) -> dict:
    evidence_lines = [str(x) for x in (evidence or [])]
    token_s = str(token or "")
    row_s = str(evidence_row or "")
    # Count evidence only when the displayed source row contains this exact
    # label and numeric cell. `evidence_row` is synthesized by the extractor,
    # so searching it would make the check pass even if the citation omitted
    # the selected value. Exact cells also prevent e.g. "50" matching "50,000".
    selected_row_present = False
    if token_s and row_s.endswith(token_s):
        expected_label = row_s[:-len(token_s)].rstrip()
        for line in evidence_lines:
            label, separator, cells = line.partition("|")
            actual_label = label.removeprefix("»").strip()
            numeric_cells = [cell.strip() for cell in cells.split("|")] if separator else []
            if actual_label == expected_label and token_s in numeric_cells:
                selected_row_present = True
                break
    token_in_evidence = selected_row_present
    label_in_evidence = selected_row_present
    tier_score = {0: 1.0, 1: 0.75, 2: 0.55}.get(tier if tier is not None else 99, 0.4)
    evidence_quality = min(1.0, 0.45 * tier_score + 0.35 * float(token_in_evidence) + 0.20 * float(label_in_evidence))

    if year_status == "ok" and year_used:
        metric_year_match = 1.0 if report_year_match else 0.75
    elif year_used:
        metric_year_match = 0.45
    else:
        metric_year_match = 0.15

    if unit and currency:
        currency_unit = 1.0
    elif unit or currency:
        currency_unit = 0.55
    else:
        currency_unit = 0.1

    if section == "income_statement":
        source_page = 1.0 if page_in_retrieved else 0.85
    elif section == "highlights":
        source_page = 0.65
    else:
        source_page = 0.35

    extraction_reliability = 0.2
    if token_s:
        extraction_reliability += 0.35
    if n_rows >= 3:
        extraction_reliability += 0.25
    if year_status == "ok":
        extraction_reliability += 0.2
    extraction_reliability = min(1.0, extraction_reliability)

    supporting = 0.2
    if is_total:
        supporting += 0.25
    if identity_status == "verified":
        supporting += 0.4
    elif identity_status == "not_reconciled":
        supporting += 0.0
    if confirm_pages:
        supporting += 0.25
    supporting_evidence = min(1.0, supporting)

    if problems or n_rivals:
        no_conflict = 0.0 if problems else 0.25
    else:
        no_conflict = 1.0

    return {
        "evidence_quality": round(evidence_quality, 4),
        "source_evidence_verified": selected_row_present,
        "metric_year_match": round(metric_year_match, 4),
        "currency_unit": round(currency_unit, 4),
        "source_page": round(source_page, 4),
        "extraction_reliability": round(extraction_reliability, 4),
        "supporting_evidence": round(supporting_evidence, 4),
        "no_conflict": round(no_conflict, 4),
        "n_rivals": n_rivals,
        "page_in_retrieved": page_in_retrieved,
        "identity_status": identity_status,
    }


def cap_level(result: dict, level: str, why: str) -> None:
    result.setdefault("warnings", []).append(why)
    if LEVELS.index(result["confidence"]) > LEVELS.index(level):
        result["confidence"] = level
        result.setdefault("reasons", []).append(f"cap {level}: {why}")


def finalize_confidence(
    result: dict,
    *,
    points: int,
    caps: list[str],
    problems: list[str],
    signals: dict,
) -> dict:
    """Set confidence, quality index, and baseline grade. Never uses rerank scores."""
    if "rerank_score" in signals or "raw_rerank_score" in signals:
        signals = {k: v for k, v in signals.items() if k not in ("rerank_score", "raw_rerank_score")}

    index = quality_index(signals)
    baseline = grade_points(points)
    quality = grade_quality(index)
    result["confidence_signals"] = signals
    result["confidence_score"] = index
    result["confidence_baseline"] = baseline
    result["confidence"] = more_conservative(baseline, quality)
    result["score"] = points
    result.setdefault("warnings", [])
    result.setdefault("reasons", [])
    result["reasons"].append(
        f"+0 quality index {index:.2f} ({quality}); baseline points {points} ({baseline}); "
        "using the more conservative of the two (not a probability, not a rerank score)"
    )
    for why in caps:
        cap_level(result, "MEDIUM", why)
    if problems:
        result["confidence"] = "LOW"
        result["needs_verification"] = True
        result["message"] = "verification required: " + "; ".join(problems)
        result["warnings"].extend(problems)
    return result

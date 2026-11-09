"""Local evaluation metrics: numeric accuracy, exact match, Recall@K, calibration.

These do not call LangSmith. Cross-encoder scores are not treated as probabilities.
"""
from __future__ import annotations

import re
from collections import defaultdict

UNIT_FACTOR = {
    "thousand": 1e3, "lakh": 1e5, "million": 1e6, "crore": 1e7, "billion": 1e9,
}
UNIT_WORDS = {
    "cr": "crore", "crores": "crore", "lakhs": "lakh", "millions": "million",
    "mn": "million", "billions": "billion", "bn": "billion", "thousands": "thousand",
}
EXTRA_FACTOR = {"lakh crore": 1e12, "lakh crores": 1e12, "trillion": 1e12}

NONE_WORDS = {"none", "not found", "notfound", "n/a", "na"}


def parse_number(text) -> float | None:
    t = str(text or "").strip()
    digits = re.sub(r"[^\d.]", "", t)
    if not digits:
        return None
    try:
        v = float(digits)
    except ValueError:
        return None
    return -v if t.startswith("(") or t.startswith("-") else v


def unit_factor(unit: str | None) -> float | None:
    u = " ".join((unit or "").lower().split())
    u = UNIT_WORDS.get(u, u)
    return EXTRA_FACTOR.get(u) or UNIT_FACTOR.get(u)


def relative_error(pred, pred_unit, expected, expected_unit) -> float | None:
    pred_n, exp_n = parse_number(pred), parse_number(expected)
    if pred_n is None or exp_n is None:
        return None
    fp, fe = unit_factor(pred_unit), unit_factor(expected_unit)
    if fp and fe:
        pred_n, exp_n = pred_n * fp, exp_n * fe
    denom = max(abs(exp_n), 1.0)
    return abs(pred_n - exp_n) / denom


def normalized_numeric_match(pred, pred_unit, expected, expected_unit, tol: float = 5e-5) -> bool:
    rel = relative_error(pred, pred_unit, expected, expected_unit)
    return rel is not None and rel <= tol


def exact_match(pred, expected) -> bool:
    """Digit-level exact match of the printed number (commas/spaces ignored)."""
    a, b = parse_number(pred), parse_number(expected)
    if a is None or b is None:
        pa = str(pred or "").strip().lower()
        ea = str(expected or "").strip().lower()
        if ea in NONE_WORDS:
            return pa in NONE_WORDS or pa in ("", "not_found")
        return False
    return abs(a - b) < 1e-9


def recall_at_k(ranked_hit_lists: list[list[bool]], k: int) -> float:
    if not ranked_hit_lists:
        return 0.0
    hits = sum(1 for ranked in ranked_hit_lists if any(ranked[:k]))
    return hits / len(ranked_hit_lists)


def calibration_by_level(rows: list[dict], min_n: int = 8) -> dict:
    """Accuracy by ordinal confidence level. Not a probabilistic ECE.

    Requires enough labeled rows. If there is no variation across levels,
    reports insufficient_variation instead of a fake calibration score.
    """
    by = defaultdict(list)
    for r in rows:
        if r.get("correct") is None:
            continue
        by[str(r.get("confidence") or "NONE")].append(bool(r["correct"]))
    labeled = sum(len(v) for v in by.values())
    table = {}
    for level in ("HIGH", "MEDIUM", "LOW", "NONE"):
        vals = by.get(level, [])
        if vals:
            table[level] = {"n": len(vals), "accuracy": sum(vals) / len(vals)}
    levels_with_n = [lv for lv, info in table.items() if info["n"] > 0]
    monotonic = True
    accs = [table[lv]["accuracy"] for lv in ("HIGH", "MEDIUM", "LOW") if lv in table]
    for i in range(len(accs) - 1):
        if accs[i] + 1e-9 < accs[i + 1]:
            monotonic = False
            break
    return {
        "n_labeled": labeled,
        "enough_data": labeled >= min_n,
        "insufficient_variation": len(levels_with_n) < 2,
        "by_level": table,
        "high_at_least_as_accurate_as_lower": monotonic if len(accs) >= 2 else None,
    }

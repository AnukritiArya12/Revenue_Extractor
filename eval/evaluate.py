"""Compare the system's answers with an answer file (written by a person) and report accuracy.

    python -m eval.evaluate                  # uses data/ground_truth.csv
    python -m eval.evaluate answers.csv      # any answer file, e.g. from a financial portal
    python -m eval.evaluate --search         # also test the search step: plain BM25 vs the improved search

Columns of the answer file (names are detected automatically):
    company | name                       required
    value | total_revenue | answer       required: the correct value. NONE = the system must say NOT FOUND
    year | fy | report                   optional: the report, e.g. FY26
    metric | item                        optional: default revenue ("profit after tax", "net_loss" also work)
    unit                                 optional: lets the file use another unit (crore, lakh crore, million, billion ...)
    scope                                optional: consolidated (default) or standalone

Printed: one line per answer, then accuracy overall / by metric / by confidence level, and the failures.
Saved:   data/processed/evaluation_report.csv
Result words: MATCH | CLOSE (under 0.5%, not counted) | DIFFERENT | NOT FOUND | no report in the system
"""
import csv
import json
import re
import sys
from collections import defaultdict

from eval.metrics_lib import calibration_by_level, exact_match, recall_at_k
from src.extract.engine import UNIT_FACTOR, extract_financial_metric, retrieve
from src.extract.metrics import METRICS
from src.extract.query import find_metric, label_year, list_reports
from src.retrieve.bm25_search import ReportNotFound, search

COLUMNS = {"company": ["company", "name", "company_name"],
           "year": ["year", "fy", "fiscal_year", "financial_year", "report"],
           "value": ["value", "expected_value", "total_revenue", "answer", "sales", "revenue"],
           "metric": ["metric", "item"], "unit": ["unit", "units"], "scope": ["scope"],
           "currency": ["currency", "currency_code"]}
NONE_WORDS = {"none", "not found", "notfound", "n/a", "na"}
SUFFIX = {"limited", "ltd", "incorporated", "inc", "corporation", "corp", "plc", "company", "co"}
UNIT_WORDS = {"cr": "crore", "crores": "crore", "lakhs": "lakh", "millions": "million", "mn": "million",
              "billions": "billion", "bn": "billion", "thousands": "thousand"}
EXTRA_FACTOR = {"lakh crore": 1e12, "lakh crores": 1e12, "trillion": 1e12}


def column(header, wanted):
    low = {h.lower().strip(): h for h in header}
    return next((low[n] for n in COLUMNS[wanted] if n in low), None)


def norm_name(s):
    return "".join(w for w in re.sub(r"[^a-z0-9 ]", "", s.lower()).split() if w not in SUFFIX)


def same_company(a, b):
    a, b = norm_name(a), norm_name(b)
    if not (a and b):
        return False
    if a == b:
        return True
    # Share the ingestion/retrieval aliases; prefix matching alone can confuse
    # distinct company names (for example, similarly named subsidiaries).
    from src.retrieve.bm25_search import _name_key
    return _name_key(a) == _name_key(b)


def number(text):
    """'2,67,022' -> 267022.0 ; '(5,000)' and '-5,000' -> -5000.0 ; anything else -> None"""
    t = str(text or "").strip()
    digits = re.sub(r"[^\d.]", "", t)
    if not digits:
        return None
    try:
        v = float(digits)
    except ValueError:
        return None
    return -v if t.startswith("(") or t.startswith("-") else v


def factor(unit):
    u = " ".join((unit or "").lower().split())
    u = UNIT_WORDS.get(u, u)
    return EXTRA_FACTOR.get(u) or UNIT_FACTOR.get(u)


def compare(answer, answer_unit, value, unit):
    fa, fs = factor(answer_unit), factor(unit)
    if fa and fs:
        rel = abs(answer * fa - value * fs) / max(abs(answer * fa), 1)
        return "MATCH" if rel <= 5e-5 else f"CLOSE ({rel * 100:.2f}% off)" if rel <= 5e-3 else f"DIFFERENT ({rel * 100:.1f}% off)"
    return "MATCH" if abs(answer - value) < 1e-6 else "DIFFERENT (no unit in the file: compared as printed)"


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def print_report(records):
    scored = [r for r in records if r["correct"] is not None]
    ok = sum(r["correct"] for r in scored)
    print(f"\nACCURACY  {ok}/{len(scored)} correct ({pct(ok, len(scored))})")
    print("\nBy metric")
    by_metric = defaultdict(list)
    for r in scored:
        by_metric[r["metric"]].append(r["correct"])
    for m, v in sorted(by_metric.items()):
        print(f"  {m:<24}{sum(v)}/{len(v)}  {pct(sum(v), len(v))}")
    print("\nBy confidence  (a useful HIGH should be more accurate than MEDIUM and LOW)")
    by_conf = defaultdict(list)
    for r in scored:
        by_conf[r["confidence"]].append(r["correct"])
    for level in ("HIGH", "MEDIUM", "LOW", "NONE"):
        if level in by_conf:
            v = by_conf[level]
            print(f"  {level:<10}{sum(v)}/{len(v)} correct  ({pct(sum(v), len(v))})")
    flagged = [r for r in scored if r["status"] == "VERIFY"]
    print(f"\nFlagged VERIFY: {len(flagged)}   (of which correct anyway: {sum(r['correct'] for r in flagged)})")
    cal = calibration_by_level(scored, min_n=8)
    print("\nConfidence calibration (accuracy by level; quality index is not a probability)")
    print(f"  labeled={cal['n_labeled']} enough_data={cal['enough_data']} "
          f"insufficient_variation={cal['insufficient_variation']}")
    for level, info in cal["by_level"].items():
        print(f"  {level:<10}n={info['n']}  accuracy={info['accuracy']:.0%}")
    if cal["high_at_least_as_accurate_as_lower"] is not None:
        print(f"  HIGH is at least as accurate as lower levels: {cal['high_at_least_as_accurate_as_lower']}")
    else:
        print("  not enough level variation to judge calibration")
    failures = [r for r in scored if not r["correct"]]
    if failures:
        print("\nFAILURES")
        for r in failures:
            print(f"  {r['company']} {r['metric']}: answer {r['expected']}, system {r['system'] or 'NOT FOUND'} "
                  f"(page {r['page']}, line '{r['label']}', {r['confidence']}) -> {r['result']}")
            if r["warnings"]:
                print(f"      why: {r['warnings']}")


def search_test(checks):
    """Does the search put a chunk with the right number in the top 5? plain BM25 vs the improved search."""
    print("\nSearch step (is the right number in the top-5 chunks?)  Recall@K")
    print(f"{'setup':<44}{'Recall@1':<12}{'Recall@5':<12}")
    setups = (("BM25 plain", lambda c, rep, m: search(c, rep, " ".join(a for t in METRICS[m]["aliases"] for a in t), k=5)),
              ("hybrid retrieve (BM25 + optional semantic/rerank)", lambda c, rep, m: retrieve(c, rep, METRICS[m], k=5)))
    for name, run in setups:
        ranked_hits = []
        for company, report, metric, raw in checks:
            pat = re.compile(r"(?<![\d.])" + re.escape(re.sub(r"[^\d.]", "", raw)) + r"(?!\d)")
            hits = [bool(pat.search(c["text"].replace(",", ""))) for _, c in run(company, report, metric)]
            ranked_hits.append(hits)
        r1 = recall_at_k(ranked_hits, 1)
        r5 = recall_at_k(ranked_hits, 5)
        n = len(checks)
        print(f"{name:<44}{r1 * n:.0f}/{n} ({r1:.0%})  {r5 * n:.0f}/{n} ({r5:.0%})")

        
def run_all_reports():
    import csv
    import os

    output_path = "data/processed/all_reports_revenue.csv"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    reports = list_reports()
    rows = []

    for company, report in reports:
        print(f"Extracting revenue: {company} - {report}")

        try:
            result = extract_financial_metric(
                company, report, "revenue"
            )

            rows.append({
                "company": company,
                "report": report,
                "metric": "revenue",
                "status": result.get("status", ""),
                "value": result.get("value", ""),
                "value_float": result.get("value_float", ""),
                "unit": result.get("unit", ""),
                "currency": result.get("currency", ""),
                "year": result.get("year", ""),
                "scope": result.get("scope", ""),
                "page": result.get("page", ""),
                "label": result.get("label", ""),
                "evidence": " || ".join(
                    result.get("evidence", []) or []
                ),
                "evidence_row": result.get("evidence_row", ""),
                "confidence": result.get("confidence", ""),
                "needs_verification": result.get(
                    "needs_verification", ""
                ),
                "message": result.get("message", ""),
                "reasons": " | ".join(
                    result.get("reasons", []) or []
                ),
            })

        except Exception as error:
            rows.append({
                "company": company,
                "report": report,
                "metric": "revenue",
                "status": "ERROR",
                "message": str(error),
            })

    columns = [
        "company", "report", "metric", "status",
        "value", "value_float", "unit", "currency",
        "year", "scope", "page", "label", "evidence",
        "evidence_row", "confidence", "needs_verification",
        "message", "reasons"
    ]

    with open(output_path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    successful = sum(1 for row in rows if row["status"] == "OK")
    print(f"\nTotal reports: {len(reports)}")
    print(f"Successful extractions: {successful}")
    print(f"Other results/errors: {len(rows) - successful}")
    print(f"Saved to: {output_path}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "data/ground_truth.csv"
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            if path.lower().endswith(".jsonl"):
                rows = [json.loads(line) for line in f if line.strip()]
                header = list(rows[0]) if rows else []
            else:
                reader = csv.DictReader(f)
                header = reader.fieldnames or []
                rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    except FileNotFoundError:
        sys.exit(f"Answer file not found: {path}")
    cols = {k: column(header, k) for k in COLUMNS}
    if not (cols["company"] and cols["value"]):
        sys.exit(f"Need a company column and a value column; found: {header}")

    reports = list_reports()
    records, checks = [], []
    print(f"{'company':<16}{'metric':<22}{'answer':<14}{'system':<14}{'unit':<9}{'page':<6}{'conf':<8}result")
    for row in rows:
        name = str(row.get(cols["company"]) or "").strip()
        raw = str(row.get(cols["value"]) or "").strip()
        get = lambda k: str(row.get(cols[k]) or "").strip() if cols[k] else ""
        requested_year = label_year(get("year")) if get("year") else None
        found_reports = [(c, y) for c, y in reports if same_company(c, name)
                         and (requested_year is None or (label_year(y) or 0) >= requested_year)]
        metric_text = get("metric") or "revenue"
        metric = metric_text.lower().replace(" ", "_")
        metric = metric if metric in METRICS else find_metric(metric_text)
        rec = {"company": name, "report": "", "metric": metric or metric_text, "expected": raw, "system": "",
               "unit": "", "page": "", "label": "", "status": "", "confidence": "", "result": "", "warnings": "",
               "correct": None}
        if not found_reports or metric is None:
            rec["result"] = "no report in the system" if not found_reports else f"unknown metric '{metric_text}'"
            records.append(rec)
            print(f"{name:<16}{rec['metric']:<22}{raw:<14}{'-':<14}{'-':<9}{'-':<6}{'-':<8}{rec['result']}")
            continue
        found_reports.sort(key=lambda cy: (abs((label_year(cy[1]) or 0) - requested_year)
                                            if requested_year else 0,
                                            label_year(cy[1]) or 0))
        company = found_reports[0][0]
        r = None
        report = ""
        for candidate_company, candidate_report in found_reports:
            try:
                candidate = extract_financial_metric(
                    candidate_company, candidate_report, metric,
                    year=requested_year, scope=get("scope") or "consolidated")
            except ReportNotFound:
                continue
            if candidate.get("year") == requested_year or requested_year is None:
                company, report, r = candidate_company, candidate_report, candidate
                if candidate["status"] in ("OK", "VERIFY"):
                    break
        if r is None:
            rec["result"] = "no report contains the requested financial year"
            records.append(rec)
            continue
        found = r["status"] in ("OK", "VERIFY")
        if raw.lower() in NONE_WORDS:
            correct = not found
            result = "OK (correctly NOT FOUND)" if correct else "WRONG (should be NOT FOUND)"
        elif number(raw) is None:
            correct, result = None, "no answer given (check by hand)"
        else:
            result = compare(number(raw), get("unit"), r["value_float"], r["unit"]) if found else "NOT FOUND"
            correct = result == "MATCH"
            exact = exact_match(r.get("value"), raw) if found else False
            result = ("OK  " if correct else "WRONG ") + result
            if found:
                result += " exact" if exact else " (normalized only)" if correct else ""
            expected_currency = get("currency").upper()
            if expected_currency and (not r.get("currency") or expected_currency != r["currency"].upper()):
                correct = False
                got_currency = r.get("currency") or "UNVERIFIED"
                result += f"; WRONG/UNVERIFIED currency expected {expected_currency}, got {got_currency}"
            expected_scope = get("scope").lower()
            if expected_scope in ("consolidated", "standalone") and r.get("scope") != expected_scope:
                correct = False
                got_scope = r.get("scope") or "UNVERIFIED"
                result += f"; WRONG/UNVERIFIED scope expected {expected_scope}, got {got_scope}"
            if requested_year and r.get("year") != requested_year:
                correct = False
                result += f"; WRONG year expected {requested_year}, got {r.get('year')}"
            checks.append((company, report, metric, raw))
        rec.update({"report": report, "system": r["value"] or "", "unit": r["unit"] or "", "page": r["page"] or "",
                    "label": r["label"] or "", "status": r["status"], "confidence": r["confidence"],
                    "result": result, "correct": correct, "warnings": " | ".join(r["warnings"] or [r["message"]]).strip(" |")})
        records.append(rec)
        flag = "  (VERIFY)" if r["status"] == "VERIFY" else ""
        print(f"{name:<16}{metric:<22}{raw:<14}{(r['value'] or '-'):<14}{str(r['unit']):<9}{str(r['page']):<6}{r['confidence']:<8}{result}{flag}")

    print_report(records)
    out = "data/processed/evaluation_report.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        fields = ["company", "report", "metric", "expected", "system", "unit", "page", "label", "status",
                  "confidence", "result", "warnings"]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    print(f"\nSaved -> {out}")
    if "--search" in sys.argv and checks:
        search_test(checks)

if __name__ == "__main__":
    import sys

    if "--all-reports" in sys.argv:
        run_all_reports()
    else:
        main()

"""Understand a natural-language question: which metric, which company, which year(s)."""
import json
import re
from pathlib import Path

from src.extract.metrics import METRICS

CHUNKS_PATH = str(Path(__file__).resolve().parents[2] / "data" / "processed" / "chunks.jsonl")


def list_reports(path: str = CHUNKS_PATH):
    """[(company, report_label)] for every ingested report."""
    seen = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                if (c["company"], c["year"]) not in seen:
                    seen.append((c["company"], c["year"]))
    return seen


def label_year(label: str):
    """End year of a report label: FY26 -> 2026, 2025-26 -> 2026, FY2025 -> 2025."""
    m = re.search(r"(20\d{2})\s*[-\u2013/]\s*(20\d{2}|\d{2})\b", label)
    if m:
        end = m.group(2)
        return int(end) if len(end) == 4 else int(m.group(1)[:2] + end)
    m = re.search(r"(20\d{2})", label)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{2})\b", label)
    return 2000 + int(m.group(1)) if m else None


def find_metric(question: str):
    """The metric whose name appears in the question; the longest phrase wins."""
    q = " " + re.sub(r"[^a-z ]", " ", question.lower()) + " "
    q = re.sub(r"\s+", " ", q)
    best = None
    for key, cfg in METRICS.items():
        for name in cfg["names"]:
            if f" {name} " in q and (best is None or len(name) > best[0]):
                best = (len(name), key)
    return best[1] if best else None


def find_company(question: str, reports):
    spaced = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()).strip() + " "
    best = None
    for company in {c for c, _ in reports}:
        variants = {company.lower(), re.sub(r"(?<=[a-z])(?=[A-Z])", " ", company).lower()}
        for v in variants:
            if f" {v} " in spaced and (best is None or len(v) > best[0]):
                best = (len(v), company)
    return best[1] if best else None


def find_years(question: str):
    """End years mentioned: 'FY 2025-26' -> 2026, 'FY26' -> 2026, 'FY 2025' -> 2025.
    'from FY 2021-22 to FY 2025-26' -> every year in the range."""
    t = question.lower()
    found = []
    for m in re.finditer(r"(20\d{2})\s*[-\u2013/]\s*(20\d{2}|\d{2})\b", t):
        end = m.group(2)
        found.append((m.start(), int(end) if len(end) == 4 else int(m.group(1)[:2] + end)))
    t2 = re.sub(r"20\d{2}\s*[-\u2013/]\s*(20\d{2}|\d{2})\b", " ", t)
    for m in re.finditer(r"\bfy\s*'?(\d{2})\b|\bfy\s*(20\d{2})\b|\b(20\d{2})\b", t2):
        y = int(m.group(1)) + 2000 if m.group(1) else int(m.group(2) or m.group(3))
        found.append((m.start() + 100000, y))        # keep after the dashed ones, then sort by value
    years = sorted({y for _, y in found})
    if len(years) >= 2 and re.search(r"\bfrom\b.*\b(to|till|until|through)\b", t):
        return list(range(years[0], years[-1] + 1)), "range"
    if years:
        return years, "list"
    if re.search(r"each year|every year|all years|all the years|year by year|year wise|yearwise|"
                 r"over the years|trend", t):
        return [], "all"
    return [], "latest"


def find_scope(question: str):
    return "standalone" if "standalone" in question.lower() else "consolidated"


def parse_question(question: str, reports):
    years, mode = find_years(question)
    return {"metric": find_metric(question), "company": find_company(question, reports),
            "years": years, "years_mode": mode, "scope": find_scope(question)}

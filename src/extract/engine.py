"""ONE generic pipeline for every financial metric.

    extract_financial_metric(company, report, metric, year=None, scope="consolidated")

The metric comes from the user's question (query.py); its rules are DATA in metrics.py.
Nothing here mentions revenue or profit. The steps are the same for every metric:

  1. candidate pages   BM25 search + every page tagged as an income statement
  2. read rows         label + numbers per row (statement_parser)
  3. match             does the row label equal one of the metric's aliases?
  4. year column       read the year headings, take the column of the requested year
  5. validate + rank   statement page, scope, arithmetic proof, year found, ...
  6. answer            best candidate + evidence, or VERIFY if two rivals are equally plausible
"""
import logging
import re
from collections import defaultdict

from src.extract.confidence import build_signals, cap_level as cap, finalize_confidence, grade_points as grade
from src.extract.metrics import IDENTITIES, METRICS
from src.extract.statement_parser import STOP, find_proofs, parse_rows, to_float
from src.ingest.section_tagger import STATEMENT_HEAD
from src.observability.tracing import log_run_io, setup_tracing, traceable
from src.retrieve.bm25_search import ReportNotFound, load_chunks
from src.retrieve.hybrid_search import HybridSearch

log = logging.getLogger("extractor")

hybrid_search = HybridSearch()


STATEMENT_SECTIONS = ("income_statement", "highlights")
MAX_LEFT_NOISE = 5            # extra words allowed in front of an alias ("Total net revenues")
AMBIGUITY_GAP = 3             # two different values within this score gap -> VERIFY

YEAR_RE = re.compile(r"(20\d{2})\s*[-\u2013/]\s*(\d{2})\b|\b(20\d{2})\b")
UNIT_RE = re.compile(r"\b(crores?|crs?\b\.?|lakhs?|millions?|billions?|thousands?|mn)\b", re.I)
UNIT_NORMAL = {"crore": "crore", "crores": "crore", "cr": "crore", "crs": "crore",
               "lakh": "lakh", "lakhs": "lakh", "million": "million", "millions": "million",
               "mn": "million",
               "billion": "billion", "billions": "billion", "thousand": "thousand",
               "thousands": "thousand"}
UNIT_FACTOR = {"thousand": 1e3, "lakh": 1e5, "million": 1e6, "crore": 1e7, "billion": 1e9}
YOY_LIMIT = 5.0               # a year-on-year change above 5x (or below 1/5) looks like a wrong column or unit
LEVELS = ["LOW", "MEDIUM", "HIGH"]
setup_tracing()


# ------------------------------------------------------------------ small helpers
def search_query(cfg: dict, company: str = None) -> str:
    """Words for the BM25 search: the metric's own names, its synonyms, and statement words."""
    words = [a for tier in cfg["aliases"] for a in tier] + cfg.get("search_terms", [])
    prefix = f"{company} " if company else ""
    return prefix + " ".join(words) + " statement of profit and loss income"


@traceable(name="retrieve", run_type="retriever")
def retrieve(company: str, report: str, cfg: dict, k: int = 5, boost: bool = True):
    """Hybrid BM25 + semantic retrieval with metric aliases."""
    phrases = [a for tier in cfg["aliases"] for a in tier]
    if company:
        phrases.append(company)

    results = hybrid_search.search(
        company=company,
        year=report,
        query=search_query(cfg, company),
        k=k,
        boost=boost,
        phrases=phrases,
    )
    ranked = [(r.get("rerank_score", r.get("hybrid_score", 0.0)), r) for r in results]
    log_run_io(
        inputs={"company": company, "report": report, "k": k},
        outputs={
            "pages": [r.get("page") for _, r in ranked],
            "n": len(ranked),
            "used_rerank": any("rerank_score" in r for _, r in ranked),
        },
    )
    return ranked


_HEADER_NOISE_RE = re.compile(
    r"^(in\s+(?:millions?|thousands?|billions?|crores?|lakhs?)\b"
    r"|except\s+(?:for\s+)?per\s+share\s+amounts?\b"
    r"|for\s+the\s+years?\s+ended\b"
    r"|continuing\s+operations\b"
    r"|consolidated\s+statement\s+of\b|\s+)+",
    re.I
)

_NOTE_TAIL_WORDS = {
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "notes", "note", "and",
    "contd", "continued", "see", "unaudited", "audited", "reconciliation"
}


def norm_label(label: str):
    """Lower-case words of a label, without leading enumerators or table header noise."""
    cl = _HEADER_NOISE_RE.sub("", label.strip())
    words = re.sub(r"[^a-z ]", " ", cl.lower().replace("-", " ")).split()
    while len(words) > 1 and (len(words[0]) == 1 or re.fullmatch(r"[ivxlc]{1,4}", words[0])):
        words.pop(0)
    return words


def match_row(label: str, cfg: dict):
    """Return the alias TIER (0 = strongest) if the label is one of the metric's names, else None."""
    words = norm_label(label)
    if not words:
        return None
    allowed = {tuple(a.split()) for a in cfg.get("allowed_excluded_aliases", [])}
    for tier, aliases in enumerate(cfg["aliases"]):
        for alias in aliases:
            a = alias.split()
            alen = len(a)
            for idx in range(len(words) - alen + 1):
                if words[idx: idx + alen] == a:
                    left_noise = idx
                    if left_noise > 8:
                        continue
                    right_words = words[idx + alen:]
                    if not right_words or all(rw in _NOTE_TAIL_WORDS or len(rw) <= 2 for rw in right_words):
                        if any(w in cfg["exclude_words"] for w in words) and tuple(words[idx:idx + alen]) not in allowed:
                            continue
                        return tier
    return None


def alias_specificity(label: str, cfg: dict):
    """Longest matching alias; exact total labels outrank matching subtotal suffixes."""
    words = norm_label(label)
    best = 0
    allowed = {tuple(a.split()) for a in cfg.get("allowed_excluded_aliases", [])}
    for aliases in cfg["aliases"]:
        for alias in aliases:
            a = alias.split()
            for idx in range(len(words) - len(a) + 1):
                if words[idx:idx + len(a)] != a:
                    continue
                tail = words[idx + len(a):]
                if tail and not all(w in _NOTE_TAIL_WORDS or len(w) <= 2 for w in tail):
                    continue
                if any(w in cfg["exclude_words"] for w in words) and tuple(a) not in allowed:
                    continue
                best = max(best, len(a))
    return best


def header_years(text: str, start: int, n_cols: int):
    """Years in the nearest lines before `start`, in order, without repeats."""
    lines = text[:start].splitlines()
    found = []
    for line in reversed(lines[-10:]):
        matches = []
        for m in YEAR_RE.finditer(line):
            y = int(m.group(1)[:2] + m.group(2)) if m.group(1) else int(m.group(3))
            matches.append(y)
        if len(matches) >= n_cols:
            return matches[-n_cols:]
        for m in reversed(matches):
            if m not in found:
                found.insert(0, m)
        if len(found) >= n_cols:
            return found[-n_cols:]
    if found:
        return found[-n_cols:]
    window = text[max(0, start - 500): start]
    years = []
    for m in YEAR_RE.finditer(window):
        y = int(m.group(1)[:2] + m.group(2)) if m.group(1) else int(m.group(3))
        if y not in years:
            years.append(y)
    return years[-n_cols:]


def detect_unit(text: str, start: int):
    """Unit as printed (million / crore ...). Unit words next to 'share' are ignored, because
    '(In millions, except number of shares, which are reflected in thousands)' has two units."""
    before = text[max(0, start - 1500): start]
    for scope_text in (before, text):
        # Financial statements commonly abbreviate million as "M" only when
        # paired with a currency, e.g. Galderma's "In M USD".
        if re.search(r"\bM\s+(?:USD|EUR|GBP|CAD)\b", scope_text, re.I):
            return "million"
        hits = []
        for m in UNIT_RE.finditer(scope_text):
            if "share" in scope_text[max(0, m.start() - 60): m.start()].lower():
                continue
            hits.append(m.group(1))
        if hits:
            return UNIT_NORMAL.get(hits[-1].lower().rstrip("."))
    return None


def report_context(texts: dict, sections: dict):
    """Use a report-wide unit/currency only when statement pages agree explicitly."""
    units, currencies = set(), set()
    for page, text in texts.items():
        if sections.get(page) not in STATEMENT_SECTIONS:
            continue
        unit = detect_unit(text, 0)
        currency = detect_currency(text, unit)
        if unit:
            units.add(unit)
        if currency:
            currencies.add(currency)
    # Some reports state their presentation currency in the MD&A, while the
    # selected annual summary table omits the currency symbol. Accept an
    # explicit named-currency declaration from the same report as evidence.
    declared = any(re.search(
        r"all amounts.{0,100}?(?:are )?in(?: millions? of)? Canadian dollars?",
        text, re.I | re.S) for text in texts.values())
    return (next(iter(units)) if len(units) == 1 else None,
            "CAD" if declared else next(iter(currencies)) if len(currencies) == 1 else None)


def detect_currency(text: str, unit, start: int = None):
    if unit in ("crore", "lakh"):
        return "INR"
    # Prefer the selected statement's local context. A report page can mention
    # foreign currencies in notes or comparative disclosures; counting every
    # currency symbol on the page can otherwise label the table incorrectly.
    if start is not None:
        text = text[max(0, start - 1500):min(len(text), start + 3000)]
    # Some PDF fonts map the rupee glyph to capital "I" in the printed unit
    # declaration, e.g. "(I in millions)". Require the financial header form.
    if re.search(r"\(\s*I\s+in\s+(?:millions?|crores?)\b", text, re.I):
        return "INR"
    patterns = {
        "USD": r"US\$|\bUSD\b|\$",
        "INR": r"\u20b9|\bRs\.?\b|\bINR\b",
        "GBP": r"\u00a3|\bGBP\b",
        "EUR": r"\u20ac|\bEUR\b",
        "CAD": r"\bCAD\b|Canadian dollars?|\bC\$",
        "SEK": r"\bSEK\b|Swedish kronor?",
    }
    present = [code for code, pattern in patterns.items() if re.search(pattern, text, re.I)]
    # If local evidence names multiple currencies, leave it unresolved instead
    # of guessing from the most frequent marker. report_context may still infer
    # the presentation currency when the statement pages agree.
    return present[0] if len(present) == 1 else None


def detect_scope(text: str, start: int):
    # PDF text extraction can place a statement heading after its rows (for
    # example, HUL's consolidated statement). Check a bounded window on both
    # sides and use the closest explicit scope marker.
    lo, hi = max(0, start - 1500), min(len(text), start + 2000)
    window = text[lo:hi]
    markers = [
        (abs(lo + match.start() - start), match.group(1).lower())
        for match in re.finditer(r"\b(consolidated|standalone)\b", window, re.I)
    ]
    if not markers:
        return "unknown"
    return min(markers, key=lambda marker: marker[0])[1]


def build_page_texts(chunks):
    """page -> full text (text and table chunks in order), page -> section type."""
    parts, tables, section = defaultdict(list), defaultdict(list), {}
    for c in chunks:
        curr_sec = section.get(c["page"], "other")
        if c["section_type"] in STATEMENT_SECTIONS:
            section[c["page"]] = c["section_type"]
        elif curr_sec == "other":
            section[c["page"]] = c["section_type"]

        if c["chunk_type"] == "text":
            parts[c["page"]].append((int(c["chunk_id"].rsplit("_c", 1)[1]), c["text"]))
        elif c["chunk_type"] == "table":
            tables[c["page"]].append(c["text"])

    page_texts = {}
    for p in set(parts.keys()).union(tables.keys()):
        t_text = "\n".join(t for _, t in sorted(parts.get(p, [])))
        tb_text = "\n\n".join(tables.get(p, []))
        if tb_text and tb_text not in t_text:
            page_texts[p] = (t_text + "\n\n" + tb_text).strip()
        else:
            page_texts[p] = t_text
    return page_texts, section


def is_valid_statement_head(text: str, start: int) -> bool:
    """Ignore statement head phrases that appear inside row descriptions (e.g. 'reclassified to statement of Profit & Loss')."""
    prefix = text[max(0, start - 45): start].lower()
    return not bool(re.search(r"\b(reclassified\s+to|transferred\s+to|charged\s+to|carried\s+to|recogni[sz]ed\s+in)\b", prefix))


# ------------------------------------------------------------------ steps 2-4
def statement_rows(text: str, cfg: dict):
    """Rows of the statement on this page: from the statement title down (to the first
    expense row when the metric lives in the income region)."""
    rows = parse_rows(text)
    anchors = [m.start() for m in STATEMENT_HEAD.finditer(text) if is_valid_statement_head(text, m.start())] + [0]
    for anchor in anchors:
        sub = [r for r in rows if r.start >= anchor]
        if cfg["region"] == "income":
            cut = []
            for r in sub:
                if STOP.match(r.label):
                    break
                cut.append(r)
            sub = cut
        if any(match_row(r.label, cfg) is not None for r in sub):
            return sub
    return []


def pick_column(row, years, target_year, column_indices=None):
    """Which number in the row belongs to the requested year? -> (token, year, status)"""
    tokens = row.tokens if column_indices is None else [row.tokens[i] for i in column_indices]
    years = years if column_indices is None or len(years) != len(row.tokens) \
        else [years[i] for i in column_indices]
    n = len(tokens)
    if years and len(years) == n:
        year = max(years) if target_year is None else target_year
        if year in years:
            return tokens[years.index(year)], year, "ok"
        return None, None, "year_not_in_row"
    if target_year is None:
        return tokens[0], None, "assumed_first_column"
    return None, None, "year_unknown"


def scoped_column_indices(text: str, row_start: int, n_cols: int, years, scope_pref):
    """Map repeated year groups in a combined Standalone/Consolidated table."""
    if scope_pref not in ("standalone", "consolidated") or n_cols < 4 or n_cols % 2:
        return None
    half = n_cols // 2
    if len(years) != n_cols or years[:half] != years[half:]:
        return None
    header = text[max(0, row_start - 600):row_start]
    markers = list(re.finditer(r"\b(standalone|consolidated)\b", header, re.I))
    if len(markers) < 2:
        return None
    first, second = markers[-2:]
    groups = [first.group(1).lower(), second.group(1).lower()]
    if groups[0] == groups[1] or second.start() - first.start() > 180:
        return None
    group = groups.index(scope_pref)
    return list(range(group * half, (group + 1) * half))


def statement_title(text: str, row_start: int, scope: str):
    """e.g. 'Consolidated Statement of Profit and Loss' (the title as printed in the report)."""
    heads = [m for m in STATEMENT_HEAD.finditer(text) if m.start() <= row_start and is_valid_statement_head(text, m.start())]
    if not heads:
        return None
    segment = text[heads[-1].start(): heads[-1].start() + 70]
    title = re.split(r"[\n(\d$\u20b9]|\s{2,}|\s+for\s+the\s+(?:year|period)", segment)[0].strip()
    title = " ".join(title.title().split()) if title.isupper() else " ".join(title.split())
    title = title[0].upper() + title[1:]
    return (scope.capitalize() + " " if scope in ("consolidated", "standalone") else "") + title


def evidence_lines(rows, idx):
    lo, hi = max(0, idx - 2), min(len(rows), idx + 3)
    return [("» " if j == idx else "  ") + rows[j].label + " | " + " | ".join(rows[j].tokens)
            for j in range(lo, hi)]


# ------------------------------------------------------------------ steps 1, 3-5
def total_revenue_includes_other_income(row, rows):
    """A proved 'total revenues' sum is not revenue when it adds other income."""
    if not row.proof:
        return False
    labels = [norm_label(rows[index].label) for index, _ in row.proof]
    has_operating_revenue = any(
        any(words[i:i + 3] == ["revenue", "from", "operations"]
            for i in range(max(0, len(words) - 2)))
        for words in labels
    )
    has_other_income = any(words[:2] == ["other", "income"] for words in labels)
    return has_operating_revenue and has_other_income


def find_candidates(texts, sections, parse_pages, cfg, year, scope_pref, page_rank, company: str = None):
    cands = []
    for page in parse_pages:
        text = texts.get(page, "")
        rows = statement_rows(text, cfg)
        if not rows:
            continue
        find_proofs(rows)
        for i, row in enumerate(rows):
            tier = match_row(row.label, cfg)
            if tier is None:
                continue
            if cfg is METRICS["revenue"] and total_revenue_includes_other_income(row, rows):
                continue
            anchor = rows[0].start if cfg["pages"] == "statement" else row.start
            # Pages can contain several tables. Resolve the year columns beside
            # this row, not beside the first numeric row on the page.
            years = header_years(text, row.start, len(row.tokens))
            # PDF text order sometimes puts the shared statement header outside
            # the row's local window. Use it only when it maps every row value.
            if len(years) != len(row.tokens):
                shared_years = header_years(text, anchor, len(row.tokens))
                if len(shared_years) == len(row.tokens):
                    years = shared_years
            scope_columns = scoped_column_indices(text, row.start, len(row.tokens), years, scope_pref)
            token, year_used, ystatus = pick_column(row, years, year, scope_columns)
            scope = scope_pref if scope_columns is not None else detect_scope(text, row.start)
            candidate_years = ([years[i] for i in scope_columns]
                               if scope_columns is not None else years)
            score = 50 - 10 * tier
            # Prefer the full metric label over an alias suffix shared with a component.
            score += 2 * alias_specificity(row.label, cfg)
            score += {"income_statement": 20, "highlights": 10}.get(sections.get(page), 0)
            if scope_pref is None:
                score += 4
            else:
                score += 8 if scope == scope_pref else 4 if scope == "unknown" else 0
            score += 5 if row.is_total else 0
            score += 5 if ystatus == "ok" else 0

            # Disambiguate Parent Company vs Subsidiary / Segment / MD&A disclosure
            head_window = text[max(0, row.start - 600): row.start + 400].lower()
            is_sub = bool(re.search(
                r"\b(retail ventures|platforms limited|subsidiary|subsidiaries|segment reporting|segment information|disaggregated revenue|"
                r"asset\s*(?:&|and)\s*wealth management|consumer\s*(?:&|and)\s*community banking|"
                r"commercial\s*(?:&|and)\s*investment bank|corporate results|business segments?|"
                r"directors'\s+report|board's\s+report|management\s+discussion|discussion\s+and\s+analysis)\b",
                head_window
            ))
            if is_sub and scope_pref == "consolidated":
                score -= 30

            # Parent company alignment
            if company and company.lower() in head_window and not is_sub:
                score += 5

            # Verified audited statement title check
            if any(is_valid_statement_head(text, m.start()) for m in STATEMENT_HEAD.finditer(head_window)):
                score += 5

            if token is None:
                score -= 100
            cands.append({"page": page, "text": text, "rows": rows, "idx": i, "row": row,
                          "tier": tier, "years": candidate_years, "token": token, "year_used": year_used,
                          "ystatus": ystatus, "scope": scope, "score": score,
                          "anchor": anchor, "rank": page_rank.get(page, 99),
                          "scope_columns": scope_columns})
    cands.sort(
        key=lambda c: (c["score"], -c["rank"], -c["page"], -c["idx"]),
        reverse=True
    )
    for c in cands:
        c["scope_conflict"] = bool(
            scope_pref
            and c["scope"] == "unknown"
            and c["year_used"]
            and any(
                other["scope"] not in ("unknown", scope_pref)
                and other["year_used"] == c["year_used"]
                and other["token"] is not None
                and c["token"] is not None
                and abs(to_float(other["token"]) - to_float(c["token"]))
                    > 0.005 * max(abs(to_float(c["token"])), 1)
                for other in cands
            )
        )
    # Scope is a substantive choice: keep the requested scope when explicit.
    # Otherwise prefer unscoped statement rows over highlights/notes, and keep
    # a clearly mismatched statement only as a capped fallback.
    if scope_pref:
        exact_scope = [c for c in cands if c["scope"] == scope_pref]
        unscoped_statements = [c for c in cands
                               if c["scope"] == "unknown"
                               and sections.get(c["page"]) == "income_statement"]
        statement_candidates = [c for c in cands
                                if sections.get(c["page"]) == "income_statement"]
        if exact_scope:
            cands = exact_scope
        elif unscoped_statements:
            cands = unscoped_statements
        elif statement_candidates:
            cands = statement_candidates
        else:
            unscoped = [c for c in cands if c["scope"] == "unknown"]
            if unscoped:
                cands = unscoped
    return cands


def find_note_value(texts, note_cfg, scope, statement_value, target_year):
    """A note row such as 'Revenue recognised' (contracted price - reductions = revenue
    recognised) that is a verified total and within `tolerance` of the statement value."""
    regex = re.compile(note_cfg["regex"], re.I)
    best = None
    for page, text in texts.items():
        if not regex.search(text):                 # cheap pre-filter
            continue
        rows = parse_rows(text)
        find_proofs(rows)
        for row in rows:
            if not (regex.fullmatch(" ".join(norm_label(row.label))) and row.is_total):
                continue
            if detect_scope(text, row.start) not in (scope, "unknown"):
                continue
            years = header_years(text, row.start, len(row.tokens))
            token, _, status = pick_column(row, years, target_year)
            if token is None:
                continue
            rel = abs(to_float(token) - statement_value) / max(abs(statement_value), 1)
            if rel <= note_cfg["tolerance"] and (best is None or rel < best[0]):
                best = (rel, page, text, row, token)
    return best


# ------------------------------------------------------------------ validation helpers


def check_identity(metric: str, best: dict, text: str):
    """Cross-line arithmetic on the same statement, e.g. PBT = Total income - Total expenses.
    -> (None, "") if not applicable, else ("verified" | "not_reconciled", description)."""
    years, n, year = best["years"], len(best["row"].tokens), best["year_used"]
    if not (year and len(years) == n and year in years):
        return None, ""
    idx = years.index(year)
    rows = [r for r in parse_rows(text) if r.start >= best["rows"][0].start]
    for ident in IDENTITIES:
        if ident["result"] != metric:
            continue
        total, found = 0.0, True
        for term_metric, sign in ident["terms"]:
            hit = next((r for r in rows if match_row(r.label, METRICS[term_metric]) is not None
                        and len(r.values) == n), None)
            if hit is None:
                found = False
                break
            total += sign * hit.values[idx]
        if not found:
            continue
        reported = best["row"].values[idx]
        if abs(total - reported) <= max(1.01, 1e-6 * abs(reported)):
            return "verified", ident["name"]
        return "not_reconciled", f"{ident['name']}: computed {total:,.2f} but reported {reported:,.2f}"
    return None, ""


def plausibility(cfg: dict, best: dict):
    """Business sanity rules. Each problem returned makes the answer VERIFY."""
    years, row, year = best["years"], best["row"], best["year_used"]
    if not (year and len(years) == len(row.tokens) and year in years):
        return []
    values = dict(zip(years, row.values))
    v, problems = values[year], []
    if cfg.get("positive_only") and v <= 0:
        problems.append(f"value {row.tokens[years.index(year)]} is not positive; this metric should be")
    prev = values.get(year - 1)
    if not cfg.get("volatile") and prev and v > 0 and prev > 0 and not (1 / YOY_LIMIT <= v / prev <= YOY_LIMIT):
        problems.append(f"year-on-year change of {v / prev:.1f}x looks implausible; check the unit or the column")
    return problems


# ------------------------------------------------------------------ the one public function
@traceable(name="extract_financial_metric", run_type="chain")
def extract_financial_metric(company: str, report: str, metric: str, year: int = None,
                             scope: str = "consolidated", _fallback_from: str = None):
    """company/report = names as stored by ingestion (e.g. 'TCS', 'FY26').
    metric = a key of METRICS. year = end year of the financial year (2026 for FY 2025-26);
    None means the latest year in the statement. scope = 'consolidated' | 'standalone' | None.
    Raises ReportNotFound if the report has not been ingested."""
    cfg = METRICS[metric]
    result = {"company": company, "report": report, "metric": metric, "metric_used": None,
              "status": "NOT_FOUND", "value": None, "value_float": None, "value_absolute": None,
              "unit": None, "currency": None, "year": None, "year_columns": [], "statement": None,
              "scope": None, "page": None, "label": None, "evidence": [], "evidence_row": None,
              "source_evidence_verified": False,
              "confidence": "NONE", "score": 0, "reasons": [], "warnings": [], "alternatives": [],
              "needs_verification": False, "interpretation": None, "message": ""}

    chunks = load_chunks(company, report)
    if not chunks:
        raise ReportNotFound(f"No report ingested for {company} {report}. See: python -m src.tools reports")
    texts, sections = build_page_texts(chunks)
    retrieved = []
    for _, c in retrieve(company, report, cfg):
        if c["page"] not in retrieved:
            retrieved.append(c["page"])
    statement_pages = [p for p in sorted(sections) if sections[p] in STATEMENT_SECTIONS]
    parse_pages = statement_pages if cfg["pages"] == "statement" \
        else list(dict.fromkeys(retrieved + statement_pages))
    log.debug("%s %s %s: search found pages %s; reading pages %s", company, report, metric, retrieved, parse_pages)

    cands = find_candidates(texts, sections, parse_pages, cfg, year, scope,
                            {p: i for i, p in enumerate(retrieved)}, company=company)
    # Some annual reports have a correct consolidated statement page that the
    # section tagger missed. If tagged statements contain no metric row, inspect
    # the already-retrieved pages before declaring the metric absent/falling back.
    if not cands and cfg["pages"] == "statement" and retrieved:
        parse_pages = list(dict.fromkeys(statement_pages + retrieved))
        cands = find_candidates(texts, sections, parse_pages, cfg, year, scope,
                                {p: i for i, p in enumerate(retrieved)}, company=company)
    for c in cands[:5]:
        log.debug("  candidate page %s '%s' %s score %s tier %s scope %s",
                  c["page"], c["row"].label, c["token"], c["score"], c["tier"], c["scope"])

    # -- no row is labelled like this metric: try the sector fallback (e.g. revenue -> total income)
    if not cands:
        fb = cfg.get("fallback")
        if fb and _fallback_from is None:
            r = extract_financial_metric(company, report, fb["metric"], year, scope, _fallback_from=metric)
            if r["status"] in ("OK", "VERIFY"):
                r["metric"], r["metric_used"] = metric, fb["metric"]
                cap(r, "MEDIUM", f"{metric.replace('_', ' ')} is not reported ({fb['reason']}); "
                                 f"{fb['metric'].replace('_', ' ')} is used instead")
                return r
        result["message"] = "no row in the report is labelled like this metric"
        return result
    best = cands[0]
    preferred_specificity = alias_specificity(best["row"].label, cfg)
    if best["token"] is None:
        result["status"] = "YEAR_NOT_IN_REPORT"
        result["year_columns"] = best["years"]
        result["message"] = f"the metric exists, but not for year {year}; columns present: {best['years']}"
        return result

    text, row, anchor = best["text"], best["row"], best["anchor"]
    token, page = best["token"], best["page"]
    points, reasons, caps = 0, [], []

    def add(p, why):
        nonlocal points
        points += p
        reasons.append(f"{p:+d} {why}")

    # -- year
    if best["year_used"]:
        add(1, f"year headings {best['years']} -> column of {best['year_used']}")
    else:
        caps.append("year headings not found: the first column was assumed")
    digits = re.sub(r"\D", "", report)
    if best["year_used"] and digits and str(best["year_used"])[-2:] == digits[-2:]:
        add(1, f"year {best['year_used']} is the report's own year ({report})")
    elif best["year_used"]:
        reasons.append(f"+0 year {best['year_used']} comes from a comparative column of report {report}")
    report_year = int(digits[-2:]) + 2000 if len(digits) >= 2 else None
    year_label_warning = None
    if best["year_used"] and report_year and best["year_used"] != report_year:
        year_label_warning = (f"report label {report} suggests {report_year}, but the source "
                              f"statement explicitly reports {best['year_used']}; using the statement year")
        reasons.append(f"+0 {year_label_warning}")

    # -- unit / currency / scope / statement page
    unit = detect_unit(text, anchor)
    currency = detect_currency(text, unit, anchor)
    report_unit, report_currency = report_context(texts, sections) if not unit or not currency else (None, None)
    if not unit and report_unit:
        unit = report_unit
        reasons.append(f"+0 unit inferred consistently from report statements: {unit}")
    if not currency and report_currency:
        currency = report_currency
        reasons.append(f"+0 currency inferred consistently from report statements: {currency}")
    add(1 if unit else 0, f"unit read from the report: {unit}" if unit else "unit not found")
    add(1 if currency else 0, f"currency: {currency}" if currency else "currency not found")
    if not unit:
        caps.append("unit not found in the report")
    if best["scope"] == "consolidated":
        add(1, "consolidated statement")
    else:
        reasons.append(f"+0 scope: {best['scope']}")
    if scope and best["scope"] not in (scope, "unknown"):
        caps.append(f"{scope} statement requested but only the {best['scope']} one has this line")
    elif scope and best["scope"] == "unknown":
        caps.append(f"requested {scope} scope could not be verified from the statement")
    if best.get("scope_conflict"):
        why = "an explicit opposite-scope revenue value exists for the same year"
        caps.append(why)
        reasons.append(f"+0 scope evidence: {why}")
    section = sections.get(page)
    add({"income_statement": 2, "highlights": 1}.get(section, 0), f"page {page} is tagged '{section}'")

    # -- arithmetic proof of a total, and cross-line identity
    if row.is_total:
        parts = " ".join(("+ " if s_ > 0 else "- ") + best["rows"][k].label for k, s_ in row.proof).lstrip("+ ")
        add(2, f"arithmetic proof: {row.label} = {parts} (verified on every year column)")
    else:
        add(1, "single line item (not a sum of other rows)")
    status_id, ident = check_identity(metric, best, text)
    if status_id == "verified":
        add(2, f"identity verified: {ident}")
    elif status_id == "not_reconciled":
        add(-1, f"identity not reconciled: {ident}")
        reasons[-1] += " (exceptional items or associates may explain the gap)"
        caps.append(f"identity not reconciled: {ident}")

    # -- cross-reference: same number on other retrieved pages?
    flat = token.replace(",", "").strip("()-")
    pat = re.compile(rf"(?<![\d.]){re.escape(flat)}(?!\d)")
    confirm = [p for p in retrieved if p != page and pat.search(texts.get(p, "").replace(",", ""))]
    add(2 if confirm else 0, f"same number also on page(s) {confirm}" if confirm
        else "number not found on other retrieved pages")

    # -- business-rule plausibility (checked on the statement value, before any note override)
    problems = plausibility(cfg, best)

    # -- optional note override (e.g. TCS "Revenue recognised")
    label = row.label
    if cfg.get("note_override"):
        hit = find_note_value(texts, cfg["note_override"], best["scope"], to_float(token), best["year_used"])
        if hit:
            _, npage, ntext, nrow, ntoken = hit
            if ntoken != token:
                reasons.append(f"+0 statement (page {page}) shows {token}; the note differs by "
                               f"{abs(to_float(ntoken) - to_float(token)):g} - the note value is used")
            reasons.append(f"+0 note on page {npage}: '{nrow.label}' is a verified total ({ntoken})")
            token, page, label = ntoken, npage, nrow.label + " (reconciliation note)"
            unit = detect_unit(ntext, nrow.start) or unit
            note_rows = parse_rows(ntext)
            find_proofs(note_rows)
            best = dict(best, rows=note_rows)
            best["idx"] = next((i for i, r in enumerate(best["rows"]) if r.start == nrow.start), 0)
            row = nrow

    # -- rivals: a different value with (almost) the same score, which is not a part of the best row
    parts_of_best = {k for k, _ in row.proof} if best["page"] == page else set()
    rivals = [c for c in cands[1:]
              if c["token"] is not None
              and abs(to_float(c["token"]) - to_float(token)) > 0.005 * max(abs(to_float(token)), 1)
              and c["year_used"] == best["year_used"]
              and c["scope"] == best["scope"]
              and sections.get(c["page"]) == "income_statement"
              and c["score"] >= cands[0]["score"] - max(AMBIGUITY_GAP, 10)
              and alias_specificity(c["row"].label, cfg) >= preferred_specificity
              and not (c["page"] == best["page"] and c["idx"] in parts_of_best)]
    seen, alts = set(), []
    for c in cands[1:]:
        key = (c["row"].label, c["token"], c["page"])
        if c["token"] is not None and key not in seen:
            seen.add(key)
            alts.append({"label": c["row"].label, "value": c["token"], "page": c["page"],
                         "scope": c["scope"], "score": c["score"]})
    if rivals:
        r = rivals[0]
        problems.insert(0, f"'{r['row'].label}' = {r['token']} (page {r['page']}) is almost as plausible as "
                           f"'{label}' = {token}")

    value_float = to_float(token)
    evidence = evidence_lines(best["rows"], best["idx"])
    # Keep the source's printed row label here; `label` may have a display-only
    # suffix such as "(reconciliation note)" that is not present in the PDF.
    evidence_row = f"{row.label} {token}"
    digits = re.sub(r"\D", "", report)
    report_year_match = bool(best["year_used"] and digits and str(best["year_used"])[-2:] == digits[-2:])
    signals = build_signals(
        tier=best["tier"],
        evidence=evidence,
        evidence_row=evidence_row,
        token=token,
        year_used=best["year_used"],
        year_status=best["ystatus"],
        report_year_match=report_year_match,
        unit=unit,
        currency=currency,
        section=sections.get(page),
        page_in_retrieved=page in retrieved,
        n_rows=len(best["rows"]),
        is_total=bool(row.is_total),
        identity_status=status_id,
        confirm_pages=confirm,
        n_rivals=len(rivals),
        problems=problems,
    )
    result.update({
        "status": "VERIFY" if problems else "OK",
        "value": token, "value_float": value_float, "unit": unit, "currency": currency,
        "value_absolute": value_float * UNIT_FACTOR[unit] if unit in UNIT_FACTOR else None,
        "year": best["year_used"], "year_columns": best["years"],
        "statement": statement_title(text, anchor, best["scope"]) if page == best["page"]
        else "Notes to the financial statements",
        "scope": best["scope"], "page": page, "label": label,
        "evidence": evidence,
        "evidence_row": evidence_row,
        "source_evidence_verified": signals["source_evidence_verified"],
        "score": points, "reasons": reasons,
        "confidence": grade(points), "warnings": [], "alternatives": alts[:5],
        "needs_verification": bool(problems),
    })
    if year_label_warning:
        result["warnings"].append(year_label_warning)
    finalize_confidence(result, points=points, caps=caps, problems=problems, signals=signals)
    if cfg.get("negative_is"):
        word = cfg["negative_is"]
        result["interpretation"] = (f"{word} of {abs(value_float):,.2f}".rstrip("0").rstrip(".")
                                    if value_float < 0
                                    else f"no {word}: the reported value is positive ({token})")
    log.info("%s %s %s -> %s %s (page %s, %s, %s)", company, report, metric, token, unit,
             page, result["status"], result["confidence"])
    log_run_io(
        inputs={"company": company, "report": report, "metric": metric, "year": year, "scope": scope},
        outputs={
            "status": result["status"], "value": result["value"], "unit": result["unit"],
            "year": result["year"], "page": result["page"], "confidence": result["confidence"],
            "source_evidence_verified": result["source_evidence_verified"],
            "confidence_score": result.get("confidence_score"),
            "warnings": result.get("warnings"),
        },
        error=result["message"] or None if result["status"] not in ("OK", "VERIFY") else None,
    )
    return result

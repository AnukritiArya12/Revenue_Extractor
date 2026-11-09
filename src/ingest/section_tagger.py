"""Tag each page as 'income_statement', 'highlights' or 'other' by looking at its CONTENT.

Every company words and places its headings differently, but a real income-statement page
always has (1) a revenue-like row with numbers and (2) several profit-and-loss markers
(net income, earnings per share, ...). Auditor reports and contents pages do not.

The tag is a ranking signal: the engine reads statement pages first and trusts them more.
"""
import re

# a revenue-like label followed by numbers = the first row of any income statement
_REV_LABEL = re.compile(
    r"(?<!of )(?:total\s+net\s+revenues?|total\s+net\s+sales|total\s+revenues?|"
    r"net\s+revenues?|net\s+sales|revenues?\s+from\s+operations|"
    r"revenues?\s+from\s+contracts?\s+with\s+customers|revenues?|turnover|"
    r"total\s+income|income\s+from\s+operations|interest\s+earned|total\s+interest\s+income)", re.I)
_NUM = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?|\d+\.\d+")
_NOTE_REF = re.compile(r"\bNotes?\s*\d+(\.\d+)?|\(contd\.?\)", re.I)
_PERCENT = re.compile(r"\d+(\.\d+)?\s*%")
_WORD = re.compile(r"[A-Za-z]{4,}")

# profit-and-loss markers
_MARKERS = [re.compile(p, re.I) for p in (
    r"per\s+(common\s+|equity\s+)?share", r"net\s+income", r"net\s+earnings", r"net\s+profit",
    r"profit\s+for\s+the\s+(year|period)", r"profit\s+before\s+(exceptional|tax)",
    r"(income|earnings)\s+before\s+(income\s+)?tax", r"total\s+expenses",
    r"total\s+costs\s+and\s+expenses", r"operating\s+(income|profit)",
    # banks and central banks: "Income and Expenditure", "Surplus", "Interest expended" ...
    r"total\s+expenditure", r"\bsurplus\b", r"net\s+interest\s+income", r"interest\s+expended",
    r"provisions\s+and\s+contingencies", r"loss\s+(before\s+tax|for\s+the\s+(year|period))",
    r"profit\s*/?\s*\(?loss\)?\s+for\s+the\s+(year|period)")]

STATEMENT_HEAD = re.compile(
    r"statements?\s+of\s+(consolidated\s+)?(profit|income|operations|earnings|comprehensive)"
    r"|income\s+statements?|profit\s+(and|&)\s+loss|income\s+and\s+expenditure", re.I)
_HIGHLIGHT_HEAD = re.compile(
    r"financial\s+highlights|key\s+financial|performance\s+highlights|\bat\s+a\s+glance\b|"
    r"five[-\s]year\s+(summary|review)|selected\s+financial\s+data|financial\s+summary", re.I)


def _has_revenue_row(text: str) -> bool:
    """A revenue label followed by >= 2 numbers before the next word (a table row, not a sentence)."""
    for m in _REV_LABEL.finditer(text):
        window = _PERCENT.sub(" ", _NOTE_REF.sub(" ", text[m.end(): m.end() + 200]))
        w = _WORD.search(window)
        if w:
            window = window[: w.start()]
        if len(_NUM.findall(window)) >= 2:
            return True
    return False


def tag_page(text: str) -> str:
    if not _has_revenue_row(text):
        return "other"
    if sum(1 for p in _MARKERS if p.search(text)) >= 2 and STATEMENT_HEAD.search(text):
        return "income_statement"
    if _HIGHLIGHT_HEAD.search(text[:1500]):
        return "highlights"
    return "other"

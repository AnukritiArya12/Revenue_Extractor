"""Turn page text into ROWS (label + numbers) and test which rows are sums of other rows.

A total equals the sum (or difference) of the rows above it, on every year column, e.g.
    Total income            = Revenue from operations + Other income
    Revenue from operations = Value of sales - GST recovered
So we do not need to trust label names to know what is a total: we test the arithmetic.
"""
import itertools
import re
from dataclasses import dataclass, field

TOKEN = re.compile(
    r"(?P<year>\b20\d{2}(?:\s*[-\u2013/]\s*\d{2})?\b(?!\d|,\d))"
    r"|(?P<num>(?:(?<![\w.])-)?(?:\(?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\)?|(?:\(?\d{1,3}(?:[ \t\u00a0]\d{3})+(?:\.\d+)?\)?(?![,\d]))|\(?\d+\.\d+\)?|\(?\d{4,}\)?))"
    r"|(?P<small>\(?\d{1,3}\)?)"
    r"|(?P<dash>[\u2014\u2013])"
    r"|(?P<word>[A-Za-z][A-Za-z'\u2019\-]*)"
)
# where the income lines end and the expense lines start
STOP = re.compile(
    r"^(operating\s+expenses?|expenses?|expenditure|total\s+(operating\s+)?(costs?|expenses?|expenditure)|"
    r"costs?\s+and\s+expenses?|cost\s+of|employee|purchases?\s+of|depreciation|"
    r"finance\s+costs?|other\s+expenses?|selling|research|general\s+and|marketing|"
    r"compensation|profit|net\s+(income|profit|earnings)|income\s+before|"
    r"earnings\s+before|operating\s+(income|profit))", re.I)


@dataclass
class Row:
    label: str
    tokens: list                 # numbers as printed, e.g. ["2,67,021", "2,55,324"]
    values: list                 # the same numbers as floats; (1,234) and -1,234 are negative
    start: int                   # character offset of the row in the page text
    proof: list = field(default_factory=list)   # [(row_index, sign), ...] if this row is a sum

    @property
    def is_total(self):
        return bool(self.proof)


def to_float(tok: str) -> float:
    """'2,67,021' -> 267021.0 ; '(5,000)' and '-5,000' -> -5000.0 ; '89 490' -> 89490.0"""
    s = str(tok).strip()
    neg = s.startswith("(") or s.startswith("-") or s.startswith("–") or s.startswith("—")
    clean = s.strip("()–—-").replace(",", "").replace(" ", "").replace("\u00a0", "")
    try:
        v = float(clean)
    except ValueError:
        return 0.0
    return -v if neg else v


def _clean(tokens):
    """Drop a leading note reference like 2.18 when the real amounts follow it."""
    # A PDF table may be split at a page/chunk boundary, leaving the note number
    # followed by only one visible amount (e.g. HCL's 3.19 | 130,144).
    # It is still a note reference, not a revenue value.
    if (len(tokens) >= 2 and re.fullmatch(r"\d{1,2}\.\d{1,2}", tokens[0])
            and all("," in t or len(re.sub(r"\D", "", t)) >= 4 for t in tokens[1:])):
        return tokens[1:]
    return tokens


def parse_rows(text: str):
    """Split page text into rows: a label followed by >= 2 amounts."""
    rows, words, nums, start = [], [], [], None

    def flush():
        nonlocal words, nums, start
        nums = _clean(nums)
        if words and len(nums) >= 2:
            try:
                rows.append(Row(" ".join(words), nums[:], [to_float(n) for n in nums], start))
            except Exception:
                pass
        words, nums, start = [], [], None

    for m in TOKEN.finditer(text):
        kind, tok = m.lastgroup, m.group()
        # Parenthesized single-letter markers such as (g) are footnotes, not
        # row boundaries; keeping the amounts together preserves year columns.
        if (kind == "word" and len(tok) == 1 and m.start() > 0 and m.end() < len(text)
                and text[m.start() - 1] == "(" and text[m.end()] == ")"):
            continue
        if kind == "word":
            if nums:
                flush()
            if not words:
                start = m.start()
            words.append(tok)
        elif kind == "num":
            if words:
                # Percentage-change columns are not financial amounts. Their
                # decimals otherwise become an extra phantom year column.
                if not re.match(r"\s*%", text[m.end():]):
                    nums.append(tok)
        elif kind == "dash":
            if words:
                nums.append("0")
        elif kind == "year":                 # a year heading: drop the header label
            words, nums, start = [], [], None
        # 'small' numbers are note references / footnotes: ignored
    flush()

    # Also parse structured pipe-separated table rows if present
    if "|" in text:
        seen_starts = {r.start for r in rows}
        for line in text.splitlines():
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                lbl = parts[0]
                col_nums = []
                for p in parts[1:]:
                    cl = p.replace("$", "").replace("€", "").replace("£", "").replace("₹", "").replace("CHF", "").strip()
                    if cl and re.fullmatch(r"\(?[–\-—]?\d{1,3}(?:[,\s\u00a0]\d{2,3})*(?:\.\d+)?\)?", cl):
                        col_nums.append(cl)
                if lbl and len(col_nums) >= 2:
                    pos = text.find(line)
                    if pos not in seen_starts:
                        try:
                            rows.append(Row(lbl, col_nums[:], [to_float(n) for n in col_nums], pos))
                            seen_starts.add(pos)
                        except Exception:
                            pass
    return rows


def _tol(v: float) -> float:
    return max(1.01, 1e-6 * abs(v))


def find_proofs(rows):
    """Mark rows that equal a sum (or difference) of the rows just above them."""
    for i, row in enumerate(rows):
        prev = list(range(max(0, i - 6), i))
        found = False
        for r in range(2, min(len(prev), 5) + 1):
            for combo in itertools.combinations(prev, r):
                if (i - 1) not in combo:
                    continue
                ncols = min(len(row.values), *(len(rows[k].values) for k in combo))
                if ncols < 2:
                    continue
                for signs in itertools.product((1, -1), repeat=r):
                    if signs[0] != 1:
                        continue
                    if all(abs(sum(s * rows[k].values[j] for s, k in zip(signs, combo))
                               - row.values[j]) <= _tol(row.values[j]) for j in range(ncols)):
                        row.proof = list(zip(combo, signs))
                        found = True
                        break
                if found:
                    break
            if found:
                break
    return rows

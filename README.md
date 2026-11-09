# Financial Metric Extractor for Annual Reports

Ask a plain-English question about an annual report (PDF or Word) and get the **correct value for the right company, metric and financial year**, with the page, the statement it came from and the evidence lines.

```
python -m src.ask "What was the revenue of TCS in FY 2025-26?"
python -m src.ask "What was the profit after tax of Infosys in FY26?"
python -m src.ask "What was the total income of TCS?"
python -m src.ask "What was the revenue of TCS from FY 2024-25 to FY 2025-26?"
```

It is **one generic pipeline**. Revenue, net sales, total income, profit, loss, EBITDA, total expenses ... all go through the same code. Only the metric (taken from the question) changes. No LLM and no API key are needed.

## The problem

The same concept appears many times in an annual report with different values and labels: highlights, consolidated and standalone statements, five-year summaries, segments, notes, MD&A. Companies also name it differently (`Total net sales`, `Revenues`, `Revenue from operations`, `Value of Sales & Services (Revenue)` ...). Taking the first match gives confident but wrong answers: last year's column, a segment total, "Total income" that includes other income.

## Architecture

```
User question
   |  query.py        which metric? which company? which year(s)?
   v
Candidate pages       BM25 search + every page tagged as an income statement
   |
   v
Table rows            label + numbers per row, in page order      (statement_parser.py)
   |
   v
Match                 does the row label equal one of the metric's aliases?   (metrics.py)
   |
   v
Year column           year headings -> column of the requested year
   |
   v
Validate + rank       statement page, consolidated/standalone, arithmetic proof, year found
   |
   v
Answer + evidence     value, unit, year, statement, page, table lines, confidence, checks
```

| File | Job |
|---|---|
| `src/extract/metrics.py` | **Data only.** What each metric is called and where it lives. |
| `src/extract/engine.py` | The one generic pipeline: `extract_financial_metric(company, report, metric, year, scope)` |
| `src/extract/query.py` | Question -> metric, company, year(s) |
| `src/extract/statement_parser.py` | Page text -> rows; tests which rows are sums of other rows |
| `src/ask.py` | Ask a question; handles one year or a range of years |
| `src/ingest/`, `src/retrieve/` | PDF/Word reading, chunking, section tagging, BM25 search |

## How it tells the metrics apart

Each metric in `metrics.py` has:

- **aliases in tiers** (the first tier is strongest). A row label matches an alias only when the label **ends with** it (up to 5 extra words in front are allowed, e.g. `Total net revenues`). So `Net sales Products` is not `Net sales`, and `Profit before tax` is not `Profit for the year`.
- **exclude words**: a label with `cost`, `other`, `before`, `attributable` ... is never used for that metric.
- **region**: only the income lines (above the expenses) or the whole statement.

On top of the names, the **arithmetic is tested**: a total equals the sum of the rows above it on every year column (`Total income = Revenue from operations + Other income`). This tells a total from a part without trusting label names.

## Year, scope and units

- The year headings above the table are read into a year -> column map. The question's year picks the column. If that year is not in the table, the result is `YEAR_NOT_IN_REPORT`, never the neighbouring column. `FY 2025-26` means the column of 2026.
- Consolidated is preferred by default; ask for "standalone" to get the standalone figure.
- The unit is read as printed (`crore`, `million` ...) and returned with the value. Nothing is converted silently.
- A range question ("from FY 2023-24 to FY 2025-26") is answered year by year. For each year the report of that year is preferred; otherwise the comparative column of a later report is used.

## Ranking and "verification required"

Each candidate gets a score: alias tier + statement page + scope + arithmetic proof + year found. If two **different** values score almost the same (and one is not simply a part of the other), the answer is `VERIFY` with confidence LOW, and both candidates are shown. The system does not pick silently.

## Evidence returned

```
Value          : 2,67,021 crore INR
Financial Year : FY 2025-26 (from report FY26)
Statement      : Consolidated Statement of Profit and Loss
Scope          : consolidated
Source         : Annual Report FY26, page 167
Matched line   : Revenue from operations
Relevant table : columns: 2026 | 2025
   » Revenue from operations | 2,67,021 | 2,55,324
     Other income | 4,402 | 3,962
     Total income | 2,71,423 | 2,59,286
Confidence     : HIGH (score 9/11)  + the list of checks
```

## Adding a new metric (no code change)

Add one entry to `src/extract/metrics.py`:

```python
"finance_costs": {
    "names": ["finance costs", "finance cost", "interest expense"],
    "aliases": [["finance costs", "finance cost", "interest expense"]],
    "exclude_words": ["per", "share"],
    "region": "any",
    "pages": "statement",
},
```

Then `python -m src.ask "What were the finance costs of TCS?"` works. EBITDA is a metric like any other (`pages: "any"`, because it is often only in highlights or MD&A). If a report does not print an EBITDA line, the answer is NOT FOUND; the system does not derive it silently.

## Definition of "revenue" used (a choice you can change in `metrics.py`)

1. A line the company itself names plainly `Revenue` / `Revenues` / `Total revenue(s)` (e.g. Reliance's `Value of Sales & Services (Revenue)`).
2. Otherwise `Revenue from operations`.
3. Otherwise `Net sales` / `Turnover`.
4. TCS: if the notes have a verified `Revenue recognised` reconciliation within 1% of the statement, that value is used (`note_override`).

`Total income` (revenue + other income) is a separate metric, so asking for it gives it.

## Results

**Reports used to build the rules** (Apple, Google, Microsoft (Word file), Goldman Sachs, Infosys, Reliance, TCS): revenue correct for 7/7. These reports were also used to tune the rules, so this number is optimistic.

| Search setup | hit@1 | hit@5 |
|---|---|---|
| BM25 plain | 4/7 | 7/7 |
| BM25 + section boost | 7/7 | 7/7 |

**Reports the system had not seen** (values checked by hand in the PDF):

| Company | Revenue | Page | Correct? |
|---|---|---|---|
| Abbott | 6,929.05 crore INR | 179 | Yes |
| Cipla | 28,162.59 crore INR | 336 | Yes |
| Indigo | 849,619 million INR | 245 | Yes |
| Walmart | 713,163 million USD | 54 | Yes |
| RBI (central bank) | NOT FOUND | - | no line is named revenue; its income can be asked as *total income* |

**Tests:** `python -m tests.test_generic` runs 47 checks on made-up pages with the same layouts as the real reports (13 revenue layouts, 10+ other metrics, year and scope validation, ambiguity, natural-language questions).

## Failures found while testing (and the fix)

| Failure | Cause | Fix |
|---|---|---|
| Apple unit read as "thousand" | Header: "(In millions, except number of shares, which are reflected in thousands ...)" | Ignore unit words next to the word "share" |
| Wrong year column | Some reports list the oldest year first | Read the year headings and choose the column by year |
| Goldman Sachs sub-line chosen | "Non-interest revenues" appears before the headline "Total net revenues" | Prefer the row the arithmetic proves as a total |
| RBI returned a confident number | A schedule of agency charges summed to a "Total"; one label contained "turnover" | Aliases must match the label end; expense words disqualify a label; no alias match means NOT FOUND |
| Reliance gross vs net of GST | Both `Revenue` and `Revenue from Operations` are printed | Prefer the line the company calls Revenue (alias tiers) |
| TCS differs by 1 crore | Statement 2,67,021 vs reconciliation note 2,67,022 | `note_override`; both numbers are printed in the checks |
| A PDF "looked scanned" | The file was damaged, so no text could be read | `python -m src.ingest.check_pdf` tells damaged from scanned |

## Known limitations

- Scanned PDFs need OCR (not included). Word files have no real page numbers, so their page is approximate.
- Statement metrics (profit and loss items). Balance sheet and cash flow metrics need new entries and their own page tagging.
- The rules were tuned on a small set of reports; an unusual layout may be refused or wrong. The confidence, the checks and the evidence lines are there to catch it.
- A row needs at least two year columns to be read.

## How to run

```
pip install -r requirements.txt

# put reports in data/reports/ as Company_Year.pdf (or .docx), e.g. TCS_FY26.pdf
python -m src.ingest.run_ingest                      # read reports into data/processed/chunks.jsonl
python -m src.ask "What was the revenue of TCS in FY 2025-26?"
python -m src.pipeline                               # revenue for every report
python -m src.pipeline profit_after_tax              # same pipeline, another metric

python -m eval.evaluate                              # compare with data/ground_truth.csv
python -m eval.evaluate answers.csv                  # compare with any answer file
python -m eval.langsmith_eval                        # local metrics on eval/dataset.jsonl
python -m tests.test_generic                         # tests on made-up pages
python -m src.ingest.check_pdf                       # health check for every PDF
```

`ground_truth.csv` is the answer key for **testing only**; the extractor never reads it. Its values must come from a person reading the report, not from the system's output. Optional columns: `metric`, `scope`.

Confidence is an ordinal quality index (HIGH / MEDIUM / LOW), not a probability, and it does not use Cross-Encoder rerank scores. LangSmith tracing is optional: copy `.env.example` to `.env`, set `LANGSMITH_API_KEY`, then `python -m eval.langsmith_eval --upload`. Without a key the extractor still runs.

## Project structure

```
revenue-extractor/
├── README.md, requirements.txt
├── data/
│   ├── reports/            # your PDF / Word reports
│   ├── processed/          # chunks.jsonl, results_*.csv (created by the tools)
│   └── ground_truth.csv    # answer key for testing only
├── src/
│   ├── ask.py              # question in, answer card out
│   ├── pipeline.py         # one metric for every report
│   ├── ingest/             # PDF/Word reading, chunking, section tagging, checks
│   ├── retrieve/           # BM25 search
│   └── extract/            # metrics.py (data), engine.py, query.py, statement_parser.py
├── eval/                   # evaluation against answer files
└── tests/                  # made-up pages, many metrics, one pipeline
```

## Explaining it in an interview (about 5 minutes)

1. The user drops in an annual report; the system extracts text and tables and keeps page numbers.
2. The user asks a question. The system finds the **metric**, **company** and **year** in it.
3. It searches for candidate pages, then reads each table row by row: label, numbers, year columns.
4. A row is a candidate when its label is one of the metric's names. These names are **data**, not code.
5. Each candidate is validated: right year column, consolidated or standalone, unit, and an **arithmetic check** that totals really equal the sum of their parts.
6. Candidates are ranked. If two different values are equally plausible, the answer says "verification required" instead of guessing.
7. The answer comes with the value, unit, year, statement, page and the table lines it came from.
8. Revenue, net sales, total income, profit, loss, EBITDA all use the same pipeline. A new metric is one new entry in a config file.

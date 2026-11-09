"""Run:  python -m tests.test_generic

One pipeline, many metrics. Builds a small made-up corpus (same layouts as the real reports we
tested: TCS, Reliance, Infosys, Apple, Google, Goldman Sachs, Microsoft, Walmart ...) in a temporary
folder and checks the answers. No PDFs are needed."""
import csv
import json
import os
import sys
import tempfile

from src.ask import ask
from src.extract.engine import extract_financial_metric
from src.ingest.chunker import build_chunks
from src.ingest.section_tagger import tag_page

CORPUS = []


def add(company, label, page, text):
    CORPUS.extend(c.to_dict() for c in build_chunks(company, label, page, tag_page(text), text, []))


# ------------------------------------------------------------------ made-up reports
TCS_STMT = ("Consolidated Statement of Profit and Loss\n(H crores)\nNote\nYear ended\nMarch 31, 2026\nYear ended\nMarch 31, 2025\n"
            "Revenue from operations\n12\n2,67,021\n2,55,324\nOther income\n13\n4,402\n3,962\nTotal income\n2,71,423\n2,59,286\n"
            "Expenses\nEmployee benefit expense\n1,20,000\n1,10,000\nTotal expenses\n2,00,000\n1,90,000\n"
            "Profit before tax\n71,423\n69,286\nTax expense\n18,000\n17,000\nProfit for the year\n53,423\n52,286\n"
            "Earnings per share\n95.1\n90.2")
TCS_STAND = TCS_STMT.replace("Consolidated", "Standalone").replace("2,67,021", "2,20,938").replace("2,55,324", "2,14,853") \
    .replace("4,402", "3,000").replace("3,962", "2,000").replace("2,71,423", "2,23,938").replace("2,59,286", "2,16,853") \
    .replace("71,423", "43,938").replace("69,286", "46,853").replace("53,423", "30,000").replace("52,286", "31,000")
TCS_NOTE = ("Notes forming part of Consolidated Financial Statements\n(H crore)\nYear ended \nMarch 31, 2026\nYear ended \nMarch 31, 2025\n"
            "Contracted price\n 2,71,825\n2,59,076\nReductions towards variable consideration components\n (4,803)\n (3,752)\n"
            "Revenue recognised\n 2,67,022\n2,55,324\n\nReconciliation of revenue recognised with the contracted price is as follows:\n")
add("TCS", "FY26", 167, TCS_STMT)
add("TCS", "FY26", 253, TCS_STAND)
add("TCS", "FY26", 211, TCS_NOTE)

add("Reliance", "FY26", 101,
    "Consolidated Statement of Profit and Loss\nfor the year ended 31st March, 2026\n(\u20b9 in crore)\nNotes 2025-26 2024-25\nIncome\n"
    "Value of Sales 10,43,589 9,50,000\nIncome from Services 1,32,330 1,21,174\nValue of Sales & Services (Revenue) 11,75,919 10,71,174\n"
    "Less: GST Recovered 1,00,244 94,000\nRevenue from Operations 10,75,675 9,77,174\nOther Income 28,962 20,940\n"
    "Total Income 11,04,637 9,98,114\nExpenses:\nCost of materials consumed 5,00,000 4,50,000\nTotal Expenses 9,50,000 8,90,000\n"
    "Profit Before Tax 1,54,637\nNet Profit for the year 1,00,000\nBasic earnings per share 59.1")
add("Infosys", "FY26", 287,
    "Consolidated Statement of Profit and Loss\n(In ` crore, except equity share and per equity share data)\nParticulars\nNote\n"
    "Year ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n2.18\n1,78,650\n1,62,990\nOther income, net\n2.19\n4,322\n3,600\n"
    "Total income\n1,82,972\n1,66,590\nExpenses\nEmployee benefit expenses\n90,000\n80,000\nTotal expenses\n1,30,000\n1,20,000\n"
    "Profit before tax\n52,972\n46,000\nProfit for the year\n35,000\n31,000\nEarnings per equity share\n80.5\n72.0")
add("Apple", "FY25", 32,
    "Apple Inc.\nCONSOLIDATED STATEMENTS OF OPERATIONS\n(In millions, except number of shares, which are reflected in thousands, and per-share amounts)\n"
    "Years ended\nSeptember 27, 2025\nSeptember 28, 2024\nSeptember 30, 2023\nNet sales:\nProducts $ 307,003 $ 294,866 $ 298,085\n"
    "Services 109,158 96,169 85,200\nTotal net sales 416,161 391,035 383,285\nCost of sales:\nProducts 200,000 190,000 180,000\n"
    "Operating income 133,000 123,000 114,000\nNet income $ 112,000 $ 93,736 $ 96,995\nEarnings per share 7.5 6.1 6.1")
add("Google", "FY25", 50,
    "Alphabet Inc.\nCONSOLIDATED STATEMENTS OF INCOME\n(in millions, except per share amounts)\nYear Ended December 31,\n2023\n2024\n2025\n"
    "Revenues\n$\n307,394\n$\n350,018\n$\n402,836\nCosts and expenses:\nCost of revenues\n133,332\n146,306\n162,000\n"
    "Operating income\n84,293\n112,390\n130,000\nNet income\n73,795\n100,118\n120,000\nBasic net income per share\n5.84\n8.13\n9.9")
add("GoldmanSachs", "FY25", 160,
    "Consolidated Statements of Earnings\n$ in millions, except per share amounts\nYear Ended December\n2025\n2024\n2023\n"
    "Investment banking 7,000 6,000 5,000\nMarket making 20,000 18,000 17,000\nTotal non-interest revenues 27,000 24,000 22,000\n"
    "Interest income 40,000 35,000 30,000\nInterest expense 8,716 9,000 8,000\nNet interest income 31,284 26,000 22,000\n"
    "Total net revenues 58,284 50,000 44,000\nOperating expenses:\nCompensation and benefits 20,000 19,000 18,000\n"
    "Total operating expenses 33,100 31,200 30,000\nNet earnings 15,300 14,276 8,000\nDiluted earnings per common share 48.1")
add("Microsoft", "FY25", 35,
    "INCOME STATEMENTS\n(In millions, except per share amounts)\n$ Year Ended June 30, | 2025 | 2024 | 2023\n"
    "Product | 63,000 | 60,000 | 55,000\nService and other | 218,724 | 185,122 | 156,915\nTotal revenue | 281,724 | 245,122 | 211,915\n"
    "Total cost of revenue | 87,831 | 74,114 | 65,863\nOperating income | 128,528 | 109,433 | 88,523\nNet income | 101,832 | 88,136 | 72,361\n"
    "Diluted earnings per share | 13.64 | 11.80 | 9.68")
add("Walmart", "FY26", 54,
    "Consolidated Statements of Income\n(Amounts in millions, except per share data) $\nFiscal Years Ended January 31, 2026 2025 2024\n"
    "Revenues:\nNet sales $ 706,000 $ 674,000 $ 642,000\nMembership and other income 7,163 6,985 6,125\nTotal revenues 713,163 680,985 648,125\n"
    "Costs and expenses:\nCost of sales 540,000 510,000 480,000\nOperating income 29,000 27,000 26,000\n"
    "Consolidated net income 20,000 19,000 18,000\nNet income per share 2.5 2.4 2.3")
add("GuardCo", "FY26", 10,
    "Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n3\n50,000\n45,000\n"
    "Other income\n4\n1,200\n900\nTotal income\n51,200\n45,900\nExpenses\nTotal expenses\n40,000\n37,000\nProfit before tax\n11,200\nProfit for the year\n8,000\nEarnings per equity share\n12.1")
add("SplitCo", "FY26", 12,
    "Statement of Profit and Loss\n(Rs. in lakhs)\nParticulars\nFor the year ended 31 March 2026\nFor the year ended 31 March 2025\n"
    "Sale of products 8,000 7,000\nSale of services 2,000 1,800\nRevenue from operations 10,000 8,800\nOther income 500 400\nTotal income 10,500 9,200\n"
    "Expenses\nPurchases of stock-in-trade 5,000 4,500\nTotal expenses 8,000 7,000\nProfit before tax 2,500\nNet profit for the year 1,800\nEarnings per share 5.1")
add("RetailCo", "FY25", 40,
    "CONSOLIDATED STATEMENTS OF INCOME\n(in millions, except per share data)\nFiscal Year Ended\nJanuary 31, 2026\nJanuary 31, 2025\n"
    "Net sales $ 640,000 $ 600,000\nMembership fees 6,000 5,500\nTotal revenue 646,000 605,500\nCost of sales 480,000 450,000\n"
    "Operating income 30,000 28,000\nNet income 20,000 18,000\nDiluted earnings per share 4.5")
add("RBI", "FY26", 203,   # central bank: no revenue-named line, only schedules of expenses
    "Schedule\nOther Income and Interest\n(\u20b9 crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\n"
    "Schedule Agency Charges i Agency Commission on Government Transactions 3,531.76 4,000.00\nii Underwriting Commission paid to the Primary Dealers 15.78 20.05\n"
    "iii Sundries Handling charges and turnover commission paid to banks for Relief Savings Bonds subscriptions SBLA etc 6.47 10.00\n"
    "iv Fees paid to the External Asset Managers Custodians Brokers etc 115.55 850.00\nTotal 3,669.56 4,880.05\n")
add("LossCo", "FY26", 20,
    "Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n10,000\n9,000\n"
    "Other income\n500\n400\nTotal income\n10,500\n9,400\nExpenses\nTotal expenses\n15,500\n12,400\nLoss before tax\n(5,000)\n(3,000)\n"
    "Loss for the year\n(5,000)\n(3,000)\nEarnings per share\n(5.0)\n(3.0)")
add("EbitdaCo", "FY26", 8, "Financial Highlights\n(Rs. in crore)\n2026 2025\nRevenue 50,000 45,000\nEBITDA 12,000 10,500\nNet income 6,000 5,000\n")
add("EbitdaCo", "FY26", 30,
    "Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n50,000\n45,000\n"
    "Other income\n1,000\n900\nTotal income\n51,000\n45,900\nExpenses\nTotal expenses\n40,000\n36,000\nProfit before tax\n11,000\nProfit for the year\n6,000\nEarnings per share\n6.0")
for label, cur, prev, ocur, oprev in (("FY26", "1,20,000", "1,00,000", "2,000", "1,500"), ("FY25", "99,500", "90,000", "1,500", "1,200")):
    y1, y0 = (2026, 2025) if label == "FY26" else (2025, 2024)
    tot = lambda a, b: f"{int(a.replace(',', '')) + int(b.replace(',', '')):,}"
    add("SeriesCo", label, 5,
        f"Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, {y1}\nYear ended March 31, {y0}\n"
        f"Revenue from operations\n{cur}\n{prev}\nOther income\n{ocur}\n{oprev}\nTotal income\n{tot(cur, ocur)}\n{tot(prev, oprev)}\n"
        f"Expenses\nTotal expenses\n80,000\n70,000\nProfit before tax\n20,000\nProfit for the year\n15,000\nEarnings per share\n9.0")
add("AmbigCo", "FY26", 10,
    "Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n5,000\n4,000\n"
    "Other income\n100\n90\nTotal income\n5,100\n4,090\nExpenses\nTotal expenses\n4,000\n3,000\nProfit before tax\n1,100\nProfit for the year\n800\nEarnings per share\n1.0")
add("AmbigCo", "FY26", 12,
    "Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n5,200\n4,100\n"
    "Other income\n100\n90\nTotal income\n5,300\n4,190\nExpenses\nTotal expenses\n4,000\n3,000\nProfit before tax\n1,300\nProfit for the year\n900\nEarnings per share\n1.1")


# ------------------------------------------------------------------ improvement tests: banks, signs, rules
add("CentralBank", "FY26", 150,
    "Income Statement for the year ended March 31, 2026\n(\u20b9 crore)\nNote\n2025-26\n2024-25\nIncome\nInterest\n2,10,687\n1,95,000\n"
    "Other Income\n1,27,620\n1,10,000\nTotal Income\n3,38,307\n3,05,000\nExpenditure\nPrinting of notes\n6,000\n5,500\nAgency charges\n3,669\n4,880\n"
    "Employee cost\n9,000\n8,000\nTotal Expenditure\n25,000\n22,000\nSurplus transferred to the Government\n3,13,307\n2,83,000")
add("BankCo", "FY26", 90,
    "Profit and Loss Account for the year ended March 31, 2026\n(\u20b9 in crore)\nSchedule\nYear ended March 31, 2026\nYear ended March 31, 2025\n"
    "I. INCOME\nInterest earned\n1,00,000\n90,000\nOther income\n20,000\n15,000\nTotal Income\n1,20,000\n1,05,000\nII. EXPENDITURE\nInterest expended\n55,000\n50,000\n"
    "Operating expenses\n20,000\n18,000\nProvisions and contingencies\n5,000\n4,000\nTotal Expenditure\n80,000\n72,000\nIII. PROFIT\nNet profit for the year\n40,000\n33,000")
add("MinusCo", "FY26", 20,
    "Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n10,000\n9,000\n"
    "Other income\n500\n400\nTotal income\n10,500\n9,400\nExpenses\nTotal expenses\n15,500\n12,400\nLoss before tax\n-5,000\n-3,000\nLoss for the year\n-5,000\n-3,000\nEarnings per share\n-5.0\n-3.0")
_STMT = ("Consolidated Statement of Profit and Loss\n(Rs. in crore)\nYear ended March 31, 2026\nYear ended March 31, 2025\nRevenue from operations\n{rev}\n"
         "Other income\n1,000\n900\nTotal income\n{ti}\n{ti0}\nExpenses\nTotal expenses\n{te}\n{te0}\nProfit before tax\n{pbt}\n{pbt0}\n"
         "Tax expense\n{tax}\n{tax0}\nProfit for the year\n{pat}\n{pat0}\nEarnings per share\n5.0\n4.0")
add("YoyCo", "FY26", 5, _STMT.format(rev="5,00,000\n5,000", ti="5,01,000", ti0="5,900", te="4,00,000", te0="4,000", pbt="1,01,000", pbt0="1,900", tax="20,000", tax0="500", pat="81,000", pat0="1,400"))
add("NegRevCo", "FY26", 5, _STMT.format(rev="(11,000)\n(9,000)", ti="(10,000)", ti0="(8,000)", te="500", te0="400", pbt="400", pbt0="300", tax="100", tax0="80", pat="300", pat0="220"))
add("BadIdentityCo", "FY26", 5, _STMT.format(rev="50,000\n45,000", ti="51,000", ti0="45,900", te="40,000", te0="36,000", pbt="9,000", pbt0="9,900", tax="2,000", tax0="2,500", pat="7,000", pat0="7,400"))
add("StandCo", "FY26", 5, _STMT.format(rev="50,000\n45,000", ti="51,000", ti0="45,900", te="40,000", te0="36,000", pbt="11,000", pbt0="9,900", tax="2,000", tax0="2,500", pat="9,000", pat0="7,400").replace("Consolidated", "Standalone"))
add("NoYearCo", "FY26", 5, _STMT.format(rev="50,000\n45,000", ti="51,000", ti0="45,900", te="40,000", te0="36,000", pbt="11,000", pbt0="9,900", tax="2,000", tax0="2,500", pat="9,000", pat0="7,400")
    .replace("Year ended March 31, 2026\nYear ended March 31, 2025\n", ""))

# ------------------------------------------------------------------ the checks
failures = []


def check(name, got, expected):
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name:<58} {got!s:<28}" + ("" if ok else f" expected {expected}"))
    if not ok:
        failures.append(name)


def val(company, report, metric, **kw):
    r = extract_financial_metric(company, report, metric, **kw)
    return r["value"] if r["status"] in ("OK", "VERIFY") else r["status"]


def run():
    print("--- revenue: same pipeline, every layout ---")
    for co, rep, exp in [("TCS", "FY26", "2,67,022"), ("Reliance", "FY26", "11,75,919"), ("Infosys", "FY26", "1,78,650"),
                         ("Apple", "FY25", "416,161"), ("Google", "FY25", "402,836"), ("GoldmanSachs", "FY25", "58,284"),
                         ("Microsoft", "FY25", "281,724"), ("Walmart", "FY26", "713,163"), ("GuardCo", "FY26", "50,000"),
                         ("SplitCo", "FY26", "10,000"), ("RetailCo", "FY25", "646,000"),
                         ("RBI", "FY26", "NOT_FOUND"), ("LossCo", "FY26", "10,000")]:
        check(f"revenue  {co}", val(co, rep, "revenue"), exp)

    print("--- other metrics: SAME function, only the metric changes ---")
    for metric, exp in [("revenue_from_operations", "2,67,021"), ("net_sales", "2,67,021"), ("total_income", "2,71,423"),
                        ("other_income", "4,402"), ("total_expenses", "2,00,000"), ("profit_before_tax", "71,423"),
                        ("profit_after_tax", "53,423"), ("operating_profit", "NOT_FOUND"), ("ebitda", "NOT_FOUND")]:
        check(f"TCS {metric}", val("TCS", "FY26", metric), exp)
    check("Apple net_sales", val("Apple", "FY25", "net_sales"), "416,161")
    check("Apple profit_after_tax (Net income)", val("Apple", "FY25", "profit_after_tax"), "112,000")
    check("Google profit_after_tax (latest column is LAST)", val("Google", "FY25", "profit_after_tax"), "120,000")
    check("Google operating_profit", val("Google", "FY25", "operating_profit"), "130,000")
    check("GoldmanSachs profit_after_tax (Net earnings)", val("GoldmanSachs", "FY25", "profit_after_tax"), "15,300")
    check("Walmart net_sales (not Total revenues)", val("Walmart", "FY26", "net_sales"), "706,000")
    check("Walmart profit_after_tax (Consolidated net income)", val("Walmart", "FY26", "profit_after_tax"), "20,000")
    check("Reliance total_income", val("Reliance", "FY26", "total_income"), "11,04,637")
    check("Reliance revenue_from_operations", val("Reliance", "FY26", "revenue_from_operations"), "10,75,675")
    check("Infosys other_income (label 'Other income, net')", val("Infosys", "FY26", "other_income"), "4,322")
    check("LossCo profit_before_tax (label 'Loss before tax')", val("LossCo", "FY26", "profit_before_tax"), "(5,000)")
    r = extract_financial_metric("LossCo", "FY26", "net_loss")
    check("LossCo net_loss value", r["value"], "(5,000)")
    check("LossCo net_loss meaning", (r["interpretation"] or "")[:14], "loss of 5,000")
    check("TCS net_loss (no loss)", (extract_financial_metric("TCS", "FY26", "net_loss")["interpretation"] or "")[:7], "no loss")
    check("EbitdaCo ebitda (from highlights page)", val("EbitdaCo", "FY26", "ebitda"), "12,000")

    print("--- year and scope validation ---")
    check("TCS revenue_from_operations FY 2025-26 -> 2026 column", val("TCS", "FY26", "revenue_from_operations", year=2026), "2,67,021")
    check("TCS revenue_from_operations FY 2024-25 -> 2025 column", val("TCS", "FY26", "revenue_from_operations", year=2025), "2,55,324")
    check("TCS revenue_from_operations FY 2021-22 -> not in this report", val("TCS", "FY26", "revenue_from_operations", year=2022), "YEAR_NOT_IN_REPORT")
    check("Google profit_after_tax FY2024 (middle column)", val("Google", "FY25", "profit_after_tax", year=2024), "100,118")
    check("TCS standalone revenue_from_operations", val("TCS", "FY26", "revenue_from_operations", scope="standalone"), "2,20,938")
    check("TCS consolidated revenue_from_operations", val("TCS", "FY26", "revenue_from_operations", scope="consolidated"), "2,67,021")

    print("--- ambiguity: two equally plausible different values -> VERIFY ---")
    r = extract_financial_metric("AmbigCo", "FY26", "revenue_from_operations")
    check("AmbigCo status", r["status"], "VERIFY")
    check("AmbigCo confidence", r["confidence"], "LOW")

    print("--- natural-language questions (the metric comes from the question) ---")
    def asked(q):
        out = ask(q)
        return out.get("error", None) or [(r["value"], r["year"]) for r in out["results"]]
    check("What was the revenue of TCS in FY 2025-26?", asked("What was the revenue of TCS in FY 2025-26?"), [("2,67,022", 2026)])
    check("What were the net sales of Apple in FY 2025?", asked("What were the net sales of Apple in FY 2025?"), [("416,161", 2025)])
    check("What was the total income of TCS?", asked("What was the total income of TCS?"), [("2,71,423", 2026)])
    check("What was the profit after tax of Infosys in FY26?", asked("What was the profit after tax of Infosys in FY26?"), [("35,000", 2026)])
    check("What was the net loss of LossCo?", asked("What was the net loss of LossCo?"), [("(5,000)", 2026)])
    check("What was EBITDA of EbitdaCo in FY 2025-26?", asked("What was EBITDA of EbitdaCo in FY 2025-26?"), [("12,000", 2026)])
    check("What were the total expenses of Goldman Sachs?", asked("What were the total expenses of Goldman Sachs?"), [("33,100", 2025)])
    check("What was the standalone revenue from operations of TCS?", asked("What was the standalone revenue from operations of TCS?"), [("2,20,938", 2026)])
    check("Revenue for each year FY 2023-24 to FY 2025-26 (two reports)",
          asked("What was the revenue of SeriesCo from FY 2023-24 to FY 2025-26?"),
          [("90,000", 2024), ("99,500", 2025), ("1,20,000", 2026)])
    check("unknown metric -> clear error", str(asked("What was the weather of TCS?")).startswith("I could not tell which metric"), True)
    check("unknown company -> clear error", str(asked("What was the revenue of Zzz Corp?")).startswith("I could not tell which company"), True)


def run_improvements(root):
    import contextlib
    import io
    import runpy
    from src.extract.query import find_years, label_year
    from src.retrieve.bm25_search import ReportNotFound

    print("--- banks and central banks: revenue falls back to Total income, and says so ---")
    r = extract_financial_metric("CentralBank", "FY26", "revenue")
    check("CentralBank revenue -> total income", r["value"], "3,38,307")
    check("  answered with", r["metric_used"], "total_income")
    check("  confidence is capped (never HIGH for a fallback)", r["confidence"], "MEDIUM")
    check("CentralBank total_income", val("CentralBank", "FY26", "total_income"), "3,38,307")
    check("CentralBank total_expenses (label 'Total Expenditure')", val("CentralBank", "FY26", "total_expenses"), "25,000")
    check("RBI schedule page still NOT_FOUND (expenses are not revenue)", val("RBI", "FY26", "revenue"), "NOT_FOUND")
    check("BankCo interest_income (label 'Interest earned')", val("BankCo", "FY26", "interest_income"), "1,00,000")
    check("BankCo revenue -> total income", val("BankCo", "FY26", "revenue"), "1,20,000")
    check("BankCo profit_after_tax (label 'Net profit for the year')", val("BankCo", "FY26", "profit_after_tax"), "40,000")
    check("A company WITH a revenue line is not affected by the fallback", extract_financial_metric("TCS", "FY26", "revenue")["metric_used"], None)

    print("--- numbers: minus signs, units ---")
    check("MinusCo net_loss written as -5,000", val("MinusCo", "FY26", "net_loss"), "-5,000")
    check("  meaning", (extract_financial_metric("MinusCo", "FY26", "net_loss")["interpretation"] or "")[:14], "loss of 5,000")
    check("TCS value normalised (crore -> absolute)", extract_financial_metric("TCS", "FY26", "revenue_from_operations")["value_absolute"], 267021 * 1e7)
    check("Apple value normalised (million -> absolute)", extract_financial_metric("Apple", "FY25", "revenue")["value_absolute"], 416161 * 1e6)

    print("--- business rules: uncertain answers must say VERIFY ---")
    r = extract_financial_metric("YoyCo", "FY26", "revenue")
    check("100x year-on-year jump -> VERIFY", r["status"], "VERIFY")
    check("  reason mentions year-on-year", "year-on-year" in r["message"], True)
    check("  confidence", r["confidence"], "LOW")
    r = extract_financial_metric("NegRevCo", "FY26", "revenue")
    check("negative revenue -> VERIFY", (r["status"], "not positive" in r["message"]), ("VERIFY", True))
    check("loss-making company: PAT is the (negative) loss and is NOT questioned", (val("MinusCo", "FY26", "profit_after_tax"), extract_financial_metric("MinusCo", "FY26", "profit_after_tax")["status"]), ("-5,000", "OK"))

    print("--- arithmetic across lines ---")
    r = extract_financial_metric("TCS", "FY26", "profit_before_tax")
    check("TCS PBT = Total income - Total expenses verified", any("identity verified" in x for x in r["reasons"]), True)
    check("  confidence", r["confidence"], "HIGH")
    r = extract_financial_metric("TCS", "FY26", "profit_after_tax")
    check("TCS PAT = PBT - Tax expense verified", any("Profit after tax = Profit before tax" in x for x in r["reasons"]), True)
    r = extract_financial_metric("BadIdentityCo", "FY26", "profit_before_tax")
    check("PBT that does not reconcile is flagged", any("identity not reconciled" in x for x in r["reasons"]), True)
    check("  and cannot be HIGH", r["confidence"], "MEDIUM")
    check("  but is not VERIFY (exceptional items can explain it)", r["status"], "OK")

    print("--- confidence caps with reasons ---")
    r = extract_financial_metric("StandCo", "FY26", "revenue")
    check("only a standalone statement exists, consolidated asked -> capped", (r["confidence"], r["scope"]), ("MEDIUM", "standalone"))
    r = extract_financial_metric("NoYearCo", "FY26", "revenue")
    check("no year headings -> capped, reason given", (r["confidence"], any("year headings not found" in w for w in r["warnings"])), ("MEDIUM", True))
    check("every answer lists its reasons", len(extract_financial_metric("Apple", "FY25", "revenue")["reasons"]) >= 6, True)
    r = extract_financial_metric("Apple", "FY25", "revenue")
    check("confidence_score is a quality index in [0, 1]", 0 < (r.get("confidence_score") or 0) <= 1, True)
    check("rerank_score is not a confidence signal", "rerank_score" not in (r.get("confidence_signals") or {}), True)
    check("confidence_baseline is recorded", r.get("confidence_baseline") in ("HIGH", "MEDIUM", "LOW"), True)

    print("--- years and errors ---")
    check("find_years FY 2025-2026 -> 2026", find_years("revenue in FY 2025-2026")[0], [2026])
    check("find_years from FY 2021-22 to FY 2025-26", find_years("from FY 2021-22 to FY 2025-26")[0], [2022, 2023, 2024, 2025, 2026])
    check("label_year 2025-2026", label_year("2025-2026"), 2026)
    try:
        extract_financial_metric("Nope", "FY26", "revenue")
        raised = "no error"
    except ReportNotFound:
        raised = "ReportNotFound"
    check("missing report -> clear ReportNotFound", raised, "ReportNotFound")

    print("--- batch output (CSV and Excel) ---")
    from src.ask import run_all
    from src.extract.query import list_reports
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_all("revenue,profit_after_tax")
    n_expected = len(list_reports()) * 2
    with open("data/processed/extraction_results.csv", encoding="utf-8-sig") as f:
        n_csv = len(list(csv.DictReader(f)))
    check("CSV has one row per report x metric", n_csv, n_expected)
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("SKIP  Excel checks (openpyxl not installed)")
    else:
        wb = load_workbook("data/processed/extraction_results.xlsx")
        check("Excel has Results + Summary sheets", wb.sheetnames, ["Results", "Summary"])
        check("Excel Results rows", wb["Results"].max_row - 1, n_expected)

    print("--- evaluation report ---")
    with open("data/ground_truth.csv", "w", encoding="utf-8") as f:
        f.write("company,year,metric,total_revenue,unit\nTCS,FY26,revenue,267022,crore\nApple,FY25,revenue,416161,million\n"
                "CentralBank,FY26,revenue,338307,crore\nRBI,FY26,revenue,NONE,\nGoogle,FY25,revenue,999999,million\n")
    buf, argv = io.StringIO(), sys.argv
    sys.argv = ["evaluate"]
    try:
        import eval.evaluate as ev
        with contextlib.redirect_stdout(buf):
            ev.main()
    finally:
        sys.argv = argv
    out = buf.getvalue()
    check("evaluation: accuracy line", "ACCURACY  4/5 correct (80%)" in out, True)
    check("evaluation: accuracy by metric and by confidence", ("By metric" in out and "By confidence" in out), True)
    check("evaluation: the wrong row is listed under FAILURES", ("FAILURES" in out and "Google" in out.split("FAILURES")[1]), True)
    check("evaluation: report file saved", os.path.exists("data/processed/evaluation_report.csv"), True)


if __name__ == "__main__":
    root = os.getcwd()
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "data", "processed"))
    with open(os.path.join(tmp, "data", "processed", "chunks.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in CORPUS))
    os.chdir(tmp)
    sys.path.insert(0, root)
    run()
    run_improvements(root)
    print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)

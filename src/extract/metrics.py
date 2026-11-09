
"""Metric definitions = DATA, not code. To add a metric, add one entry below.

Fields
  names          Phrases a user may type in a question.
  aliases        Tiers of report labels, strongest first. A row label matches
                 when it ends with an alias, allowing up to 5 extra words in front.
  exclude_words  A label containing any of these words is never used.
  region         "income" = income lines above expenses; "any" = whole statement.
  pages          "statement" = income statement/highlights; "any" = any page.
  search_terms   Extra words for BM25 search.
  fallback       Alternative metric and reason when no matching row is found.
  positive_only  A value <= 0 is suspicious and should be verified.
  volatile       Profit-like metric; skips the large year-on-year swing check.
  negative_is    Meaning of a negative value.
  note_override  A verified note value can replace the statement value.
"""

_REVENUE_EXCLUDE = [
    "other", "cost", "less", "deferred", "unearned",
    "expense", "expenses", "gain", "profit", "loss", "non",
]

_PROFIT_EXCLUDE = [
    "comprehensive", "attributable", "per", "share", "before", "other",
]

_PAT_ALIASES = [
    [
        "profit after tax",
        "profit after taxation",
        "net profit after tax",
        "pat",
    ],
    [
        "profit for the year",
        "profit for the period",
        "net profit for the year",
        "net profit for the period",
        "profit loss for the year",
        "profit loss for the period",
        "net income",
        "net income loss",
        "net earnings",
        "net profit",
        "loss for the year",
        "loss for the period",
        "net loss",
        "loss after tax",
    ],
]

METRICS = {
    "revenue": {
        "names": [
            "revenue",
            "revenues",
            "sales",
            "total sales",
            "revenue from operations",
            "premium earned",
            "discount earned",
        ],
        "aliases": [
            # Tier 1: Direct revenue labels
            [
                "total revenue",
                "total revenues",
                "total net revenues",
                "total revenues net of interest expense",
                "total revenue net of interest expense",
                "net revenue",
                "net revenues",
                "revenue",
                "revenues",
                "total operating revenues",
                "operating revenues",
                "operating revenue",
            ],
            # Tier 2: Industry-specific revenue labels
            [
                "revenue from operations",
                "revenue from operation",
                "revenues from operations",
                "total revenue from operations",
                "revenue from contracts with customers",
                "revenue from contract with customers",
                "sales and services",
                "value of sales and services(Revenue)"
                "total net sales",
                "net sales",
                "total sales",
                "sales",
                "turnover",
                "gross revenue",
                "insurance revenue",
                "revenue from insurance contracts",
                "premium earned",
                "premiums earned",
                "gross premium earned",
                "net premium earned",
                "premium income",
                "net premium income",
                "gross premium income",
                "discount earned",
                "discounts earned",
                "commission earned",
                "commissions earned",
                "fee income",
                "fees earned",
                "service income",
                "income from services",
            ],
        ],
        "exclude_words": _REVENUE_EXCLUDE,
        # Permit this exact financial-services headline while excluding expense rows.
        "allowed_excluded_aliases": [
            "total revenues net of interest expense",
            "total revenue net of interest expense",
        ],
        "region": "income",
        "pages": "statement",
        "positive_only": True,
        "search_terms": [
            "total income",
            "total revenues net of interest expense",
            "total revenue net of interest expense",
            "interest earned",
            "premium earned",
            "discount earned",
            "commission income",
            "fee income",
            "income from operations",
        ],
        "fallback": {
            "metric": "total_income",
            "reason": (
                "No matching revenue line was found; banks and some financial "
                "institutions report Total income instead."
            ),
        },
        "note_override": {
            "regex": r"revenue recogni[sz]ed",
            "tolerance": 0.01,
        },
    },

    "revenue_from_operations": {
        "names": [
            "revenue from operations",
            "revenues from operations",
            "operating income from sales",
        ],
        "aliases": [
            [
                "revenue from operations",
                "revenues from operations",
                "total revenue from operations",
                "revenue from operations net",
            ],
        ],
        "exclude_words": _REVENUE_EXCLUDE,
        "region": "income",
        "pages": "statement",
        "positive_only": True,
    },

    "net_sales": {
        "names": ["net sales", "sales", "total sales"],
        "aliases": [
            ["net sales", "total net sales", "net sale"],
            ["sales", "total sales"],
            [
                "revenue from operations",
                "revenues from operations",
                "total revenue from operations",
            ],
        ],
        "exclude_words": _REVENUE_EXCLUDE,
        "region": "income",
        "pages": "statement",
        "positive_only": True,
    },

    "total_income": {
        "names": [
            "total income",
            "total interest income",
            "income from banking operations",
            "income from insurance operations",
        ],
        "aliases": [
            # Tier 1: Direct total-income labels
            [
                "total income",
                "total revenue and other income",
                "total revenues and other income",
                "total income from operations",
                "total income from banking operations",
                "total income from insurance operations",
            ],
            # Tier 2: Banking, RBI, and insurance labels
            [
                "total interest income",
                "total interest earned",
                "interest and dividend income",
                "income from interest",
                "income from investments",
                "income from loans and advances",
                "income from banking operations",
                "income from financial operations",
                "discount earned",
                "discounts earned",
                "discount and commission",
                "bill discounting income",
                "commission and exchange",
                "commission income",
                "fees and commission income",
                "fee and commission income",
                "premium earned",
                "premiums earned",
                "premium income",
                "net premium income",
                "gross premium income",
                "income from insurance operations",
                "income from insurance contracts",
                "investment income",
                "other operating income",
            ],
        ],
        "exclude_words": [
            "tax",
            "comprehensive",
            "per",
            "share",
        ],
        "region": "income",
        "pages": "statement",
        "positive_only": True,
        "search_terms": [
            "interest earned",
            "total interest income",
            "discount earned",
            "premium earned",
            "commission and exchange",
            "investment income",
            "other income",
        ],
    },

    "other_income": {
        "names": ["other income"],
        "aliases": [
            ["other income", "other income net", "other income and gains"],
        ],
        "exclude_words": ["comprehensive", "tax", "per", "share"],
        "region": "income",
        "pages": "statement",
        "volatile": True,
    },

    "interest_income": {
        "names": ["interest income", "interest earned"],
        "aliases": [
            [
                "interest earned",
                "total interest income",
                "interest income",
                "income from interest",
            ],
        ],
        "exclude_words": ["net", "per", "share", "expense", "expended", "on"],
        "region": "income",
        "pages": "statement",
        "positive_only": True,
    },

    "net_interest_income": {
        "names": ["net interest income", "nii"],
        "aliases": [["net interest income"]],
        "exclude_words": ["per", "share"],
        "region": "any",
        "pages": "statement",
        "volatile": True,
    },

    "total_expenses": {
        "names": ["total expenses", "total expenditure", "total costs"],
        "aliases": [
            [
                "total expenses",
                "total expenditure",
                "total costs and expenses",
                "total operating expenses",
                "total costs",
            ],
        ],
        "exclude_words": ["tax", "comprehensive", "per", "share"],
        "region": "any",
        "pages": "statement",
        "positive_only": True,
    },

    "finance_costs": {
        "names": [
            "finance costs",
            "finance cost",
            "interest expense",
            "interest expended",
        ],
        "aliases": [
            [
                "finance costs",
                "finance cost",
                "interest expense",
                "interest expended",
                "interest and finance charges",
            ],
        ],
        "exclude_words": ["income", "per", "share"],
        "region": "any",
        "pages": "statement",
    },

    "tax_expense": {
        "names": [
            "tax expense",
            "income tax expense",
            "total tax expense",
            "provision for tax",
        ],
        "aliases": [
            [
                "tax expense",
                "total tax expense",
                "income tax expense",
                "provision for taxation",
                "provision for income taxes",
                "provision for tax",
            ],
        ],
        "exclude_words": ["deferred", "per", "share", "before"],
        "region": "any",
        "pages": "statement",
        "volatile": True,
    },

    "operating_profit": {
        "names": ["operating profit", "operating income"],
        "aliases": [
            ["operating profit", "operating income", "profit from operations"],
        ],
        "exclude_words": ["comprehensive", "per", "share", "other"],
        "region": "any",
        "pages": "statement",
        "volatile": True,
    },

    "profit_before_tax": {
        "names": [
            "profit before tax",
            "pbt",
            "pre tax profit",
            "pretax profit",
            "income before tax",
        ],
        "aliases": [
            [
                "profit before tax",
                "profit before taxation",
                "profit before income tax",
                "loss before tax",
                "profit loss before tax",
                "income before income taxes",
                "income before taxes",
                "earnings before income taxes",
                "pre tax profit",
                "pre tax earnings",
                "pretax income",
            ],
        ],
        "exclude_words": ["comprehensive", "per", "share", "exceptional"],
        "region": "any",
        "pages": "statement",
        "volatile": True,
    },

    "profit_after_tax": {
        "names": [
            "profit after tax",
            "pat",
            "net profit",
            "net income",
            "net earnings",
            "profit for the year",
            "profit",
        ],
        "aliases": _PAT_ALIASES,
        "exclude_words": _PROFIT_EXCLUDE,
        "region": "any",
        "pages": "statement",
        "volatile": True,
    },

    "net_loss": {
        "names": [
            "net loss",
            "loss for the year",
            "loss after tax",
            "loss",
        ],
        "aliases": [
            ["net loss", "loss for the year", "loss for the period", "loss after tax"],
        ] + _PAT_ALIASES,
        "exclude_words": _PROFIT_EXCLUDE,
        "region": "any",
        "pages": "statement",
        "volatile": True,
        "negative_is": "loss",
    },

    "ebitda": {
        "names": ["ebitda"],
        "aliases": [
            [
                "ebitda",
                "operating ebitda",
                "adjusted ebitda",
                "consolidated ebitda",
                "earnings before interest tax depreciation and amortisation",
                "earnings before interest taxes depreciation and amortization",
            ],
        ],
        "exclude_words": ["margin", "per", "share"],
        "region": "any",
        "pages": "any",
        "volatile": True,
    },

    "employee_cost": {
        "names": [
            "employee cost",
            "employee benefit expense",
            "staff costs",
        ],
        "aliases": [
            [
                "employee benefit expense",
                "employee benefits expense",
                "employee benefit expenses",
                "employee benefits expenses",
                "employee cost",
                "employee costs",
                "staff costs",
                "personnel expenses",
                "compensation and benefits",
                "salaries and wages",
            ],
        ],
        "exclude_words": ["per", "share"],
        "region": "any",
        "pages": "statement",
        "positive_only": True,
    },
}


# Cross-line arithmetic. A passing identity provides extra evidence.
# A mismatch is a warning, since legitimate adjustments can explain differences.
IDENTITIES = [
    {
        "name": "Total income = Revenue from operations + Other income",
        "result": "total_income",
        "terms": [
            ("revenue_from_operations", 1),
            ("other_income", 1),
        ],
    },
    {
        "name": "Profit before tax = Total income - Total expenses",
        "result": "profit_before_tax",
        "terms": [
            ("total_income", 1),
            ("total_expenses", -1),
        ],
    },
    {
        "name": "Profit after tax = Profit before tax - Tax expense",
        "result": "profit_after_tax",
        "terms": [
            ("profit_before_tax", 1),
            ("tax_expense", -1),
        ],
    },
]

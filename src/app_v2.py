import sys
import os

# Add project root directory to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
import pandas as pd
from src.ask import ask
from src.extract.engine import extract_financial_metric
from src.extract.query import list_reports
from src.extract.metrics import METRICS
from src.extract.ratios import DERIVED_METRICS, calculate_derived_metric
from src.extract.auditor import run_portfolio_audit

st.set_page_config(
    page_title="Financial Intelligence Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📊 Financial Metric Extractor & Intelligence Suite")
st.caption("Zero-hallucination, deterministic financial extraction with automated audit verification")

# Sidebar navigation
st.sidebar.header("Navigation")
view_mode = st.sidebar.radio(
    "Select Workflow",
    [
        "🔍 Question & Extraction",
        "📐 Financial Ratios",
        "📊 Peer Benchmarking",
        "🛡️ Portfolio Integrity Auditor"
    ]
)

# Fetch available reports and metrics
try:
    available_reports = list_reports()
    available_companies = sorted(list({c for c, _ in available_reports}))
except Exception:
    available_reports = []
    available_companies = []

# -------------------------------------------------------------------
# VIEW 1: Question & Extraction
# -------------------------------------------------------------------

if view_mode == "🔍 Question & Extraction":
    st.subheader("Natural Language Metric Query")

    col_q, col_btn = st.columns([4, 1])
    with col_q:
        question = st.text_input(
            "Enter financial query", 
            value="What was the revenue of TCS in FY 2025-26?",
            help="Supports specific company, metric, and financial year requests."
        )
    with col_btn:
        st.write(" ")
        st.write(" ")
        run_query = st.button("Query Engine", type="primary", use_container_width=True)

    if run_query and question:
        try:
            out = ask(question)
        except Exception as e:
            st.error(f"Error querying backend: {e}")
            out = {"results": []}

        if "error" in out:
            st.error(out["error"])

        results = out.get("results", [])
        if not results:
            st.warning("No metrics could be extracted for this query.")

        for r in results:
            name = r["metric"].replace("_", " ").title()
            
            if r["status"] in ("NOT_FOUND", "YEAR_NOT_IN_REPORT"):
                st.warning(f"**{r['company']} - {name}**: NOT FOUND ({r['message']})")
                continue

            # Metric Display Header Card
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Metric Value", f"{r['value']} {r['unit'] or ''} {r['currency'] or ''}")
            m2.metric("Company & Year", f"{r['company']} ({r['year']})")
            m3.metric("Statement Scope", f"{r['scope'].title()}")
            m4.metric("Confidence Level", r["confidence"])

            if r["status"] == "VERIFY":
                st.error(f"⚠️ **Verification Required**: {r['message']}")

            if r.get("metric_used"):
                st.info(f"ℹ️ Line item substitution: Reported under **{r['metric_used'].replace('_', ' ')}**")

            st.markdown(f"**Statement Source**: `{r['statement']}` | **Page**: `{r['page']}`")
            
            with st.expander("📄 Evidence & Table Excerpt", expanded=True):
                st.code("\n".join(r.get("evidence", [])), language="text")

            with st.expander("🔍 Detailed Audit & Ranking Reasons"):
                for reason in r.get("reasons", []):
                    st.write(f"- {reason}")

# -------------------------------------------------------------------
# VIEW 2: Derived Financial Ratios
# -------------------------------------------------------------------

elif view_mode == "📐 Financial Ratios":
    st.subheader("Derived Financial Ratios & Performance Indicators")

   
    c1, c2, c3 = st.columns(3)
    with c1:
        sel_comp = st.selectbox("Company", available_companies if available_companies else ["N/A"])
    with c2:
        comp_reports = [r for c, r in available_reports if c == sel_comp] if sel_comp else []
        sel_rep = st.selectbox("Report Year", comp_reports if comp_reports else ["N/A"])
    with c3:
        sel_ratio = st.selectbox("Ratio to Compute", list(DERIVED_METRICS.keys()), format_func=lambda k: DERIVED_METRICS[k]["label"])

    if st.button("Calculate Ratio") and sel_comp != "N/A" and sel_rep != "N/A":
        ratio_res = calculate_derived_metric(sel_comp, sel_rep, sel_ratio)

        if ratio_res["status"] in ("OK", "VERIFY"):
            r1, r2, r3 = st.columns(3)
            r1.metric("Calculated Ratio", f"{ratio_res['value']}{ratio_res['unit']}")
            r2.metric("Combined Confidence", ratio_res["confidence"])
            r3.metric("Scope", ratio_res["scope"].title())

            st.info(f"**Formula**: `{ratio_res['formula']}`")
            st.caption(ratio_res["description"])

            st.subheader("Component Extractions")
            num_data = ratio_res["components"]["numerator"]
            den_data = ratio_res["components"]["denominator"]

            col_num, col_den = st.columns(2)
            with col_num:
                st.markdown(f"#### Numerator: {num_data['metric'].replace('_', ' ').title()}")
                st.write(f"- Value: **{num_data['value']} {num_data['unit'] or ''}**")
                st.write(f"- Page: **{num_data['page']}** ({num_data['statement']})")
                st.code("\n".join(num_data.get("evidence", [])), language="text")

            with col_den:
                st.markdown(f"#### Denominator: {den_data['metric'].replace('_', ' ').title()}")
                st.write(f"- Value: **{den_data['value']} {den_data['unit'] or ''}**")
                st.write(f"- Page: **{den_data['page']}** ({den_data['statement']})")
                st.code("\n".join(den_data.get("evidence", [])), language="text")
        else:
            st.error(f"Failed to compute ratio: {ratio_res['message']}")

# -------------------------------------------------------------------
# VIEW 3: Peer Benchmarking Dashboard
# -------------------------------------------------------------------

elif view_mode == "📊 Peer Benchmarking":
    st.subheader("Multi-Company Peer Benchmarking")

    b1, b2, b3 = st.columns([2, 2, 1])
    with b1:
        selected_comps = st.multiselect("Select Companies", available_companies, default=available_companies[:3] if len(available_companies)>=3 else available_companies)
    with b2:
        selected_metric = st.selectbox("Financial Metric", list(METRICS.keys()))
    with b3:
        sel_scope = st.radio("Scope", ["consolidated", "standalone"])

    if st.button("Compare Peers") and selected_comps:
        benchmark_data = []

        for comp in selected_comps:
            comp_reps = [r for c, r in available_reports if c == comp]
            for rep in comp_reps:
                try:
                    res = extract_financial_metric(comp, rep, selected_metric, scope=sel_scope)
                    if res["status"] in ("OK", "VERIFY"):
                        benchmark_data.append({
                            "Company": comp,
                            "Report": rep,
                            "Year": res["year"],
                            "Raw Value": res["value_float"],
                            "Formatted Value": f"{res['value']} {res['unit'] or ''}",
                            "Confidence": res["confidence"],
                            "Page": res["page"]
                        })
                except Exception as e:
                    st.warning(f"Could not retrieve for {comp} {rep}: {e}")

        if benchmark_data:
            df = pd.DataFrame(benchmark_data)
            st.subheader("Comparison Table")
            # Render as a static HTML table via Pandas
            st.markdown(df[["Company", "Report", "Year", "Formatted Value", "Confidence", "Page"]].to_html(), unsafe_allow_html=True) 

            st.subheader("Cross-Company Metric Trend")
            chart_df = df.pivot(index="Year", columns="Company", values="Raw Value")
            st.line_chart(chart_df)
        else:
            st.info("No comparison metrics found for selected choices.")

# -------------------------------------------------------------------
# VIEW 4: Portfolio Integrity Auditor
# -------------------------------------------------------------------

elif view_mode == "🛡️ Portfolio Integrity Auditor":
    st.subheader("Automated Portfolio Financial Integrity Scan")
    st.markdown("Scans all ingested reports for potential line inconsistencies, low confidence flags, and business sanity warnings.")

    if st.button("Run Portfolio Audit Scan"):
        with st.spinner("Auditing portfolio reports..."):
            findings = run_portfolio_audit()

        if findings:
            df_findings = pd.DataFrame(findings)
            st.warning(f"Identified {len(df_findings)} potential audit items across ingested reports.")
            
            # Filter controls
            severities = st.multiselect("Filter Severity", options=list(df_findings["Severity"].unique()), default=list(df_findings["Severity"].unique()))
            filtered_df = df_findings[df_findings["Severity"].isin(severities)]
            
            st.dataframe(filtered_df, use_container_width=True)
        else:
            st.success("Audit complete! No systemic anomalies or warnings detected.")
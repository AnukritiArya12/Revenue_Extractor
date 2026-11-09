import streamlit as st

from src.ask import ask

st.title("Annual report revenue extractor")
question = st.text_input("Ask a question", "What was the revenue of TCS in FY 2025-26?")

if st.button("Ask") and question:
    try:
        out = ask(question)
    except Exception as e:                      # missing reports, etc.
        st.error(str(e))
        out = {"results": []}
    if "error" in out:
        st.error(out["error"])
    for r in out.get("results", []):
        name = r["metric"].replace("_", " ").title()
        if r["status"] in ("NOT_FOUND", "YEAR_NOT_IN_REPORT"):
            st.warning(f"{r['company']} - {name}: NOT FOUND ({r['message']})")
            continue
        st.metric(f"{r['company']} - {name}", f"{r['value']} {r['unit'] or ''} {r['currency'] or ''}")
        st.write(f"Statement: **{r['statement']}**  |  Scope: **{r['scope']}**  |  "
                 f"Page: **{r['page']}**  |  Confidence: **{r['confidence']}**")
        if r["status"] == "VERIFY":
            st.error(r["message"])
        if r["metric_used"]:
            st.info(f"No line is named {name.lower()}; {r['metric_used'].replace('_', ' ')} is shown instead.")
        st.code("\n".join(r["evidence"]))
        with st.expander("Why this confidence?"):
            st.code("\n".join(r["reasons"]))
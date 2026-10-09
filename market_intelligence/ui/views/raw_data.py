import json
import pandas as pd
import streamlit as st



def render_raw_data(ctx) -> None:
    run = st.session_state.get("run")
    if not run:
        st.markdown(
            "<div class='empty-console'>"
            "<div>"
            "<div class='hero-kicker'>Raw Evidence</div>"
            "<div class='empty-title'>Collector payloads will appear after a run.</div>"
            "<div class='empty-body'>This view is intentionally evidence-first: GNews articles, CPI JSON, FDA recall records, NOAA weather alerts, Apify errors, and cleaned item tables stay inspectable for validation.</div>"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        for name, result in run["results"].items():
            status = result.get("status", "unknown")
            label = f"{name.upper()} - {status}"
            with st.expander(label, expanded=False):
                if result.get("error"):
                    st.warning(result["error"])
                if result.get("items"):
                    st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
                raw_preview = result.get("raw")
                if raw_preview is not None:
                    st.caption("Raw payload preview: first 7,000 characters. Full evidence is in Evidence Audit download.")
                    st.code(json.dumps(raw_preview, indent=2, default=str)[:7000], language="json")

import streamlit as st


def sidebar_brief_label(api_key: str) -> str:
    """Report the brief that was actually produced, not the one that was configured.

    The tile read "NVIDIA" whenever a key existed, so it contradicted the Results page
    on any run where the AI summary was rejected and the local one was used.
    """
    run = st.session_state.get("run") or {}
    if run:
        return "Agent" if run.get("brief_source") == "nvidia" else "Rule-based"
    return "Agent" if str(api_key or "").strip() else "Rule-based"

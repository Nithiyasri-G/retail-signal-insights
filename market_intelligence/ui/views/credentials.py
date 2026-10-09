from html import escape

import streamlit as st

from market_intelligence.config.flags import signal_search_enabled
from market_intelligence.infra.credentials import test_nvidia_minimal, validate_apify, validate_nvidia


def _status_lines(rows) -> None:
    """Compact one-line-per-check status list (dot + text) instead of full-width alert boxes."""
    html = "".join(
        f"<div class='status-line'><span class='status-dot {state}'></span>"
        f"<strong>{escape(label)}</strong><span>{escape(message)}</span></div>"
        for label, state, message in rows
    )
    st.markdown(f"<div class='status-lines'>{html}</div>", unsafe_allow_html=True)


def render_credential_checks(ctx) -> None:
    apify_token = ctx.apify_token
    nvidia_key = ctx.nvidia_key
    nvidia_model = ctx.nvidia_model
    test_nvidia_button = ctx.test_nvidia_button
    validate_button = ctx.validate_button
    if validate_button:
        with st.spinner("Validating credentials..."):
            n_ok, n_msg = validate_nvidia(nvidia_key.strip(), nvidia_model.strip())
            a_ok, a_msg = validate_apify(apify_token.strip()) if signal_search_enabled() else (False, "")
        st.session_state["validation"] = {"nvidia": (n_ok, n_msg), "apify": (a_ok, a_msg)}

    if test_nvidia_button:
        with st.spinner("Testing the LLM key with one minimal request..."):
            t_ok, t_msg = test_nvidia_minimal(nvidia_key.strip(), nvidia_model.strip())
        st.session_state["nvidia_minimal_test"] = (t_ok, t_msg)

    show_status = ctx.view == "Configure" or bool(validate_button) or bool(test_nvidia_button)
    if show_status and "validation" in st.session_state:
        n_ok, n_msg = st.session_state["validation"]["nvidia"]
        a_ok, a_msg = st.session_state["validation"]["apify"]
        lines = [("LLM key", "on" if n_ok else "off", f"{'Connected' if n_ok else 'Not connected'} - {n_msg}")]
        if signal_search_enabled():
            lines.append(("Apify", "on" if a_ok else "off", f"{'Connected' if a_ok else 'Not connected'} - {a_msg}"))
        _status_lines(lines)

    if show_status and "nvidia_minimal_test" in st.session_state:
        t_ok, t_msg = st.session_state["nvidia_minimal_test"]
        _status_lines([("LLM key test", "on" if t_ok else "bad", f"{'Succeeded' if t_ok else 'Failed'} - {t_msg}")])

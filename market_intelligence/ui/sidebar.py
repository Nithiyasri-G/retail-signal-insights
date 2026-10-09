from html import escape
import os
import streamlit as st

from market_intelligence.config.flags import signal_search_enabled
from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL
from market_intelligence.ui.helpers import sidebar_brief_label


def render_sidebar(ctx) -> None:
    with st.sidebar:
        st.markdown(
            "<div class='sidebar-brand'>"
            "<div class='sidebar-brand-title'>Run Control</div>"
            "<div class='sidebar-brand-copy'>Configure context, source coverage, and guarded API spend for the next intelligence run.</div>"
            "</div>",
            unsafe_allow_html=True,
        )

        # Always shown, on every machine, with nothing to configure first: this used to be
        # hidden by an environment variable (HIDE_ADMIN_CONTROLS) or by whether a .env file
        # next to app.py happened to load, which meant the same build could show this panel
        # on one laptop and not another with nothing visibly different about the code. Keys
        # entered here live only in this browser session and are never written into any
        # export; anything already set as a real environment variable still prefills the
        # fields below, but nothing needs a .env file to appear.
        with st.expander("Credentials", expanded=False):
            st.caption("Keys stay in this browser session only and are never written into any export.")
            # U01: never prefill a password widget from an environment variable -- the
            # browser's own page source then carries the secret even though the user never
            # typed it, and "masked by type=password" does not stop a saved/shared page or
            # a browser devtools inspection from revealing it. A server-configured key is
            # still used when the field is left blank (same as before), it is simply never
            # placed INTO the widget; "Configured this session" below (unchanged) already
            # reports this without ever rendering the key itself.
            _nvidia_key_entered = st.text_input("LLM API key", value="", type="password")
            nvidia_key = _nvidia_key_entered.strip() or os.getenv("NVIDIA_API_KEY", "").strip()
            nvidia_model = st.text_input("LLM model", value=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL))
            # The Apify token is only needed by Signal Search, which is off unless ENABLE_SIGNAL_SEARCH is set.
            apify_token = (
                st.text_input("Apify token", value=os.getenv("APIFY_API_TOKEN", ""), type="password")
                if signal_search_enabled()
                else ""
            )
            bls_key = st.text_input("BLS API key (optional)", value=os.getenv("BLS_API_KEY", ""), type="password")
            configured = [
                label
                for label, value in (
                    ("LLM", nvidia_key),
                    ("Apify", apify_token),
                    ("BLS", bls_key),
                )
                if str(value or "").strip()
            ]
            # Report what the fields actually hold. Reading only the environment said
            # "no credentials configured" while a key was visibly filled in.
            st.caption(
                f"Configured this session: {', '.join(configured)}."
                if configured
                else "No credentials configured. Enter a key above to use it for this session."
            )
            validate_button = st.button("Validate credentials", width="stretch")
            test_nvidia_button = st.button("Run connectivity test", width="stretch")

        with st.expander("Retail context", expanded=True):
            retailer = st.text_input(
                "Company / Retailer",
                value="Dollar Tree",
                placeholder="Enter the company this run is for",
                help="Shown in the executive brief, the results header and every export. Leave it blank and the app falls back to a generic market label.",
            )
            if not retailer.strip():
                st.caption("No company set -- outputs will read \"General Retail Market\".")
            region = st.text_input("Region", value="US")
            country = st.selectbox("News country", ["US", "CA", "GB", "AE", "IN"], index=0)
            language = st.selectbox("News language", ["en", "es", "fr", "ar", "hi"], index=0)

        with st.expander("Signal sources", expanded=True):
            use_gnews = st.checkbox("Retail news", value=True)
            use_bls = st.checkbox("Inflation CPI", value=True)
            use_fda = st.checkbox("Product recalls", value=True)
            use_weather = st.checkbox("Weather risk", value=True)
            use_apify = False  # Signal Search is an independent lookup.

        sidebar_source_count = sum([use_gnews, use_bls, use_fda, use_weather])
        st.markdown(
            "<div class='sidebar-summary'>"
            f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Sources</div><div class='sidebar-summary-value'>{sidebar_source_count} enabled</div></div>"
            f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Brief</div><div class='sidebar-summary-value'>{escape(sidebar_brief_label(nvidia_key))}</div></div>"
            f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Market</div><div class='sidebar-summary-value'>{escape(region)}</div></div>"
            "</div>",
            unsafe_allow_html=True,
        )

        if signal_search_enabled():
            st.caption("Signal Search is a separate keyword lookup. Run Intelligence covers the selected sources above.")

        st.markdown("<div class='sidebar-divider'></div>", unsafe_allow_html=True)
        run_button = st.button("Run intelligence", type="primary", width="stretch")
        if st.session_state.pop("run_requested", False):
            run_button = True
        sidebar_status_slot = st.empty()
    if "apify_token" in locals():
        ctx.apify_token = apify_token
    if "bls_key" in locals():
        ctx.bls_key = bls_key
    if "country" in locals():
        ctx.country = country
    if "language" in locals():
        ctx.language = language
    if "nvidia_key" in locals():
        ctx.nvidia_key = nvidia_key
    if "nvidia_model" in locals():
        ctx.nvidia_model = nvidia_model
    if "region" in locals():
        ctx.region = region
    if "retailer" in locals():
        ctx.retailer = retailer
    if "run_button" in locals():
        ctx.run_button = run_button
    if "sidebar_status_slot" in locals():
        ctx.sidebar_status_slot = sidebar_status_slot
    if "test_nvidia_button" in locals():
        ctx.test_nvidia_button = test_nvidia_button
    if "use_apify" in locals():
        ctx.use_apify = use_apify
    if "use_bls" in locals():
        ctx.use_bls = use_bls
    if "use_fda" in locals():
        ctx.use_fda = use_fda
    if "use_gnews" in locals():
        ctx.use_gnews = use_gnews
    if "use_weather" in locals():
        ctx.use_weather = use_weather
    if "validate_button" in locals():
        ctx.validate_button = validate_button

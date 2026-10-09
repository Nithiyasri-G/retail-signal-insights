import streamlit as st


def render_footer(ctx) -> None:
    st.markdown(
        "<p class='subtle' style='font-size:12px;margin-top:24px;'>"
        "Retail Signal Intelligence &middot; Based on public external signals. "
        "Match recall data to internal SKU/UPC and inventory records before operational decisions."
        "</p>",
        unsafe_allow_html=True,
    )

"""Page configuration and global stylesheet for the Streamlit app."""
from pathlib import Path

import streamlit as st

_CSS_PATH = Path(__file__).parent / "styles" / "app.css"


def configure_page() -> None:
    st.set_page_config(
        page_title="Retail Signal Intelligence",
        page_icon="DT",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def inject_css() -> None:
    st.markdown(_CSS_PATH.read_text(encoding="utf-8"), unsafe_allow_html=True)

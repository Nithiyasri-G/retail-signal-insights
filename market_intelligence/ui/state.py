"""Small shared UI state helpers.

The script body used to hand render functions a module-level `retailer_label` variable. Render
functions now live in separate modules, so the value travels through st.session_state instead.
"""
import streamlit as st

CURRENT_RETAILER_KEY = "current_retailer_label"
DEFAULT_RETAILER_LABEL = "General Retail Market"


def set_current_retailer_label(label: str) -> None:
    st.session_state[CURRENT_RETAILER_KEY] = label


def current_retailer_label() -> str:
    return st.session_state.get(CURRENT_RETAILER_KEY, DEFAULT_RETAILER_LABEL)

"""Shared per-run context handed between the app's top-level view functions.

The original single-file script shared sidebar widget values (retailer, region, API keys, source
toggles, the run button...) between its blocks as module-level variables. Each block is now a view
function; the values travel through one ScriptContext instance created per Streamlit script run.
"""
from types import SimpleNamespace


class ScriptContext(SimpleNamespace):
    """Attribute bag for values produced by the sidebar/header and consumed by views."""

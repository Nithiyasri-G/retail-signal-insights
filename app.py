import json
import hashlib
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local convenience
    load_dotenv = None


if load_dotenv:
    load_dotenv()


NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODEL = "openai/gpt-oss-120b"
NVIDIA_MAX_FEATURE_ROWS = 8
NVIDIA_MAX_ARTICLES = 4
NVIDIA_MAX_TOKENS = 520
NVIDIA_TIMEOUT_SECONDS = [35]
NVIDIA_SIGNAL_ANALYSIS_MAX_TOKENS = 180
NVIDIA_FINAL_SUMMARY_MAX_TOKENS = 420
BLS_CPI_SERIES = {
    "Headline CPI": "CUUR0000SA0",
    "Food at home": "CUUR0000SAF11",
    "Household furnishings": "CUUR0000SAH3",
    "Gasoline": "CUUR0000SETB",
}
APIFY_ALLOWED_TIME_RANGES = ["", "now 1-H", "now 4-H", "now 1-d", "now 7-d", "today 1-m", "today 3-m", "today 5-y", "all"]
APIFY_SAFE_TIME_RANGE = "now 7-d"
APIFY_HARD_KEYWORD_LIMIT = 2

# Local run history for lightweight versioning on the office/local machine.
RUN_HISTORY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_intelligence_runs.db")
SIGNAL_SEARCH_TIME_WINDOWS = {
    "Last 1 hour": "now 1-H",
    "Last 4 hours": "now 4-H",
    "Last 1 day": "now 1-d",
    "Last 7 days": "now 7-d",
}
US_SIGNAL_GEOS = {
    "United States - National": "US",
    "Alabama": "US-AL", "Alaska": "US-AK", "Arizona": "US-AZ", "Arkansas": "US-AR",
    "California": "US-CA", "Colorado": "US-CO", "Connecticut": "US-CT", "Delaware": "US-DE",
    "Florida": "US-FL", "Georgia": "US-GA", "Hawaii": "US-HI", "Idaho": "US-ID",
    "Illinois": "US-IL", "Indiana": "US-IN", "Iowa": "US-IA", "Kansas": "US-KS",
    "Kentucky": "US-KY", "Louisiana": "US-LA", "Maine": "US-ME", "Maryland": "US-MD",
    "Massachusetts": "US-MA", "Michigan": "US-MI", "Minnesota": "US-MN", "Mississippi": "US-MS",
    "Missouri": "US-MO", "Montana": "US-MT", "Nebraska": "US-NE", "Nevada": "US-NV",
    "New Hampshire": "US-NH", "New Jersey": "US-NJ", "New Mexico": "US-NM", "New York": "US-NY",
    "North Carolina": "US-NC", "North Dakota": "US-ND", "Ohio": "US-OH", "Oklahoma": "US-OK",
    "Oregon": "US-OR", "Pennsylvania": "US-PA", "Rhode Island": "US-RI", "South Carolina": "US-SC",
    "South Dakota": "US-SD", "Tennessee": "US-TN", "Texas": "US-TX", "Utah": "US-UT",
    "Vermont": "US-VT", "Virginia": "US-VA", "Washington": "US-WA", "West Virginia": "US-WV",
    "Wisconsin": "US-WI", "Wyoming": "US-WY", "District of Columbia": "US-DC",
}

SOURCE_ORDER = ["gnews", "bls", "fda", "weather", "apify"]
SOURCE_LABELS = {
    "gnews": "GNews / Google News RSS",
    "bls": "BLS CPI",
    "fda": "openFDA Food Enforcement",
    "weather": "NOAA Weather Alerts",
    "apify": "Apify Google Trends",
}
SOURCE_ENDPOINTS = {
    "gnews": "GNews package or Google News RSS search feed",
    "bls": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
    "fda": "https://api.fda.gov/food/enforcement.json",
    "weather": "https://api.weather.gov/alerts/active",
    "apify": "apify/google-trends-scraper",
}
ANALYSIS_METHODS = {
    "gnews": "Classifies article titles/descriptions into event types, sentiment, source confidence, then averages article risk into one news feature.",
    "bls": "Batches CPI series, calculates month-over-month and year-over-year movement, then scores inflation pressure.",
    "fda": "Classifies recall reason, FDA class, status, UPC presence, and state coverage, then uses highest adjusted recall severity.",
    "weather": "Collects active NOAA alerts for the selected state, weights severity, urgency, and certainty, then caps supply-chain weather risk at 10.",
    "apify": "Uses top regional Google Trends index divided by 10; backend hard-limits time range and keyword count to protect quota.",
}


st.set_page_config(
    page_title="Market Intelligence Command Center",
    page_icon="DT",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
    :root {
        --bg: #F5F7FB;
        --sf: #FFFFFF;
        --s2: #F8FAFE;
        --s3: #F0F4F9;
        --s4: #E9EFF5;
        --or: #F47B25;
        --olt: #FF9F50;
        --odk: #C45D0A;
        --og: rgba(244,123,37,0.12);
        --ob: rgba(244,123,37,0.07);
        --obr: rgba(244,123,37,0.25);
        --bl: #E2E8F0;
        --t: #1E293B;
        --t2: #475569;
        --t3: #94A3B8;
        --gr: #22C55E;
        --gbg: rgba(34,197,94,0.10);
        --am: #F59E0B;
        --abg: rgba(245,158,11,0.10);
        --rd: #EF4444;
        --rbg: rgba(239,68,68,0.08);
        --r: 12px;
        --rl: 16px;
        --sh: 0 1px 3px rgba(0,0,0,0.04);
        --shm: 0 6px 14px -4px rgba(0,0,0,0.10);
        --tr: 0.2s cubic-bezier(0.4,0,0.2,1);
    }
    * { box-sizing: border-box; }
    html, body, [class*="css"] {
        font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
        color: var(--t);
    }
    .stApp { background: var(--bg); }
    #MainMenu, footer { visibility: hidden; }
    header { visibility: hidden; }
    .accent-bar {
        height: 3px;
        background: linear-gradient(90deg, var(--odk), var(--or), var(--olt), var(--or));
        background-size: 200%;
        animation: shimmer 3s linear infinite;
        width: 100%;
        margin: -1.25rem 0 0.9rem 0;
    }
    @keyframes shimmer { 0% { background-position: 200%; } 100% { background-position: -200%; } }
    @keyframes pdot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.5); } 50% { box-shadow: 0 0 0 5px rgba(34,197,94,0); } }
    .ldot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--gr);
        animation: pdot 2s infinite;
        display: inline-block;
    }
    .main .block-container {
        padding-top: 1.25rem;
        max-width: 1400px;
    }
    [data-testid="stSidebar"] {
        background: var(--sf) !important;
        border-right: 1px solid var(--bl) !important;
    }
    [data-testid="stSidebar"] * {
        color: var(--t2);
    }
    [data-testid="stSidebar"] h1 {
        font-size: 18px !important;
        line-height: 1.2 !important;
        margin-bottom: 2px !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
        font-size: 12px;
    }
    .sidebar-brand {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-left: 4px solid var(--or);
        border-radius: 8px;
        padding: 12px;
        margin: 2px 0 12px 0;
    }
    .sidebar-brand-title {
        color: var(--t);
        font-size: 15px;
        font-weight: 950;
        line-height: 1.15;
        margin-bottom: 4px;
    }
    .sidebar-brand-copy {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.4;
    }
    .sidebar-summary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
        margin: 8px 0 10px 0;
    }
    .sidebar-summary-card {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 8px;
        min-height: 58px;
    }
    .sidebar-summary-label {
        color: var(--t3);
        font-size: 8px;
        font-weight: 900;
        letter-spacing: .8px;
        text-transform: uppercase;
        margin-bottom: 3px;
    }
    .sidebar-summary-value {
        color: var(--t);
        font-size: 12px;
        line-height: 1.2;
        font-weight: 900;
    }
    .sidebar-divider {
        height: 1px;
        background: var(--bl);
        margin: 10px 0;
    }
    h1, h2, h3 {
        color: var(--t);
        letter-spacing: 0;
    }
    h1 { font-size: 2.25rem; font-weight: 900; letter-spacing: -0.02em; }
    .subtle {
        color: var(--t2);
        font-size: 0.94rem;
        line-height: 1.55;
    }
    .topbar {
        height: 54px;
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 10px;
        display: flex;
        align-items: center;
        padding: 0 18px;
        gap: 12px;
        box-shadow: var(--sh);
        margin-bottom: 18px;
    }
    .tt { font-size: 14px; font-weight: 800; color: var(--t); }
    .tt span { color: var(--t3); font-weight: 500; }
    .tbadge {
        background: var(--ob);
        border: 1px solid var(--obr);
        color: var(--or);
        font-size: 10px;
        font-weight: 800;
        padding: 2px 8px;
        border-radius: 20px;
    }
    .hero-shell {
        background: linear-gradient(180deg, #FFFFFF 0%, #FAFCFF 100%);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 16px 18px;
        box-shadow: var(--sh);
        margin-bottom: 12px;
        position: relative;
        overflow: hidden;
    }
    .hero-shell:before {
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 0;
        height: 4px;
        background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
    }
    .hero-kicker {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        background: var(--ob);
        border: 1px solid var(--obr);
        color: var(--or);
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 10px;
        font-weight: 900;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        margin-bottom: 12px;
    }
    .hero-title {
        font-size: 28px;
        line-height: 1.08;
        letter-spacing: 0;
        font-weight: 950;
        color: var(--t);
        max-width: 820px;
        margin: 0;
    }
    .hero-copy {
        color: var(--t2);
        font-size: 12px;
        line-height: 1.55;
        max-width: 820px;
        margin: 10px 0 0 0;
    }
    .hero-side {
        background: #FFFFFF;
        border: 1px solid rgba(226,232,240,0.9);
        border-radius: 8px;
        padding: 14px;
        box-shadow: var(--sh);
    }
    .hero-side-label {
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1.2px;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .hero-side-row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        border-top: 1px solid var(--bl);
        padding-top: 8px;
        margin-top: 8px;
        font-size: 12px;
        color: var(--t2);
    }
    .hero-side-row strong { color: var(--t); }
    .source-tile {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 13px 14px;
        box-shadow: var(--sh);
        min-height: 86px;
        transition: all var(--tr);
    }
    .source-tile:hover { border-color: var(--obr); box-shadow: var(--shm); transform: translateY(-1px); }
    .source-name {
        font-size: 12px;
        font-weight: 850;
        color: var(--t);
        margin-bottom: 5px;
    }
    .source-meta {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.45;
    }
    .console-panel {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 18px;
        box-shadow: var(--sh);
        margin-top: 12px;
    }
    .config-shell {
        display: grid;
        grid-template-columns: .72fr 1.28fr;
        gap: 12px;
        margin: 10px 0 14px 0;
    }
    .config-panel {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 14px 15px;
        box-shadow: var(--sh);
        min-height: 100%;
    }
    .config-panel.emphasis {
        border-left: 4px solid var(--or);
    }
    .config-eyebrow {
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 6px;
    }
    .config-title {
        color: var(--t);
        font-size: 16px;
        font-weight: 950;
        line-height: 1.15;
        margin-bottom: 6px;
    }
    .config-copy {
        color: var(--t2);
        font-size: 12px;
        line-height: 1.5;
    }
    .readiness-list {
        display: grid;
        gap: 7px;
        margin-top: 12px;
    }
    .readiness-row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 8px 9px;
        font-size: 11px;
        color: var(--t2);
    }
    .readiness-row strong { color: var(--t); font-size: 12px; }
    .source-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 8px;
        margin: 8px 0 12px 0;
    }
    .source-tile.active { border-color: rgba(34,197,94,0.28); border-left: 3px solid var(--gr); }
    .source-tile.off { opacity: .72; }
    .source-purpose {
        color: var(--t3);
        font-size: 9px;
        line-height: 1.35;
        text-transform: uppercase;
        letter-spacing: .7px;
        font-weight: 900;
        margin-top: 8px;
    }
    .config-tab-note {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px 11px;
        color: var(--t2);
        font-size: 12px;
        line-height: 1.45;
        margin: 8px 0 10px 0;
    }
    .readiness-score {
        display: grid;
        place-items: center;
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        min-height: 138px;
        margin-top: 10px;
    }
    .readiness-score-value {
        color: var(--t);
        font-size: 38px;
        line-height: 1;
        font-weight: 950;
    }
    .readiness-score-label {
        color: var(--t3);
        font-size: 9px;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-weight: 900;
        margin-top: 5px;
    }
    .control-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        margin-top: 10px;
    }
    .control-check {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px;
        min-height: 88px;
    }
    .control-check.ok { border-left: 3px solid var(--gr); }
    .control-check.warn { border-left: 3px solid var(--am); }
    .control-check-title {
        color: var(--t);
        font-size: 12px;
        font-weight: 900;
        margin-bottom: 4px;
    }
    .control-check-copy {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.4;
    }
    .routing-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 8px;
        margin: 8px 0 14px 0;
    }
    .routing-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 11px 12px;
        min-height: 154px;
        box-shadow: var(--sh);
    }
    .routing-card.active { border-top: 3px solid var(--gr); }
    .routing-card.off { border-top: 3px solid var(--t3); opacity: .74; }
    .routing-source {
        color: var(--t);
        font-size: 12px;
        font-weight: 950;
        margin-bottom: 4px;
    }
    .routing-line {
        border-top: 1px solid var(--bl);
        padding-top: 7px;
        margin-top: 7px;
    }
    .routing-label {
        color: var(--t3);
        font-size: 8px;
        letter-spacing: .8px;
        text-transform: uppercase;
        font-weight: 900;
        margin-bottom: 2px;
    }
    .routing-value {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.35;
    }
    .result-command {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        box-shadow: var(--sh);
        padding: 14px 16px;
        margin: 8px 0 12px 0;
        display: grid;
        grid-template-columns: 1.4fr .9fr;
        gap: 14px;
        align-items: stretch;
    }
    .result-command-title {
        color: var(--t);
        font-size: 20px;
        line-height: 1.15;
        font-weight: 950;
        margin-bottom: 5px;
    }
    .result-command-copy {
        color: var(--t2);
        font-size: 12px;
        line-height: 1.5;
    }
    .result-command-meta {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
    }
    .result-meta-cell {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px;
    }
    .result-meta-label {
        color: var(--t3);
        font-size: 8px;
        font-weight: 900;
        letter-spacing: .8px;
        text-transform: uppercase;
        margin-bottom: 3px;
    }
    .result-meta-value {
        color: var(--t);
        font-size: 13px;
        line-height: 1.2;
        font-weight: 900;
    }
    .decision-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
        margin: 8px 0 12px 0;
    }
    .decision-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-left: 3px solid var(--or);
        border-radius: 8px;
        padding: 12px;
        min-height: 132px;
        box-shadow: var(--sh);
    }
    .decision-owner {
        color: var(--or);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 6px;
    }
    .decision-title {
        color: var(--t);
        font-size: 13px;
        font-weight: 950;
        line-height: 1.25;
        margin-bottom: 6px;
    }
    .decision-body {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.45;
    }
    .explain-ladder {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 8px;
        margin: 8px 0 12px 0;
    }
    .explain-step {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px;
        min-height: 98px;
    }
    .explain-step-num {
        color: var(--or);
        font-size: 9px;
        letter-spacing: .8px;
        text-transform: uppercase;
        font-weight: 900;
        margin-bottom: 4px;
    }
    .explain-step-title {
        color: var(--t);
        font-size: 12px;
        font-weight: 950;
        margin-bottom: 4px;
    }
    .explain-step-copy {
        color: var(--t2);
        font-size: 10px;
        line-height: 1.4;
    }
    .audit-command {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        box-shadow: var(--sh);
        padding: 14px 16px;
        margin: 8px 0 12px 0;
        display: grid;
        grid-template-columns: 1.25fr 1fr;
        gap: 12px;
    }
    .audit-command-title {
        color: var(--t);
        font-size: 20px;
        line-height: 1.15;
        font-weight: 950;
        margin-bottom: 5px;
    }
    .audit-command-copy {
        color: var(--t2);
        font-size: 12px;
        line-height: 1.5;
    }
    .audit-status-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
    }
    .audit-status-cell {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px;
    }
    .audit-status-label {
        color: var(--t3);
        font-size: 8px;
        font-weight: 900;
        letter-spacing: .8px;
        text-transform: uppercase;
        margin-bottom: 3px;
    }
    .audit-status-value {
        color: var(--t);
        font-size: 13px;
        font-weight: 950;
        line-height: 1.2;
    }
    .audit-lineage {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 8px;
        margin: 8px 0 12px 0;
    }
    .audit-lineage-step {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px;
        min-height: 94px;
    }
    .audit-lineage-num {
        color: var(--or);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: .8px;
        text-transform: uppercase;
        margin-bottom: 4px;
    }
    .audit-lineage-title {
        color: var(--t);
        font-size: 12px;
        font-weight: 950;
        margin-bottom: 4px;
    }
    .audit-lineage-copy {
        color: var(--t2);
        font-size: 10px;
        line-height: 1.4;
    }
    .empty-console {
        background:
            linear-gradient(135deg, rgba(244,123,37,0.08), rgba(255,255,255,0.85)),
            var(--sf);
        border: 1px dashed var(--obr);
        border-radius: 18px;
        padding: 28px;
        min-height: 210px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
    }
    .empty-title {
        color: var(--t);
        font-size: 22px;
        line-height: 1.15;
        font-weight: 900;
        letter-spacing: -0.025em;
        margin-bottom: 8px;
    }
    .empty-body {
        color: var(--t2);
        font-size: 13px;
        line-height: 1.65;
        max-width: 620px;
    }
    .workflow {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 8px;
        margin-top: 12px;
    }
    .workflow-step {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 10px;
        font-size: 11px;
        color: var(--t2);
        font-weight: 700;
    }
    .workflow-step span {
        display: block;
        color: var(--or);
        font-size: 9px;
        letter-spacing: 1px;
        text-transform: uppercase;
        font-weight: 900;
        margin-bottom: 3px;
    }
    .metric-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 10px;
        padding: 16px 18px;
        min-height: 132px;
        box-shadow: var(--sh);
        transition: all var(--tr);
    }
    .metric-card:hover {
        transform: translateY(-1px);
        box-shadow: var(--shm);
        border-color: var(--obr);
    }
    .metric-label {
        color: var(--t3);
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.11em;
        margin-bottom: 8px;
        font-weight: 800;
    }
    .metric-value {
        color: var(--t);
        font-size: 2.05rem;
        font-weight: 900;
        line-height: 1;
        letter-spacing: -0.04em;
    }
    .metric-value.direction-value {
        font-size: 1.15rem;
        font-weight: 900;
        line-height: 1.25;
        letter-spacing: -0.01em;
        overflow-wrap: anywhere;
        word-break: normal;
    }
    .metric-card.level-high { border-top: 3px solid var(--rd); }
    .metric-card.level-medium { border-top: 3px solid var(--am); }
    .metric-card.level-low { border-top: 3px solid var(--gr); }
    .metric-value.level-high { color: #DC2626; font-size: 1rem; font-weight: 800; letter-spacing: 0; line-height: 1.3; }
    .metric-value.level-medium { color: #D97706; font-size: 1rem; font-weight: 800; letter-spacing: 0; line-height: 1.3; }
    .metric-value.level-low { color: #16A34A; font-size: 1rem; font-weight: 800; letter-spacing: 0; line-height: 1.3; }
    .score-number.level-high { color: #DC2626; background: rgba(239,68,68,0.08); border-color: rgba(239,68,68,0.25); }
    .score-number.level-medium { color: #D97706; background: rgba(245,158,11,0.10); border-color: rgba(245,158,11,0.25); }
    .score-number.level-low { color: #16A34A; background: rgba(34,197,94,0.10); border-color: rgba(34,197,94,0.25); }
    .metric-note {
        color: var(--t2);
        font-size: 0.82rem;
        margin-top: 10px;
        line-height: 1.4;
    }
    .pill {
        display: inline-block;
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 0.72rem;
        font-weight: 800;
        border: 1px solid var(--bl);
        color: var(--t2);
        background: var(--s2);
        margin-top: 8px;
    }
    .pill-high { color: var(--gr); background: var(--gbg); border-color: rgba(34,197,94,0.2); }
    .pill-medium { color: var(--am); background: var(--abg); border-color: rgba(245,158,11,0.2); }
    .pill-low { color: var(--rd); background: var(--rbg); border-color: rgba(239,68,68,0.2); }
    .brief-box {
        background: var(--sf);
        border: 1px solid var(--obr);
        border-left: 4px solid var(--or);
        border-radius: 10px;
        padding: 20px 22px;
        color: var(--t);
        line-height: 1.65;
        box-shadow: 0 0 0 3px var(--og);
    }
    .brief-shell {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 12px;
        box-shadow: var(--sh);
        overflow: hidden;
        margin-top: 8px;
    }
    .brief-header {
        background: linear-gradient(180deg, rgba(248,250,252,0.98), rgba(255,255,255,0.98));
        border-bottom: 1px solid var(--bl);
        padding: 18px 20px;
    }
    .brief-kicker {
        color: var(--or);
        font-size: 10px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 6px;
    }
    .brief-title {
        color: var(--t);
        font-size: 22px;
        line-height: 1.15;
        font-weight: 950;
        letter-spacing: -0.02em;
        margin-bottom: 8px;
    }
    .brief-summary-text {
        color: var(--t2);
        font-size: 13px;
        line-height: 1.55;
        max-width: 980px;
    }
    .brief-meta-strip {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
        margin-top: 14px;
    }
    .brief-meta-chip {
        background: rgba(255,255,255,0.82);
        border: 1px solid rgba(226,232,240,0.95);
        border-radius: 8px;
        padding: 10px 11px;
    }
    .brief-meta-label {
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: .9px;
        text-transform: uppercase;
        margin-bottom: 4px;
    }
    .brief-meta-value {
        color: var(--t);
        font-size: 13px;
        line-height: 1.25;
        font-weight: 900;
    }
    .brief-section-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        padding: 16px;
    }
    .brief-section-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 14px 15px;
        min-height: 150px;
        box-shadow: var(--sh);
    }
    .brief-section-card.primary {
        grid-column: 1 / -1;
        min-height: 0;
        border-color: var(--obr);
        background: rgba(244,123,37,0.035);
    }
    .brief-section-title {
        color: var(--or);
        font-size: 11px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin: 0 0 9px 0;
    }
    .brief-section-card p,
    .brief-box p {
        margin: 0 0 8px 0;
        font-size: 13px;
        color: var(--t2);
        line-height: 1.55;
    }
    .brief-list {
        margin: 0 0 0 18px;
        padding: 0;
    }
    .brief-list li {
        margin-bottom: 8px;
        color: var(--t2);
        font-size: 13px;
        line-height: 1.45;
    }
    .score-explain-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: var(--rl);
        padding: 14px 16px;
        box-shadow: var(--sh);
        margin: 8px 0;
    }
    .score-explain-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        border-bottom: 1px solid var(--bl);
        padding-bottom: 10px;
        margin-bottom: 10px;
    }
    .score-explain-title {
        color: var(--t);
        font-size: 13px;
        font-weight: 900;
        margin-bottom: 3px;
    }
    .score-explain-meta {
        color: var(--t3);
        font-size: 10px;
        font-weight: 800;
        letter-spacing: .7px;
        text-transform: uppercase;
    }
    .score-number {
        min-width: 74px;
        text-align: center;
        border-radius: 12px;
        border: 1px solid var(--obr);
        background: var(--ob);
        color: var(--or);
        font-size: 24px;
        line-height: 1;
        font-weight: 950;
        padding: 9px 8px;
    }
    .score-number span {
        display: block;
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: .8px;
        margin-top: 3px;
    }
    .score-explain-label {
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin: 8px 0 3px 0;
    }
    .score-explain-text {
        color: var(--t2);
        font-size: 12px;
        line-height: 1.5;
    }
    .brief-grounding {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 12px;
    }
    .small-header {
        color: var(--t3);
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-weight: 800;
        margin: 8px 0 12px 0;
        padding-bottom: 6px;
        border-bottom: 1px solid var(--bl);
    }
    .action-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: var(--r);
        padding: 12px 14px;
        box-shadow: var(--sh);
        min-height: 112px;
    }
    .action-label {
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        color: var(--or);
        margin-bottom: 7px;
    }
    .action-title {
        font-size: 13px;
        font-weight: 800;
        color: var(--t);
        margin-bottom: 5px;
    }
    .action-body {
        font-size: 12px;
        color: var(--t2);
        line-height: 1.45;
    }
    .note-box {
        background: rgba(244,123,37,0.04);
        border-left: 3px solid var(--or);
        border-radius: 0 8px 8px 0;
        padding: 9px 12px;
        font-size: 12px;
        color: var(--t2);
        margin: 8px 0;
    }
    .run-monitor {
        background: var(--sf);
        border: 1px solid var(--obr);
        border-radius: 12px;
        padding: 14px 16px;
        box-shadow: 0 0 0 3px var(--og);
        margin: 8px 0 16px 0;
    }
    .run-monitor-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
    }
    .run-monitor-title {
        color: var(--t);
        font-size: 13px;
        font-weight: 900;
    }
    .run-monitor-sub {
        color: var(--t3);
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 1px;
        text-transform: uppercase;
    }
    .run-progress-track {
        height: 7px;
        background: var(--s3);
        border-radius: 999px;
        overflow: hidden;
        margin: 8px 0 12px 0;
    }
    .run-progress-fill {
        height: 100%;
        background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
        border-radius: 999px;
        transition: width var(--tr);
    }
    .run-status-grid {
        display: grid;
        grid-template-columns: repeat(6, minmax(0, 1fr));
        gap: 8px;
    }
    .run-status-card {
        background: var(--s2);
        border: 1px solid var(--bl);
        border-radius: 12px;
        padding: 10px;
        min-height: 72px;
    }
    .run-status-name {
        font-size: 11px;
        font-weight: 900;
        color: var(--t);
        margin-bottom: 5px;
    }
    .run-status-detail {
        font-size: 10px;
        color: var(--t2);
        line-height: 1.35;
    }
    .status-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 7px;
        border-radius: 999px;
        font-size: 9px;
        font-weight: 900;
        letter-spacing: .6px;
        text-transform: uppercase;
        margin-bottom: 6px;
    }
    .status-running { background: var(--ob); color: var(--or); border: 1px solid var(--obr); }
    .status-success { background: var(--gbg); color: var(--gr); border: 1px solid rgba(34,197,94,.2); }
    .status-failed { background: var(--rbg); color: var(--rd); border: 1px solid rgba(239,68,68,.2); }
    .status-skipped { background: rgba(100,116,139,.08); color: var(--t2); border: 1px solid var(--bl); }
    .status-queued { background: var(--s3); color: var(--t3); border: 1px solid var(--bl); }
    .audit-banner {
        background: linear-gradient(135deg, rgba(34,197,94,0.10), rgba(255,255,255,0.92));
        border: 1px solid rgba(34,197,94,0.22);
        border-left: 4px solid var(--gr);
        border-radius: var(--rl);
        padding: 14px 16px;
        color: var(--t);
        margin: 8px 0 14px 0;
        box-shadow: var(--sh);
    }
    .audit-banner.warn {
        background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(255,255,255,0.92));
        border-color: rgba(245,158,11,0.24);
        border-left-color: var(--am);
    }
    .audit-title {
        font-size: 13px;
        font-weight: 900;
        margin-bottom: 5px;
    }
    .audit-body {
        font-size: 12px;
        color: var(--t2);
        line-height: 1.5;
    }
    .audit-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin: 10px 0 14px 0;
    }
    .audit-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 10px;
        padding: 13px 14px;
        min-height: 92px;
        box-shadow: var(--sh);
    }
    .audit-label {
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 7px;
    }
    .audit-value {
        color: var(--t);
        font-size: 18px;
        line-height: 1.1;
        font-weight: 900;
    }
    .audit-note {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.35;
        margin-top: 7px;
    }
    div[role="radiogroup"] {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 14px;
        padding: 5px;
        display: inline-flex;
        gap: 4px;
        box-shadow: var(--sh);
        margin: 4px 0 12px 0;
    }
    div[role="radiogroup"] label {
        border-radius: 10px !important;
        padding: 6px 13px !important;
        min-height: 34px !important;
        transition: all var(--tr);
    }
    div[role="radiogroup"] label:has(input:checked) {
        background: var(--ob) !important;
        border: 1px solid var(--obr) !important;
        color: var(--or) !important;
        font-weight: 850 !important;
    }
    div[role="radiogroup"] label span {
        font-size: 12px !important;
        font-weight: 750 !important;
    }
    div[data-testid="stButton"] button {
        background: var(--or);
        color: #fff;
        border: none;
        border-radius: var(--r);
        font-weight: 800;
        box-shadow: 0 2px 8px rgba(244,123,37,0.2);
        transition: all var(--tr);
    }
    div[data-testid="stButton"] button:hover {
        background: var(--odk);
        color: #fff;
        border: none;
        transform: translateY(-1px);
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 12px;
        border-bottom: 1px solid var(--bl);
        padding: 0 0 8px 0;
        margin: 6px 0 14px 0;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        color: var(--t2);
        font-weight: 700;
        min-height: 42px;
        padding: 9px 18px !important;
        border: 1px solid transparent;
        background: var(--s2);
    }
    .stTabs [aria-selected="true"] {
        background: var(--ob);
        color: var(--or) !important;
        border: 1px solid var(--obr);
        box-shadow: inset 0 -2px 0 var(--or);
    }
    .stTabs [data-baseweb="tab"] p {
        font-size: 13px;
        font-weight: 850;
        white-space: nowrap;
    }
    .stSelectbox>div>div, .stTextInput>div>div, .stTextArea>div>div {
        background: var(--s2) !important;
        border: 1px solid var(--bl) !important;
        border-radius: var(--r) !important;
        font-size: 13px !important;
        color: var(--t) !important;
    }
    [data-testid="stExpander"] {
        background: var(--sf);
        border: 1px solid var(--bl) !important;
        border-radius: 10px !important;
        box-shadow: var(--sh);
    }
    .glossary-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        margin: 10px 0 4px 0;
    }
    .glossary-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 12px;
        min-height: 94px;
    }
    .glossary-term {
        color: var(--t);
        font-size: 12px;
        font-weight: 900;
        margin-bottom: 5px;
    }
    .glossary-def {
        color: var(--t2);
        font-size: 11px;
        line-height: 1.45;
    }
    .enterprise-band {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 10px;
        padding: 14px 16px;
        margin: 10px 0 14px 0;
        box-shadow: var(--sh);
    }
    .enterprise-band-title {
        color: var(--t);
        font-size: 14px;
        font-weight: 900;
        margin-bottom: 4px;
    }
    .enterprise-band-copy {
        color: var(--t2);
        font-size: 12px;
        line-height: 1.5;
    }
    .trust-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 8px;
        margin: 10px 0 14px 0;
    }
    .trust-card {
        background: var(--sf);
        border: 1px solid var(--bl);
        border-radius: 8px;
        padding: 11px 12px;
        min-height: 86px;
    }
    .trust-card.good { border-left: 3px solid var(--gr); }
    .trust-card.warn { border-left: 3px solid var(--am); }
    .trust-label {
        color: var(--t3);
        font-size: 9px;
        font-weight: 900;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 5px;
    }
    .trust-value {
        color: var(--t);
        font-size: 17px;
        font-weight: 950;
        line-height: 1.1;
    }
    .trust-note {
        color: var(--t2);
        font-size: 10px;
        line-height: 1.35;
        margin-top: 6px;
    }
    @media (max-width: 1100px) {
        .run-status-grid, .brief-meta-strip, .glossary-grid, .trust-grid, .source-grid, .config-shell, .routing-grid, .control-grid, .result-command, .decision-grid, .explain-ladder, .audit-command, .audit-lineage { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .brief-section-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 720px) {
        .run-status-grid, .brief-meta-strip, .glossary-grid, .trust-grid, .source-grid, .config-shell, .routing-grid, .control-grid, .result-command, .decision-grid, .explain-ladder, .audit-command, .audit-status-grid, .audit-lineage, .workflow { grid-template-columns: 1fr; }
        .hero-title { font-size: 28px; }
    }
    ::-webkit-scrollbar { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--bl); border-radius: 2px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[bool, Any, str]:
    try:
        if method.upper() == "POST":
            response = requests.post(url, headers=headers, params=params, json=json_body, timeout=timeout)
        else:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        try:
            return True, response.json(), "success"
        except ValueError:
            return True, response.text, "success"
    except requests.RequestException as exc:
        return False, None, str(exc)


def friendly_nvidia_failure(raw_error: str) -> Tuple[str, str]:
    error_text = str(raw_error or "").strip()
    lower = error_text.lower()
    if "empty" in lower or "no message content" in lower:
        return (
            "fallback (NVIDIA unavailable)",
            "NVIDIA did not return usable brief content after retry, so a local grounded summary was generated from the collected signal rows.",
        )
    if "timed out" in lower or "read timeout" in lower:
        return (
            "fallback (NVIDIA timeout)",
            "NVIDIA did not respond within the configured timeout, so a local rule-based brief was generated from the collected signal rows.",
        )
    if "401" in lower or "unauthorized" in lower:
        return (
            "fallback (NVIDIA authentication)",
            "NVIDIA authentication failed, so a local rule-based brief was generated from the collected signal rows.",
        )
    if "429" in lower or "rate limit" in lower:
        return (
            "fallback (NVIDIA rate limit)",
            "NVIDIA rate limiting prevented the AI brief, so a local rule-based brief was generated from the collected signal rows.",
        )
    return (
        "fallback (NVIDIA unavailable)",
        "The NVIDIA AI brief was unavailable for this run, so a local rule-based brief was generated from the collected signal rows.",
    )


def display_fallback_reason(reason: Any) -> str:
    text = str(reason or "").strip()
    lower = text.lower()
    if not text:
        return "External LLM response used."
    if "empty nvidia response" in lower or "nvidia returned an empty response" in lower or "empty message content" in lower:
        return "NVIDIA did not return usable brief content after retry, so a local grounded summary was generated from the collected signal rows."
    return text


def confidence_class(confidence: str) -> str:
    lookup = {"High": "pill-high", "Medium": "pill-medium", "Low": "pill-low"}
    return lookup.get(confidence, "")


def risk_band(score: float) -> str:
    if score >= 8:
        return "High"
    if score >= 5:
        return "Medium"
    return "Low"


def dollar_tree_context_for_signal(row: Dict[str, Any]) -> Dict[str, str]:
    area = str(row.get("signal_area", "")).lower()
    name = str(row.get("signal_name", "")).lower()
    source = str(row.get("source", "")).lower()
    raw_category = str(row.get("affected_category", "") or row.get("raw_reference", "")).lower()
    score = float(row.get("risk_score", 0) or 0)
    priority = "High" if score >= 8 else "Medium" if score >= 5 else "Monitor"

    context = {
        "dollar_tree_category": "Total store / value basket",
        "enterprise_kpi": "Forecast Adjustment Priority",
        "planning_owner": "Demand Planning",
        "demand_direction": "Unknown until matched to POS",
        "forecast_feature": str(row.get("signal_name", "external_signal")),
        "dollar_tree_relevance": "External context signal; validate against internal sales, inventory, promotion, and store data.",
        "impact_hypothesis": "Use as a candidate external regressor, not as a standalone demand forecast.",
        "action_priority": priority,
        "evidence_grade": "Live public API" if source else "Unknown",
        "internal_data_needed": "POS sales, category hierarchy, inventory, store/DC mapping, promotion calendar",
    }

    if "recall" in area or "fda" in source or any(term in raw_category for term in ["snack", "candy", "beverage", "food"]):
        context.update(
            {
                "dollar_tree_category": "Snacks, candy, beverages, and consumables",
                "enterprise_kpi": "Safety And Compliance Risk",
                "planning_owner": "Compliance + Category Buyer",
                "demand_direction": "Downside risk / substitution demand",
                "forecast_feature": "recall_exposure_score",
                "dollar_tree_relevance": "Retailers may carry high-velocity consumables where recalls can require UPC matching, vendor review, and store withdrawal decisions.",
                "impact_hypothesis": "If affected UPCs overlap internal SKU files, demand may shift away from recalled items and toward substitutes.",
                "internal_data_needed": "SKU master, UPC list, vendor file, on-hand inventory, store distribution",
            }
        )
    elif "weather" in area or "noaa" in source:
        context.update(
            {
                "dollar_tree_category": "Emergency demand: batteries, water, cleaning, food, household essentials",
                "enterprise_kpi": "Supply Chain Disruption Risk",
                "planning_owner": "Supply Chain + Demand Planning",
                "demand_direction": "Short-term uplift for essentials; disruption risk for replenishment",
                "forecast_feature": "state_weather_disruption_score",
                "dollar_tree_relevance": "Weather alerts can affect store traffic, replenishment routes, staffing, and emergency-demand baskets in exposed states.",
                "impact_hypothesis": "Severe alerts can lift emergency categories while increasing DC-to-store execution risk.",
                "internal_data_needed": "Store locations, DC routes, state/category sales, inventory by store",
            }
        )
    elif "headline cpi" in name or ("inflation" in area and "category" not in area):
        context.update(
            {
                "dollar_tree_category": "Total value basket",
                "enterprise_kpi": "Value Basket Pressure",
                "planning_owner": "Merchandising Strategy + Demand Planning",
                "demand_direction": "Trade-down support for essentials; pressure on discretionary baskets",
                "forecast_feature": "headline_cpi_value_pressure",
                "dollar_tree_relevance": "Value-oriented retail demand can be sensitive to consumer price pressure and trade-down behavior.",
                "impact_hypothesis": "Higher inflation can increase value-seeking traffic while changing mix toward essentials.",
                "internal_data_needed": "Traffic, basket mix, category sales, price/promotion calendar",
            }
        )
    elif "gasoline" in name or "gasoline" in raw_category:
        context.update(
            {
                "dollar_tree_category": "Traffic-sensitive baskets and discretionary add-ons",
                "enterprise_kpi": "Consumer Wallet Pressure",
                "planning_owner": "Demand Planning + Store Operations",
                "demand_direction": "Possible traffic pressure; essential-item substitution risk",
                "forecast_feature": "gasoline_wallet_pressure_score",
                "dollar_tree_relevance": "Fuel inflation can reduce discretionary spend and change trip patterns for value retailers.",
                "impact_hypothesis": "Rising gas prices may shift basket composition and store visit frequency by region.",
                "internal_data_needed": "Store traffic, basket value, region/store sales, trip frequency",
            }
        )
    elif "food at home" in name:
        context.update(
            {
                "dollar_tree_category": "Food, snacks, candy, beverages",
                "enterprise_kpi": "Consumables Demand Pressure",
                "planning_owner": "Consumables Category Manager",
                "demand_direction": "Potential uplift in value consumables",
                "forecast_feature": "food_at_home_cpi_pressure",
                "dollar_tree_relevance": "Food inflation can push shoppers toward value-format consumables and smaller pack sizes.",
                "impact_hypothesis": "Higher food CPI may increase demand for value snacks, pantry, and beverage alternatives.",
                "internal_data_needed": "Consumables sales, price ladder, pack size, inventory and promotions",
            }
        )
    elif "household" in name:
        context.update(
            {
                "dollar_tree_category": "Household supplies, cleaning, home basics",
                "enterprise_kpi": "Household Essentials Pressure",
                "planning_owner": "Household Category Manager",
                "demand_direction": "Potential mix shift toward value household items",
                "forecast_feature": "household_cpi_pressure",
                "dollar_tree_relevance": "Household inflation can influence trade-down into value-oriented home and cleaning categories.",
                "impact_hypothesis": "Higher household CPI may support value demand but pressure margin and vendor costs.",
                "internal_data_needed": "Household category sales, costs, vendor data, pricing actions",
            }
        )
    elif "search" in area or "apify" in source:
        context.update(
            {
                "dollar_tree_category": "Seasonal, local demand, and store-intent categories",
                "enterprise_kpi": "Demand Interest Spike",
                "planning_owner": "Demand Planning + Digital/Marketing",
                "demand_direction": "Potential regional demand uplift",
                "forecast_feature": "google_trends_interest_score",
                "dollar_tree_relevance": "Search interest can reveal early regional intent around deals, coupons, seasonal products, or store visits.",
                "impact_hypothesis": "Rising search interest may precede demand spikes, but must be checked against store/category sales.",
                "internal_data_needed": "Regional sales, promotion calendar, store traffic, search keywords by category",
            }
        )
    elif "news" in area or "gnews" in source:
        context.update(
            {
                "dollar_tree_category": "Enterprise market events and competitor pressure",
                "enterprise_kpi": "Market Event Risk",
                "planning_owner": "Merchandising Leadership + Category Manager",
                "demand_direction": "Depends on event type and affected category",
                "forecast_feature": "retail_news_event_score",
                "dollar_tree_relevance": "Retail news can surface competitor moves, price pressure, closures, recalls, and market events relevant to retail planning.",
                "impact_hypothesis": "Use high-risk articles as explainers for forecast variance and buyer review.",
                "internal_data_needed": "Affected category sales, competitor set, promotion calendar, store overlap",
            }
        )
    return context


def enrich_feature_rows_for_retailer(feature_df: pd.DataFrame, retailer_name: str) -> pd.DataFrame:
    if feature_df.empty:
        return feature_df
    enriched = feature_df.copy()
    contexts = [dollar_tree_context_for_signal(row.to_dict()) for _, row in enriched.iterrows()]
    context_df = pd.DataFrame(contexts)
    for col in context_df.columns:
        enriched[col] = context_df[col].values
    # Relevance wording is intentionally retailer-neutral so the application can be reused across companies.
    return enriched


def source_confidence(publisher: str) -> str:
    high = ["reuters", "associated press", "ap news", "bloomberg", "sec", "pr newswire"]
    medium = ["cnbc", "yahoo", "nasdaq", "marketwatch", "forbes", "investing.com", "retail dive"]
    p = (publisher or "").lower()
    if any(name in p for name in high):
        return "High"
    if any(name in p for name in medium):
        return "Medium"
    return "Medium" if publisher else "Low"


def count_raw_records(raw_payload: Any, items: Optional[List[Dict[str, Any]]] = None) -> int:
    if items:
        return len(items)
    if raw_payload is None:
        return 0
    if isinstance(raw_payload, list):
        return len(raw_payload)
    if isinstance(raw_payload, dict):
        if isinstance(raw_payload.get("features"), list):
            return len(raw_payload["features"])
        if isinstance(raw_payload.get("results"), list):
            return len(raw_payload["results"])
        series = raw_payload.get("Results", {}).get("series") if isinstance(raw_payload.get("Results"), dict) else None
        if isinstance(series, list):
            return sum(len(s.get("data", [])) for s in series if isinstance(s, dict))
        nested_counts = [
            count_raw_records(value)
            for value in raw_payload.values()
            if isinstance(value, (dict, list))
        ]
        return sum(nested_counts) if nested_counts else 1
    return 1


def request_summary(source_key: str, run_config: Dict[str, Any]) -> str:
    if source_key == "gnews":
        keywords = run_config.get("news_keywords", [])
        return (
            f"{len(keywords)} keyword(s), country={run_config.get('country', '')}, "
            f"language={run_config.get('language', '')}, period={run_config.get('gnews_period', '')}, "
            f"max_results={run_config.get('max_news', '')}"
        )
    if source_key == "bls":
        return "series=" + ", ".join(BLS_CPI_SERIES.values())
    if source_key == "fda":
        return f"search={run_config.get('fda_query', '')}; limit={run_config.get('fda_limit', '')}"
    if source_key == "weather":
        return f"area={run_config.get('weather_area', '')}; limit={run_config.get('weather_limit', '')}; active NOAA alerts"
    if source_key == "apify":
        return (
            f"enabled={run_config.get('use_apify', False)}, token_present={run_config.get('apify_token_present', False)}, "
            f"run_mode={run_config.get('apify_run_mode', 'Skip Apify')}, confirmed={run_config.get('apify_live_confirm', False)}, "
            f"geo={run_config.get('apify_geo', '')}, time_range={run_config.get('apify_time_range', APIFY_SAFE_TIME_RANGE)}, "
            f"max_keywords={run_config.get('apify_max_keywords', APIFY_HARD_KEYWORD_LIMIT)}"
        )
    return ""


def build_collector_evidence(results: Dict[str, Dict[str, Any]], run_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = []
    enabled_sources = run_config.get("enabled_sources", {})
    for source_key in SOURCE_ORDER:
        result = results.get(source_key, {})
        status = result.get("status", "disabled" if not enabled_sources.get(source_key, False) else "not_run")
        normalized_rows = len(result.get("rows", []) or [])
        raw_records = count_raw_records(result.get("raw"), result.get("items"))
        live_request = status in {"success", "failed", "empty"} or (source_key == "apify" and result.get("raw") is not None)
        mock_used = bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
        records.append(
            {
                "source": SOURCE_LABELS[source_key],
                "status": status,
                "endpoint_or_actor": SOURCE_ENDPOINTS[source_key],
                "request_scope": request_summary(source_key, run_config),
                "raw_records_pulled": raw_records,
                "normalized_feature_rows": normalized_rows,
                "live_request_made": "Yes" if live_request else "No",
                "used_in_llm_payload": "Yes" if normalized_rows > 0 else "No",
                "mock_data_used": "Yes" if mock_used else "No",
                "analysis_method": ANALYSIS_METHODS[source_key],
                "error_or_note": result.get("error", "") or "",
            }
        )
    return records


def build_llm_payload(feature_df: pd.DataFrame, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "features": feature_df.to_dict(orient="records")[:NVIDIA_MAX_FEATURE_ROWS],
        "articles": articles[:NVIDIA_MAX_ARTICLES],
    }


def llm_system_prompt() -> str:
    return (
        "You are a retail market intelligence analyst. Ground your answer only in the supplied signals. "
        "Write concise executive guidance for buyers, category managers, demand planners, and supply-chain teams."
    )


def llm_user_prompt(retailer: str, region: str, payload: Dict[str, Any]) -> str:
    return f"""
    Retailer: {retailer}
    Region: {region}

    Signal payload:
    {json.dumps(payload, indent=2, default=str)[:9000]}

    Produce:
    1. Executive summary in 2-3 sentences
    2. Top 3 insights, and for each one cite signal_area, source, risk level (High/Medium/Low), score_reason, dollar_tree_category, planning_owner, and demand_direction
    3. Retail category impact, clearly stating how the signal can become a forecast feature
    4. Recommended buyer/category/demand-planning/supply-chain/compliance actions
    5. Confidence and limitations

    Rules:
    - Ground every claim only in the supplied payload.
    - Do not invent internal sales, POS, inventory, margin, or category-performance facts.
    - If a signal is missing, say it is missing instead of estimating it.
    - Explain why each High/Medium/Low level matters; do not expose the underlying numeric score.
    - Use retailer-neutral business language when the payload includes category, KPI, planning owner, impact hypothesis, and internal-data requirements.
    """


def payload_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def build_base_llm_audit(
    feature_df: pd.DataFrame,
    articles: List[Dict[str, Any]],
    retailer: str,
    region: str,
    model: str,
) -> Dict[str, Any]:
    payload = build_llm_payload(feature_df, articles)
    return {
        "provider": "NVIDIA",
        "model": model,
        "sent_to_llm": False,
        "brief_source": "not_generated",
        "fallback_used": False,
        "fallback_reason": "",
        "mock_data_used": False,
        "feature_rows_available": int(len(feature_df)),
        "feature_rows_sent": int(len(payload["features"])),
        "articles_available": int(len(articles)),
        "articles_sent": int(len(payload["articles"])),
        "payload_hash_sha256": payload_hash(payload),
        "system_prompt": llm_system_prompt(),
        "user_prompt": llm_user_prompt(retailer, region, payload),
        "payload": payload,
        "analysis_contract": "The brief must be grounded only in the collected feature rows and article records shown in this audit view.",
        "brief_generation_strategy": f"Payload capped at {NVIDIA_MAX_FEATURE_ROWS} feature rows and {NVIDIA_MAX_ARTICLES} articles; NVIDIA retries use timeouts {NVIDIA_TIMEOUT_SECONDS} seconds with lightweight backoff.",
    }


def any_mock_used(results: Dict[str, Dict[str, Any]], llm_audit: Dict[str, Any]) -> bool:
    collector_mock = any(
        bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
        for result in results.values()
    )
    return collector_mock or bool(llm_audit.get("mock_data_used", False))


def render_audit_banner(mock_used: bool, brief_source: str) -> None:
    banner_class = "audit-banner warn" if mock_used else "audit-banner"
    title = "Mock Data Detected" if mock_used else "No Mock Data Used In This Run"
    body = (
        "At least one collector or analysis step is marked as using mock data. Review the evidence table below."
        if mock_used
        else f"Every feature row shown below comes from the collector outputs for this run. Brief source: {brief_source}."
    )
    st.markdown(
        f"<div class='{banner_class}'><div class='audit-title'>{escape(title)}</div><div class='audit-body'>{escape(body)}</div></div>",
        unsafe_allow_html=True,
    )


def render_audit_card(label: str, value: str, note: str) -> None:
    st.markdown(
        "<div class='audit-card'>"
        f"<div class='audit-label'>{escape(label)}</div>"
        f"<div class='audit-value'>{escape(value)}</div>"
        f"<div class='audit-note'>{escape(note)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )



def test_nvidia_minimal(api_key: str, model: str) -> Tuple[bool, str]:
    """Minimal GPT-OSS 120B connectivity test used only for diagnosis."""
    if not api_key:
        return False, "NVIDIA API key not provided."
    ok, data, msg = safe_request(
        NVIDIA_CHAT_URL,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json_body={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: connected"}],
            "temperature": 0,
            "max_tokens": 12,
        },
        timeout=30,
    )
    if not ok:
        return False, str(msg)
    try:
        content = str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception:
        content = ""
    if not content:
        return False, "NVIDIA returned no message content."
    return True, content

def validate_nvidia(api_key: str, model: str) -> Tuple[bool, str]:
    if not api_key:
        return False, "NVIDIA key not provided. Brief generation will use a local fallback."
    prompt = "Return exactly: connected"
    ok, data, msg = safe_request(
        NVIDIA_CHAT_URL,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json_body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 8,
        },
        timeout=30,
    )
    if not ok:
        return False, msg
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    content_text = str(content or "").strip()
    return True, f"Connected. Model responded: {content_text or 'ok'}"


def validate_apify(token: str) -> Tuple[bool, str]:
    if not token:
        return False, "Apify token not provided. Apify collectors will be skipped."
    try:
        from apify_client import ApifyClient
    except ImportError:
        return False, "apify-client is not installed. Install requirements before running Apify collectors."
    try:
        client = ApifyClient(token)
        user = client.user().get()
        username = user.get("username") or user.get("email") or "Apify user"
        return True, f"Connected as {username}."
    except Exception as exc:  # pragma: no cover - depends on live Apify service
        return False, str(exc)


def build_cpi_signal(data: Dict[str, Any], label: str, series_id: str, retailer: str) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
    series = data.get("Results", {}).get("series", [])
    rows = []
    for item in (series[0].get("data", []) if series else [])[:24]:
        period = item.get("period", "")
        if period == "M13":
            continue
        try:
            cpi_value = float(item["value"])
        except (TypeError, ValueError, KeyError):
            continue
        rows.append(
            {
                "category": label,
                "series_id": series_id,
                "year": int(item["year"]),
                "period": period,
                "month": item.get("periodName", ""),
                "cpi_value": cpi_value,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return None, df
    df = df.sort_values(["year", "period"]).reset_index(drop=True)
    df["cpi_mom_change_pct"] = df["cpi_value"].pct_change() * 100
    df["cpi_yoy_change_pct"] = df["cpi_value"].pct_change(12) * 100
    latest = df.iloc[-1].to_dict()
    mom = latest.get("cpi_mom_change_pct")
    yoy = latest.get("cpi_yoy_change_pct")
    score = 4.0
    if pd.notna(mom):
        score = min(10.0, max(1.0, 4.0 + float(mom) * 5.0))
    if pd.notna(yoy) and yoy > 4:
        score = min(10.0, score + 1.0)
    mom_label = "unavailable" if pd.isna(mom) else f"{float(mom):.2f}% MoM"
    yoy_label = "unavailable" if pd.isna(yoy) else f"{float(yoy):.2f}% YoY"
    signal_name = "inflation_pressure_score" if label == "Headline CPI" else f"{label.lower().replace(' ', '_')}_cpi_pressure_score"
    signal = {
        "date": f"{int(latest['year'])}-{str(latest['period']).replace('M', '').zfill(2)}",
        "retailer": retailer,
        "region": "US",
        "region_scope": "national",
        "source": "BLS CPI",
        "signal_area": "Inflation" if label == "Headline CPI" else "Category CPI",
        "signal_name": signal_name,
        "signal_value": round(float(score), 2),
        "risk_score": round(float(score), 2),
        "confidence": "High",
        "score_reason": f"{label} CPI latest value {latest['cpi_value']}; change is {mom_label} and {yoy_label}. Score rises with monthly inflation pressure and elevated YoY inflation.",
        "business_impact": f"{label} inflation can affect price sensitivity, category demand, and basket mix.",
        "recommended_action": "Use category CPI as an external regressor and validate against internal category sales.",
        "raw_reference": f"{label}: CPI {latest['cpi_value']}",
    }
    return signal, df


def collect_bls_cpi(bls_key: str = "", retailer: str = "Retailer") -> Dict[str, Any]:
    payload: Dict[str, Any] = {"seriesid": list(BLS_CPI_SERIES.values())}
    if bls_key:
        payload["registrationkey"] = bls_key
    signals = []
    tables = []
    raw = {}
    errors = []
    ok, data, msg = safe_request(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        method="POST",
        headers={"Content-Type": "application/json"},
        json_body=payload,
        timeout=30,
    )
    if not ok:
        return {"status": "failed", "source": "BLS CPI", "error": msg, "raw": None, "rows": []}
    if data.get("status") != "REQUEST_SUCCEEDED":
        return {
            "status": "failed",
            "source": "BLS CPI",
            "error": "; ".join(data.get("message", [])) or "BLS request was not processed.",
            "raw": data,
            "rows": [],
        }
    series_by_id = {
        series.get("seriesID"): series
        for series in data.get("Results", {}).get("series", [])
    }
    for label, series_id in BLS_CPI_SERIES.items():
        series_payload = {"Results": {"series": [series_by_id.get(series_id, {})]}}
        raw[label] = series_payload
        signal, df = build_cpi_signal(series_payload, label, series_id, retailer)
        if signal:
            signals.append(signal)
        if not df.empty:
            tables.append(df)
    if not signals:
        return {"status": "failed", "source": "BLS CPI", "error": "; ".join(errors) or "No CPI rows returned.", "raw": raw, "rows": []}
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    return {"status": "success", "source": "BLS CPI", "error": "; ".join(errors), "raw": raw, "rows": signals, "table": table}


def classify_recall(reason: str) -> Tuple[str, float]:
    text = (reason or "").lower()
    if any(word in text for word in ["lead", "heavy metal", "salmonella", "listeria", "e. coli", "contamination"]):
        return "high_safety_risk", 8.0
    if any(word in text for word in ["undeclared", "allergen", "milk", "peanut", "soy", "tree nut"]):
        return "allergen_risk", 6.5
    if any(word in text for word in ["mislabel", "label"]):
        return "labeling_risk", 4.5
    return "general_recall_risk", 5.0


def extract_upcs(text: str) -> List[str]:
    candidates = re.findall(r"(?:UPC(?:\s*Code)?[:\s]*)?(\d(?:[\s-]?\d){7,13})", text or "", flags=re.IGNORECASE)
    cleaned = []
    for candidate in candidates:
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 14 and digits not in cleaned:
            cleaned.append(digits)
    return cleaned


def adjust_recall_score(base_score: float, classification: str, status: str) -> float:
    score = base_score
    class_text = (classification or "").lower()
    status_text = (status or "").lower()
    if "class i" in class_text:
        score += 1.5
    elif "class ii" in class_text:
        score += 0.8
    if "ongoing" in status_text:
        score += 1.0
    elif "terminated" in status_text:
        score -= 1.0
    return round(min(10.0, max(1.0, score)), 2)


def collect_fda_recalls(query: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
    params = {"search": query, "limit": limit}
    ok, data, msg = safe_request("https://api.fda.gov/food/enforcement.json", params=params, timeout=30)
    if not ok:
        return {"status": "failed", "source": "openFDA", "error": msg, "raw": None, "rows": [], "items": []}
    results = data.get("results", [])
    rows = []
    items = []
    for item in results:
        risk_type, base_score = classify_recall(item.get("reason_for_recall", ""))
        product = item.get("product_description", "Unknown product")
        state = item.get("state", "US")
        classification = item.get("classification", "")
        status = item.get("status", "")
        score = adjust_recall_score(base_score, classification, status)
        upcs = extract_upcs(f"{product} {item.get('code_info', '')}")
        items.append(
            {
                "product": product,
                "reason": item.get("reason_for_recall", ""),
                "state": state,
                "classification": classification,
                "status": status,
                "recall_date": item.get("recall_initiation_date", ""),
                "distribution_pattern": item.get("distribution_pattern", ""),
                "recalling_firm": item.get("recalling_firm", ""),
                "upcs": ", ".join(upcs) if upcs else "",
                "sku_match_status": "unknown",
                "risk_type": risk_type,
                "risk_score": score,
            }
        )
    aggregate_score = max([x["risk_score"] for x in items], default=1.0)
    ongoing_count = sum(1 for x in items if str(x.get("status", "")).lower() == "ongoing")
    class_i_count = sum(1 for x in items if "class i" in str(x.get("classification", "")).lower())
    states = sorted({str(x.get("state", "")).strip() for x in items if str(x.get("state", "")).strip()})
    upc_count = sum(1 for x in items if x.get("upcs"))
    top_risk_type = max(items, key=lambda x: x["risk_score"]).get("risk_type", "none") if items else "none"
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": "US",
        "region_scope": "national_with_state_records",
        "source": "openFDA",
        "signal_area": "Product Recalls",
        "signal_name": "recall_risk_score",
        "signal_value": len(items),
        "risk_score": round(aggregate_score, 2),
        "confidence": "High",
        "score_reason": f"Score uses highest adjusted recall severity. Inputs: {len(items)} records, {ongoing_count} ongoing, {class_i_count} Class I, {upc_count} records with UPCs, top risk type {top_risk_type}.",
        "affected_states": ", ".join(states[:8]) if states else "Unknown",
        "ongoing_count": ongoing_count,
        "class_i_count": class_i_count,
        "upc_record_count": upc_count,
        "sku_match_status": "unknown",
        "affected_category": "Food / snacks / candy / beverages",
        "business_impact": "Food and beverage recalls can trigger inventory withdrawal, substitution demand, and safety review.",
        "recommended_action": f"Prioritize ongoing and Class I recalls, then match UPCs against {retailer} inventory before store-level action.",
        "raw_reference": f"{len(items)} recall records; {ongoing_count} ongoing; {class_i_count} Class I",
    }
    rows.append(signal)
    return {"status": "success", "source": "openFDA", "error": "", "raw": data, "rows": rows, "items": items}


def normalize_weather_area(area: str) -> str:
    candidate = re.sub(r"[^A-Za-z]", "", area or "").upper()
    if len(candidate) == 2:
        return candidate
    return "TX"


def weather_alert_weight(severity: str, urgency: str, certainty: str) -> float:
    severity_score = {
        "extreme": 5.0,
        "severe": 3.0,
        "moderate": 2.0,
        "minor": 1.0,
        "unknown": 1.0,
    }.get(str(severity or "").lower(), 1.0)
    urgency_bonus = {
        "immediate": 1.5,
        "expected": 0.75,
    }.get(str(urgency or "").lower(), 0.0)
    certainty_bonus = {
        "observed": 0.5,
        "likely": 0.5,
    }.get(str(certainty or "").lower(), 0.0)
    return severity_score + urgency_bonus + certainty_bonus


def collect_weather_alerts(area: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
    state_area = normalize_weather_area(area)
    params = {"area": state_area}
    ok, data, msg = safe_request(
        "https://api.weather.gov/alerts/active",
        headers={"User-Agent": "MarketIntelligenceWorkbench/1.0", "Accept": "application/geo+json"},
        params=params,
        timeout=30,
    )
    if not ok:
        return {"status": "failed", "source": "NOAA Weather Alerts", "error": msg, "raw": None, "rows": [], "items": []}
    features = data.get("features", []) if isinstance(data, dict) else []
    selected_alerts = features[: max(1, int(limit))]
    items = []
    score_components = []
    severe_count = 0
    extreme_count = 0
    for alert in selected_alerts:
        props = alert.get("properties", {}) if isinstance(alert, dict) else {}
        severity = props.get("severity", "Unknown")
        urgency = props.get("urgency", "Unknown")
        certainty = props.get("certainty", "Unknown")
        component = weather_alert_weight(severity, urgency, certainty)
        score_components.append(component)
        severity_text = str(severity or "").lower()
        if severity_text == "extreme":
            extreme_count += 1
        if severity_text in {"severe", "extreme"}:
            severe_count += 1
        items.append(
            {
                "event": props.get("event", ""),
                "severity": severity,
                "urgency": urgency,
                "certainty": certainty,
                "headline": props.get("headline", ""),
                "area_desc": props.get("areaDesc", ""),
                "effective": props.get("effective", ""),
                "expires": props.get("expires", ""),
                "instruction": props.get("instruction", ""),
                "risk_component": round(component, 2),
            }
        )
    risk_score = round(min(10.0, sum(score_components)), 2) if items else 0.0
    if items:
        score_reason = (
            f"Score sums weighted active NOAA alerts for {state_area}, capped at 10. "
            f"Inputs: {len(items)} alert(s), {severe_count} severe/extreme, {extreme_count} extreme; "
            f"severity/urgency/certainty components total {sum(score_components):.2f}."
        )
        raw_reference = f"{len(items)} active NOAA alert(s); top event: {items[0].get('event') or 'Unknown'}"
        recommended_action = "Check affected counties against store and DC routes; use alert severity as a short-horizon disruption and emergency-demand feature."
    else:
        score_reason = f"NOAA returned 0 active alerts for {state_area}. Score is 0 because no current weather disruption signal is present."
        raw_reference = "0 active NOAA alerts"
        recommended_action = "Keep weather feature at baseline for this state, then refresh before short-horizon replenishment decisions."
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": state_area,
        "region_scope": "state_weather_alerts",
        "source": "NOAA Weather Alerts",
        "signal_area": "Weather Risk",
        "signal_name": "supply_chain_weather_risk_score",
        "signal_value": len(items),
        "risk_score": risk_score,
        "confidence": "High",
        "score_reason": score_reason,
        "alert_count": len(items),
        "severe_or_extreme_count": severe_count,
        "extreme_count": extreme_count,
        "business_impact": "Active weather alerts can disrupt store traffic, DC-to-store routes, staffing, replenishment timing, and emergency-demand categories.",
        "recommended_action": recommended_action,
        "raw_reference": raw_reference,
    }
    return {"status": "success", "source": "NOAA Weather Alerts", "error": "", "raw": data, "rows": [signal], "items": items}


def gnews_package_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
    try:
        from gnews import GNews
    except ImportError:
        return None
    google_news = GNews(language=language, country=country, period=period, max_results=max_results)
    return google_news.get_news(keyword)


def google_news_rss_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> List[Dict[str, Any]]:
    # Google News RSS supports a "when:" query operator. GNews package is preferred when installed.
    query = quote_plus(f"{keyword} when:{period}")
    country_code = country.upper()
    lang_code = language.lower()
    url = f"https://news.google.com/rss/search?q={query}&hl={lang_code}-{country_code}&gl={country_code}&ceid={country_code}:{lang_code}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    articles = []
    for item in root.findall(".//item")[:max_results]:
        source_node = item.find("source")
        articles.append(
            {
                "title": item.findtext("title", default=""),
                "description": item.findtext("description", default=""),
                "published date": item.findtext("pubDate", default=""),
                "url": item.findtext("link", default=""),
                "publisher": source_node.text if source_node is not None else "",
            }
        )
    return articles


def clean_news_description(description: str) -> str:
    text = re.sub(r"<[^>]+>", " ", description or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def article_days_old(published_date: str) -> Optional[int]:
    if not published_date:
        return None
    try:
        published = parsedate_to_datetime(published_date)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
    except (TypeError, ValueError):
        return None


def classify_news_title(title: str, description: str) -> Tuple[str, float, str]:
    text = f"{title} {description}".lower()
    if any(word in text for word in ["recall", "contamination", "lawsuit", "closure", "closing", "tariff", "warning"]):
        return "risk_event", 7.0, "negative"
    if any(word in text for word in ["inflation", "prices", "freight", "cost", "margin"]):
        return "price_pressure", 6.0, "negative"
    if any(word in text for word in ["deal", "sale", "promotion", "coupon", "holiday", "seasonal"]):
        return "demand_opportunity", 6.5, "positive"
    if any(word in text for word in ["earnings", "forecast", "guidance", "outlook"]):
        return "financial_update", 5.5, "neutral"
    return "general_market_news", 3.5, "neutral"


def collect_gnews(keywords: List[str], country: str, language: str, period: str, max_results: int, retailer: str = "Retailer") -> Dict[str, Any]:
    all_articles = []
    errors = []
    per_keyword_limit = max(1, int(max_results / max(1, len(keywords))))
    for keyword in keywords:
        try:
            articles = gnews_package_collect(keyword, country, language, period, per_keyword_limit)
            if articles is None:
                articles = google_news_rss_collect(keyword, country, language, period, per_keyword_limit)
            for article in articles or []:
                description = clean_news_description(article.get("description", ""))
                event_type, score, sentiment = classify_news_title(article.get("title", ""), description)
                publisher = article.get("publisher", "")
                if isinstance(publisher, dict):
                    publisher = publisher.get("title") or publisher.get("href") or ""
                published_date = article.get("published date") or article.get("published_date", "")
                days_old = article_days_old(published_date)
                if days_old is not None and days_old > 30:
                    score = max(1.0, score - 1.0)
                all_articles.append(
                    {
                        "keyword": keyword,
                        "title": article.get("title", ""),
                        "description": description,
                        "published_date": published_date,
                        "days_old": days_old,
                        "publisher": publisher,
                        "url": article.get("url", ""),
                        "source_tier": source_confidence(str(publisher)),
                        "event_type": event_type,
                        "sentiment": sentiment,
                        "risk_score": score,
                        "confidence": source_confidence(str(publisher)),
                    }
                )
        except Exception as exc:
            errors.append(f"{keyword}: {exc}")

    deduped = []
    seen = set()
    for article in all_articles:
        key = article["url"] or article["title"]
        if key and key not in seen:
            seen.add(key)
            deduped.append(article)

    if not deduped:
        return {
            "status": "failed" if errors else "empty",
            "source": "GNews",
            "error": "; ".join(errors) if errors else "No meaningful articles returned for selected keywords.",
            "raw": [],
            "rows": [],
            "items": [],
        }

    score = round(float(pd.Series([a["risk_score"] for a in deduped]).mean()), 2) if deduped else 1.0
    negative_count = sum(1 for article in deduped if article.get("sentiment") == "negative")
    high_conf_count = sum(1 for article in deduped if article.get("confidence") == "High")
    event_counts = pd.Series([article.get("event_type", "unknown") for article in deduped]).value_counts().to_dict()
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": country.upper(),
        "region_scope": "country_news",
        "source": "GNews / Google News RSS",
        "signal_area": "Retail News",
        "signal_name": "news_risk_score",
        "signal_value": len(deduped),
        "risk_score": score,
        "confidence": "Medium",
        "score_reason": f"Average article risk across {len(deduped)} deduped articles; {negative_count} negative articles; {high_conf_count} high-confidence publishers; event mix {event_counts}.",
        "business_impact": "Recent news can reveal competitor moves, pricing pressure, recalls, store changes, and supply-chain risk.",
        "recommended_action": "Review high-risk articles and use NVIDIA classification before executive distribution.",
        "raw_reference": f"{len(deduped)} articles",
    }
    return {"status": "success", "source": "GNews", "error": "; ".join(errors), "raw": deduped, "rows": [signal], "items": deduped}


def get_apify_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    if hasattr(obj, key):
        return getattr(obj, key)
    try:
        return obj[key]
    except (TypeError, KeyError, AttributeError):
        return None


def collect_apify_trends(
    token: str,
    keywords: List[str],
    geo: str,
    time_range: str,
    retailer: str = "Retailer",
    max_keywords: int = APIFY_HARD_KEYWORD_LIMIT,
) -> Dict[str, Any]:
    if not token:
        return {"status": "skipped", "source": "Apify Trends", "error": "No Apify token provided.", "raw": None, "rows": [], "items": []}
    try:
        from apify_client import ApifyClient
    except ImportError:
        return {"status": "failed", "source": "Apify Trends", "error": "apify-client is not installed.", "raw": None, "rows": [], "items": []}
    try:
        client = ApifyClient(token)
        safe_max_keywords = min(max(1, int(max_keywords)), APIFY_HARD_KEYWORD_LIMIT)
        safe_time_range = time_range if time_range in APIFY_ALLOWED_TIME_RANGES else APIFY_SAFE_TIME_RANGE
        safe_time_range = safe_time_range or APIFY_SAFE_TIME_RANGE
        selected_keywords = [kw for kw in keywords if kw][:safe_max_keywords]
        if not selected_keywords:
            return {"status": "skipped", "source": "Apify Trends", "error": "No trend keywords provided.", "raw": None, "rows": [], "items": []}
        run_input = {"geo": geo, "searchTerms": selected_keywords, "timeRange": safe_time_range}
        run = client.actor("apify/google-trends-scraper").call(run_input=run_input)
        dataset_id = get_apify_value(run, "defaultDatasetId") or get_apify_value(run, "default_dataset_id")
        if not dataset_id:
            return {
                "status": "failed",
                "source": "Apify Trends",
                "error": "Apify run completed but no default dataset ID was found.",
                "raw": run_input,
                "rows": [],
                "items": [],
            }
        items = list(client.dataset(dataset_id).iterate_items())
    except Exception as exc:
        return {"status": "failed", "source": "Apify Trends", "error": str(exc), "raw": None, "rows": [], "items": []}

    region_rows = []
    for item in items:
        keyword = item.get("searchTerm")
        for rank, region in enumerate(item.get("interestBySubregion", []) or [], start=1):
            values = region.get("value") or []
            if values:
                region_rows.append(
                    {
                        "keyword": keyword,
                        "region": region.get("geoName", ""),
                        "interest_score": values[0],
                        "rank": rank,
                    }
                )
    top_score = max([float(x["interest_score"]) for x in region_rows], default=0.0)
    signal_score = round(min(10.0, max(1.0, top_score / 10.0)), 2) if top_score else 1.0
    signal = {
        "date": utc_now()[:10],
        "retailer": retailer,
        "region": geo,
        "region_scope": "trend_geo",
        "source": "Apify Google Trends",
        "signal_area": "Search Demand",
        "signal_name": "search_demand_score",
        "signal_value": top_score,
        "risk_score": signal_score,
        "confidence": "Medium",
        "score_reason": f"Score is top regional Google Trends interest divided by 10. Run hard-limited to {len(selected_keywords)} keyword(s) over {safe_time_range} to control Apify quota and memory.",
        "business_impact": "Search interest can reveal early demand shifts, promotional interest, and seasonal spikes.",
        "recommended_action": "Compare rising regions against sales, inventory, and competitor promotion calendars.",
        "raw_reference": f"{len(region_rows)} regional trend rows",
    }
    return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [signal], "items": region_rows}


def _business_level_reason(reason: Any) -> str:
    """
    Convert legacy numeric-score wording into business-facing level wording
    for the Executive Brief only. Internal score calculations and stored
    score_reason values remain unchanged.
    """
    value = str(reason or "No level rationale available.").strip()

    # Common legacy phrases from the existing collectors.
    value = re.sub(
        r"^Score sums weighted active NOAA alerts for ([^,]+), capped at 10\.\s*Inputs:\s*",
        r"The current level reflects weighted active NOAA alerts for \1. Evidence: ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^Score uses highest adjusted recall severity\.\s*Inputs:\s*",
        "The current level reflects the highest adjusted recall severity. Evidence: ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^Score is based on\s*",
        "The current level is based on ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^Score reflects\s*",
        "The current level reflects ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\bscore\b",
        "level",
        value,
        flags=re.IGNORECASE,
    )
    value = value.replace("capped at 10", "normalized to the application's internal scale")
    return value


def generate_fallback_brief(feature_df: pd.DataFrame, retailer: str, region: str) -> str:
    """
    Grounded local Executive Brief used only when NVIDIA is unavailable.
    The wording mirrors the High / Medium / Low business-facing UI and does
    not expose numeric risk scores.
    """
    if feature_df.empty:
        return (
            "EXECUTIVE SUMMARY\n"
            "No external signals were collected for this run. Enable at least one source and run the command center again before using the output for planning.\n\n"
            "CONFIDENCE AND LIMITATIONS\n"
            "No signal level can be explained because no feature rows are available."
        )

    strongest = feature_df.sort_values("risk_score", ascending=False).head(3)
    avg_score = float(feature_df["risk_score"].mean())
    overall_level = risk_band(avg_score)
    top = strongest.iloc[0]
    sources = ", ".join(sorted({str(src) for src in feature_df["source"].dropna().tolist()}))
    region_label = str(region or "selected market").strip()

    lines = [
        "EXECUTIVE SUMMARY",
        (
            f"The current external environment for {region_label} shows an overall {overall_level} "
            f"signal level across {len(feature_df)} forecast-ready signals."
        ),
        (
            f"The strongest current signal is {top['signal_area']} at "
            f"{risk_band(float(top['risk_score']))} level from {top['source']}."
        ),
        f"This brief is grounded only in collected source output: {sources}.",
        "",
        "TOP SIGNAL EVIDENCE",
    ]

    for _, row in strongest.iterrows():
        level = risk_band(float(row["risk_score"]))
        reason = _business_level_reason(row.get("score_reason", "No level rationale available."))
        lines.append(
            f"- {row['signal_area']} — {row['source']} — {level}: {reason}"
        )
        lines.append(
            f"  Business impact: {row.get('business_impact', 'No business impact available.')}"
        )

    lines.extend(
        [
            "",
            "PLANNING RELEVANCE",
            "- Treat each signal level as an external planning or forecasting input candidate, not as a final demand forecast.",
            "- Validate these external signals against relevant internal POS, category, store, promotion, inventory, and forecast data before operational use.",
            "",
            "RECOMMENDED ACTIONS",
        ]
    )

    for _, row in strongest.iterrows():
        lines.append(
            f"- {row.get('recommended_action', 'Review this signal with the appropriate planning owner.')}"
        )

    lines.extend(
        [
            "",
            "CONFIDENCE AND LIMITATIONS",
            "- Signal levels are explainable directional indicators derived from public external data; they do not prove actual retailer demand movement.",
            "- Internal performance data is still required to validate category, store, inventory, and forecast impact.",
        ]
    )

    return "\n".join(lines)

def _nvidia_chat_request(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout_seconds: int,
) -> Tuple[bool, str, str, int]:
    """Make one compact NVIDIA chat-completions request."""
    started = time.monotonic()
    ok, data, msg = safe_request(
        NVIDIA_CHAT_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json_body={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        },
        timeout=timeout_seconds,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if not ok:
        return False, "", str(msg), elapsed_ms

    content = ""
    if isinstance(data, dict):
        try:
            message = data.get("choices", [{}])[0].get("message", {})
            content = str(message.get("content") or "").strip()
        except Exception:
            content = ""

    if not content:
        return False, "", "NVIDIA returned no message content", elapsed_ms

    return True, content, "", elapsed_ms


def _compact_signal_for_nvidia(row: pd.Series) -> Dict[str, Any]:
    """
    Keep only the business fields NVIDIA needs to understand one signal.
    The numeric score is used internally to derive High/Medium/Low but is
    not exposed to the final business-facing brief.
    """
    fields = [
        "signal_area",
        "source",
        "score_reason",
        "business_impact",
        "recommended_action",
        "confidence",
        "enterprise_kpi",
        "planning_owner",
        "demand_direction",
        "impact_hypothesis",
        "internal_data_needed",
        "forecast_feature",
        "latest_value",
        "mom_pct",
        "yoy_pct",
    ]

    compact: Dict[str, Any] = {}
    for key in fields:
        if key not in row.index:
            continue
        value = row.get(key)
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        compact[key] = value

    try:
        compact["signal_level"] = risk_band(float(row.get("risk_score", 0)))
    except Exception:
        compact["signal_level"] = "Unknown"

    return compact


def _normalize_executive_brief_format(brief: str) -> str:
    """Normalize LLM brief output into clean headings and bullets for the UI."""
    if not brief:
        return brief

    cleaned_lines: List[str] = []
    in_top_insights = False

    for raw_line in brief.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue

        # Remove common markdown heading markers.
        line = re.sub(r"^#{1,6}\s*", "", line).strip()

        upper = line.upper().rstrip(":")
        if upper in {
            "EXECUTIVE SUMMARY",
            "TOP INSIGHTS",
            "PLANNING RELEVANCE",
            "RECOMMENDED ACTIONS",
            "CONFIDENCE AND LIMITATIONS",
        }:
            cleaned_lines.append(upper)
            in_top_insights = upper == "TOP INSIGHTS"
            continue

        # Drop markdown table separator rows.
        if "|" in line and re.fullmatch(r"[\s|:\-]+", line):
            continue

        # Convert markdown-table Top Insight rows to readable bullets.
        if in_top_insights and "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and cells[0].lower() in {"#", "signal", "no.", "no"}:
                continue
            if cells and re.fullmatch(r"\d+", cells[0] or ""):
                cells = cells[1:]
            if len(cells) >= 5:
                signal, source, level, meaning = cells[:4]
                relevance = " | ".join(cells[4:]).strip()
                line = f"- {signal} — {source} — {level}: {meaning}"
                if relevance:
                    line += f" Planning relevance: {relevance}"
            else:
                line = "- " + " — ".join(cells)

        # Normalize numbered list items into bullets inside business sections.
        line = re.sub(r"^\d+[\.\)]\s+", "- ", line)

        # Remove markdown emphasis characters that look awkward in Streamlit text.
        line = line.replace("**", "").replace("__", "")

        cleaned_lines.append(line)

    # Collapse excessive blank lines.
    result = "\n".join(cleaned_lines)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def generate_nvidia_brief(
    api_key: str,
    model: str,
    feature_df: pd.DataFrame,
    articles: List[Dict[str, Any]],
    retailer: str,
    region: str,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Sumit's simplified NVIDIA flow:
      1. Keep only the three most important signals.
      2. Convert each one into a compact business payload.
      3. Send all three compact signals in ONE GPT-OSS 120B request.
      4. Use the existing grounded local fallback if NVIDIA is unavailable.

    This avoids the earlier multi-call / second-synthesis pattern that was
    repeatedly timing out in the office environment.
    """
    requested_model = (model or DEFAULT_NVIDIA_MODEL).strip()
    audit = build_base_llm_audit(feature_df, articles, retailer, region, requested_model)

    if not api_key:
        audit.update(
            {
                "provider": "Local deterministic fallback",
                "model": "rule_based_summary",
                "brief_source": "fallback",
                "fallback_used": True,
                "fallback_reason": "No NVIDIA API key provided. No external LLM call was made.",
                "brief_generation_strategy": "No NVIDIA key; local grounded fallback used.",
            }
        )
        return generate_fallback_brief(feature_df, retailer, region), "fallback", audit

    if feature_df.empty:
        audit.update(
            {
                "provider": "Local deterministic fallback",
                "model": "rule_based_summary",
                "brief_source": "fallback",
                "fallback_used": True,
                "fallback_reason": "No signal rows were available for NVIDIA analysis.",
                "brief_generation_strategy": "No signal rows; local grounded fallback used.",
            }
        )
        return generate_fallback_brief(feature_df, retailer, region), "fallback", audit

    working = feature_df.copy()
    if "risk_score" in working.columns:
        working = working.sort_values("risk_score", ascending=False)
    working = working.head(3)

    compact_signals = [_compact_signal_for_nvidia(row) for _, row in working.iterrows()]
    compact_payload = {
        "signals": compact_signals,
        "supporting_news": [
            {
                "title": article.get("title", ""),
                "source": article.get("source", article.get("publisher", "")),
            }
            for article in (articles or [])[:2]
        ],
    }

    system_prompt = (
        "You are a senior retail market intelligence analyst. "
        "Create a concise executive brief only from the three compact external signals supplied. "
        "Do not invent internal sales, POS, inventory, margin, customer, or forecast values."
    )

    user_prompt = f"""
Application: Market Intelligence Command Center
Market/region: {region}

Top external signals:
{json.dumps(compact_payload, indent=2, default=str)}

Return the brief in EXACTLY this plain-text structure:

EXECUTIVE SUMMARY
<2 to 3 concise sentences>

TOP INSIGHTS
- <Signal name> — <Source> — <High/Medium/Low>: <meaning>. Planning relevance: <concise relevance>.
- <Signal name> — <Source> — <High/Medium/Low>: <meaning>. Planning relevance: <concise relevance>.
- <Signal name> — <Source> — <High/Medium/Low>: <meaning>. Planning relevance: <concise relevance>.

PLANNING RELEVANCE
- <concise planning point>
- <concise planning point>
- <concise planning point>

RECOMMENDED ACTIONS
- <concise action>
- <concise action>
- <concise action>

CONFIDENCE AND LIMITATIONS
<1 to 2 concise sentences>

Rules:
- Use only the supplied signals.
- Do not expose numeric risk scores.
- Do not invent internal company performance facts.
- NEVER use a markdown table, pipes (|), numbered tables, or raw markdown syntax.
- Use simple bullet points only.
- Keep the final answer concise and business-readable.
"""

    started = time.monotonic()
    ok, content, error, elapsed_ms = _nvidia_chat_request(
        api_key=api_key,
        model=requested_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=520,
        timeout_seconds=45,
    )

    attempt = {
        "attempt": 1,
        "timeout_seconds": 45,
        "elapsed_ms": elapsed_ms,
        "success": ok,
        "technical_error": error,
    }

    if not ok:
        brief_source, friendly_reason = friendly_nvidia_failure(error)
        audit.update(
            {
                "provider": "Local deterministic fallback",
                "model": "rule_based_summary",
                "brief_source": brief_source,
                "fallback_used": True,
                "fallback_reason": friendly_reason,
                "technical_error": error,
                "nvidia_attempts": [attempt],
                "feature_rows_sent": int(len(working)),
                "articles_sent": int(min(len(articles or []), 2)),
                "payload": compact_payload,
                "payload_hash_sha256": payload_hash(compact_payload),
                "brief_generation_strategy": (
                    f"Single compact NVIDIA request using {requested_model}. "
                    f"Only the top {len(working)} signals and up to two supporting news titles were sent. "
                    "The request failed, so the grounded local fallback was used."
                ),
            }
        )
        return generate_fallback_brief(feature_df, retailer, region), brief_source, audit

    final_text = _normalize_executive_brief_format(content)
    audit.update(
        {
            "provider": "NVIDIA",
            "model": requested_model,
            "sent_to_llm": True,
            "brief_source": "nvidia",
            "fallback_used": False,
            "fallback_reason": "",
            "response_chars": len(final_text),
            "nvidia_attempts": [attempt],
            "feature_rows_sent": int(len(working)),
            "articles_sent": int(min(len(articles or []), 2)),
            "payload": compact_payload,
            "payload_hash_sha256": payload_hash(compact_payload),
            "brief_generation_strategy": (
                f"Single compact NVIDIA request using {requested_model}; "
                f"top {len(working)} signals only, with no per-signal calls and no second synthesis call."
            ),
        }
    )
    return final_text, "nvidia", audit

def render_metric_card(title: str, value: str, note: str, confidence: str = "", value_class: str = "") -> None:
    level_key = str(value or "").strip().lower() if str(value or "").strip().lower() in {"high", "medium", "low"} else ""
    level_class = f" level-{level_key}" if level_key else ""
    pill = f'<span class="pill {confidence_class(confidence)}">{confidence}</span>' if confidence and not level_key else ""
    note_html = f'<div class="metric-note">{note}</div>' if note else ""
    html = (
        f'<div class="metric-card{level_class}">'
        f'<div class="metric-label">{title}</div>'
        f'<div class="metric-value{level_class}{" " + value_class if value_class else ""}">{value}</div>'
        f"{pill}"
        f"{note_html}"
        "</div>"
    )
    st.markdown(
        html,
        unsafe_allow_html=True,
    )


def style_level_dataframe(df: pd.DataFrame):
    def level_style(value: Any) -> str:
        level = str(value or "").strip().lower()
        if level == "high":
            return "background-color:#FEE2E2;color:#B91C1C;font-weight:800"
        if level == "medium":
            return "background-color:#FEF3C7;color:#B45309;font-weight:800"
        if level == "low":
            return "background-color:#DCFCE7;color:#15803D;font-weight:800"
        return ""
    return df.style.map(level_style)


def compute_composite_scores(feature_df: pd.DataFrame) -> Dict[str, float]:
    if feature_df.empty:
        return {"Market Opportunity": 0.0, "Market Risk": 0.0, "Forecast Impact": 0.0}
    area_scores: Dict[str, float] = {}
    for _, row in feature_df.iterrows():
        if pd.isna(row.get("risk_score")):
            continue
        area = str(row["signal_area"])
        area_scores[area] = max(area_scores.get(area, 0.0), float(row["risk_score"]))
    opportunity = max(
        area_scores.get("Retail News", 0.0),
        area_scores.get("Search Demand", 0.0),
        area_scores.get("Category CPI", 0.0) * 0.7,
    )
    risk = max(
        area_scores.get("Product Recalls", 0.0),
        area_scores.get("Weather Risk", 0.0),
        area_scores.get("Inflation", 0.0),
        area_scores.get("Category CPI", 0.0),
    )
    impact = min(10.0, (opportunity * 0.45) + (risk * 0.45) + (len(feature_df) * 0.25))
    return {
        "Market Opportunity": round(opportunity, 2),
        "Market Risk": round(risk, 2),
        "Forecast Impact": round(impact, 2),
    }


def compute_dollar_tree_kpis(feature_df: pd.DataFrame) -> Dict[str, float]:
    if feature_df.empty:
        return {
            "Value Basket Pressure": 0.0,
            "Safety And Compliance Risk": 0.0,
            "Supply Chain Disruption": 0.0,
            "Demand Signal Priority": 0.0,
        }
    def max_for(column: str, values: List[str]) -> float:
        if column not in feature_df.columns:
            return 0.0
        mask = feature_df[column].astype(str).isin(values)
        if not mask.any():
            return 0.0
        return float(feature_df.loc[mask, "risk_score"].max())

    value_pressure = max(
        max_for("enterprise_kpi", ["Value Basket Pressure", "Consumer Wallet Pressure", "Consumables Demand Pressure", "Household Essentials Pressure"]),
        max_for("signal_area", ["Inflation", "Category CPI"]),
    )
    safety = max(max_for("enterprise_kpi", ["Safety And Compliance Risk"]), max_for("signal_area", ["Product Recalls"]))
    disruption = max(max_for("enterprise_kpi", ["Supply Chain Disruption Risk"]), max_for("signal_area", ["Weather Risk"]))
    demand = max(
        max_for("enterprise_kpi", ["Demand Interest Spike", "Market Event Risk"]),
        max_for("signal_area", ["Search Demand", "Retail News"]),
    )
    return {
        "Value Basket Pressure": round(value_pressure, 2),
        "Safety And Compliance Risk": round(safety, 2),
        "Supply Chain Disruption": round(disruption, 2),
        "Demand Signal Priority": round(demand, 2),
    }


def build_trust_metrics(run: Dict[str, Any], feature_df: pd.DataFrame) -> List[Dict[str, str]]:
    results = run.get("results", {})
    llm_audit = run.get("llm_audit", {})
    evidence_records = build_collector_evidence(results, run.get("run_config", {}))
    live_sources = sum(1 for record in evidence_records if record.get("live_request_made") == "Yes")
    raw_records = sum(int(record.get("raw_records_pulled", 0) or 0) for record in evidence_records)
    mock_used = any_mock_used(results, llm_audit)
    brief_source = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
    return [
        {"label": "Mock Data", "value": "No" if not mock_used else "Yes", "note": "Collector and LLM audit flags inspected.", "state": "good" if not mock_used else "warn"},
        {"label": "Live Sources", "value": str(live_sources), "note": "Sources that made a live request or returned live evidence.", "state": "good"},
        {"label": "Raw Records", "value": str(raw_records), "note": "Inspectable source records behind the normalized rows.", "state": "good" if raw_records else "warn"},
        {"label": "Feature Rows", "value": str(len(feature_df)), "note": "Forecast-ready external signal rows generated.", "state": "good" if len(feature_df) else "warn"},
        {"label": "Brief Mode", "value": brief_source, "note": display_fallback_reason(llm_audit.get("fallback_reason") or "AI brief grounded in shown payload."), "state": "good" if brief_source == "NVIDIA" else "warn"},
    ]


def render_trust_panel(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
    cards = []
    for metric in build_trust_metrics(run, feature_df):
        cards.append(
            f"<div class='trust-card {escape(metric.get('state', 'good'))}'>"
            f"<div class='trust-label'>{escape(metric['label'])}</div>"
            f"<div class='trust-value'>{escape(metric['value'])}</div>"
            f"<div class='trust-note'>{escape(metric['note'])}</div>"
            "</div>"
        )
    st.markdown("<div class='trust-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def build_previous_run_comparison(feature_df: pd.DataFrame, previous_run: Optional[Dict[str, Any]]) -> pd.DataFrame:
    if feature_df.empty or not previous_run:
        return pd.DataFrame()
    previous_df = previous_run.get("feature_df", pd.DataFrame())
    if previous_df is None or previous_df.empty:
        return pd.DataFrame()
    if "dollar_tree_category" not in previous_df.columns:
        previous_df = enrich_feature_rows_for_retailer(previous_df, str(previous_run.get("run_config", {}).get("retailer", "Retailer")))
    key_cols = [col for col in ["source", "signal_name", "region"] if col in feature_df.columns and col in previous_df.columns]
    if not key_cols:
        return pd.DataFrame()
    current_cols = key_cols + [col for col in ["signal_area", "dollar_tree_category", "planning_owner", "risk_score"] if col in feature_df.columns]
    previous_cols = key_cols + [col for col in ["risk_score"] if col in previous_df.columns]
    current = feature_df[current_cols].copy()
    previous = previous_df[previous_cols].copy()
    current = current.rename(columns={"risk_score": "current_score"})
    previous = previous.rename(columns={"risk_score": "previous_score"})
    merged = current.merge(previous, on=key_cols, how="left")
    if "previous_score" not in merged.columns:
        return pd.DataFrame()
    merged["previous_score"] = pd.to_numeric(merged["previous_score"], errors="coerce")
    merged["current_score"] = pd.to_numeric(merged["current_score"], errors="coerce")
    merged["delta"] = (merged["current_score"] - merged["previous_score"]).round(2)
    merged["movement"] = merged["delta"].apply(lambda x: "New" if pd.isna(x) else "Increased" if x > 0.25 else "Decreased" if x < -0.25 else "Stable")
    return merged.sort_values(["movement", "current_score"], ascending=[True, False])


def render_previous_run_comparison(feature_df: pd.DataFrame, previous_run: Optional[Dict[str, Any]]) -> None:
    comparison_df = build_previous_run_comparison(feature_df, previous_run)
    if comparison_df.empty:
        st.info("Previous run comparison will appear after at least two runs in this session. The comparison matches rows by source, signal name, and region.")
        return
    inc = int((comparison_df["movement"] == "Increased").sum())
    dec = int((comparison_df["movement"] == "Decreased").sum())
    stable = int((comparison_df["movement"] == "Stable").sum())
    cols = st.columns(4)
    with cols[0]:
        render_metric_card("Compared Rows", str(len(comparison_df)), "Matched by source, signal, and region.")
    with cols[1]:
        render_metric_card("Increased", str(inc), "Signals that moved to a higher level.")
    with cols[2]:
        render_metric_card("Decreased", str(dec), "Signals that moved to a lower level.")
    with cols[3]:
        render_metric_card("Stable", str(stable), "Signals that remained at the same level.")
    display_comparison = comparison_df.copy()
    display_comparison["Previous Level"] = display_comparison["previous_score"].apply(lambda x: "New" if pd.isna(x) else risk_band(float(x)))
    display_comparison["Current Level"] = display_comparison["current_score"].apply(lambda x: risk_band(float(x or 0)))
    visible_cols = [col for col in ["source", "signal_area", "signal_name", "region", "dollar_tree_category", "planning_owner", "Previous Level", "Current Level", "movement"] if col in display_comparison.columns]
    st.dataframe(style_level_dataframe(display_comparison[visible_cols]), width="stretch", hide_index=True)


def validation_guidance_for_row(row: pd.Series) -> Dict[str, str]:
    area = str(row.get("signal_area", "")).lower()
    feature = str(row.get("forecast_feature", "")).lower()
    if "recall" in area or "recall" in feature:
        return {
            "validation_analysis": "Match UPC/vendor/product text to SKU master, then compare affected-store inventory and substitution sales before and after recall date.",
            "validation_metric": "UPC match rate, exposed on-hand units, substitute category lift, withdrawal completion rate",
        }
    if "weather" in area or "weather" in feature:
        return {
            "validation_analysis": "Join alerts to store/DC geography and compare affected stores against unaffected stores for traffic, sales, and replenishment delays.",
            "validation_metric": "Affected vs control sales delta, late delivery count, emergency-category uplift",
        }
    if "cpi" in feature or "inflation" in area:
        return {
            "validation_analysis": "Join CPI pressure to weekly category sales, basket mix, unit velocity, and price changes to test trade-down behavior.",
            "validation_metric": "Category unit lift, average basket shift, price sensitivity, margin pressure",
        }
    if "trends" in feature or "search" in area:
        return {
            "validation_analysis": "Compare regional search interest against store traffic, sales velocity, and promotion calendar in the same region and week.",
            "validation_metric": "Search-to-sales lead correlation, regional conversion lift, promotion-adjusted demand",
        }
    if "news" in feature or "news" in area:
        return {
            "validation_analysis": "Tag event dates and compare category/store performance before and after the news event while controlling for promotions.",
            "validation_metric": "Pre/post category variance, forecast error reduction, event-attributed exception count",
        }
    return {
        "validation_analysis": "Join this external signal to internal POS, inventory, product hierarchy, promotion, and store data; test whether it explains forecast variance.",
        "validation_metric": "Forecast error reduction, category sales variance, inventory exception count",
    }


def build_internal_validation_plan(feature_df: pd.DataFrame) -> pd.DataFrame:
    if feature_df.empty:
        return pd.DataFrame()
    records = []
    for _, row in feature_df.sort_values("risk_score", ascending=False).iterrows():
        guidance = validation_guidance_for_row(row)
        records.append(
            {
                "external_signal": f"{row.get('signal_area', 'Signal')} / {row.get('signal_name', '')}",
                "dollar_tree_category": row.get("dollar_tree_category", ""),
                "planning_owner": row.get("planning_owner", ""),
                "internal_data_needed": row.get("internal_data_needed", ""),
                "validation_analysis": guidance["validation_analysis"],
                "validation_metric": guidance["validation_metric"],
                "decision_use": "Promote to model feature if it improves forecast error or explains planning exceptions.",
            }
        )
    return pd.DataFrame(records)


def render_internal_validation_agent(feature_df: pd.DataFrame) -> None:
    plan_df = build_internal_validation_plan(feature_df)
    if plan_df.empty:
        st.info("Internal validation guidance will appear after feature rows are generated.")
        return
    st.dataframe(plan_df, width="stretch", hide_index=True)


def render_enterprise_context(retailer_name: str) -> None:
    st.markdown(
        "<div class='enterprise-band'>"
        f"<div class='enterprise-band-title'>{escape(retailer_name)} External Signal Control Layer</div>"
        "<div class='enterprise-band-copy'>"
        "This view translates public signals into category, owner, forecast-feature, and action language for buyers, category managers, demand planners, compliance, and supply-chain teams. "
        "It does not claim internal category performance until POS, inventory, product hierarchy, promotion, vendor, and store/DC data are connected."
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_results_command_header(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
    run_config = run.get("run_config", {})
    retailer_name = str(run_config.get("retailer", retailer_label))
    llm_audit = run.get("llm_audit", {})
    source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
    avg_score = float(feature_df["risk_score"].mean()) if not feature_df.empty and "risk_score" in feature_df.columns else 0.0
    top_label = "No signal"
    if not feature_df.empty:
        top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
        top_label = f"{top.get('signal_area', 'Signal')} / {risk_band(float(top.get('risk_score', 0) or 0))}"
    brief_mode = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
    meta = [
        ("Run Time", str(run.get("timestamp", ""))),
        ("Brief Mode", brief_mode),
        ("Sources", f"{source_count} source(s)"),
        ("Overall Level", risk_band(avg_score)),
    ]
    meta_html = "".join(
        f"<div class='result-meta-cell'><div class='result-meta-label'>{escape(label)}</div><div class='result-meta-value'>{escape(value)}</div></div>"
        for label, value in meta
    )
    note = display_fallback_reason(llm_audit.get("fallback_reason") or "Executive brief is grounded in the normalized source rows shown in this run.")
    st.markdown(
        "<div class='result-command'>"
        "<div>"
        "<div class='config-eyebrow'>Results Command Center</div>"
        f"<div class='result-command-title'>{escape(retailer_name)} external signal readout</div>"
        f"<div class='result-command-copy'>Top signal: {escape(top_label)}. {escape(str(note))} Use this page from left to right: decision summary, category impact, level logic, then raw evidence.</div>"
        "</div>"
        f"<div class='result-command-meta'>{meta_html}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_decision_summary(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> None:
    cards = []
    for action in build_recommended_actions(feature_df, results):
        cards.append(
            "<div class='decision-card'>"
            f"<div class='decision-owner'>{escape(action['label'])}</div>"
            f"<div class='decision-title'>{escape(action['title'])}</div>"
            f"<div class='decision-body'>{escape(action['body'])}</div>"
            "</div>"
        )
    if not cards:
        cards.append(
            "<div class='decision-card'><div class='decision-owner'>Setup</div><div class='decision-title'>Run signal sources</div><div class='decision-body'>No decision cards are available until forecast-ready rows are generated.</div></div>"
        )
    st.markdown("<div class='decision-grid'>" + "".join(cards[:4]) + "</div>", unsafe_allow_html=True)


def render_explainability_ladder() -> None:
    steps = [
        ("01", "Collect", "Live public APIs return raw evidence; skipped sources are labeled."),
        ("02", "Normalize", "Records become forecast-ready rows with source, signal, region, and level fields."),
        ("03", "Map", "Rows are mapped to retail category, owner, KPI, and forecast feature."),
        ("04", "Classify", "Each source uses the existing rule to classify the signal as High, Medium, or Low."),
        ("05", "Brief", "NVIDIA or fallback summarizes only the shown rows and article context."),
    ]
    html = "".join(
        "<div class='explain-step'>"
        f"<div class='explain-step-num'>{escape(num)}</div>"
        f"<div class='explain-step-title'>{escape(title)}</div>"
        f"<div class='explain-step-copy'>{escape(copy)}</div>"
        "</div>"
        for num, title, copy in steps
    )
    st.markdown("<div class='explain-ladder'>" + html + "</div>", unsafe_allow_html=True)


def render_audit_command_header(
    run: Dict[str, Any],
    mock_used: bool,
    raw_records: int,
    pulled_sources: int,
    llm_audit: Dict[str, Any],
    feature_df: pd.DataFrame,
) -> None:
    brief_mode = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
    cells = [
        ("Mock Data", "No" if not mock_used else "Yes"),
        ("Raw Records", str(raw_records)),
        ("Live Sources", str(pulled_sources)),
        ("Brief Mode", brief_mode),
    ]
    cell_html = "".join(
        f"<div class='audit-status-cell'><div class='audit-status-label'>{escape(label)}</div><div class='audit-status-value'>{escape(value)}</div></div>"
        for label, value in cells
    )
    status_copy = (
        "All generated rows are traceable to collector outputs and the brief is tied to the shown payload."
        if not mock_used
        else "At least one collector or analysis step is marked as mock. Review provenance before using this run."
    )
    if llm_audit.get("fallback_used"):
        status_copy += f" Brief fallback reason: {display_fallback_reason(llm_audit.get('fallback_reason', 'NVIDIA unavailable'))}."
    st.markdown(
        "<div class='audit-command'>"
        "<div>"
        "<div class='config-eyebrow'>Evidence Audit</div>"
        "<div class='audit-command-title'>Run provenance and chain of custody</div>"
        f"<div class='audit-command-copy'>{escape(status_copy)} Feature rows available: {len(feature_df)}.</div>"
        "</div>"
        f"<div class='audit-status-grid'>{cell_html}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_audit_lineage() -> None:
    steps = [
        ("01", "Request", "Run config stores source toggles, query scope, limits, and guarded Apify mode."),
        ("02", "Collect", "Each collector records status, endpoint/actor, raw record count, and errors."),
        ("03", "Normalize", "Collector rows become forecast-ready signals with level reasoning and raw references."),
        ("04", "Analyze", "Brief payload is hashed and capped; NVIDIA/fallback status is recorded separately."),
        ("05", "Inspect", "Raw payloads, cleaned items, normalized rows, prompt, and output can be reviewed."),
    ]
    html = "".join(
        "<div class='audit-lineage-step'>"
        f"<div class='audit-lineage-num'>{escape(num)}</div>"
        f"<div class='audit-lineage-title'>{escape(title)}</div>"
        f"<div class='audit-lineage-copy'>{escape(copy)}</div>"
        "</div>"
        for num, title, copy in steps
    )
    st.markdown("<div class='audit-lineage'>" + html + "</div>", unsafe_allow_html=True)


def render_dollar_tree_impact_matrix(feature_df: pd.DataFrame) -> None:
    if feature_df.empty:
        st.info("No retail impact matrix is available until feature rows are generated.")
        return
    preferred_cols = [
        "dollar_tree_category",
        "enterprise_kpi",
        "risk_score",
        "action_priority",
        "planning_owner",
        "demand_direction",
        "forecast_feature",
        "impact_hypothesis",
        "internal_data_needed",
        "source",
    ]
    visible_cols = [col for col in preferred_cols if col in feature_df.columns]
    sort_cols = [col for col in ["action_priority", "risk_score"] if col in feature_df.columns]
    ascending = [True if col == "action_priority" else False for col in sort_cols]
    matrix_source = feature_df.sort_values(sort_cols, ascending=ascending) if sort_cols else feature_df
    matrix = matrix_source[visible_cols].copy()
    if "risk_score" in matrix.columns:
        matrix["risk_score"] = matrix["risk_score"].apply(lambda x: risk_band(float(x or 0)))
        matrix = matrix.rename(columns={"risk_score": "Risk Level"})
    st.dataframe(style_level_dataframe(matrix), width="stretch", hide_index=True)


def render_scenario_simulator(feature_df: pd.DataFrame) -> None:
    scenarios = {
        "Inflation rises again": {
            "category": "Total value basket, food, household essentials",
            "owner": "Merchandising Strategy + Demand Planning",
            "feature": "headline_cpi_value_pressure",
            "action": "Watch trade-down behavior, validate basket mix, and review value-sensitive replenishment.",
        },
        "FDA recall affects consumables": {
            "category": "Snacks, candy, beverages, consumables",
            "owner": "Compliance + Category Buyer",
            "feature": "recall_exposure_score",
            "action": "Match UPCs against SKU master, isolate affected inventory, and prepare substitute-item monitoring.",
        },
        "Severe weather hits selected state": {
            "category": "Emergency demand and replenishment-sensitive categories",
            "owner": "Supply Chain + Demand Planning",
            "feature": "state_weather_disruption_score",
            "action": "Check store/DC exposure, route risk, and short-horizon emergency-demand uplift.",
        },
        "Competitor promotion pressure rises": {
            "category": "Overlapping value categories and seasonal assortment",
            "owner": "Buyer + Category Manager",
            "feature": "competitor_promotion_pressure_score",
            "action": "Compare overlapping items, review promotional calendar, and watch category conversion.",
        },
    }
    selected = st.selectbox("Scenario", list(scenarios.keys()), label_visibility="collapsed")
    scenario = scenarios[selected]
    evidence_note = "No current run evidence matched this scenario directly."
    if not feature_df.empty and "forecast_feature" in feature_df.columns:
        matching = feature_df[feature_df["forecast_feature"].astype(str).str.contains(scenario["feature"].split("_")[0], case=False, na=False)]
        if not matching.empty:
            top = matching.sort_values("risk_score", ascending=False).iloc[0]
            evidence_note = f"Nearest current signal: {top.get('signal_area')} from {top.get('source')} is {risk_band(float(top.get('risk_score', 0) or 0))}."
    st.markdown(
        "<div class='enterprise-band'>"
        f"<div class='enterprise-band-title'>{escape(selected)}</div>"
        f"<div class='enterprise-band-copy'><strong>Likely retail category:</strong> {escape(scenario['category'])}<br>"
        f"<strong>Owner:</strong> {escape(scenario['owner'])}<br>"
        f"<strong>Forecast feature:</strong> {escape(scenario['feature'])}<br>"
        f"<strong>Action:</strong> {escape(scenario['action'])}<br>"
        f"<strong>Run evidence:</strong> {escape(evidence_note)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def build_recommended_actions(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    actions = []
    if feature_df.empty:
        return [{"label": "Setup", "title": "Run signal sources", "body": "Enable at least one source to generate forecast-ready rows."}]
    top_rows = feature_df.sort_values("risk_score", ascending=False).head(3)
    for _, row in top_rows.iterrows():
        owner = str(row.get("planning_owner") or "Planning Owner")
        category = str(row.get("dollar_tree_category") or row.get("signal_area") or "Category")
        hypothesis = str(row.get("impact_hypothesis") or row.get("recommended_action") or "Review this signal with the category owner.")
        actions.append(
            {
                "label": owner,
                "title": category,
                "body": f"{hypothesis} Next: {row.get('recommended_action', 'Review this signal with the category owner.')}",
            }
        )
    apify_result = results.get("apify")
    if apify_result and apify_result.get("status") in {"failed", "skipped"}:
        actions.append(
            {
                "label": "Apify",
                "title": "Search demand not collected",
                "body": apify_result.get("error") or "Apify did not return a usable trends signal. Keep MVP on public sources or run one guarded live query.",
            }
        )
    return actions[:4]


def render_action_card(label: str, title: str, body: str) -> None:
    html = (
        '<div class="action-card">'
        f'<div class="action-label">{escape(label)}</div>'
        f'<div class="action-title">{escape(title)}</div>'
        f'<div class="action-body">{escape(body)}</div>'
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_source_tile(name: str, status: str, detail: str, purpose: str = "") -> None:
    status_class = "pill-high" if status == "Active" else "pill-medium" if status == "Optional" else "pill-low"
    tile_state = "active" if status == "Active" else "off"
    purpose_html = f"<div class='source-purpose'>{escape(purpose)}</div>" if purpose else ""
    html = (
        f'<div class="source-tile {tile_state}">'
        f'<div class="source-name">{escape(name)} <span class="pill {status_class}" style="margin-left:6px;margin-top:0;">{escape(status)}</span></div>'
        f'<div class="source-meta">{escape(detail)}</div>'
        f"{purpose_html}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_glossary() -> None:
    terms = [
        ("Signal", "An external event or measurement that may explain demand, price pressure, or operational risk."),
        ("Risk Level", "A directional level. Higher means the signal deserves more attention, not that demand is guaranteed to move."),
        ("Forecast Feature", "A structured column that can later be joined to internal sales, store, category, promotion, and inventory data."),
        ("Region Scope", "Whether the signal is national, state-level, trend geography, or selected market context."),
        ("Fallback Brief", "A deterministic local summary generated when NVIDIA is unavailable or no API key is supplied."),
        ("Mock Data", "Synthetic or placeholder data. This app marks mock usage explicitly; live public collectors should show No in the audit table."),
    ]
    cards = []
    for term, definition in terms:
        cards.append(
            "<div class='glossary-card'>"
            f"<div class='glossary-term'>{escape(term)}</div>"
            f"<div class='glossary-def'>{escape(definition)}</div>"
            "</div>"
        )
    st.markdown("<div class='glossary-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_workflow_strip() -> None:
    steps = [
        ("01", "Collect APIs"),
        ("02", "Clean records"),
        ("03", "Classify signals"),
        ("04", "Generate brief"),
        ("05", "Plan demand"),
    ]
    html = "<div class='workflow'>" + "".join(
        f"<div class='workflow-step'><span>{num}</span>{escape(label)}</div>" for num, label in steps
    ) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def render_run_monitor(slot: Any, title: str, states: Dict[str, Dict[str, str]], progress_pct: int) -> None:
    cards = []
    for name, info in states.items():
        status = info.get("status", "queued")
        detail = info.get("detail", "")
        status_class = {
            "running": "status-running",
            "success": "status-success",
            "failed": "status-failed",
            "skipped": "status-skipped",
            "queued": "status-queued",
        }.get(status, "status-queued")
        cards.append(
            "<div class='run-status-card'>"
            f"<div class='status-badge {status_class}'>{escape(status)}</div>"
            f"<div class='run-status-name'>{escape(name)}</div>"
            f"<div class='run-status-detail'>{escape(detail)}</div>"
            "</div>"
        )
    html = (
        "<div class='run-monitor'>"
        "<div class='run-monitor-head'>"
        f"<div><div class='run-monitor-sub'>Pipeline Status</div><div class='run-monitor-title'>{escape(title)}</div></div>"
        f"<div class='tbadge'>{int(progress_pct)}%</div>"
        "</div>"
        "<div class='run-progress-track'>"
        f"<div class='run-progress-fill' style='width:{max(0, min(100, int(progress_pct)))}%;'></div>"
        "</div>"
        "<div class='run-status-grid'>"
        + "".join(cards)
        + "</div></div>"
    )
    slot.markdown(html, unsafe_allow_html=True)


def render_sidebar_status(slot: Any, message: str, state: str = "info") -> None:
    if state == "success":
        slot.success(message)
    elif state == "warning":
        slot.warning(message)
    elif state == "error":
        slot.error(message)
    else:
        slot.info(message)


def render_score_chart(feature_df: pd.DataFrame) -> None:
    if feature_df.empty:
        st.info("No feature rows yet.")
        return
    display_levels = feature_df["risk_score"].apply(lambda x: risk_band(float(x or 0)))
    level_values = display_levels.map({"Low": 1, "Medium": 2, "High": 3})
    fig = go.Figure(
        go.Bar(
            x=level_values,
            y=feature_df["signal_area"],
            orientation="h",
            marker_color=["#22C55E" if x == "Low" else "#F59E0B" if x == "Medium" else "#EF4444" for x in display_levels],
            text=display_levels,
            textposition="auto",
        )
    )
    fig.update_layout(
        height=280,
        margin={"l": 10, "r": 20, "t": 10, "b": 10},
        xaxis={"range": [0, 3.25], "title": "Risk Level", "tickmode": "array", "tickvals": [1, 2, 3], "ticktext": ["Low", "Medium", "High"]},
        yaxis={"title": ""},
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    st.plotly_chart(fig, width="stretch")


def scoring_formula_for_row(row: pd.Series) -> str:
    source = str(row.get("source", "")).lower()
    area = str(row.get("signal_area", "")).lower()
    if "bls" in source or "cpi" in area:
        return "CPI scoring starts from a neutral 4.0, adjusts upward or downward using monthly CPI change, adds pressure when YoY inflation is elevated, then clips to a 1-10 range."
    if "fda" in source or "recall" in area:
        return "Recall scoring starts from reason severity, then adjusts for FDA classification and recall status. Class I and ongoing recalls increase the score; terminated recalls reduce it."
    if "noaa" in source or "weather" in area:
        return "Weather scoring sums active NOAA alert severity weights for the selected state, adds urgency/certainty pressure, then caps the supply-chain risk score at 10."
    if "gnews" in source or "news" in area:
        return "News scoring classifies each article into event type and sentiment, adjusts for recency/source quality, then averages deduplicated article risk."
    if "apify" in source or "search" in area:
        return f"Search scoring uses top regional Google Trends interest divided by 10, with backend limits of {APIFY_SAFE_TIME_RANGE} and {APIFY_HARD_KEYWORD_LIMIT} keyword(s)."
    return "Score is normalized to a 1-10 signal intensity scale using the collector-specific scoring rule."


def render_score_explainability(feature_df: pd.DataFrame) -> None:
    if feature_df.empty:
        st.info("No signal explanations available.")
        return
    explanation_rows = feature_df.sort_values("risk_score", ascending=False).reset_index(drop=True)
    for _, row in explanation_rows.iterrows():
        score = float(row.get("risk_score", 0) or 0)
        band = risk_band(score)
        title = f"{row.get('signal_area', 'Signal')} · {str(row.get('signal_name', '')).replace('_', ' ').title()}"
        meta = f"{row.get('source', 'Unknown source')} / {row.get('region_scope', row.get('region', ''))}"
        reason = str(row.get("score_reason") or "No score reason was returned by this collector.")
        evidence = str(row.get("raw_reference") or row.get("signal_value") or "No raw reference available.")
        action = str(row.get("recommended_action") or "Review this signal before using it in planning.")
        formula = scoring_formula_for_row(row)
        html = (
            "<div class='score-explain-card'>"
            "<div class='score-explain-head'>"
            f"<div><div class='score-explain-title'>{escape(title)}</div><div class='score-explain-meta'>{escape(meta)}</div></div>"
            f"<div class='score-number level-{escape(band.lower())}'>{escape(band)}</div>"
            "</div>"
            "<div class='score-explain-label'>Classification rule</div>"
            f"<div class='score-explain-text'>{escape(formula)}</div>"
            "<div class='score-explain-label'>Why this level</div>"
            f"<div class='score-explain-text'>{escape(reason)}</div>"
            "<div class='score-explain-label'>Evidence used</div>"
            f"<div class='score-explain-text'>{escape(evidence)}</div>"
            "<div class='score-explain-label'>Planning action</div>"
            f"<div class='score-explain-text'>{escape(action)}</div>"
            "</div>"
        )
        st.markdown(html, unsafe_allow_html=True)


BRIEF_SECTION_TITLES = {
    "executive summary",
    "top 3 insights",
    "top three insights",
    "top signal evidence",
    "forecasting relevance",
    "recommended actions",
    "confidence and limitations",
    "confidence limitations",
}


def normalize_brief_line(line: str) -> str:
    normalized = str(line or "").strip()
    normalized = re.sub(r"^\s*#{1,6}\s*", "", normalized)
    normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
    normalized = normalized.replace("**", "")
    return normalized.strip()


def clean_brief_heading(line: str) -> str:
    heading = normalize_brief_line(line).rstrip(":").strip()
    heading = re.sub(r"^\d+[\.)]\s*", "", heading).strip()
    return heading


def brief_section_heading(raw_line: str) -> str:
    stripped = str(raw_line or "").strip()
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return ""
    normalized = normalize_brief_line(stripped)
    title = clean_brief_heading(normalized)
    simplified = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
    if stripped.startswith("#") or (normalized.endswith(":") and len(normalized) <= 90):
        return title
    if simplified in BRIEF_SECTION_TITLES:
        return title
    return ""


def brief_to_html(brief: str) -> str:
    parts: List[str] = []
    in_list = False
    for raw_line in str(brief or "").splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        normalized = normalize_brief_line(line)
        heading = brief_section_heading(line)
        bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
        if heading:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<div class='brief-section-title'>{escape(heading)}</div>")
        elif bullet_match:
            if not in_list:
                parts.append("<ul class='brief-list'>")
                in_list = True
            parts.append(f"<li>{escape(bullet_match.group(1))}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{escape(normalized)}</p>")
    if in_list:
        parts.append("</ul>")
    return "".join(parts)


def parse_brief_sections(brief: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current = {"title": "Executive Summary", "items": []}
    for raw_line in str(brief or "").splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            continue
        normalized = normalize_brief_line(line)
        heading = brief_section_heading(line)
        bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
        if heading:
            if current["items"]:
                sections.append(current)
            current = {"title": heading, "items": []}
        elif bullet_match:
            current["items"].append({"kind": "bullet", "text": bullet_match.group(1)})
        else:
            current["items"].append({"kind": "text", "text": normalized})
    if current["items"]:
        sections.append(current)
    return sections


def brief_sections_to_html(brief: str) -> str:
    sections = parse_brief_sections(brief)
    if not sections:
        return "<div class='brief-section-grid'><div class='brief-section-card primary'><div class='brief-section-title'>Executive Summary</div><p>No brief content was generated.</p></div></div>"
    cards = []
    for idx, section in enumerate(sections):
        paragraphs = []
        bullets = []
        for item in section["items"]:
            if item["kind"] == "bullet":
                bullets.append(f"<li>{escape(str(item['text']))}</li>")
            else:
                paragraphs.append(f"<p>{escape(str(item['text']))}</p>")
        body = "".join(paragraphs)
        if bullets:
            body += "<ul class='brief-list'>" + "".join(bullets) + "</ul>"
        primary = " primary" if idx == 0 else ""
        cards.append(
            f"<div class='brief-section-card{primary}'>"
            f"<div class='brief-section-title'>{escape(str(section['title']))}</div>"
            f"{body}"
            "</div>"
        )
    return "<div class='brief-section-grid'>" + "".join(cards) + "</div>"


def render_executive_brief(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
    brief_source = str(run.get("brief_source", "unknown"))
    articles = run.get("articles", [])
    llm_audit = run.get("llm_audit", {})
    source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
    top_label = "No signal"
    avg_score_label = "Low"
    if not feature_df.empty:
        top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
        top_label = f"{top.get('signal_area', 'Signal')} - {risk_band(float(top.get('risk_score', 0) or 0))}"
        avg_score_label = risk_band(float(feature_df['risk_score'].mean()))
    articles_sent = llm_audit.get("articles_sent", min(len(articles), 8))
    attempts = llm_audit.get("nvidia_attempts", [])
    response_label = "not called"
    if attempts:
        response_label = f"{len(attempts)} attempt(s), {attempts[-1].get('elapsed_ms', 0)} ms last"
    header_note = (
        "NVIDIA grounded response" if llm_audit.get("sent_to_llm") else "Local deterministic summary using collected feature rows"
    )
    brief_source_label = "NVIDIA" if brief_source == "nvidia" else "Local fallback"
    brief_status_note = display_fallback_reason(llm_audit.get("fallback_reason") or "NVIDIA returned a grounded response.")
    html = (
        "<div class='brief-shell'>"
        "<div class='brief-header'>"
        "<div class='brief-kicker'>Executive Brief</div>"
        "<div class='brief-title'>External Signal Readout</div>"
        f"<div class='brief-summary-text'>{escape(header_note)}. {escape(str(brief_status_note))}</div>"
        "<div class='brief-meta-strip'>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Brief Source</div><div class='brief-meta-value'>{escape(brief_source_label)}</div></div>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Rows Grounded</div><div class='brief-meta-value'>{len(feature_df)} rows / {source_count} source(s)</div></div>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>Overall Signal Level</div><div class='brief-meta-value'>{escape(avg_score_label)}</div></div>"
        f"<div class='brief-meta-chip'><div class='brief-meta-label'>AI Timing</div><div class='brief-meta-value'>{escape(response_label)}</div></div>"
        "</div>"
        f"<div class='brief-summary-text' style='margin-top:10px;'>Top evidence: {escape(top_label)}. Articles in context: {len(articles)} available, {articles_sent} sent.</div>"
        "</div>"
        f"{brief_sections_to_html(str(run.get('brief', '')))}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def parse_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]



# -----------------------------------------------------------------------------
# Demand Planning Agent
# -----------------------------------------------------------------------------

def build_demand_signal_log(feature_df: pd.DataFrame) -> pd.DataFrame:
    """Convert Market Intelligence rows into a compact Demand Signal Log."""
    if feature_df is None or feature_df.empty:
        return pd.DataFrame()
    rows = []
    ordered = feature_df.copy()
    if "risk_score" in ordered.columns:
        ordered = ordered.sort_values("risk_score", ascending=False)
    for i, (_, row) in enumerate(ordered.iterrows(), start=1):
        rows.append(
            {
                "Signal ID": f"SIG-{i:03d}",
                "Signal Area": row.get("signal_area", "External Signal"),
                "Level": risk_band(float(row.get("risk_score", 0) or 0)),
                "Source": row.get("source", ""),
                "Demand Direction": row.get("demand_direction", "Validate against internal demand data"),
                "Forecast Feature": row.get("forecast_feature", row.get("signal_name", "external_signal")),
                "Business Impact": row.get("business_impact", ""),
                "Recommended Action": row.get("recommended_action", ""),
            }
        )
    return pd.DataFrame(rows)


def demo_market_signals() -> pd.DataFrame:
    """Reference signals used when a Market Intelligence run has not been executed yet."""
    return pd.DataFrame(
        [
            {
                "Signal ID": "SIG-001",
                "Signal Area": "Weather Risk",
                "Level": "High",
                "Source": "NOAA Weather Alerts",
                "Demand Direction": "Short-term surge for emergency essentials; replenishment disruption risk",
                "Forecast Feature": "state_weather_disruption_score",
                "Business Impact": "Severe weather may lift emergency-category demand and disrupt replenishment.",
                "Recommended Action": "Validate affected stores, DC routes, inventory, and short-horizon replenishment.",
            },
            {
                "Signal ID": "SIG-002",
                "Signal Area": "Product Recalls",
                "Level": "High",
                "Source": "openFDA",
                "Demand Direction": "Slump for affected SKU; substitution demand for alternatives",
                "Forecast Feature": "recall_exposure_score",
                "Business Impact": "Recall exposure can reduce affected-item demand and shift demand to substitutes.",
                "Recommended Action": "Match UPCs to the SKU master and review exposed inventory and substitutes.",
            },
            {
                "Signal ID": "SIG-003",
                "Signal Area": "Category CPI",
                "Level": "Medium",
                "Source": "BLS CPI",
                "Demand Direction": "Potential demand shift toward value alternatives",
                "Forecast Feature": "category_cpi_pressure",
                "Business Impact": "Inflation pressure may change category mix and price sensitivity.",
                "Recommended Action": "Validate the signal against category sales, basket mix, pricing, and promotions.",
            },
        ]
    )


def default_planning_dataset() -> pd.DataFrame:
    """Reference forecast and inventory dataset used until internal planning data is connected."""
    return pd.DataFrame(
        [
            {
                "Store ID": "101",
                "Product": "Bottled Water",
                "Category": "Emergency Essentials",
                "Baseline Forecast": 500,
                "Historical Average": 480,
                "On Hand": 420,
                "Inbound": 100,
            },
            {
                "Store ID": "102",
                "Product": "Batteries",
                "Category": "Emergency Essentials",
                "Baseline Forecast": 300,
                "Historical Average": 285,
                "On Hand": 210,
                "Inbound": 50,
            },
            {
                "Store ID": "103",
                "Product": "Peanut Butter",
                "Category": "Food / Consumables",
                "Baseline Forecast": 220,
                "Historical Average": 215,
                "On Hand": 180,
                "Inbound": 40,
            },
            {
                "Store ID": "104",
                "Product": "Value Snacks",
                "Category": "Food / Consumables",
                "Baseline Forecast": 420,
                "Historical Average": 400,
                "On Hand": 350,
                "Inbound": 90,
            },
        ]
    )


def planning_products_for_signal(signal_area: str, planning_df: pd.DataFrame) -> List[str]:
    area = str(signal_area or "").lower()
    if "weather" in area:
        preferred = ["Bottled Water", "Batteries"]
    elif "recall" in area:
        preferred = ["Peanut Butter"]
    elif "cpi" in area or "inflation" in area:
        preferred = ["Value Snacks"]
    else:
        preferred = planning_df["Product"].astype(str).tolist()
    available = planning_df["Product"].astype(str).tolist()
    matched = [p for p in preferred if p in available]
    return matched or available


def classify_planning_direction(signal_row: Dict[str, Any]) -> str:
    """Translate the Market Intelligence signal into a simple planning direction.

    No user-entered uplift/decline percentage is used. The direction is based on
    the signal type and is intended only to guide the demo planning analysis.
    """
    area = str(signal_row.get("Signal Area", "") or "").lower()
    if "weather" in area:
        return "SURGE"
    if "recall" in area:
        return "SLUMP / SUBSTITUTION"
    if "cpi" in area or "inflation" in area:
        return "MIX SHIFT / REVIEW"
    return "REVIEW"


def calculate_demand_plan(
    signal_row: Dict[str, Any],
    planning_df: pd.DataFrame,
) -> Dict[str, Any]:
    """Evaluate the selected Market Intelligence signal against the full reference planning dataset.

    No product is assumed to be affected by the selected external signal. Until internal
    product/SKU/geography mapping is connected, the reference planning table is treated as
    a portfolio-level planning context only. A production analysis should first identify the
    actually affected product/category/store/DC and then evaluate its forecast and inventory.
    """
    if planning_df is None or planning_df.empty:
        raise ValueError("Reference planning data is required for demand impact analysis.")

    numeric_cols = ["Baseline Forecast", "Historical Average", "On Hand", "Inbound"]
    working = planning_df.copy()
    for col in numeric_cols:
        if col not in working.columns:
            working[col] = 0
        working[col] = pd.to_numeric(working[col], errors="coerce").fillna(0)

    baseline = float(working["Baseline Forecast"].sum())
    historical = float(working["Historical Average"].sum())
    on_hand = float(working["On Hand"].sum())
    inbound = float(working["Inbound"].sum())
    available_supply = on_hand + inbound
    gap = max(0.0, baseline - available_supply)
    surplus = max(0.0, available_supply - baseline)
    direction = classify_planning_direction(signal_row)

    stores = sorted({str(v) for v in working.get("Store ID", pd.Series(dtype=str)).dropna().astype(str).tolist() if str(v).strip()})
    categories = sorted({str(v) for v in working.get("Category", pd.Series(dtype=str)).dropna().astype(str).tolist() if str(v).strip()})

    return {
        "Signal ID": signal_row.get("Signal ID", ""),
        "Signal Area": signal_row.get("Signal Area", ""),
        "Signal Level": signal_row.get("Level", ""),
        "Signal Source": signal_row.get("Source", ""),
        "Demand Direction": direction,
        "Forecast Feature": signal_row.get("Forecast Feature", ""),
        "Planning Scope": "Reference planning portfolio",
        "Store Scope": ", ".join(stores) if stores else "Reference stores",
        "Category Scope": ", ".join(categories) if categories else "Reference categories",
        "Reference Products": int(len(working)),
        "Baseline Forecast": int(round(baseline)),
        "Historical Average": int(round(historical)),
        "On Hand": int(round(on_hand)),
        "Inbound": int(round(inbound)),
        "Available Supply": int(round(available_supply)),
        "Baseline Gap": int(round(gap)),
        "Baseline Surplus": int(round(surplus)),
    }

def local_demand_planner_report(plan: Dict[str, Any]) -> str:
    signal = plan.get("Signal Area", "External signal")
    level = plan.get("Signal Level", "")
    direction = plan.get("Demand Direction", "REVIEW")
    baseline = plan.get("Baseline Forecast", 0)
    historical = plan.get("Historical Average", 0)
    supply = plan.get("Available Supply", 0)
    gap = plan.get("Baseline Gap", 0)
    surplus = plan.get("Baseline Surplus", 0)
    product_count = plan.get("Reference Products", 0)

    if gap > 0:
        inventory_text = f"Across the reference planning dataset, available supply is {supply} units, which is {gap} units below the current baseline forecast."
    else:
        inventory_text = f"Across the reference planning dataset, available supply is {supply} units, which is {surplus} units above the current baseline forecast."

    area = str(signal or "").upper()
    if "WEATHER" in area:
        action = (
            "The signal indicates a potential demand surge or replenishment disruption. Before changing any forecast, first map the weather event to the affected geography, stores/DCs, and relevant categories, then validate historical demand and current inventory."
        )
    elif "RECALL" in area:
        action = (
            "The signal indicates potential demand decline and substitution risk. Before changing any forecast, first match the recall to internal UPC/SKU data and identify the actually affected product, stores/DCs, inventory exposure, and substitutes."
        )
    elif "CPI" in area or "INFLATION" in area:
        action = (
            "The signal indicates a possible category mix or price-sensitivity shift. Validate it against internal category sales, basket mix, pricing, promotions, and inventory before changing any forecast."
        )
    elif "NEWS" in area:
        action = (
            "The news signal provides market context only. First determine whether the event is relevant to a specific category, geography, competitor set, or product before using it in a planning decision."
        )
    else:
        action = "Review the external signal with relevant internal demand, product, geography, and inventory evidence before making a planning change."

    return (
        "PLANNING SUMMARY\n"
        f"{signal} is a {level} external signal. The planning direction is {direction}. "
        f"The signal is being evaluated against a reference planning portfolio containing {product_count} product rows; no specific product is assumed to be affected.\n\n"
        "FORECAST CONTEXT\n"
        f"The aggregated reference baseline forecast is {baseline} units and the aggregated historical average is {historical} units. "
        "No manual uplift or decline percentage is applied. The external signal is used as planning context, not as an automatic forecast override.\n\n"
        "INVENTORY IMPACT\n"
        f"{inventory_text}\n\n"
        "PLANNING ACTION\n"
        f"{action}\n\n"
        "CONFIDENCE AND LIMITATIONS\n"
        "The forecast and inventory values are reference data used to demonstrate the Demand Planner handoff. Actual signal-to-product or signal-to-location mapping, historical sales, product hierarchy, store/DC data, promotions, inventory, and forecast accuracy must be connected and validated before any production planning decision."
    )

def generate_demand_planner_report(
    api_key: str,
    model: str,
    signal_row: Dict[str, Any],
    plan: Dict[str, Any],
) -> Tuple[str, str, Dict[str, Any]]:
    """Use NVIDIA only to explain the supplied signal and already-calculated planning facts."""
    fallback = local_demand_planner_report(plan)
    if not api_key:
        return fallback, "Local fallback", {"fallback_reason": "No NVIDIA API key provided."}

    payload = {
        "external_signal": signal_row,
        "calculated_planning_result": plan,
        "calculation_contract": (
            "Forecast and inventory values were supplied or calculated before the LLM call. "
            "No manual demand uplift/decline factor is used. The LLM must explain the planning context and must not create new numeric values."
        ),
    }
    system_prompt = (
        "You are a retail demand planning analyst. Explain only the supplied external signal and deterministic planning facts. "
        "Do not invent new forecast, sales, inventory, uplift, substitution, or performance values."
    )
    user_prompt = f"""
Demand Planning Agent - Market Signal Integration

Grounded payload:
{json.dumps(payload, indent=2, default=str)}

Return exactly:
PLANNING SUMMARY
<2 concise sentences>

FORECAST CONTEXT
<explain baseline forecast, historical average, and the supplied demand direction; state that no automatic forecast override is being applied>

INVENTORY IMPACT
<explain available supply and the calculated baseline gap or surplus>

PLANNING ACTION
<one concise business action based on the supplied signal and planning facts>

CONFIDENCE AND LIMITATIONS
<state that the signal is external context, the planning data is reference data, and internal validation is required>

Rules:
- Use only the supplied payload.
- Do not invent or change any number.
- Do not expose Market Intelligence numeric risk scores.
- Use the High/Medium/Low signal level already supplied.
- Do not create a new uplift, decline, or forecast override percentage.
- No markdown tables.
"""
    ok, content, error, elapsed_ms = _nvidia_chat_request(
        api_key=api_key,
        model=model or DEFAULT_NVIDIA_MODEL,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=380,
        timeout_seconds=40,
    )
    audit = {"elapsed_ms": elapsed_ms, "success": ok, "technical_error": error, "payload": payload}
    if not ok:
        audit["fallback_reason"] = friendly_nvidia_failure(error)[1]
        return fallback, "Local fallback", audit
    return _normalize_executive_brief_format(content), "NVIDIA", audit


def render_demand_planner_view(nvidia_key: str, nvidia_model: str) -> None:
    st.markdown('<div class="small-header">Demand Planning Agent · Market Signal Integration</div>', unsafe_allow_html=True)
    st.markdown(
        "<div class='config-tab-note'>"
        "This section follows the Market Intelligence → Demand Signal Log → Demand Planning flow. "
        "The external signal comes from the current Market Intelligence run when available and is evaluated against reference forecast and inventory data. "
        "No manual scenario-adjustment percentage is applied. NVIDIA is used only to explain the planning result."
        "</div>",
        unsafe_allow_html=True,
    )

    run = st.session_state.get("run")
    run_feature_df = run.get("feature_df", pd.DataFrame()) if run else pd.DataFrame()
    signal_log = build_demand_signal_log(run_feature_df)
    using_demo_signals = signal_log.empty
    if using_demo_signals:
        signal_log = demo_market_signals()
        st.info("No completed Market Intelligence run is available in this session, so reference signals are being shown. Run Market Intelligence first to use the collected signals from the current run.")
    else:
        st.success("Using the Demand Signal Log generated from the current Market Intelligence run.")

    st.markdown('<div class="small-header">1. Demand Signal Log</div>', unsafe_allow_html=True)
    st.dataframe(style_level_dataframe(signal_log), width="stretch", hide_index=True)

    signal_options = [f"{row['Signal ID']} · {row['Signal Area']} · {row['Level']}" for _, row in signal_log.iterrows()]
    selected_signal_label = st.selectbox("Select market signal", signal_options)
    selected_signal_index = signal_options.index(selected_signal_label)
    signal_row = signal_log.iloc[selected_signal_index].to_dict()

    st.markdown('<div class="small-header">2. Forecast And Inventory Data</div>', unsafe_allow_html=True)
    if "demand_planning_data" not in st.session_state:
        st.session_state["demand_planning_data"] = default_planning_dataset()
    else:
        # Remove legacy columns from an existing Streamlit session after upgrading this version.
        legacy_df = st.session_state["demand_planning_data"].copy()
        legacy_df = legacy_df.drop(columns=["Scenario Adjustment %", "Substitute Product"], errors="ignore")
        st.session_state["demand_planning_data"] = legacy_df

    edited_df = st.data_editor(
        st.session_state["demand_planning_data"],
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        key="demand_planning_editor",
    )
    st.session_state["demand_planning_data"] = edited_df
    st.caption(
        "Reference forecast and inventory data are used because internal planning datasets are not connected yet. "
        "The Market Intelligence signal determines the planning direction; it does not automatically override the forecast."
    )

    

    if st.button("Analyze Demand Impact", type="primary"):
        plan = calculate_demand_plan(signal_row, edited_df)
        report, report_source, report_audit = generate_demand_planner_report(
            nvidia_key.strip(), nvidia_model.strip(), signal_row, plan
        )
        st.session_state["demand_plan_result"] = {
            "plan": plan,
            "report": report,
            "report_source": report_source,
            "report_audit": report_audit,
            "signal": signal_row,
            "using_demo_signals": using_demo_signals,
        }

    result = st.session_state.get("demand_plan_result")
    if result:
        plan = result["plan"]
        st.markdown('<div class="small-header">3. Forecast And Inventory Impact</div>', unsafe_allow_html=True)
        cols = st.columns(4)
        with cols[0]:
            render_metric_card("Direction", str(plan["Demand Direction"]), "Planning interpretation of the external signal.", value_class="direction-value")
        with cols[1]:
            render_metric_card("Baseline Forecast", str(plan["Baseline Forecast"]), f"Historical average: {plan['Historical Average']} units")
        with cols[2]:
            render_metric_card("Available Supply", str(plan["Available Supply"]), f"On hand {plan['On Hand']} + inbound {plan['Inbound']}")
        with cols[3]:
            gap_value = plan["Baseline Gap"]
            note = "Shortage against current baseline forecast." if gap_value > 0 else f"Baseline surplus: {plan['Baseline Surplus']} units"
            render_metric_card("Baseline Gap", str(gap_value), note)

        plan_df = pd.DataFrame([plan])
        st.dataframe(plan_df, width="stretch", hide_index=True)

        st.markdown('<div class="small-header">4. Planning Report</div>', unsafe_allow_html=True)
        st.caption(f"Report source: {result['report_source']}")
        st.markdown(f"<div class='brief-box'>{brief_to_html(result['report'])}</div>", unsafe_allow_html=True)

        export_plan = plan_df.to_csv(index=False).encode("utf-8")
        export_report = str(result["report"]).encode("utf-8")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("Download demand_plan.csv", export_plan, "demand_plan.csv", "text/csv", width="stretch")
        with d2:
            st.download_button("Download planning_report.txt", export_report, "planning_report.txt", "text/plain", width="stretch")

        with st.expander("Planning calculation audit", expanded=False):
            st.json(
                {
                    "Signal source": result["signal"],
                    "Calculated result": plan,
                    "Report source": result["report_source"],
                    "NVIDIA audit": result["report_audit"],
                    "Important": (
                        "No manual scenario-adjustment percentage is applied. The external signal supplies planning context and direction; "
                        "a production forecast override requires validated internal historical demand data."
                    ),
                }
            )


def init_run_history_db() -> None:
    """Create the lightweight local version-history table if it does not exist."""
    with sqlite3.connect(RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_intelligence_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_type TEXT NOT NULL,
                retailer TEXT,
                source TEXT,
                keywords TEXT,
                time_window TEXT,
                geography TEXT,
                top_signal TEXT,
                top_level TEXT,
                feature_rows INTEGER DEFAULT 0,
                feature_json TEXT,
                brief TEXT
            )
            """
        )
        conn.commit()


def save_run_history(
    run: Dict[str, Any],
    *,
    run_type: str,
    source: str,
    keywords: str = "",
    time_window: str = "",
    geography: str = "",
) -> None:
    """Persist one completed run for local versioning without changing the active run logic."""
    feature_df = run.get("feature_df", pd.DataFrame())
    if feature_df is None:
        feature_df = pd.DataFrame()
    top_signal = "No signal"
    top_level = "Low"
    if not feature_df.empty and "risk_score" in feature_df.columns:
        top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
        top_signal = str(top.get("signal_area", "Signal"))
        top_level = risk_band(float(top.get("risk_score", 0) or 0))
    config = run.get("run_config", {})
    with sqlite3.connect(RUN_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            """
            INSERT INTO market_intelligence_runs (
                created_at, run_type, retailer, source, keywords, time_window,
                geography, top_signal, top_level, feature_rows, feature_json, brief
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.get("timestamp", utc_now())),
                run_type,
                str(config.get("retailer", "")),
                source,
                keywords,
                time_window,
                geography,
                top_signal,
                top_level,
                int(len(feature_df)),
                json.dumps(feature_df.to_dict(orient="records"), default=str),
                str(run.get("brief", "")),
            ),
        )
        conn.commit()


def load_run_history(limit: int = 10) -> pd.DataFrame:
    """Return recent local versions for display in the Signal Search page."""
    with sqlite3.connect(RUN_HISTORY_DB, timeout=10) as conn:
        return pd.read_sql_query(
            """
            SELECT
                run_id AS "Run ID",
                created_at AS "Run Time",
                run_type AS "Run Type",
                source AS "Source",
                keywords AS "Keywords",
                time_window AS "Time Window",
                geography AS "Geography",
                top_signal AS "Top Signal",
                top_level AS "Level",
                feature_rows AS "Feature Rows"
            FROM market_intelligence_runs
            ORDER BY run_id DESC
            LIMIT ?
            """,
            conn,
            params=(int(limit),),
        )


def parse_signal_search_keywords(value: str) -> List[str]:
    return [part.strip() for part in re.split(r"[,\\n]+", value or "") if part.strip()]


def build_default_news_keywords(retailer_name: str) -> str:
    name = retailer_name.strip() or "Retailer"
    return "\n".join(
        [
            f"{name} inflation",
            f"{name} prices",
            f"{name} store closures",
            f"{name} recall",
            "discount retail tariffs",
            "Dollar General promotion",
        ]
    )


def build_default_trends_keywords(retailer_name: str) -> str:
    name = retailer_name.strip() or "Retailer"
    return "\n".join(
        [
            f"{name} sales",
            f"{name} coupons",
            f"{name} near me",
            f"{name} groceries",
            "cheap groceries",
        ]
    )


def retailer_initials(retailer_name: str) -> str:
    words = [word for word in re.split(r"\s+", retailer_name.strip()) if word]
    if not words:
        return "AI"
    return "".join(word[0].upper() for word in words[:2])


init_run_history_db()

st.session_state.setdefault("signal_search_keywords", "")
st.session_state.setdefault("signal_search_time_window", "Last 1 day")
st.session_state.setdefault("signal_search_geo", "United States - National")
st.session_state.setdefault("gnews_period", "7d")
st.session_state.setdefault("max_news", 24)
st.session_state.setdefault("fda_query", "product_description:(snacks OR candy OR beverages)")
st.session_state.setdefault("fda_limit", 8)
st.session_state.setdefault("weather_area", "TX")
st.session_state.setdefault("weather_limit", 5)
st.session_state.setdefault("apify_geo", "US")
st.session_state.setdefault("apify_time_range", APIFY_SAFE_TIME_RANGE)
st.session_state.setdefault("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)
st.session_state.setdefault("apify_run_mode", "Skip Apify")
st.session_state.setdefault("apify_live_confirm", False)
st.session_state.setdefault("workbench_view", "Configure")
if st.session_state.get("apify_time_range") not in APIFY_ALLOWED_TIME_RANGES:
    st.session_state["apify_time_range"] = APIFY_SAFE_TIME_RANGE
if st.session_state.pop("force_results_view", False):
    st.session_state["workbench_view"] = "Results"
if st.session_state.pop("reset_apify_live_confirm", False):
    st.session_state["apify_run_mode"] = "Skip Apify"
    st.session_state["apify_live_confirm"] = False


with st.sidebar:
    st.markdown(
        "<div class='sidebar-brand'>"
        "<div class='sidebar-brand-title'>Run Control</div>"
        "<div class='sidebar-brand-copy'>Configure context, source coverage, and guarded API spend for the next intelligence run.</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.expander("Credentials", expanded=False):
        st.caption("Keys stay in this Streamlit session and are never written to evidence payloads.")
        nvidia_key = st.text_input("NVIDIA API key", value=os.getenv("NVIDIA_API_KEY", ""), type="password")
        nvidia_model = st.text_input("NVIDIA model", value=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL))
        apify_token = st.text_input("Apify token", value=os.getenv("APIFY_API_TOKEN", ""), type="password")
        bls_key = st.text_input("BLS API key optional", value=os.getenv("BLS_API_KEY", ""), type="password")
        validate_button = st.button("Validate credentials", width="stretch")
        test_nvidia_button = st.button("Test NVIDIA", width="stretch")

    with st.expander("Retail context", expanded=True):
        retailer = st.text_input("Company / Retailer", value="", placeholder="Optional: enter a company or retailer")
        region = st.text_input("Region", value="US")
        country = st.selectbox("News country", ["US", "CA", "GB", "AE", "IN"], index=0)
        language = st.selectbox("News language", ["en", "es", "fr", "ar", "hi"], index=0)

    with st.expander("Signal sources", expanded=True):
        use_gnews = st.checkbox("Retail news", value=True)
        use_bls = st.checkbox("Inflation CPI", value=True)
        use_fda = st.checkbox("Product recalls", value=True)
        use_weather = st.checkbox("Weather risk", value=True)
        use_apify = st.checkbox("Search demand", value=False)

    sidebar_source_count = sum([use_gnews, use_bls, use_fda, use_weather, bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))])
    st.markdown(
        "<div class='sidebar-summary'>"
        f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Sources</div><div class='sidebar-summary-value'>{sidebar_source_count} enabled</div></div>"
        f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Brief</div><div class='sidebar-summary-value'>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</div></div>"
        f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Market</div><div class='sidebar-summary-value'>{escape(region)}</div></div>"
        f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Apify</div><div class='sidebar-summary-value'>{escape(st.session_state.get('apify_run_mode', 'Skip Apify'))}</div></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if use_apify or apify_token.strip():
        with st.expander("Apify spend guardrail", expanded=bool(use_apify)):
            if apify_token.strip():
                if st.session_state.get("apify_live_confirm", False) and st.session_state.get("apify_run_mode") == "Skip Apify":
                    st.session_state["apify_run_mode"] = "Run one live Apify call"
                st.radio(
                    "Live mode",
                    ["Skip Apify", "Run one live Apify call"],
                    key="apify_run_mode",
                    horizontal=False,
                )
                st.session_state["apify_live_confirm"] = st.session_state.get("apify_run_mode") == "Run one live Apify call"
                st.caption(f"{APIFY_SAFE_TIME_RANGE}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s). Resets after run.")
                if st.session_state.get("apify_run_mode") == "Run one live Apify call":
                    st.success("Next run will call Apify once.")
                else:
                    st.info("No Apify credits will be used.")
            else:
                st.session_state["apify_run_mode"] = "Skip Apify"
                st.session_state["apify_live_confirm"] = False
                st.warning("Add an Apify token before allowing a live trends run.")

    st.markdown("<div class='sidebar-divider'></div>", unsafe_allow_html=True)
    run_button = st.button("Run intelligence", type="primary", width="stretch")
    sidebar_status_slot = st.empty()


retailer_label = retailer.strip() or "General Retail Market"
previous_keyword_retailer = st.session_state.get("keyword_template_retailer")
previous_news_template = build_default_news_keywords(previous_keyword_retailer or retailer_label)
previous_trends_template = build_default_trends_keywords(previous_keyword_retailer or retailer_label)
next_news_template = build_default_news_keywords(retailer_label)
next_trends_template = build_default_trends_keywords(retailer_label)
if "news_keywords_text" not in st.session_state or st.session_state.get("news_keywords_text") == previous_news_template:
    st.session_state["news_keywords_text"] = next_news_template
if "trends_keywords_text" not in st.session_state or st.session_state.get("trends_keywords_text") == previous_trends_template:
    st.session_state["trends_keywords_text"] = next_trends_template
st.session_state["keyword_template_retailer"] = retailer_label
apify_source_active = bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))

st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
st.markdown(
    "<div class='topbar'>"
    "<div class='tt'>Market Intelligence <span>/ External Signals</span></div>"
    "<div class='tbadge'>AI Workbench</div>"
    "<div style='margin-left:auto;display:flex;align-items:center;gap:8px;'>"
    "<span class='ldot'></span>"
    "<span style='font-size:10px;color:var(--t3);font-weight:800;'>Live API Mode</span>"
    f"<div style='width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,var(--odk),var(--or));display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#fff;'>{escape(retailer_initials(retailer_label))}</div>"
    "</div></div>",
    unsafe_allow_html=True,
)

hero_left, hero_right = st.columns([2.2, 0.9], vertical_alignment="center")
with hero_left:
    st.markdown(
        "<div class='hero-shell'>"
        "<div class='hero-kicker'><span class='ldot'></span> External Signal Layer</div>"
        "<h1 class='hero-title'>Market Intelligence Command Center</h1>"
        "<p class='hero-copy'>A retail-grade workbench that turns news, CPI, recalls, weather alerts, search demand, and API health into forecast-ready features, High/Medium/Low signal levels, buyer actions, and a demand-planning handoff.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
with hero_right:
    active_sources = sum([use_gnews, use_bls, use_fda, use_weather, apify_source_active])
    st.markdown(
        "<div class='hero-side'>"
        "<div class='hero-side-label'>Run Profile</div>"
        f"<div class='hero-side-row'><span>Retailer</span><strong>{escape(retailer)}</strong></div>"
        f"<div class='hero-side-row'><span>Region</span><strong>{escape(region)}</strong></div>"
        f"<div class='hero-side-row'><span>Sources</span><strong>{active_sources} enabled</strong></div>"
        f"<div class='hero-side-row'><span>LLM</span><strong>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</strong></div>"
        "</div>",
        unsafe_allow_html=True,
    )

view = st.segmented_control(
    "Workbench view",
    ["Configure", "Signal Search", "Demand Planner", "Results", "Evidence Audit", "Raw Data"],
    required=True,
    label_visibility="collapsed",
    key="workbench_view",
    width="content",
)

run_status_slot = st.empty()
if not run_button and st.session_state.get("run"):
    last_run = st.session_state["run"]
    last_states = {}
    for key, result in last_run.get("results", {}).items():
        status = result.get("status", "unknown")
        last_states[key.upper()] = {
            "status": status if status in {"success", "failed", "skipped"} else "queued",
            "detail": result.get("error") or f"{len(result.get('rows', []))} feature row(s)",
        }
    last_llm_audit = last_run.get("llm_audit", {})
    if last_llm_audit:
        last_states["BRIEF"] = {
            "status": "success",
            "detail": "NVIDIA generated the brief" if last_run.get("brief_source") == "nvidia" else f"Fallback brief generated: {display_fallback_reason(last_llm_audit.get('fallback_reason', 'NVIDIA unavailable'))}",
        }
    if last_states:
        render_run_monitor(run_status_slot, f"Last run completed at {last_run.get('timestamp', '')}", last_states, 100)
    render_sidebar_status(sidebar_status_slot, f"Last run complete: {last_run.get('timestamp', '')}", "success")
elif not run_button:
    render_sidebar_status(sidebar_status_slot, "Status: idle. Apify runs only when token is present and live mode is set to Run one live Apify call.", "info")

if view == "Signal Search":
    st.markdown('<div class="small-header">New Signal Search</div>', unsafe_allow_html=True)
    st.markdown(
        "<div class='config-tab-note'>"
        "Generate a focused Apify Google Trends signal for a short time window. "
        "Enter up to two keywords, select a U.S. geography, and generate the signal. "
        "Completed searches are stored locally in SQLite for lightweight run versioning."
        "</div>",
        unsafe_allow_html=True,
    )

    search_left, search_mid, search_right = st.columns([1.5, 1, 1])
    with search_left:
        st.text_area(
            "Signal keywords",
            key="signal_search_keywords",
            height=96,
            placeholder="Example: winter storm\nbatteries",
            help=f"Enter up to {APIFY_HARD_KEYWORD_LIMIT} keywords, separated by a new line or comma.",
        )
    with search_mid:
        st.selectbox(
            "Time window",
            list(SIGNAL_SEARCH_TIME_WINDOWS.keys()),
            key="signal_search_time_window",
        )
    with search_right:
        st.selectbox(
            "U.S. geography",
            list(US_SIGNAL_GEOS.keys()),
            key="signal_search_geo",
        )

    st.caption(
        f"Source: Apify Google Trends · Maximum {APIFY_HARD_KEYWORD_LIMIT} keywords per search · "
        "The Generate Signal button makes one guarded live Apify call."
    )
    generate_signal_button = st.button("Generate Signal", type="primary")

    if generate_signal_button:
        search_keywords = parse_signal_search_keywords(st.session_state.get("signal_search_keywords", ""))
        search_window_label = st.session_state.get("signal_search_time_window", "Last 1 day")
        search_time_range = SIGNAL_SEARCH_TIME_WINDOWS.get(search_window_label, "now 1-d")
        search_geo_label = st.session_state.get("signal_search_geo", "United States - National")
        search_geo = US_SIGNAL_GEOS.get(search_geo_label, "US")

        if not apify_token.strip():
            st.error("Please provide the Apify token under Credentials before generating a live signal.")
        elif not search_keywords:
            st.error("Please enter at least one signal keyword.")
        elif len(search_keywords) > APIFY_HARD_KEYWORD_LIMIT:
            st.error(f"Please enter no more than {APIFY_HARD_KEYWORD_LIMIT} keywords for one search.")
        else:
            with st.spinner("Generating the filtered signal..."):
                apify_result = collect_apify_trends(
                    apify_token.strip(),
                    search_keywords,
                    search_geo,
                    search_time_range,
                    retailer_label,
                    len(search_keywords),
                )

                if apify_result.get("status") != "success":
                    st.error(apify_result.get("error") or "Apify did not return a usable signal for this search.")
                else:
                    search_feature_df = pd.DataFrame(apify_result.get("rows", []))
                    if not search_feature_df.empty:
                        search_feature_df["retailer"] = retailer_label
                        search_feature_df["selected_market"] = search_geo_label
                        search_feature_df = enrich_feature_rows_for_retailer(search_feature_df, retailer_label)

                    search_config = {
                        "retailer": retailer_label,
                        "region": search_geo_label,
                        "country": "US",
                        "language": language,
                        "enabled_sources": {"gnews": False, "bls": False, "fda": False, "weather": False, "apify": True},
                        "use_gnews": False,
                        "use_bls": False,
                        "use_fda": False,
                        "use_weather": False,
                        "use_apify": True,
                        "news_keywords": [],
                        "trends_keywords": search_keywords,
                        "apify_token_present": True,
                        "apify_run_mode": "Generate Signal",
                        "apify_geo": search_geo,
                        "apify_time_range": search_time_range,
                        "apify_max_keywords": len(search_keywords),
                        "apify_live_confirm": True,
                        "signal_search": True,
                    }
                    search_results = {"apify": apify_result}
                    search_brief, search_brief_source, search_llm_audit = generate_nvidia_brief(
                        nvidia_key.strip(),
                        nvidia_model.strip(),
                        search_feature_df,
                        [],
                        retailer_label,
                        search_geo_label,
                    )
                    search_run = {
                        "timestamp": utc_now(),
                        "run_config": search_config,
                        "results": search_results,
                        "feature_df": search_feature_df,
                        "articles": [],
                        "brief": search_brief,
                        "brief_source": search_brief_source,
                        "llm_audit": search_llm_audit,
                    }
                    if st.session_state.get("run"):
                        st.session_state["previous_run"] = st.session_state["run"]
                    st.session_state["run"] = search_run
                    save_run_history(
                        search_run,
                        run_type="Signal Search",
                        source="Apify Google Trends",
                        keywords=", ".join(search_keywords),
                        time_window=search_window_label,
                        geography=search_geo_label,
                    )
                    st.session_state["force_results_view"] = True
                    st.success("Signal generated and saved to local run history. Opening Results...")
                    st.rerun()

    st.markdown('<div class="small-header">Recent Run Versions</div>', unsafe_allow_html=True)
    history_df = load_run_history(10)
    if history_df.empty:
        st.info("No saved versions yet. Completed intelligence runs and signal searches will appear here.")
    else:
        st.dataframe(history_df, width="stretch", hide_index=True)


if view == "Demand Planner":
    render_demand_planner_view(nvidia_key, nvidia_model)


if view == "Configure":
    source_specs = [
        {
            "name": "GNews/RSS",
            "status": "Active" if use_gnews else "Off",
            "signal": "Retail news and competitor events",
            "feature": "retail_news_event_score",
            "owner": "Category Manager",
            "output": "Market Event Risk",
        },
        {
            "name": "BLS CPI",
            "status": "Active" if use_bls else "Off",
            "signal": "Headline and category inflation",
            "feature": "cpi_pressure_features",
            "owner": "Demand Planning",
            "output": "Value Basket Pressure",
        },
        {
            "name": "openFDA",
            "status": "Active" if use_fda else "Off",
            "signal": "Food recall enforcement records",
            "feature": "recall_exposure_score",
            "owner": "Compliance + Buyer",
            "output": "Safety And Compliance Risk",
        },
        {
            "name": "NOAA Weather",
            "status": "Active" if use_weather else "Off",
            "signal": "State weather alerts",
            "feature": "state_weather_disruption_score",
            "owner": "Supply Chain",
            "output": "Route And Store Risk",
        },
        {
            "name": "Apify Trends",
            "status": "Active" if apify_source_active else "Off",
            "signal": "Search interest by region",
            "feature": "google_trends_interest_score",
            "owner": "Demand Planning",
            "output": "Demand Interest Spike",
        },
    ]
    readiness_score = min(
        100,
        35
        + (active_sources * 10)
        + (10 if retailer_label and region else 0)
        + (5 if st.session_state.get("apify_run_mode", "Skip Apify") == "Skip Apify" else 10),
    )
    readiness = {
        "Retail context": f"{retailer_label} / {region}",
        "Public sources": f"{active_sources} enabled",
        "Brief mode": "NVIDIA" if nvidia_key.strip() else "Fallback",
        "Apify guardrail": st.session_state.get("apify_run_mode", "Skip Apify"),
    }
    readiness_rows = "".join(
        f"<div class='readiness-row'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in readiness.items()
    )
    control_cards = [
        ("Evidence trace", "ok", "Raw payloads and normalized rows remain inspectable in Evidence Audit."),
        ("Apify spend", "ok" if st.session_state.get("apify_run_mode", "Skip Apify") == "Skip Apify" else "warn", "Live trend calls require explicit guarded mode and reset after the run."),
        ("Internal data", "warn", "POS, inventory, product hierarchy, and store/DC data are not connected yet."),
    ]
    control_html = "".join(
        f"<div class='control-check {state}'><div class='control-check-title'>{escape(title)}</div><div class='control-check-copy'>{escape(copy)}</div></div>"
        for title, state, copy in control_cards
    )
    st.markdown(
        "<div class='config-shell'>"
        "<div class='config-panel emphasis'>"
        "<div class='config-eyebrow'>Run Blueprint</div>"
        "<div class='config-title'>Market intelligence run</div>"
        "<div class='config-copy'>A governed setup surface for turning public market signals into category, owner, forecast-feature, and action-ready outputs.</div>"
        f"<div class='readiness-score'><div><div class='readiness-score-value'>{readiness_score}</div><div class='readiness-score-label'>Readiness Score</div></div></div>"
        f"<div class='readiness-list'>{readiness_rows}</div>"
        "</div>"
        "<div class='config-panel'>"
        "<div class='config-eyebrow'>Controls</div>"
        "<div class='config-title'>Governed by evidence, cost guardrails, and scope limits</div>"
        "<div class='config-copy'>The run can support buyer and planner discussion, but it does not claim internal category performance until internal POS, inventory, product hierarchy, vendor, promotion, and store/DC data are connected.</div>"
        f"<div class='control-grid'>{control_html}</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="small-header">Source To Feature Routing</div>', unsafe_allow_html=True)
    routing_cards = []
    for spec in source_specs:
        status = spec["status"]
        status_class = "pill-high" if status == "Active" else "pill-low"
        tile_state = "active" if status == "Active" else "off"
        routing_cards.append(
            f"<div class='routing-card {tile_state}'>"
            f"<div class='routing-source'>{escape(spec['name'])} <span class='pill {status_class}' style='margin-left:6px;margin-top:0;'>{escape(status)}</span></div>"
            f"<div class='routing-line'><div class='routing-label'>Signal</div><div class='routing-value'>{escape(spec['signal'])}</div></div>"
            f"<div class='routing-line'><div class='routing-label'>Forecast Feature</div><div class='routing-value'>{escape(spec['feature'])}</div></div>"
            f"<div class='routing-line'><div class='routing-label'>Owner / KPI</div><div class='routing-value'>{escape(spec['owner'])} / {escape(spec['output'])}</div></div>"
            "</div>"
        )
    st.markdown("<div class='routing-grid'>" + "".join(routing_cards) + "</div>", unsafe_allow_html=True)

    setup_tab, collector_tab, governance_tab = st.tabs(["Scope", "Collector Tuning", "Governance"])
    with setup_tab:
        st.markdown(
            "<div class='config-tab-note'>Scope terms decide what the external collectors look for. Keep them readable for business review: retailer, competitor, category, recall, pricing, weather, and seasonal language.</div>",
            unsafe_allow_html=True,
        )
        keyword_guidance = [
            ("Retailer terms", "Anchor the search to the selected retailer so news and trend results stay relevant to this business context."),
            ("Competitor terms", "Capture pressure from Dollar General, Walmart, Five Below, and other value retailers that can affect assortment and pricing."),
            ("Category terms", "Add snacks, candy, household, seasonal, school, party, or consumables to connect signals to buyer-owned categories."),
            ("Risk terms", "Recall, closure, inflation, tariff, weather, and promotion terms help surface early planning risks."),
            ("Trend terms", "Coupon, near me, sales, groceries, and seasonal words influence the Apify Google Trends demand signal."),
            ("Governance", "Changing keywords changes what evidence is pulled; Evidence Audit records the exact keyword scope for each run."),
        ]
        st.markdown(
            "<div class='control-grid'>"
            + "".join(
                f"<div class='control-check ok'><div class='control-check-title'>{escape(title)}</div><div class='control-check-copy'>{escape(copy)}</div></div>"
                for title, copy in keyword_guidance
            )
            + "</div>",
            unsafe_allow_html=True,
        )
        k_left, k_right = st.columns(2)
        with k_left:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("Retail News Keywords")
            st.text_area("GNews keywords", key="news_keywords_text", height=210, label_visibility="collapsed")
            st.markdown("</div>", unsafe_allow_html=True)
        with k_right:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("Search Demand Keywords")
            st.text_area("Apify Google Trends keywords", key="trends_keywords_text", height=210, label_visibility="collapsed")
            st.markdown("</div>", unsafe_allow_html=True)

    with collector_tab:
        st.markdown(
            "<div class='config-tab-note'>Tuning controls runtime, cost, and evidence volume. Defaults stay conservative so the MVP is explainable and does not over-consume Apify credits.</div>",
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("News")
            st.selectbox("GNews period", ["1d", "7d", "30d", "3m"], key="gnews_period")
            st.slider("Max news results", min_value=5, max_value=60, step=1, key="max_news")
            st.markdown("</div>", unsafe_allow_html=True)
        with c2:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("Recall + Weather")
            st.text_input("FDA recall search", key="fda_query")
            st.slider("FDA recall limit", min_value=1, max_value=25, key="fda_limit")
            st.text_input("NOAA weather area", key="weather_area", help="Two-letter US state code, such as TX, NY, CA.")
            st.slider("NOAA alert limit", min_value=1, max_value=25, key="weather_limit")
            st.markdown("</div>", unsafe_allow_html=True)
        with c3:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("Search Demand")
            st.text_input("Apify geo", key="apify_geo")
            st.selectbox(
                "Apify time range",
                APIFY_ALLOWED_TIME_RANGES,
                key="apify_time_range",
                help="The default now 7-d window is the safest weekly actor value. Longer ranges can use more Apify credits.",
            )
            st.slider("Apify max keywords", min_value=1, max_value=APIFY_HARD_KEYWORD_LIMIT, key="apify_max_keywords")
            st.caption("Live mode is controlled in the sidebar guardrail.")
            st.markdown("</div>", unsafe_allow_html=True)

    with governance_tab:
        st.markdown(
            "<div class='config-tab-note'>Governance makes the demo credible: source status is separate from brief status, fallback is labeled, and raw collector payloads remain inspectable in Evidence Audit and Raw Data.</div>",
            unsafe_allow_html=True,
        )
        g1, g2 = st.columns([1, 1])
        with g1:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("Agent Flow")
            render_workflow_strip()
            st.markdown("</div>", unsafe_allow_html=True)
        with g2:
            st.markdown('<div class="console-panel">', unsafe_allow_html=True)
            st.subheader("Glossary")
            render_glossary()
            st.markdown("</div>", unsafe_allow_html=True)


if validate_button:
    with st.spinner("Validating credentials..."):
        n_ok, n_msg = validate_nvidia(nvidia_key.strip(), nvidia_model.strip())
        a_ok, a_msg = validate_apify(apify_token.strip())
    st.session_state["validation"] = {"nvidia": (n_ok, n_msg), "apify": (a_ok, a_msg)}

if test_nvidia_button:
    with st.spinner("Testing NVIDIA with one minimal GPT-OSS 120B request..."):
        t_ok, t_msg = test_nvidia_minimal(nvidia_key.strip(), nvidia_model.strip())
    st.session_state["nvidia_minimal_test"] = (t_ok, t_msg)

if "validation" in st.session_state:
    n_ok, n_msg = st.session_state["validation"]["nvidia"]
    a_ok, a_msg = st.session_state["validation"]["apify"]
    st.info(f"NVIDIA: {'Connected' if n_ok else 'Not connected'} - {n_msg}")
    st.info(f"Apify: {'Connected' if a_ok else 'Not connected'} - {a_msg}")

if "nvidia_minimal_test" in st.session_state:
    t_ok, t_msg = st.session_state["nvidia_minimal_test"]
    if t_ok:
        st.success(f"NVIDIA minimal test succeeded - {t_msg}")
    else:
        st.error(f"NVIDIA minimal test failed - {t_msg}")


if run_button:
    news_keywords = parse_lines(st.session_state.get("news_keywords_text", build_default_news_keywords(retailer_label)))
    trends_keywords = parse_lines(st.session_state.get("trends_keywords_text", build_default_trends_keywords(retailer_label)))
    apify_token_value = apify_token.strip()
    apify_run_mode = st.session_state.get("apify_run_mode", "Skip Apify")
    apify_time_range = st.session_state.get("apify_time_range", APIFY_SAFE_TIME_RANGE)
    if apify_time_range not in APIFY_ALLOWED_TIME_RANGES or not apify_time_range:
        apify_time_range = APIFY_SAFE_TIME_RANGE
    apify_live_confirmed = bool(apify_token_value and apify_run_mode == "Run one live Apify call")
    apify_requested = bool(use_apify or apify_live_confirmed)
    run_config = {
        "retailer": retailer.strip() or "Retailer",
        "region": region,
        "country": country,
        "language": language,
        "enabled_sources": {"gnews": use_gnews, "bls": use_bls, "fda": use_fda, "weather": use_weather, "apify": apify_requested},
        "use_gnews": use_gnews,
        "use_bls": use_bls,
        "use_fda": use_fda,
        "use_weather": use_weather,
        "use_apify": apify_requested,
        "news_keywords": news_keywords,
        "trends_keywords": trends_keywords[:APIFY_HARD_KEYWORD_LIMIT],
        "gnews_period": st.session_state.get("gnews_period", "7d"),
        "max_news": int(st.session_state.get("max_news", 24)),
        "fda_query": st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
        "fda_limit": int(st.session_state.get("fda_limit", 8)),
        "weather_area": normalize_weather_area(st.session_state.get("weather_area", "TX")),
        "weather_limit": int(st.session_state.get("weather_limit", 5)),
        "bls_series": BLS_CPI_SERIES,
        "apify_token_present": bool(apify_token_value),
        "apify_run_mode": apify_run_mode,
        "apify_geo": st.session_state.get("apify_geo", "US"),
        "apify_time_range": apify_time_range,
        "apify_max_keywords": int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
        "apify_live_confirm": apify_live_confirmed,
    }
    results: Dict[str, Dict[str, Any]] = {}
    all_rows: List[Dict[str, Any]] = []
    all_articles: List[Dict[str, Any]] = []

    steps = [
        ("gnews", use_gnews),
        ("bls", use_bls),
        ("fda", use_fda),
        ("weather", use_weather),
        ("apify", apify_requested),
    ]
    active_steps = [step for step in steps if step[1]]
    total = max(1, len(active_steps))
    completed = 0
    collector_states: Dict[str, Dict[str, str]] = {
        "GNEWS": {"status": "queued" if use_gnews else "skipped", "detail": "Retail news collector" if use_gnews else "Disabled"},
        "BLS": {"status": "queued" if use_bls else "skipped", "detail": "CPI collector" if use_bls else "Disabled"},
        "FDA": {"status": "queued" if use_fda else "skipped", "detail": "Recall collector" if use_fda else "Disabled"},
        "WEATHER": {"status": "queued" if use_weather else "skipped", "detail": "NOAA alert collector" if use_weather else "Disabled"},
        "APIFY": {"status": "queued" if apify_requested else "skipped", "detail": f"Trends collector; mode: {apify_run_mode}" if apify_requested else "Disabled"},
        "BRIEF": {"status": "queued", "detail": "NVIDIA grounded brief or local fallback"},
    }
    render_run_monitor(run_status_slot, "Starting collectors", collector_states, 2)
    render_sidebar_status(sidebar_status_slot, "Running: starting collectors...", "info")

    if use_gnews:
        collector_states["GNEWS"] = {"status": "running", "detail": "Collecting and deduplicating retail news"}
        render_run_monitor(run_status_slot, "Collecting retail news", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, "Running: collecting retail news...", "info")
        results["gnews"] = collect_gnews(
            news_keywords,
            country,
            language,
            st.session_state.get("gnews_period", "7d"),
            int(st.session_state.get("max_news", 24)),
            retailer.strip() or "Retailer",
        )
        all_rows.extend(results["gnews"].get("rows", []))
        all_articles.extend(results["gnews"].get("items", []))
        completed += 1
        gnews_status = results["gnews"].get("status", "failed")
        collector_states["GNEWS"] = {
            "status": "success" if gnews_status == "success" else "failed" if gnews_status == "failed" else "skipped",
            "detail": results["gnews"].get("error") or f"{len(results['gnews'].get('items', []))} article(s), {len(results['gnews'].get('rows', []))} feature row(s)",
        }
        render_run_monitor(run_status_slot, "Retail news complete", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"GNews {collector_states['GNEWS']['status']}: {collector_states['GNEWS']['detail']}", "warning" if gnews_status != "success" else "info")

    if use_bls:
        collector_states["BLS"] = {"status": "running", "detail": "Collecting headline and category CPI"}
        render_run_monitor(run_status_slot, "Collecting CPI inflation", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, "Running: collecting BLS CPI...", "info")
        results["bls"] = collect_bls_cpi(bls_key.strip(), retailer.strip() or "Retailer")
        all_rows.extend(results["bls"].get("rows", []))
        completed += 1
        bls_status = results["bls"].get("status", "failed")
        collector_states["BLS"] = {
            "status": "success" if bls_status == "success" else "failed",
            "detail": results["bls"].get("error") or f"{len(results['bls'].get('rows', []))} CPI feature row(s)",
        }
        render_run_monitor(run_status_slot, "CPI collection complete", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"BLS {collector_states['BLS']['status']}: {collector_states['BLS']['detail']}", "warning" if bls_status != "success" else "info")

    if use_fda:
        collector_states["FDA"] = {"status": "running", "detail": "Collecting food recall records"}
        render_run_monitor(run_status_slot, "Collecting FDA recalls", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, "Running: collecting FDA recalls...", "info")
        results["fda"] = collect_fda_recalls(
            st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
            int(st.session_state.get("fda_limit", 8)),
            retailer.strip() or "Retailer",
        )
        all_rows.extend(results["fda"].get("rows", []))
        completed += 1
        fda_status = results["fda"].get("status", "failed")
        collector_states["FDA"] = {
            "status": "success" if fda_status == "success" else "failed",
            "detail": results["fda"].get("error") or f"{len(results['fda'].get('items', []))} recall item(s), {len(results['fda'].get('rows', []))} feature row(s)",
        }
        render_run_monitor(run_status_slot, "FDA recall collection complete", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"FDA {collector_states['FDA']['status']}: {collector_states['FDA']['detail']}", "warning" if fda_status != "success" else "info")

    if use_weather:
        weather_area = normalize_weather_area(st.session_state.get("weather_area", "TX"))
        collector_states["WEATHER"] = {"status": "running", "detail": f"Collecting active NOAA alerts for {weather_area}"}
        render_run_monitor(run_status_slot, "Collecting weather alerts", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"Running: collecting NOAA weather alerts for {weather_area}...", "info")
        results["weather"] = collect_weather_alerts(
            weather_area,
            int(st.session_state.get("weather_limit", 5)),
            retailer.strip() or "Retailer",
        )
        all_rows.extend(results["weather"].get("rows", []))
        completed += 1
        weather_status = results["weather"].get("status", "failed")
        collector_states["WEATHER"] = {
            "status": "success" if weather_status == "success" else "failed",
            "detail": results["weather"].get("error") or f"{len(results['weather'].get('items', []))} active alert item(s), {len(results['weather'].get('rows', []))} feature row(s)",
        }
        render_run_monitor(run_status_slot, "Weather alert collection complete", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"Weather {collector_states['WEATHER']['status']}: {collector_states['WEATHER']['detail']}", "warning" if weather_status != "success" else "info")

    if apify_requested and not apify_token_value:
        results["apify"] = {
            "status": "skipped",
            "source": "Apify Trends",
            "error": "Apify was enabled but no Apify token was provided. No Apify credits were used.",
            "raw": None,
            "rows": [],
            "items": [],
        }
        completed += 1
        collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
        render_run_monitor(run_status_slot, "Apify skipped: token missing", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, "Apify skipped: token missing, no credits used.", "warning")

    elif apify_requested and not apify_live_confirmed:
        results["apify"] = {
            "status": "skipped",
            "source": "Apify Trends",
            "error": f"Apify was enabled but Apify live mode is '{apify_run_mode}'. Select 'Run one live Apify call' in the sidebar to spend one guarded Apify run. No Apify credits were used.",
            "raw": None,
            "rows": [],
            "items": [],
        }
        completed += 1
        collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
        render_run_monitor(run_status_slot, "Apify skipped without spending credits", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, "Apify skipped: no credits used.", "warning")

    elif apify_requested and apify_live_confirmed:
        collector_states["APIFY"] = {"status": "running", "detail": f"One guarded live run: {apify_time_range}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s)"}
        render_run_monitor(run_status_slot, "Collecting Google Trends via Apify", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"Running: Apify Trends live call ({apify_time_range}, max {APIFY_HARD_KEYWORD_LIMIT} keywords)...", "warning")
        results["apify"] = collect_apify_trends(
            apify_token_value,
            trends_keywords,
            st.session_state.get("apify_geo", "US"),
            apify_time_range,
            retailer.strip() or "Retailer",
            int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
        )
        all_rows.extend(results["apify"].get("rows", []))
        completed += 1
        st.session_state["reset_apify_live_confirm"] = True
        apify_status = results["apify"].get("status", "failed")
        collector_states["APIFY"] = {
            "status": "success" if apify_status == "success" else "failed" if apify_status == "failed" else "skipped",
            "detail": results["apify"].get("error") or f"{len(results['apify'].get('items', []))} trends row(s)",
        }
        render_run_monitor(run_status_slot, "Apify collection complete", collector_states, int((completed / total) * 100))
        render_sidebar_status(sidebar_status_slot, f"Apify {collector_states['APIFY']['status']}: {collector_states['APIFY']['detail']}", "warning" if apify_status != "success" else "info")

    collector_states["BRIEF"] = {"status": "running", "detail": "Calling NVIDIA when available, with local fallback ready"}
    render_run_monitor(run_status_slot, "Generating intelligence brief", collector_states, 98)
    render_sidebar_status(sidebar_status_slot, "Running: generating executive brief...", "info")
    feature_df = pd.DataFrame(all_rows)
    if not feature_df.empty:
        feature_df["retailer"] = retailer.strip() or "Retailer"
        if region:
            feature_df["selected_market"] = region
        feature_df = enrich_feature_rows_for_retailer(feature_df, retailer.strip() or "Retailer")
    brief, brief_source, llm_audit = generate_nvidia_brief(nvidia_key.strip(), nvidia_model.strip(), feature_df, all_articles, retailer, region)
    collector_states["BRIEF"] = {
        "status": "success",
        "detail": "NVIDIA generated the brief" if brief_source == "nvidia" else f"Fallback brief generated: {display_fallback_reason(llm_audit.get('fallback_reason', 'NVIDIA unavailable'))}",
    }
    render_run_monitor(run_status_slot, "Run complete", collector_states, 100)

    if st.session_state.get("run"):
        st.session_state["previous_run"] = st.session_state["run"]

    completed_run = {
        "timestamp": utc_now(),
        "run_config": run_config,
        "results": results,
        "feature_df": feature_df,
        "articles": all_articles,
        "brief": brief,
        "brief_source": brief_source,
        "llm_audit": llm_audit,
    }
    st.session_state["run"] = completed_run
    save_run_history(
        completed_run,
        run_type="Full Intelligence Run",
        source="Configured Sources",
        keywords=", ".join(trends_keywords[:APIFY_HARD_KEYWORD_LIMIT]) if apify_requested else "",
        time_window=apify_time_range if apify_requested else "",
        geography=st.session_state.get("apify_geo", "US") if apify_requested else region,
    )
    render_sidebar_status(sidebar_status_slot, "Run complete. Opening Results...", "success")
    st.session_state["force_results_view"] = True
    st.session_state["last_collector_states"] = collector_states
    st.rerun()


if view == "Results":
    run = st.session_state.get("run")
    if not run:
        st.markdown(
            "<div class='empty-console'>"
            "<div>"
            "<div class='hero-kicker'>Results Command Center</div>"
            "<div class='empty-title'>No intelligence run yet.</div>"
            "<div class='empty-body'>Run the governed signal pipeline to populate decision cards, retail impact mapping, level explanations, source evidence, and the executive brief.</div>"
            "</div>"
            "<div class='hero-side' style='min-width:260px;'>"
            "<div class='hero-side-label'>Output Model</div>"
            "<div class='hero-side-row'><span>Decisions</span><strong>Owner actions</strong></div>"
            "<div class='hero-side-row'><span>Evidence</span><strong>Live API rows</strong></div>"
            "<div class='hero-side-row'><span>Brief</span><strong>NVIDIA / fallback</strong></div>"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        render_workflow_strip()
    else:
        feature_df = run["feature_df"]
        if not feature_df.empty and "dollar_tree_category" not in feature_df.columns:
            feature_df = enrich_feature_rows_for_retailer(feature_df, str(run.get("run_config", {}).get("retailer", retailer_label)))
            run["feature_df"] = feature_df
        render_results_command_header(run, feature_df)
        if feature_df.empty:
            st.markdown('<div class="small-header">Run Summary</div>', unsafe_allow_html=True)
            cols = st.columns(4)
            for col, title in zip(cols, ["Signals", "Overall Signal Level", "Top Signal", "Brief"]):
                with col:
                    render_metric_card(title, "0", "No successful feature rows yet.")
        else:
            avg_score = round(float(feature_df["risk_score"].mean()), 2)
            top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
            dollar_tree_scores = compute_dollar_tree_kpis(feature_df)
            command_tab, explain_tab, evidence_tab, brief_tab = st.tabs(["Command Center", "Explainability", "Evidence And Export", "Executive Brief"])

            with command_tab:
                st.markdown('<div class="small-header">Trust And Evidence Status</div>', unsafe_allow_html=True)
                render_trust_panel(run, feature_df)

                st.markdown('<div class="small-header">Run Summary Metrics</div>', unsafe_allow_html=True)
                cols = st.columns(4)
                with cols[0]:
                    render_metric_card("Signals", str(len(feature_df)), "Forecast-ready rows generated.")
                with cols[1]:
                    render_metric_card("Overall Signal Level", risk_band(avg_score), f"Across {len(feature_df)} forecast-ready signals.")
                with cols[2]:
                    render_metric_card("Top Signal", risk_band(float(top["risk_score"])), f"{top['signal_area']} · Strongest external signal in the current run.")
                with cols[3]:
                    render_metric_card("News Articles", str(len(run["articles"])), "Deduplicated retail news items.")

                st.markdown('<div class="small-header">Retail Enterprise KPIs</div>', unsafe_allow_html=True)
                cscore_cols = st.columns(4)
                kpi_notes = {
                    "Value Basket Pressure": "Inflation and value-basket pressure.",
                    "Safety And Compliance Risk": "Recall and compliance exposure.",
                    "Supply Chain Disruption": "Weather and replenishment disruption.",
                    "Demand Signal Priority": "News and demand-interest signals.",
                }
                for col, (name, score) in zip(cscore_cols, dollar_tree_scores.items()):
                    with col:
                        render_metric_card(name, risk_band(float(score)), kpi_notes.get(name, "Current planning classification."))

                st.markdown('<div class="small-header">Decision Summary</div>', unsafe_allow_html=True)
                render_decision_summary(feature_df, run["results"])

                st.markdown('<div class="small-header">Previous Run Comparison</div>', unsafe_allow_html=True)
                render_previous_run_comparison(feature_df, st.session_state.get("previous_run"))

                st.markdown('<div class="small-header">Retail Impact Matrix</div>', unsafe_allow_html=True)
                render_dollar_tree_impact_matrix(feature_df)

                st.markdown('<div class="small-header">Internal Validation Agent</div>', unsafe_allow_html=True)
                render_internal_validation_agent(feature_df)

            with explain_tab:
                st.markdown('<div class="small-header">How The Agent Explains A Signal</div>', unsafe_allow_html=True)
                render_explainability_ladder()

                st.markdown('<div class="small-header">Scenario Simulator</div>', unsafe_allow_html=True)
                render_scenario_simulator(feature_df)

                st.markdown('<div class="small-header">Signal Levels</div>', unsafe_allow_html=True)
                render_score_chart(feature_df)

                st.markdown('<div class="small-header">Level Explainability</div>', unsafe_allow_html=True)
                render_score_explainability(feature_df)

            with evidence_tab:
                st.markdown('<div class="small-header">Forecast Feature Table</div>', unsafe_allow_html=True)
                display_feature_df = feature_df.copy()
                if "risk_score" in display_feature_df.columns:
                    display_feature_df["risk_score"] = display_feature_df["risk_score"].apply(lambda x: risk_band(float(x or 0)))
                    display_feature_df = display_feature_df.rename(columns={"risk_score": "Risk Level"})
                st.dataframe(style_level_dataframe(display_feature_df), width="stretch", hide_index=True)

                csv_data = feature_df.to_csv(index=False).encode("utf-8")
                json_data = json.dumps(feature_df.to_dict(orient="records"), indent=2).encode("utf-8")
                c1, c2 = st.columns([1, 1])
                with c1:
                    st.download_button("Download forecast_features.csv", csv_data, "forecast_features.csv", "text/csv", width="stretch")
                with c2:
                    st.download_button("Download normalized_signals.json", json_data, "normalized_signals.json", "application/json", width="stretch")

                with st.expander("Glossary and interpretation guide", expanded=False):
                    render_glossary()

            with brief_tab:
                render_executive_brief(run, feature_df)


if view == "Evidence Audit":
    run = st.session_state.get("run")
    if not run:
        st.markdown(
            "<div class='empty-console'>"
            "<div>"
            "<div class='hero-kicker'>Evidence Audit</div>"
            "<div class='empty-title'>No run evidence yet.</div>"
            "<div class='empty-body'>Run the governed signal pipeline to populate provenance, source health, mock-data status, normalized signal reasoning, LLM prompt trace, payload hash, and raw API evidence.</div>"
            "</div>"
            "<div class='hero-side' style='min-width:260px;'>"
            "<div class='hero-side-label'>Audit Model</div>"
            "<div class='hero-side-row'><span>Provenance</span><strong>Source health</strong></div>"
            "<div class='hero-side-row'><span>Trace</span><strong>Prompt + payload</strong></div>"
            "<div class='hero-side-row'><span>Evidence</span><strong>Raw API data</strong></div>"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        feature_df = run.get("feature_df", pd.DataFrame())

        if run.get("llm_audit", {}).get("technical_error"):
            with st.expander("Exact NVIDIA technical error", expanded=False):
                st.code(str(run.get("llm_audit", {}).get("technical_error")))
        run_config = run.get(
            "run_config",
            {
                "retailer": retailer_label,
                "region": region,
                "country": country,
                "language": language,
                "enabled_sources": {key: key in run.get("results", {}) for key in SOURCE_ORDER},
            },
        )
        if not feature_df.empty and "dollar_tree_category" not in feature_df.columns:
            feature_df = enrich_feature_rows_for_retailer(feature_df, str(run_config.get("retailer", retailer_label)))
            run["feature_df"] = feature_df
        llm_audit = run.get("llm_audit") or build_base_llm_audit(
            feature_df,
            run.get("articles", []),
            run_config.get("retailer", retailer_label),
            run_config.get("region", region),
            nvidia_model.strip() or DEFAULT_NVIDIA_MODEL,
        )
        llm_audit["brief_source"] = run.get("brief_source", llm_audit.get("brief_source", "unknown"))
        evidence_records = build_collector_evidence(run.get("results", {}), run_config)
        evidence_df = pd.DataFrame(evidence_records)
        mock_used = any_mock_used(run.get("results", {}), llm_audit)
        pulled_sources = int((evidence_df["raw_records_pulled"] > 0).sum()) if not evidence_df.empty else 0
        raw_records = int(evidence_df["raw_records_pulled"].sum()) if not evidence_df.empty else 0

        evidence_bundle = {
            "timestamp": run.get("timestamp"),
            "mock_data_used": mock_used,
            "run_config": run_config,
            "collector_evidence": evidence_records,
            "normalized_features": feature_df.to_dict(orient="records") if not feature_df.empty else [],
            "llm_audit": llm_audit,
            "results": {
                name: {
                    "status": result.get("status"),
                    "source": result.get("source"),
                    "error": result.get("error"),
                    "rows": result.get("rows", []),
                    "items": result.get("items", []),
                    "raw": result.get("raw"),
                }
                for name, result in run.get("results", {}).items()
            },
        }

        render_audit_command_header(run, mock_used, raw_records, pulled_sources, llm_audit, feature_df)
        render_audit_banner(mock_used, str(run.get("brief_source", "unknown")))

        provenance_tab, normalized_tab, llm_tab, raw_tab = st.tabs(["Provenance", "Normalized Signals", "LLM Trace", "Raw Payloads"])

        with provenance_tab:
            st.markdown('<div class="small-header">Chain Of Custody</div>', unsafe_allow_html=True)
            render_audit_lineage()

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                render_audit_card("Mock Data Used", "Yes" if mock_used else "No", "Collector rows are marked per run result.")
            with c2:
                render_audit_card("Raw Records", str(raw_records), f"{pulled_sources} source(s) returned inspectable data.")
            with c3:
                render_audit_card(
                    "Rows Sent To Brief",
                    f"{llm_audit.get('feature_rows_sent', 0)} / {llm_audit.get('feature_rows_available', 0)}",
                    "Feature payload is capped for concise LLM context.",
                )
            with c4:
                render_audit_card(
                    "LLM Call",
                    "NVIDIA" if llm_audit.get("sent_to_llm") else "No external LLM",
                    display_fallback_reason(llm_audit.get("fallback_reason") or "NVIDIA analyzed the shown payload."),
                )

            st.markdown('<div class="small-header">Collector Evidence Table</div>', unsafe_allow_html=True)
            st.dataframe(evidence_df, width="stretch", hide_index=True)

            st.download_button(
                "Download evidence_audit.json",
                json.dumps(evidence_bundle, indent=2, default=str).encode("utf-8"),
                "evidence_audit.json",
                "application/json",
                width="stretch",
            )

        with normalized_tab:
            st.markdown('<div class="small-header">Normalized Feature Rows And Level Reasoning</div>', unsafe_allow_html=True)
            if feature_df.empty:
                st.info("No normalized feature rows were generated. Check collector statuses and errors above.")
            else:
                preferred_cols = [
                    "source",
                    "signal_area",
                    "signal_name",
                    "dollar_tree_category",
                    "enterprise_kpi",
                    "planning_owner",
                    "demand_direction",
                    "region",
                    "region_scope",
                    "signal_value",
                    "risk_score",
                    "confidence",
                    "score_reason",
                    "impact_hypothesis",
                    "business_impact",
                    "recommended_action",
                    "internal_data_needed",
                    "raw_reference",
                ]
                visible_cols = [col for col in preferred_cols if col in feature_df.columns]
                normalized_display_df = feature_df[visible_cols].copy()
                if "risk_score" in normalized_display_df.columns:
                    normalized_display_df["risk_score"] = normalized_display_df["risk_score"].apply(lambda x: risk_band(float(x or 0)))
                    normalized_display_df = normalized_display_df.rename(columns={"risk_score": "Risk Level"})
                st.dataframe(style_level_dataframe(normalized_display_df), width="stretch", hide_index=True)

            st.markdown('<div class="small-header">Level Explainability Cards</div>', unsafe_allow_html=True)
            render_score_explainability(feature_df)

        with llm_tab:
            st.markdown('<div class="small-header">LLM Analysis Trace</div>', unsafe_allow_html=True)
            trace_cols = st.columns(4)
            with trace_cols[0]:
                render_metric_card("Brief Source", "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback", "NVIDIA if sent; fallback if local rules were used.")
            with trace_cols[1]:
                render_metric_card("Payload Hash", str(llm_audit.get("payload_hash_sha256", ""))[:12], "SHA-256 fingerprint of features/articles payload.")
            with trace_cols[2]:
                render_metric_card("Articles Sent", str(llm_audit.get("articles_sent", 0)), f"{llm_audit.get('articles_available', 0)} available.")
            with trace_cols[3]:
                render_metric_card("Fallback Used", "Yes" if llm_audit.get("fallback_used") else "No", display_fallback_reason(llm_audit.get("fallback_reason") or "External LLM response used."))

            with st.expander("Prompt and payload used for the brief", expanded=True):
                if llm_audit.get("sent_to_llm"):
                    st.success("NVIDIA was called with only the feature rows and article records shown below.")
                else:
                    st.warning("No external LLM call was made for this run. The brief was generated by deterministic local rules using the same feature rows.")
                st.markdown("System prompt")
                st.code(str(llm_audit.get("system_prompt", "")), language="text")
                st.markdown("User prompt")
                st.code(str(llm_audit.get("user_prompt", "")), language="text")
                st.markdown("Payload")
                st.code(json.dumps(llm_audit.get("payload", {}), indent=2, default=str)[:16000], language="json")
                if llm_audit.get("nvidia_attempts"):
                    st.markdown("NVIDIA timing and retry audit")
                    st.dataframe(pd.DataFrame(llm_audit.get("nvidia_attempts", [])), width="stretch", hide_index=True)
                if llm_audit.get("technical_error"):
                    st.markdown("Technical diagnostic")
                    st.code(str(llm_audit.get("technical_error", ""))[:2000], language="text")

            with st.expander("Brief output", expanded=False):
                st.markdown(f"<div class='brief-box'>{brief_to_html(str(run.get('brief', '')))}</div>", unsafe_allow_html=True)

        with raw_tab:
            st.markdown('<div class="small-header">Raw Pulled Data By Source</div>', unsafe_allow_html=True)
            for name, result in run.get("results", {}).items():
                source_name = SOURCE_LABELS.get(name, name.upper())
                raw_count = count_raw_records(result.get("raw"), result.get("items"))
                with st.expander(f"{source_name} - {result.get('status', 'unknown')} - {raw_count} raw record(s)", expanded=False):
                    if result.get("error"):
                        st.warning(result["error"])
                    if result.get("items"):
                        st.markdown("Cleaned items")
                        st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
                    raw_preview = result.get("raw")
                    if raw_preview is not None:
                        st.markdown("Raw payload")
                        st.code(json.dumps(raw_preview, indent=2, default=str)[:16000], language="json")
                    if result.get("rows"):
                        st.markdown("Normalized feature rows from this source")
                        st.dataframe(pd.DataFrame(result["rows"]), width="stretch", hide_index=True)


if view == "Raw Data":
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
                    st.code(json.dumps(raw_preview, indent=2, default=str)[:7000], language="json")


st.markdown(
    """
    <p class="subtle">
    Note: Google Trends values are relative indexes, GNews is a lightweight news signal, and recall data should be matched
    against internal SKU/UPC and inventory records before operational decisions.
    </p>
    """,
    unsafe_allow_html=True,
)


# import json
# import hashlib
# import os
# import re
# import time
# import xml.etree.ElementTree as ET
# from datetime import datetime, timezone
# from email.utils import parsedate_to_datetime
# from html import escape, unescape
# from typing import Any, Dict, List, Optional, Tuple
# from urllib.parse import quote_plus

# import pandas as pd
# import plotly.graph_objects as go
# import requests
# import streamlit as st

# try:
#     from dotenv import load_dotenv
# except ImportError:  # pragma: no cover - optional local convenience
#     load_dotenv = None


# if load_dotenv:
#     load_dotenv()


# NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# DEFAULT_NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
# NVIDIA_MAX_FEATURE_ROWS = 8
# NVIDIA_MAX_ARTICLES = 4
# NVIDIA_MAX_TOKENS = 520
# NVIDIA_TIMEOUT_SECONDS = [35, 35, 20]
# BLS_CPI_SERIES = {
#     "Headline CPI": "CUUR0000SA0",
#     "Food at home": "CUUR0000SAF11",
#     "Household furnishings": "CUUR0000SAH3",
#     "Gasoline": "CUUR0000SETB",
# }
# APIFY_ALLOWED_TIME_RANGES = ["", "now 1-H", "now 4-H", "now 1-d", "now 7-d", "today 1-m", "today 3-m", "today 5-y", "all"]
# APIFY_SAFE_TIME_RANGE = "now 7-d"
# APIFY_HARD_KEYWORD_LIMIT = 2

# SOURCE_ORDER = ["gnews", "bls", "fda", "weather", "apify"]
# SOURCE_LABELS = {
#     "gnews": "GNews / Google News RSS",
#     "bls": "BLS CPI",
#     "fda": "openFDA Food Enforcement",
#     "weather": "NOAA Weather Alerts",
#     "apify": "Apify Google Trends",
# }
# SOURCE_ENDPOINTS = {
#     "gnews": "GNews package or Google News RSS search feed",
#     "bls": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
#     "fda": "https://api.fda.gov/food/enforcement.json",
#     "weather": "https://api.weather.gov/alerts/active",
#     "apify": "apify/google-trends-scraper",
# }
# ANALYSIS_METHODS = {
#     "gnews": "Classifies article titles/descriptions into event types, sentiment, source confidence, then averages article risk into one news feature.",
#     "bls": "Batches CPI series, calculates month-over-month and year-over-year movement, then scores inflation pressure.",
#     "fda": "Classifies recall reason, FDA class, status, UPC presence, and state coverage, then uses highest adjusted recall severity.",
#     "weather": "Collects active NOAA alerts for the selected state, weights severity, urgency, and certainty, then caps supply-chain weather risk at 10.",
#     "apify": "Uses top regional Google Trends index divided by 10; backend hard-limits time range and keyword count to protect quota.",
# }


# st.set_page_config(
#     page_title="Market Intelligence Command Center",
#     page_icon="DT",
#     layout="wide",
#     initial_sidebar_state="expanded",
# )


# st.markdown(
#     """
#     <style>
#     @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
#     :root {
#         --bg: #F5F7FB;
#         --sf: #FFFFFF;
#         --s2: #F8FAFE;
#         --s3: #F0F4F9;
#         --s4: #E9EFF5;
#         --or: #F47B25;
#         --olt: #FF9F50;
#         --odk: #C45D0A;
#         --og: rgba(244,123,37,0.12);
#         --ob: rgba(244,123,37,0.07);
#         --obr: rgba(244,123,37,0.25);
#         --bl: #E2E8F0;
#         --t: #1E293B;
#         --t2: #475569;
#         --t3: #94A3B8;
#         --gr: #22C55E;
#         --gbg: rgba(34,197,94,0.10);
#         --am: #F59E0B;
#         --abg: rgba(245,158,11,0.10);
#         --rd: #EF4444;
#         --rbg: rgba(239,68,68,0.08);
#         --r: 12px;
#         --rl: 16px;
#         --sh: 0 1px 3px rgba(0,0,0,0.04);
#         --shm: 0 6px 14px -4px rgba(0,0,0,0.10);
#         --tr: 0.2s cubic-bezier(0.4,0,0.2,1);
#     }
#     * { box-sizing: border-box; }
#     html, body, [class*="css"] {
#         font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
#         color: var(--t);
#     }
#     .stApp { background: var(--bg); }
#     #MainMenu, footer { visibility: hidden; }
#     header { visibility: hidden; }
#     .accent-bar {
#         height: 3px;
#         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt), var(--or));
#         background-size: 200%;
#         animation: shimmer 3s linear infinite;
#         width: 100%;
#         margin: -1.25rem 0 0.9rem 0;
#     }
#     @keyframes shimmer { 0% { background-position: 200%; } 100% { background-position: -200%; } }
#     @keyframes pdot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.5); } 50% { box-shadow: 0 0 0 5px rgba(34,197,94,0); } }
#     .ldot {
#         width: 7px;
#         height: 7px;
#         border-radius: 50%;
#         background: var(--gr);
#         animation: pdot 2s infinite;
#         display: inline-block;
#     }
#     .main .block-container {
#         padding-top: 1.25rem;
#         max-width: 1400px;
#     }
#     [data-testid="stSidebar"] {
#         background: var(--sf) !important;
#         border-right: 1px solid var(--bl) !important;
#     }
#     [data-testid="stSidebar"] * {
#         color: var(--t2);
#     }
#     [data-testid="stSidebar"] h1 {
#         font-size: 18px !important;
#         line-height: 1.2 !important;
#         margin-bottom: 2px !important;
#     }
#     [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
#         font-size: 12px;
#     }
#     .sidebar-brand {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-left: 4px solid var(--or);
#         border-radius: 8px;
#         padding: 12px;
#         margin: 2px 0 12px 0;
#     }
#     .sidebar-brand-title {
#         color: var(--t);
#         font-size: 15px;
#         font-weight: 950;
#         line-height: 1.15;
#         margin-bottom: 4px;
#     }
#     .sidebar-brand-copy {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.4;
#     }
#     .sidebar-summary {
#         display: grid;
#         grid-template-columns: repeat(2, minmax(0, 1fr));
#         gap: 6px;
#         margin: 8px 0 10px 0;
#     }
#     .sidebar-summary-card {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 8px;
#         min-height: 58px;
#     }
#     .sidebar-summary-label {
#         color: var(--t3);
#         font-size: 8px;
#         font-weight: 900;
#         letter-spacing: .8px;
#         text-transform: uppercase;
#         margin-bottom: 3px;
#     }
#     .sidebar-summary-value {
#         color: var(--t);
#         font-size: 12px;
#         line-height: 1.2;
#         font-weight: 900;
#     }
#     .sidebar-divider {
#         height: 1px;
#         background: var(--bl);
#         margin: 10px 0;
#     }
#     h1, h2, h3 {
#         color: var(--t);
#         letter-spacing: 0;
#     }
#     h1 { font-size: 2.25rem; font-weight: 900; letter-spacing: -0.02em; }
#     .subtle {
#         color: var(--t2);
#         font-size: 0.94rem;
#         line-height: 1.55;
#     }
#     .topbar {
#         height: 54px;
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 10px;
#         display: flex;
#         align-items: center;
#         padding: 0 18px;
#         gap: 12px;
#         box-shadow: var(--sh);
#         margin-bottom: 18px;
#     }
#     .tt { font-size: 14px; font-weight: 800; color: var(--t); }
#     .tt span { color: var(--t3); font-weight: 500; }
#     .tbadge {
#         background: var(--ob);
#         border: 1px solid var(--obr);
#         color: var(--or);
#         font-size: 10px;
#         font-weight: 800;
#         padding: 2px 8px;
#         border-radius: 20px;
#     }
#     .hero-shell {
#         background: linear-gradient(180deg, #FFFFFF 0%, #FAFCFF 100%);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 16px 18px;
#         box-shadow: var(--sh);
#         margin-bottom: 12px;
#         position: relative;
#         overflow: hidden;
#     }
#     .hero-shell:before {
#         content: "";
#         position: absolute;
#         left: 0;
#         right: 0;
#         top: 0;
#         height: 4px;
#         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
#     }
#     .hero-kicker {
#         display: inline-flex;
#         align-items: center;
#         gap: 7px;
#         background: var(--ob);
#         border: 1px solid var(--obr);
#         color: var(--or);
#         border-radius: 999px;
#         padding: 4px 10px;
#         font-size: 10px;
#         font-weight: 900;
#         letter-spacing: 0.8px;
#         text-transform: uppercase;
#         margin-bottom: 12px;
#     }
#     .hero-title {
#         font-size: 28px;
#         line-height: 1.08;
#         letter-spacing: 0;
#         font-weight: 950;
#         color: var(--t);
#         max-width: 820px;
#         margin: 0;
#     }
#     .hero-copy {
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.55;
#         max-width: 820px;
#         margin: 10px 0 0 0;
#     }
#     .hero-side {
#         background: #FFFFFF;
#         border: 1px solid rgba(226,232,240,0.9);
#         border-radius: 8px;
#         padding: 14px;
#         box-shadow: var(--sh);
#     }
#     .hero-side-label {
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1.2px;
#         text-transform: uppercase;
#         margin-bottom: 8px;
#     }
#     .hero-side-row {
#         display: flex;
#         justify-content: space-between;
#         gap: 12px;
#         border-top: 1px solid var(--bl);
#         padding-top: 8px;
#         margin-top: 8px;
#         font-size: 12px;
#         color: var(--t2);
#     }
#     .hero-side-row strong { color: var(--t); }
#     .source-tile {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 13px 14px;
#         box-shadow: var(--sh);
#         min-height: 86px;
#         transition: all var(--tr);
#     }
#     .source-tile:hover { border-color: var(--obr); box-shadow: var(--shm); transform: translateY(-1px); }
#     .source-name {
#         font-size: 12px;
#         font-weight: 850;
#         color: var(--t);
#         margin-bottom: 5px;
#     }
#     .source-meta {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.45;
#     }
#     .console-panel {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 18px;
#         box-shadow: var(--sh);
#         margin-top: 12px;
#     }
#     .config-shell {
#         display: grid;
#         grid-template-columns: .72fr 1.28fr;
#         gap: 12px;
#         margin: 10px 0 14px 0;
#     }
#     .config-panel {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 14px 15px;
#         box-shadow: var(--sh);
#         min-height: 100%;
#     }
#     .config-panel.emphasis {
#         border-left: 4px solid var(--or);
#     }
#     .config-eyebrow {
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin-bottom: 6px;
#     }
#     .config-title {
#         color: var(--t);
#         font-size: 16px;
#         font-weight: 950;
#         line-height: 1.15;
#         margin-bottom: 6px;
#     }
#     .config-copy {
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.5;
#     }
#     .readiness-list {
#         display: grid;
#         gap: 7px;
#         margin-top: 12px;
#     }
#     .readiness-row {
#         display: flex;
#         justify-content: space-between;
#         gap: 12px;
#         align-items: center;
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 8px 9px;
#         font-size: 11px;
#         color: var(--t2);
#     }
#     .readiness-row strong { color: var(--t); font-size: 12px; }
#     .source-grid {
#         display: grid;
#         grid-template-columns: repeat(5, minmax(0, 1fr));
#         gap: 8px;
#         margin: 8px 0 12px 0;
#     }
#     .source-tile.active { border-color: rgba(34,197,94,0.28); border-left: 3px solid var(--gr); }
#     .source-tile.off { opacity: .72; }
#     .source-purpose {
#         color: var(--t3);
#         font-size: 9px;
#         line-height: 1.35;
#         text-transform: uppercase;
#         letter-spacing: .7px;
#         font-weight: 900;
#         margin-top: 8px;
#     }
#     .config-tab-note {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px 11px;
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.45;
#         margin: 8px 0 10px 0;
#     }
#     .readiness-score {
#         display: grid;
#         place-items: center;
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         min-height: 138px;
#         margin-top: 10px;
#     }
#     .readiness-score-value {
#         color: var(--t);
#         font-size: 38px;
#         line-height: 1;
#         font-weight: 950;
#     }
#     .readiness-score-label {
#         color: var(--t3);
#         font-size: 9px;
#         text-transform: uppercase;
#         letter-spacing: 1px;
#         font-weight: 900;
#         margin-top: 5px;
#     }
#     .control-grid {
#         display: grid;
#         grid-template-columns: repeat(3, minmax(0, 1fr));
#         gap: 8px;
#         margin-top: 10px;
#     }
#     .control-check {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px;
#         min-height: 88px;
#     }
#     .control-check.ok { border-left: 3px solid var(--gr); }
#     .control-check.warn { border-left: 3px solid var(--am); }
#     .control-check-title {
#         color: var(--t);
#         font-size: 12px;
#         font-weight: 900;
#         margin-bottom: 4px;
#     }
#     .control-check-copy {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.4;
#     }
#     .routing-grid {
#         display: grid;
#         grid-template-columns: repeat(5, minmax(0, 1fr));
#         gap: 8px;
#         margin: 8px 0 14px 0;
#     }
#     .routing-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 11px 12px;
#         min-height: 154px;
#         box-shadow: var(--sh);
#     }
#     .routing-card.active { border-top: 3px solid var(--gr); }
#     .routing-card.off { border-top: 3px solid var(--t3); opacity: .74; }
#     .routing-source {
#         color: var(--t);
#         font-size: 12px;
#         font-weight: 950;
#         margin-bottom: 4px;
#     }
#     .routing-line {
#         border-top: 1px solid var(--bl);
#         padding-top: 7px;
#         margin-top: 7px;
#     }
#     .routing-label {
#         color: var(--t3);
#         font-size: 8px;
#         letter-spacing: .8px;
#         text-transform: uppercase;
#         font-weight: 900;
#         margin-bottom: 2px;
#     }
#     .routing-value {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.35;
#     }
#     .result-command {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         box-shadow: var(--sh);
#         padding: 14px 16px;
#         margin: 8px 0 12px 0;
#         display: grid;
#         grid-template-columns: 1.4fr .9fr;
#         gap: 14px;
#         align-items: stretch;
#     }
#     .result-command-title {
#         color: var(--t);
#         font-size: 20px;
#         line-height: 1.15;
#         font-weight: 950;
#         margin-bottom: 5px;
#     }
#     .result-command-copy {
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.5;
#     }
#     .result-command-meta {
#         display: grid;
#         grid-template-columns: repeat(2, minmax(0, 1fr));
#         gap: 8px;
#     }
#     .result-meta-cell {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px;
#     }
#     .result-meta-label {
#         color: var(--t3);
#         font-size: 8px;
#         font-weight: 900;
#         letter-spacing: .8px;
#         text-transform: uppercase;
#         margin-bottom: 3px;
#     }
#     .result-meta-value {
#         color: var(--t);
#         font-size: 13px;
#         line-height: 1.2;
#         font-weight: 900;
#     }
#     .decision-grid {
#         display: grid;
#         grid-template-columns: repeat(4, minmax(0, 1fr));
#         gap: 8px;
#         margin: 8px 0 12px 0;
#     }
#     .decision-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-left: 3px solid var(--or);
#         border-radius: 8px;
#         padding: 12px;
#         min-height: 132px;
#         box-shadow: var(--sh);
#     }
#     .decision-owner {
#         color: var(--or);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin-bottom: 6px;
#     }
#     .decision-title {
#         color: var(--t);
#         font-size: 13px;
#         font-weight: 950;
#         line-height: 1.25;
#         margin-bottom: 6px;
#     }
#     .decision-body {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.45;
#     }
#     .explain-ladder {
#         display: grid;
#         grid-template-columns: repeat(5, minmax(0, 1fr));
#         gap: 8px;
#         margin: 8px 0 12px 0;
#     }
#     .explain-step {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px;
#         min-height: 98px;
#     }
#     .explain-step-num {
#         color: var(--or);
#         font-size: 9px;
#         letter-spacing: .8px;
#         text-transform: uppercase;
#         font-weight: 900;
#         margin-bottom: 4px;
#     }
#     .explain-step-title {
#         color: var(--t);
#         font-size: 12px;
#         font-weight: 950;
#         margin-bottom: 4px;
#     }
#     .explain-step-copy {
#         color: var(--t2);
#         font-size: 10px;
#         line-height: 1.4;
#     }
#     .audit-command {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         box-shadow: var(--sh);
#         padding: 14px 16px;
#         margin: 8px 0 12px 0;
#         display: grid;
#         grid-template-columns: 1.25fr 1fr;
#         gap: 12px;
#     }
#     .audit-command-title {
#         color: var(--t);
#         font-size: 20px;
#         line-height: 1.15;
#         font-weight: 950;
#         margin-bottom: 5px;
#     }
#     .audit-command-copy {
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.5;
#     }
#     .audit-status-grid {
#         display: grid;
#         grid-template-columns: repeat(2, minmax(0, 1fr));
#         gap: 8px;
#     }
#     .audit-status-cell {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px;
#     }
#     .audit-status-label {
#         color: var(--t3);
#         font-size: 8px;
#         font-weight: 900;
#         letter-spacing: .8px;
#         text-transform: uppercase;
#         margin-bottom: 3px;
#     }
#     .audit-status-value {
#         color: var(--t);
#         font-size: 13px;
#         font-weight: 950;
#         line-height: 1.2;
#     }
#     .audit-lineage {
#         display: grid;
#         grid-template-columns: repeat(5, minmax(0, 1fr));
#         gap: 8px;
#         margin: 8px 0 12px 0;
#     }
#     .audit-lineage-step {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px;
#         min-height: 94px;
#     }
#     .audit-lineage-num {
#         color: var(--or);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: .8px;
#         text-transform: uppercase;
#         margin-bottom: 4px;
#     }
#     .audit-lineage-title {
#         color: var(--t);
#         font-size: 12px;
#         font-weight: 950;
#         margin-bottom: 4px;
#     }
#     .audit-lineage-copy {
#         color: var(--t2);
#         font-size: 10px;
#         line-height: 1.4;
#     }
#     .empty-console {
#         background:
#             linear-gradient(135deg, rgba(244,123,37,0.08), rgba(255,255,255,0.85)),
#             var(--sf);
#         border: 1px dashed var(--obr);
#         border-radius: 18px;
#         padding: 28px;
#         min-height: 210px;
#         display: flex;
#         align-items: center;
#         justify-content: space-between;
#         gap: 20px;
#     }
#     .empty-title {
#         color: var(--t);
#         font-size: 22px;
#         line-height: 1.15;
#         font-weight: 900;
#         letter-spacing: -0.025em;
#         margin-bottom: 8px;
#     }
#     .empty-body {
#         color: var(--t2);
#         font-size: 13px;
#         line-height: 1.65;
#         max-width: 620px;
#     }
#     .workflow {
#         display: grid;
#         grid-template-columns: repeat(5, minmax(0, 1fr));
#         gap: 8px;
#         margin-top: 12px;
#     }
#     .workflow-step {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 10px;
#         font-size: 11px;
#         color: var(--t2);
#         font-weight: 700;
#     }
#     .workflow-step span {
#         display: block;
#         color: var(--or);
#         font-size: 9px;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         font-weight: 900;
#         margin-bottom: 3px;
#     }
#     .metric-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 10px;
#         padding: 16px 18px;
#         min-height: 132px;
#         box-shadow: var(--sh);
#         transition: all var(--tr);
#     }
#     .metric-card:hover {
#         transform: translateY(-1px);
#         box-shadow: var(--shm);
#         border-color: var(--obr);
#     }
#     .metric-label {
#         color: var(--t3);
#         font-size: 0.68rem;
#         text-transform: uppercase;
#         letter-spacing: 0.11em;
#         margin-bottom: 8px;
#         font-weight: 800;
#     }
#     .metric-value {
#         color: var(--t);
#         font-size: 2.05rem;
#         font-weight: 900;
#         line-height: 1;
#         letter-spacing: -0.04em;
#     }
#     .metric-note {
#         color: var(--t2);
#         font-size: 0.82rem;
#         margin-top: 10px;
#         line-height: 1.4;
#     }
#     .pill {
#         display: inline-block;
#         border-radius: 999px;
#         padding: 3px 10px;
#         font-size: 0.72rem;
#         font-weight: 800;
#         border: 1px solid var(--bl);
#         color: var(--t2);
#         background: var(--s2);
#         margin-top: 8px;
#     }
#     .pill-high { color: var(--gr); background: var(--gbg); border-color: rgba(34,197,94,0.2); }
#     .pill-medium { color: var(--am); background: var(--abg); border-color: rgba(245,158,11,0.2); }
#     .pill-low { color: var(--rd); background: var(--rbg); border-color: rgba(239,68,68,0.2); }
#     .brief-box {
#         background: var(--sf);
#         border: 1px solid var(--obr);
#         border-left: 4px solid var(--or);
#         border-radius: 10px;
#         padding: 20px 22px;
#         color: var(--t);
#         line-height: 1.65;
#         box-shadow: 0 0 0 3px var(--og);
#     }
#     .brief-shell {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 12px;
#         box-shadow: var(--sh);
#         overflow: hidden;
#         margin-top: 8px;
#     }
#     .brief-header {
#         background: linear-gradient(180deg, rgba(248,250,252,0.98), rgba(255,255,255,0.98));
#         border-bottom: 1px solid var(--bl);
#         padding: 18px 20px;
#     }
#     .brief-kicker {
#         color: var(--or);
#         font-size: 10px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin-bottom: 6px;
#     }
#     .brief-title {
#         color: var(--t);
#         font-size: 22px;
#         line-height: 1.15;
#         font-weight: 950;
#         letter-spacing: -0.02em;
#         margin-bottom: 8px;
#     }
#     .brief-summary-text {
#         color: var(--t2);
#         font-size: 13px;
#         line-height: 1.55;
#         max-width: 980px;
#     }
#     .brief-meta-strip {
#         display: grid;
#         grid-template-columns: repeat(4, minmax(0, 1fr));
#         gap: 8px;
#         margin-top: 14px;
#     }
#     .brief-meta-chip {
#         background: rgba(255,255,255,0.82);
#         border: 1px solid rgba(226,232,240,0.95);
#         border-radius: 8px;
#         padding: 10px 11px;
#     }
#     .brief-meta-label {
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: .9px;
#         text-transform: uppercase;
#         margin-bottom: 4px;
#     }
#     .brief-meta-value {
#         color: var(--t);
#         font-size: 13px;
#         line-height: 1.25;
#         font-weight: 900;
#     }
#     .brief-section-grid {
#         display: grid;
#         grid-template-columns: repeat(2, minmax(0, 1fr));
#         gap: 12px;
#         padding: 16px;
#     }
#     .brief-section-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 14px 15px;
#         min-height: 150px;
#         box-shadow: var(--sh);
#     }
#     .brief-section-card.primary {
#         grid-column: 1 / -1;
#         min-height: 0;
#         border-color: var(--obr);
#         background: rgba(244,123,37,0.035);
#     }
#     .brief-section-title {
#         color: var(--or);
#         font-size: 11px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin: 0 0 9px 0;
#     }
#     .brief-section-card p,
#     .brief-box p {
#         margin: 0 0 8px 0;
#         font-size: 13px;
#         color: var(--t2);
#         line-height: 1.55;
#     }
#     .brief-list {
#         margin: 0 0 0 18px;
#         padding: 0;
#     }
#     .brief-list li {
#         margin-bottom: 8px;
#         color: var(--t2);
#         font-size: 13px;
#         line-height: 1.45;
#     }
#     .score-explain-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: var(--rl);
#         padding: 14px 16px;
#         box-shadow: var(--sh);
#         margin: 8px 0;
#     }
#     .score-explain-head {
#         display: flex;
#         align-items: flex-start;
#         justify-content: space-between;
#         gap: 12px;
#         border-bottom: 1px solid var(--bl);
#         padding-bottom: 10px;
#         margin-bottom: 10px;
#     }
#     .score-explain-title {
#         color: var(--t);
#         font-size: 13px;
#         font-weight: 900;
#         margin-bottom: 3px;
#     }
#     .score-explain-meta {
#         color: var(--t3);
#         font-size: 10px;
#         font-weight: 800;
#         letter-spacing: .7px;
#         text-transform: uppercase;
#     }
#     .score-number {
#         min-width: 74px;
#         text-align: center;
#         border-radius: 12px;
#         border: 1px solid var(--obr);
#         background: var(--ob);
#         color: var(--or);
#         font-size: 24px;
#         line-height: 1;
#         font-weight: 950;
#         padding: 9px 8px;
#     }
#     .score-number span {
#         display: block;
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: .8px;
#         margin-top: 3px;
#     }
#     .score-explain-label {
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin: 8px 0 3px 0;
#     }
#     .score-explain-text {
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.5;
#     }
#     .brief-grounding {
#         display: grid;
#         grid-template-columns: repeat(4, minmax(0, 1fr));
#         gap: 8px;
#         margin-bottom: 12px;
#     }
#     .small-header {
#         color: var(--t3);
#         font-size: 0.68rem;
#         text-transform: uppercase;
#         letter-spacing: 0.12em;
#         font-weight: 800;
#         margin: 8px 0 12px 0;
#         padding-bottom: 6px;
#         border-bottom: 1px solid var(--bl);
#     }
#     .action-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: var(--r);
#         padding: 12px 14px;
#         box-shadow: var(--sh);
#         min-height: 112px;
#     }
#     .action-label {
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         color: var(--or);
#         margin-bottom: 7px;
#     }
#     .action-title {
#         font-size: 13px;
#         font-weight: 800;
#         color: var(--t);
#         margin-bottom: 5px;
#     }
#     .action-body {
#         font-size: 12px;
#         color: var(--t2);
#         line-height: 1.45;
#     }
#     .note-box {
#         background: rgba(244,123,37,0.04);
#         border-left: 3px solid var(--or);
#         border-radius: 0 8px 8px 0;
#         padding: 9px 12px;
#         font-size: 12px;
#         color: var(--t2);
#         margin: 8px 0;
#     }
#     .run-monitor {
#         background: var(--sf);
#         border: 1px solid var(--obr);
#         border-radius: 12px;
#         padding: 14px 16px;
#         box-shadow: 0 0 0 3px var(--og);
#         margin: 8px 0 16px 0;
#     }
#     .run-monitor-head {
#         display: flex;
#         align-items: center;
#         justify-content: space-between;
#         gap: 12px;
#         margin-bottom: 10px;
#     }
#     .run-monitor-title {
#         color: var(--t);
#         font-size: 13px;
#         font-weight: 900;
#     }
#     .run-monitor-sub {
#         color: var(--t3);
#         font-size: 10px;
#         font-weight: 800;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#     }
#     .run-progress-track {
#         height: 7px;
#         background: var(--s3);
#         border-radius: 999px;
#         overflow: hidden;
#         margin: 8px 0 12px 0;
#     }
#     .run-progress-fill {
#         height: 100%;
#         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
#         border-radius: 999px;
#         transition: width var(--tr);
#     }
#     .run-status-grid {
#         display: grid;
#         grid-template-columns: repeat(6, minmax(0, 1fr));
#         gap: 8px;
#     }
#     .run-status-card {
#         background: var(--s2);
#         border: 1px solid var(--bl);
#         border-radius: 12px;
#         padding: 10px;
#         min-height: 72px;
#     }
#     .run-status-name {
#         font-size: 11px;
#         font-weight: 900;
#         color: var(--t);
#         margin-bottom: 5px;
#     }
#     .run-status-detail {
#         font-size: 10px;
#         color: var(--t2);
#         line-height: 1.35;
#     }
#     .status-badge {
#         display: inline-flex;
#         align-items: center;
#         gap: 4px;
#         padding: 2px 7px;
#         border-radius: 999px;
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: .6px;
#         text-transform: uppercase;
#         margin-bottom: 6px;
#     }
#     .status-running { background: var(--ob); color: var(--or); border: 1px solid var(--obr); }
#     .status-success { background: var(--gbg); color: var(--gr); border: 1px solid rgba(34,197,94,.2); }
#     .status-failed { background: var(--rbg); color: var(--rd); border: 1px solid rgba(239,68,68,.2); }
#     .status-skipped { background: rgba(100,116,139,.08); color: var(--t2); border: 1px solid var(--bl); }
#     .status-queued { background: var(--s3); color: var(--t3); border: 1px solid var(--bl); }
#     .audit-banner {
#         background: linear-gradient(135deg, rgba(34,197,94,0.10), rgba(255,255,255,0.92));
#         border: 1px solid rgba(34,197,94,0.22);
#         border-left: 4px solid var(--gr);
#         border-radius: var(--rl);
#         padding: 14px 16px;
#         color: var(--t);
#         margin: 8px 0 14px 0;
#         box-shadow: var(--sh);
#     }
#     .audit-banner.warn {
#         background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(255,255,255,0.92));
#         border-color: rgba(245,158,11,0.24);
#         border-left-color: var(--am);
#     }
#     .audit-title {
#         font-size: 13px;
#         font-weight: 900;
#         margin-bottom: 5px;
#     }
#     .audit-body {
#         font-size: 12px;
#         color: var(--t2);
#         line-height: 1.5;
#     }
#     .audit-grid {
#         display: grid;
#         grid-template-columns: repeat(4, minmax(0, 1fr));
#         gap: 10px;
#         margin: 10px 0 14px 0;
#     }
#     .audit-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 10px;
#         padding: 13px 14px;
#         min-height: 92px;
#         box-shadow: var(--sh);
#     }
#     .audit-label {
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin-bottom: 7px;
#     }
#     .audit-value {
#         color: var(--t);
#         font-size: 18px;
#         line-height: 1.1;
#         font-weight: 900;
#     }
#     .audit-note {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.35;
#         margin-top: 7px;
#     }
#     div[role="radiogroup"] {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 14px;
#         padding: 5px;
#         display: inline-flex;
#         gap: 4px;
#         box-shadow: var(--sh);
#         margin: 4px 0 12px 0;
#     }
#     div[role="radiogroup"] label {
#         border-radius: 10px !important;
#         padding: 6px 13px !important;
#         min-height: 34px !important;
#         transition: all var(--tr);
#     }
#     div[role="radiogroup"] label:has(input:checked) {
#         background: var(--ob) !important;
#         border: 1px solid var(--obr) !important;
#         color: var(--or) !important;
#         font-weight: 850 !important;
#     }
#     div[role="radiogroup"] label span {
#         font-size: 12px !important;
#         font-weight: 750 !important;
#     }
#     div[data-testid="stButton"] button {
#         background: var(--or);
#         color: #fff;
#         border: none;
#         border-radius: var(--r);
#         font-weight: 800;
#         box-shadow: 0 2px 8px rgba(244,123,37,0.2);
#         transition: all var(--tr);
#     }
#     div[data-testid="stButton"] button:hover {
#         background: var(--odk);
#         color: #fff;
#         border: none;
#         transform: translateY(-1px);
#     }
#     .stTabs [data-baseweb="tab-list"] {
#         gap: 12px;
#         border-bottom: 1px solid var(--bl);
#         padding: 0 0 8px 0;
#         margin: 6px 0 14px 0;
#     }
#     .stTabs [data-baseweb="tab"] {
#         border-radius: 8px;
#         color: var(--t2);
#         font-weight: 700;
#         min-height: 42px;
#         padding: 9px 18px !important;
#         border: 1px solid transparent;
#         background: var(--s2);
#     }
#     .stTabs [aria-selected="true"] {
#         background: var(--ob);
#         color: var(--or) !important;
#         border: 1px solid var(--obr);
#         box-shadow: inset 0 -2px 0 var(--or);
#     }
#     .stTabs [data-baseweb="tab"] p {
#         font-size: 13px;
#         font-weight: 850;
#         white-space: nowrap;
#     }
#     .stSelectbox>div>div, .stTextInput>div>div, .stTextArea>div>div {
#         background: var(--s2) !important;
#         border: 1px solid var(--bl) !important;
#         border-radius: var(--r) !important;
#         font-size: 13px !important;
#         color: var(--t) !important;
#     }
#     [data-testid="stExpander"] {
#         background: var(--sf);
#         border: 1px solid var(--bl) !important;
#         border-radius: 10px !important;
#         box-shadow: var(--sh);
#     }
#     .glossary-grid {
#         display: grid;
#         grid-template-columns: repeat(3, minmax(0, 1fr));
#         gap: 10px;
#         margin: 10px 0 4px 0;
#     }
#     .glossary-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 12px;
#         min-height: 94px;
#     }
#     .glossary-term {
#         color: var(--t);
#         font-size: 12px;
#         font-weight: 900;
#         margin-bottom: 5px;
#     }
#     .glossary-def {
#         color: var(--t2);
#         font-size: 11px;
#         line-height: 1.45;
#     }
#     .enterprise-band {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 10px;
#         padding: 14px 16px;
#         margin: 10px 0 14px 0;
#         box-shadow: var(--sh);
#     }
#     .enterprise-band-title {
#         color: var(--t);
#         font-size: 14px;
#         font-weight: 900;
#         margin-bottom: 4px;
#     }
#     .enterprise-band-copy {
#         color: var(--t2);
#         font-size: 12px;
#         line-height: 1.5;
#     }
#     .trust-grid {
#         display: grid;
#         grid-template-columns: repeat(5, minmax(0, 1fr));
#         gap: 8px;
#         margin: 10px 0 14px 0;
#     }
#     .trust-card {
#         background: var(--sf);
#         border: 1px solid var(--bl);
#         border-radius: 8px;
#         padding: 11px 12px;
#         min-height: 86px;
#     }
#     .trust-card.good { border-left: 3px solid var(--gr); }
#     .trust-card.warn { border-left: 3px solid var(--am); }
#     .trust-label {
#         color: var(--t3);
#         font-size: 9px;
#         font-weight: 900;
#         letter-spacing: 1px;
#         text-transform: uppercase;
#         margin-bottom: 5px;
#     }
#     .trust-value {
#         color: var(--t);
#         font-size: 17px;
#         font-weight: 950;
#         line-height: 1.1;
#     }
#     .trust-note {
#         color: var(--t2);
#         font-size: 10px;
#         line-height: 1.35;
#         margin-top: 6px;
#     }
#     @media (max-width: 1100px) {
#         .run-status-grid, .brief-meta-strip, .glossary-grid, .trust-grid, .source-grid, .config-shell, .routing-grid, .control-grid, .result-command, .decision-grid, .explain-ladder, .audit-command, .audit-lineage { grid-template-columns: repeat(2, minmax(0, 1fr)); }
#         .brief-section-grid { grid-template-columns: 1fr; }
#     }
#     @media (max-width: 720px) {
#         .run-status-grid, .brief-meta-strip, .glossary-grid, .trust-grid, .source-grid, .config-shell, .routing-grid, .control-grid, .result-command, .decision-grid, .explain-ladder, .audit-command, .audit-status-grid, .audit-lineage, .workflow { grid-template-columns: 1fr; }
#         .hero-title { font-size: 28px; }
#     }
#     ::-webkit-scrollbar { width: 4px; height: 4px; }
#     ::-webkit-scrollbar-track { background: transparent; }
#     ::-webkit-scrollbar-thumb { background: var(--bl); border-radius: 2px; }
#     </style>
#     """,
#     unsafe_allow_html=True,
# )


# def utc_now() -> str:
#     return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# def safe_request(
#     url: str,
#     *,
#     method: str = "GET",
#     headers: Optional[Dict[str, str]] = None,
#     params: Optional[Dict[str, Any]] = None,
#     json_body: Optional[Dict[str, Any]] = None,
#     timeout: int = 30,
# ) -> Tuple[bool, Any, str]:
#     try:
#         if method.upper() == "POST":
#             response = requests.post(url, headers=headers, params=params, json=json_body, timeout=timeout)
#         else:
#             response = requests.get(url, headers=headers, params=params, timeout=timeout)
#         response.raise_for_status()
#         try:
#             return True, response.json(), "success"
#         except ValueError:
#             return True, response.text, "success"
#     except requests.RequestException as exc:
#         return False, None, str(exc)


# def friendly_nvidia_failure(raw_error: str) -> Tuple[str, str]:
#     error_text = str(raw_error or "").strip()
#     lower = error_text.lower()
#     if "timed out" in lower or "read timeout" in lower:
#         return (
#             "fallback (NVIDIA timeout)",
#             "NVIDIA did not respond within the configured timeout, so a local rule-based brief was generated from the collected signal rows.",
#         )
#     if "401" in lower or "unauthorized" in lower:
#         return (
#             "fallback (NVIDIA authentication)",
#             "NVIDIA authentication failed, so a local rule-based brief was generated from the collected signal rows.",
#         )
#     if "429" in lower or "rate limit" in lower:
#         return (
#             "fallback (NVIDIA rate limit)",
#             "NVIDIA rate limiting prevented the AI brief, so a local rule-based brief was generated from the collected signal rows.",
#         )
#     return (
#         "fallback (NVIDIA unavailable)",
#         "The NVIDIA AI brief was unavailable for this run, so a local rule-based brief was generated from the collected signal rows.",
#     )


# def confidence_class(confidence: str) -> str:
#     lookup = {"High": "pill-high", "Medium": "pill-medium", "Low": "pill-low"}
#     return lookup.get(confidence, "")


# def risk_band(score: float) -> str:
#     if score >= 8:
#         return "High"
#     if score >= 5:
#         return "Medium"
#     return "Low"


# def dollar_tree_context_for_signal(row: Dict[str, Any]) -> Dict[str, str]:
#     area = str(row.get("signal_area", "")).lower()
#     name = str(row.get("signal_name", "")).lower()
#     source = str(row.get("source", "")).lower()
#     raw_category = str(row.get("affected_category", "") or row.get("raw_reference", "")).lower()
#     score = float(row.get("risk_score", 0) or 0)
#     priority = "High" if score >= 8 else "Medium" if score >= 5 else "Monitor"

#     context = {
#         "dollar_tree_category": "Total store / value basket",
#         "enterprise_kpi": "Forecast Adjustment Priority",
#         "planning_owner": "Demand Planning",
#         "demand_direction": "Unknown until matched to POS",
#         "forecast_feature": str(row.get("signal_name", "external_signal")),
#         "dollar_tree_relevance": "External context signal; validate against internal sales, inventory, promotion, and store data.",
#         "impact_hypothesis": "Use as a candidate external regressor, not as a standalone demand forecast.",
#         "action_priority": priority,
#         "evidence_grade": "Live public API" if source else "Unknown",
#         "internal_data_needed": "POS sales, category hierarchy, inventory, store/DC mapping, promotion calendar",
#     }

#     if "recall" in area or "fda" in source or any(term in raw_category for term in ["snack", "candy", "beverage", "food"]):
#         context.update(
#             {
#                 "dollar_tree_category": "Snacks, candy, beverages, and consumables",
#                 "enterprise_kpi": "Safety And Compliance Risk",
#                 "planning_owner": "Compliance + Category Buyer",
#                 "demand_direction": "Downside risk / substitution demand",
#                 "forecast_feature": "recall_exposure_score",
#                 "dollar_tree_relevance": "Retailers may carry high-velocity consumables where recalls can require UPC matching, vendor review, and store withdrawal decisions.",
#                 "impact_hypothesis": "If affected UPCs overlap internal SKU files, demand may shift away from recalled items and toward substitutes.",
#                 "internal_data_needed": "SKU master, UPC list, vendor file, on-hand inventory, store distribution",
#             }
#         )
#     elif "weather" in area or "noaa" in source:
#         context.update(
#             {
#                 "dollar_tree_category": "Emergency demand: batteries, water, cleaning, food, household essentials",
#                 "enterprise_kpi": "Supply Chain Disruption Risk",
#                 "planning_owner": "Supply Chain + Demand Planning",
#                 "demand_direction": "Short-term uplift for essentials; disruption risk for replenishment",
#                 "forecast_feature": "state_weather_disruption_score",
#                 "dollar_tree_relevance": "Weather alerts can affect store traffic, replenishment routes, staffing, and emergency-demand baskets in exposed states.",
#                 "impact_hypothesis": "Severe alerts can lift emergency categories while increasing DC-to-store execution risk.",
#                 "internal_data_needed": "Store locations, DC routes, state/category sales, inventory by store",
#             }
#         )
#     elif "headline cpi" in name or ("inflation" in area and "category" not in area):
#         context.update(
#             {
#                 "dollar_tree_category": "Total value basket",
#                 "enterprise_kpi": "Value Basket Pressure",
#                 "planning_owner": "Merchandising Strategy + Demand Planning",
#                 "demand_direction": "Trade-down support for essentials; pressure on discretionary baskets",
#                 "forecast_feature": "headline_cpi_value_pressure",
#                 "dollar_tree_relevance": "Value-oriented retail demand can be sensitive to consumer price pressure and trade-down behavior.",
#                 "impact_hypothesis": "Higher inflation can increase value-seeking traffic while changing mix toward essentials.",
#                 "internal_data_needed": "Traffic, basket mix, category sales, price/promotion calendar",
#             }
#         )
#     elif "gasoline" in name or "gasoline" in raw_category:
#         context.update(
#             {
#                 "dollar_tree_category": "Traffic-sensitive baskets and discretionary add-ons",
#                 "enterprise_kpi": "Consumer Wallet Pressure",
#                 "planning_owner": "Demand Planning + Store Operations",
#                 "demand_direction": "Possible traffic pressure; essential-item substitution risk",
#                 "forecast_feature": "gasoline_wallet_pressure_score",
#                 "dollar_tree_relevance": "Fuel inflation can reduce discretionary spend and change trip patterns for value retailers.",
#                 "impact_hypothesis": "Rising gas prices may shift basket composition and store visit frequency by region.",
#                 "internal_data_needed": "Store traffic, basket value, region/store sales, trip frequency",
#             }
#         )
#     elif "food at home" in name:
#         context.update(
#             {
#                 "dollar_tree_category": "Food, snacks, candy, beverages",
#                 "enterprise_kpi": "Consumables Demand Pressure",
#                 "planning_owner": "Consumables Category Manager",
#                 "demand_direction": "Potential uplift in value consumables",
#                 "forecast_feature": "food_at_home_cpi_pressure",
#                 "dollar_tree_relevance": "Food inflation can push shoppers toward value-format consumables and smaller pack sizes.",
#                 "impact_hypothesis": "Higher food CPI may increase demand for value snacks, pantry, and beverage alternatives.",
#                 "internal_data_needed": "Consumables sales, price ladder, pack size, inventory and promotions",
#             }
#         )
#     elif "household" in name:
#         context.update(
#             {
#                 "dollar_tree_category": "Household supplies, cleaning, home basics",
#                 "enterprise_kpi": "Household Essentials Pressure",
#                 "planning_owner": "Household Category Manager",
#                 "demand_direction": "Potential mix shift toward value household items",
#                 "forecast_feature": "household_cpi_pressure",
#                 "dollar_tree_relevance": "Household inflation can influence trade-down into value-oriented home and cleaning categories.",
#                 "impact_hypothesis": "Higher household CPI may support value demand but pressure margin and vendor costs.",
#                 "internal_data_needed": "Household category sales, costs, vendor data, pricing actions",
#             }
#         )
#     elif "search" in area or "apify" in source:
#         context.update(
#             {
#                 "dollar_tree_category": "Seasonal, local demand, and store-intent categories",
#                 "enterprise_kpi": "Demand Interest Spike",
#                 "planning_owner": "Demand Planning + Digital/Marketing",
#                 "demand_direction": "Potential regional demand uplift",
#                 "forecast_feature": "google_trends_interest_score",
#                 "dollar_tree_relevance": "Search interest can reveal early regional intent around deals, coupons, seasonal products, or store visits.",
#                 "impact_hypothesis": "Rising search interest may precede demand spikes, but must be checked against store/category sales.",
#                 "internal_data_needed": "Regional sales, promotion calendar, store traffic, search keywords by category",
#             }
#         )
#     elif "news" in area or "gnews" in source:
#         context.update(
#             {
#                 "dollar_tree_category": "Enterprise market events and competitor pressure",
#                 "enterprise_kpi": "Market Event Risk",
#                 "planning_owner": "Merchandising Leadership + Category Manager",
#                 "demand_direction": "Depends on event type and affected category",
#                 "forecast_feature": "retail_news_event_score",
#                 "dollar_tree_relevance": "Retail news can surface competitor moves, price pressure, closures, recalls, and market events relevant to retail planning.",
#                 "impact_hypothesis": "Use high-risk articles as explainers for forecast variance and buyer review.",
#                 "internal_data_needed": "Affected category sales, competitor set, promotion calendar, store overlap",
#             }
#         )
#     return context


# def enrich_feature_rows_for_retailer(feature_df: pd.DataFrame, retailer_name: str) -> pd.DataFrame:
#     if feature_df.empty:
#         return feature_df
#     enriched = feature_df.copy()
#     contexts = [dollar_tree_context_for_signal(row.to_dict()) for _, row in enriched.iterrows()]
#     context_df = pd.DataFrame(contexts)
#     for col in context_df.columns:
#         enriched[col] = context_df[col].values
#     if "Retailer" not in retailer_name:
#         enriched["dollar_tree_relevance"] = enriched["dollar_tree_relevance"].str.replace("Retailer", retailer_name or "the retailer", regex=False)
#     return enriched


# def source_confidence(publisher: str) -> str:
#     high = ["reuters", "associated press", "ap news", "bloomberg", "sec", "pr newswire"]
#     medium = ["cnbc", "yahoo", "nasdaq", "marketwatch", "forbes", "investing.com", "retail dive"]
#     p = (publisher or "").lower()
#     if any(name in p for name in high):
#         return "High"
#     if any(name in p for name in medium):
#         return "Medium"
#     return "Medium" if publisher else "Low"


# def count_raw_records(raw_payload: Any, items: Optional[List[Dict[str, Any]]] = None) -> int:
#     if items:
#         return len(items)
#     if raw_payload is None:
#         return 0
#     if isinstance(raw_payload, list):
#         return len(raw_payload)
#     if isinstance(raw_payload, dict):
#         if isinstance(raw_payload.get("features"), list):
#             return len(raw_payload["features"])
#         if isinstance(raw_payload.get("results"), list):
#             return len(raw_payload["results"])
#         series = raw_payload.get("Results", {}).get("series") if isinstance(raw_payload.get("Results"), dict) else None
#         if isinstance(series, list):
#             return sum(len(s.get("data", [])) for s in series if isinstance(s, dict))
#         nested_counts = [
#             count_raw_records(value)
#             for value in raw_payload.values()
#             if isinstance(value, (dict, list))
#         ]
#         return sum(nested_counts) if nested_counts else 1
#     return 1


# def request_summary(source_key: str, run_config: Dict[str, Any]) -> str:
#     if source_key == "gnews":
#         keywords = run_config.get("news_keywords", [])
#         return (
#             f"{len(keywords)} keyword(s), country={run_config.get('country', '')}, "
#             f"language={run_config.get('language', '')}, period={run_config.get('gnews_period', '')}, "
#             f"max_results={run_config.get('max_news', '')}"
#         )
#     if source_key == "bls":
#         return "series=" + ", ".join(BLS_CPI_SERIES.values())
#     if source_key == "fda":
#         return f"search={run_config.get('fda_query', '')}; limit={run_config.get('fda_limit', '')}"
#     if source_key == "weather":
#         return f"area={run_config.get('weather_area', '')}; limit={run_config.get('weather_limit', '')}; active NOAA alerts"
#     if source_key == "apify":
#         return (
#             f"enabled={run_config.get('use_apify', False)}, token_present={run_config.get('apify_token_present', False)}, "
#             f"run_mode={run_config.get('apify_run_mode', 'Skip Apify')}, confirmed={run_config.get('apify_live_confirm', False)}, "
#             f"geo={run_config.get('apify_geo', '')}, time_range={run_config.get('apify_time_range', APIFY_SAFE_TIME_RANGE)}, "
#             f"max_keywords={run_config.get('apify_max_keywords', APIFY_HARD_KEYWORD_LIMIT)}"
#         )
#     return ""


# def build_collector_evidence(results: Dict[str, Dict[str, Any]], run_config: Dict[str, Any]) -> List[Dict[str, Any]]:
#     records = []
#     enabled_sources = run_config.get("enabled_sources", {})
#     for source_key in SOURCE_ORDER:
#         result = results.get(source_key, {})
#         status = result.get("status", "disabled" if not enabled_sources.get(source_key, False) else "not_run")
#         normalized_rows = len(result.get("rows", []) or [])
#         raw_records = count_raw_records(result.get("raw"), result.get("items"))
#         live_request = status in {"success", "failed", "empty"} or (source_key == "apify" and result.get("raw") is not None)
#         mock_used = bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
#         records.append(
#             {
#                 "source": SOURCE_LABELS[source_key],
#                 "status": status,
#                 "endpoint_or_actor": SOURCE_ENDPOINTS[source_key],
#                 "request_scope": request_summary(source_key, run_config),
#                 "raw_records_pulled": raw_records,
#                 "normalized_feature_rows": normalized_rows,
#                 "live_request_made": "Yes" if live_request else "No",
#                 "used_in_llm_payload": "Yes" if normalized_rows > 0 else "No",
#                 "mock_data_used": "Yes" if mock_used else "No",
#                 "analysis_method": ANALYSIS_METHODS[source_key],
#                 "error_or_note": result.get("error", "") or "",
#             }
#         )
#     return records


# def build_llm_payload(feature_df: pd.DataFrame, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
#     return {
#         "features": feature_df.to_dict(orient="records")[:NVIDIA_MAX_FEATURE_ROWS],
#         "articles": articles[:NVIDIA_MAX_ARTICLES],
#     }


# def llm_system_prompt() -> str:
#     return (
#         "You are a retail market intelligence analyst. Ground your answer only in the supplied signals. "
#         "Write concise executive guidance for buyers, category managers, demand planners, and supply-chain teams."
#     )


# def llm_user_prompt(retailer: str, region: str, payload: Dict[str, Any]) -> str:
#     return f"""
#     Retailer: {retailer}
#     Region: {region}

#     Signal payload:
#     {json.dumps(payload, indent=2, default=str)[:9000]}

#     Produce:
#     1. Executive summary in 2-3 sentences
#     2. Top 3 insights, and for each one cite signal_area, source, risk_score, score_reason, dollar_tree_category, planning_owner, and demand_direction
#     3. Retail category impact, clearly stating how the signal can become a forecast feature
#     4. Recommended buyer/category/demand-planning/supply-chain/compliance actions
#     5. Confidence and limitations

#     Rules:
#     - Ground every claim only in the supplied payload.
#     - Do not invent internal sales, POS, inventory, margin, or category-performance facts.
#     - If a signal is missing, say it is missing instead of estimating it.
#     - Explain why each score matters; do not only repeat the number.
#     - Use retailer-neutral business language when the payload includes category, KPI, planning owner, impact hypothesis, and internal-data requirements.
#     """


# def payload_hash(payload: Dict[str, Any]) -> str:
#     return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# def build_base_llm_audit(
#     feature_df: pd.DataFrame,
#     articles: List[Dict[str, Any]],
#     retailer: str,
#     region: str,
#     model: str,
# ) -> Dict[str, Any]:
#     payload = build_llm_payload(feature_df, articles)
#     return {
#         "provider": "NVIDIA",
#         "model": model,
#         "sent_to_llm": False,
#         "brief_source": "not_generated",
#         "fallback_used": False,
#         "fallback_reason": "",
#         "mock_data_used": False,
#         "feature_rows_available": int(len(feature_df)),
#         "feature_rows_sent": int(len(payload["features"])),
#         "articles_available": int(len(articles)),
#         "articles_sent": int(len(payload["articles"])),
#         "payload_hash_sha256": payload_hash(payload),
#         "system_prompt": llm_system_prompt(),
#         "user_prompt": llm_user_prompt(retailer, region, payload),
#         "payload": payload,
#         "analysis_contract": "The brief must be grounded only in the collected feature rows and article records shown in this audit view.",
#         "brief_generation_strategy": f"Payload capped at {NVIDIA_MAX_FEATURE_ROWS} feature rows and {NVIDIA_MAX_ARTICLES} articles; NVIDIA retries use timeouts {NVIDIA_TIMEOUT_SECONDS} seconds with lightweight backoff.",
#     }


# def any_mock_used(results: Dict[str, Dict[str, Any]], llm_audit: Dict[str, Any]) -> bool:
#     collector_mock = any(
#         bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
#         for result in results.values()
#     )
#     return collector_mock or bool(llm_audit.get("mock_data_used", False))


# def render_audit_banner(mock_used: bool, brief_source: str) -> None:
#     banner_class = "audit-banner warn" if mock_used else "audit-banner"
#     title = "Mock Data Detected" if mock_used else "No Mock Data Used In This Run"
#     body = (
#         "At least one collector or analysis step is marked as using mock data. Review the evidence table below."
#         if mock_used
#         else f"Every feature row shown below comes from the collector outputs for this run. Brief source: {brief_source}."
#     )
#     st.markdown(
#         f"<div class='{banner_class}'><div class='audit-title'>{escape(title)}</div><div class='audit-body'>{escape(body)}</div></div>",
#         unsafe_allow_html=True,
#     )


# def render_audit_card(label: str, value: str, note: str) -> None:
#     st.markdown(
#         "<div class='audit-card'>"
#         f"<div class='audit-label'>{escape(label)}</div>"
#         f"<div class='audit-value'>{escape(value)}</div>"
#         f"<div class='audit-note'>{escape(note)}</div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )


# def validate_nvidia(api_key: str, model: str) -> Tuple[bool, str]:
#     if not api_key:
#         return False, "NVIDIA key not provided. Brief generation will use a local fallback."
#     prompt = "Return exactly: connected"
#     ok, data, msg = safe_request(
#         NVIDIA_CHAT_URL,
#         method="POST",
#         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
#         json_body={
#             "model": model,
#             "messages": [{"role": "user", "content": prompt}],
#             "temperature": 0,
#             "max_tokens": 8,
#         },
#         timeout=30,
#     )
#     if not ok:
#         return False, msg
#     content = data.get("choices", [{}])[0].get("message", {}).get("content")
#     content_text = str(content or "").strip()
#     return True, f"Connected. Model responded: {content_text or 'ok'}"


# def validate_apify(token: str) -> Tuple[bool, str]:
#     if not token:
#         return False, "Apify token not provided. Apify collectors will be skipped."
#     try:
#         from apify_client import ApifyClient
#     except ImportError:
#         return False, "apify-client is not installed. Install requirements before running Apify collectors."
#     try:
#         client = ApifyClient(token)
#         user = client.user().get()
#         username = user.get("username") or user.get("email") or "Apify user"
#         return True, f"Connected as {username}."
#     except Exception as exc:  # pragma: no cover - depends on live Apify service
#         return False, str(exc)


# def build_cpi_signal(data: Dict[str, Any], label: str, series_id: str, retailer: str) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
#     series = data.get("Results", {}).get("series", [])
#     rows = []
#     for item in (series[0].get("data", []) if series else [])[:24]:
#         period = item.get("period", "")
#         if period == "M13":
#             continue
#         try:
#             cpi_value = float(item["value"])
#         except (TypeError, ValueError, KeyError):
#             continue
#         rows.append(
#             {
#                 "category": label,
#                 "series_id": series_id,
#                 "year": int(item["year"]),
#                 "period": period,
#                 "month": item.get("periodName", ""),
#                 "cpi_value": cpi_value,
#             }
#         )
#     df = pd.DataFrame(rows)
#     if df.empty:
#         return None, df
#     df = df.sort_values(["year", "period"]).reset_index(drop=True)
#     df["cpi_mom_change_pct"] = df["cpi_value"].pct_change() * 100
#     df["cpi_yoy_change_pct"] = df["cpi_value"].pct_change(12) * 100
#     latest = df.iloc[-1].to_dict()
#     mom = latest.get("cpi_mom_change_pct")
#     yoy = latest.get("cpi_yoy_change_pct")
#     score = 4.0
#     if pd.notna(mom):
#         score = min(10.0, max(1.0, 4.0 + float(mom) * 5.0))
#     if pd.notna(yoy) and yoy > 4:
#         score = min(10.0, score + 1.0)
#     mom_label = "unavailable" if pd.isna(mom) else f"{float(mom):.2f}% MoM"
#     yoy_label = "unavailable" if pd.isna(yoy) else f"{float(yoy):.2f}% YoY"
#     signal_name = "inflation_pressure_score" if label == "Headline CPI" else f"{label.lower().replace(' ', '_')}_cpi_pressure_score"
#     signal = {
#         "date": f"{int(latest['year'])}-{str(latest['period']).replace('M', '').zfill(2)}",
#         "retailer": retailer,
#         "region": "US",
#         "region_scope": "national",
#         "source": "BLS CPI",
#         "signal_area": "Inflation" if label == "Headline CPI" else "Category CPI",
#         "signal_name": signal_name,
#         "signal_value": round(float(score), 2),
#         "risk_score": round(float(score), 2),
#         "confidence": "High",
#         "score_reason": f"{label} CPI latest value {latest['cpi_value']}; change is {mom_label} and {yoy_label}. Score rises with monthly inflation pressure and elevated YoY inflation.",
#         "business_impact": f"{label} inflation can affect price sensitivity, category demand, and basket mix.",
#         "recommended_action": "Use category CPI as an external regressor and validate against internal category sales.",
#         "raw_reference": f"{label}: CPI {latest['cpi_value']}",
#     }
#     return signal, df


# def collect_bls_cpi(bls_key: str = "", retailer: str = "Retailer") -> Dict[str, Any]:
#     payload: Dict[str, Any] = {"seriesid": list(BLS_CPI_SERIES.values())}
#     if bls_key:
#         payload["registrationkey"] = bls_key
#     signals = []
#     tables = []
#     raw = {}
#     errors = []
#     ok, data, msg = safe_request(
#         "https://api.bls.gov/publicAPI/v2/timeseries/data/",
#         method="POST",
#         headers={"Content-Type": "application/json"},
#         json_body=payload,
#         timeout=30,
#     )
#     if not ok:
#         return {"status": "failed", "source": "BLS CPI", "error": msg, "raw": None, "rows": []}
#     if data.get("status") != "REQUEST_SUCCEEDED":
#         return {
#             "status": "failed",
#             "source": "BLS CPI",
#             "error": "; ".join(data.get("message", [])) or "BLS request was not processed.",
#             "raw": data,
#             "rows": [],
#         }
#     series_by_id = {
#         series.get("seriesID"): series
#         for series in data.get("Results", {}).get("series", [])
#     }
#     for label, series_id in BLS_CPI_SERIES.items():
#         series_payload = {"Results": {"series": [series_by_id.get(series_id, {})]}}
#         raw[label] = series_payload
#         signal, df = build_cpi_signal(series_payload, label, series_id, retailer)
#         if signal:
#             signals.append(signal)
#         if not df.empty:
#             tables.append(df)
#     if not signals:
#         return {"status": "failed", "source": "BLS CPI", "error": "; ".join(errors) or "No CPI rows returned.", "raw": raw, "rows": []}
#     table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
#     return {"status": "success", "source": "BLS CPI", "error": "; ".join(errors), "raw": raw, "rows": signals, "table": table}


# def classify_recall(reason: str) -> Tuple[str, float]:
#     text = (reason or "").lower()
#     if any(word in text for word in ["lead", "heavy metal", "salmonella", "listeria", "e. coli", "contamination"]):
#         return "high_safety_risk", 8.0
#     if any(word in text for word in ["undeclared", "allergen", "milk", "peanut", "soy", "tree nut"]):
#         return "allergen_risk", 6.5
#     if any(word in text for word in ["mislabel", "label"]):
#         return "labeling_risk", 4.5
#     return "general_recall_risk", 5.0


# def extract_upcs(text: str) -> List[str]:
#     candidates = re.findall(r"(?:UPC(?:\s*Code)?[:\s]*)?(\d(?:[\s-]?\d){7,13})", text or "", flags=re.IGNORECASE)
#     cleaned = []
#     for candidate in candidates:
#         digits = re.sub(r"\D", "", candidate)
#         if 8 <= len(digits) <= 14 and digits not in cleaned:
#             cleaned.append(digits)
#     return cleaned


# def adjust_recall_score(base_score: float, classification: str, status: str) -> float:
#     score = base_score
#     class_text = (classification or "").lower()
#     status_text = (status or "").lower()
#     if "class i" in class_text:
#         score += 1.5
#     elif "class ii" in class_text:
#         score += 0.8
#     if "ongoing" in status_text:
#         score += 1.0
#     elif "terminated" in status_text:
#         score -= 1.0
#     return round(min(10.0, max(1.0, score)), 2)


# def collect_fda_recalls(query: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
#     params = {"search": query, "limit": limit}
#     ok, data, msg = safe_request("https://api.fda.gov/food/enforcement.json", params=params, timeout=30)
#     if not ok:
#         return {"status": "failed", "source": "openFDA", "error": msg, "raw": None, "rows": [], "items": []}
#     results = data.get("results", [])
#     rows = []
#     items = []
#     for item in results:
#         risk_type, base_score = classify_recall(item.get("reason_for_recall", ""))
#         product = item.get("product_description", "Unknown product")
#         state = item.get("state", "US")
#         classification = item.get("classification", "")
#         status = item.get("status", "")
#         score = adjust_recall_score(base_score, classification, status)
#         upcs = extract_upcs(f"{product} {item.get('code_info', '')}")
#         items.append(
#             {
#                 "product": product,
#                 "reason": item.get("reason_for_recall", ""),
#                 "state": state,
#                 "classification": classification,
#                 "status": status,
#                 "recall_date": item.get("recall_initiation_date", ""),
#                 "distribution_pattern": item.get("distribution_pattern", ""),
#                 "recalling_firm": item.get("recalling_firm", ""),
#                 "upcs": ", ".join(upcs) if upcs else "",
#                 "sku_match_status": "unknown",
#                 "risk_type": risk_type,
#                 "risk_score": score,
#             }
#         )
#     aggregate_score = max([x["risk_score"] for x in items], default=1.0)
#     ongoing_count = sum(1 for x in items if str(x.get("status", "")).lower() == "ongoing")
#     class_i_count = sum(1 for x in items if "class i" in str(x.get("classification", "")).lower())
#     states = sorted({str(x.get("state", "")).strip() for x in items if str(x.get("state", "")).strip()})
#     upc_count = sum(1 for x in items if x.get("upcs"))
#     top_risk_type = max(items, key=lambda x: x["risk_score"]).get("risk_type", "none") if items else "none"
#     signal = {
#         "date": utc_now()[:10],
#         "retailer": retailer,
#         "region": "US",
#         "region_scope": "national_with_state_records",
#         "source": "openFDA",
#         "signal_area": "Product Recalls",
#         "signal_name": "recall_risk_score",
#         "signal_value": len(items),
#         "risk_score": round(aggregate_score, 2),
#         "confidence": "High",
#         "score_reason": f"Score uses highest adjusted recall severity. Inputs: {len(items)} records, {ongoing_count} ongoing, {class_i_count} Class I, {upc_count} records with UPCs, top risk type {top_risk_type}.",
#         "affected_states": ", ".join(states[:8]) if states else "Unknown",
#         "ongoing_count": ongoing_count,
#         "class_i_count": class_i_count,
#         "upc_record_count": upc_count,
#         "sku_match_status": "unknown",
#         "affected_category": "Food / snacks / candy / beverages",
#         "business_impact": "Food and beverage recalls can trigger inventory withdrawal, substitution demand, and safety review.",
#         "recommended_action": f"Prioritize ongoing and Class I recalls, then match UPCs against {retailer} inventory before store-level action.",
#         "raw_reference": f"{len(items)} recall records; {ongoing_count} ongoing; {class_i_count} Class I",
#     }
#     rows.append(signal)
#     return {"status": "success", "source": "openFDA", "error": "", "raw": data, "rows": rows, "items": items}


# def normalize_weather_area(area: str) -> str:
#     candidate = re.sub(r"[^A-Za-z]", "", area or "").upper()
#     if len(candidate) == 2:
#         return candidate
#     return "TX"


# def weather_alert_weight(severity: str, urgency: str, certainty: str) -> float:
#     severity_score = {
#         "extreme": 5.0,
#         "severe": 3.0,
#         "moderate": 2.0,
#         "minor": 1.0,
#         "unknown": 1.0,
#     }.get(str(severity or "").lower(), 1.0)
#     urgency_bonus = {
#         "immediate": 1.5,
#         "expected": 0.75,
#     }.get(str(urgency or "").lower(), 0.0)
#     certainty_bonus = {
#         "observed": 0.5,
#         "likely": 0.5,
#     }.get(str(certainty or "").lower(), 0.0)
#     return severity_score + urgency_bonus + certainty_bonus


# def collect_weather_alerts(area: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
#     state_area = normalize_weather_area(area)
#     params = {"area": state_area}
#     ok, data, msg = safe_request(
#         "https://api.weather.gov/alerts/active",
#         headers={"User-Agent": "MarketIntelligenceWorkbench/1.0", "Accept": "application/geo+json"},
#         params=params,
#         timeout=30,
#     )
#     if not ok:
#         return {"status": "failed", "source": "NOAA Weather Alerts", "error": msg, "raw": None, "rows": [], "items": []}
#     features = data.get("features", []) if isinstance(data, dict) else []
#     selected_alerts = features[: max(1, int(limit))]
#     items = []
#     score_components = []
#     severe_count = 0
#     extreme_count = 0
#     for alert in selected_alerts:
#         props = alert.get("properties", {}) if isinstance(alert, dict) else {}
#         severity = props.get("severity", "Unknown")
#         urgency = props.get("urgency", "Unknown")
#         certainty = props.get("certainty", "Unknown")
#         component = weather_alert_weight(severity, urgency, certainty)
#         score_components.append(component)
#         severity_text = str(severity or "").lower()
#         if severity_text == "extreme":
#             extreme_count += 1
#         if severity_text in {"severe", "extreme"}:
#             severe_count += 1
#         items.append(
#             {
#                 "event": props.get("event", ""),
#                 "severity": severity,
#                 "urgency": urgency,
#                 "certainty": certainty,
#                 "headline": props.get("headline", ""),
#                 "area_desc": props.get("areaDesc", ""),
#                 "effective": props.get("effective", ""),
#                 "expires": props.get("expires", ""),
#                 "instruction": props.get("instruction", ""),
#                 "risk_component": round(component, 2),
#             }
#         )
#     risk_score = round(min(10.0, sum(score_components)), 2) if items else 0.0
#     if items:
#         score_reason = (
#             f"Score sums weighted active NOAA alerts for {state_area}, capped at 10. "
#             f"Inputs: {len(items)} alert(s), {severe_count} severe/extreme, {extreme_count} extreme; "
#             f"severity/urgency/certainty components total {sum(score_components):.2f}."
#         )
#         raw_reference = f"{len(items)} active NOAA alert(s); top event: {items[0].get('event') or 'Unknown'}"
#         recommended_action = "Check affected counties against store and DC routes; use alert severity as a short-horizon disruption and emergency-demand feature."
#     else:
#         score_reason = f"NOAA returned 0 active alerts for {state_area}. Score is 0 because no current weather disruption signal is present."
#         raw_reference = "0 active NOAA alerts"
#         recommended_action = "Keep weather feature at baseline for this state, then refresh before short-horizon replenishment decisions."
#     signal = {
#         "date": utc_now()[:10],
#         "retailer": retailer,
#         "region": state_area,
#         "region_scope": "state_weather_alerts",
#         "source": "NOAA Weather Alerts",
#         "signal_area": "Weather Risk",
#         "signal_name": "supply_chain_weather_risk_score",
#         "signal_value": len(items),
#         "risk_score": risk_score,
#         "confidence": "High",
#         "score_reason": score_reason,
#         "alert_count": len(items),
#         "severe_or_extreme_count": severe_count,
#         "extreme_count": extreme_count,
#         "business_impact": "Active weather alerts can disrupt store traffic, DC-to-store routes, staffing, replenishment timing, and emergency-demand categories.",
#         "recommended_action": recommended_action,
#         "raw_reference": raw_reference,
#     }
#     return {"status": "success", "source": "NOAA Weather Alerts", "error": "", "raw": data, "rows": [signal], "items": items}


# def gnews_package_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
#     try:
#         from gnews import GNews
#     except ImportError:
#         return None
#     google_news = GNews(language=language, country=country, period=period, max_results=max_results)
#     return google_news.get_news(keyword)


# def google_news_rss_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> List[Dict[str, Any]]:
#     # Google News RSS supports a "when:" query operator. GNews package is preferred when installed.
#     query = quote_plus(f"{keyword} when:{period}")
#     country_code = country.upper()
#     lang_code = language.lower()
#     url = f"https://news.google.com/rss/search?q={query}&hl={lang_code}-{country_code}&gl={country_code}&ceid={country_code}:{lang_code}"
#     response = requests.get(url, timeout=30)
#     response.raise_for_status()
#     root = ET.fromstring(response.content)
#     articles = []
#     for item in root.findall(".//item")[:max_results]:
#         source_node = item.find("source")
#         articles.append(
#             {
#                 "title": item.findtext("title", default=""),
#                 "description": item.findtext("description", default=""),
#                 "published date": item.findtext("pubDate", default=""),
#                 "url": item.findtext("link", default=""),
#                 "publisher": source_node.text if source_node is not None else "",
#             }
#         )
#     return articles


# def clean_news_description(description: str) -> str:
#     text = re.sub(r"<[^>]+>", " ", description or "")
#     text = unescape(text)
#     text = re.sub(r"\s+", " ", text).strip()
#     return text


# def article_days_old(published_date: str) -> Optional[int]:
#     if not published_date:
#         return None
#     try:
#         published = parsedate_to_datetime(published_date)
#         if published.tzinfo is None:
#             published = published.replace(tzinfo=timezone.utc)
#         return max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
#     except (TypeError, ValueError):
#         return None


# def classify_news_title(title: str, description: str) -> Tuple[str, float, str]:
#     text = f"{title} {description}".lower()
#     if any(word in text for word in ["recall", "contamination", "lawsuit", "closure", "closing", "tariff", "warning"]):
#         return "risk_event", 7.0, "negative"
#     if any(word in text for word in ["inflation", "prices", "freight", "cost", "margin"]):
#         return "price_pressure", 6.0, "negative"
#     if any(word in text for word in ["deal", "sale", "promotion", "coupon", "holiday", "seasonal"]):
#         return "demand_opportunity", 6.5, "positive"
#     if any(word in text for word in ["earnings", "forecast", "guidance", "outlook"]):
#         return "financial_update", 5.5, "neutral"
#     return "general_market_news", 3.5, "neutral"


# def collect_gnews(keywords: List[str], country: str, language: str, period: str, max_results: int, retailer: str = "Retailer") -> Dict[str, Any]:
#     all_articles = []
#     errors = []
#     per_keyword_limit = max(1, int(max_results / max(1, len(keywords))))
#     for keyword in keywords:
#         try:
#             articles = gnews_package_collect(keyword, country, language, period, per_keyword_limit)
#             if articles is None:
#                 articles = google_news_rss_collect(keyword, country, language, period, per_keyword_limit)
#             for article in articles or []:
#                 description = clean_news_description(article.get("description", ""))
#                 event_type, score, sentiment = classify_news_title(article.get("title", ""), description)
#                 publisher = article.get("publisher", "")
#                 if isinstance(publisher, dict):
#                     publisher = publisher.get("title") or publisher.get("href") or ""
#                 published_date = article.get("published date") or article.get("published_date", "")
#                 days_old = article_days_old(published_date)
#                 if days_old is not None and days_old > 30:
#                     score = max(1.0, score - 1.0)
#                 all_articles.append(
#                     {
#                         "keyword": keyword,
#                         "title": article.get("title", ""),
#                         "description": description,
#                         "published_date": published_date,
#                         "days_old": days_old,
#                         "publisher": publisher,
#                         "url": article.get("url", ""),
#                         "source_tier": source_confidence(str(publisher)),
#                         "event_type": event_type,
#                         "sentiment": sentiment,
#                         "risk_score": score,
#                         "confidence": source_confidence(str(publisher)),
#                     }
#                 )
#         except Exception as exc:
#             errors.append(f"{keyword}: {exc}")

#     deduped = []
#     seen = set()
#     for article in all_articles:
#         key = article["url"] or article["title"]
#         if key and key not in seen:
#             seen.add(key)
#             deduped.append(article)

#     if not deduped:
#         return {
#             "status": "failed" if errors else "empty",
#             "source": "GNews",
#             "error": "; ".join(errors) if errors else "No meaningful articles returned for selected keywords.",
#             "raw": [],
#             "rows": [],
#             "items": [],
#         }

#     score = round(float(pd.Series([a["risk_score"] for a in deduped]).mean()), 2) if deduped else 1.0
#     negative_count = sum(1 for article in deduped if article.get("sentiment") == "negative")
#     high_conf_count = sum(1 for article in deduped if article.get("confidence") == "High")
#     event_counts = pd.Series([article.get("event_type", "unknown") for article in deduped]).value_counts().to_dict()
#     signal = {
#         "date": utc_now()[:10],
#         "retailer": retailer,
#         "region": country.upper(),
#         "region_scope": "country_news",
#         "source": "GNews / Google News RSS",
#         "signal_area": "Retail News",
#         "signal_name": "news_risk_score",
#         "signal_value": len(deduped),
#         "risk_score": score,
#         "confidence": "Medium",
#         "score_reason": f"Average article risk across {len(deduped)} deduped articles; {negative_count} negative articles; {high_conf_count} high-confidence publishers; event mix {event_counts}.",
#         "business_impact": "Recent news can reveal competitor moves, pricing pressure, recalls, store changes, and supply-chain risk.",
#         "recommended_action": "Review high-risk articles and use NVIDIA classification before executive distribution.",
#         "raw_reference": f"{len(deduped)} articles",
#     }
#     return {"status": "success", "source": "GNews", "error": "; ".join(errors), "raw": deduped, "rows": [signal], "items": deduped}


# def get_apify_value(obj: Any, key: str) -> Any:
#     if isinstance(obj, dict):
#         return obj.get(key)
#     if hasattr(obj, key):
#         return getattr(obj, key)
#     try:
#         return obj[key]
#     except (TypeError, KeyError, AttributeError):
#         return None


# def collect_apify_trends(
#     token: str,
#     keywords: List[str],
#     geo: str,
#     time_range: str,
#     retailer: str = "Retailer",
#     max_keywords: int = APIFY_HARD_KEYWORD_LIMIT,
# ) -> Dict[str, Any]:
#     if not token:
#         return {"status": "skipped", "source": "Apify Trends", "error": "No Apify token provided.", "raw": None, "rows": [], "items": []}
#     try:
#         from apify_client import ApifyClient
#     except ImportError:
#         return {"status": "failed", "source": "Apify Trends", "error": "apify-client is not installed.", "raw": None, "rows": [], "items": []}
#     try:
#         client = ApifyClient(token)
#         safe_max_keywords = min(max(1, int(max_keywords)), APIFY_HARD_KEYWORD_LIMIT)
#         safe_time_range = time_range if time_range in APIFY_ALLOWED_TIME_RANGES else APIFY_SAFE_TIME_RANGE
#         safe_time_range = safe_time_range or APIFY_SAFE_TIME_RANGE
#         selected_keywords = [kw for kw in keywords if kw][:safe_max_keywords]
#         if not selected_keywords:
#             return {"status": "skipped", "source": "Apify Trends", "error": "No trend keywords provided.", "raw": None, "rows": [], "items": []}
#         run_input = {"geo": geo, "searchTerms": selected_keywords, "timeRange": safe_time_range}
#         run = client.actor("apify/google-trends-scraper").call(run_input=run_input)
#         dataset_id = get_apify_value(run, "defaultDatasetId") or get_apify_value(run, "default_dataset_id")
#         if not dataset_id:
#             return {
#                 "status": "failed",
#                 "source": "Apify Trends",
#                 "error": "Apify run completed but no default dataset ID was found.",
#                 "raw": run_input,
#                 "rows": [],
#                 "items": [],
#             }
#         items = list(client.dataset(dataset_id).iterate_items())
#     except Exception as exc:
#         return {"status": "failed", "source": "Apify Trends", "error": str(exc), "raw": None, "rows": [], "items": []}

#     region_rows = []
#     for item in items:
#         keyword = item.get("searchTerm")
#         for rank, region in enumerate(item.get("interestBySubregion", []) or [], start=1):
#             values = region.get("value") or []
#             if values:
#                 region_rows.append(
#                     {
#                         "keyword": keyword,
#                         "region": region.get("geoName", ""),
#                         "interest_score": values[0],
#                         "rank": rank,
#                     }
#                 )
#     top_score = max([float(x["interest_score"]) for x in region_rows], default=0.0)
#     signal_score = round(min(10.0, max(1.0, top_score / 10.0)), 2) if top_score else 1.0
#     signal = {
#         "date": utc_now()[:10],
#         "retailer": retailer,
#         "region": geo,
#         "region_scope": "trend_geo",
#         "source": "Apify Google Trends",
#         "signal_area": "Search Demand",
#         "signal_name": "search_demand_score",
#         "signal_value": top_score,
#         "risk_score": signal_score,
#         "confidence": "Medium",
#         "score_reason": f"Score is top regional Google Trends interest divided by 10. Run hard-limited to {len(selected_keywords)} keyword(s) over {safe_time_range} to control Apify quota and memory.",
#         "business_impact": "Search interest can reveal early demand shifts, promotional interest, and seasonal spikes.",
#         "recommended_action": "Compare rising regions against sales, inventory, and competitor promotion calendars.",
#         "raw_reference": f"{len(region_rows)} regional trend rows",
#     }
#     return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [signal], "items": region_rows}


# def generate_fallback_brief(feature_df: pd.DataFrame, retailer: str, region: str) -> str:
#     if feature_df.empty:
#         return (
#             "Executive Summary:\n"
#             "- No external signals were collected for this run.\n"
#             "- Enable at least one source and run the workbench again before using the output for planning.\n\n"
#             "Confidence And Limitations:\n"
#             "- No score can be explained because no feature rows exist."
#         )
#     strongest = feature_df.sort_values("risk_score", ascending=False).head(3)
#     avg_score = round(float(feature_df["risk_score"].mean()), 2)
#     top = strongest.iloc[0]
#     sources = ", ".join(sorted({str(src) for src in feature_df["source"].dropna().tolist()}))
#     lines = [
#         "Executive Summary:",
#         f"- {retailer} in {region} has {risk_band(avg_score).lower()} external signal intensity with an average score of {avg_score}/10 across {len(feature_df)} forecast-ready row(s).",
#         f"- The strongest current signal is {top['signal_area']} at {float(top['risk_score']):.2f}/10 from {top['source']}.",
#         f"- This brief is grounded in collected source output only: {sources}.",
#         "",
#         "Top Signal Evidence:",
#     ]
#     for idx, (_, row) in enumerate(strongest.iterrows(), start=1):
#         lines.append(
#             f"{idx}. {row['signal_area']} scored {float(row['risk_score']):.2f}/10 from {row['source']}. Why: {row.get('score_reason', 'No score reason available.')}"
#         )
#         lines.append(f"- Business impact: {row.get('business_impact', 'No business impact available.')}")
#     lines.extend(
#         [
#             "",
#             "Forecasting Relevance:",
#             "- Treat each score as an external regressor candidate, not as a final demand forecast.",
#             "- Join these rows to internal POS, category, store, promotion, and inventory data before model training or operational action.",
#             "",
#             "Recommended Actions:",
#         ]
#     )
#     for idx, (_, row) in enumerate(strongest.iterrows(), start=1):
#         lines.append(f"{idx}. {row.get('recommended_action', 'Review this signal with the category owner.')}")
#     lines.extend(
#         [
#             "",
#             "Confidence And Limitations:",
#             "- Scores are explainable directional signals from public API data, not proof of actual Retailer demand movement.",
#             "- Category performance still requires internal sales/POS data; external APIs explain context but do not replace internal performance data.",
#         ]
#     )
#     return "\n".join(lines)


# def generate_nvidia_brief(
#     api_key: str,
#     model: str,
#     feature_df: pd.DataFrame,
#     articles: List[Dict[str, Any]],
#     retailer: str,
#     region: str,
# ) -> Tuple[str, str, Dict[str, Any]]:
#     audit = build_base_llm_audit(feature_df, articles, retailer, region, model)
#     if not api_key:
#         audit.update(
#             {
#                 "provider": "Local deterministic fallback",
#                 "model": "rule_based_summary",
#                 "brief_source": "fallback",
#                 "fallback_used": True,
#                 "fallback_reason": "No NVIDIA API key provided. No external LLM call was made.",
#             }
#         )
#         return generate_fallback_brief(feature_df, retailer, region), "fallback", audit
#     request_body = {
#         "model": model,
#         "messages": [{"role": "system", "content": audit["system_prompt"]}, {"role": "user", "content": audit["user_prompt"]}],
#         "temperature": 0.2,
#         "max_tokens": NVIDIA_MAX_TOKENS,
#     }
#     attempts: List[Dict[str, Any]] = []
#     ok = False
#     data: Any = None
#     msg = ""
#     for attempt_number, timeout_seconds in enumerate(NVIDIA_TIMEOUT_SECONDS, start=1):
#         started = time.monotonic()
#         ok, data, msg = safe_request(
#             NVIDIA_CHAT_URL,
#             method="POST",
#             headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
#             json_body=request_body,
#             timeout=timeout_seconds,
#         )
#         elapsed_ms = int((time.monotonic() - started) * 1000)
#         attempts.append(
#             {
#                 "attempt": attempt_number,
#                 "timeout_seconds": timeout_seconds,
#                 "elapsed_ms": elapsed_ms,
#                 "success": ok,
#                 "technical_error": "" if ok else msg,
#             }
#         )
#         if ok:
#             break
#         retryable = any(marker in str(msg).lower() for marker in ["timed out", "timeout", "429", "rate limit", "temporarily", "503", "502"])
#         if retryable and attempt_number < len(NVIDIA_TIMEOUT_SECONDS):
#             time.sleep(min(4.0, 1.25 * attempt_number))
#     if not ok:
#         brief_source, friendly_reason = friendly_nvidia_failure(msg)
#         audit.update(
#             {
#                 "provider": "Local deterministic fallback",
#                 "model": "rule_based_summary",
#                 "brief_source": brief_source,
#                 "fallback_used": True,
#                 "fallback_reason": friendly_reason,
#                 "nvidia_attempts": attempts,
#                 "technical_error": msg,
#             }
#         )
#         return generate_fallback_brief(feature_df, retailer, region), brief_source, audit
#     content = data.get("choices", [{}])[0].get("message", {}).get("content")
#     content_text = str(content or "").strip()
#     if not content_text:
#         audit.update(
#             {
#                 "provider": "Local deterministic fallback",
#                 "model": "rule_based_summary",
#                 "brief_source": "fallback: empty NVIDIA response",
#                 "fallback_used": True,
#                 "fallback_reason": "NVIDIA returned an empty response.",
#                 "nvidia_attempts": attempts,
#             }
#         )
#         return generate_fallback_brief(feature_df, retailer, region), "fallback: empty NVIDIA response", audit
#     audit.update(
#         {
#             "provider": "NVIDIA",
#             "model": model,
#             "sent_to_llm": True,
#             "brief_source": "nvidia",
#             "fallback_used": False,
#             "fallback_reason": "",
#             "response_chars": len(content_text),
#             "nvidia_attempts": attempts,
#             "response_time_ms": attempts[-1]["elapsed_ms"] if attempts else 0,
#         }
#     )
#     return content_text, "nvidia", audit


# def render_metric_card(title: str, value: str, note: str, confidence: str = "") -> None:
#     pill = f'<span class="pill {confidence_class(confidence)}">{confidence}</span>' if confidence else ""
#     html = (
#         '<div class="metric-card">'
#         f'<div class="metric-label">{title}</div>'
#         f'<div class="metric-value">{value}</div>'
#         f"{pill}"
#         f'<div class="metric-note">{note}</div>'
#         "</div>"
#     )
#     st.markdown(
#         html,
#         unsafe_allow_html=True,
#     )


# def compute_composite_scores(feature_df: pd.DataFrame) -> Dict[str, float]:
#     if feature_df.empty:
#         return {"Market Opportunity": 0.0, "Market Risk": 0.0, "Forecast Impact": 0.0}
#     area_scores: Dict[str, float] = {}
#     for _, row in feature_df.iterrows():
#         if pd.isna(row.get("risk_score")):
#             continue
#         area = str(row["signal_area"])
#         area_scores[area] = max(area_scores.get(area, 0.0), float(row["risk_score"]))
#     opportunity = max(
#         area_scores.get("Retail News", 0.0),
#         area_scores.get("Search Demand", 0.0),
#         area_scores.get("Category CPI", 0.0) * 0.7,
#     )
#     risk = max(
#         area_scores.get("Product Recalls", 0.0),
#         area_scores.get("Weather Risk", 0.0),
#         area_scores.get("Inflation", 0.0),
#         area_scores.get("Category CPI", 0.0),
#     )
#     impact = min(10.0, (opportunity * 0.45) + (risk * 0.45) + (len(feature_df) * 0.25))
#     return {
#         "Market Opportunity": round(opportunity, 2),
#         "Market Risk": round(risk, 2),
#         "Forecast Impact": round(impact, 2),
#     }


# def compute_dollar_tree_kpis(feature_df: pd.DataFrame) -> Dict[str, float]:
#     if feature_df.empty:
#         return {
#             "Value Basket Pressure": 0.0,
#             "Safety And Compliance Risk": 0.0,
#             "Supply Chain Disruption": 0.0,
#             "Demand Signal Priority": 0.0,
#         }
#     def max_for(column: str, values: List[str]) -> float:
#         if column not in feature_df.columns:
#             return 0.0
#         mask = feature_df[column].astype(str).isin(values)
#         if not mask.any():
#             return 0.0
#         return float(feature_df.loc[mask, "risk_score"].max())

#     value_pressure = max(
#         max_for("enterprise_kpi", ["Value Basket Pressure", "Consumer Wallet Pressure", "Consumables Demand Pressure", "Household Essentials Pressure"]),
#         max_for("signal_area", ["Inflation", "Category CPI"]),
#     )
#     safety = max(max_for("enterprise_kpi", ["Safety And Compliance Risk"]), max_for("signal_area", ["Product Recalls"]))
#     disruption = max(max_for("enterprise_kpi", ["Supply Chain Disruption Risk"]), max_for("signal_area", ["Weather Risk"]))
#     demand = max(
#         max_for("enterprise_kpi", ["Demand Interest Spike", "Market Event Risk"]),
#         max_for("signal_area", ["Search Demand", "Retail News"]),
#     )
#     return {
#         "Value Basket Pressure": round(value_pressure, 2),
#         "Safety And Compliance Risk": round(safety, 2),
#         "Supply Chain Disruption": round(disruption, 2),
#         "Demand Signal Priority": round(demand, 2),
#     }


# def build_trust_metrics(run: Dict[str, Any], feature_df: pd.DataFrame) -> List[Dict[str, str]]:
#     results = run.get("results", {})
#     llm_audit = run.get("llm_audit", {})
#     evidence_records = build_collector_evidence(results, run.get("run_config", {}))
#     live_sources = sum(1 for record in evidence_records if record.get("live_request_made") == "Yes")
#     raw_records = sum(int(record.get("raw_records_pulled", 0) or 0) for record in evidence_records)
#     mock_used = any_mock_used(results, llm_audit)
#     brief_source = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
#     return [
#         {"label": "Mock Data", "value": "No" if not mock_used else "Yes", "note": "Collector and LLM audit flags inspected.", "state": "good" if not mock_used else "warn"},
#         {"label": "Live Sources", "value": str(live_sources), "note": "Sources that made a live request or returned live evidence.", "state": "good"},
#         {"label": "Raw Records", "value": str(raw_records), "note": "Inspectable source records behind the normalized rows.", "state": "good" if raw_records else "warn"},
#         {"label": "Feature Rows", "value": str(len(feature_df)), "note": "Forecast-ready external signal rows generated.", "state": "good" if len(feature_df) else "warn"},
#         {"label": "Brief Mode", "value": brief_source, "note": str(llm_audit.get("fallback_reason") or "AI brief grounded in shown payload."), "state": "good" if brief_source == "NVIDIA" else "warn"},
#     ]


# def render_trust_panel(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
#     cards = []
#     for metric in build_trust_metrics(run, feature_df):
#         cards.append(
#             f"<div class='trust-card {escape(metric.get('state', 'good'))}'>"
#             f"<div class='trust-label'>{escape(metric['label'])}</div>"
#             f"<div class='trust-value'>{escape(metric['value'])}</div>"
#             f"<div class='trust-note'>{escape(metric['note'])}</div>"
#             "</div>"
#         )
#     st.markdown("<div class='trust-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


# def build_previous_run_comparison(feature_df: pd.DataFrame, previous_run: Optional[Dict[str, Any]]) -> pd.DataFrame:
#     if feature_df.empty or not previous_run:
#         return pd.DataFrame()
#     previous_df = previous_run.get("feature_df", pd.DataFrame())
#     if previous_df is None or previous_df.empty:
#         return pd.DataFrame()
#     if "dollar_tree_category" not in previous_df.columns:
#         previous_df = enrich_feature_rows_for_retailer(previous_df, str(previous_run.get("run_config", {}).get("retailer", "Retailer")))
#     key_cols = [col for col in ["source", "signal_name", "region"] if col in feature_df.columns and col in previous_df.columns]
#     if not key_cols:
#         return pd.DataFrame()
#     current_cols = key_cols + [col for col in ["signal_area", "dollar_tree_category", "planning_owner", "risk_score"] if col in feature_df.columns]
#     previous_cols = key_cols + [col for col in ["risk_score"] if col in previous_df.columns]
#     current = feature_df[current_cols].copy()
#     previous = previous_df[previous_cols].copy()
#     current = current.rename(columns={"risk_score": "current_score"})
#     previous = previous.rename(columns={"risk_score": "previous_score"})
#     merged = current.merge(previous, on=key_cols, how="left")
#     if "previous_score" not in merged.columns:
#         return pd.DataFrame()
#     merged["previous_score"] = pd.to_numeric(merged["previous_score"], errors="coerce")
#     merged["current_score"] = pd.to_numeric(merged["current_score"], errors="coerce")
#     merged["delta"] = (merged["current_score"] - merged["previous_score"]).round(2)
#     merged["movement"] = merged["delta"].apply(lambda x: "New" if pd.isna(x) else "Increased" if x > 0.25 else "Decreased" if x < -0.25 else "Stable")
#     return merged.sort_values(["movement", "current_score"], ascending=[True, False])


# def render_previous_run_comparison(feature_df: pd.DataFrame, previous_run: Optional[Dict[str, Any]]) -> None:
#     comparison_df = build_previous_run_comparison(feature_df, previous_run)
#     if comparison_df.empty:
#         st.info("Previous run comparison will appear after at least two runs in this session. The comparison matches rows by source, signal name, and region.")
#         return
#     inc = int((comparison_df["movement"] == "Increased").sum())
#     dec = int((comparison_df["movement"] == "Decreased").sum())
#     stable = int((comparison_df["movement"] == "Stable").sum())
#     cols = st.columns(4)
#     with cols[0]:
#         render_metric_card("Compared Rows", str(len(comparison_df)), "Matched by source, signal, and region.")
#     with cols[1]:
#         render_metric_card("Increased", str(inc), "Signals whose score rose by more than 0.25.")
#     with cols[2]:
#         render_metric_card("Decreased", str(dec), "Signals whose score fell by more than 0.25.")
#     with cols[3]:
#         render_metric_card("Stable", str(stable), "Signals within +/- 0.25.")
#     visible_cols = [col for col in ["source", "signal_area", "signal_name", "region", "dollar_tree_category", "planning_owner", "previous_score", "current_score", "delta", "movement"] if col in comparison_df.columns]
#     st.dataframe(comparison_df[visible_cols], width="stretch", hide_index=True)


# def validation_guidance_for_row(row: pd.Series) -> Dict[str, str]:
#     area = str(row.get("signal_area", "")).lower()
#     feature = str(row.get("forecast_feature", "")).lower()
#     if "recall" in area or "recall" in feature:
#         return {
#             "validation_analysis": "Match UPC/vendor/product text to SKU master, then compare affected-store inventory and substitution sales before and after recall date.",
#             "validation_metric": "UPC match rate, exposed on-hand units, substitute category lift, withdrawal completion rate",
#         }
#     if "weather" in area or "weather" in feature:
#         return {
#             "validation_analysis": "Join alerts to store/DC geography and compare affected stores against unaffected stores for traffic, sales, and replenishment delays.",
#             "validation_metric": "Affected vs control sales delta, late delivery count, emergency-category uplift",
#         }
#     if "cpi" in feature or "inflation" in area:
#         return {
#             "validation_analysis": "Join CPI pressure to weekly category sales, basket mix, unit velocity, and price changes to test trade-down behavior.",
#             "validation_metric": "Category unit lift, average basket shift, price sensitivity, margin pressure",
#         }
#     if "trends" in feature or "search" in area:
#         return {
#             "validation_analysis": "Compare regional search interest against store traffic, sales velocity, and promotion calendar in the same region and week.",
#             "validation_metric": "Search-to-sales lead correlation, regional conversion lift, promotion-adjusted demand",
#         }
#     if "news" in feature or "news" in area:
#         return {
#             "validation_analysis": "Tag event dates and compare category/store performance before and after the news event while controlling for promotions.",
#             "validation_metric": "Pre/post category variance, forecast error reduction, event-attributed exception count",
#         }
#     return {
#         "validation_analysis": "Join this external score to internal POS, inventory, product hierarchy, promotion, and store data; test whether it explains forecast variance.",
#         "validation_metric": "Forecast error reduction, category sales variance, inventory exception count",
#     }


# def build_internal_validation_plan(feature_df: pd.DataFrame) -> pd.DataFrame:
#     if feature_df.empty:
#         return pd.DataFrame()
#     records = []
#     for _, row in feature_df.sort_values("risk_score", ascending=False).iterrows():
#         guidance = validation_guidance_for_row(row)
#         records.append(
#             {
#                 "external_signal": f"{row.get('signal_area', 'Signal')} / {row.get('signal_name', '')}",
#                 "dollar_tree_category": row.get("dollar_tree_category", ""),
#                 "planning_owner": row.get("planning_owner", ""),
#                 "internal_data_needed": row.get("internal_data_needed", ""),
#                 "validation_analysis": guidance["validation_analysis"],
#                 "validation_metric": guidance["validation_metric"],
#                 "decision_use": "Promote to model feature if it improves forecast error or explains planning exceptions.",
#             }
#         )
#     return pd.DataFrame(records)


# def render_internal_validation_agent(feature_df: pd.DataFrame) -> None:
#     plan_df = build_internal_validation_plan(feature_df)
#     if plan_df.empty:
#         st.info("Internal validation guidance will appear after feature rows are generated.")
#         return
#     st.dataframe(plan_df, width="stretch", hide_index=True)


# def render_enterprise_context(retailer_name: str) -> None:
#     st.markdown(
#         "<div class='enterprise-band'>"
#         f"<div class='enterprise-band-title'>{escape(retailer_name)} External Signal Control Layer</div>"
#         "<div class='enterprise-band-copy'>"
#         "This view translates public signals into category, owner, forecast-feature, and action language for buyers, category managers, demand planners, compliance, and supply-chain teams. "
#         "It does not claim internal category performance until POS, inventory, product hierarchy, promotion, vendor, and store/DC data are connected."
#         "</div></div>",
#         unsafe_allow_html=True,
#     )


# def render_results_command_header(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
#     run_config = run.get("run_config", {})
#     retailer_name = str(run_config.get("retailer", retailer_label))
#     llm_audit = run.get("llm_audit", {})
#     source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
#     avg_score = float(feature_df["risk_score"].mean()) if not feature_df.empty and "risk_score" in feature_df.columns else 0.0
#     top_label = "No signal"
#     if not feature_df.empty:
#         top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
#         top_label = f"{top.get('signal_area', 'Signal')} / {float(top.get('risk_score', 0) or 0):.2f}"
#     brief_mode = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
#     meta = [
#         ("Run Time", str(run.get("timestamp", ""))),
#         ("Brief Mode", brief_mode),
#         ("Sources", f"{source_count} source(s)"),
#         ("Avg Score", f"{avg_score:.2f} / 10"),
#     ]
#     meta_html = "".join(
#         f"<div class='result-meta-cell'><div class='result-meta-label'>{escape(label)}</div><div class='result-meta-value'>{escape(value)}</div></div>"
#         for label, value in meta
#     )
#     note = llm_audit.get("fallback_reason") or "Executive brief is grounded in the normalized source rows shown in this run."
#     st.markdown(
#         "<div class='result-command'>"
#         "<div>"
#         "<div class='config-eyebrow'>Results Command Center</div>"
#         f"<div class='result-command-title'>{escape(retailer_name)} external signal readout</div>"
#         f"<div class='result-command-copy'>Top signal: {escape(top_label)}. {escape(str(note))} Use this page from left to right: decision summary, category impact, score logic, then raw evidence.</div>"
#         "</div>"
#         f"<div class='result-command-meta'>{meta_html}</div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )


# def render_decision_summary(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> None:
#     cards = []
#     for action in build_recommended_actions(feature_df, results):
#         cards.append(
#             "<div class='decision-card'>"
#             f"<div class='decision-owner'>{escape(action['label'])}</div>"
#             f"<div class='decision-title'>{escape(action['title'])}</div>"
#             f"<div class='decision-body'>{escape(action['body'])}</div>"
#             "</div>"
#         )
#     if not cards:
#         cards.append(
#             "<div class='decision-card'><div class='decision-owner'>Setup</div><div class='decision-title'>Run signal sources</div><div class='decision-body'>No decision cards are available until forecast-ready rows are generated.</div></div>"
#         )
#     st.markdown("<div class='decision-grid'>" + "".join(cards[:4]) + "</div>", unsafe_allow_html=True)


# def render_explainability_ladder() -> None:
#     steps = [
#         ("01", "Collect", "Live public APIs return raw evidence; skipped sources are labeled."),
#         ("02", "Normalize", "Records become forecast-ready rows with source, signal, region, and score fields."),
#         ("03", "Map", "Rows are mapped to retail category, owner, KPI, and forecast feature."),
#         ("04", "Score", "Each source uses a visible rule; score_reason explains the exact driver."),
#         ("05", "Brief", "NVIDIA or fallback summarizes only the shown rows and article context."),
#     ]
#     html = "".join(
#         "<div class='explain-step'>"
#         f"<div class='explain-step-num'>{escape(num)}</div>"
#         f"<div class='explain-step-title'>{escape(title)}</div>"
#         f"<div class='explain-step-copy'>{escape(copy)}</div>"
#         "</div>"
#         for num, title, copy in steps
#     )
#     st.markdown("<div class='explain-ladder'>" + html + "</div>", unsafe_allow_html=True)


# def render_audit_command_header(
#     run: Dict[str, Any],
#     mock_used: bool,
#     raw_records: int,
#     pulled_sources: int,
#     llm_audit: Dict[str, Any],
#     feature_df: pd.DataFrame,
# ) -> None:
#     brief_mode = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
#     cells = [
#         ("Mock Data", "No" if not mock_used else "Yes"),
#         ("Raw Records", str(raw_records)),
#         ("Live Sources", str(pulled_sources)),
#         ("Brief Mode", brief_mode),
#     ]
#     cell_html = "".join(
#         f"<div class='audit-status-cell'><div class='audit-status-label'>{escape(label)}</div><div class='audit-status-value'>{escape(value)}</div></div>"
#         for label, value in cells
#     )
#     status_copy = (
#         "All generated rows are traceable to collector outputs and the brief is tied to the shown payload."
#         if not mock_used
#         else "At least one collector or analysis step is marked as mock. Review provenance before using this run."
#     )
#     if llm_audit.get("fallback_used"):
#         status_copy += f" Brief fallback reason: {llm_audit.get('fallback_reason', 'NVIDIA unavailable')}."
#     st.markdown(
#         "<div class='audit-command'>"
#         "<div>"
#         "<div class='config-eyebrow'>Evidence Audit</div>"
#         "<div class='audit-command-title'>Run provenance and chain of custody</div>"
#         f"<div class='audit-command-copy'>{escape(status_copy)} Feature rows available: {len(feature_df)}.</div>"
#         "</div>"
#         f"<div class='audit-status-grid'>{cell_html}</div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )


# def render_audit_lineage() -> None:
#     steps = [
#         ("01", "Request", "Run config stores source toggles, query scope, limits, and guarded Apify mode."),
#         ("02", "Collect", "Each collector records status, endpoint/actor, raw record count, and errors."),
#         ("03", "Normalize", "Collector rows become forecast-ready signals with score reasons and raw references."),
#         ("04", "Analyze", "Brief payload is hashed and capped; NVIDIA/fallback status is recorded separately."),
#         ("05", "Inspect", "Raw payloads, cleaned items, normalized rows, prompt, and output can be reviewed."),
#     ]
#     html = "".join(
#         "<div class='audit-lineage-step'>"
#         f"<div class='audit-lineage-num'>{escape(num)}</div>"
#         f"<div class='audit-lineage-title'>{escape(title)}</div>"
#         f"<div class='audit-lineage-copy'>{escape(copy)}</div>"
#         "</div>"
#         for num, title, copy in steps
#     )
#     st.markdown("<div class='audit-lineage'>" + html + "</div>", unsafe_allow_html=True)


# def render_dollar_tree_impact_matrix(feature_df: pd.DataFrame) -> None:
#     if feature_df.empty:
#         st.info("No retail impact matrix is available until feature rows are generated.")
#         return
#     preferred_cols = [
#         "dollar_tree_category",
#         "enterprise_kpi",
#         "risk_score",
#         "action_priority",
#         "planning_owner",
#         "demand_direction",
#         "forecast_feature",
#         "impact_hypothesis",
#         "internal_data_needed",
#         "source",
#     ]
#     visible_cols = [col for col in preferred_cols if col in feature_df.columns]
#     sort_cols = [col for col in ["action_priority", "risk_score"] if col in feature_df.columns]
#     ascending = [True if col == "action_priority" else False for col in sort_cols]
#     matrix_source = feature_df.sort_values(sort_cols, ascending=ascending) if sort_cols else feature_df
#     matrix = matrix_source[visible_cols]
#     st.dataframe(matrix, width="stretch", hide_index=True)


# def render_scenario_simulator(feature_df: pd.DataFrame) -> None:
#     scenarios = {
#         "Inflation rises again": {
#             "category": "Total value basket, food, household essentials",
#             "owner": "Merchandising Strategy + Demand Planning",
#             "feature": "headline_cpi_value_pressure",
#             "action": "Watch trade-down behavior, validate basket mix, and review value-sensitive replenishment.",
#         },
#         "FDA recall affects consumables": {
#             "category": "Snacks, candy, beverages, consumables",
#             "owner": "Compliance + Category Buyer",
#             "feature": "recall_exposure_score",
#             "action": "Match UPCs against SKU master, isolate affected inventory, and prepare substitute-item monitoring.",
#         },
#         "Severe weather hits selected state": {
#             "category": "Emergency demand and replenishment-sensitive categories",
#             "owner": "Supply Chain + Demand Planning",
#             "feature": "state_weather_disruption_score",
#             "action": "Check store/DC exposure, route risk, and short-horizon emergency-demand uplift.",
#         },
#         "Competitor promotion pressure rises": {
#             "category": "Overlapping value categories and seasonal assortment",
#             "owner": "Buyer + Category Manager",
#             "feature": "competitor_promotion_pressure_score",
#             "action": "Compare overlapping items, review promotional calendar, and watch category conversion.",
#         },
#     }
#     selected = st.selectbox("Scenario", list(scenarios.keys()), label_visibility="collapsed")
#     scenario = scenarios[selected]
#     evidence_note = "No current run evidence matched this scenario directly."
#     if not feature_df.empty and "forecast_feature" in feature_df.columns:
#         matching = feature_df[feature_df["forecast_feature"].astype(str).str.contains(scenario["feature"].split("_")[0], case=False, na=False)]
#         if not matching.empty:
#             top = matching.sort_values("risk_score", ascending=False).iloc[0]
#             evidence_note = f"Nearest current signal: {top.get('signal_area')} from {top.get('source')} scored {float(top.get('risk_score', 0) or 0):.2f}/10."
#     st.markdown(
#         "<div class='enterprise-band'>"
#         f"<div class='enterprise-band-title'>{escape(selected)}</div>"
#         f"<div class='enterprise-band-copy'><strong>Likely retail category:</strong> {escape(scenario['category'])}<br>"
#         f"<strong>Owner:</strong> {escape(scenario['owner'])}<br>"
#         f"<strong>Forecast feature:</strong> {escape(scenario['feature'])}<br>"
#         f"<strong>Action:</strong> {escape(scenario['action'])}<br>"
#         f"<strong>Run evidence:</strong> {escape(evidence_note)}</div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )


# def build_recommended_actions(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
#     actions = []
#     if feature_df.empty:
#         return [{"label": "Setup", "title": "Run signal sources", "body": "Enable at least one source to generate forecast-ready rows."}]
#     top_rows = feature_df.sort_values("risk_score", ascending=False).head(3)
#     for _, row in top_rows.iterrows():
#         owner = str(row.get("planning_owner") or "Planning Owner")
#         category = str(row.get("dollar_tree_category") or row.get("signal_area") or "Category")
#         hypothesis = str(row.get("impact_hypothesis") or row.get("recommended_action") or "Review this signal with the category owner.")
#         actions.append(
#             {
#                 "label": owner,
#                 "title": category,
#                 "body": f"{hypothesis} Next: {row.get('recommended_action', 'Review this signal with the category owner.')}",
#             }
#         )
#     apify_result = results.get("apify")
#     if apify_result and apify_result.get("status") in {"failed", "skipped"}:
#         actions.append(
#             {
#                 "label": "Apify",
#                 "title": "Search demand not collected",
#                 "body": apify_result.get("error") or "Apify did not return a usable trends signal. Keep MVP on public sources or run one guarded live query.",
#             }
#         )
#     return actions[:4]


# def render_action_card(label: str, title: str, body: str) -> None:
#     html = (
#         '<div class="action-card">'
#         f'<div class="action-label">{escape(label)}</div>'
#         f'<div class="action-title">{escape(title)}</div>'
#         f'<div class="action-body">{escape(body)}</div>'
#         "</div>"
#     )
#     st.markdown(html, unsafe_allow_html=True)


# def render_source_tile(name: str, status: str, detail: str, purpose: str = "") -> None:
#     status_class = "pill-high" if status == "Active" else "pill-medium" if status == "Optional" else "pill-low"
#     tile_state = "active" if status == "Active" else "off"
#     purpose_html = f"<div class='source-purpose'>{escape(purpose)}</div>" if purpose else ""
#     html = (
#         f'<div class="source-tile {tile_state}">'
#         f'<div class="source-name">{escape(name)} <span class="pill {status_class}" style="margin-left:6px;margin-top:0;">{escape(status)}</span></div>'
#         f'<div class="source-meta">{escape(detail)}</div>'
#         f"{purpose_html}"
#         "</div>"
#     )
#     st.markdown(html, unsafe_allow_html=True)


# def render_glossary() -> None:
#     terms = [
#         ("Signal", "An external event or measurement that may explain demand, price pressure, or operational risk."),
#         ("Risk Score", "A 0-10 directional score. Higher means the signal deserves more attention, not that demand is guaranteed to move."),
#         ("Forecast Feature", "A structured column that can later be joined to internal sales, store, category, promotion, and inventory data."),
#         ("Region Scope", "Whether the signal is national, state-level, trend geography, or selected market context."),
#         ("Fallback Brief", "A deterministic local summary generated when NVIDIA is unavailable or no API key is supplied."),
#         ("Mock Data", "Synthetic or placeholder data. This app marks mock usage explicitly; live public collectors should show No in the audit table."),
#     ]
#     cards = []
#     for term, definition in terms:
#         cards.append(
#             "<div class='glossary-card'>"
#             f"<div class='glossary-term'>{escape(term)}</div>"
#             f"<div class='glossary-def'>{escape(definition)}</div>"
#             "</div>"
#         )
#     st.markdown("<div class='glossary-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


# def render_workflow_strip() -> None:
#     steps = [
#         ("01", "Collect APIs"),
#         ("02", "Clean records"),
#         ("03", "Score signals"),
#         ("04", "Generate brief"),
#         ("05", "Export features"),
#     ]
#     html = "<div class='workflow'>" + "".join(
#         f"<div class='workflow-step'><span>{num}</span>{escape(label)}</div>" for num, label in steps
#     ) + "</div>"
#     st.markdown(html, unsafe_allow_html=True)


# def render_run_monitor(slot: Any, title: str, states: Dict[str, Dict[str, str]], progress_pct: int) -> None:
#     cards = []
#     for name, info in states.items():
#         status = info.get("status", "queued")
#         detail = info.get("detail", "")
#         status_class = {
#             "running": "status-running",
#             "success": "status-success",
#             "failed": "status-failed",
#             "skipped": "status-skipped",
#             "queued": "status-queued",
#         }.get(status, "status-queued")
#         cards.append(
#             "<div class='run-status-card'>"
#             f"<div class='status-badge {status_class}'>{escape(status)}</div>"
#             f"<div class='run-status-name'>{escape(name)}</div>"
#             f"<div class='run-status-detail'>{escape(detail)}</div>"
#             "</div>"
#         )
#     html = (
#         "<div class='run-monitor'>"
#         "<div class='run-monitor-head'>"
#         f"<div><div class='run-monitor-sub'>Pipeline Status</div><div class='run-monitor-title'>{escape(title)}</div></div>"
#         f"<div class='tbadge'>{int(progress_pct)}%</div>"
#         "</div>"
#         "<div class='run-progress-track'>"
#         f"<div class='run-progress-fill' style='width:{max(0, min(100, int(progress_pct)))}%;'></div>"
#         "</div>"
#         "<div class='run-status-grid'>"
#         + "".join(cards)
#         + "</div></div>"
#     )
#     slot.markdown(html, unsafe_allow_html=True)


# def render_sidebar_status(slot: Any, message: str, state: str = "info") -> None:
#     if state == "success":
#         slot.success(message)
#     elif state == "warning":
#         slot.warning(message)
#     elif state == "error":
#         slot.error(message)
#     else:
#         slot.info(message)


# def render_score_chart(feature_df: pd.DataFrame) -> None:
#     if feature_df.empty:
#         st.info("No feature rows yet.")
#         return
#     fig = go.Figure(
#         go.Bar(
#             x=feature_df["risk_score"],
#             y=feature_df["signal_area"],
#             orientation="h",
#             marker_color=["#22C55E" if x < 5 else "#F59E0B" if x < 8 else "#EF4444" for x in feature_df["risk_score"]],
#             text=feature_df["risk_score"],
#             textposition="auto",
#         )
#     )
#     fig.update_layout(
#         height=280,
#         margin={"l": 10, "r": 20, "t": 10, "b": 10},
#         xaxis={"range": [0, 10], "title": "Score"},
#         yaxis={"title": ""},
#         plot_bgcolor="#FFFFFF",
#         paper_bgcolor="#FFFFFF",
#     )
#     st.plotly_chart(fig, width="stretch")


# def scoring_formula_for_row(row: pd.Series) -> str:
#     source = str(row.get("source", "")).lower()
#     area = str(row.get("signal_area", "")).lower()
#     if "bls" in source or "cpi" in area:
#         return "CPI scoring starts from a neutral 4.0, adjusts upward or downward using monthly CPI change, adds pressure when YoY inflation is elevated, then clips to a 1-10 range."
#     if "fda" in source or "recall" in area:
#         return "Recall scoring starts from reason severity, then adjusts for FDA classification and recall status. Class I and ongoing recalls increase the score; terminated recalls reduce it."
#     if "noaa" in source or "weather" in area:
#         return "Weather scoring sums active NOAA alert severity weights for the selected state, adds urgency/certainty pressure, then caps the supply-chain risk score at 10."
#     if "gnews" in source or "news" in area:
#         return "News scoring classifies each article into event type and sentiment, adjusts for recency/source quality, then averages deduplicated article risk."
#     if "apify" in source or "search" in area:
#         return f"Search scoring uses top regional Google Trends interest divided by 10, with backend limits of {APIFY_SAFE_TIME_RANGE} and {APIFY_HARD_KEYWORD_LIMIT} keyword(s)."
#     return "Score is normalized to a 1-10 signal intensity scale using the collector-specific scoring rule."


# def render_score_explainability(feature_df: pd.DataFrame) -> None:
#     if feature_df.empty:
#         st.info("No signal explanations available.")
#         return
#     explanation_rows = feature_df.sort_values("risk_score", ascending=False).reset_index(drop=True)
#     for _, row in explanation_rows.iterrows():
#         score = float(row.get("risk_score", 0) or 0)
#         band = risk_band(score)
#         title = f"{row.get('signal_area', 'Signal')} · {str(row.get('signal_name', '')).replace('_', ' ').title()}"
#         meta = f"{row.get('source', 'Unknown source')} / {row.get('region_scope', row.get('region', ''))}"
#         reason = str(row.get("score_reason") or "No score reason was returned by this collector.")
#         evidence = str(row.get("raw_reference") or row.get("signal_value") or "No raw reference available.")
#         action = str(row.get("recommended_action") or "Review this signal before using it in planning.")
#         formula = scoring_formula_for_row(row)
#         html = (
#             "<div class='score-explain-card'>"
#             "<div class='score-explain-head'>"
#             f"<div><div class='score-explain-title'>{escape(title)}</div><div class='score-explain-meta'>{escape(meta)}</div></div>"
#             f"<div class='score-number'>{score:.2f}<span>{escape(band)}</span></div>"
#             "</div>"
#             "<div class='score-explain-label'>Scoring rule</div>"
#             f"<div class='score-explain-text'>{escape(formula)}</div>"
#             "<div class='score-explain-label'>Why this score</div>"
#             f"<div class='score-explain-text'>{escape(reason)}</div>"
#             "<div class='score-explain-label'>Evidence used</div>"
#             f"<div class='score-explain-text'>{escape(evidence)}</div>"
#             "<div class='score-explain-label'>Planning action</div>"
#             f"<div class='score-explain-text'>{escape(action)}</div>"
#             "</div>"
#         )
#         st.markdown(html, unsafe_allow_html=True)


# BRIEF_SECTION_TITLES = {
#     "executive summary",
#     "top 3 insights",
#     "top three insights",
#     "top signal evidence",
#     "forecasting relevance",
#     "recommended actions",
#     "confidence and limitations",
#     "confidence limitations",
# }


# def normalize_brief_line(line: str) -> str:
#     normalized = str(line or "").strip()
#     normalized = re.sub(r"^\s*#{1,6}\s*", "", normalized)
#     normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
#     normalized = normalized.replace("**", "")
#     return normalized.strip()


# def clean_brief_heading(line: str) -> str:
#     heading = normalize_brief_line(line).rstrip(":").strip()
#     heading = re.sub(r"^\d+[\.)]\s*", "", heading).strip()
#     return heading


# def brief_section_heading(raw_line: str) -> str:
#     stripped = str(raw_line or "").strip()
#     if re.fullmatch(r"[-*_]{3,}", stripped):
#         return ""
#     normalized = normalize_brief_line(stripped)
#     title = clean_brief_heading(normalized)
#     simplified = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
#     if stripped.startswith("#") or (normalized.endswith(":") and len(normalized) <= 90):
#         return title
#     if simplified in BRIEF_SECTION_TITLES:
#         return title
#     return ""


# def brief_to_html(brief: str) -> str:
#     parts: List[str] = []
#     in_list = False
#     for raw_line in str(brief or "").splitlines():
#         line = raw_line.strip()
#         if not line or re.fullmatch(r"[-*_]{3,}", line):
#             if in_list:
#                 parts.append("</ul>")
#                 in_list = False
#             continue
#         normalized = normalize_brief_line(line)
#         heading = brief_section_heading(line)
#         bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
#         if heading:
#             if in_list:
#                 parts.append("</ul>")
#                 in_list = False
#             parts.append(f"<div class='brief-section-title'>{escape(heading)}</div>")
#         elif bullet_match:
#             if not in_list:
#                 parts.append("<ul class='brief-list'>")
#                 in_list = True
#             parts.append(f"<li>{escape(bullet_match.group(1))}</li>")
#         else:
#             if in_list:
#                 parts.append("</ul>")
#                 in_list = False
#             parts.append(f"<p>{escape(normalized)}</p>")
#     if in_list:
#         parts.append("</ul>")
#     return "".join(parts)


# def parse_brief_sections(brief: str) -> List[Dict[str, Any]]:
#     sections: List[Dict[str, Any]] = []
#     current = {"title": "Executive Summary", "items": []}
#     for raw_line in str(brief or "").splitlines():
#         line = raw_line.strip()
#         if not line or re.fullmatch(r"[-*_]{3,}", line):
#             continue
#         normalized = normalize_brief_line(line)
#         heading = brief_section_heading(line)
#         bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
#         if heading:
#             if current["items"]:
#                 sections.append(current)
#             current = {"title": heading, "items": []}
#         elif bullet_match:
#             current["items"].append({"kind": "bullet", "text": bullet_match.group(1)})
#         else:
#             current["items"].append({"kind": "text", "text": normalized})
#     if current["items"]:
#         sections.append(current)
#     return sections


# def brief_sections_to_html(brief: str) -> str:
#     sections = parse_brief_sections(brief)
#     if not sections:
#         return "<div class='brief-section-grid'><div class='brief-section-card primary'><div class='brief-section-title'>Executive Summary</div><p>No brief content was generated.</p></div></div>"
#     cards = []
#     for idx, section in enumerate(sections):
#         paragraphs = []
#         bullets = []
#         for item in section["items"]:
#             if item["kind"] == "bullet":
#                 bullets.append(f"<li>{escape(str(item['text']))}</li>")
#             else:
#                 paragraphs.append(f"<p>{escape(str(item['text']))}</p>")
#         body = "".join(paragraphs)
#         if bullets:
#             body += "<ul class='brief-list'>" + "".join(bullets) + "</ul>"
#         primary = " primary" if idx == 0 else ""
#         cards.append(
#             f"<div class='brief-section-card{primary}'>"
#             f"<div class='brief-section-title'>{escape(str(section['title']))}</div>"
#             f"{body}"
#             "</div>"
#         )
#     return "<div class='brief-section-grid'>" + "".join(cards) + "</div>"


# def render_executive_brief(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
#     brief_source = str(run.get("brief_source", "unknown"))
#     articles = run.get("articles", [])
#     llm_audit = run.get("llm_audit", {})
#     source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
#     top_label = "No signal"
#     avg_score_label = "0.00"
#     if not feature_df.empty:
#         top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
#         top_label = f"{top.get('signal_area', 'Signal')} {float(top.get('risk_score', 0) or 0):.2f}/10"
#         avg_score_label = f"{float(feature_df['risk_score'].mean()):.2f}"
#     articles_sent = llm_audit.get("articles_sent", min(len(articles), 8))
#     attempts = llm_audit.get("nvidia_attempts", [])
#     response_label = "not called"
#     if attempts:
#         response_label = f"{len(attempts)} attempt(s), {attempts[-1].get('elapsed_ms', 0)} ms last"
#     header_note = (
#         "NVIDIA grounded response" if llm_audit.get("sent_to_llm") else "Local deterministic summary using collected feature rows"
#     )
#     brief_source_label = "NVIDIA" if brief_source == "nvidia" else "Local fallback"
#     brief_status_note = llm_audit.get("fallback_reason") or "NVIDIA returned a grounded response."
#     html = (
#         "<div class='brief-shell'>"
#         "<div class='brief-header'>"
#         "<div class='brief-kicker'>Executive Brief</div>"
#         f"<div class='brief-title'>{escape(str(run.get('run_config', {}).get('retailer', retailer_label)))} External Signal Readout</div>"
#         f"<div class='brief-summary-text'>{escape(header_note)}. {escape(str(brief_status_note))}</div>"
#         "<div class='brief-meta-strip'>"
#         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Brief Source</div><div class='brief-meta-value'>{escape(brief_source_label)}</div></div>"
#         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Rows Grounded</div><div class='brief-meta-value'>{len(feature_df)} rows / {source_count} source(s)</div></div>"
#         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Average Score</div><div class='brief-meta-value'>{escape(avg_score_label)} / 10</div></div>"
#         f"<div class='brief-meta-chip'><div class='brief-meta-label'>AI Timing</div><div class='brief-meta-value'>{escape(response_label)}</div></div>"
#         "</div>"
#         f"<div class='brief-summary-text' style='margin-top:10px;'>Top evidence: {escape(top_label)}. Articles in context: {len(articles)} available, {articles_sent} sent.</div>"
#         "</div>"
#         f"{brief_sections_to_html(str(run.get('brief', '')))}"
#         "</div>"
#     )
#     st.markdown(html, unsafe_allow_html=True)


# def parse_lines(text: str) -> List[str]:
#     return [line.strip() for line in text.splitlines() if line.strip()]


# def build_default_news_keywords(retailer_name: str) -> str:
#     name = retailer_name.strip() or "Retailer"
#     return "\n".join(
#         [
#             f"{name} inflation",
#             f"{name} prices",
#             f"{name} store closures",
#             f"{name} recall",
#             "discount retail tariffs",
#             "Dollar General promotion",
#         ]
#     )


# def build_default_trends_keywords(retailer_name: str) -> str:
#     name = retailer_name.strip() or "Retailer"
#     return "\n".join(
#         [
#             f"{name} sales",
#             f"{name} coupons",
#             f"{name} near me",
#             f"{name} groceries",
#             "cheap groceries",
#         ]
#     )


# def retailer_initials(retailer_name: str) -> str:
#     words = [word for word in re.split(r"\s+", retailer_name.strip()) if word]
#     if not words:
#         return "AI"
#     return "".join(word[0].upper() for word in words[:2])


# st.session_state.setdefault("gnews_period", "7d")
# st.session_state.setdefault("max_news", 24)
# st.session_state.setdefault("fda_query", "product_description:(snacks OR candy OR beverages)")
# st.session_state.setdefault("fda_limit", 8)
# st.session_state.setdefault("weather_area", "TX")
# st.session_state.setdefault("weather_limit", 5)
# st.session_state.setdefault("apify_geo", "US")
# st.session_state.setdefault("apify_time_range", APIFY_SAFE_TIME_RANGE)
# st.session_state.setdefault("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)
# st.session_state.setdefault("apify_run_mode", "Skip Apify")
# st.session_state.setdefault("apify_live_confirm", False)
# st.session_state.setdefault("workbench_view", "Configure")
# if st.session_state.get("apify_time_range") not in APIFY_ALLOWED_TIME_RANGES:
#     st.session_state["apify_time_range"] = APIFY_SAFE_TIME_RANGE
# if st.session_state.pop("force_results_view", False):
#     st.session_state["workbench_view"] = "Results"
# if st.session_state.pop("reset_apify_live_confirm", False):
#     st.session_state["apify_run_mode"] = "Skip Apify"
#     st.session_state["apify_live_confirm"] = False


# with st.sidebar:
#     st.markdown(
#         "<div class='sidebar-brand'>"
#         "<div class='sidebar-brand-title'>Run Control</div>"
#         "<div class='sidebar-brand-copy'>Configure context, source coverage, and guarded API spend for the next intelligence run.</div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )

#     with st.expander("Credentials", expanded=False):
#         st.caption("Keys stay in this Streamlit session and are never written to evidence payloads.")
#         nvidia_key = st.text_input("NVIDIA API key", value=os.getenv("NVIDIA_API_KEY", ""), type="password")
#         nvidia_model = st.text_input("NVIDIA model", value=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL))
#         apify_token = st.text_input("Apify token", value=os.getenv("APIFY_API_TOKEN", ""), type="password")
#         bls_key = st.text_input("BLS API key optional", value=os.getenv("BLS_API_KEY", ""), type="password")
#         validate_button = st.button("Validate credentials", width="stretch")

#     with st.expander("Retail context", expanded=True):
#         retailer = st.text_input("Company / Retailer", value="", placeholder="Optional: enter a company or retailer")
#         region = st.text_input("Region", value="US")
#         country = st.selectbox("News country", ["US", "CA", "GB", "AE", "IN"], index=0)
#         language = st.selectbox("News language", ["en", "es", "fr", "ar", "hi"], index=0)

#     with st.expander("Signal sources", expanded=True):
#         use_gnews = st.checkbox("Retail news", value=True)
#         use_bls = st.checkbox("Inflation CPI", value=True)
#         use_fda = st.checkbox("Product recalls", value=True)
#         use_weather = st.checkbox("Weather risk", value=True)
#         use_apify = st.checkbox("Search demand", value=False)

#     sidebar_source_count = sum([use_gnews, use_bls, use_fda, use_weather, bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))])
#     st.markdown(
#         "<div class='sidebar-summary'>"
#         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Sources</div><div class='sidebar-summary-value'>{sidebar_source_count} enabled</div></div>"
#         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Brief</div><div class='sidebar-summary-value'>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</div></div>"
#         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Market</div><div class='sidebar-summary-value'>{escape(region)}</div></div>"
#         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Apify</div><div class='sidebar-summary-value'>{escape(st.session_state.get('apify_run_mode', 'Skip Apify'))}</div></div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )

#     if use_apify or apify_token.strip():
#         with st.expander("Apify spend guardrail", expanded=bool(use_apify)):
#             if apify_token.strip():
#                 if st.session_state.get("apify_live_confirm", False) and st.session_state.get("apify_run_mode") == "Skip Apify":
#                     st.session_state["apify_run_mode"] = "Run one live Apify call"
#                 st.radio(
#                     "Live mode",
#                     ["Skip Apify", "Run one live Apify call"],
#                     key="apify_run_mode",
#                     horizontal=False,
#                 )
#                 st.session_state["apify_live_confirm"] = st.session_state.get("apify_run_mode") == "Run one live Apify call"
#                 st.caption(f"{APIFY_SAFE_TIME_RANGE}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s). Resets after run.")
#                 if st.session_state.get("apify_run_mode") == "Run one live Apify call":
#                     st.success("Next run will call Apify once.")
#                 else:
#                     st.info("No Apify credits will be used.")
#             else:
#                 st.session_state["apify_run_mode"] = "Skip Apify"
#                 st.session_state["apify_live_confirm"] = False
#                 st.warning("Add an Apify token before allowing a live trends run.")

#     st.markdown("<div class='sidebar-divider'></div>", unsafe_allow_html=True)
#     run_button = st.button("Run intelligence", type="primary", width="stretch")
#     sidebar_status_slot = st.empty()


# retailer_label = retailer.strip() or "Retailer"
# previous_keyword_retailer = st.session_state.get("keyword_template_retailer")
# previous_news_template = build_default_news_keywords(previous_keyword_retailer or retailer_label)
# previous_trends_template = build_default_trends_keywords(previous_keyword_retailer or retailer_label)
# next_news_template = build_default_news_keywords(retailer_label)
# next_trends_template = build_default_trends_keywords(retailer_label)
# if "news_keywords_text" not in st.session_state or st.session_state.get("news_keywords_text") == previous_news_template:
#     st.session_state["news_keywords_text"] = next_news_template
# if "trends_keywords_text" not in st.session_state or st.session_state.get("trends_keywords_text") == previous_trends_template:
#     st.session_state["trends_keywords_text"] = next_trends_template
# st.session_state["keyword_template_retailer"] = retailer_label
# apify_source_active = bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))

# st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
# st.markdown(
#     "<div class='topbar'>"
#     f"<div class='tt'>Market Intelligence <span>/ {escape(retailer_label)} · External Signals</span></div>"
#     "<div class='tbadge'>AI Workbench</div>"
#     "<div style='margin-left:auto;display:flex;align-items:center;gap:8px;'>"
#     "<span class='ldot'></span>"
#     "<span style='font-size:10px;color:var(--t3);font-weight:800;'>Live API Mode</span>"
#     f"<div style='width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,var(--odk),var(--or));display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#fff;'>{escape(retailer_initials(retailer_label))}</div>"
#     "</div></div>",
#     unsafe_allow_html=True,
# )

# hero_left, hero_right = st.columns([2.2, 0.9], vertical_alignment="center")
# with hero_left:
#     st.markdown(
#         "<div class='hero-shell'>"
#         "<div class='hero-kicker'><span class='ldot'></span> External Signal Layer</div>"
#         f"<h1 class='hero-title'>{escape(retailer_label)} Market Intelligence Command Center</h1>"
#         "<p class='hero-copy'>A retail-grade workbench that turns news, CPI, recalls, weather alerts, search demand, and API health into forecast-ready features, composite risk scores, and buyer actions.</p>"
#         "</div>",
#         unsafe_allow_html=True,
#     )
# with hero_right:
#     active_sources = sum([use_gnews, use_bls, use_fda, use_weather, apify_source_active])
#     st.markdown(
#         "<div class='hero-side'>"
#         "<div class='hero-side-label'>Run Profile</div>"
#         f"<div class='hero-side-row'><span>Retailer</span><strong>{escape(retailer)}</strong></div>"
#         f"<div class='hero-side-row'><span>Region</span><strong>{escape(region)}</strong></div>"
#         f"<div class='hero-side-row'><span>Sources</span><strong>{active_sources} enabled</strong></div>"
#         f"<div class='hero-side-row'><span>LLM</span><strong>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</strong></div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )

# view = st.segmented_control(
#     "Workbench view",
#     ["Configure", "Results", "Evidence Audit", "Raw Data"],
#     required=True,
#     label_visibility="collapsed",
#     key="workbench_view",
#     width="content",
# )

# run_status_slot = st.empty()
# if not run_button and st.session_state.get("run"):
#     last_run = st.session_state["run"]
#     last_states = {}
#     for key, result in last_run.get("results", {}).items():
#         status = result.get("status", "unknown")
#         last_states[key.upper()] = {
#             "status": status if status in {"success", "failed", "skipped"} else "queued",
#             "detail": result.get("error") or f"{len(result.get('rows', []))} feature row(s)",
#         }
#     last_llm_audit = last_run.get("llm_audit", {})
#     if last_llm_audit:
#         last_states["BRIEF"] = {
#             "status": "success",
#             "detail": "NVIDIA generated the brief" if last_run.get("brief_source") == "nvidia" else f"Fallback brief generated: {last_llm_audit.get('fallback_reason', 'NVIDIA unavailable')}",
#         }
#     if last_states:
#         render_run_monitor(run_status_slot, f"Last run completed at {last_run.get('timestamp', '')}", last_states, 100)
#     render_sidebar_status(sidebar_status_slot, f"Last run complete: {last_run.get('timestamp', '')}", "success")
# elif not run_button:
#     render_sidebar_status(sidebar_status_slot, "Status: idle. Apify runs only when token is present and live mode is set to Run one live Apify call.", "info")

# if view == "Configure":
#     source_specs = [
#         {
#             "name": "GNews/RSS",
#             "status": "Active" if use_gnews else "Off",
#             "signal": "Retail news and competitor events",
#             "feature": "retail_news_event_score",
#             "owner": "Category Manager",
#             "output": "Market Event Risk",
#         },
#         {
#             "name": "BLS CPI",
#             "status": "Active" if use_bls else "Off",
#             "signal": "Headline and category inflation",
#             "feature": "cpi_pressure_features",
#             "owner": "Demand Planning",
#             "output": "Value Basket Pressure",
#         },
#         {
#             "name": "openFDA",
#             "status": "Active" if use_fda else "Off",
#             "signal": "Food recall enforcement records",
#             "feature": "recall_exposure_score",
#             "owner": "Compliance + Buyer",
#             "output": "Safety And Compliance Risk",
#         },
#         {
#             "name": "NOAA Weather",
#             "status": "Active" if use_weather else "Off",
#             "signal": "State weather alerts",
#             "feature": "state_weather_disruption_score",
#             "owner": "Supply Chain",
#             "output": "Route And Store Risk",
#         },
#         {
#             "name": "Apify Trends",
#             "status": "Active" if apify_source_active else "Off",
#             "signal": "Search interest by region",
#             "feature": "google_trends_interest_score",
#             "owner": "Demand Planning",
#             "output": "Demand Interest Spike",
#         },
#     ]
#     readiness_score = min(
#         100,
#         35
#         + (active_sources * 10)
#         + (10 if retailer_label and region else 0)
#         + (5 if st.session_state.get("apify_run_mode", "Skip Apify") == "Skip Apify" else 10),
#     )
#     readiness = {
#         "Retail context": f"{retailer_label} / {region}",
#         "Public sources": f"{active_sources} enabled",
#         "Brief mode": "NVIDIA" if nvidia_key.strip() else "Fallback",
#         "Apify guardrail": st.session_state.get("apify_run_mode", "Skip Apify"),
#     }
#     readiness_rows = "".join(
#         f"<div class='readiness-row'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
#         for label, value in readiness.items()
#     )
#     control_cards = [
#         ("Evidence trace", "ok", "Raw payloads and normalized rows remain inspectable in Evidence Audit."),
#         ("Apify spend", "ok" if st.session_state.get("apify_run_mode", "Skip Apify") == "Skip Apify" else "warn", "Live trend calls require explicit guarded mode and reset after the run."),
#         ("Internal data", "warn", "POS, inventory, product hierarchy, and store/DC data are not connected yet."),
#     ]
#     control_html = "".join(
#         f"<div class='control-check {state}'><div class='control-check-title'>{escape(title)}</div><div class='control-check-copy'>{escape(copy)}</div></div>"
#         for title, state, copy in control_cards
#     )
#     st.markdown(
#         "<div class='config-shell'>"
#         "<div class='config-panel emphasis'>"
#         "<div class='config-eyebrow'>Run Blueprint</div>"
#         f"<div class='config-title'>{escape(retailer_label)} market intelligence run</div>"
#         "<div class='config-copy'>A governed setup surface for turning public market signals into category, owner, forecast-feature, and action-ready outputs.</div>"
#         f"<div class='readiness-score'><div><div class='readiness-score-value'>{readiness_score}</div><div class='readiness-score-label'>Readiness Score</div></div></div>"
#         f"<div class='readiness-list'>{readiness_rows}</div>"
#         "</div>"
#         "<div class='config-panel'>"
#         "<div class='config-eyebrow'>Controls</div>"
#         "<div class='config-title'>Governed by evidence, cost guardrails, and scope limits</div>"
#         "<div class='config-copy'>The run can support buyer and planner discussion, but it does not claim internal category performance until internal POS, inventory, product hierarchy, vendor, promotion, and store/DC data are connected.</div>"
#         f"<div class='control-grid'>{control_html}</div>"
#         "</div>"
#         "</div>",
#         unsafe_allow_html=True,
#     )

#     st.markdown('<div class="small-header">Source To Feature Routing</div>', unsafe_allow_html=True)
#     routing_cards = []
#     for spec in source_specs:
#         status = spec["status"]
#         status_class = "pill-high" if status == "Active" else "pill-low"
#         tile_state = "active" if status == "Active" else "off"
#         routing_cards.append(
#             f"<div class='routing-card {tile_state}'>"
#             f"<div class='routing-source'>{escape(spec['name'])} <span class='pill {status_class}' style='margin-left:6px;margin-top:0;'>{escape(status)}</span></div>"
#             f"<div class='routing-line'><div class='routing-label'>Signal</div><div class='routing-value'>{escape(spec['signal'])}</div></div>"
#             f"<div class='routing-line'><div class='routing-label'>Forecast Feature</div><div class='routing-value'>{escape(spec['feature'])}</div></div>"
#             f"<div class='routing-line'><div class='routing-label'>Owner / KPI</div><div class='routing-value'>{escape(spec['owner'])} / {escape(spec['output'])}</div></div>"
#             "</div>"
#         )
#     st.markdown("<div class='routing-grid'>" + "".join(routing_cards) + "</div>", unsafe_allow_html=True)

#     setup_tab, collector_tab, governance_tab = st.tabs(["Scope", "Collector Tuning", "Governance"])
#     with setup_tab:
#         st.markdown(
#             "<div class='config-tab-note'>Scope terms decide what the external collectors look for. Keep them readable for business review: retailer, competitor, category, recall, pricing, weather, and seasonal language.</div>",
#             unsafe_allow_html=True,
#         )
#         keyword_guidance = [
#             ("Retailer terms", "Anchor the search to the selected retailer so news and trend results stay relevant to this business context."),
#             ("Competitor terms", "Capture pressure from Dollar General, Walmart, Five Below, and other value retailers that can affect assortment and pricing."),
#             ("Category terms", "Add snacks, candy, household, seasonal, school, party, or consumables to connect signals to buyer-owned categories."),
#             ("Risk terms", "Recall, closure, inflation, tariff, weather, and promotion terms help surface early planning risks."),
#             ("Trend terms", "Coupon, near me, sales, groceries, and seasonal words influence the Apify Google Trends demand signal."),
#             ("Governance", "Changing keywords changes what evidence is pulled; Evidence Audit records the exact keyword scope for each run."),
#         ]
#         st.markdown(
#             "<div class='control-grid'>"
#             + "".join(
#                 f"<div class='control-check ok'><div class='control-check-title'>{escape(title)}</div><div class='control-check-copy'>{escape(copy)}</div></div>"
#                 for title, copy in keyword_guidance
#             )
#             + "</div>",
#             unsafe_allow_html=True,
#         )
#         k_left, k_right = st.columns(2)
#         with k_left:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("Retail News Keywords")
#             st.text_area("GNews keywords", key="news_keywords_text", height=210, label_visibility="collapsed")
#             st.markdown("</div>", unsafe_allow_html=True)
#         with k_right:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("Search Demand Keywords")
#             st.text_area("Apify Google Trends keywords", key="trends_keywords_text", height=210, label_visibility="collapsed")
#             st.markdown("</div>", unsafe_allow_html=True)

#     with collector_tab:
#         st.markdown(
#             "<div class='config-tab-note'>Tuning controls runtime, cost, and evidence volume. Defaults stay conservative so the MVP is explainable and does not over-consume Apify credits.</div>",
#             unsafe_allow_html=True,
#         )
#         c1, c2, c3 = st.columns(3)
#         with c1:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("News")
#             st.selectbox("GNews period", ["1d", "7d", "30d", "3m"], key="gnews_period")
#             st.slider("Max news results", min_value=5, max_value=60, step=1, key="max_news")
#             st.markdown("</div>", unsafe_allow_html=True)
#         with c2:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("Recall + Weather")
#             st.text_input("FDA recall search", key="fda_query")
#             st.slider("FDA recall limit", min_value=1, max_value=25, key="fda_limit")
#             st.text_input("NOAA weather area", key="weather_area", help="Two-letter US state code, such as TX, NY, CA.")
#             st.slider("NOAA alert limit", min_value=1, max_value=25, key="weather_limit")
#             st.markdown("</div>", unsafe_allow_html=True)
#         with c3:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("Search Demand")
#             st.text_input("Apify geo", key="apify_geo")
#             st.selectbox(
#                 "Apify time range",
#                 APIFY_ALLOWED_TIME_RANGES,
#                 key="apify_time_range",
#                 help="The default now 7-d window is the safest weekly actor value. Longer ranges can use more Apify credits.",
#             )
#             st.slider("Apify max keywords", min_value=1, max_value=APIFY_HARD_KEYWORD_LIMIT, key="apify_max_keywords")
#             st.caption("Live mode is controlled in the sidebar guardrail.")
#             st.markdown("</div>", unsafe_allow_html=True)

#     with governance_tab:
#         st.markdown(
#             "<div class='config-tab-note'>Governance makes the demo credible: source status is separate from brief status, fallback is labeled, and raw collector payloads remain inspectable in Evidence Audit and Raw Data.</div>",
#             unsafe_allow_html=True,
#         )
#         g1, g2 = st.columns([1, 1])
#         with g1:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("Agent Flow")
#             render_workflow_strip()
#             st.markdown("</div>", unsafe_allow_html=True)
#         with g2:
#             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
#             st.subheader("Glossary")
#             render_glossary()
#             st.markdown("</div>", unsafe_allow_html=True)


# if validate_button:
#     with st.spinner("Validating credentials..."):
#         n_ok, n_msg = validate_nvidia(nvidia_key.strip(), nvidia_model.strip())
#         a_ok, a_msg = validate_apify(apify_token.strip())
#     st.session_state["validation"] = {"nvidia": (n_ok, n_msg), "apify": (a_ok, a_msg)}

# if "validation" in st.session_state:
#     n_ok, n_msg = st.session_state["validation"]["nvidia"]
#     a_ok, a_msg = st.session_state["validation"]["apify"]
#     st.info(f"NVIDIA: {'Connected' if n_ok else 'Not connected'} - {n_msg}")
#     st.info(f"Apify: {'Connected' if a_ok else 'Not connected'} - {a_msg}")


# if run_button:
#     news_keywords = parse_lines(st.session_state.get("news_keywords_text", build_default_news_keywords(retailer_label)))
#     trends_keywords = parse_lines(st.session_state.get("trends_keywords_text", build_default_trends_keywords(retailer_label)))
#     apify_token_value = apify_token.strip()
#     apify_run_mode = st.session_state.get("apify_run_mode", "Skip Apify")
#     apify_time_range = st.session_state.get("apify_time_range", APIFY_SAFE_TIME_RANGE)
#     if apify_time_range not in APIFY_ALLOWED_TIME_RANGES or not apify_time_range:
#         apify_time_range = APIFY_SAFE_TIME_RANGE
#     apify_live_confirmed = bool(apify_token_value and apify_run_mode == "Run one live Apify call")
#     apify_requested = bool(use_apify or apify_live_confirmed)
#     run_config = {
#         "retailer": retailer.strip() or "Retailer",
#         "region": region,
#         "country": country,
#         "language": language,
#         "enabled_sources": {"gnews": use_gnews, "bls": use_bls, "fda": use_fda, "weather": use_weather, "apify": apify_requested},
#         "use_gnews": use_gnews,
#         "use_bls": use_bls,
#         "use_fda": use_fda,
#         "use_weather": use_weather,
#         "use_apify": apify_requested,
#         "news_keywords": news_keywords,
#         "trends_keywords": trends_keywords[:APIFY_HARD_KEYWORD_LIMIT],
#         "gnews_period": st.session_state.get("gnews_period", "7d"),
#         "max_news": int(st.session_state.get("max_news", 24)),
#         "fda_query": st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
#         "fda_limit": int(st.session_state.get("fda_limit", 8)),
#         "weather_area": normalize_weather_area(st.session_state.get("weather_area", "TX")),
#         "weather_limit": int(st.session_state.get("weather_limit", 5)),
#         "bls_series": BLS_CPI_SERIES,
#         "apify_token_present": bool(apify_token_value),
#         "apify_run_mode": apify_run_mode,
#         "apify_geo": st.session_state.get("apify_geo", "US"),
#         "apify_time_range": apify_time_range,
#         "apify_max_keywords": int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
#         "apify_live_confirm": apify_live_confirmed,
#     }
#     results: Dict[str, Dict[str, Any]] = {}
#     all_rows: List[Dict[str, Any]] = []
#     all_articles: List[Dict[str, Any]] = []

#     steps = [
#         ("gnews", use_gnews),
#         ("bls", use_bls),
#         ("fda", use_fda),
#         ("weather", use_weather),
#         ("apify", apify_requested),
#     ]
#     active_steps = [step for step in steps if step[1]]
#     total = max(1, len(active_steps))
#     completed = 0
#     collector_states: Dict[str, Dict[str, str]] = {
#         "GNEWS": {"status": "queued" if use_gnews else "skipped", "detail": "Retail news collector" if use_gnews else "Disabled"},
#         "BLS": {"status": "queued" if use_bls else "skipped", "detail": "CPI collector" if use_bls else "Disabled"},
#         "FDA": {"status": "queued" if use_fda else "skipped", "detail": "Recall collector" if use_fda else "Disabled"},
#         "WEATHER": {"status": "queued" if use_weather else "skipped", "detail": "NOAA alert collector" if use_weather else "Disabled"},
#         "APIFY": {"status": "queued" if apify_requested else "skipped", "detail": f"Trends collector; mode: {apify_run_mode}" if apify_requested else "Disabled"},
#         "BRIEF": {"status": "queued", "detail": "NVIDIA grounded brief or local fallback"},
#     }
#     render_run_monitor(run_status_slot, "Starting collectors", collector_states, 2)
#     render_sidebar_status(sidebar_status_slot, "Running: starting collectors...", "info")

#     if use_gnews:
#         collector_states["GNEWS"] = {"status": "running", "detail": "Collecting and deduplicating retail news"}
#         render_run_monitor(run_status_slot, "Collecting retail news", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, "Running: collecting retail news...", "info")
#         results["gnews"] = collect_gnews(
#             news_keywords,
#             country,
#             language,
#             st.session_state.get("gnews_period", "7d"),
#             int(st.session_state.get("max_news", 24)),
#             retailer.strip() or "Retailer",
#         )
#         all_rows.extend(results["gnews"].get("rows", []))
#         all_articles.extend(results["gnews"].get("items", []))
#         completed += 1
#         gnews_status = results["gnews"].get("status", "failed")
#         collector_states["GNEWS"] = {
#             "status": "success" if gnews_status == "success" else "failed" if gnews_status == "failed" else "skipped",
#             "detail": results["gnews"].get("error") or f"{len(results['gnews'].get('items', []))} article(s), {len(results['gnews'].get('rows', []))} feature row(s)",
#         }
#         render_run_monitor(run_status_slot, "Retail news complete", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"GNews {collector_states['GNEWS']['status']}: {collector_states['GNEWS']['detail']}", "warning" if gnews_status != "success" else "info")

#     if use_bls:
#         collector_states["BLS"] = {"status": "running", "detail": "Collecting headline and category CPI"}
#         render_run_monitor(run_status_slot, "Collecting CPI inflation", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, "Running: collecting BLS CPI...", "info")
#         results["bls"] = collect_bls_cpi(bls_key.strip(), retailer.strip() or "Retailer")
#         all_rows.extend(results["bls"].get("rows", []))
#         completed += 1
#         bls_status = results["bls"].get("status", "failed")
#         collector_states["BLS"] = {
#             "status": "success" if bls_status == "success" else "failed",
#             "detail": results["bls"].get("error") or f"{len(results['bls'].get('rows', []))} CPI feature row(s)",
#         }
#         render_run_monitor(run_status_slot, "CPI collection complete", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"BLS {collector_states['BLS']['status']}: {collector_states['BLS']['detail']}", "warning" if bls_status != "success" else "info")

#     if use_fda:
#         collector_states["FDA"] = {"status": "running", "detail": "Collecting food recall records"}
#         render_run_monitor(run_status_slot, "Collecting FDA recalls", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, "Running: collecting FDA recalls...", "info")
#         results["fda"] = collect_fda_recalls(
#             st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
#             int(st.session_state.get("fda_limit", 8)),
#             retailer.strip() or "Retailer",
#         )
#         all_rows.extend(results["fda"].get("rows", []))
#         completed += 1
#         fda_status = results["fda"].get("status", "failed")
#         collector_states["FDA"] = {
#             "status": "success" if fda_status == "success" else "failed",
#             "detail": results["fda"].get("error") or f"{len(results['fda'].get('items', []))} recall item(s), {len(results['fda'].get('rows', []))} feature row(s)",
#         }
#         render_run_monitor(run_status_slot, "FDA recall collection complete", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"FDA {collector_states['FDA']['status']}: {collector_states['FDA']['detail']}", "warning" if fda_status != "success" else "info")

#     if use_weather:
#         weather_area = normalize_weather_area(st.session_state.get("weather_area", "TX"))
#         collector_states["WEATHER"] = {"status": "running", "detail": f"Collecting active NOAA alerts for {weather_area}"}
#         render_run_monitor(run_status_slot, "Collecting weather alerts", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"Running: collecting NOAA weather alerts for {weather_area}...", "info")
#         results["weather"] = collect_weather_alerts(
#             weather_area,
#             int(st.session_state.get("weather_limit", 5)),
#             retailer.strip() or "Retailer",
#         )
#         all_rows.extend(results["weather"].get("rows", []))
#         completed += 1
#         weather_status = results["weather"].get("status", "failed")
#         collector_states["WEATHER"] = {
#             "status": "success" if weather_status == "success" else "failed",
#             "detail": results["weather"].get("error") or f"{len(results['weather'].get('items', []))} active alert item(s), {len(results['weather'].get('rows', []))} feature row(s)",
#         }
#         render_run_monitor(run_status_slot, "Weather alert collection complete", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"Weather {collector_states['WEATHER']['status']}: {collector_states['WEATHER']['detail']}", "warning" if weather_status != "success" else "info")

#     if apify_requested and not apify_token_value:
#         results["apify"] = {
#             "status": "skipped",
#             "source": "Apify Trends",
#             "error": "Apify was enabled but no Apify token was provided. No Apify credits were used.",
#             "raw": None,
#             "rows": [],
#             "items": [],
#         }
#         completed += 1
#         collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
#         render_run_monitor(run_status_slot, "Apify skipped: token missing", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, "Apify skipped: token missing, no credits used.", "warning")

#     elif apify_requested and not apify_live_confirmed:
#         results["apify"] = {
#             "status": "skipped",
#             "source": "Apify Trends",
#             "error": f"Apify was enabled but Apify live mode is '{apify_run_mode}'. Select 'Run one live Apify call' in the sidebar to spend one guarded Apify run. No Apify credits were used.",
#             "raw": None,
#             "rows": [],
#             "items": [],
#         }
#         completed += 1
#         collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
#         render_run_monitor(run_status_slot, "Apify skipped without spending credits", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, "Apify skipped: no credits used.", "warning")

#     elif apify_requested and apify_live_confirmed:
#         collector_states["APIFY"] = {"status": "running", "detail": f"One guarded live run: {apify_time_range}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s)"}
#         render_run_monitor(run_status_slot, "Collecting Google Trends via Apify", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"Running: Apify Trends live call ({apify_time_range}, max {APIFY_HARD_KEYWORD_LIMIT} keywords)...", "warning")
#         results["apify"] = collect_apify_trends(
#             apify_token_value,
#             trends_keywords,
#             st.session_state.get("apify_geo", "US"),
#             apify_time_range,
#             retailer.strip() or "Retailer",
#             int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
#         )
#         all_rows.extend(results["apify"].get("rows", []))
#         completed += 1
#         st.session_state["reset_apify_live_confirm"] = True
#         apify_status = results["apify"].get("status", "failed")
#         collector_states["APIFY"] = {
#             "status": "success" if apify_status == "success" else "failed" if apify_status == "failed" else "skipped",
#             "detail": results["apify"].get("error") or f"{len(results['apify'].get('items', []))} trends row(s)",
#         }
#         render_run_monitor(run_status_slot, "Apify collection complete", collector_states, int((completed / total) * 100))
#         render_sidebar_status(sidebar_status_slot, f"Apify {collector_states['APIFY']['status']}: {collector_states['APIFY']['detail']}", "warning" if apify_status != "success" else "info")

#     collector_states["BRIEF"] = {"status": "running", "detail": "Calling NVIDIA when available, with local fallback ready"}
#     render_run_monitor(run_status_slot, "Generating intelligence brief", collector_states, 98)
#     render_sidebar_status(sidebar_status_slot, "Running: generating executive brief...", "info")
#     feature_df = pd.DataFrame(all_rows)
#     if not feature_df.empty:
#         feature_df["retailer"] = retailer.strip() or "Retailer"
#         if region:
#             feature_df["selected_market"] = region
#         feature_df = enrich_feature_rows_for_retailer(feature_df, retailer.strip() or "Retailer")
#     brief, brief_source, llm_audit = generate_nvidia_brief(nvidia_key.strip(), nvidia_model.strip(), feature_df, all_articles, retailer, region)
#     collector_states["BRIEF"] = {
#         "status": "success",
#         "detail": "NVIDIA generated the brief" if brief_source == "nvidia" else f"Fallback brief generated: {llm_audit.get('fallback_reason', 'NVIDIA unavailable')}",
#     }
#     render_run_monitor(run_status_slot, "Run complete", collector_states, 100)

#     if st.session_state.get("run"):
#         st.session_state["previous_run"] = st.session_state["run"]

#     st.session_state["run"] = {
#         "timestamp": utc_now(),
#         "run_config": run_config,
#         "results": results,
#         "feature_df": feature_df,
#         "articles": all_articles,
#         "brief": brief,
#         "brief_source": brief_source,
#         "llm_audit": llm_audit,
#     }
#     render_sidebar_status(sidebar_status_slot, "Run complete. Opening Results...", "success")
#     st.session_state["force_results_view"] = True
#     st.session_state["last_collector_states"] = collector_states
#     st.rerun()


# if view == "Results":
#     run = st.session_state.get("run")
#     if not run:
#         st.markdown(
#             "<div class='empty-console'>"
#             "<div>"
#             "<div class='hero-kicker'>Results Command Center</div>"
#             "<div class='empty-title'>No intelligence run yet.</div>"
#             "<div class='empty-body'>Run the governed signal pipeline to populate decision cards, Retailer impact mapping, score explanations, source evidence, and the executive brief.</div>"
#             "</div>"
#             "<div class='hero-side' style='min-width:260px;'>"
#             "<div class='hero-side-label'>Output Model</div>"
#             "<div class='hero-side-row'><span>Decisions</span><strong>Owner actions</strong></div>"
#             "<div class='hero-side-row'><span>Evidence</span><strong>Live API rows</strong></div>"
#             "<div class='hero-side-row'><span>Brief</span><strong>NVIDIA / fallback</strong></div>"
#             "</div>"
#             "</div>",
#             unsafe_allow_html=True,
#         )
#         render_workflow_strip()
#     else:
#         feature_df = run["feature_df"]
#         if not feature_df.empty and "dollar_tree_category" not in feature_df.columns:
#             feature_df = enrich_feature_rows_for_retailer(feature_df, str(run.get("run_config", {}).get("retailer", retailer_label)))
#             run["feature_df"] = feature_df
#         render_results_command_header(run, feature_df)
#         if feature_df.empty:
#             st.markdown('<div class="small-header">Run Summary</div>', unsafe_allow_html=True)
#             cols = st.columns(4)
#             for col, title in zip(cols, ["Signals", "Avg Score", "Highest Score", "Brief"]):
#                 with col:
#                     render_metric_card(title, "0", "No successful feature rows yet.")
#         else:
#             avg_score = round(float(feature_df["risk_score"].mean()), 2)
#             top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
#             dollar_tree_scores = compute_dollar_tree_kpis(feature_df)
#             command_tab, explain_tab, evidence_tab, brief_tab = st.tabs(["Command Center", "Explainability", "Evidence And Export", "Executive Brief"])

#             with command_tab:
#                 st.markdown('<div class="small-header">Trust And Evidence Status</div>', unsafe_allow_html=True)
#                 render_trust_panel(run, feature_df)

#                 st.markdown('<div class="small-header">Run Summary Metrics</div>', unsafe_allow_html=True)
#                 cols = st.columns(4)
#                 with cols[0]:
#                     render_metric_card("Signals", str(len(feature_df)), "Forecast-ready rows generated.")
#                 with cols[1]:
#                     render_metric_card("Average Score", str(avg_score), f"{risk_band(avg_score)} overall signal intensity.")
#                 with cols[2]:
#                     render_metric_card("Top Signal", str(top["risk_score"]), str(top["signal_area"]), str(top["confidence"]))
#                 with cols[3]:
#                     render_metric_card("News Articles", str(len(run["articles"])), "Deduplicated retail news items.")

#                 st.markdown('<div class="small-header">Retail Enterprise KPIs</div>', unsafe_allow_html=True)
#                 cscore_cols = st.columns(4)
#                 for col, (name, score) in zip(cscore_cols, dollar_tree_scores.items()):
#                     with col:
#                         render_metric_card(name, str(score), f"{risk_band(score)} priority for planning.")

#                 st.markdown('<div class="small-header">Decision Summary</div>', unsafe_allow_html=True)
#                 render_decision_summary(feature_df, run["results"])

#                 st.markdown('<div class="small-header">Previous Run Comparison</div>', unsafe_allow_html=True)
#                 render_previous_run_comparison(feature_df, st.session_state.get("previous_run"))

#                 st.markdown('<div class="small-header">Retail Impact Matrix</div>', unsafe_allow_html=True)
#                 render_dollar_tree_impact_matrix(feature_df)

#                 st.markdown('<div class="small-header">Internal Validation Agent</div>', unsafe_allow_html=True)
#                 render_internal_validation_agent(feature_df)

#             with explain_tab:
#                 st.markdown('<div class="small-header">How The Agent Explains A Signal</div>', unsafe_allow_html=True)
#                 render_explainability_ladder()

#                 st.markdown('<div class="small-header">Scenario Simulator</div>', unsafe_allow_html=True)
#                 render_scenario_simulator(feature_df)

#                 st.markdown('<div class="small-header">Signal Scores</div>', unsafe_allow_html=True)
#                 render_score_chart(feature_df)

#                 st.markdown('<div class="small-header">Score Explainability</div>', unsafe_allow_html=True)
#                 render_score_explainability(feature_df)

#             with evidence_tab:
#                 st.markdown('<div class="small-header">Forecast Feature Table</div>', unsafe_allow_html=True)
#                 st.dataframe(feature_df, width="stretch", hide_index=True)

#                 csv_data = feature_df.to_csv(index=False).encode("utf-8")
#                 json_data = json.dumps(feature_df.to_dict(orient="records"), indent=2).encode("utf-8")
#                 c1, c2 = st.columns([1, 1])
#                 with c1:
#                     st.download_button("Download forecast_features.csv", csv_data, "forecast_features.csv", "text/csv", width="stretch")
#                 with c2:
#                     st.download_button("Download normalized_signals.json", json_data, "normalized_signals.json", "application/json", width="stretch")

#                 with st.expander("Glossary and interpretation guide", expanded=False):
#                     render_glossary()

#             with brief_tab:
#                 render_executive_brief(run, feature_df)


# if view == "Evidence Audit":
#     run = st.session_state.get("run")
#     if not run:
#         st.markdown(
#             "<div class='empty-console'>"
#             "<div>"
#             "<div class='hero-kicker'>Evidence Audit</div>"
#             "<div class='empty-title'>No run evidence yet.</div>"
#             "<div class='empty-body'>Run the governed signal pipeline to populate provenance, source health, mock-data status, normalized signal reasoning, LLM prompt trace, payload hash, and raw API evidence.</div>"
#             "</div>"
#             "<div class='hero-side' style='min-width:260px;'>"
#             "<div class='hero-side-label'>Audit Model</div>"
#             "<div class='hero-side-row'><span>Provenance</span><strong>Source health</strong></div>"
#             "<div class='hero-side-row'><span>Trace</span><strong>Prompt + payload</strong></div>"
#             "<div class='hero-side-row'><span>Evidence</span><strong>Raw API data</strong></div>"
#             "</div>"
#             "</div>",
#             unsafe_allow_html=True,
#         )
#     else:
#         feature_df = run.get("feature_df", pd.DataFrame())
#         run_config = run.get(
#             "run_config",
#             {
#                 "retailer": retailer_label,
#                 "region": region,
#                 "country": country,
#                 "language": language,
#                 "enabled_sources": {key: key in run.get("results", {}) for key in SOURCE_ORDER},
#             },
#         )
#         if not feature_df.empty and "dollar_tree_category" not in feature_df.columns:
#             feature_df = enrich_feature_rows_for_retailer(feature_df, str(run_config.get("retailer", retailer_label)))
#             run["feature_df"] = feature_df
#         llm_audit = run.get("llm_audit") or build_base_llm_audit(
#             feature_df,
#             run.get("articles", []),
#             run_config.get("retailer", retailer_label),
#             run_config.get("region", region),
#             nvidia_model.strip() or DEFAULT_NVIDIA_MODEL,
#         )
#         llm_audit["brief_source"] = run.get("brief_source", llm_audit.get("brief_source", "unknown"))
#         evidence_records = build_collector_evidence(run.get("results", {}), run_config)
#         evidence_df = pd.DataFrame(evidence_records)
#         mock_used = any_mock_used(run.get("results", {}), llm_audit)
#         pulled_sources = int((evidence_df["raw_records_pulled"] > 0).sum()) if not evidence_df.empty else 0
#         raw_records = int(evidence_df["raw_records_pulled"].sum()) if not evidence_df.empty else 0

#         evidence_bundle = {
#             "timestamp": run.get("timestamp"),
#             "mock_data_used": mock_used,
#             "run_config": run_config,
#             "collector_evidence": evidence_records,
#             "normalized_features": feature_df.to_dict(orient="records") if not feature_df.empty else [],
#             "llm_audit": llm_audit,
#             "results": {
#                 name: {
#                     "status": result.get("status"),
#                     "source": result.get("source"),
#                     "error": result.get("error"),
#                     "rows": result.get("rows", []),
#                     "items": result.get("items", []),
#                     "raw": result.get("raw"),
#                 }
#                 for name, result in run.get("results", {}).items()
#             },
#         }

#         render_audit_command_header(run, mock_used, raw_records, pulled_sources, llm_audit, feature_df)
#         render_audit_banner(mock_used, str(run.get("brief_source", "unknown")))

#         provenance_tab, normalized_tab, llm_tab, raw_tab = st.tabs(["Provenance", "Normalized Signals", "LLM Trace", "Raw Payloads"])

#         with provenance_tab:
#             st.markdown('<div class="small-header">Chain Of Custody</div>', unsafe_allow_html=True)
#             render_audit_lineage()

#             c1, c2, c3, c4 = st.columns(4)
#             with c1:
#                 render_audit_card("Mock Data Used", "Yes" if mock_used else "No", "Collector rows are marked per run result.")
#             with c2:
#                 render_audit_card("Raw Records", str(raw_records), f"{pulled_sources} source(s) returned inspectable data.")
#             with c3:
#                 render_audit_card(
#                     "Rows Sent To Brief",
#                     f"{llm_audit.get('feature_rows_sent', 0)} / {llm_audit.get('feature_rows_available', 0)}",
#                     "Feature payload is capped for concise LLM context.",
#                 )
#             with c4:
#                 render_audit_card(
#                     "LLM Call",
#                     "NVIDIA" if llm_audit.get("sent_to_llm") else "No external LLM",
#                     str(llm_audit.get("fallback_reason") or "NVIDIA analyzed the shown payload."),
#                 )

#             st.markdown('<div class="small-header">Collector Evidence Table</div>', unsafe_allow_html=True)
#             st.dataframe(evidence_df, width="stretch", hide_index=True)

#             st.download_button(
#                 "Download evidence_audit.json",
#                 json.dumps(evidence_bundle, indent=2, default=str).encode("utf-8"),
#                 "evidence_audit.json",
#                 "application/json",
#                 width="stretch",
#             )

#         with normalized_tab:
#             st.markdown('<div class="small-header">Normalized Feature Rows And Score Reasoning</div>', unsafe_allow_html=True)
#             if feature_df.empty:
#                 st.info("No normalized feature rows were generated. Check collector statuses and errors above.")
#             else:
#                 preferred_cols = [
#                     "source",
#                     "signal_area",
#                     "signal_name",
#                     "dollar_tree_category",
#                     "enterprise_kpi",
#                     "planning_owner",
#                     "demand_direction",
#                     "region",
#                     "region_scope",
#                     "signal_value",
#                     "risk_score",
#                     "confidence",
#                     "score_reason",
#                     "impact_hypothesis",
#                     "business_impact",
#                     "recommended_action",
#                     "internal_data_needed",
#                     "raw_reference",
#                 ]
#                 visible_cols = [col for col in preferred_cols if col in feature_df.columns]
#                 st.dataframe(feature_df[visible_cols], width="stretch", hide_index=True)

#             st.markdown('<div class="small-header">Score Explainability Cards</div>', unsafe_allow_html=True)
#             render_score_explainability(feature_df)

#         with llm_tab:
#             st.markdown('<div class="small-header">LLM Analysis Trace</div>', unsafe_allow_html=True)
#             trace_cols = st.columns(4)
#             with trace_cols[0]:
#                 render_metric_card("Brief Source", "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback", "NVIDIA if sent; fallback if local rules were used.")
#             with trace_cols[1]:
#                 render_metric_card("Payload Hash", str(llm_audit.get("payload_hash_sha256", ""))[:12], "SHA-256 fingerprint of features/articles payload.")
#             with trace_cols[2]:
#                 render_metric_card("Articles Sent", str(llm_audit.get("articles_sent", 0)), f"{llm_audit.get('articles_available', 0)} available.")
#             with trace_cols[3]:
#                 render_metric_card("Fallback Used", "Yes" if llm_audit.get("fallback_used") else "No", str(llm_audit.get("fallback_reason") or "External LLM response used."))

#             with st.expander("Prompt and payload used for the brief", expanded=True):
#                 if llm_audit.get("sent_to_llm"):
#                     st.success("NVIDIA was called with only the feature rows and article records shown below.")
#                 else:
#                     st.warning("No external LLM call was made for this run. The brief was generated by deterministic local rules using the same feature rows.")
#                 st.markdown("System prompt")
#                 st.code(str(llm_audit.get("system_prompt", "")), language="text")
#                 st.markdown("User prompt")
#                 st.code(str(llm_audit.get("user_prompt", "")), language="text")
#                 st.markdown("Payload")
#                 st.code(json.dumps(llm_audit.get("payload", {}), indent=2, default=str)[:16000], language="json")
#                 if llm_audit.get("nvidia_attempts"):
#                     st.markdown("NVIDIA timing and retry audit")
#                     st.dataframe(pd.DataFrame(llm_audit.get("nvidia_attempts", [])), width="stretch", hide_index=True)
#                 if llm_audit.get("technical_error"):
#                     st.markdown("Technical diagnostic")
#                     st.code(str(llm_audit.get("technical_error", ""))[:2000], language="text")

#             with st.expander("Brief output", expanded=False):
#                 st.markdown(f"<div class='brief-box'>{brief_to_html(str(run.get('brief', '')))}</div>", unsafe_allow_html=True)

#         with raw_tab:
#             st.markdown('<div class="small-header">Raw Pulled Data By Source</div>', unsafe_allow_html=True)
#             for name, result in run.get("results", {}).items():
#                 source_name = SOURCE_LABELS.get(name, name.upper())
#                 raw_count = count_raw_records(result.get("raw"), result.get("items"))
#                 with st.expander(f"{source_name} - {result.get('status', 'unknown')} - {raw_count} raw record(s)", expanded=False):
#                     if result.get("error"):
#                         st.warning(result["error"])
#                     if result.get("items"):
#                         st.markdown("Cleaned items")
#                         st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
#                     raw_preview = result.get("raw")
#                     if raw_preview is not None:
#                         st.markdown("Raw payload")
#                         st.code(json.dumps(raw_preview, indent=2, default=str)[:16000], language="json")
#                     if result.get("rows"):
#                         st.markdown("Normalized feature rows from this source")
#                         st.dataframe(pd.DataFrame(result["rows"]), width="stretch", hide_index=True)


# if view == "Raw Data":
#     run = st.session_state.get("run")
#     if not run:
#         st.markdown(
#             "<div class='empty-console'>"
#             "<div>"
#             "<div class='hero-kicker'>Raw Evidence</div>"
#             "<div class='empty-title'>Collector payloads will appear after a run.</div>"
#             "<div class='empty-body'>This view is intentionally evidence-first: GNews articles, CPI JSON, FDA recall records, NOAA weather alerts, Apify errors, and cleaned item tables stay inspectable for validation.</div>"
#             "</div>"
#             "</div>",
#             unsafe_allow_html=True,
#         )
#     else:
#         for name, result in run["results"].items():
#             status = result.get("status", "unknown")
#             label = f"{name.upper()} - {status}"
#             with st.expander(label, expanded=False):
#                 if result.get("error"):
#                     st.warning(result["error"])
#                 if result.get("items"):
#                     st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
#                 raw_preview = result.get("raw")
#                 if raw_preview is not None:
#                     st.code(json.dumps(raw_preview, indent=2, default=str)[:7000], language="json")


# st.markdown(
#     """
#     <p class="subtle">
#     Note: Google Trends values are relative indexes, GNews is a lightweight news signal, and recall data should be matched
#     against internal SKU/UPC and inventory records before operational decisions.
#     </p>
#     """,
#     unsafe_allow_html=True,
# )


# # import json
# # import hashlib
# # import os
# # import re
# # import time
# # import xml.etree.ElementTree as ET
# # from datetime import datetime, timezone
# # from email.utils import parsedate_to_datetime
# # from html import escape, unescape
# # from typing import Any, Dict, List, Optional, Tuple
# # from urllib.parse import quote_plus

# # import pandas as pd
# # import plotly.graph_objects as go
# # import requests
# # import streamlit as st

# # try:
# #     from dotenv import load_dotenv
# # except ImportError:  # pragma: no cover - optional local convenience
# #     load_dotenv = None


# # if load_dotenv:
# #     load_dotenv()


# # NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# # DEFAULT_NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
# # BLS_CPI_SERIES = {
# #     "Headline CPI": "CUUR0000SA0",
# #     "Food at home": "CUUR0000SAF11",
# #     "Household furnishings": "CUUR0000SAH3",
# #     "Gasoline": "CUUR0000SETB",
# # }
# # APIFY_ALLOWED_TIME_RANGES = ["", "now 1-H", "now 4-H", "now 1-d", "now 7-d", "today 1-m", "today 3-m", "today 5-y", "all"]
# # APIFY_SAFE_TIME_RANGE = "now 7-d"
# # APIFY_HARD_KEYWORD_LIMIT = 2

# # SOURCE_ORDER = ["gnews", "bls", "fda", "weather", "apify"]
# # SOURCE_LABELS = {
# #     "gnews": "GNews / Google News RSS",
# #     "bls": "BLS CPI",
# #     "fda": "openFDA Food Enforcement",
# #     "weather": "NOAA Weather Alerts",
# #     "apify": "Apify Google Trends",
# # }
# # SOURCE_ENDPOINTS = {
# #     "gnews": "GNews package or Google News RSS search feed",
# #     "bls": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
# #     "fda": "https://api.fda.gov/food/enforcement.json",
# #     "weather": "https://api.weather.gov/alerts/active",
# #     "apify": "apify/google-trends-scraper",
# # }
# # ANALYSIS_METHODS = {
# #     "gnews": "Classifies article titles/descriptions into event types, sentiment, source confidence, then averages article risk into one news feature.",
# #     "bls": "Batches CPI series, calculates month-over-month and year-over-year movement, then scores inflation pressure.",
# #     "fda": "Classifies recall reason, FDA class, status, UPC presence, and state coverage, then uses highest adjusted recall severity.",
# #     "weather": "Collects active NOAA alerts for the selected state, weights severity, urgency, and certainty, then caps supply-chain weather risk at 10.",
# #     "apify": "Uses top regional Google Trends index divided by 10; backend hard-limits time range and keyword count to protect quota.",
# # }


# # st.set_page_config(
# #     page_title="Market Intelligence Command Center",
# #     page_icon="DT",
# #     layout="wide",
# #     initial_sidebar_state="expanded",
# # )


# # st.markdown(
# #     """
# #     <style>
# #     @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
# #     :root {
# #         --bg: #F5F7FB;
# #         --sf: #FFFFFF;
# #         --s2: #F8FAFE;
# #         --s3: #F0F4F9;
# #         --s4: #E9EFF5;
# #         --or: #F47B25;
# #         --olt: #FF9F50;
# #         --odk: #C45D0A;
# #         --og: rgba(244,123,37,0.12);
# #         --ob: rgba(244,123,37,0.07);
# #         --obr: rgba(244,123,37,0.25);
# #         --bl: #E2E8F0;
# #         --t: #1E293B;
# #         --t2: #475569;
# #         --t3: #94A3B8;
# #         --gr: #22C55E;
# #         --gbg: rgba(34,197,94,0.10);
# #         --am: #F59E0B;
# #         --abg: rgba(245,158,11,0.10);
# #         --rd: #EF4444;
# #         --rbg: rgba(239,68,68,0.08);
# #         --r: 12px;
# #         --rl: 16px;
# #         --sh: 0 1px 3px rgba(0,0,0,0.04);
# #         --shm: 0 6px 14px -4px rgba(0,0,0,0.10);
# #         --tr: 0.2s cubic-bezier(0.4,0,0.2,1);
# #     }
# #     * { box-sizing: border-box; }
# #     html, body, [class*="css"] {
# #         font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
# #         color: var(--t);
# #     }
# #     .stApp { background: var(--bg); }
# #     #MainMenu, footer { visibility: hidden; }
# #     header { visibility: hidden; }
# #     .accent-bar {
# #         height: 3px;
# #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt), var(--or));
# #         background-size: 200%;
# #         animation: shimmer 3s linear infinite;
# #         width: 100%;
# #         margin: -1.25rem 0 0.9rem 0;
# #     }
# #     @keyframes shimmer { 0% { background-position: 200%; } 100% { background-position: -200%; } }
# #     @keyframes pdot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.5); } 50% { box-shadow: 0 0 0 5px rgba(34,197,94,0); } }
# #     .ldot {
# #         width: 7px;
# #         height: 7px;
# #         border-radius: 50%;
# #         background: var(--gr);
# #         animation: pdot 2s infinite;
# #         display: inline-block;
# #     }
# #     .main .block-container {
# #         padding-top: 1.25rem;
# #         max-width: 1400px;
# #     }
# #     [data-testid="stSidebar"] {
# #         background: var(--sf) !important;
# #         border-right: 1px solid var(--bl) !important;
# #     }
# #     [data-testid="stSidebar"] * {
# #         color: var(--t2);
# #     }
# #     [data-testid="stSidebar"] h1 {
# #         font-size: 18px !important;
# #         line-height: 1.2 !important;
# #         margin-bottom: 2px !important;
# #     }
# #     [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
# #         font-size: 12px;
# #     }
# #     .sidebar-brand {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-left: 4px solid var(--or);
# #         border-radius: 8px;
# #         padding: 12px;
# #         margin: 2px 0 12px 0;
# #     }
# #     .sidebar-brand-title {
# #         color: var(--t);
# #         font-size: 15px;
# #         font-weight: 950;
# #         line-height: 1.15;
# #         margin-bottom: 4px;
# #     }
# #     .sidebar-brand-copy {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.4;
# #     }
# #     .sidebar-summary {
# #         display: grid;
# #         grid-template-columns: repeat(2, minmax(0, 1fr));
# #         gap: 6px;
# #         margin: 8px 0 10px 0;
# #     }
# #     .sidebar-summary-card {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 8px;
# #         min-height: 58px;
# #     }
# #     .sidebar-summary-label {
# #         color: var(--t3);
# #         font-size: 8px;
# #         font-weight: 900;
# #         letter-spacing: .8px;
# #         text-transform: uppercase;
# #         margin-bottom: 3px;
# #     }
# #     .sidebar-summary-value {
# #         color: var(--t);
# #         font-size: 12px;
# #         line-height: 1.2;
# #         font-weight: 900;
# #     }
# #     .sidebar-divider {
# #         height: 1px;
# #         background: var(--bl);
# #         margin: 10px 0;
# #     }
# #     h1, h2, h3 {
# #         color: var(--t);
# #         letter-spacing: 0;
# #     }
# #     h1 { font-size: 2.25rem; font-weight: 900; letter-spacing: -0.02em; }
# #     .subtle {
# #         color: var(--t2);
# #         font-size: 0.94rem;
# #         line-height: 1.55;
# #     }
# #     .topbar {
# #         height: 54px;
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 10px;
# #         display: flex;
# #         align-items: center;
# #         padding: 0 18px;
# #         gap: 12px;
# #         box-shadow: var(--sh);
# #         margin-bottom: 18px;
# #     }
# #     .tt { font-size: 14px; font-weight: 800; color: var(--t); }
# #     .tt span { color: var(--t3); font-weight: 500; }
# #     .tbadge {
# #         background: var(--ob);
# #         border: 1px solid var(--obr);
# #         color: var(--or);
# #         font-size: 10px;
# #         font-weight: 800;
# #         padding: 2px 8px;
# #         border-radius: 20px;
# #     }
# #     .hero-shell {
# #         background: linear-gradient(180deg, #FFFFFF 0%, #FAFCFF 100%);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 16px 18px;
# #         box-shadow: var(--sh);
# #         margin-bottom: 12px;
# #         position: relative;
# #         overflow: hidden;
# #     }
# #     .hero-shell:before {
# #         content: "";
# #         position: absolute;
# #         left: 0;
# #         right: 0;
# #         top: 0;
# #         height: 4px;
# #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
# #     }
# #     .hero-kicker {
# #         display: inline-flex;
# #         align-items: center;
# #         gap: 7px;
# #         background: var(--ob);
# #         border: 1px solid var(--obr);
# #         color: var(--or);
# #         border-radius: 999px;
# #         padding: 4px 10px;
# #         font-size: 10px;
# #         font-weight: 900;
# #         letter-spacing: 0.8px;
# #         text-transform: uppercase;
# #         margin-bottom: 12px;
# #     }
# #     .hero-title {
# #         font-size: 28px;
# #         line-height: 1.08;
# #         letter-spacing: 0;
# #         font-weight: 950;
# #         color: var(--t);
# #         max-width: 820px;
# #         margin: 0;
# #     }
# #     .hero-copy {
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.55;
# #         max-width: 820px;
# #         margin: 10px 0 0 0;
# #     }
# #     .hero-side {
# #         background: #FFFFFF;
# #         border: 1px solid rgba(226,232,240,0.9);
# #         border-radius: 8px;
# #         padding: 14px;
# #         box-shadow: var(--sh);
# #     }
# #     .hero-side-label {
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1.2px;
# #         text-transform: uppercase;
# #         margin-bottom: 8px;
# #     }
# #     .hero-side-row {
# #         display: flex;
# #         justify-content: space-between;
# #         gap: 12px;
# #         border-top: 1px solid var(--bl);
# #         padding-top: 8px;
# #         margin-top: 8px;
# #         font-size: 12px;
# #         color: var(--t2);
# #     }
# #     .hero-side-row strong { color: var(--t); }
# #     .source-tile {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 13px 14px;
# #         box-shadow: var(--sh);
# #         min-height: 86px;
# #         transition: all var(--tr);
# #     }
# #     .source-tile:hover { border-color: var(--obr); box-shadow: var(--shm); transform: translateY(-1px); }
# #     .source-name {
# #         font-size: 12px;
# #         font-weight: 850;
# #         color: var(--t);
# #         margin-bottom: 5px;
# #     }
# #     .source-meta {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.45;
# #     }
# #     .console-panel {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 18px;
# #         box-shadow: var(--sh);
# #         margin-top: 12px;
# #     }
# #     .config-shell {
# #         display: grid;
# #         grid-template-columns: .72fr 1.28fr;
# #         gap: 12px;
# #         margin: 10px 0 14px 0;
# #     }
# #     .config-panel {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 14px 15px;
# #         box-shadow: var(--sh);
# #         min-height: 100%;
# #     }
# #     .config-panel.emphasis {
# #         border-left: 4px solid var(--or);
# #     }
# #     .config-eyebrow {
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin-bottom: 6px;
# #     }
# #     .config-title {
# #         color: var(--t);
# #         font-size: 16px;
# #         font-weight: 950;
# #         line-height: 1.15;
# #         margin-bottom: 6px;
# #     }
# #     .config-copy {
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.5;
# #     }
# #     .readiness-list {
# #         display: grid;
# #         gap: 7px;
# #         margin-top: 12px;
# #     }
# #     .readiness-row {
# #         display: flex;
# #         justify-content: space-between;
# #         gap: 12px;
# #         align-items: center;
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 8px 9px;
# #         font-size: 11px;
# #         color: var(--t2);
# #     }
# #     .readiness-row strong { color: var(--t); font-size: 12px; }
# #     .source-grid {
# #         display: grid;
# #         grid-template-columns: repeat(5, minmax(0, 1fr));
# #         gap: 8px;
# #         margin: 8px 0 12px 0;
# #     }
# #     .source-tile.active { border-color: rgba(34,197,94,0.28); border-left: 3px solid var(--gr); }
# #     .source-tile.off { opacity: .72; }
# #     .source-purpose {
# #         color: var(--t3);
# #         font-size: 9px;
# #         line-height: 1.35;
# #         text-transform: uppercase;
# #         letter-spacing: .7px;
# #         font-weight: 900;
# #         margin-top: 8px;
# #     }
# #     .config-tab-note {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px 11px;
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.45;
# #         margin: 8px 0 10px 0;
# #     }
# #     .readiness-score {
# #         display: grid;
# #         place-items: center;
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         min-height: 138px;
# #         margin-top: 10px;
# #     }
# #     .readiness-score-value {
# #         color: var(--t);
# #         font-size: 38px;
# #         line-height: 1;
# #         font-weight: 950;
# #     }
# #     .readiness-score-label {
# #         color: var(--t3);
# #         font-size: 9px;
# #         text-transform: uppercase;
# #         letter-spacing: 1px;
# #         font-weight: 900;
# #         margin-top: 5px;
# #     }
# #     .control-grid {
# #         display: grid;
# #         grid-template-columns: repeat(3, minmax(0, 1fr));
# #         gap: 8px;
# #         margin-top: 10px;
# #     }
# #     .control-check {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px;
# #         min-height: 88px;
# #     }
# #     .control-check.ok { border-left: 3px solid var(--gr); }
# #     .control-check.warn { border-left: 3px solid var(--am); }
# #     .control-check-title {
# #         color: var(--t);
# #         font-size: 12px;
# #         font-weight: 900;
# #         margin-bottom: 4px;
# #     }
# #     .control-check-copy {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.4;
# #     }
# #     .routing-grid {
# #         display: grid;
# #         grid-template-columns: repeat(5, minmax(0, 1fr));
# #         gap: 8px;
# #         margin: 8px 0 14px 0;
# #     }
# #     .routing-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 11px 12px;
# #         min-height: 154px;
# #         box-shadow: var(--sh);
# #     }
# #     .routing-card.active { border-top: 3px solid var(--gr); }
# #     .routing-card.off { border-top: 3px solid var(--t3); opacity: .74; }
# #     .routing-source {
# #         color: var(--t);
# #         font-size: 12px;
# #         font-weight: 950;
# #         margin-bottom: 4px;
# #     }
# #     .routing-line {
# #         border-top: 1px solid var(--bl);
# #         padding-top: 7px;
# #         margin-top: 7px;
# #     }
# #     .routing-label {
# #         color: var(--t3);
# #         font-size: 8px;
# #         letter-spacing: .8px;
# #         text-transform: uppercase;
# #         font-weight: 900;
# #         margin-bottom: 2px;
# #     }
# #     .routing-value {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.35;
# #     }
# #     .result-command {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         box-shadow: var(--sh);
# #         padding: 14px 16px;
# #         margin: 8px 0 12px 0;
# #         display: grid;
# #         grid-template-columns: 1.4fr .9fr;
# #         gap: 14px;
# #         align-items: stretch;
# #     }
# #     .result-command-title {
# #         color: var(--t);
# #         font-size: 20px;
# #         line-height: 1.15;
# #         font-weight: 950;
# #         margin-bottom: 5px;
# #     }
# #     .result-command-copy {
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.5;
# #     }
# #     .result-command-meta {
# #         display: grid;
# #         grid-template-columns: repeat(2, minmax(0, 1fr));
# #         gap: 8px;
# #     }
# #     .result-meta-cell {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px;
# #     }
# #     .result-meta-label {
# #         color: var(--t3);
# #         font-size: 8px;
# #         font-weight: 900;
# #         letter-spacing: .8px;
# #         text-transform: uppercase;
# #         margin-bottom: 3px;
# #     }
# #     .result-meta-value {
# #         color: var(--t);
# #         font-size: 13px;
# #         line-height: 1.2;
# #         font-weight: 900;
# #     }
# #     .decision-grid {
# #         display: grid;
# #         grid-template-columns: repeat(4, minmax(0, 1fr));
# #         gap: 8px;
# #         margin: 8px 0 12px 0;
# #     }
# #     .decision-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-left: 3px solid var(--or);
# #         border-radius: 8px;
# #         padding: 12px;
# #         min-height: 132px;
# #         box-shadow: var(--sh);
# #     }
# #     .decision-owner {
# #         color: var(--or);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin-bottom: 6px;
# #     }
# #     .decision-title {
# #         color: var(--t);
# #         font-size: 13px;
# #         font-weight: 950;
# #         line-height: 1.25;
# #         margin-bottom: 6px;
# #     }
# #     .decision-body {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.45;
# #     }
# #     .explain-ladder {
# #         display: grid;
# #         grid-template-columns: repeat(5, minmax(0, 1fr));
# #         gap: 8px;
# #         margin: 8px 0 12px 0;
# #     }
# #     .explain-step {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px;
# #         min-height: 98px;
# #     }
# #     .explain-step-num {
# #         color: var(--or);
# #         font-size: 9px;
# #         letter-spacing: .8px;
# #         text-transform: uppercase;
# #         font-weight: 900;
# #         margin-bottom: 4px;
# #     }
# #     .explain-step-title {
# #         color: var(--t);
# #         font-size: 12px;
# #         font-weight: 950;
# #         margin-bottom: 4px;
# #     }
# #     .explain-step-copy {
# #         color: var(--t2);
# #         font-size: 10px;
# #         line-height: 1.4;
# #     }
# #     .audit-command {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         box-shadow: var(--sh);
# #         padding: 14px 16px;
# #         margin: 8px 0 12px 0;
# #         display: grid;
# #         grid-template-columns: 1.25fr 1fr;
# #         gap: 12px;
# #     }
# #     .audit-command-title {
# #         color: var(--t);
# #         font-size: 20px;
# #         line-height: 1.15;
# #         font-weight: 950;
# #         margin-bottom: 5px;
# #     }
# #     .audit-command-copy {
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.5;
# #     }
# #     .audit-status-grid {
# #         display: grid;
# #         grid-template-columns: repeat(2, minmax(0, 1fr));
# #         gap: 8px;
# #     }
# #     .audit-status-cell {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px;
# #     }
# #     .audit-status-label {
# #         color: var(--t3);
# #         font-size: 8px;
# #         font-weight: 900;
# #         letter-spacing: .8px;
# #         text-transform: uppercase;
# #         margin-bottom: 3px;
# #     }
# #     .audit-status-value {
# #         color: var(--t);
# #         font-size: 13px;
# #         font-weight: 950;
# #         line-height: 1.2;
# #     }
# #     .audit-lineage {
# #         display: grid;
# #         grid-template-columns: repeat(5, minmax(0, 1fr));
# #         gap: 8px;
# #         margin: 8px 0 12px 0;
# #     }
# #     .audit-lineage-step {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px;
# #         min-height: 94px;
# #     }
# #     .audit-lineage-num {
# #         color: var(--or);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: .8px;
# #         text-transform: uppercase;
# #         margin-bottom: 4px;
# #     }
# #     .audit-lineage-title {
# #         color: var(--t);
# #         font-size: 12px;
# #         font-weight: 950;
# #         margin-bottom: 4px;
# #     }
# #     .audit-lineage-copy {
# #         color: var(--t2);
# #         font-size: 10px;
# #         line-height: 1.4;
# #     }
# #     .empty-console {
# #         background:
# #             linear-gradient(135deg, rgba(244,123,37,0.08), rgba(255,255,255,0.85)),
# #             var(--sf);
# #         border: 1px dashed var(--obr);
# #         border-radius: 18px;
# #         padding: 28px;
# #         min-height: 210px;
# #         display: flex;
# #         align-items: center;
# #         justify-content: space-between;
# #         gap: 20px;
# #     }
# #     .empty-title {
# #         color: var(--t);
# #         font-size: 22px;
# #         line-height: 1.15;
# #         font-weight: 900;
# #         letter-spacing: -0.025em;
# #         margin-bottom: 8px;
# #     }
# #     .empty-body {
# #         color: var(--t2);
# #         font-size: 13px;
# #         line-height: 1.65;
# #         max-width: 620px;
# #     }
# #     .workflow {
# #         display: grid;
# #         grid-template-columns: repeat(5, minmax(0, 1fr));
# #         gap: 8px;
# #         margin-top: 12px;
# #     }
# #     .workflow-step {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 10px;
# #         font-size: 11px;
# #         color: var(--t2);
# #         font-weight: 700;
# #     }
# #     .workflow-step span {
# #         display: block;
# #         color: var(--or);
# #         font-size: 9px;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         font-weight: 900;
# #         margin-bottom: 3px;
# #     }
# #     .metric-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 10px;
# #         padding: 16px 18px;
# #         min-height: 132px;
# #         box-shadow: var(--sh);
# #         transition: all var(--tr);
# #     }
# #     .metric-card:hover {
# #         transform: translateY(-1px);
# #         box-shadow: var(--shm);
# #         border-color: var(--obr);
# #     }
# #     .metric-label {
# #         color: var(--t3);
# #         font-size: 0.68rem;
# #         text-transform: uppercase;
# #         letter-spacing: 0.11em;
# #         margin-bottom: 8px;
# #         font-weight: 800;
# #     }
# #     .metric-value {
# #         color: var(--t);
# #         font-size: 2.05rem;
# #         font-weight: 900;
# #         line-height: 1;
# #         letter-spacing: -0.04em;
# #     }
# #     .metric-note {
# #         color: var(--t2);
# #         font-size: 0.82rem;
# #         margin-top: 10px;
# #         line-height: 1.4;
# #     }
# #     .pill {
# #         display: inline-block;
# #         border-radius: 999px;
# #         padding: 3px 10px;
# #         font-size: 0.72rem;
# #         font-weight: 800;
# #         border: 1px solid var(--bl);
# #         color: var(--t2);
# #         background: var(--s2);
# #         margin-top: 8px;
# #     }
# #     .pill-high { color: var(--gr); background: var(--gbg); border-color: rgba(34,197,94,0.2); }
# #     .pill-medium { color: var(--am); background: var(--abg); border-color: rgba(245,158,11,0.2); }
# #     .pill-low { color: var(--rd); background: var(--rbg); border-color: rgba(239,68,68,0.2); }
# #     .brief-box {
# #         background: var(--sf);
# #         border: 1px solid var(--obr);
# #         border-left: 4px solid var(--or);
# #         border-radius: 10px;
# #         padding: 20px 22px;
# #         color: var(--t);
# #         line-height: 1.65;
# #         box-shadow: 0 0 0 3px var(--og);
# #     }
# #     .brief-shell {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 12px;
# #         box-shadow: var(--sh);
# #         overflow: hidden;
# #         margin-top: 8px;
# #     }
# #     .brief-header {
# #         background: linear-gradient(180deg, rgba(248,250,252,0.98), rgba(255,255,255,0.98));
# #         border-bottom: 1px solid var(--bl);
# #         padding: 18px 20px;
# #     }
# #     .brief-kicker {
# #         color: var(--or);
# #         font-size: 10px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin-bottom: 6px;
# #     }
# #     .brief-title {
# #         color: var(--t);
# #         font-size: 22px;
# #         line-height: 1.15;
# #         font-weight: 950;
# #         letter-spacing: -0.02em;
# #         margin-bottom: 8px;
# #     }
# #     .brief-summary-text {
# #         color: var(--t2);
# #         font-size: 13px;
# #         line-height: 1.55;
# #         max-width: 980px;
# #     }
# #     .brief-meta-strip {
# #         display: grid;
# #         grid-template-columns: repeat(4, minmax(0, 1fr));
# #         gap: 8px;
# #         margin-top: 14px;
# #     }
# #     .brief-meta-chip {
# #         background: rgba(255,255,255,0.82);
# #         border: 1px solid rgba(226,232,240,0.95);
# #         border-radius: 8px;
# #         padding: 10px 11px;
# #     }
# #     .brief-meta-label {
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: .9px;
# #         text-transform: uppercase;
# #         margin-bottom: 4px;
# #     }
# #     .brief-meta-value {
# #         color: var(--t);
# #         font-size: 13px;
# #         line-height: 1.25;
# #         font-weight: 900;
# #     }
# #     .brief-section-grid {
# #         display: grid;
# #         grid-template-columns: repeat(2, minmax(0, 1fr));
# #         gap: 12px;
# #         padding: 16px;
# #     }
# #     .brief-section-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 14px 15px;
# #         min-height: 150px;
# #         box-shadow: var(--sh);
# #     }
# #     .brief-section-card.primary {
# #         grid-column: 1 / -1;
# #         min-height: 0;
# #         border-color: var(--obr);
# #         background: rgba(244,123,37,0.035);
# #     }
# #     .brief-section-title {
# #         color: var(--or);
# #         font-size: 11px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin: 0 0 9px 0;
# #     }
# #     .brief-section-card p,
# #     .brief-box p {
# #         margin: 0 0 8px 0;
# #         font-size: 13px;
# #         color: var(--t2);
# #         line-height: 1.55;
# #     }
# #     .brief-list {
# #         margin: 0 0 0 18px;
# #         padding: 0;
# #     }
# #     .brief-list li {
# #         margin-bottom: 8px;
# #         color: var(--t2);
# #         font-size: 13px;
# #         line-height: 1.45;
# #     }
# #     .score-explain-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: var(--rl);
# #         padding: 14px 16px;
# #         box-shadow: var(--sh);
# #         margin: 8px 0;
# #     }
# #     .score-explain-head {
# #         display: flex;
# #         align-items: flex-start;
# #         justify-content: space-between;
# #         gap: 12px;
# #         border-bottom: 1px solid var(--bl);
# #         padding-bottom: 10px;
# #         margin-bottom: 10px;
# #     }
# #     .score-explain-title {
# #         color: var(--t);
# #         font-size: 13px;
# #         font-weight: 900;
# #         margin-bottom: 3px;
# #     }
# #     .score-explain-meta {
# #         color: var(--t3);
# #         font-size: 10px;
# #         font-weight: 800;
# #         letter-spacing: .7px;
# #         text-transform: uppercase;
# #     }
# #     .score-number {
# #         min-width: 74px;
# #         text-align: center;
# #         border-radius: 12px;
# #         border: 1px solid var(--obr);
# #         background: var(--ob);
# #         color: var(--or);
# #         font-size: 24px;
# #         line-height: 1;
# #         font-weight: 950;
# #         padding: 9px 8px;
# #     }
# #     .score-number span {
# #         display: block;
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: .8px;
# #         margin-top: 3px;
# #     }
# #     .score-explain-label {
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin: 8px 0 3px 0;
# #     }
# #     .score-explain-text {
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.5;
# #     }
# #     .brief-grounding {
# #         display: grid;
# #         grid-template-columns: repeat(4, minmax(0, 1fr));
# #         gap: 8px;
# #         margin-bottom: 12px;
# #     }
# #     .small-header {
# #         color: var(--t3);
# #         font-size: 0.68rem;
# #         text-transform: uppercase;
# #         letter-spacing: 0.12em;
# #         font-weight: 800;
# #         margin: 8px 0 12px 0;
# #         padding-bottom: 6px;
# #         border-bottom: 1px solid var(--bl);
# #     }
# #     .action-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: var(--r);
# #         padding: 12px 14px;
# #         box-shadow: var(--sh);
# #         min-height: 112px;
# #     }
# #     .action-label {
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         color: var(--or);
# #         margin-bottom: 7px;
# #     }
# #     .action-title {
# #         font-size: 13px;
# #         font-weight: 800;
# #         color: var(--t);
# #         margin-bottom: 5px;
# #     }
# #     .action-body {
# #         font-size: 12px;
# #         color: var(--t2);
# #         line-height: 1.45;
# #     }
# #     .note-box {
# #         background: rgba(244,123,37,0.04);
# #         border-left: 3px solid var(--or);
# #         border-radius: 0 8px 8px 0;
# #         padding: 9px 12px;
# #         font-size: 12px;
# #         color: var(--t2);
# #         margin: 8px 0;
# #     }
# #     .run-monitor {
# #         background: var(--sf);
# #         border: 1px solid var(--obr);
# #         border-radius: 12px;
# #         padding: 14px 16px;
# #         box-shadow: 0 0 0 3px var(--og);
# #         margin: 8px 0 16px 0;
# #     }
# #     .run-monitor-head {
# #         display: flex;
# #         align-items: center;
# #         justify-content: space-between;
# #         gap: 12px;
# #         margin-bottom: 10px;
# #     }
# #     .run-monitor-title {
# #         color: var(--t);
# #         font-size: 13px;
# #         font-weight: 900;
# #     }
# #     .run-monitor-sub {
# #         color: var(--t3);
# #         font-size: 10px;
# #         font-weight: 800;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #     }
# #     .run-progress-track {
# #         height: 7px;
# #         background: var(--s3);
# #         border-radius: 999px;
# #         overflow: hidden;
# #         margin: 8px 0 12px 0;
# #     }
# #     .run-progress-fill {
# #         height: 100%;
# #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
# #         border-radius: 999px;
# #         transition: width var(--tr);
# #     }
# #     .run-status-grid {
# #         display: grid;
# #         grid-template-columns: repeat(6, minmax(0, 1fr));
# #         gap: 8px;
# #     }
# #     .run-status-card {
# #         background: var(--s2);
# #         border: 1px solid var(--bl);
# #         border-radius: 12px;
# #         padding: 10px;
# #         min-height: 72px;
# #     }
# #     .run-status-name {
# #         font-size: 11px;
# #         font-weight: 900;
# #         color: var(--t);
# #         margin-bottom: 5px;
# #     }
# #     .run-status-detail {
# #         font-size: 10px;
# #         color: var(--t2);
# #         line-height: 1.35;
# #     }
# #     .status-badge {
# #         display: inline-flex;
# #         align-items: center;
# #         gap: 4px;
# #         padding: 2px 7px;
# #         border-radius: 999px;
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: .6px;
# #         text-transform: uppercase;
# #         margin-bottom: 6px;
# #     }
# #     .status-running { background: var(--ob); color: var(--or); border: 1px solid var(--obr); }
# #     .status-success { background: var(--gbg); color: var(--gr); border: 1px solid rgba(34,197,94,.2); }
# #     .status-failed { background: var(--rbg); color: var(--rd); border: 1px solid rgba(239,68,68,.2); }
# #     .status-skipped { background: rgba(100,116,139,.08); color: var(--t2); border: 1px solid var(--bl); }
# #     .status-queued { background: var(--s3); color: var(--t3); border: 1px solid var(--bl); }
# #     .audit-banner {
# #         background: linear-gradient(135deg, rgba(34,197,94,0.10), rgba(255,255,255,0.92));
# #         border: 1px solid rgba(34,197,94,0.22);
# #         border-left: 4px solid var(--gr);
# #         border-radius: var(--rl);
# #         padding: 14px 16px;
# #         color: var(--t);
# #         margin: 8px 0 14px 0;
# #         box-shadow: var(--sh);
# #     }
# #     .audit-banner.warn {
# #         background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(255,255,255,0.92));
# #         border-color: rgba(245,158,11,0.24);
# #         border-left-color: var(--am);
# #     }
# #     .audit-title {
# #         font-size: 13px;
# #         font-weight: 900;
# #         margin-bottom: 5px;
# #     }
# #     .audit-body {
# #         font-size: 12px;
# #         color: var(--t2);
# #         line-height: 1.5;
# #     }
# #     .audit-grid {
# #         display: grid;
# #         grid-template-columns: repeat(4, minmax(0, 1fr));
# #         gap: 10px;
# #         margin: 10px 0 14px 0;
# #     }
# #     .audit-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 10px;
# #         padding: 13px 14px;
# #         min-height: 92px;
# #         box-shadow: var(--sh);
# #     }
# #     .audit-label {
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin-bottom: 7px;
# #     }
# #     .audit-value {
# #         color: var(--t);
# #         font-size: 18px;
# #         line-height: 1.1;
# #         font-weight: 900;
# #     }
# #     .audit-note {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.35;
# #         margin-top: 7px;
# #     }
# #     div[role="radiogroup"] {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 14px;
# #         padding: 5px;
# #         display: inline-flex;
# #         gap: 4px;
# #         box-shadow: var(--sh);
# #         margin: 4px 0 12px 0;
# #     }
# #     div[role="radiogroup"] label {
# #         border-radius: 10px !important;
# #         padding: 6px 13px !important;
# #         min-height: 34px !important;
# #         transition: all var(--tr);
# #     }
# #     div[role="radiogroup"] label:has(input:checked) {
# #         background: var(--ob) !important;
# #         border: 1px solid var(--obr) !important;
# #         color: var(--or) !important;
# #         font-weight: 850 !important;
# #     }
# #     div[role="radiogroup"] label span {
# #         font-size: 12px !important;
# #         font-weight: 750 !important;
# #     }
# #     div[data-testid="stButton"] button {
# #         background: var(--or);
# #         color: #fff;
# #         border: none;
# #         border-radius: var(--r);
# #         font-weight: 800;
# #         box-shadow: 0 2px 8px rgba(244,123,37,0.2);
# #         transition: all var(--tr);
# #     }
# #     div[data-testid="stButton"] button:hover {
# #         background: var(--odk);
# #         color: #fff;
# #         border: none;
# #         transform: translateY(-1px);
# #     }
# #     .stTabs [data-baseweb="tab-list"] {
# #         gap: 12px;
# #         border-bottom: 1px solid var(--bl);
# #         padding: 0 0 8px 0;
# #         margin: 6px 0 14px 0;
# #     }
# #     .stTabs [data-baseweb="tab"] {
# #         border-radius: 8px;
# #         color: var(--t2);
# #         font-weight: 700;
# #         min-height: 42px;
# #         padding: 9px 18px !important;
# #         border: 1px solid transparent;
# #         background: var(--s2);
# #     }
# #     .stTabs [aria-selected="true"] {
# #         background: var(--ob);
# #         color: var(--or) !important;
# #         border: 1px solid var(--obr);
# #         box-shadow: inset 0 -2px 0 var(--or);
# #     }
# #     .stTabs [data-baseweb="tab"] p {
# #         font-size: 13px;
# #         font-weight: 850;
# #         white-space: nowrap;
# #     }
# #     .stSelectbox>div>div, .stTextInput>div>div, .stTextArea>div>div {
# #         background: var(--s2) !important;
# #         border: 1px solid var(--bl) !important;
# #         border-radius: var(--r) !important;
# #         font-size: 13px !important;
# #         color: var(--t) !important;
# #     }
# #     [data-testid="stExpander"] {
# #         background: var(--sf);
# #         border: 1px solid var(--bl) !important;
# #         border-radius: 10px !important;
# #         box-shadow: var(--sh);
# #     }
# #     .glossary-grid {
# #         display: grid;
# #         grid-template-columns: repeat(3, minmax(0, 1fr));
# #         gap: 10px;
# #         margin: 10px 0 4px 0;
# #     }
# #     .glossary-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 12px;
# #         min-height: 94px;
# #     }
# #     .glossary-term {
# #         color: var(--t);
# #         font-size: 12px;
# #         font-weight: 900;
# #         margin-bottom: 5px;
# #     }
# #     .glossary-def {
# #         color: var(--t2);
# #         font-size: 11px;
# #         line-height: 1.45;
# #     }
# #     .enterprise-band {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 10px;
# #         padding: 14px 16px;
# #         margin: 10px 0 14px 0;
# #         box-shadow: var(--sh);
# #     }
# #     .enterprise-band-title {
# #         color: var(--t);
# #         font-size: 14px;
# #         font-weight: 900;
# #         margin-bottom: 4px;
# #     }
# #     .enterprise-band-copy {
# #         color: var(--t2);
# #         font-size: 12px;
# #         line-height: 1.5;
# #     }
# #     .trust-grid {
# #         display: grid;
# #         grid-template-columns: repeat(5, minmax(0, 1fr));
# #         gap: 8px;
# #         margin: 10px 0 14px 0;
# #     }
# #     .trust-card {
# #         background: var(--sf);
# #         border: 1px solid var(--bl);
# #         border-radius: 8px;
# #         padding: 11px 12px;
# #         min-height: 86px;
# #     }
# #     .trust-card.good { border-left: 3px solid var(--gr); }
# #     .trust-card.warn { border-left: 3px solid var(--am); }
# #     .trust-label {
# #         color: var(--t3);
# #         font-size: 9px;
# #         font-weight: 900;
# #         letter-spacing: 1px;
# #         text-transform: uppercase;
# #         margin-bottom: 5px;
# #     }
# #     .trust-value {
# #         color: var(--t);
# #         font-size: 17px;
# #         font-weight: 950;
# #         line-height: 1.1;
# #     }
# #     .trust-note {
# #         color: var(--t2);
# #         font-size: 10px;
# #         line-height: 1.35;
# #         margin-top: 6px;
# #     }
# #     @media (max-width: 1100px) {
# #         .run-status-grid, .brief-meta-strip, .glossary-grid, .trust-grid, .source-grid, .config-shell, .routing-grid, .control-grid, .result-command, .decision-grid, .explain-ladder, .audit-command, .audit-lineage { grid-template-columns: repeat(2, minmax(0, 1fr)); }
# #         .brief-section-grid { grid-template-columns: 1fr; }
# #     }
# #     @media (max-width: 720px) {
# #         .run-status-grid, .brief-meta-strip, .glossary-grid, .trust-grid, .source-grid, .config-shell, .routing-grid, .control-grid, .result-command, .decision-grid, .explain-ladder, .audit-command, .audit-status-grid, .audit-lineage, .workflow { grid-template-columns: 1fr; }
# #         .hero-title { font-size: 28px; }
# #     }
# #     ::-webkit-scrollbar { width: 4px; height: 4px; }
# #     ::-webkit-scrollbar-track { background: transparent; }
# #     ::-webkit-scrollbar-thumb { background: var(--bl); border-radius: 2px; }
# #     </style>
# #     """,
# #     unsafe_allow_html=True,
# # )


# # def utc_now() -> str:
# #     return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# # def safe_request(
# #     url: str,
# #     *,
# #     method: str = "GET",
# #     headers: Optional[Dict[str, str]] = None,
# #     params: Optional[Dict[str, Any]] = None,
# #     json_body: Optional[Dict[str, Any]] = None,
# #     timeout: int = 30,
# # ) -> Tuple[bool, Any, str]:
# #     try:
# #         if method.upper() == "POST":
# #             response = requests.post(url, headers=headers, params=params, json=json_body, timeout=timeout)
# #         else:
# #             response = requests.get(url, headers=headers, params=params, timeout=timeout)
# #         response.raise_for_status()
# #         try:
# #             return True, response.json(), "success"
# #         except ValueError:
# #             return True, response.text, "success"
# #     except requests.RequestException as exc:
# #         return False, None, str(exc)


# # def friendly_nvidia_failure(raw_error: str) -> Tuple[str, str]:
# #     error_text = str(raw_error or "").strip()
# #     lower = error_text.lower()
# #     if "timed out" in lower or "read timeout" in lower:
# #         return (
# #             "fallback (NVIDIA timeout)",
# #             "NVIDIA did not respond within the configured timeout, so a local rule-based brief was generated from the collected signal rows.",
# #         )
# #     if "401" in lower or "unauthorized" in lower:
# #         return (
# #             "fallback (NVIDIA authentication)",
# #             "NVIDIA authentication failed, so a local rule-based brief was generated from the collected signal rows.",
# #         )
# #     if "429" in lower or "rate limit" in lower:
# #         return (
# #             "fallback (NVIDIA rate limit)",
# #             "NVIDIA rate limiting prevented the AI brief, so a local rule-based brief was generated from the collected signal rows.",
# #         )
# #     return (
# #         "fallback (NVIDIA unavailable)",
# #         "The NVIDIA AI brief was unavailable for this run, so a local rule-based brief was generated from the collected signal rows.",
# #     )


# # def confidence_class(confidence: str) -> str:
# #     lookup = {"High": "pill-high", "Medium": "pill-medium", "Low": "pill-low"}
# #     return lookup.get(confidence, "")


# # def risk_band(score: float) -> str:
# #     if score >= 8:
# #         return "High"
# #     if score >= 5:
# #         return "Medium"
# #     return "Low"


# # def dollar_tree_context_for_signal(row: Dict[str, Any]) -> Dict[str, str]:
# #     area = str(row.get("signal_area", "")).lower()
# #     name = str(row.get("signal_name", "")).lower()
# #     source = str(row.get("source", "")).lower()
# #     raw_category = str(row.get("affected_category", "") or row.get("raw_reference", "")).lower()
# #     score = float(row.get("risk_score", 0) or 0)
# #     priority = "High" if score >= 8 else "Medium" if score >= 5 else "Monitor"

# #     context = {
# #         "dollar_tree_category": "Total store / value basket",
# #         "enterprise_kpi": "Forecast Adjustment Priority",
# #         "planning_owner": "Demand Planning",
# #         "demand_direction": "Unknown until matched to POS",
# #         "forecast_feature": str(row.get("signal_name", "external_signal")),
# #         "dollar_tree_relevance": "External context signal; validate against internal sales, inventory, promotion, and store data.",
# #         "impact_hypothesis": "Use as a candidate external regressor, not as a standalone demand forecast.",
# #         "action_priority": priority,
# #         "evidence_grade": "Live public API" if source else "Unknown",
# #         "internal_data_needed": "POS sales, category hierarchy, inventory, store/DC mapping, promotion calendar",
# #     }

# #     if "recall" in area or "fda" in source or any(term in raw_category for term in ["snack", "candy", "beverage", "food"]):
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Snacks, candy, beverages, and consumables",
# #                 "enterprise_kpi": "Safety And Compliance Risk",
# #                 "planning_owner": "Compliance + Category Buyer",
# #                 "demand_direction": "Downside risk / substitution demand",
# #                 "forecast_feature": "recall_exposure_score",
# #                 "dollar_tree_relevance": "Retailers may carry high-velocity consumables where recalls can require UPC matching, vendor review, and store withdrawal decisions.",
# #                 "impact_hypothesis": "If affected UPCs overlap internal SKU files, demand may shift away from recalled items and toward substitutes.",
# #                 "internal_data_needed": "SKU master, UPC list, vendor file, on-hand inventory, store distribution",
# #             }
# #         )
# #     elif "weather" in area or "noaa" in source:
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Emergency demand: batteries, water, cleaning, food, household essentials",
# #                 "enterprise_kpi": "Supply Chain Disruption Risk",
# #                 "planning_owner": "Supply Chain + Demand Planning",
# #                 "demand_direction": "Short-term uplift for essentials; disruption risk for replenishment",
# #                 "forecast_feature": "state_weather_disruption_score",
# #                 "dollar_tree_relevance": "Weather alerts can affect store traffic, replenishment routes, staffing, and emergency-demand baskets in exposed states.",
# #                 "impact_hypothesis": "Severe alerts can lift emergency categories while increasing DC-to-store execution risk.",
# #                 "internal_data_needed": "Store locations, DC routes, state/category sales, inventory by store",
# #             }
# #         )
# #     elif "headline cpi" in name or ("inflation" in area and "category" not in area):
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Total value basket",
# #                 "enterprise_kpi": "Value Basket Pressure",
# #                 "planning_owner": "Merchandising Strategy + Demand Planning",
# #                 "demand_direction": "Trade-down support for essentials; pressure on discretionary baskets",
# #                 "forecast_feature": "headline_cpi_value_pressure",
# #                 "dollar_tree_relevance": "Value-oriented retail demand can be sensitive to consumer price pressure and trade-down behavior.",
# #                 "impact_hypothesis": "Higher inflation can increase value-seeking traffic while changing mix toward essentials.",
# #                 "internal_data_needed": "Traffic, basket mix, category sales, price/promotion calendar",
# #             }
# #         )
# #     elif "gasoline" in name or "gasoline" in raw_category:
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Traffic-sensitive baskets and discretionary add-ons",
# #                 "enterprise_kpi": "Consumer Wallet Pressure",
# #                 "planning_owner": "Demand Planning + Store Operations",
# #                 "demand_direction": "Possible traffic pressure; essential-item substitution risk",
# #                 "forecast_feature": "gasoline_wallet_pressure_score",
# #                 "dollar_tree_relevance": "Fuel inflation can reduce discretionary spend and change trip patterns for value retailers.",
# #                 "impact_hypothesis": "Rising gas prices may shift basket composition and store visit frequency by region.",
# #                 "internal_data_needed": "Store traffic, basket value, region/store sales, trip frequency",
# #             }
# #         )
# #     elif "food at home" in name:
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Food, snacks, candy, beverages",
# #                 "enterprise_kpi": "Consumables Demand Pressure",
# #                 "planning_owner": "Consumables Category Manager",
# #                 "demand_direction": "Potential uplift in value consumables",
# #                 "forecast_feature": "food_at_home_cpi_pressure",
# #                 "dollar_tree_relevance": "Food inflation can push shoppers toward value-format consumables and smaller pack sizes.",
# #                 "impact_hypothesis": "Higher food CPI may increase demand for value snacks, pantry, and beverage alternatives.",
# #                 "internal_data_needed": "Consumables sales, price ladder, pack size, inventory and promotions",
# #             }
# #         )
# #     elif "household" in name:
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Household supplies, cleaning, home basics",
# #                 "enterprise_kpi": "Household Essentials Pressure",
# #                 "planning_owner": "Household Category Manager",
# #                 "demand_direction": "Potential mix shift toward value household items",
# #                 "forecast_feature": "household_cpi_pressure",
# #                 "dollar_tree_relevance": "Household inflation can influence trade-down into value-oriented home and cleaning categories.",
# #                 "impact_hypothesis": "Higher household CPI may support value demand but pressure margin and vendor costs.",
# #                 "internal_data_needed": "Household category sales, costs, vendor data, pricing actions",
# #             }
# #         )
# #     elif "search" in area or "apify" in source:
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Seasonal, local demand, and store-intent categories",
# #                 "enterprise_kpi": "Demand Interest Spike",
# #                 "planning_owner": "Demand Planning + Digital/Marketing",
# #                 "demand_direction": "Potential regional demand uplift",
# #                 "forecast_feature": "google_trends_interest_score",
# #                 "dollar_tree_relevance": "Search interest can reveal early regional intent around deals, coupons, seasonal products, or store visits.",
# #                 "impact_hypothesis": "Rising search interest may precede demand spikes, but must be checked against store/category sales.",
# #                 "internal_data_needed": "Regional sales, promotion calendar, store traffic, search keywords by category",
# #             }
# #         )
# #     elif "news" in area or "gnews" in source:
# #         context.update(
# #             {
# #                 "dollar_tree_category": "Enterprise market events and competitor pressure",
# #                 "enterprise_kpi": "Market Event Risk",
# #                 "planning_owner": "Merchandising Leadership + Category Manager",
# #                 "demand_direction": "Depends on event type and affected category",
# #                 "forecast_feature": "retail_news_event_score",
# #                 "dollar_tree_relevance": "Retail news can surface competitor moves, price pressure, closures, recalls, and market events relevant to retail planning.",
# #                 "impact_hypothesis": "Use high-risk articles as explainers for forecast variance and buyer review.",
# #                 "internal_data_needed": "Affected category sales, competitor set, promotion calendar, store overlap",
# #             }
# #         )
# #     return context


# # def enrich_feature_rows_for_retailer(feature_df: pd.DataFrame, retailer_name: str) -> pd.DataFrame:
# #     if feature_df.empty:
# #         return feature_df
# #     enriched = feature_df.copy()
# #     contexts = [dollar_tree_context_for_signal(row.to_dict()) for _, row in enriched.iterrows()]
# #     context_df = pd.DataFrame(contexts)
# #     for col in context_df.columns:
# #         enriched[col] = context_df[col].values
# #     if "Retailer" not in retailer_name:
# #         enriched["dollar_tree_relevance"] = enriched["dollar_tree_relevance"].str.replace("Retailer", retailer_name or "the retailer", regex=False)
# #     return enriched


# # def source_confidence(publisher: str) -> str:
# #     high = ["reuters", "associated press", "ap news", "bloomberg", "sec", "pr newswire"]
# #     medium = ["cnbc", "yahoo", "nasdaq", "marketwatch", "forbes", "investing.com", "retail dive"]
# #     p = (publisher or "").lower()
# #     if any(name in p for name in high):
# #         return "High"
# #     if any(name in p for name in medium):
# #         return "Medium"
# #     return "Medium" if publisher else "Low"


# # def count_raw_records(raw_payload: Any, items: Optional[List[Dict[str, Any]]] = None) -> int:
# #     if items:
# #         return len(items)
# #     if raw_payload is None:
# #         return 0
# #     if isinstance(raw_payload, list):
# #         return len(raw_payload)
# #     if isinstance(raw_payload, dict):
# #         if isinstance(raw_payload.get("features"), list):
# #             return len(raw_payload["features"])
# #         if isinstance(raw_payload.get("results"), list):
# #             return len(raw_payload["results"])
# #         series = raw_payload.get("Results", {}).get("series") if isinstance(raw_payload.get("Results"), dict) else None
# #         if isinstance(series, list):
# #             return sum(len(s.get("data", [])) for s in series if isinstance(s, dict))
# #         nested_counts = [
# #             count_raw_records(value)
# #             for value in raw_payload.values()
# #             if isinstance(value, (dict, list))
# #         ]
# #         return sum(nested_counts) if nested_counts else 1
# #     return 1


# # def request_summary(source_key: str, run_config: Dict[str, Any]) -> str:
# #     if source_key == "gnews":
# #         keywords = run_config.get("news_keywords", [])
# #         return (
# #             f"{len(keywords)} keyword(s), country={run_config.get('country', '')}, "
# #             f"language={run_config.get('language', '')}, period={run_config.get('gnews_period', '')}, "
# #             f"max_results={run_config.get('max_news', '')}"
# #         )
# #     if source_key == "bls":
# #         return "series=" + ", ".join(BLS_CPI_SERIES.values())
# #     if source_key == "fda":
# #         return f"search={run_config.get('fda_query', '')}; limit={run_config.get('fda_limit', '')}"
# #     if source_key == "weather":
# #         return f"area={run_config.get('weather_area', '')}; limit={run_config.get('weather_limit', '')}; active NOAA alerts"
# #     if source_key == "apify":
# #         return (
# #             f"enabled={run_config.get('use_apify', False)}, token_present={run_config.get('apify_token_present', False)}, "
# #             f"run_mode={run_config.get('apify_run_mode', 'Skip Apify')}, confirmed={run_config.get('apify_live_confirm', False)}, "
# #             f"geo={run_config.get('apify_geo', '')}, time_range={run_config.get('apify_time_range', APIFY_SAFE_TIME_RANGE)}, "
# #             f"max_keywords={run_config.get('apify_max_keywords', APIFY_HARD_KEYWORD_LIMIT)}"
# #         )
# #     return ""


# # def build_collector_evidence(results: Dict[str, Dict[str, Any]], run_config: Dict[str, Any]) -> List[Dict[str, Any]]:
# #     records = []
# #     enabled_sources = run_config.get("enabled_sources", {})
# #     for source_key in SOURCE_ORDER:
# #         result = results.get(source_key, {})
# #         status = result.get("status", "disabled" if not enabled_sources.get(source_key, False) else "not_run")
# #         normalized_rows = len(result.get("rows", []) or [])
# #         raw_records = count_raw_records(result.get("raw"), result.get("items"))
# #         live_request = status in {"success", "failed", "empty"} or (source_key == "apify" and result.get("raw") is not None)
# #         mock_used = bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
# #         records.append(
# #             {
# #                 "source": SOURCE_LABELS[source_key],
# #                 "status": status,
# #                 "endpoint_or_actor": SOURCE_ENDPOINTS[source_key],
# #                 "request_scope": request_summary(source_key, run_config),
# #                 "raw_records_pulled": raw_records,
# #                 "normalized_feature_rows": normalized_rows,
# #                 "live_request_made": "Yes" if live_request else "No",
# #                 "used_in_llm_payload": "Yes" if normalized_rows > 0 else "No",
# #                 "mock_data_used": "Yes" if mock_used else "No",
# #                 "analysis_method": ANALYSIS_METHODS[source_key],
# #                 "error_or_note": result.get("error", "") or "",
# #             }
# #         )
# #     return records


# # def build_llm_payload(feature_df: pd.DataFrame, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
# #     return {
# #         "features": feature_df.to_dict(orient="records")[:12],
# #         "articles": articles[:8],
# #     }


# # def llm_system_prompt() -> str:
# #     return (
# #         "You are a retail market intelligence analyst. Ground your answer only in the supplied signals. "
# #         "Write concise executive guidance for buyers, category managers, demand planners, and supply-chain teams."
# #     )


# # def llm_user_prompt(retailer: str, region: str, payload: Dict[str, Any]) -> str:
# #     return f"""
# #     Retailer: {retailer}
# #     Region: {region}

# #     Signal payload:
# #     {json.dumps(payload, indent=2, default=str)[:9000]}

# #     Produce:
# #     1. Executive summary in 2-3 sentences
# #     2. Top 3 insights, and for each one cite signal_area, source, risk_score, score_reason, dollar_tree_category, planning_owner, and demand_direction
# #     3. Retail category impact, clearly stating how the signal can become a forecast feature
# #     4. Recommended buyer/category/demand-planning/supply-chain/compliance actions
# #     5. Confidence and limitations

# #     Rules:
# #     - Ground every claim only in the supplied payload.
# #     - Do not invent internal sales, POS, inventory, margin, or category-performance facts.
# #     - If a signal is missing, say it is missing instead of estimating it.
# #     - Explain why each score matters; do not only repeat the number.
# #     - Use retailer-neutral business language when the payload includes category, KPI, planning owner, impact hypothesis, and internal-data requirements.
# #     """


# # def payload_hash(payload: Dict[str, Any]) -> str:
# #     return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# # def build_base_llm_audit(
# #     feature_df: pd.DataFrame,
# #     articles: List[Dict[str, Any]],
# #     retailer: str,
# #     region: str,
# #     model: str,
# # ) -> Dict[str, Any]:
# #     payload = build_llm_payload(feature_df, articles)
# #     return {
# #         "provider": "NVIDIA",
# #         "model": model,
# #         "sent_to_llm": False,
# #         "brief_source": "not_generated",
# #         "fallback_used": False,
# #         "fallback_reason": "",
# #         "mock_data_used": False,
# #         "feature_rows_available": int(len(feature_df)),
# #         "feature_rows_sent": int(len(payload["features"])),
# #         "articles_available": int(len(articles)),
# #         "articles_sent": int(len(payload["articles"])),
# #         "payload_hash_sha256": payload_hash(payload),
# #         "system_prompt": llm_system_prompt(),
# #         "user_prompt": llm_user_prompt(retailer, region, payload),
# #         "payload": payload,
# #         "analysis_contract": "The brief must be grounded only in the collected feature rows and article records shown in this audit view.",
# #     }


# # def any_mock_used(results: Dict[str, Dict[str, Any]], llm_audit: Dict[str, Any]) -> bool:
# #     collector_mock = any(
# #         bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
# #         for result in results.values()
# #     )
# #     return collector_mock or bool(llm_audit.get("mock_data_used", False))


# # def render_audit_banner(mock_used: bool, brief_source: str) -> None:
# #     banner_class = "audit-banner warn" if mock_used else "audit-banner"
# #     title = "Mock Data Detected" if mock_used else "No Mock Data Used In This Run"
# #     body = (
# #         "At least one collector or analysis step is marked as using mock data. Review the evidence table below."
# #         if mock_used
# #         else f"Every feature row shown below comes from the collector outputs for this run. Brief source: {brief_source}."
# #     )
# #     st.markdown(
# #         f"<div class='{banner_class}'><div class='audit-title'>{escape(title)}</div><div class='audit-body'>{escape(body)}</div></div>",
# #         unsafe_allow_html=True,
# #     )


# # def render_audit_card(label: str, value: str, note: str) -> None:
# #     st.markdown(
# #         "<div class='audit-card'>"
# #         f"<div class='audit-label'>{escape(label)}</div>"
# #         f"<div class='audit-value'>{escape(value)}</div>"
# #         f"<div class='audit-note'>{escape(note)}</div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )


# # def validate_nvidia(api_key: str, model: str) -> Tuple[bool, str]:
# #     if not api_key:
# #         return False, "NVIDIA key not provided. Brief generation will use a local fallback."
# #     prompt = "Return exactly: connected"
# #     ok, data, msg = safe_request(
# #         NVIDIA_CHAT_URL,
# #         method="POST",
# #         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
# #         json_body={
# #             "model": model,
# #             "messages": [{"role": "user", "content": prompt}],
# #             "temperature": 0,
# #             "max_tokens": 8,
# #         },
# #         timeout=30,
# #     )
# #     if not ok:
# #         return False, msg
# #     content = data.get("choices", [{}])[0].get("message", {}).get("content")
# #     content_text = str(content or "").strip()
# #     return True, f"Connected. Model responded: {content_text or 'ok'}"


# # def validate_apify(token: str) -> Tuple[bool, str]:
# #     if not token:
# #         return False, "Apify token not provided. Apify collectors will be skipped."
# #     try:
# #         from apify_client import ApifyClient
# #     except ImportError:
# #         return False, "apify-client is not installed. Install requirements before running Apify collectors."
# #     try:
# #         client = ApifyClient(token)
# #         user = client.user().get()
# #         username = user.get("username") or user.get("email") or "Apify user"
# #         return True, f"Connected as {username}."
# #     except Exception as exc:  # pragma: no cover - depends on live Apify service
# #         return False, str(exc)


# # def build_cpi_signal(data: Dict[str, Any], label: str, series_id: str, retailer: str) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
# #     series = data.get("Results", {}).get("series", [])
# #     rows = []
# #     for item in (series[0].get("data", []) if series else [])[:24]:
# #         period = item.get("period", "")
# #         if period == "M13":
# #             continue
# #         try:
# #             cpi_value = float(item["value"])
# #         except (TypeError, ValueError, KeyError):
# #             continue
# #         rows.append(
# #             {
# #                 "category": label,
# #                 "series_id": series_id,
# #                 "year": int(item["year"]),
# #                 "period": period,
# #                 "month": item.get("periodName", ""),
# #                 "cpi_value": cpi_value,
# #             }
# #         )
# #     df = pd.DataFrame(rows)
# #     if df.empty:
# #         return None, df
# #     df = df.sort_values(["year", "period"]).reset_index(drop=True)
# #     df["cpi_mom_change_pct"] = df["cpi_value"].pct_change() * 100
# #     df["cpi_yoy_change_pct"] = df["cpi_value"].pct_change(12) * 100
# #     latest = df.iloc[-1].to_dict()
# #     mom = latest.get("cpi_mom_change_pct")
# #     yoy = latest.get("cpi_yoy_change_pct")
# #     score = 4.0
# #     if pd.notna(mom):
# #         score = min(10.0, max(1.0, 4.0 + float(mom) * 5.0))
# #     if pd.notna(yoy) and yoy > 4:
# #         score = min(10.0, score + 1.0)
# #     mom_label = "unavailable" if pd.isna(mom) else f"{float(mom):.2f}% MoM"
# #     yoy_label = "unavailable" if pd.isna(yoy) else f"{float(yoy):.2f}% YoY"
# #     signal_name = "inflation_pressure_score" if label == "Headline CPI" else f"{label.lower().replace(' ', '_')}_cpi_pressure_score"
# #     signal = {
# #         "date": f"{int(latest['year'])}-{str(latest['period']).replace('M', '').zfill(2)}",
# #         "retailer": retailer,
# #         "region": "US",
# #         "region_scope": "national",
# #         "source": "BLS CPI",
# #         "signal_area": "Inflation" if label == "Headline CPI" else "Category CPI",
# #         "signal_name": signal_name,
# #         "signal_value": round(float(score), 2),
# #         "risk_score": round(float(score), 2),
# #         "confidence": "High",
# #         "score_reason": f"{label} CPI latest value {latest['cpi_value']}; change is {mom_label} and {yoy_label}. Score rises with monthly inflation pressure and elevated YoY inflation.",
# #         "business_impact": f"{label} inflation can affect price sensitivity, category demand, and basket mix.",
# #         "recommended_action": "Use category CPI as an external regressor and validate against internal category sales.",
# #         "raw_reference": f"{label}: CPI {latest['cpi_value']}",
# #     }
# #     return signal, df


# # def collect_bls_cpi(bls_key: str = "", retailer: str = "Retailer") -> Dict[str, Any]:
# #     payload: Dict[str, Any] = {"seriesid": list(BLS_CPI_SERIES.values())}
# #     if bls_key:
# #         payload["registrationkey"] = bls_key
# #     signals = []
# #     tables = []
# #     raw = {}
# #     errors = []
# #     ok, data, msg = safe_request(
# #         "https://api.bls.gov/publicAPI/v2/timeseries/data/",
# #         method="POST",
# #         headers={"Content-Type": "application/json"},
# #         json_body=payload,
# #         timeout=30,
# #     )
# #     if not ok:
# #         return {"status": "failed", "source": "BLS CPI", "error": msg, "raw": None, "rows": []}
# #     if data.get("status") != "REQUEST_SUCCEEDED":
# #         return {
# #             "status": "failed",
# #             "source": "BLS CPI",
# #             "error": "; ".join(data.get("message", [])) or "BLS request was not processed.",
# #             "raw": data,
# #             "rows": [],
# #         }
# #     series_by_id = {
# #         series.get("seriesID"): series
# #         for series in data.get("Results", {}).get("series", [])
# #     }
# #     for label, series_id in BLS_CPI_SERIES.items():
# #         series_payload = {"Results": {"series": [series_by_id.get(series_id, {})]}}
# #         raw[label] = series_payload
# #         signal, df = build_cpi_signal(series_payload, label, series_id, retailer)
# #         if signal:
# #             signals.append(signal)
# #         if not df.empty:
# #             tables.append(df)
# #     if not signals:
# #         return {"status": "failed", "source": "BLS CPI", "error": "; ".join(errors) or "No CPI rows returned.", "raw": raw, "rows": []}
# #     table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
# #     return {"status": "success", "source": "BLS CPI", "error": "; ".join(errors), "raw": raw, "rows": signals, "table": table}


# # def classify_recall(reason: str) -> Tuple[str, float]:
# #     text = (reason or "").lower()
# #     if any(word in text for word in ["lead", "heavy metal", "salmonella", "listeria", "e. coli", "contamination"]):
# #         return "high_safety_risk", 8.0
# #     if any(word in text for word in ["undeclared", "allergen", "milk", "peanut", "soy", "tree nut"]):
# #         return "allergen_risk", 6.5
# #     if any(word in text for word in ["mislabel", "label"]):
# #         return "labeling_risk", 4.5
# #     return "general_recall_risk", 5.0


# # def extract_upcs(text: str) -> List[str]:
# #     candidates = re.findall(r"(?:UPC(?:\s*Code)?[:\s]*)?(\d(?:[\s-]?\d){7,13})", text or "", flags=re.IGNORECASE)
# #     cleaned = []
# #     for candidate in candidates:
# #         digits = re.sub(r"\D", "", candidate)
# #         if 8 <= len(digits) <= 14 and digits not in cleaned:
# #             cleaned.append(digits)
# #     return cleaned


# # def adjust_recall_score(base_score: float, classification: str, status: str) -> float:
# #     score = base_score
# #     class_text = (classification or "").lower()
# #     status_text = (status or "").lower()
# #     if "class i" in class_text:
# #         score += 1.5
# #     elif "class ii" in class_text:
# #         score += 0.8
# #     if "ongoing" in status_text:
# #         score += 1.0
# #     elif "terminated" in status_text:
# #         score -= 1.0
# #     return round(min(10.0, max(1.0, score)), 2)


# # def collect_fda_recalls(query: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
# #     params = {"search": query, "limit": limit}
# #     ok, data, msg = safe_request("https://api.fda.gov/food/enforcement.json", params=params, timeout=30)
# #     if not ok:
# #         return {"status": "failed", "source": "openFDA", "error": msg, "raw": None, "rows": [], "items": []}
# #     results = data.get("results", [])
# #     rows = []
# #     items = []
# #     for item in results:
# #         risk_type, base_score = classify_recall(item.get("reason_for_recall", ""))
# #         product = item.get("product_description", "Unknown product")
# #         state = item.get("state", "US")
# #         classification = item.get("classification", "")
# #         status = item.get("status", "")
# #         score = adjust_recall_score(base_score, classification, status)
# #         upcs = extract_upcs(f"{product} {item.get('code_info', '')}")
# #         items.append(
# #             {
# #                 "product": product,
# #                 "reason": item.get("reason_for_recall", ""),
# #                 "state": state,
# #                 "classification": classification,
# #                 "status": status,
# #                 "recall_date": item.get("recall_initiation_date", ""),
# #                 "distribution_pattern": item.get("distribution_pattern", ""),
# #                 "recalling_firm": item.get("recalling_firm", ""),
# #                 "upcs": ", ".join(upcs) if upcs else "",
# #                 "sku_match_status": "unknown",
# #                 "risk_type": risk_type,
# #                 "risk_score": score,
# #             }
# #         )
# #     aggregate_score = max([x["risk_score"] for x in items], default=1.0)
# #     ongoing_count = sum(1 for x in items if str(x.get("status", "")).lower() == "ongoing")
# #     class_i_count = sum(1 for x in items if "class i" in str(x.get("classification", "")).lower())
# #     states = sorted({str(x.get("state", "")).strip() for x in items if str(x.get("state", "")).strip()})
# #     upc_count = sum(1 for x in items if x.get("upcs"))
# #     top_risk_type = max(items, key=lambda x: x["risk_score"]).get("risk_type", "none") if items else "none"
# #     signal = {
# #         "date": utc_now()[:10],
# #         "retailer": retailer,
# #         "region": "US",
# #         "region_scope": "national_with_state_records",
# #         "source": "openFDA",
# #         "signal_area": "Product Recalls",
# #         "signal_name": "recall_risk_score",
# #         "signal_value": len(items),
# #         "risk_score": round(aggregate_score, 2),
# #         "confidence": "High",
# #         "score_reason": f"Score uses highest adjusted recall severity. Inputs: {len(items)} records, {ongoing_count} ongoing, {class_i_count} Class I, {upc_count} records with UPCs, top risk type {top_risk_type}.",
# #         "affected_states": ", ".join(states[:8]) if states else "Unknown",
# #         "ongoing_count": ongoing_count,
# #         "class_i_count": class_i_count,
# #         "upc_record_count": upc_count,
# #         "sku_match_status": "unknown",
# #         "affected_category": "Food / snacks / candy / beverages",
# #         "business_impact": "Food and beverage recalls can trigger inventory withdrawal, substitution demand, and safety review.",
# #         "recommended_action": f"Prioritize ongoing and Class I recalls, then match UPCs against {retailer} inventory before store-level action.",
# #         "raw_reference": f"{len(items)} recall records; {ongoing_count} ongoing; {class_i_count} Class I",
# #     }
# #     rows.append(signal)
# #     return {"status": "success", "source": "openFDA", "error": "", "raw": data, "rows": rows, "items": items}


# # def normalize_weather_area(area: str) -> str:
# #     candidate = re.sub(r"[^A-Za-z]", "", area or "").upper()
# #     if len(candidate) == 2:
# #         return candidate
# #     return "TX"


# # def weather_alert_weight(severity: str, urgency: str, certainty: str) -> float:
# #     severity_score = {
# #         "extreme": 5.0,
# #         "severe": 3.0,
# #         "moderate": 2.0,
# #         "minor": 1.0,
# #         "unknown": 1.0,
# #     }.get(str(severity or "").lower(), 1.0)
# #     urgency_bonus = {
# #         "immediate": 1.5,
# #         "expected": 0.75,
# #     }.get(str(urgency or "").lower(), 0.0)
# #     certainty_bonus = {
# #         "observed": 0.5,
# #         "likely": 0.5,
# #     }.get(str(certainty or "").lower(), 0.0)
# #     return severity_score + urgency_bonus + certainty_bonus


# # def collect_weather_alerts(area: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
# #     state_area = normalize_weather_area(area)
# #     params = {"area": state_area}
# #     ok, data, msg = safe_request(
# #         "https://api.weather.gov/alerts/active",
# #         headers={"User-Agent": "MarketIntelligenceWorkbench/1.0", "Accept": "application/geo+json"},
# #         params=params,
# #         timeout=30,
# #     )
# #     if not ok:
# #         return {"status": "failed", "source": "NOAA Weather Alerts", "error": msg, "raw": None, "rows": [], "items": []}
# #     features = data.get("features", []) if isinstance(data, dict) else []
# #     selected_alerts = features[: max(1, int(limit))]
# #     items = []
# #     score_components = []
# #     severe_count = 0
# #     extreme_count = 0
# #     for alert in selected_alerts:
# #         props = alert.get("properties", {}) if isinstance(alert, dict) else {}
# #         severity = props.get("severity", "Unknown")
# #         urgency = props.get("urgency", "Unknown")
# #         certainty = props.get("certainty", "Unknown")
# #         component = weather_alert_weight(severity, urgency, certainty)
# #         score_components.append(component)
# #         severity_text = str(severity or "").lower()
# #         if severity_text == "extreme":
# #             extreme_count += 1
# #         if severity_text in {"severe", "extreme"}:
# #             severe_count += 1
# #         items.append(
# #             {
# #                 "event": props.get("event", ""),
# #                 "severity": severity,
# #                 "urgency": urgency,
# #                 "certainty": certainty,
# #                 "headline": props.get("headline", ""),
# #                 "area_desc": props.get("areaDesc", ""),
# #                 "effective": props.get("effective", ""),
# #                 "expires": props.get("expires", ""),
# #                 "instruction": props.get("instruction", ""),
# #                 "risk_component": round(component, 2),
# #             }
# #         )
# #     risk_score = round(min(10.0, sum(score_components)), 2) if items else 0.0
# #     if items:
# #         score_reason = (
# #             f"Score sums weighted active NOAA alerts for {state_area}, capped at 10. "
# #             f"Inputs: {len(items)} alert(s), {severe_count} severe/extreme, {extreme_count} extreme; "
# #             f"severity/urgency/certainty components total {sum(score_components):.2f}."
# #         )
# #         raw_reference = f"{len(items)} active NOAA alert(s); top event: {items[0].get('event') or 'Unknown'}"
# #         recommended_action = "Check affected counties against store and DC routes; use alert severity as a short-horizon disruption and emergency-demand feature."
# #     else:
# #         score_reason = f"NOAA returned 0 active alerts for {state_area}. Score is 0 because no current weather disruption signal is present."
# #         raw_reference = "0 active NOAA alerts"
# #         recommended_action = "Keep weather feature at baseline for this state, then refresh before short-horizon replenishment decisions."
# #     signal = {
# #         "date": utc_now()[:10],
# #         "retailer": retailer,
# #         "region": state_area,
# #         "region_scope": "state_weather_alerts",
# #         "source": "NOAA Weather Alerts",
# #         "signal_area": "Weather Risk",
# #         "signal_name": "supply_chain_weather_risk_score",
# #         "signal_value": len(items),
# #         "risk_score": risk_score,
# #         "confidence": "High",
# #         "score_reason": score_reason,
# #         "alert_count": len(items),
# #         "severe_or_extreme_count": severe_count,
# #         "extreme_count": extreme_count,
# #         "business_impact": "Active weather alerts can disrupt store traffic, DC-to-store routes, staffing, replenishment timing, and emergency-demand categories.",
# #         "recommended_action": recommended_action,
# #         "raw_reference": raw_reference,
# #     }
# #     return {"status": "success", "source": "NOAA Weather Alerts", "error": "", "raw": data, "rows": [signal], "items": items}


# # def gnews_package_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
# #     try:
# #         from gnews import GNews
# #     except ImportError:
# #         return None
# #     google_news = GNews(language=language, country=country, period=period, max_results=max_results)
# #     return google_news.get_news(keyword)


# # def google_news_rss_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> List[Dict[str, Any]]:
# #     # Google News RSS supports a "when:" query operator. GNews package is preferred when installed.
# #     query = quote_plus(f"{keyword} when:{period}")
# #     country_code = country.upper()
# #     lang_code = language.lower()
# #     url = f"https://news.google.com/rss/search?q={query}&hl={lang_code}-{country_code}&gl={country_code}&ceid={country_code}:{lang_code}"
# #     response = requests.get(url, timeout=30)
# #     response.raise_for_status()
# #     root = ET.fromstring(response.content)
# #     articles = []
# #     for item in root.findall(".//item")[:max_results]:
# #         source_node = item.find("source")
# #         articles.append(
# #             {
# #                 "title": item.findtext("title", default=""),
# #                 "description": item.findtext("description", default=""),
# #                 "published date": item.findtext("pubDate", default=""),
# #                 "url": item.findtext("link", default=""),
# #                 "publisher": source_node.text if source_node is not None else "",
# #             }
# #         )
# #     return articles


# # def clean_news_description(description: str) -> str:
# #     text = re.sub(r"<[^>]+>", " ", description or "")
# #     text = unescape(text)
# #     text = re.sub(r"\s+", " ", text).strip()
# #     return text


# # def article_days_old(published_date: str) -> Optional[int]:
# #     if not published_date:
# #         return None
# #     try:
# #         published = parsedate_to_datetime(published_date)
# #         if published.tzinfo is None:
# #             published = published.replace(tzinfo=timezone.utc)
# #         return max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
# #     except (TypeError, ValueError):
# #         return None


# # def classify_news_title(title: str, description: str) -> Tuple[str, float, str]:
# #     text = f"{title} {description}".lower()
# #     if any(word in text for word in ["recall", "contamination", "lawsuit", "closure", "closing", "tariff", "warning"]):
# #         return "risk_event", 7.0, "negative"
# #     if any(word in text for word in ["inflation", "prices", "freight", "cost", "margin"]):
# #         return "price_pressure", 6.0, "negative"
# #     if any(word in text for word in ["deal", "sale", "promotion", "coupon", "holiday", "seasonal"]):
# #         return "demand_opportunity", 6.5, "positive"
# #     if any(word in text for word in ["earnings", "forecast", "guidance", "outlook"]):
# #         return "financial_update", 5.5, "neutral"
# #     return "general_market_news", 3.5, "neutral"


# # def collect_gnews(keywords: List[str], country: str, language: str, period: str, max_results: int, retailer: str = "Retailer") -> Dict[str, Any]:
# #     all_articles = []
# #     errors = []
# #     per_keyword_limit = max(1, int(max_results / max(1, len(keywords))))
# #     for keyword in keywords:
# #         try:
# #             articles = gnews_package_collect(keyword, country, language, period, per_keyword_limit)
# #             if articles is None:
# #                 articles = google_news_rss_collect(keyword, country, language, period, per_keyword_limit)
# #             for article in articles or []:
# #                 description = clean_news_description(article.get("description", ""))
# #                 event_type, score, sentiment = classify_news_title(article.get("title", ""), description)
# #                 publisher = article.get("publisher", "")
# #                 if isinstance(publisher, dict):
# #                     publisher = publisher.get("title") or publisher.get("href") or ""
# #                 published_date = article.get("published date") or article.get("published_date", "")
# #                 days_old = article_days_old(published_date)
# #                 if days_old is not None and days_old > 30:
# #                     score = max(1.0, score - 1.0)
# #                 all_articles.append(
# #                     {
# #                         "keyword": keyword,
# #                         "title": article.get("title", ""),
# #                         "description": description,
# #                         "published_date": published_date,
# #                         "days_old": days_old,
# #                         "publisher": publisher,
# #                         "url": article.get("url", ""),
# #                         "source_tier": source_confidence(str(publisher)),
# #                         "event_type": event_type,
# #                         "sentiment": sentiment,
# #                         "risk_score": score,
# #                         "confidence": source_confidence(str(publisher)),
# #                     }
# #                 )
# #         except Exception as exc:
# #             errors.append(f"{keyword}: {exc}")

# #     deduped = []
# #     seen = set()
# #     for article in all_articles:
# #         key = article["url"] or article["title"]
# #         if key and key not in seen:
# #             seen.add(key)
# #             deduped.append(article)

# #     if not deduped:
# #         return {
# #             "status": "failed" if errors else "empty",
# #             "source": "GNews",
# #             "error": "; ".join(errors) if errors else "No meaningful articles returned for selected keywords.",
# #             "raw": [],
# #             "rows": [],
# #             "items": [],
# #         }

# #     score = round(float(pd.Series([a["risk_score"] for a in deduped]).mean()), 2) if deduped else 1.0
# #     negative_count = sum(1 for article in deduped if article.get("sentiment") == "negative")
# #     high_conf_count = sum(1 for article in deduped if article.get("confidence") == "High")
# #     event_counts = pd.Series([article.get("event_type", "unknown") for article in deduped]).value_counts().to_dict()
# #     signal = {
# #         "date": utc_now()[:10],
# #         "retailer": retailer,
# #         "region": country.upper(),
# #         "region_scope": "country_news",
# #         "source": "GNews / Google News RSS",
# #         "signal_area": "Retail News",
# #         "signal_name": "news_risk_score",
# #         "signal_value": len(deduped),
# #         "risk_score": score,
# #         "confidence": "Medium",
# #         "score_reason": f"Average article risk across {len(deduped)} deduped articles; {negative_count} negative articles; {high_conf_count} high-confidence publishers; event mix {event_counts}.",
# #         "business_impact": "Recent news can reveal competitor moves, pricing pressure, recalls, store changes, and supply-chain risk.",
# #         "recommended_action": "Review high-risk articles and use NVIDIA classification before executive distribution.",
# #         "raw_reference": f"{len(deduped)} articles",
# #     }
# #     return {"status": "success", "source": "GNews", "error": "; ".join(errors), "raw": deduped, "rows": [signal], "items": deduped}


# # def get_apify_value(obj: Any, key: str) -> Any:
# #     if isinstance(obj, dict):
# #         return obj.get(key)
# #     if hasattr(obj, key):
# #         return getattr(obj, key)
# #     try:
# #         return obj[key]
# #     except (TypeError, KeyError, AttributeError):
# #         return None


# # def collect_apify_trends(
# #     token: str,
# #     keywords: List[str],
# #     geo: str,
# #     time_range: str,
# #     retailer: str = "Retailer",
# #     max_keywords: int = APIFY_HARD_KEYWORD_LIMIT,
# # ) -> Dict[str, Any]:
# #     if not token:
# #         return {"status": "skipped", "source": "Apify Trends", "error": "No Apify token provided.", "raw": None, "rows": [], "items": []}
# #     try:
# #         from apify_client import ApifyClient
# #     except ImportError:
# #         return {"status": "failed", "source": "Apify Trends", "error": "apify-client is not installed.", "raw": None, "rows": [], "items": []}
# #     try:
# #         client = ApifyClient(token)
# #         safe_max_keywords = min(max(1, int(max_keywords)), APIFY_HARD_KEYWORD_LIMIT)
# #         safe_time_range = time_range if time_range in APIFY_ALLOWED_TIME_RANGES else APIFY_SAFE_TIME_RANGE
# #         safe_time_range = safe_time_range or APIFY_SAFE_TIME_RANGE
# #         selected_keywords = [kw for kw in keywords if kw][:safe_max_keywords]
# #         if not selected_keywords:
# #             return {"status": "skipped", "source": "Apify Trends", "error": "No trend keywords provided.", "raw": None, "rows": [], "items": []}
# #         run_input = {"geo": geo, "searchTerms": selected_keywords, "timeRange": safe_time_range}
# #         run = client.actor("apify/google-trends-scraper").call(run_input=run_input)
# #         dataset_id = get_apify_value(run, "defaultDatasetId") or get_apify_value(run, "default_dataset_id")
# #         if not dataset_id:
# #             return {
# #                 "status": "failed",
# #                 "source": "Apify Trends",
# #                 "error": "Apify run completed but no default dataset ID was found.",
# #                 "raw": run_input,
# #                 "rows": [],
# #                 "items": [],
# #             }
# #         items = list(client.dataset(dataset_id).iterate_items())
# #     except Exception as exc:
# #         return {"status": "failed", "source": "Apify Trends", "error": str(exc), "raw": None, "rows": [], "items": []}

# #     region_rows = []
# #     for item in items:
# #         keyword = item.get("searchTerm")
# #         for rank, region in enumerate(item.get("interestBySubregion", []) or [], start=1):
# #             values = region.get("value") or []
# #             if values:
# #                 region_rows.append(
# #                     {
# #                         "keyword": keyword,
# #                         "region": region.get("geoName", ""),
# #                         "interest_score": values[0],
# #                         "rank": rank,
# #                     }
# #                 )
# #     top_score = max([float(x["interest_score"]) for x in region_rows], default=0.0)
# #     signal_score = round(min(10.0, max(1.0, top_score / 10.0)), 2) if top_score else 1.0
# #     signal = {
# #         "date": utc_now()[:10],
# #         "retailer": retailer,
# #         "region": geo,
# #         "region_scope": "trend_geo",
# #         "source": "Apify Google Trends",
# #         "signal_area": "Search Demand",
# #         "signal_name": "search_demand_score",
# #         "signal_value": top_score,
# #         "risk_score": signal_score,
# #         "confidence": "Medium",
# #         "score_reason": f"Score is top regional Google Trends interest divided by 10. Run hard-limited to {len(selected_keywords)} keyword(s) over {safe_time_range} to control Apify quota and memory.",
# #         "business_impact": "Search interest can reveal early demand shifts, promotional interest, and seasonal spikes.",
# #         "recommended_action": "Compare rising regions against sales, inventory, and competitor promotion calendars.",
# #         "raw_reference": f"{len(region_rows)} regional trend rows",
# #     }
# #     return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [signal], "items": region_rows}


# # def generate_fallback_brief(feature_df: pd.DataFrame, retailer: str, region: str) -> str:
# #     if feature_df.empty:
# #         return (
# #             "Executive Summary:\n"
# #             "- No external signals were collected for this run.\n"
# #             "- Enable at least one source and run the workbench again before using the output for planning.\n\n"
# #             "Confidence And Limitations:\n"
# #             "- No score can be explained because no feature rows exist."
# #         )
# #     strongest = feature_df.sort_values("risk_score", ascending=False).head(3)
# #     avg_score = round(float(feature_df["risk_score"].mean()), 2)
# #     top = strongest.iloc[0]
# #     sources = ", ".join(sorted({str(src) for src in feature_df["source"].dropna().tolist()}))
# #     lines = [
# #         "Executive Summary:",
# #         f"- {retailer} in {region} has {risk_band(avg_score).lower()} external signal intensity with an average score of {avg_score}/10 across {len(feature_df)} forecast-ready row(s).",
# #         f"- The strongest current signal is {top['signal_area']} at {float(top['risk_score']):.2f}/10 from {top['source']}.",
# #         f"- This brief is grounded in collected source output only: {sources}.",
# #         "",
# #         "Top Signal Evidence:",
# #     ]
# #     for idx, (_, row) in enumerate(strongest.iterrows(), start=1):
# #         lines.append(
# #             f"{idx}. {row['signal_area']} scored {float(row['risk_score']):.2f}/10 from {row['source']}. Why: {row.get('score_reason', 'No score reason available.')}"
# #         )
# #         lines.append(f"- Business impact: {row.get('business_impact', 'No business impact available.')}")
# #     lines.extend(
# #         [
# #             "",
# #             "Forecasting Relevance:",
# #             "- Treat each score as an external regressor candidate, not as a final demand forecast.",
# #             "- Join these rows to internal POS, category, store, promotion, and inventory data before model training or operational action.",
# #             "",
# #             "Recommended Actions:",
# #         ]
# #     )
# #     for idx, (_, row) in enumerate(strongest.iterrows(), start=1):
# #         lines.append(f"{idx}. {row.get('recommended_action', 'Review this signal with the category owner.')}")
# #     lines.extend(
# #         [
# #             "",
# #             "Confidence And Limitations:",
# #             "- Scores are explainable directional signals from public API data, not proof of actual Retailer demand movement.",
# #             "- Category performance still requires internal sales/POS data; external APIs explain context but do not replace internal performance data.",
# #         ]
# #     )
# #     return "\n".join(lines)


# # def generate_nvidia_brief(
# #     api_key: str,
# #     model: str,
# #     feature_df: pd.DataFrame,
# #     articles: List[Dict[str, Any]],
# #     retailer: str,
# #     region: str,
# # ) -> Tuple[str, str, Dict[str, Any]]:
# #     audit = build_base_llm_audit(feature_df, articles, retailer, region, model)
# #     if not api_key:
# #         audit.update(
# #             {
# #                 "provider": "Local deterministic fallback",
# #                 "model": "rule_based_summary",
# #                 "brief_source": "fallback",
# #                 "fallback_used": True,
# #                 "fallback_reason": "No NVIDIA API key provided. No external LLM call was made.",
# #             }
# #         )
# #         return generate_fallback_brief(feature_df, retailer, region), "fallback", audit
# #     request_body = {
# #         "model": model,
# #         "messages": [{"role": "system", "content": audit["system_prompt"]}, {"role": "user", "content": audit["user_prompt"]}],
# #         "temperature": 0.2,
# #         "max_tokens": 650,
# #     }
# #     attempts: List[Dict[str, Any]] = []
# #     ok = False
# #     data: Any = None
# #     msg = ""
# #     for attempt_number, timeout_seconds in enumerate([45, 25], start=1):
# #         started = time.monotonic()
# #         ok, data, msg = safe_request(
# #             NVIDIA_CHAT_URL,
# #             method="POST",
# #             headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
# #             json_body=request_body,
# #             timeout=timeout_seconds,
# #         )
# #         elapsed_ms = int((time.monotonic() - started) * 1000)
# #         attempts.append(
# #             {
# #                 "attempt": attempt_number,
# #                 "timeout_seconds": timeout_seconds,
# #                 "elapsed_ms": elapsed_ms,
# #                 "success": ok,
# #                 "technical_error": "" if ok else msg,
# #             }
# #         )
# #         if ok:
# #             break
# #     if not ok:
# #         brief_source, friendly_reason = friendly_nvidia_failure(msg)
# #         audit.update(
# #             {
# #                 "provider": "Local deterministic fallback",
# #                 "model": "rule_based_summary",
# #                 "brief_source": brief_source,
# #                 "fallback_used": True,
# #                 "fallback_reason": friendly_reason,
# #                 "nvidia_attempts": attempts,
# #                 "technical_error": msg,
# #             }
# #         )
# #         return generate_fallback_brief(feature_df, retailer, region), brief_source, audit
# #     content = data.get("choices", [{}])[0].get("message", {}).get("content")
# #     content_text = str(content or "").strip()
# #     if not content_text:
# #         audit.update(
# #             {
# #                 "provider": "Local deterministic fallback",
# #                 "model": "rule_based_summary",
# #                 "brief_source": "fallback: empty NVIDIA response",
# #                 "fallback_used": True,
# #                 "fallback_reason": "NVIDIA returned an empty response.",
# #                 "nvidia_attempts": attempts,
# #             }
# #         )
# #         return generate_fallback_brief(feature_df, retailer, region), "fallback: empty NVIDIA response", audit
# #     audit.update(
# #         {
# #             "provider": "NVIDIA",
# #             "model": model,
# #             "sent_to_llm": True,
# #             "brief_source": "nvidia",
# #             "fallback_used": False,
# #             "fallback_reason": "",
# #             "response_chars": len(content_text),
# #             "nvidia_attempts": attempts,
# #             "response_time_ms": attempts[-1]["elapsed_ms"] if attempts else 0,
# #         }
# #     )
# #     return content_text, "nvidia", audit


# # def render_metric_card(title: str, value: str, note: str, confidence: str = "") -> None:
# #     pill = f'<span class="pill {confidence_class(confidence)}">{confidence}</span>' if confidence else ""
# #     html = (
# #         '<div class="metric-card">'
# #         f'<div class="metric-label">{title}</div>'
# #         f'<div class="metric-value">{value}</div>'
# #         f"{pill}"
# #         f'<div class="metric-note">{note}</div>'
# #         "</div>"
# #     )
# #     st.markdown(
# #         html,
# #         unsafe_allow_html=True,
# #     )


# # def compute_composite_scores(feature_df: pd.DataFrame) -> Dict[str, float]:
# #     if feature_df.empty:
# #         return {"Market Opportunity": 0.0, "Market Risk": 0.0, "Forecast Impact": 0.0}
# #     area_scores: Dict[str, float] = {}
# #     for _, row in feature_df.iterrows():
# #         if pd.isna(row.get("risk_score")):
# #             continue
# #         area = str(row["signal_area"])
# #         area_scores[area] = max(area_scores.get(area, 0.0), float(row["risk_score"]))
# #     opportunity = max(
# #         area_scores.get("Retail News", 0.0),
# #         area_scores.get("Search Demand", 0.0),
# #         area_scores.get("Category CPI", 0.0) * 0.7,
# #     )
# #     risk = max(
# #         area_scores.get("Product Recalls", 0.0),
# #         area_scores.get("Weather Risk", 0.0),
# #         area_scores.get("Inflation", 0.0),
# #         area_scores.get("Category CPI", 0.0),
# #     )
# #     impact = min(10.0, (opportunity * 0.45) + (risk * 0.45) + (len(feature_df) * 0.25))
# #     return {
# #         "Market Opportunity": round(opportunity, 2),
# #         "Market Risk": round(risk, 2),
# #         "Forecast Impact": round(impact, 2),
# #     }


# # def compute_dollar_tree_kpis(feature_df: pd.DataFrame) -> Dict[str, float]:
# #     if feature_df.empty:
# #         return {
# #             "Value Basket Pressure": 0.0,
# #             "Safety And Compliance Risk": 0.0,
# #             "Supply Chain Disruption": 0.0,
# #             "Demand Signal Priority": 0.0,
# #         }
# #     def max_for(column: str, values: List[str]) -> float:
# #         if column not in feature_df.columns:
# #             return 0.0
# #         mask = feature_df[column].astype(str).isin(values)
# #         if not mask.any():
# #             return 0.0
# #         return float(feature_df.loc[mask, "risk_score"].max())

# #     value_pressure = max(
# #         max_for("enterprise_kpi", ["Value Basket Pressure", "Consumer Wallet Pressure", "Consumables Demand Pressure", "Household Essentials Pressure"]),
# #         max_for("signal_area", ["Inflation", "Category CPI"]),
# #     )
# #     safety = max(max_for("enterprise_kpi", ["Safety And Compliance Risk"]), max_for("signal_area", ["Product Recalls"]))
# #     disruption = max(max_for("enterprise_kpi", ["Supply Chain Disruption Risk"]), max_for("signal_area", ["Weather Risk"]))
# #     demand = max(
# #         max_for("enterprise_kpi", ["Demand Interest Spike", "Market Event Risk"]),
# #         max_for("signal_area", ["Search Demand", "Retail News"]),
# #     )
# #     return {
# #         "Value Basket Pressure": round(value_pressure, 2),
# #         "Safety And Compliance Risk": round(safety, 2),
# #         "Supply Chain Disruption": round(disruption, 2),
# #         "Demand Signal Priority": round(demand, 2),
# #     }


# # def build_trust_metrics(run: Dict[str, Any], feature_df: pd.DataFrame) -> List[Dict[str, str]]:
# #     results = run.get("results", {})
# #     llm_audit = run.get("llm_audit", {})
# #     evidence_records = build_collector_evidence(results, run.get("run_config", {}))
# #     live_sources = sum(1 for record in evidence_records if record.get("live_request_made") == "Yes")
# #     raw_records = sum(int(record.get("raw_records_pulled", 0) or 0) for record in evidence_records)
# #     mock_used = any_mock_used(results, llm_audit)
# #     brief_source = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
# #     return [
# #         {"label": "Mock Data", "value": "No" if not mock_used else "Yes", "note": "Collector and LLM audit flags inspected.", "state": "good" if not mock_used else "warn"},
# #         {"label": "Live Sources", "value": str(live_sources), "note": "Sources that made a live request or returned live evidence.", "state": "good"},
# #         {"label": "Raw Records", "value": str(raw_records), "note": "Inspectable source records behind the normalized rows.", "state": "good" if raw_records else "warn"},
# #         {"label": "Feature Rows", "value": str(len(feature_df)), "note": "Forecast-ready external signal rows generated.", "state": "good" if len(feature_df) else "warn"},
# #         {"label": "Brief Mode", "value": brief_source, "note": str(llm_audit.get("fallback_reason") or "AI brief grounded in shown payload."), "state": "good" if brief_source == "NVIDIA" else "warn"},
# #     ]


# # def render_trust_panel(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
# #     cards = []
# #     for metric in build_trust_metrics(run, feature_df):
# #         cards.append(
# #             f"<div class='trust-card {escape(metric.get('state', 'good'))}'>"
# #             f"<div class='trust-label'>{escape(metric['label'])}</div>"
# #             f"<div class='trust-value'>{escape(metric['value'])}</div>"
# #             f"<div class='trust-note'>{escape(metric['note'])}</div>"
# #             "</div>"
# #         )
# #     st.markdown("<div class='trust-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


# # def render_enterprise_context(retailer_name: str) -> None:
# #     st.markdown(
# #         "<div class='enterprise-band'>"
# #         f"<div class='enterprise-band-title'>{escape(retailer_name)} External Signal Control Layer</div>"
# #         "<div class='enterprise-band-copy'>"
# #         "This view translates public signals into category, owner, forecast-feature, and action language for buyers, category managers, demand planners, compliance, and supply-chain teams. "
# #         "It does not claim internal category performance until POS, inventory, product hierarchy, promotion, vendor, and store/DC data are connected."
# #         "</div></div>",
# #         unsafe_allow_html=True,
# #     )


# # def render_results_command_header(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
# #     run_config = run.get("run_config", {})
# #     retailer_name = str(run_config.get("retailer", retailer_label))
# #     llm_audit = run.get("llm_audit", {})
# #     source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
# #     avg_score = float(feature_df["risk_score"].mean()) if not feature_df.empty and "risk_score" in feature_df.columns else 0.0
# #     top_label = "No signal"
# #     if not feature_df.empty:
# #         top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
# #         top_label = f"{top.get('signal_area', 'Signal')} / {float(top.get('risk_score', 0) or 0):.2f}"
# #     brief_mode = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
# #     meta = [
# #         ("Run Time", str(run.get("timestamp", ""))),
# #         ("Brief Mode", brief_mode),
# #         ("Sources", f"{source_count} source(s)"),
# #         ("Avg Score", f"{avg_score:.2f} / 10"),
# #     ]
# #     meta_html = "".join(
# #         f"<div class='result-meta-cell'><div class='result-meta-label'>{escape(label)}</div><div class='result-meta-value'>{escape(value)}</div></div>"
# #         for label, value in meta
# #     )
# #     note = llm_audit.get("fallback_reason") or "Executive brief is grounded in the normalized source rows shown in this run."
# #     st.markdown(
# #         "<div class='result-command'>"
# #         "<div>"
# #         "<div class='config-eyebrow'>Results Command Center</div>"
# #         f"<div class='result-command-title'>{escape(retailer_name)} external signal readout</div>"
# #         f"<div class='result-command-copy'>Top signal: {escape(top_label)}. {escape(str(note))} Use this page from left to right: decision summary, category impact, score logic, then raw evidence.</div>"
# #         "</div>"
# #         f"<div class='result-command-meta'>{meta_html}</div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )


# # def render_decision_summary(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> None:
# #     cards = []
# #     for action in build_recommended_actions(feature_df, results):
# #         cards.append(
# #             "<div class='decision-card'>"
# #             f"<div class='decision-owner'>{escape(action['label'])}</div>"
# #             f"<div class='decision-title'>{escape(action['title'])}</div>"
# #             f"<div class='decision-body'>{escape(action['body'])}</div>"
# #             "</div>"
# #         )
# #     if not cards:
# #         cards.append(
# #             "<div class='decision-card'><div class='decision-owner'>Setup</div><div class='decision-title'>Run signal sources</div><div class='decision-body'>No decision cards are available until forecast-ready rows are generated.</div></div>"
# #         )
# #     st.markdown("<div class='decision-grid'>" + "".join(cards[:4]) + "</div>", unsafe_allow_html=True)


# # def render_explainability_ladder() -> None:
# #     steps = [
# #         ("01", "Collect", "Live public APIs return raw evidence; skipped sources are labeled."),
# #         ("02", "Normalize", "Records become forecast-ready rows with source, signal, region, and score fields."),
# #         ("03", "Map", "Rows are mapped to retail category, owner, KPI, and forecast feature."),
# #         ("04", "Score", "Each source uses a visible rule; score_reason explains the exact driver."),
# #         ("05", "Brief", "NVIDIA or fallback summarizes only the shown rows and article context."),
# #     ]
# #     html = "".join(
# #         "<div class='explain-step'>"
# #         f"<div class='explain-step-num'>{escape(num)}</div>"
# #         f"<div class='explain-step-title'>{escape(title)}</div>"
# #         f"<div class='explain-step-copy'>{escape(copy)}</div>"
# #         "</div>"
# #         for num, title, copy in steps
# #     )
# #     st.markdown("<div class='explain-ladder'>" + html + "</div>", unsafe_allow_html=True)


# # def render_audit_command_header(
# #     run: Dict[str, Any],
# #     mock_used: bool,
# #     raw_records: int,
# #     pulled_sources: int,
# #     llm_audit: Dict[str, Any],
# #     feature_df: pd.DataFrame,
# # ) -> None:
# #     brief_mode = "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback"
# #     cells = [
# #         ("Mock Data", "No" if not mock_used else "Yes"),
# #         ("Raw Records", str(raw_records)),
# #         ("Live Sources", str(pulled_sources)),
# #         ("Brief Mode", brief_mode),
# #     ]
# #     cell_html = "".join(
# #         f"<div class='audit-status-cell'><div class='audit-status-label'>{escape(label)}</div><div class='audit-status-value'>{escape(value)}</div></div>"
# #         for label, value in cells
# #     )
# #     status_copy = (
# #         "All generated rows are traceable to collector outputs and the brief is tied to the shown payload."
# #         if not mock_used
# #         else "At least one collector or analysis step is marked as mock. Review provenance before using this run."
# #     )
# #     if llm_audit.get("fallback_used"):
# #         status_copy += f" Brief fallback reason: {llm_audit.get('fallback_reason', 'NVIDIA unavailable')}."
# #     st.markdown(
# #         "<div class='audit-command'>"
# #         "<div>"
# #         "<div class='config-eyebrow'>Evidence Audit</div>"
# #         "<div class='audit-command-title'>Run provenance and chain of custody</div>"
# #         f"<div class='audit-command-copy'>{escape(status_copy)} Feature rows available: {len(feature_df)}.</div>"
# #         "</div>"
# #         f"<div class='audit-status-grid'>{cell_html}</div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )


# # def render_audit_lineage() -> None:
# #     steps = [
# #         ("01", "Request", "Run config stores source toggles, query scope, limits, and guarded Apify mode."),
# #         ("02", "Collect", "Each collector records status, endpoint/actor, raw record count, and errors."),
# #         ("03", "Normalize", "Collector rows become forecast-ready signals with score reasons and raw references."),
# #         ("04", "Analyze", "Brief payload is hashed and capped; NVIDIA/fallback status is recorded separately."),
# #         ("05", "Inspect", "Raw payloads, cleaned items, normalized rows, prompt, and output can be reviewed."),
# #     ]
# #     html = "".join(
# #         "<div class='audit-lineage-step'>"
# #         f"<div class='audit-lineage-num'>{escape(num)}</div>"
# #         f"<div class='audit-lineage-title'>{escape(title)}</div>"
# #         f"<div class='audit-lineage-copy'>{escape(copy)}</div>"
# #         "</div>"
# #         for num, title, copy in steps
# #     )
# #     st.markdown("<div class='audit-lineage'>" + html + "</div>", unsafe_allow_html=True)


# # def render_dollar_tree_impact_matrix(feature_df: pd.DataFrame) -> None:
# #     if feature_df.empty:
# #         st.info("No retail impact matrix is available until feature rows are generated.")
# #         return
# #     preferred_cols = [
# #         "dollar_tree_category",
# #         "enterprise_kpi",
# #         "risk_score",
# #         "action_priority",
# #         "planning_owner",
# #         "demand_direction",
# #         "forecast_feature",
# #         "impact_hypothesis",
# #         "internal_data_needed",
# #         "source",
# #     ]
# #     visible_cols = [col for col in preferred_cols if col in feature_df.columns]
# #     matrix = feature_df.sort_values(["action_priority", "risk_score"], ascending=[True, False])[visible_cols]
# #     st.dataframe(matrix, width="stretch", hide_index=True)


# # def render_scenario_simulator(feature_df: pd.DataFrame) -> None:
# #     scenarios = {
# #         "Inflation rises again": {
# #             "category": "Total value basket, food, household essentials",
# #             "owner": "Merchandising Strategy + Demand Planning",
# #             "feature": "headline_cpi_value_pressure",
# #             "action": "Watch trade-down behavior, validate basket mix, and review value-sensitive replenishment.",
# #         },
# #         "FDA recall affects consumables": {
# #             "category": "Snacks, candy, beverages, consumables",
# #             "owner": "Compliance + Category Buyer",
# #             "feature": "recall_exposure_score",
# #             "action": "Match UPCs against SKU master, isolate affected inventory, and prepare substitute-item monitoring.",
# #         },
# #         "Severe weather hits selected state": {
# #             "category": "Emergency demand and replenishment-sensitive categories",
# #             "owner": "Supply Chain + Demand Planning",
# #             "feature": "state_weather_disruption_score",
# #             "action": "Check store/DC exposure, route risk, and short-horizon emergency-demand uplift.",
# #         },
# #         "Competitor promotion pressure rises": {
# #             "category": "Overlapping value categories and seasonal assortment",
# #             "owner": "Buyer + Category Manager",
# #             "feature": "competitor_promotion_pressure_score",
# #             "action": "Compare overlapping items, review promotional calendar, and watch category conversion.",
# #         },
# #     }
# #     selected = st.selectbox("Scenario", list(scenarios.keys()), label_visibility="collapsed")
# #     scenario = scenarios[selected]
# #     evidence_note = "No current run evidence matched this scenario directly."
# #     if not feature_df.empty and "forecast_feature" in feature_df.columns:
# #         matching = feature_df[feature_df["forecast_feature"].astype(str).str.contains(scenario["feature"].split("_")[0], case=False, na=False)]
# #         if not matching.empty:
# #             top = matching.sort_values("risk_score", ascending=False).iloc[0]
# #             evidence_note = f"Nearest current signal: {top.get('signal_area')} from {top.get('source')} scored {float(top.get('risk_score', 0) or 0):.2f}/10."
# #     st.markdown(
# #         "<div class='enterprise-band'>"
# #         f"<div class='enterprise-band-title'>{escape(selected)}</div>"
# #         f"<div class='enterprise-band-copy'><strong>Likely retail category:</strong> {escape(scenario['category'])}<br>"
# #         f"<strong>Owner:</strong> {escape(scenario['owner'])}<br>"
# #         f"<strong>Forecast feature:</strong> {escape(scenario['feature'])}<br>"
# #         f"<strong>Action:</strong> {escape(scenario['action'])}<br>"
# #         f"<strong>Run evidence:</strong> {escape(evidence_note)}</div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )


# # def build_recommended_actions(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
# #     actions = []
# #     if feature_df.empty:
# #         return [{"label": "Setup", "title": "Run signal sources", "body": "Enable at least one source to generate forecast-ready rows."}]
# #     top_rows = feature_df.sort_values("risk_score", ascending=False).head(3)
# #     for _, row in top_rows.iterrows():
# #         owner = str(row.get("planning_owner") or "Planning Owner")
# #         category = str(row.get("dollar_tree_category") or row.get("signal_area") or "Category")
# #         hypothesis = str(row.get("impact_hypothesis") or row.get("recommended_action") or "Review this signal with the category owner.")
# #         actions.append(
# #             {
# #                 "label": owner,
# #                 "title": category,
# #                 "body": f"{hypothesis} Next: {row.get('recommended_action', 'Review this signal with the category owner.')}",
# #             }
# #         )
# #     apify_result = results.get("apify")
# #     if apify_result and apify_result.get("status") in {"failed", "skipped"}:
# #         actions.append(
# #             {
# #                 "label": "Apify",
# #                 "title": "Search demand not collected",
# #                 "body": apify_result.get("error") or "Apify did not return a usable trends signal. Keep MVP on public sources or run one guarded live query.",
# #             }
# #         )
# #     return actions[:4]


# # def render_action_card(label: str, title: str, body: str) -> None:
# #     html = (
# #         '<div class="action-card">'
# #         f'<div class="action-label">{escape(label)}</div>'
# #         f'<div class="action-title">{escape(title)}</div>'
# #         f'<div class="action-body">{escape(body)}</div>'
# #         "</div>"
# #     )
# #     st.markdown(html, unsafe_allow_html=True)


# # def render_source_tile(name: str, status: str, detail: str, purpose: str = "") -> None:
# #     status_class = "pill-high" if status == "Active" else "pill-medium" if status == "Optional" else "pill-low"
# #     tile_state = "active" if status == "Active" else "off"
# #     purpose_html = f"<div class='source-purpose'>{escape(purpose)}</div>" if purpose else ""
# #     html = (
# #         f'<div class="source-tile {tile_state}">'
# #         f'<div class="source-name">{escape(name)} <span class="pill {status_class}" style="margin-left:6px;margin-top:0;">{escape(status)}</span></div>'
# #         f'<div class="source-meta">{escape(detail)}</div>'
# #         f"{purpose_html}"
# #         "</div>"
# #     )
# #     st.markdown(html, unsafe_allow_html=True)


# # def render_glossary() -> None:
# #     terms = [
# #         ("Signal", "An external event or measurement that may explain demand, price pressure, or operational risk."),
# #         ("Risk Score", "A 0-10 directional score. Higher means the signal deserves more attention, not that demand is guaranteed to move."),
# #         ("Forecast Feature", "A structured column that can later be joined to internal sales, store, category, promotion, and inventory data."),
# #         ("Region Scope", "Whether the signal is national, state-level, trend geography, or selected market context."),
# #         ("Fallback Brief", "A deterministic local summary generated when NVIDIA is unavailable or no API key is supplied."),
# #         ("Mock Data", "Synthetic or placeholder data. This app marks mock usage explicitly; live public collectors should show No in the audit table."),
# #     ]
# #     cards = []
# #     for term, definition in terms:
# #         cards.append(
# #             "<div class='glossary-card'>"
# #             f"<div class='glossary-term'>{escape(term)}</div>"
# #             f"<div class='glossary-def'>{escape(definition)}</div>"
# #             "</div>"
# #         )
# #     st.markdown("<div class='glossary-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


# # def render_workflow_strip() -> None:
# #     steps = [
# #         ("01", "Collect APIs"),
# #         ("02", "Clean records"),
# #         ("03", "Score signals"),
# #         ("04", "Generate brief"),
# #         ("05", "Export features"),
# #     ]
# #     html = "<div class='workflow'>" + "".join(
# #         f"<div class='workflow-step'><span>{num}</span>{escape(label)}</div>" for num, label in steps
# #     ) + "</div>"
# #     st.markdown(html, unsafe_allow_html=True)


# # def render_run_monitor(slot: Any, title: str, states: Dict[str, Dict[str, str]], progress_pct: int) -> None:
# #     cards = []
# #     for name, info in states.items():
# #         status = info.get("status", "queued")
# #         detail = info.get("detail", "")
# #         status_class = {
# #             "running": "status-running",
# #             "success": "status-success",
# #             "failed": "status-failed",
# #             "skipped": "status-skipped",
# #             "queued": "status-queued",
# #         }.get(status, "status-queued")
# #         cards.append(
# #             "<div class='run-status-card'>"
# #             f"<div class='status-badge {status_class}'>{escape(status)}</div>"
# #             f"<div class='run-status-name'>{escape(name)}</div>"
# #             f"<div class='run-status-detail'>{escape(detail)}</div>"
# #             "</div>"
# #         )
# #     html = (
# #         "<div class='run-monitor'>"
# #         "<div class='run-monitor-head'>"
# #         f"<div><div class='run-monitor-sub'>Pipeline Status</div><div class='run-monitor-title'>{escape(title)}</div></div>"
# #         f"<div class='tbadge'>{int(progress_pct)}%</div>"
# #         "</div>"
# #         "<div class='run-progress-track'>"
# #         f"<div class='run-progress-fill' style='width:{max(0, min(100, int(progress_pct)))}%;'></div>"
# #         "</div>"
# #         "<div class='run-status-grid'>"
# #         + "".join(cards)
# #         + "</div></div>"
# #     )
# #     slot.markdown(html, unsafe_allow_html=True)


# # def render_sidebar_status(slot: Any, message: str, state: str = "info") -> None:
# #     if state == "success":
# #         slot.success(message)
# #     elif state == "warning":
# #         slot.warning(message)
# #     elif state == "error":
# #         slot.error(message)
# #     else:
# #         slot.info(message)


# # def render_score_chart(feature_df: pd.DataFrame) -> None:
# #     if feature_df.empty:
# #         st.info("No feature rows yet.")
# #         return
# #     fig = go.Figure(
# #         go.Bar(
# #             x=feature_df["risk_score"],
# #             y=feature_df["signal_area"],
# #             orientation="h",
# #             marker_color=["#22C55E" if x < 5 else "#F59E0B" if x < 8 else "#EF4444" for x in feature_df["risk_score"]],
# #             text=feature_df["risk_score"],
# #             textposition="auto",
# #         )
# #     )
# #     fig.update_layout(
# #         height=280,
# #         margin={"l": 10, "r": 20, "t": 10, "b": 10},
# #         xaxis={"range": [0, 10], "title": "Score"},
# #         yaxis={"title": ""},
# #         plot_bgcolor="#FFFFFF",
# #         paper_bgcolor="#FFFFFF",
# #     )
# #     st.plotly_chart(fig, width="stretch")


# # def scoring_formula_for_row(row: pd.Series) -> str:
# #     source = str(row.get("source", "")).lower()
# #     area = str(row.get("signal_area", "")).lower()
# #     if "bls" in source or "cpi" in area:
# #         return "CPI scoring starts from a neutral 4.0, adjusts upward or downward using monthly CPI change, adds pressure when YoY inflation is elevated, then clips to a 1-10 range."
# #     if "fda" in source or "recall" in area:
# #         return "Recall scoring starts from reason severity, then adjusts for FDA classification and recall status. Class I and ongoing recalls increase the score; terminated recalls reduce it."
# #     if "noaa" in source or "weather" in area:
# #         return "Weather scoring sums active NOAA alert severity weights for the selected state, adds urgency/certainty pressure, then caps the supply-chain risk score at 10."
# #     if "gnews" in source or "news" in area:
# #         return "News scoring classifies each article into event type and sentiment, adjusts for recency/source quality, then averages deduplicated article risk."
# #     if "apify" in source or "search" in area:
# #         return f"Search scoring uses top regional Google Trends interest divided by 10, with backend limits of {APIFY_SAFE_TIME_RANGE} and {APIFY_HARD_KEYWORD_LIMIT} keyword(s)."
# #     return "Score is normalized to a 1-10 signal intensity scale using the collector-specific scoring rule."


# # def render_score_explainability(feature_df: pd.DataFrame) -> None:
# #     if feature_df.empty:
# #         st.info("No signal explanations available.")
# #         return
# #     explanation_rows = feature_df.sort_values("risk_score", ascending=False).reset_index(drop=True)
# #     for _, row in explanation_rows.iterrows():
# #         score = float(row.get("risk_score", 0) or 0)
# #         band = risk_band(score)
# #         title = f"{row.get('signal_area', 'Signal')} · {str(row.get('signal_name', '')).replace('_', ' ').title()}"
# #         meta = f"{row.get('source', 'Unknown source')} / {row.get('region_scope', row.get('region', ''))}"
# #         reason = str(row.get("score_reason") or "No score reason was returned by this collector.")
# #         evidence = str(row.get("raw_reference") or row.get("signal_value") or "No raw reference available.")
# #         action = str(row.get("recommended_action") or "Review this signal before using it in planning.")
# #         formula = scoring_formula_for_row(row)
# #         html = (
# #             "<div class='score-explain-card'>"
# #             "<div class='score-explain-head'>"
# #             f"<div><div class='score-explain-title'>{escape(title)}</div><div class='score-explain-meta'>{escape(meta)}</div></div>"
# #             f"<div class='score-number'>{score:.2f}<span>{escape(band)}</span></div>"
# #             "</div>"
# #             "<div class='score-explain-label'>Scoring rule</div>"
# #             f"<div class='score-explain-text'>{escape(formula)}</div>"
# #             "<div class='score-explain-label'>Why this score</div>"
# #             f"<div class='score-explain-text'>{escape(reason)}</div>"
# #             "<div class='score-explain-label'>Evidence used</div>"
# #             f"<div class='score-explain-text'>{escape(evidence)}</div>"
# #             "<div class='score-explain-label'>Planning action</div>"
# #             f"<div class='score-explain-text'>{escape(action)}</div>"
# #             "</div>"
# #         )
# #         st.markdown(html, unsafe_allow_html=True)


# # BRIEF_SECTION_TITLES = {
# #     "executive summary",
# #     "top 3 insights",
# #     "top three insights",
# #     "top signal evidence",
# #     "forecasting relevance",
# #     "recommended actions",
# #     "confidence and limitations",
# #     "confidence limitations",
# # }


# # def normalize_brief_line(line: str) -> str:
# #     normalized = str(line or "").strip()
# #     normalized = re.sub(r"^\s*#{1,6}\s*", "", normalized)
# #     normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
# #     normalized = normalized.replace("**", "")
# #     return normalized.strip()


# # def clean_brief_heading(line: str) -> str:
# #     heading = normalize_brief_line(line).rstrip(":").strip()
# #     heading = re.sub(r"^\d+[\.)]\s*", "", heading).strip()
# #     return heading


# # def brief_section_heading(raw_line: str) -> str:
# #     stripped = str(raw_line or "").strip()
# #     if re.fullmatch(r"[-*_]{3,}", stripped):
# #         return ""
# #     normalized = normalize_brief_line(stripped)
# #     title = clean_brief_heading(normalized)
# #     simplified = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
# #     if stripped.startswith("#") or (normalized.endswith(":") and len(normalized) <= 90):
# #         return title
# #     if simplified in BRIEF_SECTION_TITLES:
# #         return title
# #     return ""


# # def brief_to_html(brief: str) -> str:
# #     parts: List[str] = []
# #     in_list = False
# #     for raw_line in str(brief or "").splitlines():
# #         line = raw_line.strip()
# #         if not line or re.fullmatch(r"[-*_]{3,}", line):
# #             if in_list:
# #                 parts.append("</ul>")
# #                 in_list = False
# #             continue
# #         normalized = normalize_brief_line(line)
# #         heading = brief_section_heading(line)
# #         bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
# #         if heading:
# #             if in_list:
# #                 parts.append("</ul>")
# #                 in_list = False
# #             parts.append(f"<div class='brief-section-title'>{escape(heading)}</div>")
# #         elif bullet_match:
# #             if not in_list:
# #                 parts.append("<ul class='brief-list'>")
# #                 in_list = True
# #             parts.append(f"<li>{escape(bullet_match.group(1))}</li>")
# #         else:
# #             if in_list:
# #                 parts.append("</ul>")
# #                 in_list = False
# #             parts.append(f"<p>{escape(normalized)}</p>")
# #     if in_list:
# #         parts.append("</ul>")
# #     return "".join(parts)


# # def parse_brief_sections(brief: str) -> List[Dict[str, Any]]:
# #     sections: List[Dict[str, Any]] = []
# #     current = {"title": "Executive Summary", "items": []}
# #     for raw_line in str(brief or "").splitlines():
# #         line = raw_line.strip()
# #         if not line or re.fullmatch(r"[-*_]{3,}", line):
# #             continue
# #         normalized = normalize_brief_line(line)
# #         heading = brief_section_heading(line)
# #         bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+|[A-Z]\.\s+)(.*)$", normalized)
# #         if heading:
# #             if current["items"]:
# #                 sections.append(current)
# #             current = {"title": heading, "items": []}
# #         elif bullet_match:
# #             current["items"].append({"kind": "bullet", "text": bullet_match.group(1)})
# #         else:
# #             current["items"].append({"kind": "text", "text": normalized})
# #     if current["items"]:
# #         sections.append(current)
# #     return sections


# # def brief_sections_to_html(brief: str) -> str:
# #     sections = parse_brief_sections(brief)
# #     if not sections:
# #         return "<div class='brief-section-grid'><div class='brief-section-card primary'><div class='brief-section-title'>Executive Summary</div><p>No brief content was generated.</p></div></div>"
# #     cards = []
# #     for idx, section in enumerate(sections):
# #         paragraphs = []
# #         bullets = []
# #         for item in section["items"]:
# #             if item["kind"] == "bullet":
# #                 bullets.append(f"<li>{escape(str(item['text']))}</li>")
# #             else:
# #                 paragraphs.append(f"<p>{escape(str(item['text']))}</p>")
# #         body = "".join(paragraphs)
# #         if bullets:
# #             body += "<ul class='brief-list'>" + "".join(bullets) + "</ul>"
# #         primary = " primary" if idx == 0 else ""
# #         cards.append(
# #             f"<div class='brief-section-card{primary}'>"
# #             f"<div class='brief-section-title'>{escape(str(section['title']))}</div>"
# #             f"{body}"
# #             "</div>"
# #         )
# #     return "<div class='brief-section-grid'>" + "".join(cards) + "</div>"


# # def render_executive_brief(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
# #     brief_source = str(run.get("brief_source", "unknown"))
# #     articles = run.get("articles", [])
# #     llm_audit = run.get("llm_audit", {})
# #     source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
# #     top_label = "No signal"
# #     avg_score_label = "0.00"
# #     if not feature_df.empty:
# #         top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
# #         top_label = f"{top.get('signal_area', 'Signal')} {float(top.get('risk_score', 0) or 0):.2f}/10"
# #         avg_score_label = f"{float(feature_df['risk_score'].mean()):.2f}"
# #     articles_sent = llm_audit.get("articles_sent", min(len(articles), 8))
# #     attempts = llm_audit.get("nvidia_attempts", [])
# #     response_label = "not called"
# #     if attempts:
# #         response_label = f"{len(attempts)} attempt(s), {attempts[-1].get('elapsed_ms', 0)} ms last"
# #     header_note = (
# #         "NVIDIA grounded response" if llm_audit.get("sent_to_llm") else "Local deterministic summary using collected feature rows"
# #     )
# #     brief_source_label = "NVIDIA" if brief_source == "nvidia" else "Local fallback"
# #     brief_status_note = llm_audit.get("fallback_reason") or "NVIDIA returned a grounded response."
# #     html = (
# #         "<div class='brief-shell'>"
# #         "<div class='brief-header'>"
# #         "<div class='brief-kicker'>Executive Brief</div>"
# #         f"<div class='brief-title'>{escape(str(run.get('run_config', {}).get('retailer', retailer_label)))} External Signal Readout</div>"
# #         f"<div class='brief-summary-text'>{escape(header_note)}. {escape(str(brief_status_note))}</div>"
# #         "<div class='brief-meta-strip'>"
# #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Brief Source</div><div class='brief-meta-value'>{escape(brief_source_label)}</div></div>"
# #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Rows Grounded</div><div class='brief-meta-value'>{len(feature_df)} rows / {source_count} source(s)</div></div>"
# #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Average Score</div><div class='brief-meta-value'>{escape(avg_score_label)} / 10</div></div>"
# #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>AI Timing</div><div class='brief-meta-value'>{escape(response_label)}</div></div>"
# #         "</div>"
# #         f"<div class='brief-summary-text' style='margin-top:10px;'>Top evidence: {escape(top_label)}. Articles in context: {len(articles)} available, {articles_sent} sent.</div>"
# #         "</div>"
# #         f"{brief_sections_to_html(str(run.get('brief', '')))}"
# #         "</div>"
# #     )
# #     st.markdown(html, unsafe_allow_html=True)


# # def parse_lines(text: str) -> List[str]:
# #     return [line.strip() for line in text.splitlines() if line.strip()]


# # def build_default_news_keywords(retailer_name: str) -> str:
# #     name = retailer_name.strip() or "Retailer"
# #     return "\n".join(
# #         [
# #             f"{name} inflation",
# #             f"{name} prices",
# #             f"{name} store closures",
# #             f"{name} recall",
# #             "discount retail tariffs",
# #             "Dollar General promotion",
# #         ]
# #     )


# # def build_default_trends_keywords(retailer_name: str) -> str:
# #     name = retailer_name.strip() or "Retailer"
# #     return "\n".join(
# #         [
# #             f"{name} sales",
# #             f"{name} coupons",
# #             f"{name} near me",
# #             f"{name} groceries",
# #             "cheap groceries",
# #         ]
# #     )


# # def retailer_initials(retailer_name: str) -> str:
# #     words = [word for word in re.split(r"\s+", retailer_name.strip()) if word]
# #     if not words:
# #         return "AI"
# #     return "".join(word[0].upper() for word in words[:2])


# # st.session_state.setdefault("gnews_period", "7d")
# # st.session_state.setdefault("max_news", 24)
# # st.session_state.setdefault("fda_query", "product_description:(snacks OR candy OR beverages)")
# # st.session_state.setdefault("fda_limit", 8)
# # st.session_state.setdefault("weather_area", "TX")
# # st.session_state.setdefault("weather_limit", 5)
# # st.session_state.setdefault("apify_geo", "US")
# # st.session_state.setdefault("apify_time_range", APIFY_SAFE_TIME_RANGE)
# # st.session_state.setdefault("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)
# # st.session_state.setdefault("apify_run_mode", "Skip Apify")
# # st.session_state.setdefault("apify_live_confirm", False)
# # st.session_state.setdefault("workbench_view", "Configure")
# # if st.session_state.get("apify_time_range") not in APIFY_ALLOWED_TIME_RANGES:
# #     st.session_state["apify_time_range"] = APIFY_SAFE_TIME_RANGE
# # if st.session_state.pop("force_results_view", False):
# #     st.session_state["workbench_view"] = "Results"
# # if st.session_state.pop("reset_apify_live_confirm", False):
# #     st.session_state["apify_run_mode"] = "Skip Apify"
# #     st.session_state["apify_live_confirm"] = False


# # with st.sidebar:
# #     st.markdown(
# #         "<div class='sidebar-brand'>"
# #         "<div class='sidebar-brand-title'>Run Control</div>"
# #         "<div class='sidebar-brand-copy'>Configure context, source coverage, and guarded API spend for the next intelligence run.</div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )

# #     with st.expander("Credentials", expanded=False):
# #         st.caption("Keys stay in this Streamlit session and are never written to evidence payloads.")
# #         nvidia_key = st.text_input("NVIDIA API key", value=os.getenv("NVIDIA_API_KEY", ""), type="password")
# #         nvidia_model = st.text_input("NVIDIA model", value=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL))
# #         apify_token = st.text_input("Apify token", value=os.getenv("APIFY_API_TOKEN", ""), type="password")
# #         bls_key = st.text_input("BLS API key optional", value=os.getenv("BLS_API_KEY", ""), type="password")
# #         validate_button = st.button("Validate credentials", width="stretch")

# #     with st.expander("Retail context", expanded=True):
# #         retailer = st.text_input("Company / Retailer", value="", placeholder="Optional: enter a company or retailer")
# #         region = st.text_input("Region", value="US")
# #         country = st.selectbox("News country", ["US", "CA", "GB", "AE", "IN"], index=0)
# #         language = st.selectbox("News language", ["en", "es", "fr", "ar", "hi"], index=0)

# #     with st.expander("Signal sources", expanded=True):
# #         use_gnews = st.checkbox("Retail news", value=True)
# #         use_bls = st.checkbox("Inflation CPI", value=True)
# #         use_fda = st.checkbox("Product recalls", value=True)
# #         use_weather = st.checkbox("Weather risk", value=True)
# #         use_apify = st.checkbox("Search demand", value=False)

# #     sidebar_source_count = sum([use_gnews, use_bls, use_fda, use_weather, bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))])
# #     st.markdown(
# #         "<div class='sidebar-summary'>"
# #         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Sources</div><div class='sidebar-summary-value'>{sidebar_source_count} enabled</div></div>"
# #         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Brief</div><div class='sidebar-summary-value'>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</div></div>"
# #         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Market</div><div class='sidebar-summary-value'>{escape(region)}</div></div>"
# #         f"<div class='sidebar-summary-card'><div class='sidebar-summary-label'>Apify</div><div class='sidebar-summary-value'>{escape(st.session_state.get('apify_run_mode', 'Skip Apify'))}</div></div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )

# #     if use_apify or apify_token.strip():
# #         with st.expander("Apify spend guardrail", expanded=bool(use_apify)):
# #             if apify_token.strip():
# #                 if st.session_state.get("apify_live_confirm", False) and st.session_state.get("apify_run_mode") == "Skip Apify":
# #                     st.session_state["apify_run_mode"] = "Run one live Apify call"
# #                 st.radio(
# #                     "Live mode",
# #                     ["Skip Apify", "Run one live Apify call"],
# #                     key="apify_run_mode",
# #                     horizontal=False,
# #                 )
# #                 st.session_state["apify_live_confirm"] = st.session_state.get("apify_run_mode") == "Run one live Apify call"
# #                 st.caption(f"{APIFY_SAFE_TIME_RANGE}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s). Resets after run.")
# #                 if st.session_state.get("apify_run_mode") == "Run one live Apify call":
# #                     st.success("Next run will call Apify once.")
# #                 else:
# #                     st.info("No Apify credits will be used.")
# #             else:
# #                 st.session_state["apify_run_mode"] = "Skip Apify"
# #                 st.session_state["apify_live_confirm"] = False
# #                 st.warning("Add an Apify token before allowing a live trends run.")

# #     st.markdown("<div class='sidebar-divider'></div>", unsafe_allow_html=True)
# #     run_button = st.button("Run intelligence", type="primary", width="stretch")
# #     sidebar_status_slot = st.empty()


# # retailer_label = retailer.strip() or "Retailer"
# # previous_keyword_retailer = st.session_state.get("keyword_template_retailer")
# # previous_news_template = build_default_news_keywords(previous_keyword_retailer or retailer_label)
# # previous_trends_template = build_default_trends_keywords(previous_keyword_retailer or retailer_label)
# # next_news_template = build_default_news_keywords(retailer_label)
# # next_trends_template = build_default_trends_keywords(retailer_label)
# # if "news_keywords_text" not in st.session_state or st.session_state.get("news_keywords_text") == previous_news_template:
# #     st.session_state["news_keywords_text"] = next_news_template
# # if "trends_keywords_text" not in st.session_state or st.session_state.get("trends_keywords_text") == previous_trends_template:
# #     st.session_state["trends_keywords_text"] = next_trends_template
# # st.session_state["keyword_template_retailer"] = retailer_label
# # apify_source_active = bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))

# # st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
# # st.markdown(
# #     "<div class='topbar'>"
# #     f"<div class='tt'>Market Intelligence <span>/ {escape(retailer_label)} · External Signals</span></div>"
# #     "<div class='tbadge'>AI Workbench</div>"
# #     "<div style='margin-left:auto;display:flex;align-items:center;gap:8px;'>"
# #     "<span class='ldot'></span>"
# #     "<span style='font-size:10px;color:var(--t3);font-weight:800;'>Live API Mode</span>"
# #     f"<div style='width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,var(--odk),var(--or));display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#fff;'>{escape(retailer_initials(retailer_label))}</div>"
# #     "</div></div>",
# #     unsafe_allow_html=True,
# # )

# # hero_left, hero_right = st.columns([2.2, 0.9], vertical_alignment="center")
# # with hero_left:
# #     st.markdown(
# #         "<div class='hero-shell'>"
# #         "<div class='hero-kicker'><span class='ldot'></span> External Signal Layer</div>"
# #         f"<h1 class='hero-title'>{escape(retailer_label)} Market Intelligence Command Center</h1>"
# #         "<p class='hero-copy'>A retail-grade workbench that turns news, CPI, recalls, weather alerts, search demand, and API health into forecast-ready features, composite risk scores, and buyer actions.</p>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )
# # with hero_right:
# #     active_sources = sum([use_gnews, use_bls, use_fda, use_weather, apify_source_active])
# #     st.markdown(
# #         "<div class='hero-side'>"
# #         "<div class='hero-side-label'>Run Profile</div>"
# #         f"<div class='hero-side-row'><span>Retailer</span><strong>{escape(retailer)}</strong></div>"
# #         f"<div class='hero-side-row'><span>Region</span><strong>{escape(region)}</strong></div>"
# #         f"<div class='hero-side-row'><span>Sources</span><strong>{active_sources} enabled</strong></div>"
# #         f"<div class='hero-side-row'><span>LLM</span><strong>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</strong></div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )

# # view = st.segmented_control(
# #     "Workbench view",
# #     ["Configure", "Results", "Evidence Audit", "Raw Data"],
# #     required=True,
# #     label_visibility="collapsed",
# #     key="workbench_view",
# #     width="content",
# # )

# # run_status_slot = st.empty()
# # if not run_button and st.session_state.get("run"):
# #     last_run = st.session_state["run"]
# #     last_states = {}
# #     for key, result in last_run.get("results", {}).items():
# #         status = result.get("status", "unknown")
# #         last_states[key.upper()] = {
# #             "status": status if status in {"success", "failed", "skipped"} else "queued",
# #             "detail": result.get("error") or f"{len(result.get('rows', []))} feature row(s)",
# #         }
# #     last_llm_audit = last_run.get("llm_audit", {})
# #     if last_llm_audit:
# #         last_states["BRIEF"] = {
# #             "status": "success",
# #             "detail": "NVIDIA generated the brief" if last_run.get("brief_source") == "nvidia" else f"Fallback brief generated: {last_llm_audit.get('fallback_reason', 'NVIDIA unavailable')}",
# #         }
# #     if last_states:
# #         render_run_monitor(run_status_slot, f"Last run completed at {last_run.get('timestamp', '')}", last_states, 100)
# #     render_sidebar_status(sidebar_status_slot, f"Last run complete: {last_run.get('timestamp', '')}", "success")
# # elif not run_button:
# #     render_sidebar_status(sidebar_status_slot, "Status: idle. Apify runs only when token is present and live mode is set to Run one live Apify call.", "info")

# # if view == "Configure":
# #     source_specs = [
# #         {
# #             "name": "GNews/RSS",
# #             "status": "Active" if use_gnews else "Off",
# #             "signal": "Retail news and competitor events",
# #             "feature": "retail_news_event_score",
# #             "owner": "Category Manager",
# #             "output": "Market Event Risk",
# #         },
# #         {
# #             "name": "BLS CPI",
# #             "status": "Active" if use_bls else "Off",
# #             "signal": "Headline and category inflation",
# #             "feature": "cpi_pressure_features",
# #             "owner": "Demand Planning",
# #             "output": "Value Basket Pressure",
# #         },
# #         {
# #             "name": "openFDA",
# #             "status": "Active" if use_fda else "Off",
# #             "signal": "Food recall enforcement records",
# #             "feature": "recall_exposure_score",
# #             "owner": "Compliance + Buyer",
# #             "output": "Safety And Compliance Risk",
# #         },
# #         {
# #             "name": "NOAA Weather",
# #             "status": "Active" if use_weather else "Off",
# #             "signal": "State weather alerts",
# #             "feature": "state_weather_disruption_score",
# #             "owner": "Supply Chain",
# #             "output": "Route And Store Risk",
# #         },
# #         {
# #             "name": "Apify Trends",
# #             "status": "Active" if apify_source_active else "Off",
# #             "signal": "Search interest by region",
# #             "feature": "google_trends_interest_score",
# #             "owner": "Demand Planning",
# #             "output": "Demand Interest Spike",
# #         },
# #     ]
# #     readiness_score = min(
# #         100,
# #         35
# #         + (active_sources * 10)
# #         + (10 if retailer_label and region else 0)
# #         + (5 if st.session_state.get("apify_run_mode", "Skip Apify") == "Skip Apify" else 10),
# #     )
# #     readiness = {
# #         "Retail context": f"{retailer_label} / {region}",
# #         "Public sources": f"{active_sources} enabled",
# #         "Brief mode": "NVIDIA" if nvidia_key.strip() else "Fallback",
# #         "Apify guardrail": st.session_state.get("apify_run_mode", "Skip Apify"),
# #     }
# #     readiness_rows = "".join(
# #         f"<div class='readiness-row'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
# #         for label, value in readiness.items()
# #     )
# #     control_cards = [
# #         ("Evidence trace", "ok", "Raw payloads and normalized rows remain inspectable in Evidence Audit."),
# #         ("Apify spend", "ok" if st.session_state.get("apify_run_mode", "Skip Apify") == "Skip Apify" else "warn", "Live trend calls require explicit guarded mode and reset after the run."),
# #         ("Internal data", "warn", "POS, inventory, product hierarchy, and store/DC data are not connected yet."),
# #     ]
# #     control_html = "".join(
# #         f"<div class='control-check {state}'><div class='control-check-title'>{escape(title)}</div><div class='control-check-copy'>{escape(copy)}</div></div>"
# #         for title, state, copy in control_cards
# #     )
# #     st.markdown(
# #         "<div class='config-shell'>"
# #         "<div class='config-panel emphasis'>"
# #         "<div class='config-eyebrow'>Run Blueprint</div>"
# #         f"<div class='config-title'>{escape(retailer_label)} market intelligence run</div>"
# #         "<div class='config-copy'>A governed setup surface for turning public market signals into category, owner, forecast-feature, and action-ready outputs.</div>"
# #         f"<div class='readiness-score'><div><div class='readiness-score-value'>{readiness_score}</div><div class='readiness-score-label'>Readiness Score</div></div></div>"
# #         f"<div class='readiness-list'>{readiness_rows}</div>"
# #         "</div>"
# #         "<div class='config-panel'>"
# #         "<div class='config-eyebrow'>Controls</div>"
# #         "<div class='config-title'>Governed by evidence, cost guardrails, and scope limits</div>"
# #         "<div class='config-copy'>The run can support buyer and planner discussion, but it does not claim internal category performance until internal POS, inventory, product hierarchy, vendor, promotion, and store/DC data are connected.</div>"
# #         f"<div class='control-grid'>{control_html}</div>"
# #         "</div>"
# #         "</div>",
# #         unsafe_allow_html=True,
# #     )

# #     st.markdown('<div class="small-header">Source To Feature Routing</div>', unsafe_allow_html=True)
# #     routing_cards = []
# #     for spec in source_specs:
# #         status = spec["status"]
# #         status_class = "pill-high" if status == "Active" else "pill-low"
# #         tile_state = "active" if status == "Active" else "off"
# #         routing_cards.append(
# #             f"<div class='routing-card {tile_state}'>"
# #             f"<div class='routing-source'>{escape(spec['name'])} <span class='pill {status_class}' style='margin-left:6px;margin-top:0;'>{escape(status)}</span></div>"
# #             f"<div class='routing-line'><div class='routing-label'>Signal</div><div class='routing-value'>{escape(spec['signal'])}</div></div>"
# #             f"<div class='routing-line'><div class='routing-label'>Forecast Feature</div><div class='routing-value'>{escape(spec['feature'])}</div></div>"
# #             f"<div class='routing-line'><div class='routing-label'>Owner / KPI</div><div class='routing-value'>{escape(spec['owner'])} / {escape(spec['output'])}</div></div>"
# #             "</div>"
# #         )
# #     st.markdown("<div class='routing-grid'>" + "".join(routing_cards) + "</div>", unsafe_allow_html=True)

# #     setup_tab, collector_tab, governance_tab = st.tabs(["Scope", "Collector Tuning", "Governance"])
# #     with setup_tab:
# #         st.markdown(
# #             "<div class='config-tab-note'>Scope terms decide what the external collectors look for. Keep them readable for business review: retailer, competitor, category, recall, pricing, weather, and seasonal language.</div>",
# #             unsafe_allow_html=True,
# #         )
# #         k_left, k_right = st.columns(2)
# #         with k_left:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("Retail News Keywords")
# #             st.text_area("GNews keywords", key="news_keywords_text", height=210, label_visibility="collapsed")
# #             st.markdown("</div>", unsafe_allow_html=True)
# #         with k_right:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("Search Demand Keywords")
# #             st.text_area("Apify Google Trends keywords", key="trends_keywords_text", height=210, label_visibility="collapsed")
# #             st.markdown("</div>", unsafe_allow_html=True)

# #     with collector_tab:
# #         st.markdown(
# #             "<div class='config-tab-note'>Tuning controls runtime, cost, and evidence volume. Defaults stay conservative so the MVP is explainable and does not over-consume Apify credits.</div>",
# #             unsafe_allow_html=True,
# #         )
# #         c1, c2, c3 = st.columns(3)
# #         with c1:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("News")
# #             st.selectbox("GNews period", ["1d", "7d", "30d", "3m"], key="gnews_period")
# #             st.slider("Max news results", min_value=5, max_value=60, step=1, key="max_news")
# #             st.markdown("</div>", unsafe_allow_html=True)
# #         with c2:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("Recall + Weather")
# #             st.text_input("FDA recall search", key="fda_query")
# #             st.slider("FDA recall limit", min_value=1, max_value=25, key="fda_limit")
# #             st.text_input("NOAA weather area", key="weather_area", help="Two-letter US state code, such as TX, NY, CA.")
# #             st.slider("NOAA alert limit", min_value=1, max_value=25, key="weather_limit")
# #             st.markdown("</div>", unsafe_allow_html=True)
# #         with c3:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("Search Demand")
# #             st.text_input("Apify geo", key="apify_geo")
# #             st.selectbox(
# #                 "Apify time range",
# #                 APIFY_ALLOWED_TIME_RANGES,
# #                 key="apify_time_range",
# #                 help="The default now 7-d window is the safest weekly actor value. Longer ranges can use more Apify credits.",
# #             )
# #             st.slider("Apify max keywords", min_value=1, max_value=APIFY_HARD_KEYWORD_LIMIT, key="apify_max_keywords")
# #             st.caption("Live mode is controlled in the sidebar guardrail.")
# #             st.markdown("</div>", unsafe_allow_html=True)

# #     with governance_tab:
# #         st.markdown(
# #             "<div class='config-tab-note'>Governance makes the demo credible: source status is separate from brief status, fallback is labeled, and raw collector payloads remain inspectable in Evidence Audit and Raw Data.</div>",
# #             unsafe_allow_html=True,
# #         )
# #         g1, g2 = st.columns([1, 1])
# #         with g1:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("Agent Flow")
# #             render_workflow_strip()
# #             st.markdown("</div>", unsafe_allow_html=True)
# #         with g2:
# #             st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# #             st.subheader("Glossary")
# #             render_glossary()
# #             st.markdown("</div>", unsafe_allow_html=True)


# # if validate_button:
# #     with st.spinner("Validating credentials..."):
# #         n_ok, n_msg = validate_nvidia(nvidia_key.strip(), nvidia_model.strip())
# #         a_ok, a_msg = validate_apify(apify_token.strip())
# #     st.session_state["validation"] = {"nvidia": (n_ok, n_msg), "apify": (a_ok, a_msg)}

# # if "validation" in st.session_state:
# #     n_ok, n_msg = st.session_state["validation"]["nvidia"]
# #     a_ok, a_msg = st.session_state["validation"]["apify"]
# #     st.info(f"NVIDIA: {'Connected' if n_ok else 'Not connected'} - {n_msg}")
# #     st.info(f"Apify: {'Connected' if a_ok else 'Not connected'} - {a_msg}")


# # if run_button:
# #     news_keywords = parse_lines(st.session_state.get("news_keywords_text", build_default_news_keywords(retailer_label)))
# #     trends_keywords = parse_lines(st.session_state.get("trends_keywords_text", build_default_trends_keywords(retailer_label)))
# #     apify_token_value = apify_token.strip()
# #     apify_run_mode = st.session_state.get("apify_run_mode", "Skip Apify")
# #     apify_time_range = st.session_state.get("apify_time_range", APIFY_SAFE_TIME_RANGE)
# #     if apify_time_range not in APIFY_ALLOWED_TIME_RANGES or not apify_time_range:
# #         apify_time_range = APIFY_SAFE_TIME_RANGE
# #     apify_live_confirmed = bool(apify_token_value and apify_run_mode == "Run one live Apify call")
# #     apify_requested = bool(use_apify or apify_live_confirmed)
# #     run_config = {
# #         "retailer": retailer.strip() or "Retailer",
# #         "region": region,
# #         "country": country,
# #         "language": language,
# #         "enabled_sources": {"gnews": use_gnews, "bls": use_bls, "fda": use_fda, "weather": use_weather, "apify": apify_requested},
# #         "use_gnews": use_gnews,
# #         "use_bls": use_bls,
# #         "use_fda": use_fda,
# #         "use_weather": use_weather,
# #         "use_apify": apify_requested,
# #         "news_keywords": news_keywords,
# #         "trends_keywords": trends_keywords[:APIFY_HARD_KEYWORD_LIMIT],
# #         "gnews_period": st.session_state.get("gnews_period", "7d"),
# #         "max_news": int(st.session_state.get("max_news", 24)),
# #         "fda_query": st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
# #         "fda_limit": int(st.session_state.get("fda_limit", 8)),
# #         "weather_area": normalize_weather_area(st.session_state.get("weather_area", "TX")),
# #         "weather_limit": int(st.session_state.get("weather_limit", 5)),
# #         "bls_series": BLS_CPI_SERIES,
# #         "apify_token_present": bool(apify_token_value),
# #         "apify_run_mode": apify_run_mode,
# #         "apify_geo": st.session_state.get("apify_geo", "US"),
# #         "apify_time_range": apify_time_range,
# #         "apify_max_keywords": int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
# #         "apify_live_confirm": apify_live_confirmed,
# #     }
# #     results: Dict[str, Dict[str, Any]] = {}
# #     all_rows: List[Dict[str, Any]] = []
# #     all_articles: List[Dict[str, Any]] = []

# #     steps = [
# #         ("gnews", use_gnews),
# #         ("bls", use_bls),
# #         ("fda", use_fda),
# #         ("weather", use_weather),
# #         ("apify", apify_requested),
# #     ]
# #     active_steps = [step for step in steps if step[1]]
# #     total = max(1, len(active_steps))
# #     completed = 0
# #     collector_states: Dict[str, Dict[str, str]] = {
# #         "GNEWS": {"status": "queued" if use_gnews else "skipped", "detail": "Retail news collector" if use_gnews else "Disabled"},
# #         "BLS": {"status": "queued" if use_bls else "skipped", "detail": "CPI collector" if use_bls else "Disabled"},
# #         "FDA": {"status": "queued" if use_fda else "skipped", "detail": "Recall collector" if use_fda else "Disabled"},
# #         "WEATHER": {"status": "queued" if use_weather else "skipped", "detail": "NOAA alert collector" if use_weather else "Disabled"},
# #         "APIFY": {"status": "queued" if apify_requested else "skipped", "detail": f"Trends collector; mode: {apify_run_mode}" if apify_requested else "Disabled"},
# #         "BRIEF": {"status": "queued", "detail": "NVIDIA grounded brief or local fallback"},
# #     }
# #     render_run_monitor(run_status_slot, "Starting collectors", collector_states, 2)
# #     render_sidebar_status(sidebar_status_slot, "Running: starting collectors...", "info")

# #     if use_gnews:
# #         collector_states["GNEWS"] = {"status": "running", "detail": "Collecting and deduplicating retail news"}
# #         render_run_monitor(run_status_slot, "Collecting retail news", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, "Running: collecting retail news...", "info")
# #         results["gnews"] = collect_gnews(
# #             news_keywords,
# #             country,
# #             language,
# #             st.session_state.get("gnews_period", "7d"),
# #             int(st.session_state.get("max_news", 24)),
# #             retailer.strip() or "Retailer",
# #         )
# #         all_rows.extend(results["gnews"].get("rows", []))
# #         all_articles.extend(results["gnews"].get("items", []))
# #         completed += 1
# #         gnews_status = results["gnews"].get("status", "failed")
# #         collector_states["GNEWS"] = {
# #             "status": "success" if gnews_status == "success" else "failed" if gnews_status == "failed" else "skipped",
# #             "detail": results["gnews"].get("error") or f"{len(results['gnews'].get('items', []))} article(s), {len(results['gnews'].get('rows', []))} feature row(s)",
# #         }
# #         render_run_monitor(run_status_slot, "Retail news complete", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"GNews {collector_states['GNEWS']['status']}: {collector_states['GNEWS']['detail']}", "warning" if gnews_status != "success" else "info")

# #     if use_bls:
# #         collector_states["BLS"] = {"status": "running", "detail": "Collecting headline and category CPI"}
# #         render_run_monitor(run_status_slot, "Collecting CPI inflation", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, "Running: collecting BLS CPI...", "info")
# #         results["bls"] = collect_bls_cpi(bls_key.strip(), retailer.strip() or "Retailer")
# #         all_rows.extend(results["bls"].get("rows", []))
# #         completed += 1
# #         bls_status = results["bls"].get("status", "failed")
# #         collector_states["BLS"] = {
# #             "status": "success" if bls_status == "success" else "failed",
# #             "detail": results["bls"].get("error") or f"{len(results['bls'].get('rows', []))} CPI feature row(s)",
# #         }
# #         render_run_monitor(run_status_slot, "CPI collection complete", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"BLS {collector_states['BLS']['status']}: {collector_states['BLS']['detail']}", "warning" if bls_status != "success" else "info")

# #     if use_fda:
# #         collector_states["FDA"] = {"status": "running", "detail": "Collecting food recall records"}
# #         render_run_monitor(run_status_slot, "Collecting FDA recalls", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, "Running: collecting FDA recalls...", "info")
# #         results["fda"] = collect_fda_recalls(
# #             st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
# #             int(st.session_state.get("fda_limit", 8)),
# #             retailer.strip() or "Retailer",
# #         )
# #         all_rows.extend(results["fda"].get("rows", []))
# #         completed += 1
# #         fda_status = results["fda"].get("status", "failed")
# #         collector_states["FDA"] = {
# #             "status": "success" if fda_status == "success" else "failed",
# #             "detail": results["fda"].get("error") or f"{len(results['fda'].get('items', []))} recall item(s), {len(results['fda'].get('rows', []))} feature row(s)",
# #         }
# #         render_run_monitor(run_status_slot, "FDA recall collection complete", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"FDA {collector_states['FDA']['status']}: {collector_states['FDA']['detail']}", "warning" if fda_status != "success" else "info")

# #     if use_weather:
# #         weather_area = normalize_weather_area(st.session_state.get("weather_area", "TX"))
# #         collector_states["WEATHER"] = {"status": "running", "detail": f"Collecting active NOAA alerts for {weather_area}"}
# #         render_run_monitor(run_status_slot, "Collecting weather alerts", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"Running: collecting NOAA weather alerts for {weather_area}...", "info")
# #         results["weather"] = collect_weather_alerts(
# #             weather_area,
# #             int(st.session_state.get("weather_limit", 5)),
# #             retailer.strip() or "Retailer",
# #         )
# #         all_rows.extend(results["weather"].get("rows", []))
# #         completed += 1
# #         weather_status = results["weather"].get("status", "failed")
# #         collector_states["WEATHER"] = {
# #             "status": "success" if weather_status == "success" else "failed",
# #             "detail": results["weather"].get("error") or f"{len(results['weather'].get('items', []))} active alert item(s), {len(results['weather'].get('rows', []))} feature row(s)",
# #         }
# #         render_run_monitor(run_status_slot, "Weather alert collection complete", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"Weather {collector_states['WEATHER']['status']}: {collector_states['WEATHER']['detail']}", "warning" if weather_status != "success" else "info")

# #     if apify_requested and not apify_token_value:
# #         results["apify"] = {
# #             "status": "skipped",
# #             "source": "Apify Trends",
# #             "error": "Apify was enabled but no Apify token was provided. No Apify credits were used.",
# #             "raw": None,
# #             "rows": [],
# #             "items": [],
# #         }
# #         completed += 1
# #         collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
# #         render_run_monitor(run_status_slot, "Apify skipped: token missing", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, "Apify skipped: token missing, no credits used.", "warning")

# #     elif apify_requested and not apify_live_confirmed:
# #         results["apify"] = {
# #             "status": "skipped",
# #             "source": "Apify Trends",
# #             "error": f"Apify was enabled but Apify live mode is '{apify_run_mode}'. Select 'Run one live Apify call' in the sidebar to spend one guarded Apify run. No Apify credits were used.",
# #             "raw": None,
# #             "rows": [],
# #             "items": [],
# #         }
# #         completed += 1
# #         collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
# #         render_run_monitor(run_status_slot, "Apify skipped without spending credits", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, "Apify skipped: no credits used.", "warning")

# #     elif apify_requested and apify_live_confirmed:
# #         collector_states["APIFY"] = {"status": "running", "detail": f"One guarded live run: {apify_time_range}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s)"}
# #         render_run_monitor(run_status_slot, "Collecting Google Trends via Apify", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"Running: Apify Trends live call ({apify_time_range}, max {APIFY_HARD_KEYWORD_LIMIT} keywords)...", "warning")
# #         results["apify"] = collect_apify_trends(
# #             apify_token_value,
# #             trends_keywords,
# #             st.session_state.get("apify_geo", "US"),
# #             apify_time_range,
# #             retailer.strip() or "Retailer",
# #             int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
# #         )
# #         all_rows.extend(results["apify"].get("rows", []))
# #         completed += 1
# #         st.session_state["reset_apify_live_confirm"] = True
# #         apify_status = results["apify"].get("status", "failed")
# #         collector_states["APIFY"] = {
# #             "status": "success" if apify_status == "success" else "failed" if apify_status == "failed" else "skipped",
# #             "detail": results["apify"].get("error") or f"{len(results['apify'].get('items', []))} trends row(s)",
# #         }
# #         render_run_monitor(run_status_slot, "Apify collection complete", collector_states, int((completed / total) * 100))
# #         render_sidebar_status(sidebar_status_slot, f"Apify {collector_states['APIFY']['status']}: {collector_states['APIFY']['detail']}", "warning" if apify_status != "success" else "info")

# #     collector_states["BRIEF"] = {"status": "running", "detail": "Calling NVIDIA when available, with local fallback ready"}
# #     render_run_monitor(run_status_slot, "Generating intelligence brief", collector_states, 98)
# #     render_sidebar_status(sidebar_status_slot, "Running: generating executive brief...", "info")
# #     feature_df = pd.DataFrame(all_rows)
# #     if not feature_df.empty:
# #         feature_df["retailer"] = retailer.strip() or "Retailer"
# #         if region:
# #             feature_df["selected_market"] = region
# #         feature_df = enrich_feature_rows_for_retailer(feature_df, retailer.strip() or "Retailer")
# #     brief, brief_source, llm_audit = generate_nvidia_brief(nvidia_key.strip(), nvidia_model.strip(), feature_df, all_articles, retailer, region)
# #     collector_states["BRIEF"] = {
# #         "status": "success",
# #         "detail": "NVIDIA generated the brief" if brief_source == "nvidia" else f"Fallback brief generated: {llm_audit.get('fallback_reason', 'NVIDIA unavailable')}",
# #     }
# #     render_run_monitor(run_status_slot, "Run complete", collector_states, 100)

# #     st.session_state["run"] = {
# #         "timestamp": utc_now(),
# #         "run_config": run_config,
# #         "results": results,
# #         "feature_df": feature_df,
# #         "articles": all_articles,
# #         "brief": brief,
# #         "brief_source": brief_source,
# #         "llm_audit": llm_audit,
# #     }
# #     render_sidebar_status(sidebar_status_slot, "Run complete. Opening Results...", "success")
# #     st.session_state["force_results_view"] = True
# #     st.session_state["last_collector_states"] = collector_states
# #     st.rerun()


# # if view == "Results":
# #     run = st.session_state.get("run")
# #     if not run:
# #         st.markdown(
# #             "<div class='empty-console'>"
# #             "<div>"
# #             "<div class='hero-kicker'>Results Command Center</div>"
# #             "<div class='empty-title'>No intelligence run yet.</div>"
# #             "<div class='empty-body'>Run the governed signal pipeline to populate decision cards, Retailer impact mapping, score explanations, source evidence, and the executive brief.</div>"
# #             "</div>"
# #             "<div class='hero-side' style='min-width:260px;'>"
# #             "<div class='hero-side-label'>Output Model</div>"
# #             "<div class='hero-side-row'><span>Decisions</span><strong>Owner actions</strong></div>"
# #             "<div class='hero-side-row'><span>Evidence</span><strong>Live API rows</strong></div>"
# #             "<div class='hero-side-row'><span>Brief</span><strong>NVIDIA / fallback</strong></div>"
# #             "</div>"
# #             "</div>",
# #             unsafe_allow_html=True,
# #         )
# #         render_workflow_strip()
# #     else:
# #         feature_df = run["feature_df"]
# #         if not feature_df.empty and "dollar_tree_category" not in feature_df.columns:
# #             feature_df = enrich_feature_rows_for_retailer(feature_df, str(run.get("run_config", {}).get("retailer", retailer_label)))
# #             run["feature_df"] = feature_df
# #         render_results_command_header(run, feature_df)
# #         if feature_df.empty:
# #             st.markdown('<div class="small-header">Run Summary</div>', unsafe_allow_html=True)
# #             cols = st.columns(4)
# #             for col, title in zip(cols, ["Signals", "Avg Score", "Highest Score", "Brief"]):
# #                 with col:
# #                     render_metric_card(title, "0", "No successful feature rows yet.")
# #         else:
# #             avg_score = round(float(feature_df["risk_score"].mean()), 2)
# #             top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
# #             dollar_tree_scores = compute_dollar_tree_kpis(feature_df)
# #             command_tab, explain_tab, evidence_tab, brief_tab = st.tabs(["Command Center", "Explainability", "Evidence And Export", "Executive Brief"])

# #             with command_tab:
# #                 st.markdown('<div class="small-header">Trust And Evidence Status</div>', unsafe_allow_html=True)
# #                 render_trust_panel(run, feature_df)

# #                 st.markdown('<div class="small-header">Run Summary Metrics</div>', unsafe_allow_html=True)
# #                 cols = st.columns(4)
# #                 with cols[0]:
# #                     render_metric_card("Signals", str(len(feature_df)), "Forecast-ready rows generated.")
# #                 with cols[1]:
# #                     render_metric_card("Average Score", str(avg_score), f"{risk_band(avg_score)} overall signal intensity.")
# #                 with cols[2]:
# #                     render_metric_card("Top Signal", str(top["risk_score"]), str(top["signal_area"]), str(top["confidence"]))
# #                 with cols[3]:
# #                     render_metric_card("News Articles", str(len(run["articles"])), "Deduplicated retail news items.")

# #                 st.markdown('<div class="small-header">Retail Enterprise KPIs</div>', unsafe_allow_html=True)
# #                 cscore_cols = st.columns(4)
# #                 for col, (name, score) in zip(cscore_cols, dollar_tree_scores.items()):
# #                     with col:
# #                         render_metric_card(name, str(score), f"{risk_band(score)} priority for planning.")

# #                 st.markdown('<div class="small-header">Decision Summary</div>', unsafe_allow_html=True)
# #                 render_decision_summary(feature_df, run["results"])

# #                 st.markdown('<div class="small-header">Retail Impact Matrix</div>', unsafe_allow_html=True)
# #                 render_dollar_tree_impact_matrix(feature_df)

# #             with explain_tab:
# #                 st.markdown('<div class="small-header">How The Agent Explains A Signal</div>', unsafe_allow_html=True)
# #                 render_explainability_ladder()

# #                 st.markdown('<div class="small-header">Scenario Simulator</div>', unsafe_allow_html=True)
# #                 render_scenario_simulator(feature_df)

# #                 st.markdown('<div class="small-header">Signal Scores</div>', unsafe_allow_html=True)
# #                 render_score_chart(feature_df)

# #                 st.markdown('<div class="small-header">Score Explainability</div>', unsafe_allow_html=True)
# #                 render_score_explainability(feature_df)

# #             with evidence_tab:
# #                 st.markdown('<div class="small-header">Forecast Feature Table</div>', unsafe_allow_html=True)
# #                 st.dataframe(feature_df, width="stretch", hide_index=True)

# #                 csv_data = feature_df.to_csv(index=False).encode("utf-8")
# #                 json_data = json.dumps(feature_df.to_dict(orient="records"), indent=2).encode("utf-8")
# #                 c1, c2 = st.columns([1, 1])
# #                 with c1:
# #                     st.download_button("Download forecast_features.csv", csv_data, "forecast_features.csv", "text/csv", width="stretch")
# #                 with c2:
# #                     st.download_button("Download normalized_signals.json", json_data, "normalized_signals.json", "application/json", width="stretch")

# #                 with st.expander("Glossary and interpretation guide", expanded=False):
# #                     render_glossary()

# #             with brief_tab:
# #                 render_executive_brief(run, feature_df)


# # if view == "Evidence Audit":
# #     run = st.session_state.get("run")
# #     if not run:
# #         st.markdown(
# #             "<div class='empty-console'>"
# #             "<div>"
# #             "<div class='hero-kicker'>Evidence Audit</div>"
# #             "<div class='empty-title'>No run evidence yet.</div>"
# #             "<div class='empty-body'>Run the governed signal pipeline to populate provenance, source health, mock-data status, normalized signal reasoning, LLM prompt trace, payload hash, and raw API evidence.</div>"
# #             "</div>"
# #             "<div class='hero-side' style='min-width:260px;'>"
# #             "<div class='hero-side-label'>Audit Model</div>"
# #             "<div class='hero-side-row'><span>Provenance</span><strong>Source health</strong></div>"
# #             "<div class='hero-side-row'><span>Trace</span><strong>Prompt + payload</strong></div>"
# #             "<div class='hero-side-row'><span>Evidence</span><strong>Raw API data</strong></div>"
# #             "</div>"
# #             "</div>",
# #             unsafe_allow_html=True,
# #         )
# #     else:
# #         feature_df = run.get("feature_df", pd.DataFrame())
# #         run_config = run.get(
# #             "run_config",
# #             {
# #                 "retailer": retailer_label,
# #                 "region": region,
# #                 "country": country,
# #                 "language": language,
# #                 "enabled_sources": {key: key in run.get("results", {}) for key in SOURCE_ORDER},
# #             },
# #         )
# #         if not feature_df.empty and "dollar_tree_category" not in feature_df.columns:
# #             feature_df = enrich_feature_rows_for_retailer(feature_df, str(run_config.get("retailer", retailer_label)))
# #             run["feature_df"] = feature_df
# #         llm_audit = run.get("llm_audit") or build_base_llm_audit(
# #             feature_df,
# #             run.get("articles", []),
# #             run_config.get("retailer", retailer_label),
# #             run_config.get("region", region),
# #             nvidia_model.strip() or DEFAULT_NVIDIA_MODEL,
# #         )
# #         llm_audit["brief_source"] = run.get("brief_source", llm_audit.get("brief_source", "unknown"))
# #         evidence_records = build_collector_evidence(run.get("results", {}), run_config)
# #         evidence_df = pd.DataFrame(evidence_records)
# #         mock_used = any_mock_used(run.get("results", {}), llm_audit)
# #         pulled_sources = int((evidence_df["raw_records_pulled"] > 0).sum()) if not evidence_df.empty else 0
# #         raw_records = int(evidence_df["raw_records_pulled"].sum()) if not evidence_df.empty else 0

# #         evidence_bundle = {
# #             "timestamp": run.get("timestamp"),
# #             "mock_data_used": mock_used,
# #             "run_config": run_config,
# #             "collector_evidence": evidence_records,
# #             "normalized_features": feature_df.to_dict(orient="records") if not feature_df.empty else [],
# #             "llm_audit": llm_audit,
# #             "results": {
# #                 name: {
# #                     "status": result.get("status"),
# #                     "source": result.get("source"),
# #                     "error": result.get("error"),
# #                     "rows": result.get("rows", []),
# #                     "items": result.get("items", []),
# #                     "raw": result.get("raw"),
# #                 }
# #                 for name, result in run.get("results", {}).items()
# #             },
# #         }

# #         render_audit_command_header(run, mock_used, raw_records, pulled_sources, llm_audit, feature_df)
# #         render_audit_banner(mock_used, str(run.get("brief_source", "unknown")))

# #         provenance_tab, normalized_tab, llm_tab, raw_tab = st.tabs(["Provenance", "Normalized Signals", "LLM Trace", "Raw Payloads"])

# #         with provenance_tab:
# #             st.markdown('<div class="small-header">Chain Of Custody</div>', unsafe_allow_html=True)
# #             render_audit_lineage()

# #             c1, c2, c3, c4 = st.columns(4)
# #             with c1:
# #                 render_audit_card("Mock Data Used", "Yes" if mock_used else "No", "Collector rows are marked per run result.")
# #             with c2:
# #                 render_audit_card("Raw Records", str(raw_records), f"{pulled_sources} source(s) returned inspectable data.")
# #             with c3:
# #                 render_audit_card(
# #                     "Rows Sent To Brief",
# #                     f"{llm_audit.get('feature_rows_sent', 0)} / {llm_audit.get('feature_rows_available', 0)}",
# #                     "Feature payload is capped for concise LLM context.",
# #                 )
# #             with c4:
# #                 render_audit_card(
# #                     "LLM Call",
# #                     "NVIDIA" if llm_audit.get("sent_to_llm") else "No external LLM",
# #                     str(llm_audit.get("fallback_reason") or "NVIDIA analyzed the shown payload."),
# #                 )

# #             st.markdown('<div class="small-header">Collector Evidence Table</div>', unsafe_allow_html=True)
# #             st.dataframe(evidence_df, width="stretch", hide_index=True)

# #             st.download_button(
# #                 "Download evidence_audit.json",
# #                 json.dumps(evidence_bundle, indent=2, default=str).encode("utf-8"),
# #                 "evidence_audit.json",
# #                 "application/json",
# #                 width="stretch",
# #             )

# #         with normalized_tab:
# #             st.markdown('<div class="small-header">Normalized Feature Rows And Score Reasoning</div>', unsafe_allow_html=True)
# #             if feature_df.empty:
# #                 st.info("No normalized feature rows were generated. Check collector statuses and errors above.")
# #             else:
# #                 preferred_cols = [
# #                     "source",
# #                     "signal_area",
# #                     "signal_name",
# #                     "dollar_tree_category",
# #                     "enterprise_kpi",
# #                     "planning_owner",
# #                     "demand_direction",
# #                     "region",
# #                     "region_scope",
# #                     "signal_value",
# #                     "risk_score",
# #                     "confidence",
# #                     "score_reason",
# #                     "impact_hypothesis",
# #                     "business_impact",
# #                     "recommended_action",
# #                     "internal_data_needed",
# #                     "raw_reference",
# #                 ]
# #                 visible_cols = [col for col in preferred_cols if col in feature_df.columns]
# #                 st.dataframe(feature_df[visible_cols], width="stretch", hide_index=True)

# #             st.markdown('<div class="small-header">Score Explainability Cards</div>', unsafe_allow_html=True)
# #             render_score_explainability(feature_df)

# #         with llm_tab:
# #             st.markdown('<div class="small-header">LLM Analysis Trace</div>', unsafe_allow_html=True)
# #             trace_cols = st.columns(4)
# #             with trace_cols[0]:
# #                 render_metric_card("Brief Source", "NVIDIA" if run.get("brief_source") == "nvidia" else "Fallback", "NVIDIA if sent; fallback if local rules were used.")
# #             with trace_cols[1]:
# #                 render_metric_card("Payload Hash", str(llm_audit.get("payload_hash_sha256", ""))[:12], "SHA-256 fingerprint of features/articles payload.")
# #             with trace_cols[2]:
# #                 render_metric_card("Articles Sent", str(llm_audit.get("articles_sent", 0)), f"{llm_audit.get('articles_available', 0)} available.")
# #             with trace_cols[3]:
# #                 render_metric_card("Fallback Used", "Yes" if llm_audit.get("fallback_used") else "No", str(llm_audit.get("fallback_reason") or "External LLM response used."))

# #             with st.expander("Prompt and payload used for the brief", expanded=True):
# #                 if llm_audit.get("sent_to_llm"):
# #                     st.success("NVIDIA was called with only the feature rows and article records shown below.")
# #                 else:
# #                     st.warning("No external LLM call was made for this run. The brief was generated by deterministic local rules using the same feature rows.")
# #                 st.markdown("System prompt")
# #                 st.code(str(llm_audit.get("system_prompt", "")), language="text")
# #                 st.markdown("User prompt")
# #                 st.code(str(llm_audit.get("user_prompt", "")), language="text")
# #                 st.markdown("Payload")
# #                 st.code(json.dumps(llm_audit.get("payload", {}), indent=2, default=str)[:16000], language="json")
# #                 if llm_audit.get("nvidia_attempts"):
# #                     st.markdown("NVIDIA timing and retry audit")
# #                     st.dataframe(pd.DataFrame(llm_audit.get("nvidia_attempts", [])), width="stretch", hide_index=True)
# #                 if llm_audit.get("technical_error"):
# #                     st.markdown("Technical diagnostic")
# #                     st.code(str(llm_audit.get("technical_error", ""))[:2000], language="text")

# #             with st.expander("Brief output", expanded=False):
# #                 st.markdown(f"<div class='brief-box'>{brief_to_html(str(run.get('brief', '')))}</div>", unsafe_allow_html=True)

# #         with raw_tab:
# #             st.markdown('<div class="small-header">Raw Pulled Data By Source</div>', unsafe_allow_html=True)
# #             for name, result in run.get("results", {}).items():
# #                 source_name = SOURCE_LABELS.get(name, name.upper())
# #                 raw_count = count_raw_records(result.get("raw"), result.get("items"))
# #                 with st.expander(f"{source_name} - {result.get('status', 'unknown')} - {raw_count} raw record(s)", expanded=False):
# #                     if result.get("error"):
# #                         st.warning(result["error"])
# #                     if result.get("items"):
# #                         st.markdown("Cleaned items")
# #                         st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
# #                     raw_preview = result.get("raw")
# #                     if raw_preview is not None:
# #                         st.markdown("Raw payload")
# #                         st.code(json.dumps(raw_preview, indent=2, default=str)[:16000], language="json")
# #                     if result.get("rows"):
# #                         st.markdown("Normalized feature rows from this source")
# #                         st.dataframe(pd.DataFrame(result["rows"]), width="stretch", hide_index=True)


# # if view == "Raw Data":
# #     run = st.session_state.get("run")
# #     if not run:
# #         st.markdown(
# #             "<div class='empty-console'>"
# #             "<div>"
# #             "<div class='hero-kicker'>Raw Evidence</div>"
# #             "<div class='empty-title'>Collector payloads will appear after a run.</div>"
# #             "<div class='empty-body'>This view is intentionally evidence-first: GNews articles, CPI JSON, FDA recall records, NOAA weather alerts, Apify errors, and cleaned item tables stay inspectable for validation.</div>"
# #             "</div>"
# #             "</div>",
# #             unsafe_allow_html=True,
# #         )
# #     else:
# #         for name, result in run["results"].items():
# #             status = result.get("status", "unknown")
# #             label = f"{name.upper()} - {status}"
# #             with st.expander(label, expanded=False):
# #                 if result.get("error"):
# #                     st.warning(result["error"])
# #                 if result.get("items"):
# #                     st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
# #                 raw_preview = result.get("raw")
# #                 if raw_preview is not None:
# #                     st.code(json.dumps(raw_preview, indent=2, default=str)[:7000], language="json")


# # st.markdown(
# #     """
# #     <p class="subtle">
# #     Note: Google Trends values are relative indexes, GNews is a lightweight news signal, and recall data should be matched
# #     against internal SKU/UPC and inventory records before operational decisions.
# #     </p>
# #     """,
# #     unsafe_allow_html=True,
# # )

# # # import json
# # # import hashlib
# # # import os
# # # import re
# # # import xml.etree.ElementTree as ET
# # # from datetime import datetime, timezone
# # # from email.utils import parsedate_to_datetime
# # # from html import escape, unescape
# # # from typing import Any, Dict, List, Optional, Tuple
# # # from urllib.parse import quote_plus

# # # import pandas as pd
# # # import plotly.graph_objects as go
# # # import requests
# # # import streamlit as st

# # # try:
# # #     from dotenv import load_dotenv
# # # except ImportError:  # pragma: no cover - optional local convenience
# # #     load_dotenv = None


# # # if load_dotenv:
# # #     load_dotenv()


# # # NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# # # DEFAULT_NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
# # # BLS_CPI_SERIES = {
# # #     "Headline CPI": "CUUR0000SA0",
# # #     "Food at home": "CUUR0000SAF11",
# # #     "Household furnishings": "CUUR0000SAH3",
# # #     "Gasoline": "CUUR0000SETB",
# # # }
# # # APIFY_SAFE_TIME_RANGE = "today 7-d"
# # # APIFY_HARD_KEYWORD_LIMIT = 2

# # # SOURCE_ORDER = ["gnews", "bls", "fda", "weather", "apify"]
# # # SOURCE_LABELS = {
# # #     "gnews": "GNews / Google News RSS",
# # #     "bls": "BLS CPI",
# # #     "fda": "openFDA Food Enforcement",
# # #     "weather": "NOAA Weather Alerts",
# # #     "apify": "Apify Google Trends",
# # # }
# # # SOURCE_ENDPOINTS = {
# # #     "gnews": "GNews package or Google News RSS search feed",
# # #     "bls": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
# # #     "fda": "https://api.fda.gov/food/enforcement.json",
# # #     "weather": "https://api.weather.gov/alerts/active",
# # #     "apify": "apify/google-trends-scraper",
# # # }
# # # ANALYSIS_METHODS = {
# # #     "gnews": "Classifies article titles/descriptions into event types, sentiment, source confidence, then averages article risk into one news feature.",
# # #     "bls": "Batches CPI series, calculates month-over-month and year-over-year movement, then scores inflation pressure.",
# # #     "fda": "Classifies recall reason, FDA class, status, UPC presence, and state coverage, then uses highest adjusted recall severity.",
# # #     "weather": "Collects active NOAA alerts for the selected state, weights severity, urgency, and certainty, then caps supply-chain weather risk at 10.",
# # #     "apify": "Uses top regional Google Trends index divided by 10; backend hard-limits time range and keyword count to protect quota.",
# # # }


# # # st.set_page_config(
# # #     page_title="Market Intelligence Command Center",
# # #     page_icon="DT",
# # #     layout="wide",
# # #     initial_sidebar_state="expanded",
# # # )


# # # st.markdown(
# # #     """
# # #     <style>
# # #     @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
# # #     :root {
# # #         --bg: #F5F7FB;
# # #         --sf: #FFFFFF;
# # #         --s2: #F8FAFE;
# # #         --s3: #F0F4F9;
# # #         --s4: #E9EFF5;
# # #         --or: #F47B25;
# # #         --olt: #FF9F50;
# # #         --odk: #C45D0A;
# # #         --og: rgba(244,123,37,0.12);
# # #         --ob: rgba(244,123,37,0.07);
# # #         --obr: rgba(244,123,37,0.25);
# # #         --bl: #E2E8F0;
# # #         --t: #1E293B;
# # #         --t2: #475569;
# # #         --t3: #94A3B8;
# # #         --gr: #22C55E;
# # #         --gbg: rgba(34,197,94,0.10);
# # #         --am: #F59E0B;
# # #         --abg: rgba(245,158,11,0.10);
# # #         --rd: #EF4444;
# # #         --rbg: rgba(239,68,68,0.08);
# # #         --r: 12px;
# # #         --rl: 16px;
# # #         --sh: 0 1px 3px rgba(0,0,0,0.04);
# # #         --shm: 0 6px 14px -4px rgba(0,0,0,0.10);
# # #         --tr: 0.2s cubic-bezier(0.4,0,0.2,1);
# # #     }
# # #     * { box-sizing: border-box; }
# # #     html, body, [class*="css"] {
# # #         font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
# # #         color: var(--t);
# # #     }
# # #     .stApp { background: var(--bg); }
# # #     #MainMenu, footer { visibility: hidden; }
# # #     header { visibility: hidden; }
# # #     .accent-bar {
# # #         height: 3px;
# # #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt), var(--or));
# # #         background-size: 200%;
# # #         animation: shimmer 3s linear infinite;
# # #         width: 100%;
# # #         margin: -1.25rem 0 0.9rem 0;
# # #     }
# # #     @keyframes shimmer { 0% { background-position: 200%; } 100% { background-position: -200%; } }
# # #     @keyframes pdot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.5); } 50% { box-shadow: 0 0 0 5px rgba(34,197,94,0); } }
# # #     .ldot {
# # #         width: 7px;
# # #         height: 7px;
# # #         border-radius: 50%;
# # #         background: var(--gr);
# # #         animation: pdot 2s infinite;
# # #         display: inline-block;
# # #     }
# # #     .main .block-container {
# # #         padding-top: 1.25rem;
# # #         max-width: 1400px;
# # #     }
# # #     [data-testid="stSidebar"] {
# # #         background: var(--sf) !important;
# # #         border-right: 1px solid var(--bl) !important;
# # #     }
# # #     [data-testid="stSidebar"] * {
# # #         color: var(--t2);
# # #     }
# # #     h1, h2, h3 {
# # #         color: var(--t);
# # #         letter-spacing: 0;
# # #     }
# # #     h1 { font-size: 2.25rem; font-weight: 900; letter-spacing: -0.02em; }
# # #     .subtle {
# # #         color: var(--t2);
# # #         font-size: 0.94rem;
# # #         line-height: 1.55;
# # #     }
# # #     .topbar {
# # #         height: 54px;
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: var(--rl);
# # #         display: flex;
# # #         align-items: center;
# # #         padding: 0 18px;
# # #         gap: 12px;
# # #         box-shadow: var(--sh);
# # #         margin-bottom: 18px;
# # #     }
# # #     .tt { font-size: 14px; font-weight: 800; color: var(--t); }
# # #     .tt span { color: var(--t3); font-weight: 500; }
# # #     .tbadge {
# # #         background: var(--ob);
# # #         border: 1px solid var(--obr);
# # #         color: var(--or);
# # #         font-size: 10px;
# # #         font-weight: 800;
# # #         padding: 2px 8px;
# # #         border-radius: 20px;
# # #     }
# # #     .hero-shell {
# # #         background:
# # #             radial-gradient(circle at 88% 18%, rgba(244,123,37,0.18), transparent 28%),
# # #             linear-gradient(135deg, #FFFFFF 0%, #F8FAFE 54%, #FFF7ED 100%);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 22px;
# # #         padding: 24px 26px;
# # #         box-shadow: 0 12px 30px -22px rgba(15,23,42,0.35);
# # #         margin-bottom: 16px;
# # #         position: relative;
# # #         overflow: hidden;
# # #     }
# # #     .hero-shell:before {
# # #         content: "";
# # #         position: absolute;
# # #         left: 0;
# # #         right: 0;
# # #         top: 0;
# # #         height: 4px;
# # #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
# # #     }
# # #     .hero-kicker {
# # #         display: inline-flex;
# # #         align-items: center;
# # #         gap: 7px;
# # #         background: var(--ob);
# # #         border: 1px solid var(--obr);
# # #         color: var(--or);
# # #         border-radius: 999px;
# # #         padding: 4px 10px;
# # #         font-size: 10px;
# # #         font-weight: 900;
# # #         letter-spacing: 0.8px;
# # #         text-transform: uppercase;
# # #         margin-bottom: 12px;
# # #     }
# # #     .hero-title {
# # #         font-size: 42px;
# # #         line-height: 1.02;
# # #         letter-spacing: -0.04em;
# # #         font-weight: 950;
# # #         color: var(--t);
# # #         max-width: 820px;
# # #         margin: 0;
# # #     }
# # #     .hero-copy {
# # #         color: var(--t2);
# # #         font-size: 14px;
# # #         line-height: 1.7;
# # #         max-width: 820px;
# # #         margin: 14px 0 0 0;
# # #     }
# # #     .hero-side {
# # #         background: rgba(255,255,255,0.74);
# # #         border: 1px solid rgba(226,232,240,0.9);
# # #         border-radius: var(--rl);
# # #         padding: 14px;
# # #         box-shadow: var(--sh);
# # #     }
# # #     .hero-side-label {
# # #         color: var(--t3);
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: 1.2px;
# # #         text-transform: uppercase;
# # #         margin-bottom: 8px;
# # #     }
# # #     .hero-side-row {
# # #         display: flex;
# # #         justify-content: space-between;
# # #         gap: 12px;
# # #         border-top: 1px solid var(--bl);
# # #         padding-top: 8px;
# # #         margin-top: 8px;
# # #         font-size: 12px;
# # #         color: var(--t2);
# # #     }
# # #     .hero-side-row strong { color: var(--t); }
# # #     .source-tile {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 14px;
# # #         padding: 13px 14px;
# # #         box-shadow: var(--sh);
# # #         min-height: 86px;
# # #         transition: all var(--tr);
# # #     }
# # #     .source-tile:hover { border-color: var(--obr); box-shadow: var(--shm); transform: translateY(-1px); }
# # #     .source-name {
# # #         font-size: 12px;
# # #         font-weight: 850;
# # #         color: var(--t);
# # #         margin-bottom: 5px;
# # #     }
# # #     .source-meta {
# # #         color: var(--t2);
# # #         font-size: 11px;
# # #         line-height: 1.45;
# # #     }
# # #     .console-panel {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 18px;
# # #         padding: 18px;
# # #         box-shadow: var(--sh);
# # #         margin-top: 12px;
# # #     }
# # #     .empty-console {
# # #         background:
# # #             linear-gradient(135deg, rgba(244,123,37,0.08), rgba(255,255,255,0.85)),
# # #             var(--sf);
# # #         border: 1px dashed var(--obr);
# # #         border-radius: 18px;
# # #         padding: 28px;
# # #         min-height: 210px;
# # #         display: flex;
# # #         align-items: center;
# # #         justify-content: space-between;
# # #         gap: 20px;
# # #     }
# # #     .empty-title {
# # #         color: var(--t);
# # #         font-size: 22px;
# # #         line-height: 1.15;
# # #         font-weight: 900;
# # #         letter-spacing: -0.025em;
# # #         margin-bottom: 8px;
# # #     }
# # #     .empty-body {
# # #         color: var(--t2);
# # #         font-size: 13px;
# # #         line-height: 1.65;
# # #         max-width: 620px;
# # #     }
# # #     .workflow {
# # #         display: grid;
# # #         grid-template-columns: repeat(5, minmax(0, 1fr));
# # #         gap: 8px;
# # #         margin-top: 12px;
# # #     }
# # #     .workflow-step {
# # #         background: var(--s2);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 12px;
# # #         padding: 10px;
# # #         font-size: 11px;
# # #         color: var(--t2);
# # #         font-weight: 700;
# # #     }
# # #     .workflow-step span {
# # #         display: block;
# # #         color: var(--or);
# # #         font-size: 9px;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #         font-weight: 900;
# # #         margin-bottom: 3px;
# # #     }
# # #     .metric-card {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: var(--rl);
# # #         padding: 16px 18px;
# # #         min-height: 132px;
# # #         box-shadow: var(--sh);
# # #         transition: all var(--tr);
# # #     }
# # #     .metric-card:hover {
# # #         transform: translateY(-1px);
# # #         box-shadow: var(--shm);
# # #         border-color: var(--obr);
# # #     }
# # #     .metric-label {
# # #         color: var(--t3);
# # #         font-size: 0.68rem;
# # #         text-transform: uppercase;
# # #         letter-spacing: 0.11em;
# # #         margin-bottom: 8px;
# # #         font-weight: 800;
# # #     }
# # #     .metric-value {
# # #         color: var(--t);
# # #         font-size: 2.05rem;
# # #         font-weight: 900;
# # #         line-height: 1;
# # #         letter-spacing: -0.04em;
# # #     }
# # #     .metric-note {
# # #         color: var(--t2);
# # #         font-size: 0.82rem;
# # #         margin-top: 10px;
# # #         line-height: 1.4;
# # #     }
# # #     .pill {
# # #         display: inline-block;
# # #         border-radius: 999px;
# # #         padding: 3px 10px;
# # #         font-size: 0.72rem;
# # #         font-weight: 800;
# # #         border: 1px solid var(--bl);
# # #         color: var(--t2);
# # #         background: var(--s2);
# # #         margin-top: 8px;
# # #     }
# # #     .pill-high { color: var(--gr); background: var(--gbg); border-color: rgba(34,197,94,0.2); }
# # #     .pill-medium { color: var(--am); background: var(--abg); border-color: rgba(245,158,11,0.2); }
# # #     .pill-low { color: var(--rd); background: var(--rbg); border-color: rgba(239,68,68,0.2); }
# # #     .brief-box {
# # #         background: var(--sf);
# # #         border: 1px solid var(--obr);
# # #         border-left: 4px solid var(--or);
# # #         border-radius: var(--rl);
# # #         padding: 20px 22px;
# # #         color: var(--t);
# # #         line-height: 1.65;
# # #         box-shadow: 0 0 0 3px var(--og);
# # #     }
# # #     .brief-shell {
# # #         background:
# # #             linear-gradient(135deg, rgba(255,255,255,0.98), rgba(248,250,254,0.94)),
# # #             var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 20px;
# # #         box-shadow: 0 18px 36px -28px rgba(15,23,42,0.55);
# # #         overflow: hidden;
# # #         margin-top: 8px;
# # #     }
# # #     .brief-header {
# # #         background:
# # #             radial-gradient(circle at 92% 18%, rgba(244,123,37,0.18), transparent 28%),
# # #             linear-gradient(135deg, rgba(244,123,37,0.09), rgba(255,255,255,0.96));
# # #         border-bottom: 1px solid var(--bl);
# # #         padding: 18px 20px;
# # #     }
# # #     .brief-kicker {
# # #         color: var(--or);
# # #         font-size: 10px;
# # #         font-weight: 900;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #         margin-bottom: 6px;
# # #     }
# # #     .brief-title {
# # #         color: var(--t);
# # #         font-size: 22px;
# # #         line-height: 1.15;
# # #         font-weight: 950;
# # #         letter-spacing: -0.02em;
# # #         margin-bottom: 8px;
# # #     }
# # #     .brief-summary-text {
# # #         color: var(--t2);
# # #         font-size: 13px;
# # #         line-height: 1.55;
# # #         max-width: 980px;
# # #     }
# # #     .brief-meta-strip {
# # #         display: grid;
# # #         grid-template-columns: repeat(4, minmax(0, 1fr));
# # #         gap: 8px;
# # #         margin-top: 14px;
# # #     }
# # #     .brief-meta-chip {
# # #         background: rgba(255,255,255,0.82);
# # #         border: 1px solid rgba(226,232,240,0.95);
# # #         border-radius: 12px;
# # #         padding: 10px 11px;
# # #     }
# # #     .brief-meta-label {
# # #         color: var(--t3);
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: .9px;
# # #         text-transform: uppercase;
# # #         margin-bottom: 4px;
# # #     }
# # #     .brief-meta-value {
# # #         color: var(--t);
# # #         font-size: 13px;
# # #         line-height: 1.25;
# # #         font-weight: 900;
# # #     }
# # #     .brief-section-grid {
# # #         display: grid;
# # #         grid-template-columns: repeat(2, minmax(0, 1fr));
# # #         gap: 12px;
# # #         padding: 16px;
# # #     }
# # #     .brief-section-card {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 14px;
# # #         padding: 14px 15px;
# # #         min-height: 150px;
# # #         box-shadow: var(--sh);
# # #     }
# # #     .brief-section-card.primary {
# # #         grid-column: 1 / -1;
# # #         min-height: 0;
# # #         border-color: var(--obr);
# # #         background: linear-gradient(135deg, rgba(244,123,37,0.055), rgba(255,255,255,0.96));
# # #     }
# # #     .brief-section-title {
# # #         color: var(--or);
# # #         font-size: 11px;
# # #         font-weight: 900;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #         margin: 0 0 9px 0;
# # #     }
# # #     .brief-section-card p,
# # #     .brief-box p {
# # #         margin: 0 0 8px 0;
# # #         font-size: 13px;
# # #         color: var(--t2);
# # #         line-height: 1.55;
# # #     }
# # #     .brief-list {
# # #         margin: 0 0 0 18px;
# # #         padding: 0;
# # #     }
# # #     .brief-list li {
# # #         margin-bottom: 8px;
# # #         color: var(--t2);
# # #         font-size: 13px;
# # #         line-height: 1.45;
# # #     }
# # #     .score-explain-card {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: var(--rl);
# # #         padding: 14px 16px;
# # #         box-shadow: var(--sh);
# # #         margin: 8px 0;
# # #     }
# # #     .score-explain-head {
# # #         display: flex;
# # #         align-items: flex-start;
# # #         justify-content: space-between;
# # #         gap: 12px;
# # #         border-bottom: 1px solid var(--bl);
# # #         padding-bottom: 10px;
# # #         margin-bottom: 10px;
# # #     }
# # #     .score-explain-title {
# # #         color: var(--t);
# # #         font-size: 13px;
# # #         font-weight: 900;
# # #         margin-bottom: 3px;
# # #     }
# # #     .score-explain-meta {
# # #         color: var(--t3);
# # #         font-size: 10px;
# # #         font-weight: 800;
# # #         letter-spacing: .7px;
# # #         text-transform: uppercase;
# # #     }
# # #     .score-number {
# # #         min-width: 74px;
# # #         text-align: center;
# # #         border-radius: 12px;
# # #         border: 1px solid var(--obr);
# # #         background: var(--ob);
# # #         color: var(--or);
# # #         font-size: 24px;
# # #         line-height: 1;
# # #         font-weight: 950;
# # #         padding: 9px 8px;
# # #     }
# # #     .score-number span {
# # #         display: block;
# # #         color: var(--t3);
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: .8px;
# # #         margin-top: 3px;
# # #     }
# # #     .score-explain-label {
# # #         color: var(--t3);
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #         margin: 8px 0 3px 0;
# # #     }
# # #     .score-explain-text {
# # #         color: var(--t2);
# # #         font-size: 12px;
# # #         line-height: 1.5;
# # #     }
# # #     .brief-grounding {
# # #         display: grid;
# # #         grid-template-columns: repeat(4, minmax(0, 1fr));
# # #         gap: 8px;
# # #         margin-bottom: 12px;
# # #     }
# # #     .small-header {
# # #         color: var(--t3);
# # #         font-size: 0.68rem;
# # #         text-transform: uppercase;
# # #         letter-spacing: 0.12em;
# # #         font-weight: 800;
# # #         margin: 8px 0 12px 0;
# # #         padding-bottom: 6px;
# # #         border-bottom: 1px solid var(--bl);
# # #     }
# # #     .action-card {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: var(--r);
# # #         padding: 12px 14px;
# # #         box-shadow: var(--sh);
# # #         min-height: 112px;
# # #     }
# # #     .action-label {
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #         color: var(--or);
# # #         margin-bottom: 7px;
# # #     }
# # #     .action-title {
# # #         font-size: 13px;
# # #         font-weight: 800;
# # #         color: var(--t);
# # #         margin-bottom: 5px;
# # #     }
# # #     .action-body {
# # #         font-size: 12px;
# # #         color: var(--t2);
# # #         line-height: 1.45;
# # #     }
# # #     .note-box {
# # #         background: rgba(244,123,37,0.04);
# # #         border-left: 3px solid var(--or);
# # #         border-radius: 0 8px 8px 0;
# # #         padding: 9px 12px;
# # #         font-size: 12px;
# # #         color: var(--t2);
# # #         margin: 8px 0;
# # #     }
# # #     .run-monitor {
# # #         background: var(--sf);
# # #         border: 1px solid var(--obr);
# # #         border-radius: 16px;
# # #         padding: 14px 16px;
# # #         box-shadow: 0 0 0 3px var(--og);
# # #         margin: 8px 0 16px 0;
# # #     }
# # #     .run-monitor-head {
# # #         display: flex;
# # #         align-items: center;
# # #         justify-content: space-between;
# # #         gap: 12px;
# # #         margin-bottom: 10px;
# # #     }
# # #     .run-monitor-title {
# # #         color: var(--t);
# # #         font-size: 13px;
# # #         font-weight: 900;
# # #     }
# # #     .run-monitor-sub {
# # #         color: var(--t3);
# # #         font-size: 10px;
# # #         font-weight: 800;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #     }
# # #     .run-progress-track {
# # #         height: 7px;
# # #         background: var(--s3);
# # #         border-radius: 999px;
# # #         overflow: hidden;
# # #         margin: 8px 0 12px 0;
# # #     }
# # #     .run-progress-fill {
# # #         height: 100%;
# # #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
# # #         border-radius: 999px;
# # #         transition: width var(--tr);
# # #     }
# # #     .run-status-grid {
# # #         display: grid;
# # #         grid-template-columns: repeat(4, minmax(0, 1fr));
# # #         gap: 8px;
# # #     }
# # #     .run-status-card {
# # #         background: var(--s2);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 12px;
# # #         padding: 10px;
# # #         min-height: 72px;
# # #     }
# # #     .run-status-name {
# # #         font-size: 11px;
# # #         font-weight: 900;
# # #         color: var(--t);
# # #         margin-bottom: 5px;
# # #     }
# # #     .run-status-detail {
# # #         font-size: 10px;
# # #         color: var(--t2);
# # #         line-height: 1.35;
# # #     }
# # #     .status-badge {
# # #         display: inline-flex;
# # #         align-items: center;
# # #         gap: 4px;
# # #         padding: 2px 7px;
# # #         border-radius: 999px;
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: .6px;
# # #         text-transform: uppercase;
# # #         margin-bottom: 6px;
# # #     }
# # #     .status-running { background: var(--ob); color: var(--or); border: 1px solid var(--obr); }
# # #     .status-success { background: var(--gbg); color: var(--gr); border: 1px solid rgba(34,197,94,.2); }
# # #     .status-failed { background: var(--rbg); color: var(--rd); border: 1px solid rgba(239,68,68,.2); }
# # #     .status-skipped { background: rgba(100,116,139,.08); color: var(--t2); border: 1px solid var(--bl); }
# # #     .status-queued { background: var(--s3); color: var(--t3); border: 1px solid var(--bl); }
# # #     .audit-banner {
# # #         background: linear-gradient(135deg, rgba(34,197,94,0.10), rgba(255,255,255,0.92));
# # #         border: 1px solid rgba(34,197,94,0.22);
# # #         border-left: 4px solid var(--gr);
# # #         border-radius: var(--rl);
# # #         padding: 14px 16px;
# # #         color: var(--t);
# # #         margin: 8px 0 14px 0;
# # #         box-shadow: var(--sh);
# # #     }
# # #     .audit-banner.warn {
# # #         background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(255,255,255,0.92));
# # #         border-color: rgba(245,158,11,0.24);
# # #         border-left-color: var(--am);
# # #     }
# # #     .audit-title {
# # #         font-size: 13px;
# # #         font-weight: 900;
# # #         margin-bottom: 5px;
# # #     }
# # #     .audit-body {
# # #         font-size: 12px;
# # #         color: var(--t2);
# # #         line-height: 1.5;
# # #     }
# # #     .audit-grid {
# # #         display: grid;
# # #         grid-template-columns: repeat(4, minmax(0, 1fr));
# # #         gap: 10px;
# # #         margin: 10px 0 14px 0;
# # #     }
# # #     .audit-card {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: var(--rl);
# # #         padding: 13px 14px;
# # #         min-height: 92px;
# # #         box-shadow: var(--sh);
# # #     }
# # #     .audit-label {
# # #         color: var(--t3);
# # #         font-size: 9px;
# # #         font-weight: 900;
# # #         letter-spacing: 1px;
# # #         text-transform: uppercase;
# # #         margin-bottom: 7px;
# # #     }
# # #     .audit-value {
# # #         color: var(--t);
# # #         font-size: 18px;
# # #         line-height: 1.1;
# # #         font-weight: 900;
# # #     }
# # #     .audit-note {
# # #         color: var(--t2);
# # #         font-size: 11px;
# # #         line-height: 1.35;
# # #         margin-top: 7px;
# # #     }
# # #     div[role="radiogroup"] {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl);
# # #         border-radius: 14px;
# # #         padding: 5px;
# # #         display: inline-flex;
# # #         gap: 4px;
# # #         box-shadow: var(--sh);
# # #         margin: 4px 0 12px 0;
# # #     }
# # #     div[role="radiogroup"] label {
# # #         border-radius: 10px !important;
# # #         padding: 6px 13px !important;
# # #         min-height: 34px !important;
# # #         transition: all var(--tr);
# # #     }
# # #     div[role="radiogroup"] label:has(input:checked) {
# # #         background: var(--ob) !important;
# # #         border: 1px solid var(--obr) !important;
# # #         color: var(--or) !important;
# # #         font-weight: 850 !important;
# # #     }
# # #     div[role="radiogroup"] label span {
# # #         font-size: 12px !important;
# # #         font-weight: 750 !important;
# # #     }
# # #     div[data-testid="stButton"] button {
# # #         background: var(--or);
# # #         color: #fff;
# # #         border: none;
# # #         border-radius: var(--r);
# # #         font-weight: 800;
# # #         box-shadow: 0 2px 8px rgba(244,123,37,0.2);
# # #         transition: all var(--tr);
# # #     }
# # #     div[data-testid="stButton"] button:hover {
# # #         background: var(--odk);
# # #         color: #fff;
# # #         border: none;
# # #         transform: translateY(-1px);
# # #     }
# # #     .stTabs [data-baseweb="tab-list"] {
# # #         gap: 4px;
# # #         border-bottom: 1px solid var(--bl);
# # #     }
# # #     .stTabs [data-baseweb="tab"] {
# # #         border-radius: 9px 9px 0 0;
# # #         color: var(--t2);
# # #         font-weight: 700;
# # #     }
# # #     .stTabs [aria-selected="true"] {
# # #         background: var(--ob);
# # #         color: var(--or) !important;
# # #         border: 1px solid var(--obr);
# # #         border-bottom-color: transparent;
# # #     }
# # #     .stSelectbox>div>div, .stTextInput>div>div, .stTextArea>div>div {
# # #         background: var(--s2) !important;
# # #         border: 1px solid var(--bl) !important;
# # #         border-radius: var(--r) !important;
# # #         font-size: 13px !important;
# # #         color: var(--t) !important;
# # #     }
# # #     [data-testid="stExpander"] {
# # #         background: var(--sf);
# # #         border: 1px solid var(--bl) !important;
# # #         border-radius: var(--rl) !important;
# # #         box-shadow: var(--sh);
# # #     }
# # #     ::-webkit-scrollbar { width: 4px; height: 4px; }
# # #     ::-webkit-scrollbar-track { background: transparent; }
# # #     ::-webkit-scrollbar-thumb { background: var(--bl); border-radius: 2px; }
# # #     </style>
# # #     """,
# # #     unsafe_allow_html=True,
# # # )


# # # def utc_now() -> str:
# # #     return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# # # def safe_request(
# # #     url: str,
# # #     *,
# # #     method: str = "GET",
# # #     headers: Optional[Dict[str, str]] = None,
# # #     params: Optional[Dict[str, Any]] = None,
# # #     json_body: Optional[Dict[str, Any]] = None,
# # #     timeout: int = 30,
# # # ) -> Tuple[bool, Any, str]:
# # #     try:
# # #         if method.upper() == "POST":
# # #             response = requests.post(url, headers=headers, params=params, json=json_body, timeout=timeout)
# # #         else:
# # #             response = requests.get(url, headers=headers, params=params, timeout=timeout)
# # #         response.raise_for_status()
# # #         try:
# # #             return True, response.json(), "success"
# # #         except ValueError:
# # #             return True, response.text, "success"
# # #     except requests.RequestException as exc:
# # #         return False, None, str(exc)


# # # def confidence_class(confidence: str) -> str:
# # #     lookup = {"High": "pill-high", "Medium": "pill-medium", "Low": "pill-low"}
# # #     return lookup.get(confidence, "")


# # # def risk_band(score: float) -> str:
# # #     if score >= 8:
# # #         return "High"
# # #     if score >= 5:
# # #         return "Medium"
# # #     return "Low"


# # # def source_confidence(publisher: str) -> str:
# # #     high = ["reuters", "associated press", "ap news", "bloomberg", "sec", "pr newswire"]
# # #     medium = ["cnbc", "yahoo", "nasdaq", "marketwatch", "forbes", "investing.com", "retail dive"]
# # #     p = (publisher or "").lower()
# # #     if any(name in p for name in high):
# # #         return "High"
# # #     if any(name in p for name in medium):
# # #         return "Medium"
# # #     return "Medium" if publisher else "Low"


# # # def count_raw_records(raw_payload: Any, items: Optional[List[Dict[str, Any]]] = None) -> int:
# # #     if items:
# # #         return len(items)
# # #     if raw_payload is None:
# # #         return 0
# # #     if isinstance(raw_payload, list):
# # #         return len(raw_payload)
# # #     if isinstance(raw_payload, dict):
# # #         if isinstance(raw_payload.get("features"), list):
# # #             return len(raw_payload["features"])
# # #         if isinstance(raw_payload.get("results"), list):
# # #             return len(raw_payload["results"])
# # #         series = raw_payload.get("Results", {}).get("series") if isinstance(raw_payload.get("Results"), dict) else None
# # #         if isinstance(series, list):
# # #             return sum(len(s.get("data", [])) for s in series if isinstance(s, dict))
# # #         nested_counts = [
# # #             count_raw_records(value)
# # #             for value in raw_payload.values()
# # #             if isinstance(value, (dict, list))
# # #         ]
# # #         return sum(nested_counts) if nested_counts else 1
# # #     return 1


# # # def request_summary(source_key: str, run_config: Dict[str, Any]) -> str:
# # #     if source_key == "gnews":
# # #         keywords = run_config.get("news_keywords", [])
# # #         return (
# # #             f"{len(keywords)} keyword(s), country={run_config.get('country', '')}, "
# # #             f"language={run_config.get('language', '')}, period={run_config.get('gnews_period', '')}, "
# # #             f"max_results={run_config.get('max_news', '')}"
# # #         )
# # #     if source_key == "bls":
# # #         return "series=" + ", ".join(BLS_CPI_SERIES.values())
# # #     if source_key == "fda":
# # #         return f"search={run_config.get('fda_query', '')}; limit={run_config.get('fda_limit', '')}"
# # #     if source_key == "weather":
# # #         return f"area={run_config.get('weather_area', '')}; limit={run_config.get('weather_limit', '')}; active NOAA alerts"
# # #     if source_key == "apify":
# # #         return (
# # #             f"enabled={run_config.get('use_apify', False)}, token_present={run_config.get('apify_token_present', False)}, "
# # #             f"run_mode={run_config.get('apify_run_mode', 'Skip Apify')}, confirmed={run_config.get('apify_live_confirm', False)}, "
# # #             f"geo={run_config.get('apify_geo', '')}, time_range={APIFY_SAFE_TIME_RANGE}, "
# # #             f"max_keywords={run_config.get('apify_max_keywords', APIFY_HARD_KEYWORD_LIMIT)}"
# # #         )
# # #     return ""


# # # def build_collector_evidence(results: Dict[str, Dict[str, Any]], run_config: Dict[str, Any]) -> List[Dict[str, Any]]:
# # #     records = []
# # #     enabled_sources = run_config.get("enabled_sources", {})
# # #     for source_key in SOURCE_ORDER:
# # #         result = results.get(source_key, {})
# # #         status = result.get("status", "disabled" if not enabled_sources.get(source_key, False) else "not_run")
# # #         normalized_rows = len(result.get("rows", []) or [])
# # #         raw_records = count_raw_records(result.get("raw"), result.get("items"))
# # #         live_request = status in {"success", "failed", "empty"} or (source_key == "apify" and result.get("raw") is not None)
# # #         mock_used = bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
# # #         records.append(
# # #             {
# # #                 "source": SOURCE_LABELS[source_key],
# # #                 "status": status,
# # #                 "endpoint_or_actor": SOURCE_ENDPOINTS[source_key],
# # #                 "request_scope": request_summary(source_key, run_config),
# # #                 "raw_records_pulled": raw_records,
# # #                 "normalized_feature_rows": normalized_rows,
# # #                 "live_request_made": "Yes" if live_request else "No",
# # #                 "used_in_llm_payload": "Yes" if normalized_rows > 0 else "No",
# # #                 "mock_data_used": "Yes" if mock_used else "No",
# # #                 "analysis_method": ANALYSIS_METHODS[source_key],
# # #                 "error_or_note": result.get("error", "") or "",
# # #             }
# # #         )
# # #     return records


# # # def build_llm_payload(feature_df: pd.DataFrame, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
# # #     return {
# # #         "features": feature_df.to_dict(orient="records")[:12],
# # #         "articles": articles[:8],
# # #     }


# # # def llm_system_prompt() -> str:
# # #     return (
# # #         "You are a retail market intelligence analyst. Ground your answer only in the supplied signals. "
# # #         "Write concise executive guidance for buyers, category managers, demand planners, and supply-chain teams."
# # #     )


# # # def llm_user_prompt(retailer: str, region: str, payload: Dict[str, Any]) -> str:
# # #     return f"""
# # #     Retailer: {retailer}
# # #     Region: {region}

# # #     Signal payload:
# # #     {json.dumps(payload, indent=2, default=str)[:9000]}

# # #     Produce:
# # #     1. Executive summary in 2-3 sentences
# # #     2. Top 3 insights, and for each one cite signal_area, source, risk_score, and score_reason
# # #     3. Forecasting relevance, clearly stating how the signal can become a feature
# # #     4. Recommended buyer/category/demand-planning actions
# # #     5. Confidence and limitations

# # #     Rules:
# # #     - Ground every claim only in the supplied payload.
# # #     - Do not invent internal sales, POS, inventory, margin, or category-performance facts.
# # #     - If a signal is missing, say it is missing instead of estimating it.
# # #     - Explain why each score matters; do not only repeat the number.
# # #     """


# # # def payload_hash(payload: Dict[str, Any]) -> str:
# # #     return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# # # def build_base_llm_audit(
# # #     feature_df: pd.DataFrame,
# # #     articles: List[Dict[str, Any]],
# # #     retailer: str,
# # #     region: str,
# # #     model: str,
# # # ) -> Dict[str, Any]:
# # #     payload = build_llm_payload(feature_df, articles)
# # #     return {
# # #         "provider": "NVIDIA",
# # #         "model": model,
# # #         "sent_to_llm": False,
# # #         "brief_source": "not_generated",
# # #         "fallback_used": False,
# # #         "fallback_reason": "",
# # #         "mock_data_used": False,
# # #         "feature_rows_available": int(len(feature_df)),
# # #         "feature_rows_sent": int(len(payload["features"])),
# # #         "articles_available": int(len(articles)),
# # #         "articles_sent": int(len(payload["articles"])),
# # #         "payload_hash_sha256": payload_hash(payload),
# # #         "system_prompt": llm_system_prompt(),
# # #         "user_prompt": llm_user_prompt(retailer, region, payload),
# # #         "payload": payload,
# # #         "analysis_contract": "The brief must be grounded only in the collected feature rows and article records shown in this audit view.",
# # #     }


# # # def any_mock_used(results: Dict[str, Dict[str, Any]], llm_audit: Dict[str, Any]) -> bool:
# # #     collector_mock = any(
# # #         bool(result.get("mock_used", False) or result.get("meta", {}).get("mock_used", False))
# # #         for result in results.values()
# # #     )
# # #     return collector_mock or bool(llm_audit.get("mock_data_used", False))


# # # def render_audit_banner(mock_used: bool, brief_source: str) -> None:
# # #     banner_class = "audit-banner warn" if mock_used else "audit-banner"
# # #     title = "Mock Data Detected" if mock_used else "No Mock Data Used In This Run"
# # #     body = (
# # #         "At least one collector or analysis step is marked as using mock data. Review the evidence table below."
# # #         if mock_used
# # #         else f"Every feature row shown below comes from the collector outputs for this run. Brief source: {brief_source}."
# # #     )
# # #     st.markdown(
# # #         f"<div class='{banner_class}'><div class='audit-title'>{escape(title)}</div><div class='audit-body'>{escape(body)}</div></div>",
# # #         unsafe_allow_html=True,
# # #     )


# # # def render_audit_card(label: str, value: str, note: str) -> None:
# # #     st.markdown(
# # #         "<div class='audit-card'>"
# # #         f"<div class='audit-label'>{escape(label)}</div>"
# # #         f"<div class='audit-value'>{escape(value)}</div>"
# # #         f"<div class='audit-note'>{escape(note)}</div>"
# # #         "</div>",
# # #         unsafe_allow_html=True,
# # #     )


# # # def validate_nvidia(api_key: str, model: str) -> Tuple[bool, str]:
# # #     if not api_key:
# # #         return False, "NVIDIA key not provided. Brief generation will use a local fallback."
# # #     prompt = "Return exactly: connected"
# # #     ok, data, msg = safe_request(
# # #         NVIDIA_CHAT_URL,
# # #         method="POST",
# # #         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
# # #         json_body={
# # #             "model": model,
# # #             "messages": [{"role": "user", "content": prompt}],
# # #             "temperature": 0,
# # #             "max_tokens": 8,
# # #         },
# # #         timeout=30,
# # #     )
# # #     if not ok:
# # #         return False, msg
# # #     content = data.get("choices", [{}])[0].get("message", {}).get("content")
# # #     content_text = str(content or "").strip()
# # #     return True, f"Connected. Model responded: {content_text or 'ok'}"


# # # def validate_apify(token: str) -> Tuple[bool, str]:
# # #     if not token:
# # #         return False, "Apify token not provided. Apify collectors will be skipped."
# # #     try:
# # #         from apify_client import ApifyClient
# # #     except ImportError:
# # #         return False, "apify-client is not installed. Install requirements before running Apify collectors."
# # #     try:
# # #         client = ApifyClient(token)
# # #         user = client.user().get()
# # #         username = user.get("username") or user.get("email") or "Apify user"
# # #         return True, f"Connected as {username}."
# # #     except Exception as exc:  # pragma: no cover - depends on live Apify service
# # #         return False, str(exc)


# # # def build_cpi_signal(data: Dict[str, Any], label: str, series_id: str, retailer: str) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
# # #     series = data.get("Results", {}).get("series", [])
# # #     rows = []
# # #     for item in (series[0].get("data", []) if series else [])[:24]:
# # #         period = item.get("period", "")
# # #         if period == "M13":
# # #             continue
# # #         try:
# # #             cpi_value = float(item["value"])
# # #         except (TypeError, ValueError, KeyError):
# # #             continue
# # #         rows.append(
# # #             {
# # #                 "category": label,
# # #                 "series_id": series_id,
# # #                 "year": int(item["year"]),
# # #                 "period": period,
# # #                 "month": item.get("periodName", ""),
# # #                 "cpi_value": cpi_value,
# # #             }
# # #         )
# # #     df = pd.DataFrame(rows)
# # #     if df.empty:
# # #         return None, df
# # #     df = df.sort_values(["year", "period"]).reset_index(drop=True)
# # #     df["cpi_mom_change_pct"] = df["cpi_value"].pct_change() * 100
# # #     df["cpi_yoy_change_pct"] = df["cpi_value"].pct_change(12) * 100
# # #     latest = df.iloc[-1].to_dict()
# # #     mom = latest.get("cpi_mom_change_pct")
# # #     yoy = latest.get("cpi_yoy_change_pct")
# # #     score = 4.0
# # #     if pd.notna(mom):
# # #         score = min(10.0, max(1.0, 4.0 + float(mom) * 5.0))
# # #     if pd.notna(yoy) and yoy > 4:
# # #         score = min(10.0, score + 1.0)
# # #     mom_label = "unavailable" if pd.isna(mom) else f"{float(mom):.2f}% MoM"
# # #     yoy_label = "unavailable" if pd.isna(yoy) else f"{float(yoy):.2f}% YoY"
# # #     signal_name = "inflation_pressure_score" if label == "Headline CPI" else f"{label.lower().replace(' ', '_')}_cpi_pressure_score"
# # #     signal = {
# # #         "date": f"{int(latest['year'])}-{str(latest['period']).replace('M', '').zfill(2)}",
# # #         "retailer": retailer,
# # #         "region": "US",
# # #         "region_scope": "national",
# # #         "source": "BLS CPI",
# # #         "signal_area": "Inflation" if label == "Headline CPI" else "Category CPI",
# # #         "signal_name": signal_name,
# # #         "signal_value": round(float(score), 2),
# # #         "risk_score": round(float(score), 2),
# # #         "confidence": "High",
# # #         "score_reason": f"{label} CPI latest value {latest['cpi_value']}; change is {mom_label} and {yoy_label}. Score rises with monthly inflation pressure and elevated YoY inflation.",
# # #         "business_impact": f"{label} inflation can affect price sensitivity, category demand, and basket mix.",
# # #         "recommended_action": "Use category CPI as an external regressor and validate against internal category sales.",
# # #         "raw_reference": f"{label}: CPI {latest['cpi_value']}",
# # #     }
# # #     return signal, df


# # # def collect_bls_cpi(bls_key: str = "", retailer: str = "Retailer") -> Dict[str, Any]:
# # #     payload: Dict[str, Any] = {"seriesid": list(BLS_CPI_SERIES.values())}
# # #     if bls_key:
# # #         payload["registrationkey"] = bls_key
# # #     signals = []
# # #     tables = []
# # #     raw = {}
# # #     errors = []
# # #     ok, data, msg = safe_request(
# # #         "https://api.bls.gov/publicAPI/v2/timeseries/data/",
# # #         method="POST",
# # #         headers={"Content-Type": "application/json"},
# # #         json_body=payload,
# # #         timeout=30,
# # #     )
# # #     if not ok:
# # #         return {"status": "failed", "source": "BLS CPI", "error": msg, "raw": None, "rows": []}
# # #     if data.get("status") != "REQUEST_SUCCEEDED":
# # #         return {
# # #             "status": "failed",
# # #             "source": "BLS CPI",
# # #             "error": "; ".join(data.get("message", [])) or "BLS request was not processed.",
# # #             "raw": data,
# # #             "rows": [],
# # #         }
# # #     series_by_id = {
# # #         series.get("seriesID"): series
# # #         for series in data.get("Results", {}).get("series", [])
# # #     }
# # #     for label, series_id in BLS_CPI_SERIES.items():
# # #         series_payload = {"Results": {"series": [series_by_id.get(series_id, {})]}}
# # #         raw[label] = series_payload
# # #         signal, df = build_cpi_signal(series_payload, label, series_id, retailer)
# # #         if signal:
# # #             signals.append(signal)
# # #         if not df.empty:
# # #             tables.append(df)
# # #     if not signals:
# # #         return {"status": "failed", "source": "BLS CPI", "error": "; ".join(errors) or "No CPI rows returned.", "raw": raw, "rows": []}
# # #     table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
# # #     return {"status": "success", "source": "BLS CPI", "error": "; ".join(errors), "raw": raw, "rows": signals, "table": table}


# # # def classify_recall(reason: str) -> Tuple[str, float]:
# # #     text = (reason or "").lower()
# # #     if any(word in text for word in ["lead", "heavy metal", "salmonella", "listeria", "e. coli", "contamination"]):
# # #         return "high_safety_risk", 8.0
# # #     if any(word in text for word in ["undeclared", "allergen", "milk", "peanut", "soy", "tree nut"]):
# # #         return "allergen_risk", 6.5
# # #     if any(word in text for word in ["mislabel", "label"]):
# # #         return "labeling_risk", 4.5
# # #     return "general_recall_risk", 5.0


# # # def extract_upcs(text: str) -> List[str]:
# # #     candidates = re.findall(r"(?:UPC(?:\s*Code)?[:\s]*)?(\d(?:[\s-]?\d){7,13})", text or "", flags=re.IGNORECASE)
# # #     cleaned = []
# # #     for candidate in candidates:
# # #         digits = re.sub(r"\D", "", candidate)
# # #         if 8 <= len(digits) <= 14 and digits not in cleaned:
# # #             cleaned.append(digits)
# # #     return cleaned


# # # def adjust_recall_score(base_score: float, classification: str, status: str) -> float:
# # #     score = base_score
# # #     class_text = (classification or "").lower()
# # #     status_text = (status or "").lower()
# # #     if "class i" in class_text:
# # #         score += 1.5
# # #     elif "class ii" in class_text:
# # #         score += 0.8
# # #     if "ongoing" in status_text:
# # #         score += 1.0
# # #     elif "terminated" in status_text:
# # #         score -= 1.0
# # #     return round(min(10.0, max(1.0, score)), 2)


# # # def collect_fda_recalls(query: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
# # #     params = {"search": query, "limit": limit}
# # #     ok, data, msg = safe_request("https://api.fda.gov/food/enforcement.json", params=params, timeout=30)
# # #     if not ok:
# # #         return {"status": "failed", "source": "openFDA", "error": msg, "raw": None, "rows": [], "items": []}
# # #     results = data.get("results", [])
# # #     rows = []
# # #     items = []
# # #     for item in results:
# # #         risk_type, base_score = classify_recall(item.get("reason_for_recall", ""))
# # #         product = item.get("product_description", "Unknown product")
# # #         state = item.get("state", "US")
# # #         classification = item.get("classification", "")
# # #         status = item.get("status", "")
# # #         score = adjust_recall_score(base_score, classification, status)
# # #         upcs = extract_upcs(f"{product} {item.get('code_info', '')}")
# # #         items.append(
# # #             {
# # #                 "product": product,
# # #                 "reason": item.get("reason_for_recall", ""),
# # #                 "state": state,
# # #                 "classification": classification,
# # #                 "status": status,
# # #                 "recall_date": item.get("recall_initiation_date", ""),
# # #                 "distribution_pattern": item.get("distribution_pattern", ""),
# # #                 "recalling_firm": item.get("recalling_firm", ""),
# # #                 "upcs": ", ".join(upcs) if upcs else "",
# # #                 "sku_match_status": "unknown",
# # #                 "risk_type": risk_type,
# # #                 "risk_score": score,
# # #             }
# # #         )
# # #     aggregate_score = max([x["risk_score"] for x in items], default=1.0)
# # #     ongoing_count = sum(1 for x in items if str(x.get("status", "")).lower() == "ongoing")
# # #     class_i_count = sum(1 for x in items if "class i" in str(x.get("classification", "")).lower())
# # #     states = sorted({str(x.get("state", "")).strip() for x in items if str(x.get("state", "")).strip()})
# # #     upc_count = sum(1 for x in items if x.get("upcs"))
# # #     top_risk_type = max(items, key=lambda x: x["risk_score"]).get("risk_type", "none") if items else "none"
# # #     signal = {
# # #         "date": utc_now()[:10],
# # #         "retailer": retailer,
# # #         "region": "US",
# # #         "region_scope": "national_with_state_records",
# # #         "source": "openFDA",
# # #         "signal_area": "Product Recalls",
# # #         "signal_name": "recall_risk_score",
# # #         "signal_value": len(items),
# # #         "risk_score": round(aggregate_score, 2),
# # #         "confidence": "High",
# # #         "score_reason": f"Score uses highest adjusted recall severity. Inputs: {len(items)} records, {ongoing_count} ongoing, {class_i_count} Class I, {upc_count} records with UPCs, top risk type {top_risk_type}.",
# # #         "affected_states": ", ".join(states[:8]) if states else "Unknown",
# # #         "ongoing_count": ongoing_count,
# # #         "class_i_count": class_i_count,
# # #         "upc_record_count": upc_count,
# # #         "sku_match_status": "unknown",
# # #         "affected_category": "Food / snacks / candy / beverages",
# # #         "business_impact": "Food and beverage recalls can trigger inventory withdrawal, substitution demand, and safety review.",
# # #         "recommended_action": f"Prioritize ongoing and Class I recalls, then match UPCs against {retailer} inventory before store-level action.",
# # #         "raw_reference": f"{len(items)} recall records; {ongoing_count} ongoing; {class_i_count} Class I",
# # #     }
# # #     rows.append(signal)
# # #     return {"status": "success", "source": "openFDA", "error": "", "raw": data, "rows": rows, "items": items}


# # # def normalize_weather_area(area: str) -> str:
# # #     candidate = re.sub(r"[^A-Za-z]", "", area or "").upper()
# # #     if len(candidate) == 2:
# # #         return candidate
# # #     return "TX"


# # # def weather_alert_weight(severity: str, urgency: str, certainty: str) -> float:
# # #     severity_score = {
# # #         "extreme": 5.0,
# # #         "severe": 3.0,
# # #         "moderate": 2.0,
# # #         "minor": 1.0,
# # #         "unknown": 1.0,
# # #     }.get(str(severity or "").lower(), 1.0)
# # #     urgency_bonus = {
# # #         "immediate": 1.5,
# # #         "expected": 0.75,
# # #     }.get(str(urgency or "").lower(), 0.0)
# # #     certainty_bonus = {
# # #         "observed": 0.5,
# # #         "likely": 0.5,
# # #     }.get(str(certainty or "").lower(), 0.0)
# # #     return severity_score + urgency_bonus + certainty_bonus


# # # def collect_weather_alerts(area: str, limit: int, retailer: str = "Retailer") -> Dict[str, Any]:
# # #     state_area = normalize_weather_area(area)
# # #     params = {"area": state_area}
# # #     ok, data, msg = safe_request(
# # #         "https://api.weather.gov/alerts/active",
# # #         headers={"User-Agent": "MarketIntelligenceWorkbench/1.0", "Accept": "application/geo+json"},
# # #         params=params,
# # #         timeout=30,
# # #     )
# # #     if not ok:
# # #         return {"status": "failed", "source": "NOAA Weather Alerts", "error": msg, "raw": None, "rows": [], "items": []}
# # #     features = data.get("features", []) if isinstance(data, dict) else []
# # #     selected_alerts = features[: max(1, int(limit))]
# # #     items = []
# # #     score_components = []
# # #     severe_count = 0
# # #     extreme_count = 0
# # #     for alert in selected_alerts:
# # #         props = alert.get("properties", {}) if isinstance(alert, dict) else {}
# # #         severity = props.get("severity", "Unknown")
# # #         urgency = props.get("urgency", "Unknown")
# # #         certainty = props.get("certainty", "Unknown")
# # #         component = weather_alert_weight(severity, urgency, certainty)
# # #         score_components.append(component)
# # #         severity_text = str(severity or "").lower()
# # #         if severity_text == "extreme":
# # #             extreme_count += 1
# # #         if severity_text in {"severe", "extreme"}:
# # #             severe_count += 1
# # #         items.append(
# # #             {
# # #                 "event": props.get("event", ""),
# # #                 "severity": severity,
# # #                 "urgency": urgency,
# # #                 "certainty": certainty,
# # #                 "headline": props.get("headline", ""),
# # #                 "area_desc": props.get("areaDesc", ""),
# # #                 "effective": props.get("effective", ""),
# # #                 "expires": props.get("expires", ""),
# # #                 "instruction": props.get("instruction", ""),
# # #                 "risk_component": round(component, 2),
# # #             }
# # #         )
# # #     risk_score = round(min(10.0, sum(score_components)), 2) if items else 0.0
# # #     if items:
# # #         score_reason = (
# # #             f"Score sums weighted active NOAA alerts for {state_area}, capped at 10. "
# # #             f"Inputs: {len(items)} alert(s), {severe_count} severe/extreme, {extreme_count} extreme; "
# # #             f"severity/urgency/certainty components total {sum(score_components):.2f}."
# # #         )
# # #         raw_reference = f"{len(items)} active NOAA alert(s); top event: {items[0].get('event') or 'Unknown'}"
# # #         recommended_action = "Check affected counties against store and DC routes; use alert severity as a short-horizon disruption and emergency-demand feature."
# # #     else:
# # #         score_reason = f"NOAA returned 0 active alerts for {state_area}. Score is 0 because no current weather disruption signal is present."
# # #         raw_reference = "0 active NOAA alerts"
# # #         recommended_action = "Keep weather feature at baseline for this state, then refresh before short-horizon replenishment decisions."
# # #     signal = {
# # #         "date": utc_now()[:10],
# # #         "retailer": retailer,
# # #         "region": state_area,
# # #         "region_scope": "state_weather_alerts",
# # #         "source": "NOAA Weather Alerts",
# # #         "signal_area": "Weather Risk",
# # #         "signal_name": "supply_chain_weather_risk_score",
# # #         "signal_value": len(items),
# # #         "risk_score": risk_score,
# # #         "confidence": "High",
# # #         "score_reason": score_reason,
# # #         "alert_count": len(items),
# # #         "severe_or_extreme_count": severe_count,
# # #         "extreme_count": extreme_count,
# # #         "business_impact": "Active weather alerts can disrupt store traffic, DC-to-store routes, staffing, replenishment timing, and emergency-demand categories.",
# # #         "recommended_action": recommended_action,
# # #         "raw_reference": raw_reference,
# # #     }
# # #     return {"status": "success", "source": "NOAA Weather Alerts", "error": "", "raw": data, "rows": [signal], "items": items}


# # # def gnews_package_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
# # #     try:
# # #         from gnews import GNews
# # #     except ImportError:
# # #         return None
# # #     google_news = GNews(language=language, country=country, period=period, max_results=max_results)
# # #     return google_news.get_news(keyword)


# # # def google_news_rss_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> List[Dict[str, Any]]:
# # #     # Google News RSS supports a "when:" query operator. GNews package is preferred when installed.
# # #     query = quote_plus(f"{keyword} when:{period}")
# # #     country_code = country.upper()
# # #     lang_code = language.lower()
# # #     url = f"https://news.google.com/rss/search?q={query}&hl={lang_code}-{country_code}&gl={country_code}&ceid={country_code}:{lang_code}"
# # #     response = requests.get(url, timeout=30)
# # #     response.raise_for_status()
# # #     root = ET.fromstring(response.content)
# # #     articles = []
# # #     for item in root.findall(".//item")[:max_results]:
# # #         source_node = item.find("source")
# # #         articles.append(
# # #             {
# # #                 "title": item.findtext("title", default=""),
# # #                 "description": item.findtext("description", default=""),
# # #                 "published date": item.findtext("pubDate", default=""),
# # #                 "url": item.findtext("link", default=""),
# # #                 "publisher": source_node.text if source_node is not None else "",
# # #             }
# # #         )
# # #     return articles


# # # def clean_news_description(description: str) -> str:
# # #     text = re.sub(r"<[^>]+>", " ", description or "")
# # #     text = unescape(text)
# # #     text = re.sub(r"\s+", " ", text).strip()
# # #     return text


# # # def article_days_old(published_date: str) -> Optional[int]:
# # #     if not published_date:
# # #         return None
# # #     try:
# # #         published = parsedate_to_datetime(published_date)
# # #         if published.tzinfo is None:
# # #             published = published.replace(tzinfo=timezone.utc)
# # #         return max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
# # #     except (TypeError, ValueError):
# # #         return None


# # # def classify_news_title(title: str, description: str) -> Tuple[str, float, str]:
# # #     text = f"{title} {description}".lower()
# # #     if any(word in text for word in ["recall", "contamination", "lawsuit", "closure", "closing", "tariff", "warning"]):
# # #         return "risk_event", 7.0, "negative"
# # #     if any(word in text for word in ["inflation", "prices", "freight", "cost", "margin"]):
# # #         return "price_pressure", 6.0, "negative"
# # #     if any(word in text for word in ["deal", "sale", "promotion", "coupon", "holiday", "seasonal"]):
# # #         return "demand_opportunity", 6.5, "positive"
# # #     if any(word in text for word in ["earnings", "forecast", "guidance", "outlook"]):
# # #         return "financial_update", 5.5, "neutral"
# # #     return "general_market_news", 3.5, "neutral"


# # # def collect_gnews(keywords: List[str], country: str, language: str, period: str, max_results: int, retailer: str = "Retailer") -> Dict[str, Any]:
# # #     all_articles = []
# # #     errors = []
# # #     per_keyword_limit = max(1, int(max_results / max(1, len(keywords))))
# # #     for keyword in keywords:
# # #         try:
# # #             articles = gnews_package_collect(keyword, country, language, period, per_keyword_limit)
# # #             if articles is None:
# # #                 articles = google_news_rss_collect(keyword, country, language, period, per_keyword_limit)
# # #             for article in articles or []:
# # #                 description = clean_news_description(article.get("description", ""))
# # #                 event_type, score, sentiment = classify_news_title(article.get("title", ""), description)
# # #                 publisher = article.get("publisher", "")
# # #                 if isinstance(publisher, dict):
# # #                     publisher = publisher.get("title") or publisher.get("href") or ""
# # #                 published_date = article.get("published date") or article.get("published_date", "")
# # #                 days_old = article_days_old(published_date)
# # #                 if days_old is not None and days_old > 30:
# # #                     score = max(1.0, score - 1.0)
# # #                 all_articles.append(
# # #                     {
# # #                         "keyword": keyword,
# # #                         "title": article.get("title", ""),
# # #                         "description": description,
# # #                         "published_date": published_date,
# # #                         "days_old": days_old,
# # #                         "publisher": publisher,
# # #                         "url": article.get("url", ""),
# # #                         "source_tier": source_confidence(str(publisher)),
# # #                         "event_type": event_type,
# # #                         "sentiment": sentiment,
# # #                         "risk_score": score,
# # #                         "confidence": source_confidence(str(publisher)),
# # #                     }
# # #                 )
# # #         except Exception as exc:
# # #             errors.append(f"{keyword}: {exc}")

# # #     deduped = []
# # #     seen = set()
# # #     for article in all_articles:
# # #         key = article["url"] or article["title"]
# # #         if key and key not in seen:
# # #             seen.add(key)
# # #             deduped.append(article)

# # #     if not deduped:
# # #         return {
# # #             "status": "failed" if errors else "empty",
# # #             "source": "GNews",
# # #             "error": "; ".join(errors) if errors else "No meaningful articles returned for selected keywords.",
# # #             "raw": [],
# # #             "rows": [],
# # #             "items": [],
# # #         }

# # #     score = round(float(pd.Series([a["risk_score"] for a in deduped]).mean()), 2) if deduped else 1.0
# # #     negative_count = sum(1 for article in deduped if article.get("sentiment") == "negative")
# # #     high_conf_count = sum(1 for article in deduped if article.get("confidence") == "High")
# # #     event_counts = pd.Series([article.get("event_type", "unknown") for article in deduped]).value_counts().to_dict()
# # #     signal = {
# # #         "date": utc_now()[:10],
# # #         "retailer": retailer,
# # #         "region": country.upper(),
# # #         "region_scope": "country_news",
# # #         "source": "GNews / Google News RSS",
# # #         "signal_area": "Retail News",
# # #         "signal_name": "news_risk_score",
# # #         "signal_value": len(deduped),
# # #         "risk_score": score,
# # #         "confidence": "Medium",
# # #         "score_reason": f"Average article risk across {len(deduped)} deduped articles; {negative_count} negative articles; {high_conf_count} high-confidence publishers; event mix {event_counts}.",
# # #         "business_impact": "Recent news can reveal competitor moves, pricing pressure, recalls, store changes, and supply-chain risk.",
# # #         "recommended_action": "Review high-risk articles and use NVIDIA classification before executive distribution.",
# # #         "raw_reference": f"{len(deduped)} articles",
# # #     }
# # #     return {"status": "success", "source": "GNews", "error": "; ".join(errors), "raw": deduped, "rows": [signal], "items": deduped}


# # # def get_apify_value(obj: Any, key: str) -> Any:
# # #     if isinstance(obj, dict):
# # #         return obj.get(key)
# # #     if hasattr(obj, key):
# # #         return getattr(obj, key)
# # #     try:
# # #         return obj[key]
# # #     except (TypeError, KeyError, AttributeError):
# # #         return None


# # # def collect_apify_trends(
# # #     token: str,
# # #     keywords: List[str],
# # #     geo: str,
# # #     time_range: str,
# # #     retailer: str = "Retailer",
# # #     max_keywords: int = APIFY_HARD_KEYWORD_LIMIT,
# # # ) -> Dict[str, Any]:
# # #     if not token:
# # #         return {"status": "skipped", "source": "Apify Trends", "error": "No Apify token provided.", "raw": None, "rows": [], "items": []}
# # #     try:
# # #         from apify_client import ApifyClient
# # #     except ImportError:
# # #         return {"status": "failed", "source": "Apify Trends", "error": "apify-client is not installed.", "raw": None, "rows": [], "items": []}
# # #     try:
# # #         client = ApifyClient(token)
# # #         safe_max_keywords = min(max(1, int(max_keywords)), APIFY_HARD_KEYWORD_LIMIT)
# # #         safe_time_range = APIFY_SAFE_TIME_RANGE
# # #         selected_keywords = [kw for kw in keywords if kw][:safe_max_keywords]
# # #         if not selected_keywords:
# # #             return {"status": "skipped", "source": "Apify Trends", "error": "No trend keywords provided.", "raw": None, "rows": [], "items": []}
# # #         run_input = {"geo": geo, "searchTerms": selected_keywords, "timeRange": safe_time_range}
# # #         run = client.actor("apify/google-trends-scraper").call(run_input=run_input)
# # #         dataset_id = get_apify_value(run, "defaultDatasetId") or get_apify_value(run, "default_dataset_id")
# # #         if not dataset_id:
# # #             return {
# # #                 "status": "failed",
# # #                 "source": "Apify Trends",
# # #                 "error": "Apify run completed but no default dataset ID was found.",
# # #                 "raw": run_input,
# # #                 "rows": [],
# # #                 "items": [],
# # #             }
# # #         items = list(client.dataset(dataset_id).iterate_items())
# # #     except Exception as exc:
# # #         return {"status": "failed", "source": "Apify Trends", "error": str(exc), "raw": None, "rows": [], "items": []}

# # #     region_rows = []
# # #     for item in items:
# # #         keyword = item.get("searchTerm")
# # #         for rank, region in enumerate(item.get("interestBySubregion", []) or [], start=1):
# # #             values = region.get("value") or []
# # #             if values:
# # #                 region_rows.append(
# # #                     {
# # #                         "keyword": keyword,
# # #                         "region": region.get("geoName", ""),
# # #                         "interest_score": values[0],
# # #                         "rank": rank,
# # #                     }
# # #                 )
# # #     top_score = max([float(x["interest_score"]) for x in region_rows], default=0.0)
# # #     signal_score = round(min(10.0, max(1.0, top_score / 10.0)), 2) if top_score else 1.0
# # #     signal = {
# # #         "date": utc_now()[:10],
# # #         "retailer": retailer,
# # #         "region": geo,
# # #         "region_scope": "trend_geo",
# # #         "source": "Apify Google Trends",
# # #         "signal_area": "Search Demand",
# # #         "signal_name": "search_demand_score",
# # #         "signal_value": top_score,
# # #         "risk_score": signal_score,
# # #         "confidence": "Medium",
# # #         "score_reason": f"Score is top regional Google Trends interest divided by 10. Run hard-limited to {len(selected_keywords)} keyword(s) over {safe_time_range} to control Apify quota and memory.",
# # #         "business_impact": "Search interest can reveal early demand shifts, promotional interest, and seasonal spikes.",
# # #         "recommended_action": "Compare rising regions against sales, inventory, and competitor promotion calendars.",
# # #         "raw_reference": f"{len(region_rows)} regional trend rows",
# # #     }
# # #     return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [signal], "items": region_rows}


# # # def generate_fallback_brief(feature_df: pd.DataFrame, retailer: str, region: str) -> str:
# # #     if feature_df.empty:
# # #         return (
# # #             "Executive Summary:\n"
# # #             "- No external signals were collected for this run.\n"
# # #             "- Enable at least one source and run the workbench again before using the output for planning.\n\n"
# # #             "Confidence And Limitations:\n"
# # #             "- No score can be explained because no feature rows exist."
# # #         )
# # #     strongest = feature_df.sort_values("risk_score", ascending=False).head(3)
# # #     avg_score = round(float(feature_df["risk_score"].mean()), 2)
# # #     top = strongest.iloc[0]
# # #     sources = ", ".join(sorted({str(src) for src in feature_df["source"].dropna().tolist()}))
# # #     lines = [
# # #         "Executive Summary:",
# # #         f"- {retailer} in {region} has {risk_band(avg_score).lower()} external signal intensity with an average score of {avg_score}/10 across {len(feature_df)} forecast-ready row(s).",
# # #         f"- The strongest current signal is {top['signal_area']} at {float(top['risk_score']):.2f}/10 from {top['source']}.",
# # #         f"- This brief is grounded in collected source output only: {sources}.",
# # #         "",
# # #         "Top Signal Evidence:",
# # #     ]
# # #     for idx, (_, row) in enumerate(strongest.iterrows(), start=1):
# # #         lines.append(
# # #             f"{idx}. {row['signal_area']} scored {float(row['risk_score']):.2f}/10 from {row['source']}. Why: {row.get('score_reason', 'No score reason available.')}"
# # #         )
# # #         lines.append(f"- Business impact: {row.get('business_impact', 'No business impact available.')}")
# # #     lines.extend(
# # #         [
# # #             "",
# # #             "Forecasting Relevance:",
# # #             "- Treat each score as an external regressor candidate, not as a final demand forecast.",
# # #             "- Join these rows to internal POS, category, store, promotion, and inventory data before model training or operational action.",
# # #             "",
# # #             "Recommended Actions:",
# # #         ]
# # #     )
# # #     for idx, (_, row) in enumerate(strongest.iterrows(), start=1):
# # #         lines.append(f"{idx}. {row.get('recommended_action', 'Review this signal with the category owner.')}")
# # #     lines.extend(
# # #         [
# # #             "",
# # #             "Confidence And Limitations:",
# # #             "- Scores are explainable directional signals from public API data, not proof of actual Retailer demand movement.",
# # #             "- Category performance still requires internal sales/POS data; external APIs explain context but do not replace internal performance data.",
# # #         ]
# # #     )
# # #     return "\n".join(lines)


# # # def generate_nvidia_brief(
# # #     api_key: str,
# # #     model: str,
# # #     feature_df: pd.DataFrame,
# # #     articles: List[Dict[str, Any]],
# # #     retailer: str,
# # #     region: str,
# # # ) -> Tuple[str, str, Dict[str, Any]]:
# # #     audit = build_base_llm_audit(feature_df, articles, retailer, region, model)
# # #     if not api_key:
# # #         audit.update(
# # #             {
# # #                 "provider": "Local deterministic fallback",
# # #                 "model": "rule_based_summary",
# # #                 "brief_source": "fallback",
# # #                 "fallback_used": True,
# # #                 "fallback_reason": "No NVIDIA API key provided. No external LLM call was made.",
# # #             }
# # #         )
# # #         return generate_fallback_brief(feature_df, retailer, region), "fallback", audit
# # #     ok, data, msg = safe_request(
# # #         NVIDIA_CHAT_URL,
# # #         method="POST",
# # #         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
# # #         json_body={
# # #             "model": model,
# # #             "messages": [{"role": "system", "content": audit["system_prompt"]}, {"role": "user", "content": audit["user_prompt"]}],
# # #             "temperature": 0.25,
# # #             "max_tokens": 900,
# # #         },
# # #         timeout=60,
# # #     )
# # #     if not ok:
# # #         audit.update(
# # #             {
# # #                 "provider": "Local deterministic fallback",
# # #                 "model": "rule_based_summary",
# # #                 "brief_source": f"fallback: {msg}",
# # #                 "fallback_used": True,
# # #                 "fallback_reason": f"NVIDIA request failed: {msg}",
# # #             }
# # #         )
# # #         return generate_fallback_brief(feature_df, retailer, region), f"fallback: {msg}", audit
# # #     content = data.get("choices", [{}])[0].get("message", {}).get("content")
# # #     content_text = str(content or "").strip()
# # #     if not content_text:
# # #         audit.update(
# # #             {
# # #                 "provider": "Local deterministic fallback",
# # #                 "model": "rule_based_summary",
# # #                 "brief_source": "fallback: empty NVIDIA response",
# # #                 "fallback_used": True,
# # #                 "fallback_reason": "NVIDIA returned an empty response.",
# # #             }
# # #         )
# # #         return generate_fallback_brief(feature_df, retailer, region), "fallback: empty NVIDIA response", audit
# # #     audit.update(
# # #         {
# # #             "provider": "NVIDIA",
# # #             "model": model,
# # #             "sent_to_llm": True,
# # #             "brief_source": "nvidia",
# # #             "fallback_used": False,
# # #             "fallback_reason": "",
# # #             "response_chars": len(content_text),
# # #         }
# # #     )
# # #     return content_text, "nvidia", audit


# # # def render_metric_card(title: str, value: str, note: str, confidence: str = "") -> None:
# # #     pill = f'<span class="pill {confidence_class(confidence)}">{confidence}</span>' if confidence else ""
# # #     html = (
# # #         '<div class="metric-card">'
# # #         f'<div class="metric-label">{title}</div>'
# # #         f'<div class="metric-value">{value}</div>'
# # #         f"{pill}"
# # #         f'<div class="metric-note">{note}</div>'
# # #         "</div>"
# # #     )
# # #     st.markdown(
# # #         html,
# # #         unsafe_allow_html=True,
# # #     )


# # # def compute_composite_scores(feature_df: pd.DataFrame) -> Dict[str, float]:
# # #     if feature_df.empty:
# # #         return {"Market Opportunity": 0.0, "Market Risk": 0.0, "Forecast Impact": 0.0}
# # #     area_scores: Dict[str, float] = {}
# # #     for _, row in feature_df.iterrows():
# # #         if pd.isna(row.get("risk_score")):
# # #             continue
# # #         area = str(row["signal_area"])
# # #         area_scores[area] = max(area_scores.get(area, 0.0), float(row["risk_score"]))
# # #     opportunity = max(
# # #         area_scores.get("Retail News", 0.0),
# # #         area_scores.get("Search Demand", 0.0),
# # #         area_scores.get("Category CPI", 0.0) * 0.7,
# # #     )
# # #     risk = max(
# # #         area_scores.get("Product Recalls", 0.0),
# # #         area_scores.get("Weather Risk", 0.0),
# # #         area_scores.get("Inflation", 0.0),
# # #         area_scores.get("Category CPI", 0.0),
# # #     )
# # #     impact = min(10.0, (opportunity * 0.45) + (risk * 0.45) + (len(feature_df) * 0.25))
# # #     return {
# # #         "Market Opportunity": round(opportunity, 2),
# # #         "Market Risk": round(risk, 2),
# # #         "Forecast Impact": round(impact, 2),
# # #     }


# # # def build_recommended_actions(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
# # #     actions = []
# # #     if feature_df.empty:
# # #         return [{"label": "Setup", "title": "Run signal sources", "body": "Enable at least one source to generate forecast-ready rows."}]
# # #     top_rows = feature_df.sort_values("risk_score", ascending=False).head(3)
# # #     for _, row in top_rows.iterrows():
# # #         actions.append(
# # #             {
# # #                 "label": str(row["signal_area"]),
# # #                 "title": str(row["signal_name"]).replace("_", " ").title(),
# # #                 "body": f"{row['recommended_action']} Reason: {row.get('score_reason', 'No score reason available.')}",
# # #             }
# # #         )
# # #     apify_result = results.get("apify")
# # #     if apify_result and apify_result.get("status") in {"failed", "skipped"}:
# # #         actions.append(
# # #             {
# # #                 "label": "Apify",
# # #                 "title": "Search demand not collected",
# # #                 "body": apify_result.get("error") or "Apify did not return a usable trends signal. Keep MVP on public sources or run one guarded live query.",
# # #             }
# # #         )
# # #     return actions[:4]


# # # def render_action_card(label: str, title: str, body: str) -> None:
# # #     html = (
# # #         '<div class="action-card">'
# # #         f'<div class="action-label">{escape(label)}</div>'
# # #         f'<div class="action-title">{escape(title)}</div>'
# # #         f'<div class="action-body">{escape(body)}</div>'
# # #         "</div>"
# # #     )
# # #     st.markdown(html, unsafe_allow_html=True)


# # # def render_source_tile(name: str, status: str, detail: str) -> None:
# # #     status_class = "pill-high" if status == "Active" else "pill-medium" if status == "Optional" else "pill-low"
# # #     html = (
# # #         '<div class="source-tile">'
# # #         f'<div class="source-name">{escape(name)} <span class="pill {status_class}" style="margin-left:6px;margin-top:0;">{escape(status)}</span></div>'
# # #         f'<div class="source-meta">{escape(detail)}</div>'
# # #         "</div>"
# # #     )
# # #     st.markdown(html, unsafe_allow_html=True)


# # # def render_workflow_strip() -> None:
# # #     steps = [
# # #         ("01", "Collect APIs"),
# # #         ("02", "Clean records"),
# # #         ("03", "Score signals"),
# # #         ("04", "Generate brief"),
# # #         ("05", "Export features"),
# # #     ]
# # #     html = "<div class='workflow'>" + "".join(
# # #         f"<div class='workflow-step'><span>{num}</span>{escape(label)}</div>" for num, label in steps
# # #     ) + "</div>"
# # #     st.markdown(html, unsafe_allow_html=True)


# # # def render_run_monitor(slot: Any, title: str, states: Dict[str, Dict[str, str]], progress_pct: int) -> None:
# # #     cards = []
# # #     for name, info in states.items():
# # #         status = info.get("status", "queued")
# # #         detail = info.get("detail", "")
# # #         status_class = {
# # #             "running": "status-running",
# # #             "success": "status-success",
# # #             "failed": "status-failed",
# # #             "skipped": "status-skipped",
# # #             "queued": "status-queued",
# # #         }.get(status, "status-queued")
# # #         cards.append(
# # #             "<div class='run-status-card'>"
# # #             f"<div class='status-badge {status_class}'>{escape(status)}</div>"
# # #             f"<div class='run-status-name'>{escape(name)}</div>"
# # #             f"<div class='run-status-detail'>{escape(detail)}</div>"
# # #             "</div>"
# # #         )
# # #     html = (
# # #         "<div class='run-monitor'>"
# # #         "<div class='run-monitor-head'>"
# # #         f"<div><div class='run-monitor-sub'>Pipeline Status</div><div class='run-monitor-title'>{escape(title)}</div></div>"
# # #         f"<div class='tbadge'>{int(progress_pct)}%</div>"
# # #         "</div>"
# # #         "<div class='run-progress-track'>"
# # #         f"<div class='run-progress-fill' style='width:{max(0, min(100, int(progress_pct)))}%;'></div>"
# # #         "</div>"
# # #         "<div class='run-status-grid'>"
# # #         + "".join(cards)
# # #         + "</div></div>"
# # #     )
# # #     slot.markdown(html, unsafe_allow_html=True)


# # # def render_sidebar_status(slot: Any, message: str, state: str = "info") -> None:
# # #     if state == "success":
# # #         slot.success(message)
# # #     elif state == "warning":
# # #         slot.warning(message)
# # #     elif state == "error":
# # #         slot.error(message)
# # #     else:
# # #         slot.info(message)


# # # def render_score_chart(feature_df: pd.DataFrame) -> None:
# # #     if feature_df.empty:
# # #         st.info("No feature rows yet.")
# # #         return
# # #     fig = go.Figure(
# # #         go.Bar(
# # #             x=feature_df["risk_score"],
# # #             y=feature_df["signal_area"],
# # #             orientation="h",
# # #             marker_color=["#22C55E" if x < 5 else "#F59E0B" if x < 8 else "#EF4444" for x in feature_df["risk_score"]],
# # #             text=feature_df["risk_score"],
# # #             textposition="auto",
# # #         )
# # #     )
# # #     fig.update_layout(
# # #         height=280,
# # #         margin={"l": 10, "r": 20, "t": 10, "b": 10},
# # #         xaxis={"range": [0, 10], "title": "Score"},
# # #         yaxis={"title": ""},
# # #         plot_bgcolor="#FFFFFF",
# # #         paper_bgcolor="#FFFFFF",
# # #     )
# # #     st.plotly_chart(fig, width="stretch")


# # # def scoring_formula_for_row(row: pd.Series) -> str:
# # #     source = str(row.get("source", "")).lower()
# # #     area = str(row.get("signal_area", "")).lower()
# # #     if "bls" in source or "cpi" in area:
# # #         return "CPI scoring starts from a neutral 4.0, adjusts upward or downward using monthly CPI change, adds pressure when YoY inflation is elevated, then clips to a 1-10 range."
# # #     if "fda" in source or "recall" in area:
# # #         return "Recall scoring starts from reason severity, then adjusts for FDA classification and recall status. Class I and ongoing recalls increase the score; terminated recalls reduce it."
# # #     if "noaa" in source or "weather" in area:
# # #         return "Weather scoring sums active NOAA alert severity weights for the selected state, adds urgency/certainty pressure, then caps the supply-chain risk score at 10."
# # #     if "gnews" in source or "news" in area:
# # #         return "News scoring classifies each article into event type and sentiment, adjusts for recency/source quality, then averages deduplicated article risk."
# # #     if "apify" in source or "search" in area:
# # #         return f"Search scoring uses top regional Google Trends interest divided by 10, with backend limits of {APIFY_SAFE_TIME_RANGE} and {APIFY_HARD_KEYWORD_LIMIT} keyword(s)."
# # #     return "Score is normalized to a 1-10 signal intensity scale using the collector-specific scoring rule."


# # # def render_score_explainability(feature_df: pd.DataFrame) -> None:
# # #     if feature_df.empty:
# # #         st.info("No signal explanations available.")
# # #         return
# # #     explanation_rows = feature_df.sort_values("risk_score", ascending=False).reset_index(drop=True)
# # #     for _, row in explanation_rows.iterrows():
# # #         score = float(row.get("risk_score", 0) or 0)
# # #         band = risk_band(score)
# # #         title = f"{row.get('signal_area', 'Signal')} · {str(row.get('signal_name', '')).replace('_', ' ').title()}"
# # #         meta = f"{row.get('source', 'Unknown source')} / {row.get('region_scope', row.get('region', ''))}"
# # #         reason = str(row.get("score_reason") or "No score reason was returned by this collector.")
# # #         evidence = str(row.get("raw_reference") or row.get("signal_value") or "No raw reference available.")
# # #         action = str(row.get("recommended_action") or "Review this signal before using it in planning.")
# # #         formula = scoring_formula_for_row(row)
# # #         html = (
# # #             "<div class='score-explain-card'>"
# # #             "<div class='score-explain-head'>"
# # #             f"<div><div class='score-explain-title'>{escape(title)}</div><div class='score-explain-meta'>{escape(meta)}</div></div>"
# # #             f"<div class='score-number'>{score:.2f}<span>{escape(band)}</span></div>"
# # #             "</div>"
# # #             "<div class='score-explain-label'>Scoring rule</div>"
# # #             f"<div class='score-explain-text'>{escape(formula)}</div>"
# # #             "<div class='score-explain-label'>Why this score</div>"
# # #             f"<div class='score-explain-text'>{escape(reason)}</div>"
# # #             "<div class='score-explain-label'>Evidence used</div>"
# # #             f"<div class='score-explain-text'>{escape(evidence)}</div>"
# # #             "<div class='score-explain-label'>Planning action</div>"
# # #             f"<div class='score-explain-text'>{escape(action)}</div>"
# # #             "</div>"
# # #         )
# # #         st.markdown(html, unsafe_allow_html=True)


# # # def brief_to_html(brief: str) -> str:
# # #     parts: List[str] = []
# # #     in_list = False
# # #     for raw_line in str(brief or "").splitlines():
# # #         line = raw_line.strip()
# # #         if not line:
# # #             if in_list:
# # #                 parts.append("</ul>")
# # #                 in_list = False
# # #             continue
# # #         normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", line)
# # #         normalized = normalized.replace("**", "")
# # #         is_header = normalized.endswith(":") and len(normalized) <= 90 and not re.match(r"^[-\d]", normalized)
# # #         bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+)(.*)$", normalized)
# # #         if is_header:
# # #             if in_list:
# # #                 parts.append("</ul>")
# # #                 in_list = False
# # #             parts.append(f"<div class='brief-section-title'>{escape(normalized.rstrip(':'))}</div>")
# # #         elif bullet_match:
# # #             if not in_list:
# # #                 parts.append("<ul class='brief-list'>")
# # #                 in_list = True
# # #             parts.append(f"<li>{escape(bullet_match.group(1))}</li>")
# # #         else:
# # #             if in_list:
# # #                 parts.append("</ul>")
# # #                 in_list = False
# # #             parts.append(f"<p>{escape(normalized)}</p>")
# # #     if in_list:
# # #         parts.append("</ul>")
# # #     return "".join(parts)


# # # def parse_brief_sections(brief: str) -> List[Dict[str, Any]]:
# # #     sections: List[Dict[str, Any]] = []
# # #     current = {"title": "Executive Summary", "items": []}
# # #     for raw_line in str(brief or "").splitlines():
# # #         line = raw_line.strip()
# # #         if not line:
# # #             continue
# # #         normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", line).replace("**", "")
# # #         is_header = normalized.endswith(":") and len(normalized) <= 90 and not re.match(r"^[-\d]", normalized)
# # #         bullet_match = re.match(r"^(?:[-*]\s+|\d+\.\s+)(.*)$", normalized)
# # #         if is_header:
# # #             if current["items"]:
# # #                 sections.append(current)
# # #             current = {"title": normalized.rstrip(":"), "items": []}
# # #         elif bullet_match:
# # #             current["items"].append({"kind": "bullet", "text": bullet_match.group(1)})
# # #         else:
# # #             current["items"].append({"kind": "text", "text": normalized})
# # #     if current["items"]:
# # #         sections.append(current)
# # #     return sections


# # # def brief_sections_to_html(brief: str) -> str:
# # #     sections = parse_brief_sections(brief)
# # #     if not sections:
# # #         return "<div class='brief-section-grid'><div class='brief-section-card primary'><div class='brief-section-title'>Executive Summary</div><p>No brief content was generated.</p></div></div>"
# # #     cards = []
# # #     for idx, section in enumerate(sections):
# # #         paragraphs = []
# # #         bullets = []
# # #         for item in section["items"]:
# # #             if item["kind"] == "bullet":
# # #                 bullets.append(f"<li>{escape(str(item['text']))}</li>")
# # #             else:
# # #                 paragraphs.append(f"<p>{escape(str(item['text']))}</p>")
# # #         body = "".join(paragraphs)
# # #         if bullets:
# # #             body += "<ul class='brief-list'>" + "".join(bullets) + "</ul>"
# # #         primary = " primary" if idx == 0 else ""
# # #         cards.append(
# # #             f"<div class='brief-section-card{primary}'>"
# # #             f"<div class='brief-section-title'>{escape(str(section['title']))}</div>"
# # #             f"{body}"
# # #             "</div>"
# # #         )
# # #     return "<div class='brief-section-grid'>" + "".join(cards) + "</div>"


# # # def render_executive_brief(run: Dict[str, Any], feature_df: pd.DataFrame) -> None:
# # #     brief_source = str(run.get("brief_source", "unknown"))
# # #     articles = run.get("articles", [])
# # #     llm_audit = run.get("llm_audit", {})
# # #     source_count = feature_df["source"].nunique() if not feature_df.empty and "source" in feature_df.columns else 0
# # #     top_label = "No signal"
# # #     avg_score_label = "0.00"
# # #     if not feature_df.empty:
# # #         top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
# # #         top_label = f"{top.get('signal_area', 'Signal')} {float(top.get('risk_score', 0) or 0):.2f}/10"
# # #         avg_score_label = f"{float(feature_df['risk_score'].mean()):.2f}"
# # #     articles_sent = llm_audit.get("articles_sent", min(len(articles), 8))
# # #     header_note = (
# # #         "NVIDIA grounded response" if llm_audit.get("sent_to_llm") else "Local deterministic summary using collected feature rows"
# # #     )
# # #     html = (
# # #         "<div class='brief-shell'>"
# # #         "<div class='brief-header'>"
# # #         "<div class='brief-kicker'>Executive Brief</div>"
# # #         f"<div class='brief-title'>{escape(str(run.get('run_config', {}).get('retailer', retailer_label)))} External Signal Readout</div>"
# # #         f"<div class='brief-summary-text'>{escape(header_note)}. The section cards below are grounded in collector rows and score reasons from this run.</div>"
# # #         "<div class='brief-meta-strip'>"
# # #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Brief Source</div><div class='brief-meta-value'>{escape(brief_source)}</div></div>"
# # #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Rows Grounded</div><div class='brief-meta-value'>{len(feature_df)} rows / {source_count} source(s)</div></div>"
# # #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Average Score</div><div class='brief-meta-value'>{escape(avg_score_label)} / 10</div></div>"
# # #         f"<div class='brief-meta-chip'><div class='brief-meta-label'>Top Evidence</div><div class='brief-meta-value'>{escape(top_label)}</div></div>"
# # #         "</div>"
# # #         f"<div class='brief-summary-text' style='margin-top:10px;'>Articles used in brief context: {len(articles)} available, {articles_sent} sent.</div>"
# # #         "</div>"
# # #         f"{brief_sections_to_html(str(run.get('brief', '')))}"
# # #         "</div>"
# # #     )
# # #     st.markdown(html, unsafe_allow_html=True)


# # # def parse_lines(text: str) -> List[str]:
# # #     return [line.strip() for line in text.splitlines() if line.strip()]


# # # def build_default_news_keywords(retailer_name: str) -> str:
# # #     name = retailer_name.strip() or "Retailer"
# # #     return "\n".join(
# # #         [
# # #             f"{name} inflation",
# # #             f"{name} prices",
# # #             f"{name} store closures",
# # #             f"{name} recall",
# # #             "discount retail tariffs",
# # #             "Dollar General promotion",
# # #         ]
# # #     )


# # # def build_default_trends_keywords(retailer_name: str) -> str:
# # #     name = retailer_name.strip() or "Retailer"
# # #     return "\n".join(
# # #         [
# # #             f"{name} sales",
# # #             f"{name} coupons",
# # #             f"{name} near me",
# # #             f"{name} groceries",
# # #             "cheap groceries",
# # #         ]
# # #     )


# # # def retailer_initials(retailer_name: str) -> str:
# # #     words = [word for word in re.split(r"\s+", retailer_name.strip()) if word]
# # #     if not words:
# # #         return "AI"
# # #     return "".join(word[0].upper() for word in words[:2])


# # # st.session_state.setdefault("gnews_period", "7d")
# # # st.session_state.setdefault("max_news", 24)
# # # st.session_state.setdefault("fda_query", "product_description:(snacks OR candy OR beverages)")
# # # st.session_state.setdefault("fda_limit", 8)
# # # st.session_state.setdefault("weather_area", "TX")
# # # st.session_state.setdefault("weather_limit", 5)
# # # st.session_state.setdefault("apify_geo", "US")
# # # st.session_state.setdefault("apify_time_range", APIFY_SAFE_TIME_RANGE)
# # # st.session_state.setdefault("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)
# # # st.session_state.setdefault("apify_run_mode", "Skip Apify")
# # # st.session_state.setdefault("apify_live_confirm", False)
# # # st.session_state.setdefault("workbench_view", "Configure")
# # # if st.session_state.pop("force_results_view", False):
# # #     st.session_state["workbench_view"] = "Results"
# # # if st.session_state.pop("reset_apify_live_confirm", False):
# # #     st.session_state["apify_run_mode"] = "Skip Apify"
# # #     st.session_state["apify_live_confirm"] = False


# # # with st.sidebar:
# # #     st.title("Workbench Setup")
# # #     st.caption("Credentials are used only for this Streamlit session.")

# # #     nvidia_key = st.text_input("NVIDIA API key", value=os.getenv("NVIDIA_API_KEY", ""), type="password")
# # #     nvidia_model = st.text_input("NVIDIA model", value=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL))
# # #     apify_token = st.text_input("Apify token", value=os.getenv("APIFY_API_TOKEN", ""), type="password")
# # #     bls_key = st.text_input("BLS API key optional", value=os.getenv("BLS_API_KEY", ""), type="password")

# # #     st.markdown('<div class="small-header">Retail Context</div>', unsafe_allow_html=True)
# # #     retailer = st.text_input("Company / Retailer", value="", placeholder="Optional: enter a company or retailer")
# # #     region = st.text_input("Region", value="US")
# # #     country = st.selectbox("News country", ["US", "CA", "GB", "AE", "IN"], index=0)
# # #     language = st.selectbox("News language", ["en", "es", "fr", "ar", "hi"], index=0)

# # #     st.markdown('<div class="small-header">Signal Sources</div>', unsafe_allow_html=True)
# # #     use_gnews = st.checkbox("Retail news via GNews/RSS", value=True)
# # #     use_bls = st.checkbox("Inflation via BLS CPI", value=True)
# # #     use_fda = st.checkbox("Product recalls via openFDA", value=True)
# # #     use_weather = st.checkbox("Weather risk via NOAA", value=True)
# # #     use_apify = st.checkbox("Search demand via Apify Trends", value=False)
# # #     if use_apify or apify_token.strip():
# # #         st.markdown('<div class="small-header">Apify Guardrail</div>', unsafe_allow_html=True)
# # #         if apify_token.strip():
# # #             if st.session_state.get("apify_live_confirm", False) and st.session_state.get("apify_run_mode") == "Skip Apify":
# # #                 st.session_state["apify_run_mode"] = "Run one live Apify call"
# # #             st.radio(
# # #                 "Apify live mode",
# # #                 ["Skip Apify", "Run one live Apify call"],
# # #                 key="apify_run_mode",
# # #                 horizontal=False,
# # #             )
# # #             st.session_state["apify_live_confirm"] = st.session_state.get("apify_run_mode") == "Run one live Apify call"
# # #             st.caption(f"Guarded live mode: {APIFY_SAFE_TIME_RANGE}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s). This resets after the run.")
# # #             if st.session_state.get("apify_run_mode") == "Run one live Apify call":
# # #                 st.success("Next run will call Apify once.")
# # #             else:
# # #                 st.info("Apify mode is Skip Apify. Select Run one live Apify call to collect trends.")
# # #         else:
# # #             st.session_state["apify_run_mode"] = "Skip Apify"
# # #             st.session_state["apify_live_confirm"] = False
# # #             st.warning("Add an Apify token before allowing a live trends run.")

# # #     st.markdown('<div class="small-header">Run Controls</div>', unsafe_allow_html=True)
# # #     validate_button = st.button("Validate credentials", width="stretch")
# # #     run_button = st.button("Run intelligence", type="primary", width="stretch")
# # #     sidebar_status_slot = st.empty()


# # # retailer_label = retailer.strip() or "Retailer"
# # # previous_keyword_retailer = st.session_state.get("keyword_template_retailer")
# # # previous_news_template = build_default_news_keywords(previous_keyword_retailer or retailer_label)
# # # previous_trends_template = build_default_trends_keywords(previous_keyword_retailer or retailer_label)
# # # next_news_template = build_default_news_keywords(retailer_label)
# # # next_trends_template = build_default_trends_keywords(retailer_label)
# # # if "news_keywords_text" not in st.session_state or st.session_state.get("news_keywords_text") == previous_news_template:
# # #     st.session_state["news_keywords_text"] = next_news_template
# # # if "trends_keywords_text" not in st.session_state or st.session_state.get("trends_keywords_text") == previous_trends_template:
# # #     st.session_state["trends_keywords_text"] = next_trends_template
# # # st.session_state["keyword_template_retailer"] = retailer_label
# # # apify_source_active = bool(use_apify or (apify_token.strip() and st.session_state.get("apify_run_mode") == "Run one live Apify call"))

# # # st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
# # # st.markdown(
# # #     "<div class='topbar'>"
# # #     f"<div class='tt'>Market Intelligence <span>/ {escape(retailer_label)} · External Signals</span></div>"
# # #     "<div class='tbadge'>AI Workbench</div>"
# # #     "<div style='margin-left:auto;display:flex;align-items:center;gap:8px;'>"
# # #     "<span class='ldot'></span>"
# # #     "<span style='font-size:10px;color:var(--t3);font-weight:800;'>Live API Mode</span>"
# # #     f"<div style='width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,var(--odk),var(--or));display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#fff;'>{escape(retailer_initials(retailer_label))}</div>"
# # #     "</div></div>",
# # #     unsafe_allow_html=True,
# # # )

# # # hero_left, hero_right = st.columns([2.2, 0.9], vertical_alignment="center")
# # # with hero_left:
# # #     st.markdown(
# # #         "<div class='hero-shell'>"
# # #         "<div class='hero-kicker'><span class='ldot'></span> External Signal Layer</div>"
# # #         f"<h1 class='hero-title'>{escape(retailer_label)} Market Intelligence Command Center</h1>"
# # #         "<p class='hero-copy'>A retail-grade workbench that turns news, CPI, recalls, weather alerts, search demand, and API health into forecast-ready features, composite risk scores, and buyer actions.</p>"
# # #         "</div>",
# # #         unsafe_allow_html=True,
# # #     )
# # # with hero_right:
# # #     active_sources = sum([use_gnews, use_bls, use_fda, use_weather, apify_source_active])
# # #     st.markdown(
# # #         "<div class='hero-side'>"
# # #         "<div class='hero-side-label'>Run Profile</div>"
# # #         f"<div class='hero-side-row'><span>Retailer</span><strong>{escape(retailer)}</strong></div>"
# # #         f"<div class='hero-side-row'><span>Region</span><strong>{escape(region)}</strong></div>"
# # #         f"<div class='hero-side-row'><span>Sources</span><strong>{active_sources} enabled</strong></div>"
# # #         f"<div class='hero-side-row'><span>LLM</span><strong>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</strong></div>"
# # #         "</div>",
# # #         unsafe_allow_html=True,
# # #     )

# # # view = st.segmented_control(
# # #     "Workbench view",
# # #     ["Configure", "Results", "Evidence Audit", "Raw Data"],
# # #     required=True,
# # #     label_visibility="collapsed",
# # #     key="workbench_view",
# # #     width="content",
# # # )

# # # run_status_slot = st.empty()
# # # if not run_button and st.session_state.get("run"):
# # #     last_run = st.session_state["run"]
# # #     last_states = {}
# # #     for key, result in last_run.get("results", {}).items():
# # #         status = result.get("status", "unknown")
# # #         last_states[key.upper()] = {
# # #             "status": status if status in {"success", "failed", "skipped"} else "queued",
# # #             "detail": result.get("error") or f"{len(result.get('rows', []))} feature row(s)",
# # #         }
# # #     if last_states:
# # #         render_run_monitor(run_status_slot, f"Last run completed at {last_run.get('timestamp', '')}", last_states, 100)
# # #     render_sidebar_status(sidebar_status_slot, f"Last run complete: {last_run.get('timestamp', '')}", "success")
# # # elif not run_button:
# # #     render_sidebar_status(sidebar_status_slot, "Status: idle. Apify runs only when token is present and live mode is set to Run one live Apify call.", "info")

# # # if view == "Configure":
# # #     st.markdown('<div class="small-header">Signal Source Stack</div>', unsafe_allow_html=True)
# # #     src_cols = st.columns(5)
# # #     source_specs = [
# # #         ("GNews/RSS", "Active" if use_gnews else "Off", "Retail articles, sentiment hints, source confidence."),
# # #         ("BLS CPI", "Active" if use_bls else "Off", "Headline and category inflation pressure features."),
# # #         ("openFDA", "Active" if use_fda else "Off", "Recall severity, UPC extraction, SKU-match prep."),
# # #         ("NOAA Weather", "Active" if use_weather else "Off", "Weather alerts, route disruption, emergency demand."),
# # #         ("Apify Trends", "Active" if apify_source_active else "Off", "Search interest and regional demand spikes."),
# # #     ]
# # #     for col, spec in zip(src_cols, source_specs):
# # #         with col:
# # #             render_source_tile(*spec)

# # #     left, right = st.columns([1.15, 0.85])
# # #     with left:
# # #         st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# # #         st.subheader("Signal Keywords")
# # #         st.text_area("GNews keywords", key="news_keywords_text", height=170)
# # #         st.text_area("Apify Google Trends keywords", key="trends_keywords_text", height=150)
# # #         st.markdown("</div>", unsafe_allow_html=True)
# # #     with right:
# # #         st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# # #         st.subheader("Collector Settings")
# # #         st.selectbox("GNews period", ["1d", "7d", "30d", "3m"], key="gnews_period")
# # #         st.slider("Max news results", min_value=5, max_value=60, step=1, key="max_news")
# # #         st.text_input("FDA recall search", key="fda_query")
# # #         st.slider("FDA recall limit", min_value=1, max_value=25, key="fda_limit")
# # #         st.text_input("NOAA weather area", key="weather_area", help="Two-letter US state code, such as TX, NY, CA.")
# # #         st.slider("NOAA alert limit", min_value=1, max_value=25, key="weather_limit")
# # #         st.text_input("Apify geo", key="apify_geo")
# # #         st.selectbox("Apify time range", [APIFY_SAFE_TIME_RANGE], key="apify_time_range")
# # #         st.slider("Apify max keywords", min_value=1, max_value=APIFY_HARD_KEYWORD_LIMIT, key="apify_max_keywords")
# # #         st.caption("Apify live mode is in the sidebar next to Run intelligence.")
# # #         st.markdown("</div>", unsafe_allow_html=True)

# # #     st.markdown('<div class="small-header">Agent Flow</div>', unsafe_allow_html=True)
# # #     render_workflow_strip()


# # # if validate_button:
# # #     with st.spinner("Validating credentials..."):
# # #         n_ok, n_msg = validate_nvidia(nvidia_key.strip(), nvidia_model.strip())
# # #         a_ok, a_msg = validate_apify(apify_token.strip())
# # #     st.session_state["validation"] = {"nvidia": (n_ok, n_msg), "apify": (a_ok, a_msg)}

# # # if "validation" in st.session_state:
# # #     n_ok, n_msg = st.session_state["validation"]["nvidia"]
# # #     a_ok, a_msg = st.session_state["validation"]["apify"]
# # #     st.info(f"NVIDIA: {'Connected' if n_ok else 'Not connected'} - {n_msg}")
# # #     st.info(f"Apify: {'Connected' if a_ok else 'Not connected'} - {a_msg}")


# # # if run_button:
# # #     news_keywords = parse_lines(st.session_state.get("news_keywords_text", build_default_news_keywords(retailer_label)))
# # #     trends_keywords = parse_lines(st.session_state.get("trends_keywords_text", build_default_trends_keywords(retailer_label)))
# # #     apify_token_value = apify_token.strip()
# # #     apify_run_mode = st.session_state.get("apify_run_mode", "Skip Apify")
# # #     apify_live_confirmed = bool(apify_token_value and apify_run_mode == "Run one live Apify call")
# # #     apify_requested = bool(use_apify or apify_live_confirmed)
# # #     run_config = {
# # #         "retailer": retailer.strip() or "Retailer",
# # #         "region": region,
# # #         "country": country,
# # #         "language": language,
# # #         "enabled_sources": {"gnews": use_gnews, "bls": use_bls, "fda": use_fda, "weather": use_weather, "apify": apify_requested},
# # #         "use_gnews": use_gnews,
# # #         "use_bls": use_bls,
# # #         "use_fda": use_fda,
# # #         "use_weather": use_weather,
# # #         "use_apify": apify_requested,
# # #         "news_keywords": news_keywords,
# # #         "trends_keywords": trends_keywords[:APIFY_HARD_KEYWORD_LIMIT],
# # #         "gnews_period": st.session_state.get("gnews_period", "7d"),
# # #         "max_news": int(st.session_state.get("max_news", 24)),
# # #         "fda_query": st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
# # #         "fda_limit": int(st.session_state.get("fda_limit", 8)),
# # #         "weather_area": normalize_weather_area(st.session_state.get("weather_area", "TX")),
# # #         "weather_limit": int(st.session_state.get("weather_limit", 5)),
# # #         "bls_series": BLS_CPI_SERIES,
# # #         "apify_token_present": bool(apify_token_value),
# # #         "apify_run_mode": apify_run_mode,
# # #         "apify_geo": st.session_state.get("apify_geo", "US"),
# # #         "apify_time_range": APIFY_SAFE_TIME_RANGE,
# # #         "apify_max_keywords": int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
# # #         "apify_live_confirm": apify_live_confirmed,
# # #     }
# # #     results: Dict[str, Dict[str, Any]] = {}
# # #     all_rows: List[Dict[str, Any]] = []
# # #     all_articles: List[Dict[str, Any]] = []

# # #     steps = [
# # #         ("gnews", use_gnews),
# # #         ("bls", use_bls),
# # #         ("fda", use_fda),
# # #         ("weather", use_weather),
# # #         ("apify", apify_requested),
# # #     ]
# # #     active_steps = [step for step in steps if step[1]]
# # #     total = max(1, len(active_steps))
# # #     completed = 0
# # #     collector_states: Dict[str, Dict[str, str]] = {
# # #         "GNEWS": {"status": "queued" if use_gnews else "skipped", "detail": "Retail news collector" if use_gnews else "Disabled"},
# # #         "BLS": {"status": "queued" if use_bls else "skipped", "detail": "CPI collector" if use_bls else "Disabled"},
# # #         "FDA": {"status": "queued" if use_fda else "skipped", "detail": "Recall collector" if use_fda else "Disabled"},
# # #         "WEATHER": {"status": "queued" if use_weather else "skipped", "detail": "NOAA alert collector" if use_weather else "Disabled"},
# # #         "APIFY": {"status": "queued" if apify_requested else "skipped", "detail": f"Trends collector; mode: {apify_run_mode}" if apify_requested else "Disabled"},
# # #     }
# # #     render_run_monitor(run_status_slot, "Starting collectors", collector_states, 2)
# # #     render_sidebar_status(sidebar_status_slot, "Running: starting collectors...", "info")

# # #     if use_gnews:
# # #         collector_states["GNEWS"] = {"status": "running", "detail": "Collecting and deduplicating retail news"}
# # #         render_run_monitor(run_status_slot, "Collecting retail news", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, "Running: collecting retail news...", "info")
# # #         results["gnews"] = collect_gnews(
# # #             news_keywords,
# # #             country,
# # #             language,
# # #             st.session_state.get("gnews_period", "7d"),
# # #             int(st.session_state.get("max_news", 24)),
# # #             retailer.strip() or "Retailer",
# # #         )
# # #         all_rows.extend(results["gnews"].get("rows", []))
# # #         all_articles.extend(results["gnews"].get("items", []))
# # #         completed += 1
# # #         gnews_status = results["gnews"].get("status", "failed")
# # #         collector_states["GNEWS"] = {
# # #             "status": "success" if gnews_status == "success" else "failed" if gnews_status == "failed" else "skipped",
# # #             "detail": results["gnews"].get("error") or f"{len(results['gnews'].get('items', []))} article(s), {len(results['gnews'].get('rows', []))} feature row(s)",
# # #         }
# # #         render_run_monitor(run_status_slot, "Retail news complete", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"GNews {collector_states['GNEWS']['status']}: {collector_states['GNEWS']['detail']}", "warning" if gnews_status != "success" else "info")

# # #     if use_bls:
# # #         collector_states["BLS"] = {"status": "running", "detail": "Collecting headline and category CPI"}
# # #         render_run_monitor(run_status_slot, "Collecting CPI inflation", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, "Running: collecting BLS CPI...", "info")
# # #         results["bls"] = collect_bls_cpi(bls_key.strip(), retailer.strip() or "Retailer")
# # #         all_rows.extend(results["bls"].get("rows", []))
# # #         completed += 1
# # #         bls_status = results["bls"].get("status", "failed")
# # #         collector_states["BLS"] = {
# # #             "status": "success" if bls_status == "success" else "failed",
# # #             "detail": results["bls"].get("error") or f"{len(results['bls'].get('rows', []))} CPI feature row(s)",
# # #         }
# # #         render_run_monitor(run_status_slot, "CPI collection complete", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"BLS {collector_states['BLS']['status']}: {collector_states['BLS']['detail']}", "warning" if bls_status != "success" else "info")

# # #     if use_fda:
# # #         collector_states["FDA"] = {"status": "running", "detail": "Collecting food recall records"}
# # #         render_run_monitor(run_status_slot, "Collecting FDA recalls", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, "Running: collecting FDA recalls...", "info")
# # #         results["fda"] = collect_fda_recalls(
# # #             st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
# # #             int(st.session_state.get("fda_limit", 8)),
# # #             retailer.strip() or "Retailer",
# # #         )
# # #         all_rows.extend(results["fda"].get("rows", []))
# # #         completed += 1
# # #         fda_status = results["fda"].get("status", "failed")
# # #         collector_states["FDA"] = {
# # #             "status": "success" if fda_status == "success" else "failed",
# # #             "detail": results["fda"].get("error") or f"{len(results['fda'].get('items', []))} recall item(s), {len(results['fda'].get('rows', []))} feature row(s)",
# # #         }
# # #         render_run_monitor(run_status_slot, "FDA recall collection complete", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"FDA {collector_states['FDA']['status']}: {collector_states['FDA']['detail']}", "warning" if fda_status != "success" else "info")

# # #     if use_weather:
# # #         weather_area = normalize_weather_area(st.session_state.get("weather_area", "TX"))
# # #         collector_states["WEATHER"] = {"status": "running", "detail": f"Collecting active NOAA alerts for {weather_area}"}
# # #         render_run_monitor(run_status_slot, "Collecting weather alerts", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"Running: collecting NOAA weather alerts for {weather_area}...", "info")
# # #         results["weather"] = collect_weather_alerts(
# # #             weather_area,
# # #             int(st.session_state.get("weather_limit", 5)),
# # #             retailer.strip() or "Retailer",
# # #         )
# # #         all_rows.extend(results["weather"].get("rows", []))
# # #         completed += 1
# # #         weather_status = results["weather"].get("status", "failed")
# # #         collector_states["WEATHER"] = {
# # #             "status": "success" if weather_status == "success" else "failed",
# # #             "detail": results["weather"].get("error") or f"{len(results['weather'].get('items', []))} active alert item(s), {len(results['weather'].get('rows', []))} feature row(s)",
# # #         }
# # #         render_run_monitor(run_status_slot, "Weather alert collection complete", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"Weather {collector_states['WEATHER']['status']}: {collector_states['WEATHER']['detail']}", "warning" if weather_status != "success" else "info")

# # #     if apify_requested and not apify_token_value:
# # #         results["apify"] = {
# # #             "status": "skipped",
# # #             "source": "Apify Trends",
# # #             "error": "Apify was enabled but no Apify token was provided. No Apify credits were used.",
# # #             "raw": None,
# # #             "rows": [],
# # #             "items": [],
# # #         }
# # #         completed += 1
# # #         collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
# # #         render_run_monitor(run_status_slot, "Apify skipped: token missing", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, "Apify skipped: token missing, no credits used.", "warning")

# # #     elif apify_requested and not apify_live_confirmed:
# # #         results["apify"] = {
# # #             "status": "skipped",
# # #             "source": "Apify Trends",
# # #             "error": f"Apify was enabled but Apify live mode is '{apify_run_mode}'. Select 'Run one live Apify call' in the sidebar to spend one guarded Apify run. No Apify credits were used.",
# # #             "raw": None,
# # #             "rows": [],
# # #             "items": [],
# # #         }
# # #         completed += 1
# # #         collector_states["APIFY"] = {"status": "skipped", "detail": results["apify"]["error"]}
# # #         render_run_monitor(run_status_slot, "Apify skipped without spending credits", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, "Apify skipped: no credits used.", "warning")

# # #     elif apify_requested and apify_live_confirmed:
# # #         collector_states["APIFY"] = {"status": "running", "detail": f"One guarded live run: {APIFY_SAFE_TIME_RANGE}, max {APIFY_HARD_KEYWORD_LIMIT} keyword(s)"}
# # #         render_run_monitor(run_status_slot, "Collecting Google Trends via Apify", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"Running: Apify Trends live call ({APIFY_SAFE_TIME_RANGE}, max {APIFY_HARD_KEYWORD_LIMIT} keywords)...", "warning")
# # #         results["apify"] = collect_apify_trends(
# # #             apify_token_value,
# # #             trends_keywords,
# # #             st.session_state.get("apify_geo", "US"),
# # #             APIFY_SAFE_TIME_RANGE,
# # #             retailer.strip() or "Retailer",
# # #             int(st.session_state.get("apify_max_keywords", APIFY_HARD_KEYWORD_LIMIT)),
# # #         )
# # #         all_rows.extend(results["apify"].get("rows", []))
# # #         completed += 1
# # #         st.session_state["reset_apify_live_confirm"] = True
# # #         apify_status = results["apify"].get("status", "failed")
# # #         collector_states["APIFY"] = {
# # #             "status": "success" if apify_status == "success" else "failed" if apify_status == "failed" else "skipped",
# # #             "detail": results["apify"].get("error") or f"{len(results['apify'].get('items', []))} trends row(s)",
# # #         }
# # #         render_run_monitor(run_status_slot, "Apify collection complete", collector_states, int((completed / total) * 100))
# # #         render_sidebar_status(sidebar_status_slot, f"Apify {collector_states['APIFY']['status']}: {collector_states['APIFY']['detail']}", "warning" if apify_status != "success" else "info")

# # #     render_run_monitor(run_status_slot, "Generating intelligence brief", collector_states, 98)
# # #     render_sidebar_status(sidebar_status_slot, "Running: generating executive brief...", "info")
# # #     feature_df = pd.DataFrame(all_rows)
# # #     if not feature_df.empty:
# # #         feature_df["retailer"] = retailer.strip() or "Retailer"
# # #         if region:
# # #             feature_df["selected_market"] = region
# # #     brief, brief_source, llm_audit = generate_nvidia_brief(nvidia_key.strip(), nvidia_model.strip(), feature_df, all_articles, retailer, region)

# # #     st.session_state["run"] = {
# # #         "timestamp": utc_now(),
# # #         "run_config": run_config,
# # #         "results": results,
# # #         "feature_df": feature_df,
# # #         "articles": all_articles,
# # #         "brief": brief,
# # #         "brief_source": brief_source,
# # #         "llm_audit": llm_audit,
# # #     }
# # #     render_sidebar_status(sidebar_status_slot, "Run complete. Opening Results...", "success")
# # #     st.session_state["force_results_view"] = True
# # #     st.session_state["last_collector_states"] = collector_states
# # #     st.rerun()


# # # if view == "Results":
# # #     run = st.session_state.get("run")
# # #     if not run:
# # #         st.markdown(
# # #             "<div class='empty-console'>"
# # #             "<div>"
# # #             "<div class='hero-kicker'>Ready for first run</div>"
# # #             "<div class='empty-title'>No intelligence run yet.</div>"
# # #             "<div class='empty-body'>Configure the signal stack, then click <strong>Run intelligence</strong> in the sidebar. The agent will collect public signals, normalize them into feature rows, score the risk/opportunity surface, and produce an executive brief.</div>"
# # #             "</div>"
# # #             "<div class='hero-side' style='min-width:260px;'>"
# # #             "<div class='hero-side-label'>MVP Output</div>"
# # #             "<div class='hero-side-row'><span>Feature rows</span><strong>CSV / JSON</strong></div>"
# # #             "<div class='hero-side-row'><span>Scores</span><strong>0-10</strong></div>"
# # #             "<div class='hero-side-row'><span>Brief</span><strong>NVIDIA / fallback</strong></div>"
# # #             "</div>"
# # #             "</div>",
# # #             unsafe_allow_html=True,
# # #         )
# # #         render_workflow_strip()
# # #     else:
# # #         feature_df = run["feature_df"]
# # #         st.markdown(
# # #             f"<div class='note-box'><strong>Last run:</strong> {escape(run['timestamp'])} UTC &nbsp; | &nbsp; <strong>Brief source:</strong> {escape(str(run['brief_source']))}</div>",
# # #             unsafe_allow_html=True,
# # #         )
# # #         cols = st.columns(4)
# # #         if feature_df.empty:
# # #             for col, title in zip(cols, ["Signals", "Avg Score", "Highest Score", "Brief"]):
# # #                 with col:
# # #                     render_metric_card(title, "0", "No successful feature rows yet.")
# # #         else:
# # #             avg_score = round(float(feature_df["risk_score"].mean()), 2)
# # #             top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
# # #             composite_scores = compute_composite_scores(feature_df)
# # #             with cols[0]:
# # #                 render_metric_card("Signals", str(len(feature_df)), "Forecast-ready rows generated.")
# # #             with cols[1]:
# # #                 render_metric_card("Average Score", str(avg_score), f"{risk_band(avg_score)} overall signal intensity.")
# # #             with cols[2]:
# # #                 render_metric_card("Top Signal", str(top["risk_score"]), str(top["signal_area"]), str(top["confidence"]))
# # #             with cols[3]:
# # #                 render_metric_card("News Articles", str(len(run["articles"])), "Deduplicated retail news items.")

# # #             st.markdown('<div class="small-header">Composite Agent Scores</div>', unsafe_allow_html=True)
# # #             cscore_cols = st.columns(3)
# # #             for col, (name, score) in zip(cscore_cols, composite_scores.items()):
# # #                 with col:
# # #                     render_metric_card(name, str(score), f"{risk_band(score)} priority for planning.")

# # #             st.markdown('<div class="small-header">Recommended Actions</div>', unsafe_allow_html=True)
# # #             action_cols = st.columns(4)
# # #             for col, action in zip(action_cols, build_recommended_actions(feature_df, run["results"])):
# # #                 with col:
# # #                     render_action_card(action["label"], action["title"], action["body"])

# # #             st.markdown('<div class="small-header">Signal Scores</div>', unsafe_allow_html=True)
# # #             render_score_chart(feature_df)
# # #             st.markdown('<div class="small-header">Score Explainability</div>', unsafe_allow_html=True)
# # #             render_score_explainability(feature_df)

# # #             st.markdown('<div class="small-header">Forecast Feature Table</div>', unsafe_allow_html=True)
# # #             st.dataframe(feature_df, width="stretch", hide_index=True)

# # #             csv_data = feature_df.to_csv(index=False).encode("utf-8")
# # #             json_data = json.dumps(feature_df.to_dict(orient="records"), indent=2).encode("utf-8")
# # #             c1, c2 = st.columns([1, 1])
# # #             with c1:
# # #                 st.download_button("Download forecast_features.csv", csv_data, "forecast_features.csv", "text/csv", width="stretch")
# # #             with c2:
# # #                 st.download_button("Download normalized_signals.json", json_data, "normalized_signals.json", "application/json", width="stretch")

# # #         st.markdown('<div class="small-header">Executive Brief</div>', unsafe_allow_html=True)
# # #         render_executive_brief(run, feature_df)


# # # if view == "Evidence Audit":
# # #     run = st.session_state.get("run")
# # #     if not run:
# # #         st.markdown(
# # #             "<div class='empty-console'>"
# # #             "<div>"
# # #             "<div class='hero-kicker'>Evidence Audit</div>"
# # #             "<div class='empty-title'>No run evidence yet.</div>"
# # #             "<div class='empty-body'>Run the workbench once to see raw collector payloads, normalized feature rows, score reasoning, and the exact LLM or fallback analysis input.</div>"
# # #             "</div>"
# # #             "</div>",
# # #             unsafe_allow_html=True,
# # #         )
# # #     else:
# # #         feature_df = run.get("feature_df", pd.DataFrame())
# # #         run_config = run.get(
# # #             "run_config",
# # #             {
# # #                 "retailer": retailer_label,
# # #                 "region": region,
# # #                 "country": country,
# # #                 "language": language,
# # #                 "enabled_sources": {key: key in run.get("results", {}) for key in SOURCE_ORDER},
# # #             },
# # #         )
# # #         llm_audit = run.get("llm_audit") or build_base_llm_audit(
# # #             feature_df,
# # #             run.get("articles", []),
# # #             run_config.get("retailer", retailer_label),
# # #             run_config.get("region", region),
# # #             nvidia_model.strip() or DEFAULT_NVIDIA_MODEL,
# # #         )
# # #         llm_audit["brief_source"] = run.get("brief_source", llm_audit.get("brief_source", "unknown"))
# # #         evidence_records = build_collector_evidence(run.get("results", {}), run_config)
# # #         evidence_df = pd.DataFrame(evidence_records)
# # #         mock_used = any_mock_used(run.get("results", {}), llm_audit)
# # #         pulled_sources = int((evidence_df["raw_records_pulled"] > 0).sum()) if not evidence_df.empty else 0
# # #         raw_records = int(evidence_df["raw_records_pulled"].sum()) if not evidence_df.empty else 0

# # #         render_audit_banner(mock_used, str(run.get("brief_source", "unknown")))

# # #         c1, c2, c3, c4 = st.columns(4)
# # #         with c1:
# # #             render_audit_card("Mock Data Used", "Yes" if mock_used else "No", "Collector rows are marked per run result.")
# # #         with c2:
# # #             render_audit_card("Raw Records", str(raw_records), f"{pulled_sources} source(s) returned inspectable data.")
# # #         with c3:
# # #             render_audit_card(
# # #                 "Rows Sent To Brief",
# # #                 f"{llm_audit.get('feature_rows_sent', 0)} / {llm_audit.get('feature_rows_available', 0)}",
# # #                 "Feature payload is capped for concise LLM context.",
# # #             )
# # #         with c4:
# # #             render_audit_card(
# # #                 "LLM Call",
# # #                 "NVIDIA" if llm_audit.get("sent_to_llm") else "No external LLM",
# # #                 str(llm_audit.get("fallback_reason") or "NVIDIA analyzed the shown payload."),
# # #             )

# # #         st.markdown('<div class="small-header">Collector Evidence Table</div>', unsafe_allow_html=True)
# # #         st.dataframe(evidence_df, width="stretch", hide_index=True)

# # #         st.markdown('<div class="small-header">Normalized Feature Rows And Score Reasoning</div>', unsafe_allow_html=True)
# # #         if feature_df.empty:
# # #             st.info("No normalized feature rows were generated. Check collector statuses and errors above.")
# # #         else:
# # #             preferred_cols = [
# # #                 "source",
# # #                 "signal_area",
# # #                 "signal_name",
# # #                 "region",
# # #                 "region_scope",
# # #                 "signal_value",
# # #                 "risk_score",
# # #                 "confidence",
# # #                 "score_reason",
# # #                 "business_impact",
# # #                 "recommended_action",
# # #                 "raw_reference",
# # #             ]
# # #             visible_cols = [col for col in preferred_cols if col in feature_df.columns]
# # #             st.dataframe(feature_df[visible_cols], width="stretch", hide_index=True)

# # #         st.markdown('<div class="small-header">LLM Analysis Trace</div>', unsafe_allow_html=True)
# # #         trace_cols = st.columns(4)
# # #         with trace_cols[0]:
# # #             render_metric_card("Brief Source", str(run.get("brief_source", "unknown")), "NVIDIA if sent; fallback if local rules were used.")
# # #         with trace_cols[1]:
# # #             render_metric_card("Payload Hash", str(llm_audit.get("payload_hash_sha256", ""))[:12], "SHA-256 fingerprint of features/articles payload.")
# # #         with trace_cols[2]:
# # #             render_metric_card("Articles Sent", str(llm_audit.get("articles_sent", 0)), f"{llm_audit.get('articles_available', 0)} available.")
# # #         with trace_cols[3]:
# # #             render_metric_card("Fallback Used", "Yes" if llm_audit.get("fallback_used") else "No", str(llm_audit.get("fallback_reason") or "External LLM response used."))

# # #         with st.expander("Prompt and payload used for the brief", expanded=True):
# # #             if llm_audit.get("sent_to_llm"):
# # #                 st.success("NVIDIA was called with only the feature rows and article records shown below.")
# # #             else:
# # #                 st.warning("No external LLM call was made for this run. The brief was generated by deterministic local rules using the same feature rows.")
# # #             st.markdown("System prompt")
# # #             st.code(str(llm_audit.get("system_prompt", "")), language="text")
# # #             st.markdown("User prompt")
# # #             st.code(str(llm_audit.get("user_prompt", "")), language="text")
# # #             st.markdown("Payload")
# # #             st.code(json.dumps(llm_audit.get("payload", {}), indent=2, default=str)[:16000], language="json")

# # #         with st.expander("Brief output", expanded=False):
# # #             st.markdown(f"<div class='brief-box'>{brief_to_html(str(run.get('brief', '')))}</div>", unsafe_allow_html=True)

# # #         st.markdown('<div class="small-header">Raw Pulled Data By Source</div>', unsafe_allow_html=True)
# # #         for name, result in run.get("results", {}).items():
# # #             source_name = SOURCE_LABELS.get(name, name.upper())
# # #             raw_count = count_raw_records(result.get("raw"), result.get("items"))
# # #             with st.expander(f"{source_name} - {result.get('status', 'unknown')} - {raw_count} raw record(s)", expanded=False):
# # #                 if result.get("error"):
# # #                     st.warning(result["error"])
# # #                 if result.get("items"):
# # #                     st.markdown("Cleaned items")
# # #                     st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
# # #                 raw_preview = result.get("raw")
# # #                 if raw_preview is not None:
# # #                     st.markdown("Raw payload")
# # #                     st.code(json.dumps(raw_preview, indent=2, default=str)[:16000], language="json")
# # #                 if result.get("rows"):
# # #                     st.markdown("Normalized feature rows from this source")
# # #                     st.dataframe(pd.DataFrame(result["rows"]), width="stretch", hide_index=True)

# # #         evidence_bundle = {
# # #             "timestamp": run.get("timestamp"),
# # #             "mock_data_used": mock_used,
# # #             "run_config": run_config,
# # #             "collector_evidence": evidence_records,
# # #             "normalized_features": feature_df.to_dict(orient="records") if not feature_df.empty else [],
# # #             "llm_audit": llm_audit,
# # #             "results": {
# # #                 name: {
# # #                     "status": result.get("status"),
# # #                     "source": result.get("source"),
# # #                     "error": result.get("error"),
# # #                     "rows": result.get("rows", []),
# # #                     "items": result.get("items", []),
# # #                     "raw": result.get("raw"),
# # #                 }
# # #                 for name, result in run.get("results", {}).items()
# # #             },
# # #         }
# # #         st.download_button(
# # #             "Download evidence_audit.json",
# # #             json.dumps(evidence_bundle, indent=2, default=str).encode("utf-8"),
# # #             "evidence_audit.json",
# # #             "application/json",
# # #             width="stretch",
# # #         )


# # # if view == "Raw Data":
# # #     run = st.session_state.get("run")
# # #     if not run:
# # #         st.markdown(
# # #             "<div class='empty-console'>"
# # #             "<div>"
# # #             "<div class='hero-kicker'>Raw Evidence</div>"
# # #             "<div class='empty-title'>Collector payloads will appear after a run.</div>"
# # #             "<div class='empty-body'>This view is intentionally evidence-first: GNews articles, CPI JSON, FDA recall records, NOAA weather alerts, Apify errors, and cleaned item tables stay inspectable for validation.</div>"
# # #             "</div>"
# # #             "</div>",
# # #             unsafe_allow_html=True,
# # #         )
# # #     else:
# # #         for name, result in run["results"].items():
# # #             status = result.get("status", "unknown")
# # #             label = f"{name.upper()} - {status}"
# # #             with st.expander(label, expanded=False):
# # #                 if result.get("error"):
# # #                     st.warning(result["error"])
# # #                 if result.get("items"):
# # #                     st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
# # #                 raw_preview = result.get("raw")
# # #                 if raw_preview is not None:
# # #                     st.code(json.dumps(raw_preview, indent=2, default=str)[:7000], language="json")


# # # st.markdown(
# # #     """
# # #     <p class="subtle">
# # #     Note: Google Trends values are relative indexes, GNews is a lightweight news signal, and recall data should be matched
# # #     against internal SKU/UPC and inventory records before operational decisions.
# # #     </p>
# # #     """,
# # #     unsafe_allow_html=True,
# # # )


# # # # import json
# # # # import os
# # # # import re
# # # # import xml.etree.ElementTree as ET
# # # # from datetime import datetime, timezone
# # # # from email.utils import parsedate_to_datetime
# # # # from html import escape, unescape
# # # # from typing import Any, Dict, List, Optional, Tuple
# # # # from urllib.parse import quote_plus

# # # # import pandas as pd
# # # # import plotly.graph_objects as go
# # # # import requests
# # # # import streamlit as st

# # # # try:
# # # #     from dotenv import load_dotenv
# # # # except ImportError:  # pragma: no cover - optional local convenience
# # # #     load_dotenv = None


# # # # if load_dotenv:
# # # #     load_dotenv()


# # # # NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# # # # DEFAULT_NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
# # # # BLS_CPI_SERIES = {
# # # #     "Headline CPI": "CUUR0000SA0",
# # # #     "Food at home": "CUUR0000SAF11",
# # # #     "Household furnishings": "CUUR0000SAH3",
# # # #     "Gasoline": "CUUR0000SETB",
# # # # }


# # # # st.set_page_config(
# # # #     page_title="Market Intelligence Command Center",
# # # #     page_icon="DT",
# # # #     layout="wide",
# # # #     initial_sidebar_state="expanded",
# # # # )


# # # # st.markdown(
# # # #     """
# # # #     <style>
# # # #     @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
# # # #     :root {
# # # #         --bg: #F5F7FB;
# # # #         --sf: #FFFFFF;
# # # #         --s2: #F8FAFE;
# # # #         --s3: #F0F4F9;
# # # #         --s4: #E9EFF5;
# # # #         --or: #F47B25;
# # # #         --olt: #FF9F50;
# # # #         --odk: #C45D0A;
# # # #         --og: rgba(244,123,37,0.12);
# # # #         --ob: rgba(244,123,37,0.07);
# # # #         --obr: rgba(244,123,37,0.25);
# # # #         --bl: #E2E8F0;
# # # #         --t: #1E293B;
# # # #         --t2: #475569;
# # # #         --t3: #94A3B8;
# # # #         --gr: #22C55E;
# # # #         --gbg: rgba(34,197,94,0.10);
# # # #         --am: #F59E0B;
# # # #         --abg: rgba(245,158,11,0.10);
# # # #         --rd: #EF4444;
# # # #         --rbg: rgba(239,68,68,0.08);
# # # #         --r: 12px;
# # # #         --rl: 16px;
# # # #         --sh: 0 1px 3px rgba(0,0,0,0.04);
# # # #         --shm: 0 6px 14px -4px rgba(0,0,0,0.10);
# # # #         --tr: 0.2s cubic-bezier(0.4,0,0.2,1);
# # # #     }
# # # #     * { box-sizing: border-box; }
# # # #     html, body, [class*="css"] {
# # # #         font-family: 'Inter', ui-sans-serif, system-ui, sans-serif;
# # # #         color: var(--t);
# # # #     }
# # # #     .stApp { background: var(--bg); }
# # # #     #MainMenu, footer { visibility: hidden; }
# # # #     header { visibility: hidden; }
# # # #     .accent-bar {
# # # #         height: 3px;
# # # #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt), var(--or));
# # # #         background-size: 200%;
# # # #         animation: shimmer 3s linear infinite;
# # # #         width: 100%;
# # # #         margin: -1.25rem 0 0.9rem 0;
# # # #     }
# # # #     @keyframes shimmer { 0% { background-position: 200%; } 100% { background-position: -200%; } }
# # # #     @keyframes pdot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.5); } 50% { box-shadow: 0 0 0 5px rgba(34,197,94,0); } }
# # # #     .ldot {
# # # #         width: 7px;
# # # #         height: 7px;
# # # #         border-radius: 50%;
# # # #         background: var(--gr);
# # # #         animation: pdot 2s infinite;
# # # #         display: inline-block;
# # # #     }
# # # #     .main .block-container {
# # # #         padding-top: 1.25rem;
# # # #         max-width: 1400px;
# # # #     }
# # # #     [data-testid="stSidebar"] {
# # # #         background: var(--sf) !important;
# # # #         border-right: 1px solid var(--bl) !important;
# # # #     }
# # # #     [data-testid="stSidebar"] * {
# # # #         color: var(--t2);
# # # #     }
# # # #     h1, h2, h3 {
# # # #         color: var(--t);
# # # #         letter-spacing: 0;
# # # #     }
# # # #     h1 { font-size: 2.25rem; font-weight: 900; letter-spacing: -0.02em; }
# # # #     .subtle {
# # # #         color: var(--t2);
# # # #         font-size: 0.94rem;
# # # #         line-height: 1.55;
# # # #     }
# # # #     .topbar {
# # # #         height: 54px;
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: var(--rl);
# # # #         display: flex;
# # # #         align-items: center;
# # # #         padding: 0 18px;
# # # #         gap: 12px;
# # # #         box-shadow: var(--sh);
# # # #         margin-bottom: 18px;
# # # #     }
# # # #     .tt { font-size: 14px; font-weight: 800; color: var(--t); }
# # # #     .tt span { color: var(--t3); font-weight: 500; }
# # # #     .tbadge {
# # # #         background: var(--ob);
# # # #         border: 1px solid var(--obr);
# # # #         color: var(--or);
# # # #         font-size: 10px;
# # # #         font-weight: 800;
# # # #         padding: 2px 8px;
# # # #         border-radius: 20px;
# # # #     }
# # # #     .hero-shell {
# # # #         background:
# # # #             radial-gradient(circle at 88% 18%, rgba(244,123,37,0.18), transparent 28%),
# # # #             linear-gradient(135deg, #FFFFFF 0%, #F8FAFE 54%, #FFF7ED 100%);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: 22px;
# # # #         padding: 24px 26px;
# # # #         box-shadow: 0 12px 30px -22px rgba(15,23,42,0.35);
# # # #         margin-bottom: 16px;
# # # #         position: relative;
# # # #         overflow: hidden;
# # # #     }
# # # #     .hero-shell:before {
# # # #         content: "";
# # # #         position: absolute;
# # # #         left: 0;
# # # #         right: 0;
# # # #         top: 0;
# # # #         height: 4px;
# # # #         background: linear-gradient(90deg, var(--odk), var(--or), var(--olt));
# # # #     }
# # # #     .hero-kicker {
# # # #         display: inline-flex;
# # # #         align-items: center;
# # # #         gap: 7px;
# # # #         background: var(--ob);
# # # #         border: 1px solid var(--obr);
# # # #         color: var(--or);
# # # #         border-radius: 999px;
# # # #         padding: 4px 10px;
# # # #         font-size: 10px;
# # # #         font-weight: 900;
# # # #         letter-spacing: 0.8px;
# # # #         text-transform: uppercase;
# # # #         margin-bottom: 12px;
# # # #     }
# # # #     .hero-title {
# # # #         font-size: 42px;
# # # #         line-height: 1.02;
# # # #         letter-spacing: -0.04em;
# # # #         font-weight: 950;
# # # #         color: var(--t);
# # # #         max-width: 820px;
# # # #         margin: 0;
# # # #     }
# # # #     .hero-copy {
# # # #         color: var(--t2);
# # # #         font-size: 14px;
# # # #         line-height: 1.7;
# # # #         max-width: 820px;
# # # #         margin: 14px 0 0 0;
# # # #     }
# # # #     .hero-side {
# # # #         background: rgba(255,255,255,0.74);
# # # #         border: 1px solid rgba(226,232,240,0.9);
# # # #         border-radius: var(--rl);
# # # #         padding: 14px;
# # # #         box-shadow: var(--sh);
# # # #     }
# # # #     .hero-side-label {
# # # #         color: var(--t3);
# # # #         font-size: 9px;
# # # #         font-weight: 900;
# # # #         letter-spacing: 1.2px;
# # # #         text-transform: uppercase;
# # # #         margin-bottom: 8px;
# # # #     }
# # # #     .hero-side-row {
# # # #         display: flex;
# # # #         justify-content: space-between;
# # # #         gap: 12px;
# # # #         border-top: 1px solid var(--bl);
# # # #         padding-top: 8px;
# # # #         margin-top: 8px;
# # # #         font-size: 12px;
# # # #         color: var(--t2);
# # # #     }
# # # #     .hero-side-row strong { color: var(--t); }
# # # #     .source-tile {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: 14px;
# # # #         padding: 13px 14px;
# # # #         box-shadow: var(--sh);
# # # #         min-height: 86px;
# # # #         transition: all var(--tr);
# # # #     }
# # # #     .source-tile:hover { border-color: var(--obr); box-shadow: var(--shm); transform: translateY(-1px); }
# # # #     .source-name {
# # # #         font-size: 12px;
# # # #         font-weight: 850;
# # # #         color: var(--t);
# # # #         margin-bottom: 5px;
# # # #     }
# # # #     .source-meta {
# # # #         color: var(--t2);
# # # #         font-size: 11px;
# # # #         line-height: 1.45;
# # # #     }
# # # #     .console-panel {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: 18px;
# # # #         padding: 18px;
# # # #         box-shadow: var(--sh);
# # # #         margin-top: 12px;
# # # #     }
# # # #     .empty-console {
# # # #         background:
# # # #             linear-gradient(135deg, rgba(244,123,37,0.08), rgba(255,255,255,0.85)),
# # # #             var(--sf);
# # # #         border: 1px dashed var(--obr);
# # # #         border-radius: 18px;
# # # #         padding: 28px;
# # # #         min-height: 210px;
# # # #         display: flex;
# # # #         align-items: center;
# # # #         justify-content: space-between;
# # # #         gap: 20px;
# # # #     }
# # # #     .empty-title {
# # # #         color: var(--t);
# # # #         font-size: 22px;
# # # #         line-height: 1.15;
# # # #         font-weight: 900;
# # # #         letter-spacing: -0.025em;
# # # #         margin-bottom: 8px;
# # # #     }
# # # #     .empty-body {
# # # #         color: var(--t2);
# # # #         font-size: 13px;
# # # #         line-height: 1.65;
# # # #         max-width: 620px;
# # # #     }
# # # #     .workflow {
# # # #         display: grid;
# # # #         grid-template-columns: repeat(5, minmax(0, 1fr));
# # # #         gap: 8px;
# # # #         margin-top: 12px;
# # # #     }
# # # #     .workflow-step {
# # # #         background: var(--s2);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: 12px;
# # # #         padding: 10px;
# # # #         font-size: 11px;
# # # #         color: var(--t2);
# # # #         font-weight: 700;
# # # #     }
# # # #     .workflow-step span {
# # # #         display: block;
# # # #         color: var(--or);
# # # #         font-size: 9px;
# # # #         letter-spacing: 1px;
# # # #         text-transform: uppercase;
# # # #         font-weight: 900;
# # # #         margin-bottom: 3px;
# # # #     }
# # # #     .metric-card {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: var(--rl);
# # # #         padding: 16px 18px;
# # # #         min-height: 132px;
# # # #         box-shadow: var(--sh);
# # # #         transition: all var(--tr);
# # # #     }
# # # #     .metric-card:hover {
# # # #         transform: translateY(-1px);
# # # #         box-shadow: var(--shm);
# # # #         border-color: var(--obr);
# # # #     }
# # # #     .metric-label {
# # # #         color: var(--t3);
# # # #         font-size: 0.68rem;
# # # #         text-transform: uppercase;
# # # #         letter-spacing: 0.11em;
# # # #         margin-bottom: 8px;
# # # #         font-weight: 800;
# # # #     }
# # # #     .metric-value {
# # # #         color: var(--t);
# # # #         font-size: 2.05rem;
# # # #         font-weight: 900;
# # # #         line-height: 1;
# # # #         letter-spacing: -0.04em;
# # # #     }
# # # #     .metric-note {
# # # #         color: var(--t2);
# # # #         font-size: 0.82rem;
# # # #         margin-top: 10px;
# # # #         line-height: 1.4;
# # # #     }
# # # #     .pill {
# # # #         display: inline-block;
# # # #         border-radius: 999px;
# # # #         padding: 3px 10px;
# # # #         font-size: 0.72rem;
# # # #         font-weight: 800;
# # # #         border: 1px solid var(--bl);
# # # #         color: var(--t2);
# # # #         background: var(--s2);
# # # #         margin-top: 8px;
# # # #     }
# # # #     .pill-high { color: var(--gr); background: var(--gbg); border-color: rgba(34,197,94,0.2); }
# # # #     .pill-medium { color: var(--am); background: var(--abg); border-color: rgba(245,158,11,0.2); }
# # # #     .pill-low { color: var(--rd); background: var(--rbg); border-color: rgba(239,68,68,0.2); }
# # # #     .brief-box {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--obr);
# # # #         border-left: 4px solid var(--or);
# # # #         border-radius: var(--rl);
# # # #         padding: 20px 22px;
# # # #         color: var(--t);
# # # #         line-height: 1.65;
# # # #         box-shadow: 0 0 0 3px var(--og);
# # # #     }
# # # #     .small-header {
# # # #         color: var(--t3);
# # # #         font-size: 0.68rem;
# # # #         text-transform: uppercase;
# # # #         letter-spacing: 0.12em;
# # # #         font-weight: 800;
# # # #         margin: 8px 0 12px 0;
# # # #         padding-bottom: 6px;
# # # #         border-bottom: 1px solid var(--bl);
# # # #     }
# # # #     .action-card {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: var(--r);
# # # #         padding: 12px 14px;
# # # #         box-shadow: var(--sh);
# # # #         min-height: 112px;
# # # #     }
# # # #     .action-label {
# # # #         font-size: 9px;
# # # #         font-weight: 900;
# # # #         letter-spacing: 1px;
# # # #         text-transform: uppercase;
# # # #         color: var(--or);
# # # #         margin-bottom: 7px;
# # # #     }
# # # #     .action-title {
# # # #         font-size: 13px;
# # # #         font-weight: 800;
# # # #         color: var(--t);
# # # #         margin-bottom: 5px;
# # # #     }
# # # #     .action-body {
# # # #         font-size: 12px;
# # # #         color: var(--t2);
# # # #         line-height: 1.45;
# # # #     }
# # # #     .note-box {
# # # #         background: rgba(244,123,37,0.04);
# # # #         border-left: 3px solid var(--or);
# # # #         border-radius: 0 8px 8px 0;
# # # #         padding: 9px 12px;
# # # #         font-size: 12px;
# # # #         color: var(--t2);
# # # #         margin: 8px 0;
# # # #     }
# # # #     div[role="radiogroup"] {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl);
# # # #         border-radius: 14px;
# # # #         padding: 5px;
# # # #         display: inline-flex;
# # # #         gap: 4px;
# # # #         box-shadow: var(--sh);
# # # #         margin: 4px 0 12px 0;
# # # #     }
# # # #     div[role="radiogroup"] label {
# # # #         border-radius: 10px !important;
# # # #         padding: 6px 13px !important;
# # # #         min-height: 34px !important;
# # # #         transition: all var(--tr);
# # # #     }
# # # #     div[role="radiogroup"] label:has(input:checked) {
# # # #         background: var(--ob) !important;
# # # #         border: 1px solid var(--obr) !important;
# # # #         color: var(--or) !important;
# # # #         font-weight: 850 !important;
# # # #     }
# # # #     div[role="radiogroup"] label span {
# # # #         font-size: 12px !important;
# # # #         font-weight: 750 !important;
# # # #     }
# # # #     div[data-testid="stButton"] button {
# # # #         background: var(--or);
# # # #         color: #fff;
# # # #         border: none;
# # # #         border-radius: var(--r);
# # # #         font-weight: 800;
# # # #         box-shadow: 0 2px 8px rgba(244,123,37,0.2);
# # # #         transition: all var(--tr);
# # # #     }
# # # #     div[data-testid="stButton"] button:hover {
# # # #         background: var(--odk);
# # # #         color: #fff;
# # # #         border: none;
# # # #         transform: translateY(-1px);
# # # #     }
# # # #     .stTabs [data-baseweb="tab-list"] {
# # # #         gap: 4px;
# # # #         border-bottom: 1px solid var(--bl);
# # # #     }
# # # #     .stTabs [data-baseweb="tab"] {
# # # #         border-radius: 9px 9px 0 0;
# # # #         color: var(--t2);
# # # #         font-weight: 700;
# # # #     }
# # # #     .stTabs [aria-selected="true"] {
# # # #         background: var(--ob);
# # # #         color: var(--or) !important;
# # # #         border: 1px solid var(--obr);
# # # #         border-bottom-color: transparent;
# # # #     }
# # # #     .stSelectbox>div>div, .stTextInput>div>div, .stTextArea>div>div {
# # # #         background: var(--s2) !important;
# # # #         border: 1px solid var(--bl) !important;
# # # #         border-radius: var(--r) !important;
# # # #         font-size: 13px !important;
# # # #         color: var(--t) !important;
# # # #     }
# # # #     [data-testid="stExpander"] {
# # # #         background: var(--sf);
# # # #         border: 1px solid var(--bl) !important;
# # # #         border-radius: var(--rl) !important;
# # # #         box-shadow: var(--sh);
# # # #     }
# # # #     ::-webkit-scrollbar { width: 4px; height: 4px; }
# # # #     ::-webkit-scrollbar-track { background: transparent; }
# # # #     ::-webkit-scrollbar-thumb { background: var(--bl); border-radius: 2px; }
# # # #     </style>
# # # #     """,
# # # #     unsafe_allow_html=True,
# # # # )


# # # # def utc_now() -> str:
# # # #     return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# # # # def safe_request(
# # # #     url: str,
# # # #     *,
# # # #     method: str = "GET",
# # # #     headers: Optional[Dict[str, str]] = None,
# # # #     params: Optional[Dict[str, Any]] = None,
# # # #     json_body: Optional[Dict[str, Any]] = None,
# # # #     timeout: int = 30,
# # # # ) -> Tuple[bool, Any, str]:
# # # #     try:
# # # #         if method.upper() == "POST":
# # # #             response = requests.post(url, headers=headers, params=params, json=json_body, timeout=timeout)
# # # #         else:
# # # #             response = requests.get(url, headers=headers, params=params, timeout=timeout)
# # # #         response.raise_for_status()
# # # #         try:
# # # #             return True, response.json(), "success"
# # # #         except ValueError:
# # # #             return True, response.text, "success"
# # # #     except requests.RequestException as exc:
# # # #         return False, None, str(exc)


# # # # def confidence_class(confidence: str) -> str:
# # # #     lookup = {"High": "pill-high", "Medium": "pill-medium", "Low": "pill-low"}
# # # #     return lookup.get(confidence, "")


# # # # def risk_band(score: float) -> str:
# # # #     if score >= 8:
# # # #         return "High"
# # # #     if score >= 5:
# # # #         return "Medium"
# # # #     return "Low"


# # # # def source_confidence(publisher: str) -> str:
# # # #     high = ["reuters", "associated press", "ap news", "bloomberg", "sec", "pr newswire"]
# # # #     medium = ["cnbc", "yahoo", "nasdaq", "marketwatch", "forbes", "investing.com", "retail dive"]
# # # #     p = (publisher or "").lower()
# # # #     if any(name in p for name in high):
# # # #         return "High"
# # # #     if any(name in p for name in medium):
# # # #         return "Medium"
# # # #     return "Medium" if publisher else "Low"


# # # # def validate_nvidia(api_key: str, model: str) -> Tuple[bool, str]:
# # # #     if not api_key:
# # # #         return False, "NVIDIA key not provided. Brief generation will use a local fallback."
# # # #     prompt = "Return exactly: connected"
# # # #     ok, data, msg = safe_request(
# # # #         NVIDIA_CHAT_URL,
# # # #         method="POST",
# # # #         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
# # # #         json_body={
# # # #             "model": model,
# # # #             "messages": [{"role": "user", "content": prompt}],
# # # #             "temperature": 0,
# # # #             "max_tokens": 8,
# # # #         },
# # # #         timeout=30,
# # # #     )
# # # #     if not ok:
# # # #         return False, msg
# # # #     content = data.get("choices", [{}])[0].get("message", {}).get("content")
# # # #     content_text = str(content or "").strip()
# # # #     return True, f"Connected. Model responded: {content_text or 'ok'}"


# # # # def validate_apify(token: str) -> Tuple[bool, str]:
# # # #     if not token:
# # # #         return False, "Apify token not provided. Apify collectors will be skipped."
# # # #     try:
# # # #         from apify_client import ApifyClient
# # # #     except ImportError:
# # # #         return False, "apify-client is not installed. Install requirements before running Apify collectors."
# # # #     try:
# # # #         client = ApifyClient(token)
# # # #         user = client.user().get()
# # # #         username = user.get("username") or user.get("email") or "Apify user"
# # # #         return True, f"Connected as {username}."
# # # #     except Exception as exc:  # pragma: no cover - depends on live Apify service
# # # #         return False, str(exc)


# # # # def build_cpi_signal(data: Dict[str, Any], label: str, series_id: str) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
# # # #     series = data.get("Results", {}).get("series", [])
# # # #     rows = []
# # # #     for item in (series[0].get("data", []) if series else [])[:24]:
# # # #         period = item.get("period", "")
# # # #         if period == "M13":
# # # #             continue
# # # #         try:
# # # #             cpi_value = float(item["value"])
# # # #         except (TypeError, ValueError, KeyError):
# # # #             continue
# # # #         rows.append(
# # # #             {
# # # #                 "category": label,
# # # #                 "series_id": series_id,
# # # #                 "year": int(item["year"]),
# # # #                 "period": period,
# # # #                 "month": item.get("periodName", ""),
# # # #                 "cpi_value": cpi_value,
# # # #             }
# # # #         )
# # # #     df = pd.DataFrame(rows)
# # # #     if df.empty:
# # # #         return None, df
# # # #     df = df.sort_values(["year", "period"]).reset_index(drop=True)
# # # #     df["cpi_mom_change_pct"] = df["cpi_value"].pct_change() * 100
# # # #     df["cpi_yoy_change_pct"] = df["cpi_value"].pct_change(12) * 100
# # # #     latest = df.iloc[-1].to_dict()
# # # #     mom = latest.get("cpi_mom_change_pct")
# # # #     yoy = latest.get("cpi_yoy_change_pct")
# # # #     score = 4.0
# # # #     if pd.notna(mom):
# # # #         score = min(10.0, max(1.0, 4.0 + float(mom) * 5.0))
# # # #     if pd.notna(yoy) and yoy > 4:
# # # #         score = min(10.0, score + 1.0)
# # # #     signal_name = "inflation_pressure_score" if label == "Headline CPI" else f"{label.lower().replace(' ', '_')}_cpi_pressure_score"
# # # #     signal = {
# # # #         "date": f"{int(latest['year'])}-{str(latest['period']).replace('M', '').zfill(2)}",
# # # #         "retailer": "Retailer",
# # # #         "region": "US",
# # # #         "source": "BLS CPI",
# # # #         "signal_area": "Inflation" if label == "Headline CPI" else "Category CPI",
# # # #         "signal_name": signal_name,
# # # #         "signal_value": round(float(score), 2),
# # # #         "risk_score": round(float(score), 2),
# # # #         "confidence": "High",
# # # #         "business_impact": f"{label} inflation can affect price sensitivity, category demand, and basket mix.",
# # # #         "recommended_action": "Use category CPI as an external regressor and validate against internal category sales.",
# # # #         "raw_reference": f"{label}: CPI {latest['cpi_value']}",
# # # #     }
# # # #     return signal, df


# # # # def collect_bls_cpi(bls_key: str = "") -> Dict[str, Any]:
# # # #     payload: Dict[str, Any] = {"seriesid": list(BLS_CPI_SERIES.values())}
# # # #     if bls_key:
# # # #         payload["registrationkey"] = bls_key
# # # #     signals = []
# # # #     tables = []
# # # #     raw = {}
# # # #     errors = []
# # # #     ok, data, msg = safe_request(
# # # #         "https://api.bls.gov/publicAPI/v2/timeseries/data/",
# # # #         method="POST",
# # # #         headers={"Content-Type": "application/json"},
# # # #         json_body=payload,
# # # #         timeout=30,
# # # #     )
# # # #     if not ok:
# # # #         return {"status": "failed", "source": "BLS CPI", "error": msg, "raw": None, "rows": []}
# # # #     if data.get("status") != "REQUEST_SUCCEEDED":
# # # #         return {
# # # #             "status": "failed",
# # # #             "source": "BLS CPI",
# # # #             "error": "; ".join(data.get("message", [])) or "BLS request was not processed.",
# # # #             "raw": data,
# # # #             "rows": [],
# # # #         }
# # # #     series_by_id = {
# # # #         series.get("seriesID"): series
# # # #         for series in data.get("Results", {}).get("series", [])
# # # #     }
# # # #     for label, series_id in BLS_CPI_SERIES.items():
# # # #         series_payload = {"Results": {"series": [series_by_id.get(series_id, {})]}}
# # # #         raw[label] = series_payload
# # # #         signal, df = build_cpi_signal(series_payload, label, series_id)
# # # #         if signal:
# # # #             signals.append(signal)
# # # #         if not df.empty:
# # # #             tables.append(df)
# # # #     if not signals:
# # # #         return {"status": "failed", "source": "BLS CPI", "error": "; ".join(errors) or "No CPI rows returned.", "raw": raw, "rows": []}
# # # #     table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
# # # #     return {"status": "success", "source": "BLS CPI", "error": "; ".join(errors), "raw": raw, "rows": signals, "table": table}


# # # # def classify_recall(reason: str) -> Tuple[str, float]:
# # # #     text = (reason or "").lower()
# # # #     if any(word in text for word in ["lead", "heavy metal", "salmonella", "listeria", "e. coli", "contamination"]):
# # # #         return "high_safety_risk", 8.0
# # # #     if any(word in text for word in ["undeclared", "allergen", "milk", "peanut", "soy", "tree nut"]):
# # # #         return "allergen_risk", 6.5
# # # #     if any(word in text for word in ["mislabel", "label"]):
# # # #         return "labeling_risk", 4.5
# # # #     return "general_recall_risk", 5.0


# # # # def extract_upcs(text: str) -> List[str]:
# # # #     candidates = re.findall(r"(?:UPC(?:\s*Code)?[:\s]*)?(\d(?:[\s-]?\d){7,13})", text or "", flags=re.IGNORECASE)
# # # #     cleaned = []
# # # #     for candidate in candidates:
# # # #         digits = re.sub(r"\D", "", candidate)
# # # #         if 8 <= len(digits) <= 14 and digits not in cleaned:
# # # #             cleaned.append(digits)
# # # #     return cleaned


# # # # def adjust_recall_score(base_score: float, classification: str, status: str) -> float:
# # # #     score = base_score
# # # #     class_text = (classification or "").lower()
# # # #     status_text = (status or "").lower()
# # # #     if "class i" in class_text:
# # # #         score += 1.5
# # # #     elif "class ii" in class_text:
# # # #         score += 0.8
# # # #     if "ongoing" in status_text:
# # # #         score += 1.0
# # # #     elif "terminated" in status_text:
# # # #         score -= 1.0
# # # #     return round(min(10.0, max(1.0, score)), 2)


# # # # def collect_fda_recalls(query: str, limit: int) -> Dict[str, Any]:
# # # #     params = {"search": query, "limit": limit}
# # # #     ok, data, msg = safe_request("https://api.fda.gov/food/enforcement.json", params=params, timeout=30)
# # # #     if not ok:
# # # #         return {"status": "failed", "source": "openFDA", "error": msg, "raw": None, "rows": [], "items": []}
# # # #     results = data.get("results", [])
# # # #     rows = []
# # # #     items = []
# # # #     for item in results:
# # # #         risk_type, base_score = classify_recall(item.get("reason_for_recall", ""))
# # # #         product = item.get("product_description", "Unknown product")
# # # #         state = item.get("state", "US")
# # # #         classification = item.get("classification", "")
# # # #         status = item.get("status", "")
# # # #         score = adjust_recall_score(base_score, classification, status)
# # # #         upcs = extract_upcs(f"{product} {item.get('code_info', '')}")
# # # #         items.append(
# # # #             {
# # # #                 "product": product,
# # # #                 "reason": item.get("reason_for_recall", ""),
# # # #                 "state": state,
# # # #                 "classification": classification,
# # # #                 "status": status,
# # # #                 "recall_date": item.get("recall_initiation_date", ""),
# # # #                 "distribution_pattern": item.get("distribution_pattern", ""),
# # # #                 "recalling_firm": item.get("recalling_firm", ""),
# # # #                 "upcs": ", ".join(upcs) if upcs else "",
# # # #                 "sku_match_status": "unknown",
# # # #                 "risk_type": risk_type,
# # # #                 "risk_score": score,
# # # #             }
# # # #         )
# # # #     aggregate_score = max([x["risk_score"] for x in items], default=1.0)
# # # #     ongoing_count = sum(1 for x in items if str(x.get("status", "")).lower() == "ongoing")
# # # #     class_i_count = sum(1 for x in items if "class i" in str(x.get("classification", "")).lower())
# # # #     signal = {
# # # #         "date": utc_now()[:10],
# # # #         "retailer": "Retailer",
# # # #         "region": "US",
# # # #         "source": "openFDA",
# # # #         "signal_area": "Product Recalls",
# # # #         "signal_name": "recall_risk_score",
# # # #         "signal_value": len(items),
# # # #         "risk_score": round(aggregate_score, 2),
# # # #         "confidence": "High",
# # # #         "business_impact": "Food and beverage recalls can trigger inventory withdrawal, substitution demand, and safety review.",
# # # #         "recommended_action": "Prioritize ongoing and Class I recalls, then match UPCs against Retailer inventory before store-level action.",
# # # #         "raw_reference": f"{len(items)} recall records; {ongoing_count} ongoing; {class_i_count} Class I",
# # # #     }
# # # #     rows.append(signal)
# # # #     return {"status": "success", "source": "openFDA", "error": "", "raw": data, "rows": rows, "items": items}


# # # # def gnews_package_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
# # # #     try:
# # # #         from gnews import GNews
# # # #     except ImportError:
# # # #         return None
# # # #     google_news = GNews(language=language, country=country, period=period, max_results=max_results)
# # # #     return google_news.get_news(keyword)


# # # # def google_news_rss_collect(keyword: str, country: str, language: str, period: str, max_results: int) -> List[Dict[str, Any]]:
# # # #     # Google News RSS supports a "when:" query operator. GNews package is preferred when installed.
# # # #     query = quote_plus(f"{keyword} when:{period}")
# # # #     country_code = country.upper()
# # # #     lang_code = language.lower()
# # # #     url = f"https://news.google.com/rss/search?q={query}&hl={lang_code}-{country_code}&gl={country_code}&ceid={country_code}:{lang_code}"
# # # #     response = requests.get(url, timeout=30)
# # # #     response.raise_for_status()
# # # #     root = ET.fromstring(response.content)
# # # #     articles = []
# # # #     for item in root.findall(".//item")[:max_results]:
# # # #         source_node = item.find("source")
# # # #         articles.append(
# # # #             {
# # # #                 "title": item.findtext("title", default=""),
# # # #                 "description": item.findtext("description", default=""),
# # # #                 "published date": item.findtext("pubDate", default=""),
# # # #                 "url": item.findtext("link", default=""),
# # # #                 "publisher": source_node.text if source_node is not None else "",
# # # #             }
# # # #         )
# # # #     return articles


# # # # def clean_news_description(description: str) -> str:
# # # #     text = re.sub(r"<[^>]+>", " ", description or "")
# # # #     text = unescape(text)
# # # #     text = re.sub(r"\s+", " ", text).strip()
# # # #     return text


# # # # def article_days_old(published_date: str) -> Optional[int]:
# # # #     if not published_date:
# # # #         return None
# # # #     try:
# # # #         published = parsedate_to_datetime(published_date)
# # # #         if published.tzinfo is None:
# # # #             published = published.replace(tzinfo=timezone.utc)
# # # #         return max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
# # # #     except (TypeError, ValueError):
# # # #         return None


# # # # def classify_news_title(title: str, description: str) -> Tuple[str, float, str]:
# # # #     text = f"{title} {description}".lower()
# # # #     if any(word in text for word in ["recall", "contamination", "lawsuit", "closure", "closing", "tariff", "warning"]):
# # # #         return "risk_event", 7.0, "negative"
# # # #     if any(word in text for word in ["inflation", "prices", "freight", "cost", "margin"]):
# # # #         return "price_pressure", 6.0, "negative"
# # # #     if any(word in text for word in ["deal", "sale", "promotion", "coupon", "holiday", "seasonal"]):
# # # #         return "demand_opportunity", 6.5, "positive"
# # # #     if any(word in text for word in ["earnings", "forecast", "guidance", "outlook"]):
# # # #         return "financial_update", 5.5, "neutral"
# # # #     return "general_market_news", 3.5, "neutral"


# # # # def collect_gnews(keywords: List[str], country: str, language: str, period: str, max_results: int) -> Dict[str, Any]:
# # # #     all_articles = []
# # # #     errors = []
# # # #     per_keyword_limit = max(1, int(max_results / max(1, len(keywords))))
# # # #     for keyword in keywords:
# # # #         try:
# # # #             articles = gnews_package_collect(keyword, country, language, period, per_keyword_limit)
# # # #             if articles is None:
# # # #                 articles = google_news_rss_collect(keyword, country, language, period, per_keyword_limit)
# # # #             for article in articles or []:
# # # #                 description = clean_news_description(article.get("description", ""))
# # # #                 event_type, score, sentiment = classify_news_title(article.get("title", ""), description)
# # # #                 publisher = article.get("publisher", "")
# # # #                 if isinstance(publisher, dict):
# # # #                     publisher = publisher.get("title") or publisher.get("href") or ""
# # # #                 published_date = article.get("published date") or article.get("published_date", "")
# # # #                 days_old = article_days_old(published_date)
# # # #                 if days_old is not None and days_old > 30:
# # # #                     score = max(1.0, score - 1.0)
# # # #                 all_articles.append(
# # # #                     {
# # # #                         "keyword": keyword,
# # # #                         "title": article.get("title", ""),
# # # #                         "description": description,
# # # #                         "published_date": published_date,
# # # #                         "days_old": days_old,
# # # #                         "publisher": publisher,
# # # #                         "url": article.get("url", ""),
# # # #                         "source_tier": source_confidence(str(publisher)),
# # # #                         "event_type": event_type,
# # # #                         "sentiment": sentiment,
# # # #                         "risk_score": score,
# # # #                         "confidence": source_confidence(str(publisher)),
# # # #                     }
# # # #                 )
# # # #         except Exception as exc:
# # # #             errors.append(f"{keyword}: {exc}")

# # # #     deduped = []
# # # #     seen = set()
# # # #     for article in all_articles:
# # # #         key = article["url"] or article["title"]
# # # #         if key and key not in seen:
# # # #             seen.add(key)
# # # #             deduped.append(article)

# # # #     score = round(float(pd.Series([a["risk_score"] for a in deduped]).mean()), 2) if deduped else 1.0
# # # #     signal = {
# # # #         "date": utc_now()[:10],
# # # #         "retailer": "Retailer",
# # # #         "region": country.upper(),
# # # #         "source": "GNews / Google News RSS",
# # # #         "signal_area": "Retail News",
# # # #         "signal_name": "news_risk_score",
# # # #         "signal_value": len(deduped),
# # # #         "risk_score": score,
# # # #         "confidence": "Medium",
# # # #         "business_impact": "Recent news can reveal competitor moves, pricing pressure, recalls, store changes, and supply-chain risk.",
# # # #         "recommended_action": "Review high-risk articles and use NVIDIA classification before executive distribution.",
# # # #         "raw_reference": f"{len(deduped)} articles",
# # # #     }
# # # #     status = "success" if deduped or not errors else "failed"
# # # #     return {"status": status, "source": "GNews", "error": "; ".join(errors), "raw": deduped, "rows": [signal], "items": deduped}


# # # # def collect_apify_trends(token: str, keywords: List[str], geo: str, time_range: str) -> Dict[str, Any]:
# # # #     if not token:
# # # #         return {"status": "skipped", "source": "Apify Trends", "error": "No Apify token provided.", "raw": None, "rows": [], "items": []}
# # # #     try:
# # # #         from apify_client import ApifyClient
# # # #     except ImportError:
# # # #         return {"status": "failed", "source": "Apify Trends", "error": "apify-client is not installed.", "raw": None, "rows": [], "items": []}
# # # #     try:
# # # #         client = ApifyClient(token)
# # # #         run_input = {"geo": geo, "searchTerms": keywords, "timeRange": time_range}
# # # #         run = client.actor("apify/google-trends-scraper").call(run_input=run_input)
# # # #         items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
# # # #     except Exception as exc:
# # # #         return {"status": "failed", "source": "Apify Trends", "error": str(exc), "raw": None, "rows": [], "items": []}

# # # #     region_rows = []
# # # #     for item in items:
# # # #         keyword = item.get("searchTerm")
# # # #         for rank, region in enumerate(item.get("interestBySubregion", []) or [], start=1):
# # # #             values = region.get("value") or []
# # # #             if values:
# # # #                 region_rows.append(
# # # #                     {
# # # #                         "keyword": keyword,
# # # #                         "region": region.get("geoName", ""),
# # # #                         "interest_score": values[0],
# # # #                         "rank": rank,
# # # #                     }
# # # #                 )
# # # #     top_score = max([float(x["interest_score"]) for x in region_rows], default=0.0)
# # # #     signal_score = round(min(10.0, max(1.0, top_score / 10.0)), 2) if top_score else 1.0
# # # #     signal = {
# # # #         "date": utc_now()[:10],
# # # #         "retailer": "Retailer",
# # # #         "region": geo,
# # # #         "source": "Apify Google Trends",
# # # #         "signal_area": "Search Demand",
# # # #         "signal_name": "search_demand_score",
# # # #         "signal_value": top_score,
# # # #         "risk_score": signal_score,
# # # #         "confidence": "Medium",
# # # #         "business_impact": "Search interest can reveal early demand shifts, promotional interest, and seasonal spikes.",
# # # #         "recommended_action": "Compare rising regions against sales, inventory, and competitor promotion calendars.",
# # # #         "raw_reference": f"{len(region_rows)} regional trend rows",
# # # #     }
# # # #     return {"status": "success", "source": "Apify Trends", "error": "", "raw": items, "rows": [signal], "items": region_rows}


# # # # def generate_fallback_brief(feature_df: pd.DataFrame, retailer: str, region: str) -> str:
# # # #     if feature_df.empty:
# # # #         return "No signals were collected. Add at least one enabled source and run the workbench again."
# # # #     strongest = feature_df.sort_values("risk_score", ascending=False).head(3)
# # # #     lines = [
# # # #         f"Market intelligence summary for {retailer} in {region}.",
# # # #         "",
# # # #         "Top signals:",
# # # #     ]
# # # #     for _, row in strongest.iterrows():
# # # #         lines.append(
# # # #             f"- {row['signal_area']}: {row['risk_score']}/10 from {row['source']}. {row['business_impact']}"
# # # #         )
# # # #     lines.extend(
# # # #         [
# # # #             "",
# # # #             "Recommended focus:",
# # # #             "Use these signals as forecast-ready external features, then validate them against internal POS, category, and inventory data before operational decisions.",
# # # #         ]
# # # #     )
# # # #     return "\n".join(lines)


# # # # def generate_nvidia_brief(api_key: str, model: str, feature_df: pd.DataFrame, articles: List[Dict[str, Any]], retailer: str, region: str) -> Tuple[str, str]:
# # # #     if not api_key:
# # # #         return generate_fallback_brief(feature_df, retailer, region), "fallback"
# # # #     payload = {
# # # #         "features": feature_df.to_dict(orient="records")[:12],
# # # #         "articles": articles[:8],
# # # #     }
# # # #     system = (
# # # #         "You are a retail market intelligence analyst. Ground your answer only in the supplied signals. "
# # # #         "Write concise executive guidance for buyers, category managers, demand planners, and supply-chain teams."
# # # #     )
# # # #     user = f"""
# # # #     Retailer: {retailer}
# # # #     Region: {region}

# # # #     Signal payload:
# # # #     {json.dumps(payload, indent=2)[:9000]}

# # # #     Produce:
# # # #     1. Top 3 insights
# # # #     2. Forecasting relevance
# # # #     3. Recommended actions
# # # #     4. Confidence and limitations
# # # #     """
# # # #     ok, data, msg = safe_request(
# # # #         NVIDIA_CHAT_URL,
# # # #         method="POST",
# # # #         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
# # # #         json_body={
# # # #             "model": model,
# # # #             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
# # # #             "temperature": 0.25,
# # # #             "max_tokens": 900,
# # # #         },
# # # #         timeout=60,
# # # #     )
# # # #     if not ok:
# # # #         return generate_fallback_brief(feature_df, retailer, region), f"fallback: {msg}"
# # # #     content = data.get("choices", [{}])[0].get("message", {}).get("content")
# # # #     content_text = str(content or "").strip()
# # # #     return content_text or generate_fallback_brief(feature_df, retailer, region), "nvidia"


# # # # def render_metric_card(title: str, value: str, note: str, confidence: str = "") -> None:
# # # #     pill = f'<span class="pill {confidence_class(confidence)}">{confidence}</span>' if confidence else ""
# # # #     html = (
# # # #         '<div class="metric-card">'
# # # #         f'<div class="metric-label">{title}</div>'
# # # #         f'<div class="metric-value">{value}</div>'
# # # #         f"{pill}"
# # # #         f'<div class="metric-note">{note}</div>'
# # # #         "</div>"
# # # #     )
# # # #     st.markdown(
# # # #         html,
# # # #         unsafe_allow_html=True,
# # # #     )


# # # # def compute_composite_scores(feature_df: pd.DataFrame) -> Dict[str, float]:
# # # #     if feature_df.empty:
# # # #         return {"Market Opportunity": 0.0, "Market Risk": 0.0, "Forecast Impact": 0.0}
# # # #     area_scores: Dict[str, float] = {}
# # # #     for _, row in feature_df.iterrows():
# # # #         if pd.isna(row.get("risk_score")):
# # # #             continue
# # # #         area = str(row["signal_area"])
# # # #         area_scores[area] = max(area_scores.get(area, 0.0), float(row["risk_score"]))
# # # #     opportunity = max(
# # # #         area_scores.get("Retail News", 0.0),
# # # #         area_scores.get("Search Demand", 0.0),
# # # #         area_scores.get("Category CPI", 0.0) * 0.7,
# # # #     )
# # # #     risk = max(
# # # #         area_scores.get("Product Recalls", 0.0),
# # # #         area_scores.get("Inflation", 0.0),
# # # #         area_scores.get("Category CPI", 0.0),
# # # #     )
# # # #     impact = min(10.0, (opportunity * 0.45) + (risk * 0.45) + (len(feature_df) * 0.25))
# # # #     return {
# # # #         "Market Opportunity": round(opportunity, 2),
# # # #         "Market Risk": round(risk, 2),
# # # #         "Forecast Impact": round(impact, 2),
# # # #     }


# # # # def build_recommended_actions(feature_df: pd.DataFrame, results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
# # # #     actions = []
# # # #     if feature_df.empty:
# # # #         return [{"label": "Setup", "title": "Run signal sources", "body": "Enable at least one source to generate forecast-ready rows."}]
# # # #     top_rows = feature_df.sort_values("risk_score", ascending=False).head(3)
# # # #     for _, row in top_rows.iterrows():
# # # #         actions.append(
# # # #             {
# # # #                 "label": str(row["signal_area"]),
# # # #                 "title": str(row["signal_name"]).replace("_", " ").title(),
# # # #                 "body": str(row["recommended_action"]),
# # # #             }
# # # #         )
# # # #     apify_result = results.get("apify")
# # # #     if apify_result and apify_result.get("status") == "failed":
# # # #         actions.append(
# # # #             {
# # # #                 "label": "Apify",
# # # #                 "title": "Search demand skipped",
# # # #                 "body": "Apify connected but failed during collection. If usage limit is exceeded, rerun after quota reset or keep MVP on public sources.",
# # # #             }
# # # #         )
# # # #     return actions[:4]


# # # # def render_action_card(label: str, title: str, body: str) -> None:
# # # #     html = (
# # # #         '<div class="action-card">'
# # # #         f'<div class="action-label">{escape(label)}</div>'
# # # #         f'<div class="action-title">{escape(title)}</div>'
# # # #         f'<div class="action-body">{escape(body)}</div>'
# # # #         "</div>"
# # # #     )
# # # #     st.markdown(html, unsafe_allow_html=True)


# # # # def render_source_tile(name: str, status: str, detail: str) -> None:
# # # #     status_class = "pill-high" if status == "Active" else "pill-medium" if status == "Optional" else "pill-low"
# # # #     html = (
# # # #         '<div class="source-tile">'
# # # #         f'<div class="source-name">{escape(name)} <span class="pill {status_class}" style="margin-left:6px;margin-top:0;">{escape(status)}</span></div>'
# # # #         f'<div class="source-meta">{escape(detail)}</div>'
# # # #         "</div>"
# # # #     )
# # # #     st.markdown(html, unsafe_allow_html=True)


# # # # def render_workflow_strip() -> None:
# # # #     steps = [
# # # #         ("01", "Collect APIs"),
# # # #         ("02", "Clean records"),
# # # #         ("03", "Score signals"),
# # # #         ("04", "Generate brief"),
# # # #         ("05", "Export features"),
# # # #     ]
# # # #     html = "<div class='workflow'>" + "".join(
# # # #         f"<div class='workflow-step'><span>{num}</span>{escape(label)}</div>" for num, label in steps
# # # #     ) + "</div>"
# # # #     st.markdown(html, unsafe_allow_html=True)


# # # # def render_score_chart(feature_df: pd.DataFrame) -> None:
# # # #     if feature_df.empty:
# # # #         st.info("No feature rows yet.")
# # # #         return
# # # #     fig = go.Figure(
# # # #         go.Bar(
# # # #             x=feature_df["risk_score"],
# # # #             y=feature_df["signal_area"],
# # # #             orientation="h",
# # # #             marker_color=["#22C55E" if x < 5 else "#F59E0B" if x < 8 else "#EF4444" for x in feature_df["risk_score"]],
# # # #             text=feature_df["risk_score"],
# # # #             textposition="auto",
# # # #         )
# # # #     )
# # # #     fig.update_layout(
# # # #         height=280,
# # # #         margin={"l": 10, "r": 20, "t": 10, "b": 10},
# # # #         xaxis={"range": [0, 10], "title": "Score"},
# # # #         yaxis={"title": ""},
# # # #         plot_bgcolor="#FFFFFF",
# # # #         paper_bgcolor="#FFFFFF",
# # # #     )
# # # #     st.plotly_chart(fig, width="stretch")


# # # # def parse_lines(text: str) -> List[str]:
# # # #     return [line.strip() for line in text.splitlines() if line.strip()]


# # # # with st.sidebar:
# # # #     st.title("Workbench Setup")
# # # #     st.caption("Credentials are used only for this Streamlit session.")

# # # #     nvidia_key = st.text_input("NVIDIA API key", value=os.getenv("NVIDIA_API_KEY", ""), type="password")
# # # #     nvidia_model = st.text_input("NVIDIA model", value=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL))
# # # #     apify_token = st.text_input("Apify token", value=os.getenv("APIFY_API_TOKEN", ""), type="password")
# # # #     bls_key = st.text_input("BLS API key optional", value=os.getenv("BLS_API_KEY", ""), type="password")

# # # #     st.markdown('<div class="small-header">Retail Context</div>', unsafe_allow_html=True)
# # # #     retailer = st.text_input("Company / Retailer", value="", placeholder="Optional: enter a company or retailer")
# # # #     region = st.text_input("Region", value="US")
# # # #     country = st.selectbox("News country", ["US", "CA", "GB", "AE", "IN"], index=0)
# # # #     language = st.selectbox("News language", ["en", "es", "fr", "ar", "hi"], index=0)

# # # #     st.markdown('<div class="small-header">Signal Sources</div>', unsafe_allow_html=True)
# # # #     use_gnews = st.checkbox("Retail news via GNews/RSS", value=True)
# # # #     use_bls = st.checkbox("Inflation via BLS CPI", value=True)
# # # #     use_fda = st.checkbox("Product recalls via openFDA", value=True)
# # # #     use_apify = st.checkbox("Search demand via Apify Trends", value=False)

# # # #     st.markdown('<div class="small-header">Run Controls</div>', unsafe_allow_html=True)
# # # #     validate_button = st.button("Validate credentials", width="stretch")
# # # #     run_button = st.button("Run intelligence", type="primary", width="stretch")


# # # # DEFAULT_NEWS_KEYWORDS = "\n".join(
# # # #     [
# # # #         "Retailer inflation",
# # # #         "Retailer prices",
# # # #         "Retailer store closures",
# # # #         "Retailer recall",
# # # #         "discount retail tariffs",
# # # #         "Dollar General promotion",
# # # #     ]
# # # # )
# # # # DEFAULT_TRENDS_KEYWORDS = "\n".join(
# # # #     [
# # # #         "Retailer sales",
# # # #         "Retailer coupons",
# # # #         "Retailer near me",
# # # #         "Retailer groceries",
# # # #         "cheap groceries",
# # # #     ]
# # # # )

# # # # st.session_state.setdefault("news_keywords_text", DEFAULT_NEWS_KEYWORDS)
# # # # st.session_state.setdefault("trends_keywords_text", DEFAULT_TRENDS_KEYWORDS)
# # # # st.session_state.setdefault("gnews_period", "7d")
# # # # st.session_state.setdefault("max_news", 24)
# # # # st.session_state.setdefault("fda_query", "product_description:(snacks OR candy OR beverages)")
# # # # st.session_state.setdefault("fda_limit", 8)
# # # # st.session_state.setdefault("apify_geo", "US")
# # # # st.session_state.setdefault("apify_time_range", "today 1-m")
# # # # st.session_state.setdefault("workbench_view", "Configure")
# # # # if st.session_state.pop("force_results_view", False):
# # # #     st.session_state["workbench_view"] = "Results"

# # # # st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
# # # # st.markdown(
# # # #     "<div class='topbar'>"
# # # #     "<div class='tt'>Market Intelligence <span>/ External Signals</span></div>"
# # # #     "<div class='tbadge'>AI Workbench</div>"
# # # #     "<div style='margin-left:auto;display:flex;align-items:center;gap:8px;'>"
# # # #     "<span class='ldot'></span>"
# # # #     "<span style='font-size:10px;color:var(--t3);font-weight:800;'>Live API Mode</span>"
# # # #     "<div style='width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,var(--odk),var(--or));display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;color:#fff;'>DT</div>"
# # # #     "</div></div>",
# # # #     unsafe_allow_html=True,
# # # # )

# # # # hero_left, hero_right = st.columns([2.2, 0.9], vertical_alignment="center")
# # # # with hero_left:
# # # #     st.markdown(
# # # #         "<div class='hero-shell'>"
# # # #         "<div class='hero-kicker'><span class='ldot'></span> External Signal Layer</div>"
# # # #         "<h1 class='hero-title'>Retailer Market Intelligence Command Center</h1>"
# # # #         "<p class='hero-copy'>A retail-grade workbench that turns news, CPI, recalls, search demand, and API health into forecast-ready features, composite risk scores, and buyer actions.</p>"
# # # #         "</div>",
# # # #         unsafe_allow_html=True,
# # # #     )
# # # # with hero_right:
# # # #     active_sources = sum([use_gnews, use_bls, use_fda, use_apify])
# # # #     st.markdown(
# # # #         "<div class='hero-side'>"
# # # #         "<div class='hero-side-label'>Run Profile</div>"
# # # #         f"<div class='hero-side-row'><span>Retailer</span><strong>{escape(retailer)}</strong></div>"
# # # #         f"<div class='hero-side-row'><span>Region</span><strong>{escape(region)}</strong></div>"
# # # #         f"<div class='hero-side-row'><span>Sources</span><strong>{active_sources} enabled</strong></div>"
# # # #         f"<div class='hero-side-row'><span>LLM</span><strong>{'NVIDIA' if nvidia_key.strip() else 'Fallback'}</strong></div>"
# # # #         "</div>",
# # # #         unsafe_allow_html=True,
# # # #     )

# # # # view = st.segmented_control(
# # # #     "Workbench view",
# # # #     ["Configure", "Results", "Raw Data"],
# # # #     required=True,
# # # #     label_visibility="collapsed",
# # # #     key="workbench_view",
# # # #     width="content",
# # # # )

# # # # if view == "Configure":
# # # #     st.markdown('<div class="small-header">Signal Source Stack</div>', unsafe_allow_html=True)
# # # #     src_cols = st.columns(4)
# # # #     source_specs = [
# # # #         ("GNews/RSS", "Active" if use_gnews else "Off", "Retail articles, sentiment hints, source confidence."),
# # # #         ("BLS CPI", "Active" if use_bls else "Off", "Headline and category inflation pressure features."),
# # # #         ("openFDA", "Active" if use_fda else "Off", "Recall severity, UPC extraction, SKU-match prep."),
# # # #         ("Apify Trends", "Optional" if use_apify else "Off", "Search interest and regional demand spikes."),
# # # #     ]
# # # #     for col, spec in zip(src_cols, source_specs):
# # # #         with col:
# # # #             render_source_tile(*spec)

# # # #     left, right = st.columns([1.15, 0.85])
# # # #     with left:
# # # #         st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# # # #         st.subheader("Signal Keywords")
# # # #         st.text_area("GNews keywords", key="news_keywords_text", height=170)
# # # #         st.text_area("Apify Google Trends keywords", key="trends_keywords_text", height=150)
# # # #         st.markdown("</div>", unsafe_allow_html=True)
# # # #     with right:
# # # #         st.markdown('<div class="console-panel">', unsafe_allow_html=True)
# # # #         st.subheader("Collector Settings")
# # # #         st.selectbox("GNews period", ["1d", "7d", "30d", "3m"], key="gnews_period")
# # # #         st.slider("Max news results", min_value=5, max_value=60, step=1, key="max_news")
# # # #         st.text_input("FDA recall search", key="fda_query")
# # # #         st.slider("FDA recall limit", min_value=1, max_value=25, key="fda_limit")
# # # #         st.text_input("Apify geo", key="apify_geo")
# # # #         st.selectbox("Apify time range", ["today 7-d", "today 1-m", "today 3-m", "today 12-m"], key="apify_time_range")
# # # #         st.markdown("</div>", unsafe_allow_html=True)

# # # #     st.markdown('<div class="small-header">Agent Flow</div>', unsafe_allow_html=True)
# # # #     render_workflow_strip()


# # # # if validate_button:
# # # #     with st.spinner("Validating credentials..."):
# # # #         n_ok, n_msg = validate_nvidia(nvidia_key.strip(), nvidia_model.strip())
# # # #         a_ok, a_msg = validate_apify(apify_token.strip())
# # # #     st.session_state["validation"] = {"nvidia": (n_ok, n_msg), "apify": (a_ok, a_msg)}

# # # # if "validation" in st.session_state:
# # # #     n_ok, n_msg = st.session_state["validation"]["nvidia"]
# # # #     a_ok, a_msg = st.session_state["validation"]["apify"]
# # # #     st.info(f"NVIDIA: {'Connected' if n_ok else 'Not connected'} - {n_msg}")
# # # #     st.info(f"Apify: {'Connected' if a_ok else 'Not connected'} - {a_msg}")


# # # # if run_button:
# # # #     news_keywords = parse_lines(st.session_state.get("news_keywords_text", DEFAULT_NEWS_KEYWORDS))
# # # #     trends_keywords = parse_lines(st.session_state.get("trends_keywords_text", DEFAULT_TRENDS_KEYWORDS))
# # # #     results: Dict[str, Dict[str, Any]] = {}
# # # #     all_rows: List[Dict[str, Any]] = []
# # # #     all_articles: List[Dict[str, Any]] = []

# # # #     progress = st.progress(0, text="Starting collectors")
# # # #     steps = [
# # # #         ("gnews", use_gnews),
# # # #         ("bls", use_bls),
# # # #         ("fda", use_fda),
# # # #         ("apify", use_apify),
# # # #     ]
# # # #     active_steps = [step for step in steps if step[1]]
# # # #     total = max(1, len(active_steps))
# # # #     completed = 0

# # # #     if use_gnews:
# # # #         progress.progress(completed / total, text="Collecting retail news")
# # # #         results["gnews"] = collect_gnews(
# # # #             news_keywords,
# # # #             country,
# # # #             language,
# # # #             st.session_state.get("gnews_period", "7d"),
# # # #             int(st.session_state.get("max_news", 24)),
# # # #         )
# # # #         all_rows.extend(results["gnews"].get("rows", []))
# # # #         all_articles.extend(results["gnews"].get("items", []))
# # # #         completed += 1

# # # #     if use_bls:
# # # #         progress.progress(completed / total, text="Collecting CPI inflation")
# # # #         results["bls"] = collect_bls_cpi(bls_key.strip())
# # # #         all_rows.extend(results["bls"].get("rows", []))
# # # #         completed += 1

# # # #     if use_fda:
# # # #         progress.progress(completed / total, text="Collecting FDA recalls")
# # # #         results["fda"] = collect_fda_recalls(
# # # #             st.session_state.get("fda_query", "product_description:(snacks OR candy OR beverages)"),
# # # #             int(st.session_state.get("fda_limit", 8)),
# # # #         )
# # # #         all_rows.extend(results["fda"].get("rows", []))
# # # #         completed += 1

# # # #     if use_apify:
# # # #         progress.progress(completed / total, text="Collecting Google Trends via Apify")
# # # #         results["apify"] = collect_apify_trends(
# # # #             apify_token.strip(),
# # # #             trends_keywords,
# # # #             st.session_state.get("apify_geo", "US"),
# # # #             st.session_state.get("apify_time_range", "today 1-m"),
# # # #         )
# # # #         all_rows.extend(results["apify"].get("rows", []))
# # # #         completed += 1

# # # #     progress.progress(1.0, text="Generating intelligence brief")
# # # #     feature_df = pd.DataFrame(all_rows)
# # # #     if not feature_df.empty:
# # # #         feature_df["retailer"] = retailer
# # # #         feature_df["region"] = feature_df["region"].replace({"US": region}) if region else feature_df["region"]
# # # #     brief, brief_source = generate_nvidia_brief(nvidia_key.strip(), nvidia_model.strip(), feature_df, all_articles, retailer, region)

# # # #     st.session_state["run"] = {
# # # #         "timestamp": utc_now(),
# # # #         "results": results,
# # # #         "feature_df": feature_df,
# # # #         "articles": all_articles,
# # # #         "brief": brief,
# # # #         "brief_source": brief_source,
# # # #     }
# # # #     st.session_state["force_results_view"] = True
# # # #     progress.empty()
# # # #     st.rerun()


# # # # if view == "Results":
# # # #     run = st.session_state.get("run")
# # # #     if not run:
# # # #         st.markdown(
# # # #             "<div class='empty-console'>"
# # # #             "<div>"
# # # #             "<div class='hero-kicker'>Ready for first run</div>"
# # # #             "<div class='empty-title'>No intelligence run yet.</div>"
# # # #             "<div class='empty-body'>Configure the signal stack, then click <strong>Run intelligence</strong> in the sidebar. The agent will collect public signals, normalize them into feature rows, score the risk/opportunity surface, and produce an executive brief.</div>"
# # # #             "</div>"
# # # #             "<div class='hero-side' style='min-width:260px;'>"
# # # #             "<div class='hero-side-label'>MVP Output</div>"
# # # #             "<div class='hero-side-row'><span>Feature rows</span><strong>CSV / JSON</strong></div>"
# # # #             "<div class='hero-side-row'><span>Scores</span><strong>0-10</strong></div>"
# # # #             "<div class='hero-side-row'><span>Brief</span><strong>NVIDIA / fallback</strong></div>"
# # # #             "</div>"
# # # #             "</div>",
# # # #             unsafe_allow_html=True,
# # # #         )
# # # #         render_workflow_strip()
# # # #     else:
# # # #         feature_df = run["feature_df"]
# # # #         st.markdown(
# # # #             f"<div class='note-box'><strong>Last run:</strong> {escape(run['timestamp'])} UTC &nbsp; | &nbsp; <strong>Brief source:</strong> {escape(str(run['brief_source']))}</div>",
# # # #             unsafe_allow_html=True,
# # # #         )
# # # #         cols = st.columns(4)
# # # #         if feature_df.empty:
# # # #             for col, title in zip(cols, ["Signals", "Avg Score", "Highest Score", "Brief"]):
# # # #                 with col:
# # # #                     render_metric_card(title, "0", "No successful feature rows yet.")
# # # #         else:
# # # #             avg_score = round(float(feature_df["risk_score"].mean()), 2)
# # # #             top = feature_df.sort_values("risk_score", ascending=False).iloc[0]
# # # #             composite_scores = compute_composite_scores(feature_df)
# # # #             with cols[0]:
# # # #                 render_metric_card("Signals", str(len(feature_df)), "Forecast-ready rows generated.")
# # # #             with cols[1]:
# # # #                 render_metric_card("Average Score", str(avg_score), f"{risk_band(avg_score)} overall signal intensity.")
# # # #             with cols[2]:
# # # #                 render_metric_card("Top Signal", str(top["risk_score"]), str(top["signal_area"]), str(top["confidence"]))
# # # #             with cols[3]:
# # # #                 render_metric_card("News Articles", str(len(run["articles"])), "Deduplicated retail news items.")

# # # #             st.markdown('<div class="small-header">Composite Agent Scores</div>', unsafe_allow_html=True)
# # # #             cscore_cols = st.columns(3)
# # # #             for col, (name, score) in zip(cscore_cols, composite_scores.items()):
# # # #                 with col:
# # # #                     render_metric_card(name, str(score), f"{risk_band(score)} priority for planning.")

# # # #             st.markdown('<div class="small-header">Recommended Actions</div>', unsafe_allow_html=True)
# # # #             action_cols = st.columns(4)
# # # #             for col, action in zip(action_cols, build_recommended_actions(feature_df, run["results"])):
# # # #                 with col:
# # # #                     render_action_card(action["label"], action["title"], action["body"])

# # # #             st.markdown('<div class="small-header">Signal Scores</div>', unsafe_allow_html=True)
# # # #             render_score_chart(feature_df)

# # # #             st.markdown('<div class="small-header">Forecast Feature Table</div>', unsafe_allow_html=True)
# # # #             st.dataframe(feature_df, width="stretch", hide_index=True)

# # # #             csv_data = feature_df.to_csv(index=False).encode("utf-8")
# # # #             json_data = json.dumps(feature_df.to_dict(orient="records"), indent=2).encode("utf-8")
# # # #             c1, c2 = st.columns([1, 1])
# # # #             with c1:
# # # #                 st.download_button("Download forecast_features.csv", csv_data, "forecast_features.csv", "text/csv", width="stretch")
# # # #             with c2:
# # # #                 st.download_button("Download normalized_signals.json", json_data, "normalized_signals.json", "application/json", width="stretch")

# # # #         st.markdown('<div class="small-header">Executive Brief</div>', unsafe_allow_html=True)
# # # #         st.markdown(f'<div class="brief-box">{run["brief"].replace(chr(10), "<br>")}</div>', unsafe_allow_html=True)


# # # # if view == "Raw Data":
# # # #     run = st.session_state.get("run")
# # # #     if not run:
# # # #         st.markdown(
# # # #             "<div class='empty-console'>"
# # # #             "<div>"
# # # #             "<div class='hero-kicker'>Raw Evidence</div>"
# # # #             "<div class='empty-title'>Collector payloads will appear after a run.</div>"
# # # #             "<div class='empty-body'>This view is intentionally evidence-first: GNews articles, CPI JSON, FDA recall records, Apify errors, and cleaned item tables stay inspectable for validation.</div>"
# # # #             "</div>"
# # # #             "</div>",
# # # #             unsafe_allow_html=True,
# # # #         )
# # # #     else:
# # # #         for name, result in run["results"].items():
# # # #             status = result.get("status", "unknown")
# # # #             label = f"{name.upper()} - {status}"
# # # #             with st.expander(label, expanded=False):
# # # #                 if result.get("error"):
# # # #                     st.warning(result["error"])
# # # #                 if result.get("items"):
# # # #                     st.dataframe(pd.DataFrame(result["items"]), width="stretch", hide_index=True)
# # # #                 raw_preview = result.get("raw")
# # # #                 if raw_preview is not None:
# # # #                     st.code(json.dumps(raw_preview, indent=2, default=str)[:7000], language="json")


# # # # st.markdown(
# # # #     """
# # # #     <p class="subtle">
# # # #     Note: Google Trends values are relative indexes, GNews is a lightweight news signal, and recall data should be matched
# # # #     against internal SKU/UPC and inventory records before operational decisions.
# # # #     </p>
# # # #     """,
# # # #     unsafe_allow_html=True,
# # # # )

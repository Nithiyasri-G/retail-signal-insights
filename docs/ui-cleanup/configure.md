# UI cleanup log: Configure page

Status: first cleanup pass done (layout only, no colour changes). Next pages are cleaned one at a time, in the same style.

## Principles agreed so far
- Layout and spacing only. Do not change colours without asking (an earlier blue/navy restyle was reverted).
- The audience is customer demos and stakeholders: show less, use plain language, no engineering names by default.
- Colour is for state only (status dots, ready/attention text). The orange brand accent is unchanged.
- Keep the demo walkthrough order from `docs/customer-demo-runbook.md`.

## Product name
**Retail Signal Intelligence** (was "Market Intelligence Command Center"). Used in the header, browser tab title, `app.py` docstring and footer. On Results, the "Results Command Center" label became "Results" and the "Command Center" tab became "Decisions".

## Global shell (applies to every page)
- **Header bar** (`appbar` in `ui/layout.py`): product name, then Retailer, Region, Sources and LLM as an inline strip, then a status chip ("No run yet" or "Last run 06 Oct, 11:30 UTC", read from `run["timestamp"]`). White card, no hero, no marketing paragraph (the description is a hover tooltip on the title).
- **Navigation:** `st.segmented_control(key="workbench_view")` styled as a flat underline bar. Order follows the runbook: Configure, Results, Operational Impact Center (with a What-if scenario tab inside it), Demand Planner, Evidence Audit, Signal Search, Raw Data (admin only).
- **Admin view toggle** (`st.toggle(key="admin_view")`, default off), top right of the nav row. Sets `ctx.admin_view`.
- **Tabs inside pages** (`st.tabs`) use the same underline style (global CSS, so Results tabs are affected too).
- **Fonts:** system stack (Segoe UI first); the Google Fonts import was removed.
- **Footer:** one quiet line. The Google Trends note moved to Signal Search.
- **Focus ring:** visible keyboard focus on all controls.
- **Theme:** `.streamlit/config.toml` (light, original orange primary).

## Configure page, top to bottom
1. **Run readiness card** (`st.container(key="readiness_card")`)
   - Summary line: "Ready to run" (green) or "Needs attention: <items>" (amber).
   - Check rows (Retailer, Region, Public sources, News keywords): shown only in Admin view or when something is missing.
   - Line: "Executive brief: AI-generated" or "Executive brief: generated locally" (no warning styling).
   - **Pointer line to the single **Run intelligence** button in the left panel (the duplicate button here was removed). Adds a non-blocking "Weather coverage" row listing the NOAA states.
2. **Data sources card** (`st.container(key="sources_card")`)
   - Stakeholder view: Source, Status (dot + text), What it tells you.
   - Admin view adds Forecast feature, Owner, KPI.
   - Sources: GNews/RSS, BLS CPI, openFDA, NOAA Weather.
3. **Notice bar:** "Based on public external signals only. Internal sales and inventory data are not connected. Raw payloads are in Evidence Audit."
4. **Advanced settings** (`st.expander`, collapsed unless Admin view), with three tabs:
   - **Scope:** "News keywords" heading, one caption, the keywords text area (`news_keywords_text`).
   - **Collector Tuning:** News (GNews period, max news results) and Recalls and weather (FDA query and limit, NOAA area and limit). Help tooltips unchanged.
   - **Governance:** one traceability caption, "How a run works" (workflow strip) and "Glossary" cards.

## Removed from Configure
Hero banner, "External Signal Layer" pill, "Run Profile" card, "90 configuration completeness" tile, "Run Blueprint / Controls" panels, three striped control cards, four routing cards, five striped keyword-guidance cards, info boxes above each tab, large `st.subheader` headings.

## Files touched
- `market_intelligence/ui/views/configure.py` (page content, `_section_title` helper)
- `market_intelligence/ui/layout.py` (header bar, nav order, admin toggle, `_last_run_status`)
- `market_intelligence/ui/sidebar.py` (reads `run_requested`)
- `market_intelligence/ui/styles/app.css` (appbar, nav, cards, table, tabs, `section-title`)
- `market_intelligence/ui/theme.py`, `app.py`, `ui/footer.py`, `ui/views/results.py`, `ui/views/results_page.py`, `ui/views/signal_search.py` (name and copy)
- `.streamlit/config.toml` (new)

## Constraints to keep (tests)
- Keep `st.segmented_control` and its option labels (`tests/test_application_smoke.py`).
- `app.py` under 100 lines; Streamlit imported only in `ui/` (`tests/test_architecture.py`).
- The retailer name of another retailer must not appear in any `.py` or `.css` file (`tests/ui/test_client_presentation.py`).
- Full suite: 195 tests passing at the end of this pass.

## Known leftovers (to do in later passes)
- Glossary cards and the numbered "How a run works" steps are still bordered boxes.
- Pipeline Status panel (shown while a run is in progress) still uses tiny uppercase labels and a glow.
- Sidebar: heavy expander borders, 8px summary labels, Credentials still visible to stakeholders.
- The new Run button was seen starting the pipeline; a full run to completion was not observed in testing.
- Dead CSS from the removed hero and cards remains in `app.css` (about 1,300 lines).
- Other pages (Results, Operational Impact Center, Demand Planner, Evidence Audit, Signal Search, Raw Data) have not all been reviewed in a browser.

## Data collection changes made alongside the Configure work (not visual)

### News keywords (Scope tab defaults)
Defaults are built by `build_default_news_keywords(retailer, states)` in `signals/search.py`. 16 searches, grouped:
- Company, quoted for exact matching: `"<Retailer>"` recall, tariffs, price increase, store closures, distribution center.
- Supply chain and wider market: retail import tariffs, Red Sea shipping disruption, Middle East conflict oil prices supply chain, port strike shipping delays, freight rates trucking retail, consumer spending discount retailers, grocery food inflation.
- Weather near the stores: two searches per state taken from the NOAA weather areas (currently TX, GA): "<State> severe weather storms" and "<State> flooding hurricane". Unknown state codes are skipped.
- No other retailer is named in code (a test forbids it). Competitor names are typed into the keyword box at run time.
- "Max news results" defaults to 48 (was 24), so 16 keywords still return about 3 articles each.
- The news classifier (`collectors/live_news.py`) gained two categories so these articles score as risk instead of a neutral 3.5: `weather_disruption` (6.0) and `supply_disruption` (6.5), matched on whole words.
- A browser tab already open keeps its old keywords until the page is refreshed.
- Weakest defaults seen in a live test: `"<Retailer>" price increase` and `"<Retailer>" distribution center` (mostly investor and blog content). First candidates to drop.

### GNews collector
- The `gnews` Python package has no timeout and could hang a run on "Collecting and deduplicating retail news". The Google News RSS feed (15 s timeout) is now used first; the package is a fallback limited to 25 s.
- Keywords are fetched four at a time; 16 keywords take about 10 s.

### Public API resilience (`infra/http.py`)
- `safe_request` retries read-only GET requests up to 3 times (1.5 s, then 3 s apart) on HTTP 429, 500, 502, 503, 504, connection errors and timeouts. 4xx errors other than 429 and all POST requests are never retried.
- Error messages are short ("api.fda.gov temporarily unavailable (HTTP 500)") instead of the full URL with the query string. The openFDA collector's "404 means no matching records" rule still works.
- Triggered by a one-off openFDA HTTP 500 that did not reproduce a few minutes later (same query returned 20 records in under 2 s).

### Tests added
- `tests/collectors/test_news_keywords.py` (keyword builder, classifier)
- `tests/infra/test_http_retry.py` (retry, give-up message, no retry on 4xx or POST)

## Signal Search (Google Trends) hidden by default
- Signal Search is a paid Google Trends lookup through Apify. Without an Apify account it cannot work, so the tab, the "Apify token" field in Credentials, the Apify line in the credential status and the sidebar sentence about it are hidden unless `ENABLE_SIGNAL_SEARCH=true` (new `signal_search_enabled()` in `config/flags.py`; documented in `.env.example`, `RUNNING.md` and the demo runbook). The view and collector code are untouched, so setting the flag restores everything.
- A browser session still holding the hidden view falls back to Configure. `tests/test_signal_search_flag.py` covers both states; the two view lists in the existing tests no longer visit Signal Search.
- The main run never called Apify, so Run intelligence and the pipeline are unaffected.

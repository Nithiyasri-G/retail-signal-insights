# UI cleanup log: Operational Impact Center

Status: redesigned (second pass). The first pass only trimmed text and left the old structure, so the page was rethought as an incident inbox. Layout only, no colour changes. Checked in a browser against a saved real run (08 Oct 2026, 04:56 UTC) and in the empty (no run in session) state.

## Purpose
An incident inbox: here are the weather alerts and recalls, open one to see its impact. The table is the main element; everything else is quiet.

## Decisions (agreed with the product owner)
- Remove the three summary cards; counts go in the tab labels.
- Open an incident by clicking a row (replaces the long "Select incident to review" dropdown).
- One context line with a help icon carries the synthetic-data notice and the long "how this works" text.
- Detail tables: keep all columns, set widths (stakeholder column trimming not wanted).

## New structure
1. Header: page title on the left; two status dots on the right (NOAA weather, openFDA recalls; grey when not run, green on success, red on failure; hover for the status).
2. One context line: "Live NOAA and openFDA alerts assessed against the store and distribution-centre network. Internal operations data is illustrative." with a help icon holding the method text.
3. Tabs with counts: "Weather (12)", "Product recall (21)". Count is the live incidents of the latest run, or the earlier incidents when there are none.
4. Inbox toolbar: filters in one row (labels collapsed, options read "All priorities" and so on) and Export CSV at the right (Weather).
5. Table with row selection and explicit column widths. The highest-priority incident is shown by default; the caption says so.
6. Empty state: when there is no live incident the earlier incidents are the table (no blue alert boxes). With no run in the session, one line says so at the top.
7. "Earlier incidents": a second table under the live one (only when live incidents exist); a row click opens its detail.
8. Incident detail: header (type, affected scope, incident ID, priority), one provenance line, then the existing tabs. Impact opens with a bordered row of key numbers instead of a two-column table. Store, product and DC filters sit in a collapsed "Filter by store, product or DC" section.

## Files
- `ui/views/operational_impact/center.py` (header, context line, tab counts)
- `ui/views/operational_impact/weather_tab.py`, `recall_tab.py` (toolbar, row-click tables, earlier incidents)
- `ui/views/operational_impact/incident_detail.py` (detail header, stat row, filter expander)
- `ui/views/operational_impact/inbox.py` (new: `incident_table`, `stat_row`)
- `ui/styles/app.css` (`page-head`, `detail-head`, `stat-row`)
- `operational_status_line` in `ui/operational_impact.py` is no longer used by the page but stays (tested in `tests/ui/test_view_models.py`).

## Known leftovers
- The "No match / insufficient evidence" count is no longer on the page (it was a card); it is not yet a filter value.
- The Executable Order Qty and Remaining Gap cards are still in the DC Replenishment section lower in the Impact tab; not moved up.
- Wide store, product and DC tables still scroll sideways; the inbox tables have explicit widths but several columns are still wide text.
- Evidence, Recommended Actions and Decision History tabs inside the detail were not reviewed.
- POC Scenario Lab shares the detail view and has its own yellow warning box; not reviewed.
- Row-click selection could not be driven by the existing AppTest suites (they do not cover this page); verified in the browser only.

## Third pass: filters, row colour, visibility
- Every incident table (weather and recall, current and earlier) has one filter row: free-text search across the whole row, Priority, event type (weather event or recall class), plus Actionability and Demand Basis for live weather, Source for live recalls. Live weather also has Export CSV in the same row. Results are ordered High, Medium, Low (stable within a level).
- The whole row is tinted by priority: red for High, amber for Medium, green for Low; rows with no priority (for example "No Store match") stay plain. Implemented by `row_level_column` in `style_operational_dataframe` (`ui/components.py`), applied before the per-cell colours so the Priority cell keeps its own emphasis.
- Priority is now the first column (identifiers such as incident reference, recall number and incident ID moved to the end) so it is never scrolled out of view.
- Tables show every row (35 px per row, capped at 620 px, then they scroll inside) instead of cutting off after about ten rows.
- Files: `ui/views/operational_impact/inbox.py` (`incident_filters`, `incident_table`), `weather_tab.py`, `recall_tab.py`, `ui/components.py`.

## Fourth pass: incident finder, compact tables
- The free-text search box is now "Find an incident", a searchable dropdown of the incidents left by the other filters (label: event, area, store exposure, reference; recalls: product, firm, class, numbers). Typing narrows the list; the chosen incident is pinned to the first row (`pin_selected` in `ui/views/operational_impact/inbox.py`) and, being first, opens in the detail below. Because the label includes store exposure, typing a city such as "Savannah" still finds the alerts that reach that store.
- Tables are half the height (cap 310 px, about eight rows, then they scroll) and narrower: stakeholders see five or six columns (weather: Priority, Event, Area, Alert Window, Store Exposure (Store Match is Admin view only, as it overlaps Store Exposure); recall: Priority, Date, Product, Firm, Class, Affected Stores; earlier incidents: five or six). Admin view shows all columns. Export CSV still contains every column. Column widths are fixed pixel values so they fit without sideways scrolling.

## Fifth pass: all columns back
- The compact column sets from the fourth pass were reversed on request: every column is shown again for everyone (weather, recall, current and earlier). The incident number sits right after Priority (weather: Incident Reference; recall: Incident ID then Recall Number) so it is visible without scrolling. The remaining columns follow and scroll sideways. The half-height table, the incident dropdown with pinning, the filters and the row colours are unchanged.

## Sixth pass: incident detail
- Header (`render_incident_detail_panel` in `ui/views/operational_impact/incident_detail.py`): the event name is the title (recalls: "Product Recall · Class I"); a red/amber/green priority chip, an "Active now, ends in 15h" chip (weather), the incident ID and the scope follow. The method note "(NOAA UGC/FIPS join)" is dropped from the headline (it stays in the Evidence tab).
- Impact tab, weather: four large headline numbers (Stores affected, Stores with an inventory gap, Units to order, Remaining gap after DC allocation; the last is red when above zero) and one facts line (products with a gap, stores with route risk, staffing available, comparable cases). The two summary cards that used to sit above the DC table were removed; their totals are the Units to order and Remaining gap numbers. These headline totals cover the whole incident; the table totals follow the store/product/DC filters.
- Store/product/DC filters are a single inline row of four dropdowns instead of an expander.
- Store Demand & Inventory table: decision columns first (Store ID, Store Name, Product, Gap, Order Quantity, Quantity Rounding), then the supply detail (Baseline, Projected Demand, On Hand, Inbound, UPC, Units per Case, MOQ); a bold Total row sums Gap and Order Quantity; the table is tall enough to show every row; Quantity Rounding is capped at 300 px so the supply columns stay in view.
- Recall detail keeps its key-numbers row (`stat_row`) under the same header.
- New helpers and styles: `kpi_row` (inbox.py), `.kpi-row`, `.chip-*`, `.fact-line`, `.detail-sub` (app.css).
- Known leftovers: the DC Replenishment table still has 14 columns; the AI-suggested plan, Evidence, Recommended Actions and Decision History tabs are not restyled; the provenance line under the header still repeats "synthetic fixtures v1".

## Seventh pass: replenishment tables
- The Store Demand & Inventory table and the DC Replenishment Plan table were duplicating the same twelve rows, with almost every Gap cell red. They are replaced by one section, "Replenishment by store and product", shown **side by side** (the Combined view and its radio were tried and removed on request).
- **Left, "Demand and inventory" (Status, Store, Product, Gap, Order Quantity, Quantity Rounding, Baseline, Projected Demand, On Hand, Inbound, UPC, Units per Case, MOQ). **Right, "DC replenishment"** (Status, Store, Product, Primary Planned Qty, Backup DC, Backup Planned Qty, Residual Gap first; then Primary DC, ATPs, Backup DC Status, Route Risk, which matter less because the primary DC is nearly always the same). Every earlier column is still available. Each table scrolls sideways inside its half of the page.
- **Row colour = escalation status** (computed in `_replenishment_frame`): Escalate (red row) when Residual Gap is above zero, Covered (amber row) when there is a shortfall that the DCs cover, No gap (no colour). Rows are ordered Escalate, Covered, No gap. The status word is also in the first column and coloured, so colour is not the only signal. Cell-level red/green on Gap, Residual Gap and Route Risk is switched off in these tables (`emphasise_cells=False`) so the row colour is the one signal.
- The store is shown once per run of rows ("101 · Dallas, TX"), then blank; a bold Total row sums Gap, Order Quantity, planned quantities and Residual Gap. The columns are renamed consistently (Forecast Shortfall is Gap in both views).
- Files: `ui/views/operational_impact/incident_detail.py` (`_replenishment_frame`, `_render_replenishment_table`), `ui/components.py` (`style_operational_dataframe` gained status tints and `emphasise_cells`).
- Known leftover: the two tables are cramped on a laptop-width page; the first columns fill most of each half and the rest scroll inside each table.

## Eighth pass: assistants named by role, less explanatory text (incident detail)
- The two language-model features in an incident now carry a role name instead of "AI"/"NVIDIA" (constants `PLANNER_AGENT` and `ANALYST_AGENT` at the top of `incident_detail.py`, so renaming is one edit):
  - **Replenishment Planner** (was "AI-Suggested Replenishment Plan"): heading, one-line description, button "Run Replenishment Planner", per-store results with the credit line "Prepared by Replenishment Planner" (plus "(rule-based)" when the local summary was used; the vendor label is no longer shown). The stale-input notice reads "The inputs changed since the last plan. Run the Replenishment Planner again to refresh it."
  - **Incident Analyst** (was "Generate/Refresh AI explanation", Recommended Actions tab): buttons "Ask the Incident Analyst to explain" and "Re-run Incident Analyst", spinner "Incident Analyst is working...", credit line "Prepared by Incident Analyst".
- Long explanatory captions were shortened to one line, with the detail behind a help icon: the table legend, the inventory snapshot note, the demand-uplift note, the Replenishment Planner grounding statement, and the staffing/commute note.
- Still using "AI"/"NVIDIA" wording (not yet renamed): the Results "Executive Brief" tab (Written by NVIDIA, AI Timing, Regenerate NVIDIA brief), the header "LLM" field, the Configure readiness line "Executive brief: AI-generated", the Demand Planner caption, and the Evidence Audit pages (technical, probably fine to keep).

## Ninth pass: "agent" wording across the whole app
Rule (from the product owner): everywhere on screen the words NVIDIA, AI and LLM are replaced by "agent" wording. The only place "LLM" stays is the API key (and its model field).
- Agents by role: **Briefing Agent** (executive brief), **Replenishment Planner Agent** and **Incident Analyst Agent** (incident detail), **Demand Analyst Agent** (Demand Planner).
- Header: field "LLM: NVIDIA/Fallback" is now "Agent: Active / Rule-based". Sidebar summary, Configure readiness ("Executive brief: written by agent" / "rule-based"), Results confidence line (Brief Mode: Agent / Rule-based), Evidence Audit (Brief Source, "Agent call", "Agent analysis trace", "Rule-based Summary Used") and the pipeline status ("Briefing Agent wrote the brief", "Rule-based brief used") follow the same vocabulary.
- Executive brief tab: "Written by the Briefing Agent ...", "Agent timing", button "Re-run Briefing Agent".
- Credentials: "LLM API key", "LLM model", "Configured this session: LLM", status lines "LLM key" and "LLM key test", spinner "Testing the LLM key with one minimal request...".
- Failure messages (`util/text.py` `display_fallback_reason`, `infra/credentials.py`, `infra/nvidia.py`, `llm/brief.py`) say "The agent did not respond in time, so a rule-based summary was used instead." and similar; the term "fallback" is no longer shown for the brief. Runs saved before this change still contain old wording, so `display_fallback_reason` rewrites those phrases (and drops the raw endpoint URL) at display time.
- The model identifier (nvidia/nemotron...) is shown in the Brief Trace only when `SHOW_LLM_TRACE` is on.
- Unchanged on purpose: internal values and identifiers the code relies on (`brief_source == "nvidia"`, `nvidia_key`, the "AI summary" / "Local summary" source tokens, environment variable names), the exported audit JSON key names, and "County name fallback" (a geography method, not the agent).
- Tests updated for the renamed key label ("LLM API key", "Configured this session: LLM") and the new rule-based wording; 202 tests pass.

## Tenth pass: one "Evidence & recommendations" tab
- The incident detail now has three tabs: **Impact**, **Evidence & recommendations**, **Decision History** (the separate Evidence and Recommended Actions tabs were merged: the evidence explains why, the actions say what to do, the agent explains both).
- The merged tab reads top to bottom: **Evidence**, **Recommended actions**, **Incident Analyst Agent**.
  - Evidence is a label/value list instead of a two-column table. Weather: NOAA alert area; Store mapping (method plus a coloured confidence chip); Stores matched ("4 of 8 evaluated" plus the store numbers); Evidence basis; Explanation. Recall (which previously had only a raw JSON dump): Product, Recall (number and class), Recalling firm, Recall date, Reason, Distribution, Product match, and UPCs/lots when present. The underlying source record stays in a collapsed expander. A one-line note appears only when the mapping was a county-name match.
  - Recommended actions are one card per action: the action as the title, chips for urgency (red/amber/green), owner and status, then labelled fields (Reason, Evidence, Decision Support, Rule, plus any other fields the action carries). This replaced a wide table whose last columns were cut off.
  - The Incident Analyst Agent runs on request (as before). Its answer is shown in a bordered card with real headings (Incident Summary, Recommended Actions, Limitations); the long "Calculated Impact" list is folded into a collapsed section because those figures are on the Impact tab. The credit line "Prepared by Incident Analyst Agent" (plus "(rule-based)" when the local summary was used) sits at the bottom.
- Code: `_evidence_rows`, `_kv_list`, `_recommendation_cards`, `_explanation_sections` and `_render_explanation` in `incident_detail.py`; styles `.kv-list`, `.rec-card` in `app.css`.

## Eleventh pass: Impact tab clean-up, planner moved
- **Replenishment Planner Agent** moved out of the Impact tab into "Evidence & recommendations", between Recommended actions and the Incident Analyst Agent (`_render_replenishment_planner`). Impact now holds facts and numbers only; the second tab holds the judgement. The Planner covers every store with a gap in the incident (it no longer follows the Impact tab's store/product/DC filters).
- **Staffing readiness:** the five cards with descriptive sentences became one row of key numbers (Scheduled staff, Commute at risk, Expected available, On-call backup, Staffing risk; the risk is coloured red, amber or green). The detail sentences moved behind help icons. In the table the whole row is tinted by Staffing Risk, and Store, Staffing Risk and Staffing Action come first with the other columns to the right.
- **Customer store traffic:** the table with a two-line caption became three phase cards (Pre event, During event, Post event) showing the change, the multiplier and the one-line meaning. The caption is one line, with the pattern explanation behind a help icon.
- All section headings in the detail use the same style (`_section_heading`). The key-number cells are 160 px minimum so five fit in a row.
- Styles: `.kpi-value.warn`, `.phase-row`, `.phase-card`; `elevated` added to the row tints in `style_operational_dataframe`.

## Twelfth pass: the scenario lab becomes a tab of the Operational Impact Center
- The top-level "POC Scenario Lab" view is gone (7 top-level tabs left: Configure, Results, Operational Impact Center, Demand Planner, Evidence Audit, Signal Search, plus Raw Data in admin mode). The same builder now lives in the Operational Impact Center as a third tab, **What-if scenario**, beside Weather and Product recall.
- Why it was kept: the demo runbook uses it as the controlled way to show the incident workflow, and live alerts often miss the demo store network (in the saved data only 1 of 6 weather alerts reached a store and no live recall matched a product).
- The "POC" wording was dropped. The yellow warning became the same calm notice line as elsewhere ("Synthetic scenario - not an operational incident and not live NOAA/openFDA data."), and the intro is one line with the detail behind a help icon. The form, button labels ("Create weather scenario", "Create recall scenario") and the "Open scenario detail" selector are unchanged; saved scenarios are still stored apart from live incidents.
- `ENABLE_SCENARIO_LAB=false` still hides it; the check moved to `scenario_lab_enabled()` in `config/flags.py`. `render_poc_scenario_lab` was renamed `render_scenario_lab`.
- Because the scenario detail now lives on the same page, the page shows one scenario detail whenever a scenario has been saved. Tests that navigated to the old tab were updated (`test_application_smoke.py`, two tests in `test_review_regressions.py`); `.env.example`, `RUNNING.md`, `docs/customer-demo-runbook.md` and `docs/operations-runbook.md` now say "What-if scenario".

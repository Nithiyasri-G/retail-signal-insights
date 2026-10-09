# UI cleanup log: Demand Planner

Status: first cleanup pass done (layout and wording only, no colour changes, calculation untouched). Checked against a saved real run (08 Oct 2026) in a browser, plus an AppTest script that ran Analyze on all seven signals of that run.

## How it connects to the other pages
- Signals come from the same run as Results: `build_demand_signal_log(run["feature_df"])`. With no run it shows three reference signals (`demo_market_signals`).
- It connects to the Operational Impact Center only through the state of a weather signal (a GA signal lists GA stores). Nothing on screen said which incidents relate to a signal, so a High weather signal for GA could sit beside incidents that never reach a store.
- Stores and inventory are the demo store master and the same demo inventory the Operational Impact Center uses.

## Findings
- **No calculation bug:** Analyze ran without error for all seven signals of the saved run (national, GA and TX signals; category-scoped and whole-store).
- **No edit leakage:** an edit made in the inventory table of one store is not carried to another store when the store is switched.
- **Wording leaks fixed:** "Report written by: Local summary" (or "AI summary") was printed raw; "Demand Planning Agent" and "Demand Analyst Agent" were both used for one agent; the notice said "Run Market Intelligence first" (the old product name).
- **Misleading banner fixed:** a run that produced no signal rows still said "No completed run is available".
- **Noise:** a paragraph and a large blue box before any content; a signal table with 12 columns (including internal ones) plus a separate dropdown; raw "Captured At" timestamps and calculation-irrelevant columns in the inventory table.
- **Still open (by design, worth knowing):** state-level weather severity here and store-matched incident priority on the Operational Impact Center answer different questions, so they can disagree (GA High vs no GA incident reaching a store). The page now says so in one line instead of leaving it unexplained.

## New structure (`ui/views/demand_planner.py`)
1. **Header:** "Demand Planner" with a one-line question and the long explanation behind a help icon; one status line: "Signals from the run of <time> (7 signals)" or "Reference signals (no run in this session)".
2. **1. Market signal:** the signal table, whole rows tinted red / amber / green by Level. Click a row to choose the signal (the first is selected by default); the old dropdown was removed. Internal columns (Signal Key, Top Event, Catalog Categories) show in Admin view only.
3. **2. Store forecast and inventory:** store selector (default is a store the signal reaches), the scope caption, and the new line "Operational Impact Center: 4 weather incident(s) for GA in this run; 0 reach a store" (weather signals) or the recall count (recall signals). The inventory table shows Product, Category, the four editable numbers, Case Pack and MOQ; Admin view shows every column. The calculation reads only the four numbers and Category.
4. **Analyze Demand Impact** (label unchanged), with a spinner naming the agent.
5. **3. Result:** four large numbers (planning direction, products in scope, baseline forecast, baseline gap in red or surplus in green), then a label/value list of the facts. The scope explanation is behind a help icon.
6. **4. Demand Analyst Agent:** the report in a bordered card with real headings (Planning Summary, Forecast Context, Inventory Impact, Planning Action) and the credit line "Prepared by Demand Analyst Agent" (plus "(rule-based)" when the local summary was used). CSV and text downloads and the calculation audit are unchanged.

## Shared code
- New `ui/agents.py`: the agent names (Briefing, Replenishment Planner, Incident Analyst, Demand Analyst), `agent_credit`, and `render_agent_text` (formats an agent's ALL-CAPS section headings). The incident detail now imports these instead of keeping its own copies.
- `ui/views/operational_impact/inbox.py` gained `kv_list`; `ui/components.py` styles a "Level" column like a priority.

## Known leftovers
- Row selection in the signal table cannot be driven by AppTest, so tests use the default (first) signal; clicking was verified in the browser.
- The Briefing Agent wording on the Results page (Executive Brief tab) is separate from this page and was done earlier.

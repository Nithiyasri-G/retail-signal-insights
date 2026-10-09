# UI cleanup log: Results page

Status: first cleanup pass done (layout only, no colour changes). Checked against a saved real run (06 Oct 2026, 14:28 UTC).

## Problems found
- The same run was shown six ways in a row (header tiles, six trust cards, four run-summary cards, four KPI cards, three decision cards, two 9-row tables), about nine sections before the reader knew what to do.
- Leftovers on the page: the Pipeline Status panel and the NVIDIA/Apify status lines.
- Raw ISO run time, instructional copy, tiny uppercase labels, orphan sixth trust card, decision grid with four slots for three cards, wide tables cut off on the right, internal feature names in the validation table, large "no previous run" info box.

## Decisions (agreed with the product owner)
- Answer first: headline, then decisions, then KPIs.
- Internal Validation Agent table is Admin view only.
- A visible one-line banner states that internal operations data is synthetic (taken from the trust metric, so it changes if real data is connected).

## Decisions tab, top to bottom (`ui/views/results_page.py`, `ui/views/results.py`)
1. **Headline strip** (above the tabs): Overall signal level (coloured), Top signal with its level, Run time (readable format), Live sources. Retailer name as a small title.
2. **Data confidence line:** one line of trust metrics (External signals, Internal operations, Live sources, Raw records, Feature rows, Brief mode), plus the brief fallback note if one exists.
3. **Synthetic-data banner:** shown when the Internal operations metric is in warning state.
4. **What needs attention:** the decision cards, full width (auto-fit grid). Each card has the owner, a title, "Why" and "Next step" (the body text is split on "Next:").
5. **Key performance indicators:** the four KPI cards, unchanged except for sentence-case labels. Level colours are kept.
6. **Change since previous run:** one-line caption until there are two runs.
7. **Impact by retail category:** six stakeholder columns (Category, Region, KPI, Risk, Priority, Owner) with explicit widths so nothing scrolls sideways. Admin view adds demand direction, forecast feature, impact hypothesis, internal data needed, source.
8. **Internal validation plan:** Admin view only.

Other tabs (Explainability, Evidence and Export, Executive Brief) were not changed in this pass.

## Also changed (global)
- `.small-header` section titles are now 16px sentence case (was 10px uppercase with a rule), on every page.
- Metric card labels are 12px sentence case; card minimum height lowered.
- Pipeline Status panel (`ui/views/run_restore.py`) only shows on Configure. Credential status lines (`ui/views/credentials.py`) only show on Configure, or right after pressing Validate / Run connectivity test.
- Empty state (no run yet) is a title and one sentence plus the workflow strip.

## Removed from the Decisions tab
"Results Command Center" header block and its four mini tiles, six trust cards, "Run Summary Metrics" cards (Signals, Overall level, Top signal, News articles), the hero-side "Output Model" panel.

## Known leftovers
- Long category names are cut off with an ellipsis in the impact table (hover shows the full text).
- KPI card heights can differ by a line where one description wraps.
- Explainability, Evidence and Export and Executive Brief tabs still use the older card styles.
- Plotly chart on the Explainability tab uses its own hard-coded colours.
- `render_trust_panel` is no longer used on this page (still exported).

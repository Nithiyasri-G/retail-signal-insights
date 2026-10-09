# Architecture

`app.py` is only wiring (page setup, sidebar/header, routing to one view function per screen).
Everything else lives in the `market_intelligence` package. Streamlit is imported **only** under
`market_intelligence/ui/`; business logic, data access, LLM calls and persistence can be used and
tested without it.

## Layers (a package may import its own layer or a lower one, never a higher one)

| # | Packages | Responsibility |
|---|---|---|
| 1 | `config`, `util` | Settings/env (`settings.py`, `RUN_HISTORY_DB`), source constants, operational logging, clock, hashing, table/text helpers |
| 2 | `infra`, `models`, `provenance` | HTTP (`infra/http.py`), NVIDIA client (`infra/nvidia.py`), credential checks, typed models, provenance labels |
| 3 | `data`, `history`, `replenishment`, `scenarios` | Fixture repository + demo data loaders (`data/demo.py`, `data/demo_scenarios.py`), quantity/allocation rules, saved scenarios |
| 4 | `collectors`, `persistence`, `signals`, `recalls`, `weather`, `demand` | Live collectors (`collectors/live_*.py`), SQLite stores (`persistence/`), signal scoring/labels, recall parsing + matching, weather scenario/staffing/assumptions/inbox, demand planning |
| 5 | `incidents`, `llm` | Incident builders and recommended actions; prompts, brief generation, validators, incident/DC explanations |
| 6 | `application` | Use cases that run without a UI. `run_intelligence.py` is the Run Intelligence pipeline, driven through a `RunObserver` |
| 7 | `ui` | Everything that touches Streamlit: theme/CSS (`ui/styles/app.css`), components, one module per view under `ui/views/`, the sidebar, layout, `ScriptContext` |

`tests/test_architecture.py` enforces: Streamlit only in `ui`, imports only point downwards,
`app.py` contains no function or class definitions and stays under 100 lines.

## Request flow

```
app.py
  configure_page(); inject_css(); ctx = ScriptContext()
  init_app_state -> render_sidebar -> render_header_and_navigation   (fill ctx: keys, toggles, view)
  if ctx.view == "...": render_<view>(ctx)                           (ui/views/*)
  if ctx.run_button:   run_intelligence_pipeline(ctx)                (ui/views/run_pipeline.py)
        builds RunRequest -> application.run_intelligence(request, observer)
        observer renders progress + publishes the run to st.session_state
```

Sidebar widget values travel between functions on `ctx` (a `ScriptContext`) instead of the module-level
variables the original single script used.

## Where things go

- **New data source**: add `collectors/live_<name>.py`, call it from `application/run_intelligence.py`, add its toggle in `ui/sidebar.py`.
- **New screen**: add `ui/views/<name>.py` with `render_<name>(ctx)`, add it to the view list in `ui/layout.py` and route it in `app.py`.
- **New rule/calculation**: put it in the matching domain package (`weather/`, `recalls/`, `demand/`, `signals/`), never in `ui/`.
- **Anything that calls NVIDIA**: `llm/` (use `infra.nvidia`), grounded and validated as the existing generators are.

## Testing seams

Callers reach shared I/O through the defining module so tests can patch one place:
`market_intelligence.infra.nvidia._nvidia_chat_request`, `market_intelligence.infra.http.safe_request`,
`market_intelligence.config.settings.RUN_HISTORY_DB`, `market_intelligence.recalls.matching.match_recall_to_catalog`,
and the collector/persistence names imported into `application/run_intelligence.py`.

## Behaviour notes vs. the old single-file app

Streamlit re-ran the whole of `app.py` on every interaction; now only the thin `app.py` and the view
functions re-run, while package modules are imported once per server process. Restart the server after
editing fixture CSVs or `market_intelligence` code if you are not using Streamlit's file watcher.

The pre-existing typed layers (`collectors/<source>.py` normalizers, `data/connectors`, `data/repositories.py`,
`history/`, `persistence/incidents.py`, `recalls/impact.py`, `weather/impact.py`) are kept as-is and are covered by
their own tests, but they are not yet wired into the running app. Consolidating them with the live code paths is
a separate, deliberate follow-up.

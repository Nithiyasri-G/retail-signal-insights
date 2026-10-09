import json
import streamlit as st

from market_intelligence.signals.labels import client_feature_table
from market_intelligence.signals.retail_context import enrich_feature_rows_for_retailer
from market_intelligence.signals.retail_context import top_signal_labels
from market_intelligence.signals.risk import risk_band
from market_intelligence.signals.scoring import compute_retail_kpis
from market_intelligence.ui.brief import render_executive_brief
from market_intelligence.ui.components import render_metric_card, style_level_dataframe
from market_intelligence.ui.views.results import render_data_confidence, render_decision_summary, render_explainability_ladder, render_glossary, render_internal_validation_agent, render_previous_run_comparison, render_results_command_header, render_retail_impact_matrix, render_scenario_simulator, render_score_chart, render_score_explainability, render_trust_panel, render_workflow_strip


def render_results_page(ctx) -> None:
    # One keyed container so the Results page boxes share a single scoped type/card style (see app.css).
    with st.container(key="results_page"):
        _render_results_body(ctx)


def _render_results_body(ctx) -> None:
    nvidia_key = ctx.nvidia_key
    nvidia_model = ctx.nvidia_model
    retailer_label = ctx.retailer_label
    run = st.session_state.get("run")
    if not run:
        st.markdown(
            "<div class='panel-title'>No intelligence run yet</div>"
            "<div class='confidence-line'>Use Run intelligence in the left panel to see decisions, category impact, "
            "level explanations, evidence and the executive brief here.</div>",
            unsafe_allow_html=True,
        )
        render_workflow_strip()
    else:
        feature_df = run["feature_df"]
        if not feature_df.empty and "retail_category" not in feature_df.columns:
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
            retail_kpi_scores = compute_retail_kpis(feature_df)
            command_tab, explain_tab, evidence_tab, brief_tab = st.tabs(["Decisions", "Explainability", "Evidence and Export", "Executive Brief"])

            with command_tab:
                render_data_confidence(run, feature_df)

                st.markdown('<div class="small-header">What needs attention</div>', unsafe_allow_html=True)
                render_decision_summary(feature_df, run["results"])
                top_labels, top_level = top_signal_labels(feature_df)
                if len(top_labels) > 3:
                    st.caption(f"{len(top_labels)} signals are {top_level}; showing the 3 highest-ranked. All signals are in Evidence and Export.")
                else:
                    st.caption("Showing the highest-priority external signals from this run (up to three). Weather signals are state-specific; all signals are in Evidence and Export.")

                st.markdown('<div class="small-header">Key performance indicators</div>', unsafe_allow_html=True)
                cscore_cols = st.columns(4)
                kpi_notes = {
                    "Consumer Price Pressure": "Highest of Headline, Food, Household and Gasoline CPI. The category table below shows each separately.",
                    "Safety and Compliance Risk": "Recall and compliance exposure.",
                    "Supply Chain Disruption Risk": "Highest state-level weather signal; store/DC exposure is confirmed separately.",
                    "Demand Signal Priority": "News and demand-interest signals.",
                }
                for col, (name, score) in zip(cscore_cols, retail_kpi_scores.items()):
                    with col:
                        render_metric_card(name, risk_band(float(score)), kpi_notes.get(name, "Current planning classification."))

                st.markdown('<div class="small-header">Change since previous run</div>', unsafe_allow_html=True)
                render_previous_run_comparison(feature_df, st.session_state.get("previous_run"))

                st.markdown('<div class="small-header">Impact by retail category</div>', unsafe_allow_html=True)
                render_retail_impact_matrix(feature_df, admin=bool(getattr(ctx, "admin_view", False)))

                if getattr(ctx, "admin_view", False):
                    st.markdown('<div class="small-header">Internal validation plan</div>', unsafe_allow_html=True)
                    render_internal_validation_agent(feature_df)
            with explain_tab:
                st.markdown('<div class="small-header">How The Agent Explains A Signal</div>', unsafe_allow_html=True)
                render_explainability_ladder()

                st.markdown('<div class="small-header">Signal Interpretation Examples</div>', unsafe_allow_html=True)
                render_scenario_simulator(feature_df)

                st.markdown('<div class="small-header">Signal Levels</div>', unsafe_allow_html=True)
                render_score_chart(feature_df)

                st.markdown('<div class="small-header">Level Explainability</div>', unsafe_allow_html=True)
                render_score_explainability(feature_df)

            with evidence_tab:
                st.markdown('<div class="small-header">Candidate Forecast Signals</div>', unsafe_allow_html=True)
                # feature_df is the union of every collector's signal dict, so rendering
                # it whole put collector-private keys on screen -- catalog_categories as a
                # Python list, is_demo_scenario as a boolean, and a dozen columns that are
                # blank for every row from a different source.
                run_date = str(run.get("timestamp", ""))[:10] or None
                display_feature_df = client_feature_table(feature_df, run_date=run_date)
                st.dataframe(style_level_dataframe(display_feature_df), width="stretch", hide_index=True)
                st.caption("These are candidate signals, not validated forecast features. Run Date is when the signal was collected; for CPI, Data Period is the latest month BLS has published (it reports with a delay).")

                # The table above is labelled and column-limited; these two downloads
                # used to ship the raw collector schema, which is what the label map and
                # column allow-list exist to prevent.
                export_feature_df = client_feature_table(feature_df, for_export=True, run_date=run_date)
                csv_data = export_feature_df.to_csv(index=False).encode("utf-8")
                json_data = json.dumps(export_feature_df.to_dict(orient="records"), indent=2).encode("utf-8")
                c1, c2 = st.columns([1, 1])
                with c1:
                    st.download_button("Download candidate_forecast_signals.csv", csv_data, "candidate_forecast_signals.csv", "text/csv", width="stretch")
                with c2:
                    st.download_button("Download normalized_signals.json", json_data, "normalized_signals.json", "application/json", width="stretch")

                with st.expander("Glossary and interpretation guide", expanded=False):
                    render_glossary()

            with brief_tab:
                render_executive_brief(run, feature_df, nvidia_key, nvidia_model)

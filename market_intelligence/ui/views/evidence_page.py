from market_intelligence.data.fixture_repository import FixtureRepository
from market_intelligence.provenance.models import DataProvenance
import json
import pandas as pd
import streamlit as st

from market_intelligence.config.settings import DEFAULT_NVIDIA_MODEL
from market_intelligence.config.sources import SOURCE_LABELS, SOURCE_ORDER
from market_intelligence.llm.audit import build_base_llm_audit, display_fallback_reason, exportable_llm_audit, show_llm_trace_detail
from market_intelligence.signals.collector_evidence import any_mock_used, build_collector_evidence, count_raw_records
from market_intelligence.signals.labels import readable_feature_values, with_client_labels
from market_intelligence.signals.retail_context import enrich_feature_rows_for_retailer
from market_intelligence.signals.risk import risk_band
from market_intelligence.ui.brief import brief_to_html
from market_intelligence.ui.components import render_audit_banner, render_audit_card, render_metric_card, style_level_dataframe
from market_intelligence.ui.views.evidence_audit import render_audit_command_header, render_audit_lineage
from market_intelligence.util.tables import format_timestamp


def render_evidence_page(ctx) -> None:
    country = ctx.country
    language = ctx.language
    nvidia_model = ctx.nvidia_model
    region = ctx.region
    retailer_label = ctx.retailer_label
    run = st.session_state.get("run")
    if not run:
        st.markdown(
            "<div class='empty-console'>"
            "<div>"
            "<div class='hero-kicker'>Evidence Audit</div>"
            "<div class='empty-title'>No run evidence yet.</div>"
            "<div class='empty-body'>Run the governed signal pipeline to populate provenance, source health, mock-data status, normalized signal reasoning, agent prompt trace, payload hash, and raw API evidence.</div>"
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

        if run.get("llm_audit", {}).get("technical_error") and show_llm_trace_detail():
            with st.expander("Brief generation diagnostic (internal)", expanded=False):
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
        if not feature_df.empty and "retail_category" not in feature_df.columns:
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
        evidence_records = build_collector_evidence(run.get("results", {}), run_config, llm_audit)
        evidence_df = pd.DataFrame(evidence_records)
        mock_used = any_mock_used(run.get("results", {}), llm_audit)
        pulled_sources = int((evidence_df["raw_records_pulled"] > 0).sum()) if not evidence_df.empty else 0
        raw_records = int(evidence_df["raw_records_pulled"].sum()) if not evidence_df.empty else 0

        evidence_bundle = {
            "timestamp": run.get("timestamp"),
            "external_collector_mock_used": mock_used,
            "provenance": DataProvenance(
                external_signal_mode="Mixed" if mock_used else ("Live" if any(r.get("status") == "success" and r.get("rows") for r in run.get("results", {}).values()) else "Unavailable"),
                internal_operations_mode="Synthetic",
                explanation_mode="Agent" if llm_audit.get("accepted_response") else "Rule-based",
                as_of=str(FixtureRepository("v1").manifest.get("created_at", "Unknown")),
                dataset_version="fixtures-v1",
            ).to_dict(),
            "run_config": run_config,
            "collector_evidence": evidence_records,
            "normalized_features": feature_df.to_dict(orient="records") if not feature_df.empty else [],
            # The full prompt text and raw request payload are internal review material,
            # gated the same way the Brief Trace tab gates them (SHOW_LLM_TRACE); this
            # download ignored that until now and shipped the whole prompt to anyone who
            # clicked it.
            "llm_audit": exportable_llm_audit(llm_audit),
            "brief": run.get("brief", ""),
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
        audit_provenance = DataProvenance(
            external_signal_mode="Mixed" if mock_used else ("Live" if any(r.get("status") == "success" and r.get("rows") for r in run.get("results", {}).values()) else "Unavailable"),
            internal_operations_mode="Synthetic",
            explanation_mode="Agent" if llm_audit.get("accepted_response") else "Rule-based",
            as_of=str(FixtureRepository("v1").manifest.get("created_at", "Unknown")),
            dataset_version="fixtures-v1",
        )
        render_audit_banner(audit_provenance)

        # The LLM Trace tab prints the system prompt, the user prompt and the whole
        # payload. That is the right artifact for an internal review and the wrong one
        # for a client session, so it is off unless SHOW_LLM_TRACE is set. The summary
        # that replaces it keeps the audit claim intact -- hash, row counts, timings --
        # without putting prompt engineering on screen.
        # Raw collector payloads live in the Raw Data view; repeating them here duplicated it.
        trace_labels = ["Provenance", "Normalized Signals", "Brief Trace"]
        provenance_tab, normalized_tab, llm_tab = st.tabs(trace_labels)

        with provenance_tab:
            st.markdown('<div class="small-header">Chain Of Custody</div>', unsafe_allow_html=True)
            render_audit_lineage()

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                render_audit_card("External Signal", audit_provenance.external_signal_mode, "Collector rows are marked per run result.")
            with c2:
                render_audit_card("Internal Operations", audit_provenance.internal_operations_mode, f"{audit_provenance.dataset_version}; as of {format_timestamp(audit_provenance.as_of)}")
            with c3:
                render_audit_card(
                    "Rows Sent To Brief",
                    f"{llm_audit.get('feature_rows_sent', 0)} / {llm_audit.get('feature_rows_available', 0)}",
                    "Feature payload is capped for concise agent context.",
                )
            with c4:
                render_audit_card(
                    "Agent call",
                    "Briefing Agent" if llm_audit.get("sent_to_llm") else "No agent call",
                    display_fallback_reason(llm_audit.get("fallback_reason") or "Accepted agent output is grounded in the recorded payload."),
                )

            st.markdown('<div class="small-header">Collector Evidence Table</div>', unsafe_allow_html=True)
            st.dataframe(with_client_labels(evidence_df), width="stretch", hide_index=True)

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
                    "retail_category",
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
                normalized_display_df = readable_feature_values(feature_df[visible_cols].copy())
                if "risk_score" in normalized_display_df.columns:
                    normalized_display_df["risk_score"] = normalized_display_df["risk_score"].apply(lambda x: risk_band(float(x or 0)))
                    normalized_display_df = normalized_display_df.rename(columns={"risk_score": "Risk Level"})
                st.dataframe(style_level_dataframe(with_client_labels(normalized_display_df)), width="stretch", hide_index=True)

            st.caption("Business explanations are available in Results → Explainability.")

        with llm_tab:
            st.markdown('<div class="small-header">Agent analysis trace</div>', unsafe_allow_html=True)
            model_note = (
                f"Model requested: {llm_audit.get('requested_model', llm_audit.get('model'))}; accepted model: {llm_audit.get('model') if llm_audit.get('accepted_response') else 'none'}. "
                if show_llm_trace_detail()
                else ""
            )
            st.caption(f"{model_note}Supporting news: first collected article sampling, not relevance-ranked.")
            trace_cols = st.columns(4)
            with trace_cols[0]:
                render_metric_card("Brief Source", "Agent" if run.get("brief_source") == "nvidia" else "Rule-based", "Agent if its response was used; rule-based if local logic was.")
            with trace_cols[1]:
                render_metric_card("Payload Hash", str(llm_audit.get("payload_hash_sha256", ""))[:12], "SHA-256 fingerprint of features/articles payload.")
            with trace_cols[2]:
                render_metric_card("Articles Supplied", str(llm_audit.get("articles_sent", 0)), f"Of {llm_audit.get('articles_available', 0)} collected; the payload is capped to keep the brief focused.")
            with trace_cols[3]:
                render_metric_card("Rule-based Summary Used", "Yes" if llm_audit.get("fallback_used") else "No", display_fallback_reason(llm_audit.get("fallback_reason") or "The agent was used."))

            if llm_audit.get("accepted_response"):
                st.success(
                    "The Briefing Agent wrote this brief from only the feature rows and article "
                    "records collected in this run -- no other context was supplied."
                )
            else:
                st.info(
                    "The displayed brief was written by rule-based "
                    "logic from the same feature rows."
                )

            if show_llm_trace_detail():
                with st.expander("Prompt and payload used for the brief", expanded=False):
                    st.caption(
                        "Internal review detail. Hidden unless SHOW_LLM_TRACE is enabled."
                    )
                    st.markdown("System prompt")
                    st.code(str(llm_audit.get("system_prompt", "")), language="text")
                    st.markdown("User prompt")
                    st.code(str(llm_audit.get("user_prompt", "")), language="text")
                    st.markdown("Payload")
                    st.code(json.dumps(llm_audit.get("payload", {}), indent=2, default=str)[:16000], language="json")
                    if llm_audit.get("nvidia_attempts"):
                        st.markdown("Attempt timing and retries")
                        st.dataframe(pd.DataFrame(llm_audit.get("nvidia_attempts", [])), width="stretch", hide_index=True)
                    if llm_audit.get("technical_error"):
                        st.markdown("Technical diagnostic")
                        st.code(str(llm_audit.get("technical_error", ""))[:2000], language="text")
            else:
                attempt_rows = llm_audit.get("nvidia_attempts", []) or []
                elapsed = sum(int(a.get("elapsed_ms", 0) or 0) for a in attempt_rows)
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"Audit Item": "Payload fingerprint (SHA-256)", "Value": str(llm_audit.get("payload_hash_sha256", ""))[:16]},
                            {"Audit Item": "Signal rows supplied", "Value": f"{llm_audit.get('feature_rows_sent', 0)} of {llm_audit.get('feature_rows_available', 0)}"},
                            {"Audit Item": "Article records supplied", "Value": f"{llm_audit.get('articles_sent', 0)} of {llm_audit.get('articles_available', 0)}"},
                            {"Audit Item": "Generation attempts", "Value": f"{len(attempt_rows)} ({elapsed} ms total)"},
                            {"Audit Item": "Grounding rule", "Value": str(llm_audit.get("analysis_contract", ""))},
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

            with st.expander("Brief output", expanded=False):
                st.markdown(f"<div class='brief-box'>{brief_to_html(str(run.get('brief', '')))}</div>", unsafe_allow_html=True)

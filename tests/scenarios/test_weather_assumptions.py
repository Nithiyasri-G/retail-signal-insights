from market_intelligence.scenarios.weather_assumptions import WeatherAssumptionSet


def test_synthetic_assumptions_never_use_observed_history_labels() -> None:
    assumptions = WeatherAssumptionSet(
        version="scenario-assumptions-v1",
        event_type="Flood Watch",
        region="South Central",
        phase_uplift={"During-Event": 20.5},
        traffic_delta={"Pre-Event": 12.0},
    )

    labels = assumptions.display_labels()
    rendered = " | ".join(labels.values())

    assert labels["demand"] == "Scenario Demand Assumption"
    assert labels["traffic"] == "Scenario Traffic Assumption"
    assert labels["comparables"] == "Comparable Scenario Cases"
    assert "Historical Uplift" not in rendered
    assert "Historical Traffic" not in rendered
    assert "Comparable Past Events" not in rendered


def test_observed_event_history_has_a_distinct_schema() -> None:
    observed = WeatherAssumptionSet(
        version="observed-events-v1",
        event_type="Flood Watch",
        region="South Central",
        phase_uplift={},
        traffic_delta={},
        provenance="observed",
    )

    assert observed.display_labels()["demand"] == "Observed Historical Demand Uplift"
    assert observed.provenance == "observed"
    assert observed.evidence_label == "Observed event-history dataset (observed-events-v1)"


def test_synthetic_evidence_identifies_versioned_assumption_set() -> None:
    assumptions = WeatherAssumptionSet(
        version="scenario-assumptions-v1",
        event_type="Flood Watch",
        region="South Central",
    )

    assert assumptions.evidence_label == "Versioned POC assumption set (scenario-assumptions-v1)"

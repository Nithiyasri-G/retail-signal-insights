from market_intelligence.collectors.live_news import classify_news_title
from market_intelligence.signals.search import build_default_news_keywords


def test_default_keywords_cover_company_supply_chain_and_local_weather() -> None:
    lines = build_default_news_keywords("Dollar Tree", ["TX", "GA"]).splitlines()
    assert '"Dollar Tree" recall' in lines
    assert "Red Sea shipping disruption" in lines
    assert "Middle East conflict oil prices supply chain" in lines
    assert "Texas severe weather storms" in lines
    assert "Georgia flooding hurricane" in lines
    assert len(lines) == len(set(lines)) <= 20


def test_default_keywords_skip_unknown_states_and_work_without_states() -> None:
    assert "Atlantis" not in build_default_news_keywords("Acme", ["ZZ"])
    assert not any("weather" in line for line in build_default_news_keywords("Acme").splitlines())


def test_news_classifier_recognises_weather_and_supply_disruption() -> None:
    assert classify_news_title("Tropical Storm threatens Georgia with flooding", "")[0] == "weather_disruption"
    assert classify_news_title("Houthi attacks raise Red Sea shipping risks", "")[0] == "supply_disruption"
    # Whole words only: "support" must not read as "port", "boil" not as "oil".
    assert classify_news_title("Store support team update on boil water notice", "")[0] == "general_market_news"
    # Existing behaviour is unchanged: recalls stay risk events, deals stay opportunities.
    assert classify_news_title("Dollar Tree recall of balloon pumps", "")[0] == "risk_event"
    assert classify_news_title("Coupon deal this weekend", "")[0] == "demand_opportunity"

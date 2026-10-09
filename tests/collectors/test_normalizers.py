from market_intelligence.collectors.apify import normalize_apify_interest
from market_intelligence.collectors.bls import normalize_bls_observation
from market_intelligence.collectors.news import normalize_news_item
from market_intelligence.collectors.openfda import normalize_openfda_recall
from market_intelligence.recalls.impact import match_recall_upc


def test_external_normalizers_and_recall_match() -> None:
    assert normalize_apify_interest({"query": "water", "value": 80, "date": "2026-09-23"}, geography="US").interest == 80
    assert normalize_bls_observation("CPI", {"year": "2026", "period": "M08", "value": "321.1"}).year == 2026
    assert normalize_news_item({"title": "Storm", "link": "https://example.test", "source": "Wire"}).publisher == "Wire"
    recall = normalize_openfda_recall({"recall_number": "R1", "product_description": "Water", "classification": "II"})
    assert recall.recall_number == "R1"
    assert match_recall_upc(["0490-0002-8911"], [{"UPC": "049000028911"}]).status == "confirmed_exact"
    assert match_recall_upc(["123"], [{"UPC": "049000028911"}]).status == "unmatched"

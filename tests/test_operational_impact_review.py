"""Operational Impact Center review fixes (scenario lab, weather and recall presentation)."""
import pandas as pd

from market_intelligence.data.distribution import distribution_summary
from market_intelligence.incidents.builders import build_recall_incidents
from market_intelligence.llm.incident import local_dc_replenishment_recommendation
from market_intelligence.recalls.ladder import EVIDENCE_LADDER, ladder_entry
from market_intelligence.recalls.matching import allocate_lot_units, match_recall_to_catalog
from market_intelligence.scenarios.service import ScenarioRecord
from market_intelligence.ui.views.operational_impact.center import scenario_header_rows
from market_intelligence.ui.views.operational_impact.incident_detail import (
    _replenishment_frame,
    no_split_notes,
    source_classification,
)
from market_intelligence.weather.inbox_helpers import expired_action_status, weather_record_type
from market_intelligence.weather.wording import ROUTE_RISK_WARNING


def test_scenario_header_repeats_the_chosen_inputs():
    record = ScenarioRecord.create(
        scenario_type="Weather", created_by="", assumptions={"event": "Air Quality Alert", "state": "GA"}, result={},
    )
    rows = dict(scenario_header_rows(record))
    assert rows["Event"] == "Air Quality Alert" and rows["State"] == "GA"
    assert rows["Created by"] == "POC User"  # the default is visible and consistently cased
    assert rows["Scenario ID"].startswith("SCN-") and rows["Assumption version"]
    recall = ScenarioRecord.create(scenario_type="Product Recall", created_by="Nithi", assumptions={"product": "First Aid Kit"}, result={})
    recall_rows = dict(scenario_header_rows(recall))
    assert recall_rows["Product"] == "First Aid Kit" and "State" not in recall_rows


def test_expired_proposed_action_is_closed_but_recorded_decisions_are_kept():
    assert expired_action_status("Proposed", True) == "Expired - closed without action"
    assert expired_action_status("Proposed", False) == "Proposed"
    assert expired_action_status("Approved", True) == "Approved"


def test_weather_record_type_tells_the_four_kinds_apart():
    assert weather_record_type(True, False, 3) == "Controlled demonstration"
    assert weather_record_type(False, True, 3) == "Expired incident"
    assert weather_record_type(False, False, 0) == "Live - no Store intersection"
    assert weather_record_type(False, False, 2) == "Live external incident"


def _plan_row(**over):
    row = {
        "Store ID": "101", "Store Name": "Store 101 - Dallas, TX", "Product": "Bottled Water", "Order Quantity": 72,
        "Forecast Shortfall": 70, "Primary Planned Qty": 0, "Backup Planned Qty": 0, "Residual Gap": 72,
        "Primary DC Available ATP": 15, "Backup DC Available ATP": 37, "Route Risk": "No",
    }
    row.update(over)
    return row


def test_no_split_note_explains_a_zero_allocation_with_stock_available():
    frame = _replenishment_frame(pd.DataFrame([_plan_row()]), show_uplift=False)
    notes = no_split_notes(frame)
    assert len(notes) == 1
    assert "single-DC-per-product" in notes[0] and "72-unit" in notes[0]


def test_no_split_note_is_silent_when_allocation_happened_or_no_stock_exists():
    covered = _replenishment_frame(pd.DataFrame([_plan_row(**{"Primary Planned Qty": 72, "Residual Gap": 0})]), show_uplift=False)
    assert no_split_notes(covered) == []
    no_stock = _replenishment_frame(
        pd.DataFrame([_plan_row(**{"Primary DC Available ATP": 0, "Backup DC Available ATP": 0})]), show_uplift=False
    )
    assert no_split_notes(no_stock) == []


def test_replenishment_frame_keeps_store_on_every_row_for_export():
    rows = [_plan_row(Product="Batteries"), _plan_row(Product="Flashlights")]
    frame = _replenishment_frame(pd.DataFrame(rows), show_uplift=False)
    assert frame["Store"].tolist() == ["101 · Dallas, TX"] * 2


def test_route_risk_wording_is_the_same_for_every_product_row_of_the_store():
    rows = pd.DataFrame([
        {"Product": "A", "Order Quantity": 10, "Primary DC": "D1", "Primary Planned Qty": 10, "Backup Planned Qty": 0, "Residual Gap": 0, "Route Risk": "Yes"},
        {"Product": "B", "Order Quantity": 5, "Primary DC": "D1", "Primary Planned Qty": 5, "Backup Planned Qty": 0, "Residual Gap": 0, "Route Risk": "No"},
    ])
    text = local_dc_replenishment_recommendation(rows, "Store 101")
    assert text.count(ROUTE_RISK_WARNING) == 2


def test_source_classification_labels_every_kind():
    weather = {"Type": "Weather", "Evidence": {}}
    recall = {"Type": "Product Recall", "Evidence": {}}
    assert source_classification(weather) == "Live NOAA"
    assert source_classification(recall) == "Live openFDA"
    assert source_classification(weather, scenario_view=True) == "Controlled Weather Scenario"
    assert source_classification(recall, scenario_view=True) == "Controlled Product Recall Scenario"
    assert source_classification(weather, override="Historical Incident") == "Historical Incident"


def test_lot_units_are_whole_and_add_back_to_the_total():
    for quantity in (70.5, 1052.8, 171.9, 0, 3):
        split = allocate_lot_units(quantity, ["LOT-A", "LOT-B", "LOT-C"])
        assert all(isinstance(v, int) and v >= 0 for v in split.values())
        assert sum(split.values()) == round(quantity)


def test_confirmed_recall_inventory_is_whole_units_that_reconcile():
    item = {"product": "First Aid Kit", "upcs": "", "lots": "", "distribution_pattern": "Nationwide", "classification": "Class I"}
    from market_intelligence.data.demo import demo_lot_master, demo_product_catalog
    catalog = demo_product_catalog()
    lot_master = demo_lot_master(catalog)
    tracked = catalog[catalog["Lot Tracked"] == True].iloc[0]  # noqa: E712
    lot = lot_master[lot_master["UPC"] == tracked["UPC"]].iloc[0]["Lot ID"]
    item.update({"product": tracked["Product"], "upcs": tracked["UPC"], "lots": lot})
    result = match_recall_to_catalog(item)
    assert result["match_status"] == "confirmed_exact"
    for line in result["store_lines"]:
        assert float(line["On Hand"]).is_integer()
    assert result["total_store_exposure"] == sum(line["On Hand"] for line in result["store_lines"])
    assert result["total_dc_on_hand"] == sum(line["On Hand"] for line in result["dc_lines"])
    assert result["total_dc_in_transit"] == sum(line["In Transit"] for line in result["dc_lines"])
    assert result["exposed_units"] == result["total_store_exposure"] + result["total_dc_on_hand"] + result["total_dc_in_transit"]


def test_distribution_footprint_is_a_candidate_count_for_every_match_level():
    texas_louisiana = {"product": "zzz unmatched item", "upcs": "", "lots": "", "distribution_pattern": "Texas and Louisiana"}
    result = match_recall_to_catalog(texas_louisiana)
    assert result["distribution_known"] is True
    assert result["footprint_store_count"] == len(result["footprint_store_ids"]) > 0
    unknown = match_recall_to_catalog({**texas_louisiana, "distribution_pattern": ""})
    assert unknown["distribution_known"] is False and unknown["footprint_store_count"] == 0


def test_probable_match_keeps_the_candidate_out_of_the_confirmed_fields():
    result = match_recall_to_catalog({"product": "Peanut Butter cookies", "upcs": "", "lots": "", "distribution_pattern": "Nationwide"})
    if result["match_status"] == "product_review_required":
        assert result["upc"] == "" and result["store_lines"] == []
        assert result["candidate_product"] and result["candidate_upc"]
        assert result["footprint_store_count"] > 0


def test_distribution_summary_is_short():
    assert distribution_summary("Nationwide") == "Nationwide"
    assert distribution_summary("Texas and Louisiana") == "LA, TX"
    assert distribution_summary("Domestic: AL, AR, FL, GA, IL, IN, KS, KY, LA, MA. Foreign: Not applicable.") == "10 states"
    assert distribution_summary("") == "Not supplied"


def test_every_ladder_level_states_its_action_and_only_exact_match_allows_store_action():
    for status, entry in EVIDENCE_LADDER.items():
        assert entry["action"] and entry["confirmed"] and entry["required"], status
    assert ladder_entry("confirmed_exact")["store_action"] is True
    assert all(not e["store_action"] for s, e in EVIDENCE_LADDER.items() if s != "confirmed_exact")
    assert ladder_entry("lot_review_required")["action"] == "Hold withdrawal until lot coverage is confirmed."
    assert ladder_entry("category_hazard_match")["action"] == "Buyer/compliance review only; no inventory withdrawal."


def test_review_level_incidents_carry_the_footprint_and_the_permitted_action():
    item = {"product": "mystery", "recall_number": "F-1", "upcs": "", "lots": "", "reason": "Undeclared milk allergen",
            "distribution_pattern": "Texas and Louisiana", "classification": "Class II"}
    incident = build_recall_incidents({"status": "success", "items": [item]}, "run")[0]
    metrics = incident["Impact Metrics"]
    assert metrics["Distribution-footprint stores (candidates)"] != "Unknown distribution"
    assert metrics["UPC/Lot-Confirmed Affected Stores"] == 0
    assert incident["Recommended Actions"][0]["Decision Support"] == ladder_entry(incident["Status"])["action"]


def test_probable_match_reports_candidate_stores_and_estimates_product_level_stock():
    item = {"product": "Peanut Butter cookies", "upcs": "", "lots": "", "distribution_pattern": "Texas and Louisiana"}
    result = match_recall_to_catalog(item)
    if result["match_status"] != "product_review_required":
        return
    assert result["candidate_estimate_available"] is True
    assert result["candidate_stores_carrying"] <= result["footprint_store_count"]
    assert result["estimated_store_on_hand"] > 0
    assert result["store_lines"] == [] and result["upc"] == "" and result["financial_exposure"] == 0.0
    nowhere = match_recall_to_catalog({**item, "distribution_pattern": "New York"})
    assert nowhere["candidate_stores_carrying"] == 0 and nowhere["estimated_store_on_hand"] == 0
    unknown = match_recall_to_catalog({**item, "distribution_pattern": ""})
    assert unknown["candidate_estimate_available"] is False


def test_category_match_gets_no_product_estimate():
    result = match_recall_to_catalog({"product": "zzz", "reason": "Undeclared milk allergen", "upcs": "", "lots": "", "distribution_pattern": "Texas"})
    assert result["match_status"] == "category_hazard_match"
    assert "candidate_stores_carrying" not in result


def test_live_feed_note_says_why_there_are_no_live_incidents():
    from market_intelligence.ui.views.operational_impact.inbox import live_feed_note
    assert "not selected" in live_feed_note({"results": {}}, "weather", "NOAA weather alerts")
    failed = {"results": {"weather": {"status": "failed", "error": "HTTP 503."}}}
    assert "could not be collected" in live_feed_note(failed, "weather", "NOAA") and "HTTP 503" in live_feed_note(failed, "weather", "NOAA")
    empty = {"results": {"fda": {"status": "success", "items": []}}}
    assert "no active items" in live_feed_note(empty, "fda", "openFDA recalls")
    assert live_feed_note({"results": {"fda": {"status": "success", "items": [{}]}}}, "fda", "x") == ""


def test_recall_fixture_set_is_consistent_and_leaves_the_weather_fixtures_alone():
    from market_intelligence.data.fixture_repository import FixtureRepository
    v1, wide = FixtureRepository("v1"), FixtureRepository("recall_v1")
    products, suppliers = wide.table("products"), wide.table("suppliers")
    assert wide.manifest["provenance"] == "synthetic" and len(products) > 100
    assert products["UPC"].is_unique and products["Product"].is_unique
    assert products["UPC"].str.len().eq(12).all()
    assert set(products["Supplier ID"]) <= set(suppliers["Supplier ID"])
    assert set(wide.table("lots")["UPC"]) <= set(products["UPC"])
    assert set(wide.table("store_inventory")["UPC"]) <= set(products["UPC"])
    assert set(wide.table("dc_inventory")["UPC"]) <= set(products["UPC"])
    original = v1.table("products")
    assert set(original["UPC"]) <= set(products["UPC"])
    # every original row survives unchanged in the wider set
    assert wide.table("store_inventory").merge(v1.table("store_inventory"), how="inner").shape[0] == len(v1.table("store_inventory"))
    # weather inputs were not touched
    assert len(v1.table("products")) == 21 and len(v1.table("store_inventory")) == 336
    # varied assortment: some store skips some item, so "stores carrying" is a real count
    carried = wide.table("store_inventory").groupby("UPC")["Store ID"].nunique()
    assert carried.min() < 16 and carried.max() == 16


def test_product_text_match_uses_whole_words_and_the_most_specific_name():
    def candidate(text):
        return match_recall_to_catalog({"product": text, "upcs": "", "lots": "", "distribution_pattern": "Texas"}).get("candidate_product")
    assert candidate("ERIDANOUS Shortbread Cookies with apricot filling") == "Shortbread Cookies"  # not "Bread"
    assert candidate("Bread Pudding with Vanilla Sauce kit") == "Bread Pudding"  # longer name beats "Bread"
    assert candidate("Prince Italian Bread; Sesame") == "Italian Bread"
    # words scattered through an ingredient list are not a product name
    assert candidate("Salsa Roja Medium. Ingredients: Fire Roasted Diced Tomatoes, Yellow Onion") is None


def test_probable_match_carries_brand_vendor_and_size_and_flags_a_brand_hit():
    result = match_recall_to_catalog({
        "product": "Italian Bread; Sesame", "recalling_firm": "Blue Ridge Bakery Co", "upcs": "", "lots": "",
        "distribution_pattern": "Texas",
    })
    assert result["candidate_brand"] == "Blue Ridge" and result["candidate_vendor"] == "Blue Ridge Bakery Co"
    assert result["candidate_package_size"] and result["brand_matched"] is True
    assert result["store_lines"] == [] and result["upc"] == ""
    other = match_recall_to_catalog({"product": "Italian Bread", "recalling_firm": "Someone Else", "upcs": "", "lots": "", "distribution_pattern": "Texas"})
    assert other["brand_matched"] is False


def test_added_items_give_a_varied_candidate_store_count():
    counts = {
        match_recall_to_catalog({"product": name, "upcs": "", "lots": "", "distribution_pattern": "Nationwide"})["candidate_stores_carrying"]
        for name in ("Italian Bread", "Fresh Salsa", "Granola", "Sliced Ham", "Pain Reliever", "Hummus")
    }
    assert len(counts) > 1 and max(counts) <= 16


def test_product_title_stops_before_the_ingredient_list():
    from market_intelligence.recalls.matching import product_title
    assert product_title("Pico De Gallo. Ingredients: Tomato, Red Onion").strip(". ") == "Pico De Gallo"
    assert product_title("Honeyville YELLOW COLOR 1 GALLON NFI: WATER, FD&C YELLOW #5") == "Honeyville YELLOW COLOR 1 GALLON"
    assert product_title("Plain title only") == "Plain title only"


def test_ingredient_words_never_identify_the_product():
    def candidate(text):
        return match_recall_to_catalog({"product": text, "upcs": "", "lots": "", "distribution_pattern": "Texas"}).get("candidate_product")
    assert candidate("Mystery Dip. Ingredients: Italian Bread crumbs, Fresh Salsa, Granola") is None
    assert candidate("Italian Bread. Ingredients: flour") == "Italian Bread"


def test_product_name_must_be_one_contiguous_phrase():
    def candidate(text):
        return match_recall_to_catalog({"product": text, "upcs": "", "lots": "", "distribution_pattern": "Texas"}).get("candidate_product")
    assert candidate("PUMPKIN SPICE NO BAKE COOKIE") is None  # words separated by unrelated words
    assert candidate("Tomato Soup Bisque Kit") == "Tomato Soup"
    assert candidate("Potato Sourdough Bread") == "Sourdough Bread"  # "Potato Bread" is not contiguous


def test_package_size_is_supporting_evidence_only():
    from market_intelligence.recalls.matching import package_size_in_text
    assert package_size_in_text("12 oz", "ITALIAN BREAD NET WEIGHT 12 OZ. (340g)") is True
    assert package_size_in_text("12 oz", "NET WT 112 OZ") is False
    assert package_size_in_text("13.8 oz", "NET WT 13.8 OZ (39g)") is True
    assert package_size_in_text("each", "anything") is False
    result = match_recall_to_catalog({"product": "Italian Bread; NET WEIGHT 99 OZ", "upcs": "", "lots": "", "distribution_pattern": "Texas"})
    assert result["match_status"] == "product_review_required" and result["size_matched"] is False and result["brand_matched"] is False


def test_candidate_store_statement_wording():
    from market_intelligence.recalls.matching import candidate_store_statement
    assert candidate_store_statement({"candidate_estimate_available": True, "candidate_stores_carrying": 11, "candidate_product": "Granola"}) == (
        "11 stores carry the possible Granola match; 0 are confirmed affected because the recall UPC and lot have not matched."
    )
    assert candidate_store_statement({"candidate_estimate_available": True, "candidate_stores_carrying": 1, "candidate_product": "Granola"}).startswith("1 store carries the possible")
    assert "Distribution is not stated" in candidate_store_statement({"candidate_estimate_available": False})


def test_probable_recall_scenario_reaches_candidate_level_only():
    from market_intelligence.data.demo_scenarios import demo_recall_probable_scenario
    item = demo_recall_probable_scenario("Italian Bread")
    assert item["upcs"] == "" and item["lots"] == "" and item["is_demo_scenario"] is True
    incident = build_recall_incidents({"status": "success", "items": [item]}, "run")[0]
    assert incident["Status"] == "product_review_required"
    assert incident["Impact Metrics"]["UPC/Lot-Confirmed Affected Stores"] == 0
    assert "carry the possible" in incident["Impact Metrics"]["Stores Carrying Possible Product Match"]
    assert incident["Impact Metrics"]["Brand/vendor check"] in {"Found", "Not found"}

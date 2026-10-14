"""Product-family analog tier for recalls: the recall counterpart of weather's event-family ladder."""
from market_intelligence.incidents.builders import build_recall_incidents
from market_intelligence.recalls.families import OTHER_FAMILY, product_family
from market_intelligence.recalls.ladder import ladder_entry
from market_intelligence.recalls.matching import match_recall_to_catalog


def _recall(product, distribution="GA, TX", **extra):
    item = {"product": product, "recall_number": "F-FAMILY-" + product[:12], "upcs": "", "lots": "",
            "reason": "Undeclared allergen", "distribution_pattern": distribution, "classification": "Class II"}
    item.update(extra)
    return item


def test_family_rules_are_generic_keyword_rules_on_the_title():
    assert product_family("Salsa Roja Medium.") == "Salsa / Dips"
    assert product_family("Hexing Haw Balls candy; packaged in 200g bag") == "Candy / Confectionery"
    assert product_family("Prince PAN DE MANTECA; SPANISH STYLE") == "Bread / Bakery"
    assert product_family("Spice Magic Fennel Candy") == "Candy / Confectionery"  # candy before spice
    assert product_family("Peanut Butter") == "Spreads / Sweeteners"  # not dairy butter
    assert product_family("Shortbread Cookies") == "Cookies / Sweet Baked Goods"  # not bread
    assert product_family("Widget assembly") == OTHER_FAMILY


def test_unnamed_product_in_a_carried_family_gets_a_planning_estimate_but_no_confirmed_exposure():
    match = match_recall_to_catalog(_recall("Acme Salsa Roja Hot 16 oz"))
    assert match["match_status"] == "product_family_match"
    assert match["product_family"] == "Salsa / Dips"
    assert match["family_stores_carrying"] > 0 and match["family_estimated_store_on_hand"] > 0
    assert match["family_estimated_value"] > 0
    # Nothing is confirmed: no UPC, no store lines, no financial exposure.
    assert match["upc"] == "" and match["store_lines"] == [] and match["financial_exposure"] == 0.0
    assert set(match["family_store_ids"]) <= set(match["footprint_store_ids"])


def test_family_incident_is_a_shelf_check_never_a_withdrawal():
    incident = build_recall_incidents({"status": "success", "items": [_recall("Acme Salsa Roja Hot 16 oz")]}, "run")[0]
    assert incident["Status"] == "product_family_match"
    assert incident["Store IDs"] == []
    metrics = incident["Impact Metrics"]
    assert metrics["UPC/Lot-Confirmed Affected Stores"] == 0
    assert metrics["Product family"] == "Salsa / Dips"
    assert metrics["Same-family store on hand (planning only)"] > 0
    action = incident["Recommended Actions"][0]
    assert action["Action"] == "Product-family shelf check"
    assert action["Decision Support"] == ladder_entry("product_family_match")["action"]
    assert ladder_entry("product_family_match")["store_action"] is False


def test_family_outside_the_store_footprint_falls_back_to_category_review():
    # Same family, but distributed only where the retailer has no stores: like a weather alert with no store inside it.
    match = match_recall_to_catalog(_recall("Acme Salsa Roja Hot 16 oz", distribution="New York."))
    assert match["match_status"] != "product_family_match"


def test_named_catalogue_product_still_wins_over_the_family_tier():
    match = match_recall_to_catalog(_recall("Fresh Salsa, 16 oz tub"))
    assert match["match_status"] == "product_review_required"

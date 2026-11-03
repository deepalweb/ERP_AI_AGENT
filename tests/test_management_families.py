from backend.app.services import management


def _case(case_id, metric_id, priority, domain="inventory", category="critical", exposure=None):
    return {
        "case_id": case_id, "metric_id": metric_id, "domain": domain, "category": category, "horizon": "snapshot",
        "status": "open", "priority": priority, "last_seen_at": "2026-10-05T00:00:00", "business_impact_score": 1.0,
        "latest": {"title": f"title {case_id}", "impact_summary": {"financial_impact_rs": exposure} if exposure is not None else None},
    }


def test_transfer_shortage_and_extreme_events_share_one_family():
    assert management.family_for({"metric_id": "transfer_discrepancy_missing_item", "domain": "inventory"}) == "warehouse_transfer_shortages"
    assert management.family_for({"metric_id": "extreme_event_missing_item", "domain": "inventory"}) == "warehouse_transfer_shortages"
    assert management.family_for({"metric_id": "transfer_discrepancy_s46_missing_item", "domain": "inventory"}) == "warehouse_transfer_shortages"


def test_overages_are_separate_and_damage_evidence_shares_a_family():
    assert management.family_for({"metric_id": "transfer_discrepancy_extra_quantity", "domain": "inventory"}) == "warehouse_transfer_overages"
    assert management.family_for({"metric_id": "transfer_discrepancy_product_damage", "domain": "inventory"}) == "damage_and_writeoffs"
    assert management.family_for({"metric_id": "damage_writeoff", "domain": "inventory"}) == "damage_and_writeoffs"


def test_scoped_completed_variance_cases_join_stock_count_controls_family():
    finding = {
        "metric_id": "completed_stock_count_variance-all-2026-07-19_2026-10-16",
        "domain": "inventory",
    }
    assert management.family_for(finding) == "stock_count_controls"


def test_out_of_v1_domains_are_not_grouped():
    assert management.family_for({"metric_id": "negative_stock_control", "domain": "controls"}) is None
    assert management.family_for({"metric_id": "revenue", "domain": "revenue", "category": "critical"}) is None


def test_parent_priority_is_highest_child_and_driver_is_named():
    items = [
        _case("a", "transfer_discrepancy_extra_quantity", "medium"),
        _case("b", "transfer_discrepancy_missing_item", "critical"),
        _case("c", "extreme_event_missing_item", "medium"),
    ]
    [family] = [f for f in management.families_from_cases(items) if f["key"] == "warehouse_transfer_shortages"]
    assert family["priority"] == "critical"
    assert family["driving_case_id"] == "b"
    assert family["active_children"] == 2


def test_exposure_is_counted_per_child_and_never_summed():
    items = [
        _case("b", "transfer_discrepancy_missing_item", "critical", exposure=100.0),
        _case("c", "extreme_event_missing_item", "medium", exposure=60.0),
    ]
    [family] = [f for f in management.families_from_cases(items) if f["key"] == "warehouse_transfer_shortages"]
    assert family["exposure_quantified_children"] == 2
    assert "exposure_total" not in family
    assert "Not summed" in family["exposure_note"]


def test_duplicate_and_overlapping_children_are_cross_referenced():
    items = [
        _case("variance-global", "completed_stock_count_variance", "critical", exposure=100.0),
        _case("variance-window", "completed_stock_count_variance-all-90d", "critical", exposure=100.0),
    ]
    relationships = [
        {"case_id": "variance-window", "related_case_id": "variance-global", "relationship_type": "duplicate_of"},
    ]
    [family] = [f for f in management.families_from_cases(items, relationships) if f["key"] == "stock_count_controls"]
    by_id = {child["case_id"]: child for child in family["children"]}
    assert by_id["variance-window"]["related_evidence"] == [{
        "case_id": "variance-global", "title": "title variance-global", "relationship_type": "duplicate_of",
        "is_duplicate_of": True,
    }]
    assert by_id["variance-global"]["related_evidence"][0]["case_id"] == "variance-window"


def test_empty_families_are_omitted():
    assert management.families_from_cases([]) == []

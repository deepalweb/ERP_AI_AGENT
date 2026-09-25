"""Tests for backend/app/services/customer_lookup.py -- Customer Lookup
Design, Phase A. Pure unit tests; search_customers/reporting.run_query/
get_predicted_next_basket/appdb.run_query are all monkeypatched so this
never hits a real database.
"""

from backend.app.services import customer_lookup as cl


def _match(partner_id=1, name="Ann Silva", phone="+94 77 300 0003", order_count=5):
    return {"partner_id": partner_id, "name": name, "phone": phone, "email": "ann@example.com", "order_count": order_count}


def _lifecycle_row(as_of_date, revenue=1000.0, last_order=None):
    return {"revenue": revenue, "last_order": last_order, "as_of_date": as_of_date}


def _fake_run_query(lifecycle_rows, percentile_rows):
    """Dispatches on a distinguishing keyword, same pattern as
    test_data_controls.py's _fake_run_query -- LIFECYCLE_SQL and
    VALUE_PERCENTILE_SQL each issue their own query."""
    def run_query(sql, params=None):
        if "PERCENTILE_CONT" in sql:
            return percentile_rows
        return lifecycle_rows
    return run_query


def test_mask_phone_keeps_only_last_four_digits():
    assert cl._mask_phone("+94 77 300 0003") == "•••• 0003"


def test_mask_phone_handles_missing_or_short_numbers():
    assert cl._mask_phone(None) is None
    assert cl._mask_phone("12") is None


def test_get_customer_lookup_returns_not_found_for_no_matches(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [])
    result = cl.get_customer_lookup(name="Nobody")
    assert result == {"status": "not_found"}


def test_get_customer_lookup_masks_every_match_when_multiple(monkeypatch):
    matches = [_match(1, "Ann Silva"), _match(2, "Ann Perera", phone="0773000004")]
    monkeypatch.setattr(cl, "search_customers", lambda **kw: matches)
    result = cl.get_customer_lookup(name="Ann")
    assert result["status"] == "multiple"
    for row in result["matches"]:
        assert set(row) == {"partner_id", "name", "phone_masked", "order_count"}
        assert "0003" in row["phone_masked"] or "0004" in row["phone_masked"]


def test_get_customer_lookup_never_leaks_raw_phone_or_email_in_single_match(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=7)])
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    logged = []
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: logged.append((sql, params)))

    result = cl.get_customer_lookup(phone="+94 77 300 0003")

    assert result["status"] == "found"
    customer = result["customer"]
    assert "+94 77 300 0003" not in str(customer)
    assert "ann@example.com" not in str(customer)
    assert customer["phone_masked"] == "•••• 0003"
    assert customer["email_masked_or_omitted"] is None
    assert customer["partner_id"] == 7


def test_get_customer_lookup_builds_profile_from_lifecycle_row(monkeypatch):
    import datetime

    as_of = datetime.datetime(2026, 9, 20)
    last_order = datetime.datetime(2026, 9, 15)
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=3, order_count=9)])
    monkeypatch.setattr(cl.reporting, "run_query", _fake_run_query(
        [_lifecycle_row(as_of, revenue=45_000.0, last_order=last_order)],
        [{"p80": 40_000.0, "p50": 10_000.0}],
    ))
    monkeypatch.setattr(cl, "_lifecycle_category", lambda row, as_of_date: "active")
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: {"as_of_date": "2026-09-19", "partner_id": partner_id, "products": [], "total_repeat_products": 0})
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: None)

    result = cl.get_customer_lookup(name="Ann")

    assert result["status"] == "found"
    assert result["as_of"] == "2026-09-20"
    customer = result["customer"]
    assert customer["lifecycle_category"] == "active"
    assert customer["lifetime_revenue"] == 45_000.0
    assert customer["last_order_date"] == "2026-09-15"
    assert customer["order_count"] == 9
    assert customer["value_tier"] == "high"  # 45,000 >= p80 of 40,000
    assert customer["next_basket"]["total_repeat_products"] == 0
    assert customer["deterioration"] is None


def test_value_tier_is_classified_against_the_whole_population_not_this_customer(monkeypatch):
    """Acceptance check: a single customer's own revenue must never be
    compared against percentiles computed from only their own revenue --
    p80/p50 here come from a distinct query over every customer, mocked
    independently of the one lifecycle row under test."""
    as_of = __import__("datetime").datetime(2026, 9, 20)
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=5, order_count=2)])
    monkeypatch.setattr(cl.reporting, "run_query", _fake_run_query(
        [_lifecycle_row(as_of, revenue=5_000.0)],  # this customer alone: far below the real p50
        [{"p80": 40_000.0, "p50": 10_000.0}],       # population-wide, unrelated to this customer's own 5,000
    ))
    monkeypatch.setattr(cl, "_lifecycle_category", lambda row, as_of_date: "active")
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: None)

    result = cl.get_customer_lookup(name="Ann")

    assert result["customer"]["value_tier"] == "low"  # 5,000 < p50 of 10,000 -- correctly low, not "high" by default


def test_get_customer_lookup_handles_no_lifecycle_row_gracefully(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=4)])
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: None)

    result = cl.get_customer_lookup(name="Ann")

    assert result["status"] == "found"
    assert result["as_of"] is None
    assert result["customer"]["lifecycle_category"] is None
    assert result["customer"]["lifetime_revenue"] is None


def test_single_match_logs_access_as_masked_not_named_identity(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=11)])
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setenv("APP_AUTH_MODE", "basic")
    calls = []
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: calls.append((sql, params)))

    cl.get_customer_lookup(name="Ann")

    insert_calls = [c for c in calls if "INSERT INTO customer_access_log" in c[0]]
    assert len(insert_calls) == 1
    _, params = insert_calls[0]
    assert params["partner_id"] == 11
    assert params["access_type"] == "analytical_lookup"
    assert params["sensitivity"] == "masked"
    assert params["auth_context"] == "basic"  # deployment auth mode -- not a named user, see docstring


def test_multiple_matches_does_not_log_access(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(1), _match(2)])
    calls = []
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: calls.append((sql, params)))

    cl.get_customer_lookup(name="Ann")

    assert calls == []  # masked search alone is not logged, only the single-match detail path

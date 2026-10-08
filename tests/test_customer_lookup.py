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


def _fake_run_query(lifecycle_rows, percentile_rows, identity_rows=None,
                     returns_rows=None, ticket_rows=None, loyalty_rows=None):
    """Dispatches on a distinguishing keyword, same pattern as
    test_data_controls.py's _fake_run_query -- LIFECYCLE_SQL,
    VALUE_PERCENTILE_SQL, PARTNER_IDENTITY_SQL, and the three
    _customer_context queries each issue their own query. The three
    context fixtures default to [] (not lifecycle_rows) -- without this,
    every existing single-match test would feed a lifecycle row's shape
    into _customer_context's return_order/helpdesk_ticket/loyalty_card
    parsing and KeyError."""
    def run_query(sql, params=None):
        if "PERCENTILE_CONT" in sql:
            return percentile_rows
        if "res_partner" in sql and "WHERE rp.id" in sql:
            return identity_rows or []
        if "FROM return_order" in sql:
            return returns_rows or []
        if "FROM helpdesk_ticket" in sql:
            return ticket_rows or []
        if "FROM loyalty_card" in sql:
            return loyalty_rows or []
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
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
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
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
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
    assert customer["deterioration"] is None  # get_deterioration_window returned no rows


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
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: None)

    result = cl.get_customer_lookup(name="Ann")

    assert result["customer"]["value_tier"] == "low"  # 5,000 < p50 of 10,000 -- correctly low, not "high" by default


def test_get_customer_lookup_handles_no_lifecycle_row_gracefully(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=4)])
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [{"recent_orders": 0, "recent_revenue": 0, "prior_orders": 0, "prior_revenue": 0, "as_of_date": None}])
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: None)

    result = cl.get_customer_lookup(name="Ann")

    assert result["status"] == "found"
    assert result["as_of"] is None
    assert result["customer"]["lifecycle_category"] is None
    assert result["customer"]["lifetime_revenue"] is None
    # No lifecycle row means no last_order/prior_avg_gap -- cadence context
    # is honestly None, but the recent/prior order counts still come through.
    assert result["customer"]["deterioration"]["days_since_last_order"] is None
    assert result["customer"]["deterioration"]["cadence_ratio"] is None


def test_single_match_logs_access_as_masked_not_named_identity(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=11)])
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
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


def test_deterioration_matches_the_recent_vs_prior_shape(monkeypatch):
    """Wave 1 Customer Phase B, measurement layer: reproduces the example
    from the design conversation almost exactly -- 2 recent orders vs 6
    prior, a 34-day-old last order against an 11-day historical interval
    (34 / 11 = 3.09 -> 3.1x)."""
    import datetime

    as_of = datetime.datetime(2026, 9, 20)
    last_order = as_of - datetime.timedelta(days=34)
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [
        {"recent_orders": 2, "recent_revenue": 5_000.0, "prior_orders": 6, "prior_revenue": 18_000.0, "as_of_date": as_of}
    ])
    lifecycle_row = {"last_order": last_order, "prior_avg_gap": 11.0}

    result = cl._deterioration(partner_id=42, lifecycle_row=lifecycle_row, window_days=60)

    assert result["window_days"] == 60
    assert result["as_of"] == "2026-09-20"
    assert result["recent"] == {"orders": 2, "revenue": 5000.0, "avg_order_value": 2500.0}
    assert result["prior"] == {"orders": 6, "revenue": 18000.0, "avg_order_value": 3000.0}
    assert result["days_since_last_order"] == 34
    assert result["historical_reorder_interval_days"] == 11.0
    assert result["cadence_ratio"] == 3.1


def test_deterioration_handles_zero_prior_orders_without_a_division_error(monkeypatch):
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [
        {"recent_orders": 1, "recent_revenue": 500.0, "prior_orders": 0, "prior_revenue": 0.0, "as_of_date": None}
    ])
    result = cl._deterioration(partner_id=1, lifecycle_row=None, window_days=60)
    assert result["prior"] == {"orders": 0, "revenue": 0.0, "avg_order_value": None}
    assert result["cadence_ratio"] is None
    assert result["historical_reorder_interval_days"] is None


def test_deterioration_returns_none_for_a_partner_with_no_orders_at_all(monkeypatch):
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
    assert cl._deterioration(partner_id=999, lifecycle_row=None) is None


def test_get_customer_profile_builds_the_same_analytical_shape_from_a_known_partner_id(monkeypatch):
    """The Customers page's detail view -- a caller who already has a
    partner_id (from a prior search) shouldn't need to search by name/
    phone/email again."""
    import datetime

    as_of = datetime.datetime(2026, 9, 20)
    identity_rows = [{"name": "Ann Silva", "phone": "+94 77 300 0003", "order_count": 9}]
    monkeypatch.setattr(cl.reporting, "run_query", _fake_run_query(
        [_lifecycle_row(as_of, revenue=45_000.0)],
        [{"p80": 40_000.0, "p50": 10_000.0}],
        identity_rows,
    ))
    monkeypatch.setattr(cl, "_lifecycle_category", lambda row, as_of_date: "active")
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
    logged = []
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: logged.append((sql, params)))

    result = cl.get_customer_profile(17008)

    assert result["customer"]["partner_id"] == 17008
    assert result["customer"]["name"] == "Ann Silva"
    assert result["customer"]["phone_masked"] == "•••• 0003"
    assert result["customer"]["order_count"] == 9
    assert result["customer"]["value_tier"] == "high"
    blob = str(result)
    assert "+94 77 300 0003" not in blob
    insert_calls = [c for c in logged if "INSERT INTO customer_access_log" in c[0]]
    assert len(insert_calls) == 1  # single-customer detail path logs, same as get_customer_lookup


def test_get_customer_profile_returns_none_for_an_unknown_partner_id(monkeypatch):
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    assert cl.get_customer_profile(999999) is None


def test_multiple_matches_does_not_log_access(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(1), _match(2)])
    calls = []
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: calls.append((sql, params)))

    cl.get_customer_lookup(name="Ann")

    assert calls == []  # masked search alone is not logged, only the single-match detail path


# ---------------------------------------------------------------------------
# _customer_context -- returns/support tickets/loyalty for one customer's
# own profile. Small, individually-real signals (see the module's own SQL
# constant docstrings for why these never become a population detector).

def _return_row(name="RO/2026/0001", date="2026-09-17", amount=1000.0):
    return {"id": 1, "name": name, "date": __import__("datetime").date.fromisoformat(date), "state": "done", "amount_total": amount}


def _ticket_row(name="T1", stage="New", closed=False, priority="2"):
    return {"id": 1, "name": name, "priority": priority,
            "create_date": __import__("datetime").datetime(2026, 6, 3, 10, 0), "close_date": None,
            "stage_name": stage, "stage_closed": closed}


def _loyalty_row(points=10.0, program="Non-Woven Bag - Free issue"):
    return {"points": points, "program_name": program}


def _fake_context_run_query(returns_rows=None, ticket_rows=None, loyalty_rows=None):
    def run_query(sql, params=None):
        if "FROM return_order" in sql:
            return returns_rows or []
        if "FROM helpdesk_ticket" in sql:
            return ticket_rows or []
        if "FROM loyalty_card" in sql:
            return loyalty_rows or []
        raise AssertionError(f"unexpected query: {sql[:60]}")
    return run_query


def test_customer_context_counts_and_totals_returns(monkeypatch):
    monkeypatch.setattr(cl.reporting, "run_query", _fake_context_run_query(
        returns_rows=[_return_row(amount=500.0), _return_row(amount=300.0)],
    ))
    ctx = cl._customer_context(1)
    assert ctx["returns"]["count"] == 2
    assert ctx["returns"]["total_value"] == 800.0
    assert len(ctx["returns"]["recent"]) == 2


def test_customer_context_counts_open_tickets_by_fold(monkeypatch):
    monkeypatch.setattr(cl.reporting, "run_query", _fake_context_run_query(
        ticket_rows=[_ticket_row(closed=False), _ticket_row(closed=True)],
    ))
    ctx = cl._customer_context(1)
    assert ctx["support_tickets"]["count"] == 2
    assert ctx["support_tickets"]["open_count"] == 1  # only the non-folded (not closed) one


def test_customer_context_sums_loyalty_points_across_programs(monkeypatch):
    monkeypatch.setattr(cl.reporting, "run_query", _fake_context_run_query(
        loyalty_rows=[_loyalty_row(points=10.0, program="A"), _loyalty_row(points=5.0, program="B")],
    ))
    ctx = cl._customer_context(1)
    assert ctx["loyalty"]["points"] == 15.0
    assert ctx["loyalty"]["programs"] == ["A", "B"]


def test_customer_context_loyalty_is_none_when_not_a_member(monkeypatch):
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    ctx = cl._customer_context(1)
    assert ctx["loyalty"] is None
    assert ctx["returns"]["count"] == 0
    assert ctx["support_tickets"]["count"] == 0


def test_customer_profile_includes_context(monkeypatch):
    monkeypatch.setattr(cl, "search_customers", lambda **kw: [_match(partner_id=9)])
    monkeypatch.setattr(cl.reporting, "run_query", lambda sql, params=None: [])
    monkeypatch.setattr(cl, "get_predicted_next_basket", lambda partner_id: None)
    monkeypatch.setattr(cl, "get_deterioration_window", lambda partner_id, window_days=60: [])
    monkeypatch.setattr(cl.appdb, "run_query", lambda sql, params=None: None)

    result = cl.get_customer_lookup(name="Ann")
    assert "context" in result["customer"]
    assert result["customer"]["context"]["returns"]["count"] == 0

"""Tests for backend/app/services/stock_counts.py: stuck physical
stock-count sessions -- discovered live investigating a manager-reported
"huge stock variance" at Colombo 6 that traced to an unfinished 7,724-line
count. Pure unit tests; reporting.run_query is monkeypatched, dispatching on
a distinguishing keyword in each query string since get_stuck_stock_counts
issues three queries in sequence (sessions, lines, last scan).
"""

from datetime import datetime, timedelta

from backend.app.services import stock_counts


def _fake_run_query(sessions_rows, line_rows, last_scan_rows):
    def run_query(sql, params=None):
        if "FROM stock_count sc" in sql:
            return sessions_rows
        if "stock_count_record scr" in sql:
            return last_scan_rows
        if "stock_count_line scl" in sql:
            return line_rows
        raise AssertionError(f"unexpected query: {sql[:60]}")
    return run_query


def _session_row(id, name, state, started, outlet, responsible, review_datetime=None, notes=None):
    return {
        "id": id, "name": name, "state": state, "inprogress_datetime": started,
        "review_datetime": review_datetime, "notes": notes, "outlet": outlet, "responsible": responsible,
    }


def _line_row(count_id, onhand_qty, cost, list_price, was_scanned, avg_cost=0, product="Test Product", sku="000001"):
    return {
        "count_id": count_id, "onhand_qty": onhand_qty, "avg_cost": avg_cost,
        "cost": cost, "list_price": list_price, "product": product, "sku": sku, "was_scanned": was_scanned,
    }


def test_no_stuck_sessions_returns_empty_result(monkeypatch):
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query([], [], []))
    result = stock_counts.get_stuck_stock_counts()
    assert result["sessions"] == []
    assert result["total_value_at_risk_rs"] == 0.0
    assert result["valuation_coverage"] == {
        "total_unscanned_lines": 0, "source_cost_lines": 0, "catalog_estimate_lines": 0, "no_cost_data_lines": 0,
    }


def test_computes_unscanned_value_only_for_unscanned_lines(monkeypatch):
    started = datetime.utcnow() - timedelta(days=3)
    sessions_rows = [
        _session_row(786, "SC/2026/00783", "review", started, "Celeste Daily Colombo 6", "Ashan Kannangara"),
    ]
    line_rows = [
        # scanned -- must NOT count toward unscanned value
        _line_row(786, 100, 50.0, 80.0, True),
        # unscanned, real catalog cost (no avg_cost from source) -- 40 * 20 = 800
        _line_row(786, 40, 20.0, 30.0, False),
        # unscanned, no usable cost (cost > list_price -- inventory._usable_cost rejects it) -- excluded
        _line_row(786, 999, 500.0, 10.0, False),
    ]
    last_scan_rows = [{"count_id": 786, "last_scan_at": started + timedelta(hours=1)}]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query(sessions_rows, line_rows, last_scan_rows))

    result = stock_counts.get_stuck_stock_counts()
    assert len(result["sessions"]) == 1
    s = result["sessions"][0]
    assert s["id"] == 786
    assert s["outlet"] == "Celeste Daily Colombo 6"
    assert s["responsible"] == "Ashan Kannangara"
    assert s["total_lines"] == 3
    assert s["scanned_lines"] == 1
    assert s["unscanned_lines"] == 2
    assert s["unscanned_value_rs"] == 800.0
    assert s["days_stuck"] == 3.0
    assert s["has_scan_activity"] is True
    assert result["total_value_at_risk_rs"] == 800.0
    # Final Stock-Count Financial Disclosure, item 1: the scanned line never
    # enters the coverage tally (coverage is about the UNSCANNED population
    # only); of the 2 unscanned lines, 1 priced via catalog fallback, 1 had
    # no usable cost at all.
    assert result["valuation_coverage"] == {
        "total_unscanned_lines": 2, "source_cost_lines": 0, "catalog_estimate_lines": 1, "no_cost_data_lines": 1,
    }


def test_top_products_aggregates_unscanned_value_across_sessions_and_excludes_scanned_lines(monkeypatch):
    started = datetime.utcnow() - timedelta(days=3)
    sessions_rows = [
        _session_row(1, "SC/A", "review", started, "Celeste Daily Colombo 6", "Someone"),
        _session_row(2, "SC/B", "review", started, "Celeste Daily - Attidiya", "Someone Else"),
    ]
    line_rows = [
        # Same product, unscanned, across two different sessions -- must combine.
        _line_row(1, 10, 10.0, 20.0, False, product="Carrot", sku="000083"),
        _line_row(2, 5, 10.0, 20.0, False, product="Carrot", sku="000083"),
        # Different product, unscanned.
        _line_row(1, 2, 50.0, 60.0, False, product="Coconut", sku="001363"),
        # Scanned -- must not contribute to the product total at all.
        _line_row(1, 100, 10.0, 20.0, True, product="Carrot", sku="000083"),
    ]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query(sessions_rows, line_rows, []))

    result = stock_counts.get_stuck_stock_counts()
    top = {p["sku"]: p for p in result["top_products"]}
    assert top["000083"]["name"] == "Carrot"
    assert top["000083"]["qty"] == 15.0
    assert top["000083"]["value_rs"] == 150.0  # (10 + 5) * 10, scanned line excluded
    assert top["000083"]["lines"] == 2
    assert top["001363"]["value_rs"] == 100.0  # 2 * 50
    # Sorted by value descending.
    assert result["top_products"][0]["sku"] == "000083"


def test_prefers_source_system_avg_cost_over_catalog_price(monkeypatch):
    """Incident Investigation Phase 1/2: Odoo's own per-line avg_cost is a
    more authoritative valuation basis than the app's re-derived catalog
    cost -- verified live to differ by ~1.2% on a real session. avg_cost
    should win whenever it's actually populated (> 0)."""
    started = datetime.utcnow() - timedelta(days=1)
    sessions_rows = [_session_row(9, "SC/X", "review", started, "Celeste Daily Colombo 6", "X")]
    # onhand 10, avg_cost 7 (source) vs catalog cost 20 -- avg_cost must win: 10*7=70, not 10*20=200
    line_rows = [_line_row(9, 10, 20.0, 30.0, False, avg_cost=7.0)]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query(sessions_rows, line_rows, []))

    result = stock_counts.get_stuck_stock_counts()
    assert result["sessions"][0]["unscanned_value_rs"] == 70.0


def test_sessions_sorted_by_unscanned_value_descending(monkeypatch):
    started = datetime.utcnow() - timedelta(days=1)
    sessions_rows = [
        _session_row(1, "SC/A", "review", started, "Celeste Daily - Attidiya", "A"),
        _session_row(2, "SC/B", "draft", started, "Celeste Daily Colombo 6", "B"),
    ]
    line_rows = [
        _line_row(1, 10, 5.0, 10.0, False),  # 50
        _line_row(2, 100, 50.0, 100.0, False),  # 5000
    ]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query(sessions_rows, line_rows, []))

    result = stock_counts.get_stuck_stock_counts()
    assert [s["id"] for s in result["sessions"]] == [2, 1]
    assert result["total_value_at_risk_rs"] == 5050.0


def test_last_scan_at_is_none_when_nothing_was_ever_scanned(monkeypatch):
    started = datetime.utcnow() - timedelta(days=2)
    sessions_rows = [_session_row(3, "SC/C", "review", started, "Celeste Daily Ethul Kotte", "C")]
    line_rows = [_line_row(3, 5, 2.0, 4.0, False)]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query(sessions_rows, line_rows, []))

    result = stock_counts.get_stuck_stock_counts()
    assert result["sessions"][0]["last_scan_at"] is None
    assert result["sessions"][0]["has_scan_activity"] is False


def test_review_started_at_and_notes_pass_through(monkeypatch):
    """Incident Investigation Phase 1: most real stuck sessions have already
    entered 'review' state (not simply abandoned mid-count), and some carry
    a real system-stated reason in notes -- both must reach the finding."""
    started = datetime.utcnow() - timedelta(days=5)
    reviewed = started + timedelta(hours=2)
    sessions_rows = [
        _session_row(
            4, "SC/D", "review", started, "Celeste Daily Colombo 6", "D",
            review_datetime=reviewed, notes="Auto-created for negative stock correction",
        ),
    ]
    line_rows = [_line_row(4, 1, 1.0, 1.0, False)]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_run_query(sessions_rows, line_rows, []))

    result = stock_counts.get_stuck_stock_counts()
    s = result["sessions"][0]
    assert s["review_started_at"] == reviewed.isoformat()
    assert s["notes"] == "Auto-created for negative stock correction"


def _fake_detail_run_query(header_rows, line_rows, scan_rows):
    def run_query(sql, params=None):
        # Check scan events first: SESSION_SCAN_EVENTS_SQL also selects
        # product_name, so it would match the line-detail check below too.
        if "FROM stock_count_record scr" in sql:
            return scan_rows
        if "FROM stock_count_line scl" in sql:
            return line_rows
        if "FROM stock_count sc" in sql:
            return header_rows
        raise AssertionError(f"unexpected query: {sql[:60]}")
    return run_query


def test_session_detail_returns_none_outside_scope(monkeypatch):
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_detail_run_query([], [], []))
    assert stock_counts.get_stock_count_session_detail(999999) is None


def test_session_detail_distinguishes_responsible_from_creator_and_scan_events_can_be_empty(monkeypatch):
    started = datetime.utcnow() - timedelta(days=10)
    header_rows = [{
        "id": 431, "name": "SC/2026/00428", "state": "review", "inprogress_datetime": started,
        "review_datetime": started + timedelta(hours=5), "completed_datetime": None,
        "notes": None, "create_date": started, "write_date": started + timedelta(days=1),
        "outlet": "Celeste Daily Ethul Kotte", "responsible": "Dilanka Ranasinghe", "created_by": "Fazlan Fairooz",
    }]
    line_rows = [{
        "id": 1, "onhand_qty": 5, "counted_qty": 0, "variance": -5, "variance_value": -500.0, "avg_cost": 100.0,
        "cost": 90.0, "list_price": 120.0, "first_scan_datetime": None,
        "product_name": "Test Product", "sku": "000638", "was_scanned": False,
    }]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_detail_run_query(header_rows, line_rows, []))

    detail = stock_counts.get_stock_count_session_detail(431)
    assert detail["responsible"] == "Dilanka Ranasinghe"
    assert detail["created_by"] == "Fazlan Fairooz"
    assert detail["responsible"] != detail["created_by"]
    assert detail["scan_events"] == []
    assert detail["has_scan_activity"] is False
    assert detail["unscanned_value_rs"] == 500.0  # avg_cost (100) preferred over catalog cost (90)
    assert detail["lines"][0]["sku"] == "000638"


def _header_row(id=1, name="SC/X", state="review", started=None, notes=None, responsible="R", created_by="C"):
    started = started or datetime.utcnow() - timedelta(days=1)
    return {
        "id": id, "name": name, "state": state, "inprogress_datetime": started,
        "review_datetime": None, "completed_datetime": None, "notes": notes,
        "create_date": started, "write_date": started,
        "outlet": "Celeste Daily Colombo 6", "responsible": responsible, "created_by": created_by,
    }


def test_session_detail_includes_a_live_retrieval_timestamp_distinct_from_case_evidence(monkeypatch):
    """Final Integrity Check item 2: this endpoint is a live read, never a
    replay of the case's own preserved case_observations snapshot -- the
    caller needs its own timestamp to label it as such."""
    before = datetime.utcnow()
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_detail_run_query([_header_row()], [], []))
    detail = stock_counts.get_stock_count_session_detail(1)
    assert datetime.fromisoformat(detail["retrieved_at"]) >= before


def test_valuation_basis_labels_each_line_by_how_it_was_actually_valued(monkeypatch):
    """Final Integrity Check item 1: source_cost/catalog_estimate/no_cost_data/
    scanned must reflect exactly which branch of the valuation logic a line
    actually took -- verified live this genuinely diverges from Odoo's own
    raw variance_value on real data (a real session showed Rs 3,267.59 by
    this app's basis vs Rs 40.89 summing Odoo's own signed field), so the
    per-line label is what lets a manager reconcile the two honestly."""
    header_rows = [_header_row()]
    line_rows = [
        # avg_cost present -- source_cost basis, unscanned.
        {"id": 1, "onhand_qty": 2, "counted_qty": 0, "variance": -2, "variance_value": -20.0, "avg_cost": 10.0,
         "cost": 5.0, "list_price": 8.0, "first_scan_datetime": None, "product_name": "A", "sku": "A1", "was_scanned": False},
        # avg_cost 0, catalog cost usable -- catalog_estimate basis.
        {"id": 2, "onhand_qty": 3, "counted_qty": 0, "variance": -3, "variance_value": 0.0, "avg_cost": 0.0,
         "cost": 7.0, "list_price": 9.0, "first_scan_datetime": None, "product_name": "B", "sku": "B1", "was_scanned": False},
        # avg_cost 0, no usable catalog cost either -- no_cost_data, excluded from the total.
        {"id": 3, "onhand_qty": 4, "counted_qty": 0, "variance": -4, "variance_value": 0.0, "avg_cost": 0.0,
         "cost": 0.0, "list_price": 0.0, "first_scan_datetime": None, "product_name": "C", "sku": "C1", "was_scanned": False},
        # scanned -- real count, basis label is "scanned" regardless of cost fields.
        {"id": 4, "onhand_qty": 1, "counted_qty": 1, "variance": 0, "variance_value": 0.0, "avg_cost": 15.0,
         "cost": 15.0, "list_price": 20.0, "first_scan_datetime": datetime.utcnow(), "product_name": "D", "sku": "D1", "was_scanned": True},
    ]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_detail_run_query(header_rows, line_rows, []))

    detail = stock_counts.get_stock_count_session_detail(1)
    by_sku = {l["sku"]: l for l in detail["lines"]}
    assert by_sku["A1"]["valuation_basis"] == "source_cost"
    assert by_sku["B1"]["valuation_basis"] == "catalog_estimate"
    assert by_sku["C1"]["valuation_basis"] == "no_cost_data"
    assert by_sku["D1"]["valuation_basis"] == "scanned"
    # Total only reflects the two priced unscanned lines: 2*10 + 3*7 = 41; line C1 (no cost) and D1 (scanned) excluded.
    assert detail["unscanned_value_rs"] == 41.0


def _fake_completed_variance_run_query(session_rows, product_type_rows, line_rows=None):
    def run_query(sql, params=None):
        if "celeste_product_type" in sql:
            return product_type_rows
        if "scl.counted_qty, scl.onhand_qty" in sql:
            return line_rows or []
        if "GROUP BY sc.id, sc.name, rs.id" in sql:
            return session_rows
        raise AssertionError(f"unexpected query: {sql[:60]}")
    return run_query


def _completed_session_row(id, name, store_id, outlet, completed, lines, net, gross):
    return {
        "id": id, "name": name, "store_id": store_id, "outlet": outlet, "completed_datetime": completed,
        "lines": lines, "net_variance_value": net, "gross_abs_variance_value": gross,
    }


def test_get_completed_stock_count_variance_computes_totals_and_breakdowns(monkeypatch):
    session_rows = [
        _completed_session_row(866, "SC/2026/00863", 1, "Celeste Daily Colombo 6",
                                datetime(2026, 9, 8), 7735, 2_084_739.66, 7_098_828.12),
        _completed_session_row(764, "SC/2026/00761", 2, "Celeste Daily Ethul Kotte",
                                datetime(2026, 8, 7), 2, 321_682.25, 321_682.25),
    ]
    product_type_rows = [
        {"product_type": "INVENTORY", "gross_abs_variance_value": 7_000_000.0, "net_variance_value": 2_000_000.0},
        {"product_type": "PACKING MATERIAL", "gross_abs_variance_value": 420_510.37, "net_variance_value": 406_421.91},
    ]
    monkeypatch.setattr(
        stock_counts.reporting, "run_query", _fake_completed_variance_run_query(session_rows, product_type_rows),
    )

    result = stock_counts.get_completed_stock_count_variance(start_date="2026-06-01", end_date="2026-09-15")
    assert result["session_count"] == 2
    assert result["gross_abs_variance_value_rs"] == 7_420_510.37
    assert result["net_variance_value_rs"] == 2_406_421.91
    # Sorted by gross_abs_variance_value_rs descending -- the dominant session first.
    assert result["sessions"][0]["name"] == "SC/2026/00863"

    outlet_breakdown = next(b for b in result["breakdowns"] if b["dimension"] == "store")
    assert outlet_breakdown["total"] == 7_420_510.37
    assert [c["name"] for c in outlet_breakdown["top_contributors"]] == ["Celeste Daily Colombo 6", "Celeste Daily Ethul Kotte"]

    product_type_breakdown = next(b for b in result["breakdowns"] if b["dimension"] == "product_type")
    assert [c["name"] for c in product_type_breakdown["top_contributors"]] == ["INVENTORY", "PACKING MATERIAL"]


def test_get_completed_stock_count_variance_normalizes_variance_by_counted_value(monkeypatch):
    # Session A: Rs 500K variance on Rs 100M counted stock (0.5%) -- healthy
    # despite the big raw number. Session B: Rs 150K on Rs 5M (3%) -- worse,
    # despite the smaller raw number. Raw Rs alone would rank these backwards.
    # Both sessions counted exactly what was on the system here (no
    # shortages), so vs-system and vs-counted agree -- the divergence case
    # is covered separately below.
    session_rows = [
        _completed_session_row(1, "SC-A", 1, "Outlet A", datetime(2026, 9, 1), 100, 0.0, 500_000.0),
        _completed_session_row(2, "SC-B", 2, "Outlet B", datetime(2026, 9, 1), 100, 0.0, 150_000.0),
    ]
    line_rows = (
        [{"count_id": 1, "counted_qty": 100, "onhand_qty": 100, "avg_cost": 1_000_000.0, "cost": 0.0, "list_price": 0.0}]
        + [{"count_id": 2, "counted_qty": 100, "onhand_qty": 100, "avg_cost": 50_000.0, "cost": 0.0, "list_price": 0.0}]
    )
    monkeypatch.setattr(
        stock_counts.reporting, "run_query",
        _fake_completed_variance_run_query(session_rows, [], line_rows),
    )

    result = stock_counts.get_completed_stock_count_variance(start_date="2026-06-01", end_date="2026-09-15")
    by_name = {s["name"]: s for s in result["sessions"]}
    assert by_name["SC-A"]["counted_value_rs"] == 100_000_000.0
    assert by_name["SC-A"]["variance_rate_vs_counted_pct"] == 0.5
    assert by_name["SC-A"]["variance_rate_vs_system_pct"] == 0.5
    assert by_name["SC-B"]["counted_value_rs"] == 5_000_000.0
    assert by_name["SC-B"]["variance_rate_vs_counted_pct"] == 3.0
    assert result["counted_value_rs"] == 105_000_000.0
    assert result["variance_rate_vs_counted_pct"] == round(650_000 / 105_000_000 * 100, 2)

    outlet_breakdown = next(b for b in result["breakdowns"] if b["dimension"] == "store")
    outlet_a = next(c for c in outlet_breakdown["top_contributors"] if c["name"] == "Outlet A")
    assert outlet_a["variance_rate_vs_counted_pct"] == 0.5


def test_get_completed_stock_count_variance_rate_is_none_without_priceable_lines(monkeypatch):
    session_rows = [_completed_session_row(1, "SC-A", 1, "Outlet A", datetime(2026, 9, 1), 100, 0.0, 500_000.0)]
    # No usable cost anywhere (avg_cost 0, catalog cost 0) -- both
    # denominators stay 0, so both rates must be None (unknown), never a
    # fabricated 0%.
    line_rows = [{"count_id": 1, "counted_qty": 100, "onhand_qty": 100, "avg_cost": 0.0, "cost": 0.0, "list_price": 0.0}]
    monkeypatch.setattr(
        stock_counts.reporting, "run_query",
        _fake_completed_variance_run_query(session_rows, [], line_rows),
    )
    result = stock_counts.get_completed_stock_count_variance(start_date="2026-06-01", end_date="2026-09-15")
    assert result["sessions"][0]["counted_value_rs"] == 0.0
    assert result["sessions"][0]["variance_rate_vs_counted_pct"] is None
    assert result["sessions"][0]["variance_rate_vs_system_pct"] is None
    assert result["variance_rate_vs_counted_pct"] is None
    assert result["variance_rate_vs_system_pct"] is None


def test_get_completed_stock_count_variance_vs_system_fixes_the_complete_shortage_blind_spot(monkeypatch):
    # The exact example from the math review: Product A (system 100,
    # counted 0, Rs 1,000/unit -- a complete shortage, Rs 100,000 variance)
    # contributes NOTHING to counted value, so vs-counted reads a
    # meaningless 100% even though only HALF the session's stock (by
    # system value) was actually wrong. vs-system correctly halves it,
    # since Product A's full system-recorded value still counts in that
    # denominator.
    session_rows = [_completed_session_row(1, "SC-A", 1, "Outlet A", datetime(2026, 9, 1), 2, 100_000.0, 100_000.0)]
    line_rows = [
        {"count_id": 1, "counted_qty": 0, "onhand_qty": 100, "avg_cost": 1_000.0, "cost": 0.0, "list_price": 0.0},
        {"count_id": 1, "counted_qty": 100, "onhand_qty": 100, "avg_cost": 1_000.0, "cost": 0.0, "list_price": 0.0},
    ]
    monkeypatch.setattr(
        stock_counts.reporting, "run_query",
        _fake_completed_variance_run_query(session_rows, [], line_rows),
    )
    result = stock_counts.get_completed_stock_count_variance(start_date="2026-06-01", end_date="2026-09-15")
    session = result["sessions"][0]
    assert session["counted_value_rs"] == 100_000.0  # only Product B
    assert session["system_value_rs"] == 200_000.0  # both products
    assert session["variance_rate_vs_counted_pct"] == 100.0  # the misleading figure
    assert session["variance_rate_vs_system_pct"] == 50.0  # the honest one


def test_get_completed_stock_count_variance_returns_zero_state_for_no_sessions(monkeypatch):
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_completed_variance_run_query([], []))
    result = stock_counts.get_completed_stock_count_variance(start_date="2026-06-01", end_date="2026-09-15")
    assert result["session_count"] == 0
    assert result["sessions"] == []
    assert result["gross_abs_variance_value_rs"] == 0.0
    outlet_breakdown = next(b for b in result["breakdowns"] if b["dimension"] == "store")
    assert outlet_breakdown["top_contributors"] == []


def test_get_completed_stock_count_variance_narrows_to_one_store(monkeypatch):
    session_rows = [
        _completed_session_row(866, "SC/2026/00863", 1, "Celeste Daily Colombo 6",
                                datetime(2026, 9, 8), 7735, 2_084_739.66, 7_098_828.12),
        _completed_session_row(764, "SC/2026/00761", 2, "Celeste Daily Ethul Kotte",
                                datetime(2026, 8, 7), 2, 321_682.25, 321_682.25),
    ]
    monkeypatch.setattr(stock_counts.reporting, "run_query", _fake_completed_variance_run_query(session_rows, []))

    result = stock_counts.get_completed_stock_count_variance(start_date="2026-06-01", end_date="2026-09-15", store_id=1)
    assert result["session_count"] == 1
    assert result["sessions"][0]["outlet"] == "Celeste Daily Colombo 6"
    # PRODUCT_TYPE_VARIANCE_SQL isn't store-scoped -- narrowing to one store
    # must drop that breakdown entirely rather than silently mismatch it.
    assert not any(b["dimension"] == "product_type" for b in result["breakdowns"])


def test_session_detail_retrievable_for_a_done_or_cancelled_session_independent_of_stuck_filter(monkeypatch):
    """Final Integrity Check item 3: SESSION_HEADER_SQL has no state filter
    (unlike STUCK_SESSIONS_SQL) -- a session that has since completed or
    been cancelled in the source system must still be individually
    inspectable, correctly labeled by its OWN state, entirely independent
    of whatever the aggregate 'stuck' population or the Celeste case's own
    status currently says. Deterministic fixtures stand in for a real
    resolved session (none of the current real stuck sessions has actually
    completed yet)."""
    for state in ("done", "cancel"):
        monkeypatch.setattr(
            stock_counts.reporting, "run_query",
            _fake_detail_run_query([_header_row(state=state)], [], []),
        )
        detail = stock_counts.get_stock_count_session_detail(1)
        assert detail["state"] == state

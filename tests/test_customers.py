"""get_customer_intelligence's segmentation is DERIVED from
customer_predictions.get_lifecycle_rows() (see customers.py's module
docstring for why -- the two used to be independently-tuned rules that
could silently disagree). These tests monkeypatch get_lifecycle_rows
directly, pre-labeled with lifecycle_category, so they check customers.py's
own LIFECYCLE_TO_SEGMENT mapping and repeat-rate math without re-deriving
_lifecycle_category's SQL-shaped classification (already covered by
test_customer_lifecycle_segment.py)."""

from datetime import datetime

import pytest

from backend.app.services import customer_predictions as cp
from backend.app.services import customers

AS_OF = datetime(2026, 10, 5)


@pytest.fixture(autouse=True)
def _no_live_retention_trend(monkeypatch):
    # retention_trend isn't what these tests check -- stub it out so they
    # don't depend on a real reporting DB connection.
    monkeypatch.setattr(customers.intelligence, "get_trend", lambda metric: [])


def _row(partner_id, category, revenue, order_count=5, days_since_last=1):
    return {
        "partner_id": partner_id, "lifecycle_category": category,
        "order_count": order_count, "revenue": revenue,
        "last_order": AS_OF, "days_since_last": days_since_last,
    }


async def test_every_lifecycle_category_maps_to_exactly_one_known_segment(monkeypatch):
    rows = [_row(i, cid, revenue=1000.0) for i, (cid, _, _) in enumerate(cp.LIFECYCLE_CATEGORIES)]
    monkeypatch.setattr(cp, "get_lifecycle_rows", lambda: (rows, AS_OF))
    result = await customers.get_customer_intelligence()
    assert result["as_of_date"] == AS_OF.date().isoformat()
    counted = sum(s["count"] for s in result["segments"])
    assert counted == len(rows)  # every category landed in one of the 4 known segments


async def test_win_back_and_excluded_count_as_active_dormant_counts_as_one_time(monkeypatch):
    rows = [_row(1, "win_back", 1000.0), _row(2, "excluded", 1000.0), _row(3, "dormant", 1000.0)]
    monkeypatch.setattr(cp, "get_lifecycle_rows", lambda: (rows, AS_OF))
    result = await customers.get_customer_intelligence()
    by_id = {s["id"]: s["count"] for s in result["segments"]}
    assert by_id["active"] == 2  # win_back + excluded
    assert by_id["one_time"] == 1  # dormant
    assert by_id["at_risk"] == 0
    assert by_id["churned"] == 0


async def test_due_and_lapsing_both_count_as_at_risk(monkeypatch):
    rows = [_row(1, "due", 500.0), _row(2, "lapsing", 500.0)]
    monkeypatch.setattr(cp, "get_lifecycle_rows", lambda: (rows, AS_OF))
    result = await customers.get_customer_intelligence()
    by_id = {s["id"]: s["count"] for s in result["segments"]}
    assert by_id["at_risk"] == 2


async def test_repeat_purchase_rate_counts_order_count_not_segment(monkeypatch):
    # A brand-new customer with 1 order segments as "active" (see
    # LIFECYCLE_TO_SEGMENT), but must NOT count as a repeat customer --
    # repeat_purchase_rate_pct is order_count > 1, independent of segment.
    rows = [
        _row(1, "new", 1000.0, order_count=1),
        _row(2, "active", 1000.0, order_count=5),
    ]
    monkeypatch.setattr(cp, "get_lifecycle_rows", lambda: (rows, AS_OF))
    result = await customers.get_customer_intelligence()
    assert result["repeat_purchase_rate_pct"] == 50.0  # 1 of 2 customers


async def test_at_risk_customers_are_named_and_sorted_by_revenue(monkeypatch):
    rows = [_row(1, "due", revenue=500.0), _row(2, "lapsing", revenue=5000.0)]
    monkeypatch.setattr(cp, "get_lifecycle_rows", lambda: (rows, AS_OF))

    async def fake_fetch_report(sql, params=None):
        assert "res_partner" in sql
        return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    monkeypatch.setattr(customers.reporting, "fetch_report", fake_fetch_report)
    result = await customers.get_customer_intelligence()
    assert [c["name"] for c in result["at_risk_customers"]] == ["Bob", "Alice"]


async def test_no_customers_returns_empty_payload_without_crashing(monkeypatch):
    monkeypatch.setattr(cp, "get_lifecycle_rows", lambda: ([], None))
    result = await customers.get_customer_intelligence()
    assert result["segments"] == []
    assert result["at_risk_customers"] == []
    assert result["repeat_purchase_rate_pct"] == 0.0

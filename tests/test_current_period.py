from datetime import UTC, datetime

import pytest

from mcp_server.tools import current_period, intelligence


def rows(values=(100, 120, 60), denominators=(20, 20, 20)):
    # Odoo reporting timestamps are UTC stored without timezone information.
    return [{"start": datetime(2026, month, 1, tzinfo=UTC).replace(tzinfo=None),
             "finish": datetime(2026, month, 27, 11, 4, 58, 1, tzinfo=UTC).replace(tzinfo=None),
             "as_of": datetime(2026, 9, 27, 11, 4, 58, tzinfo=UTC).replace(tzinfo=None), "comparable": True,
             "numerator": value, "denominator": denominator}
            for month, value, denominator in zip((7, 8, 9), values, denominators)]


def test_latest_month_is_assessed_against_matching_windows(monkeypatch):
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: rows())
    result = current_period.assess("revenue")
    assert result["confirm"] == {"period": "2026-09", "current": 60, "baseline": 110,
                                 "pct_change": -45.45, "unit": "currency"}
    assert result["category"] == "critical"
    assert result["provisional"] is True
    assert result["source_data_through"] == "2026-09-27T11:04:58"
    assert result["comparison"]["baseline_windows"][-1]["end_exclusive"] == "2026-08-27T11:04:58.000001"


def test_shorter_month_is_not_compared_as_equal_duration(monkeypatch):
    data = rows()
    data[0]["comparable"] = False
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: data)
    result = current_period.assess("revenue")
    assert result["confirm"]["baseline"] == 120
    assert result["comparison"]["excluded_short_months"] == ["2026-07"]


def test_rates_and_aov_use_weighted_totals(monkeypatch):
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: rows((10, 20, 30), (100, 200, 150)))
    assert current_period.assess("cancellation_rate")["confirm"]["current"] == 20
    assert current_period.assess("average_order_value")["confirm"]["current"] == 0.2


def test_zero_exposure_does_not_substitute_an_earlier_month(monkeypatch):
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: rows(denominators=(20, 20, 0)))
    result = current_period.assess("cancellation_rate")
    assert result["confirm"]["period"] == "2026-09"
    assert result["confirm"]["current"] is None
    assert result["status"] == "insufficient_data"
    assert result["category"] is None


def test_first_month_still_shows_available_measurement(monkeypatch):
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: rows()[-1:])
    result = current_period.assess("revenue")
    assert result["confirm"]["current"] == 60
    assert result["confirm"]["baseline"] is None
    assert result["status"] == "insufficient_data"


def test_source_failure_is_explicit_and_is_not_healthy(monkeypatch):
    def fail(sql):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(current_period, "_read_rows", fail)
    result = current_period.assess("revenue")
    assert result["status"] == "unavailable"
    assert result["category"] is None
    assert "Source measurement unavailable" in result["reason"]


@pytest.mark.parametrize("failure_kind", ["pool_timeout", "query_timeout"])
def test_timeouts_are_not_misreported_as_an_unreachable_database(monkeypatch, failure_kind):
    from sqlalchemy.exc import OperationalError, TimeoutError

    class Cancelled(Exception):
        sqlstate = "57014"

    def fail(sql):
        if failure_kind == "pool_timeout":
            raise TimeoutError("pool busy")
        raise OperationalError("SELECT", {}, Cancelled())

    monkeypatch.setattr(current_period, "_read_rows", fail)
    result = current_period.assess("revenue")
    assert result["failure_kind"] == failure_kind
    assert result["status"] == "unavailable"
    assert "reachable" not in result["reason"]


def test_every_registered_metric_has_current_period_coverage(monkeypatch):
    assert set(current_period.SOURCES) == set(intelligence.METRICS)
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: rows())
    current_period.assess_all.cache_clear()
    result = current_period.assess_all()
    assert {r["metric_id"] for r in result["items"]} == set(intelligence.METRICS)
    assert all(r["confirm"]["period"] == "2026-09" for r in result["items"])
    current_period.assess_all.cache_clear()


@pytest.mark.parametrize("metric_id", intelligence.METRICS)
def test_sql_keeps_latest_record_and_explicit_window_bounds(metric_id):
    sql = current_period.window_sql(metric_id)
    assert "MAX(occurred_at)::timestamp AS as_of" in sql
    assert "INTERVAL '1 microsecond'" in sql
    if metric_id == "customer_retention_rate":
        assert "s.first_seen<w.finish" in sql
    elif metric_id == "supplier_concentration":
        assert "s.occurred_at>=w.start AND s.occurred_at<w.finish" in sql
    else:
        assert "s.occurred_at < date_trunc('month',s.occurred_at)" in sql
    assert "comparable" in sql
    if not metric_id.startswith(("supplier_", "purchase_")):
        assert str(current_period.CELESTE_STORE_IDS) in sql


def test_retention_uses_full_previous_cohort_and_supplier_ties_are_not_duplicated():
    assert "p.month=w.start-INTERVAL '1 month'" in current_period.window_sql("customer_retention_rate")
    assert "MAX(value) AS numerator,SUM(value) AS denominator" in current_period.window_sql("supplier_concentration")


def test_current_period_endpoint_is_read_only_and_can_refresh(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.main import app

    monkeypatch.setenv("APP_AUTH_MODE", "local")
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: rows())
    current_period.assess_all.cache_clear()
    response = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)).get("/intelligence/current-period?refresh=true")
    assert response.status_code == 200
    assert len(response.json()["items"]) == len(intelligence.METRICS)
    current_period.assess_all.cache_clear()

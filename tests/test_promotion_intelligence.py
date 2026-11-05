from datetime import date

from mcp_server.tools import promotion_intelligence as pi


def _pop_row(pct):
    return {"avg_discount_pct": pct}


def _active_row(id_=1, name="Promo A", applied_to="both", pct=17.0, value=50000.0, n=100):
    return {"id": id_, "name": name, "applied_to": applied_to, "start_date": date(2026, 11, 1),
            "end_date": date(2026, 11, 30), "avg_discount_pct": pct, "total_discount_value": value, "n_products": n}


def _build(monkeypatch, population_rates, active_rows):
    def fake_run_query(sql, params=None):
        if "store_promotion sp" in sql and "sp.start_date <= CURRENT_DATE" in sql:
            return active_rows
        return [_pop_row(p) for p in population_rates]
    monkeypatch.setattr(pi.reporting, "run_query", fake_run_query)
    return pi.build_promotion_intelligence_findings()


def test_no_population_data_produces_no_case(monkeypatch):
    assert _build(monkeypatch, [], []) == []


def test_normal_active_promotion_does_not_fire(monkeypatch):
    population = [16.0, 17.0, 18.0, 16.5, 17.5] * 18  # median ~17
    active = [_active_row(pct=18.0)]  # well under 1.5x median
    assert _build(monkeypatch, population, active) == []


def test_outlier_active_promotion_fires(monkeypatch):
    population = [16.0, 17.0, 18.0, 16.5, 17.5] * 18  # median ~17
    active = [_active_row(id_=109, name="September - Salary_Week 1", pct=29.0, n=25)]
    [finding] = _build(monkeypatch, population, active)
    assert finding["metric_id"] == "promotion_discount_outlier"
    assert finding["flagged_promotions"][0]["name"] == "September - Salary_Week 1"
    assert finding["flagged_promotions"][0]["ratio_to_median"] > pi.RATIO_TO_MEDIAN

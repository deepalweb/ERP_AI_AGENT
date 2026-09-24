from datetime import date, timedelta

import pytest

from analytics.outlet_orders import baseline, evaluate, prepare_series, promotion_decision, score


def history(n=140):
    start = date(2026, 1, 1)
    return {
        "1": {
            "dates": [(start + timedelta(days=i)).isoformat() for i in range(n)],
            "values": [100 + i % 7 for i in range(n)],
        }
    }


def test_missing_days_are_unknown_and_partial_edges_removed():
    start = date(2026, 1, 1)
    rows = [
        {"store_id": 1, "day": start + timedelta(days=i), "orders": 10}
        for i in range(140)
        if i != 10
    ]
    series, quality = prepare_series(rows, start + timedelta(days=139))
    assert quality["1"]["unknown_days"] == ["2026-01-11"]
    assert series["1"]["dates"][0] == "2026-01-12"
    assert series["1"]["dates"][-1] == "2026-05-19"
    assert set(series["1"]["values"]) == {10}


def test_weekday_baselines_align_to_next_day():
    assert baseline(list(range(28)), "seasonal_naive") == list(range(21, 28))
    assert baseline(list(range(28)), "weekday_mean_4w") == [10.5 + i for i in range(7)]


def test_model_never_sees_target_or_future_and_holdout_is_disjoint():
    calls = []

    def model(values):
        calls.append(list(values))
        return values[-7:], None

    data = history()
    result = evaluate(data, model)
    assert [len(v) for v in calls] == list(range(84, 140, 7))
    assert all(v == data["1"]["values"][: len(v)] for v in calls)
    validation = {r["target"] for r in result["predictions"] if r["split"] == "validation"}
    holdout = {r["target"] for r in result["predictions"] if r["split"] == "holdout"}
    assert max(validation) < min(holdout)
    assert result["metrics"]["holdout"]["seasonal_naive"]["overall"]["mae"] == 0


def test_metrics_do_not_hide_zero_denominator_or_signed_bias():
    metric = score([{"actual": 0, "prediction": 5}])
    assert metric["wape_pct"] is None and metric["bias_pct"] is None
    metric = score([{"actual": 10, "prediction": 8, "q10": 7, "q90": 11}])
    assert metric["wape_pct"] == 20
    assert metric["bias_pct"] == -20
    assert metric["coverage_80_pct"] == 100


def test_stale_data_blocks_even_perfect_model():
    result = evaluate(history())
    decision = promotion_decision(result, date(2026, 9, 15), date(2026, 9, 18))
    assert not decision["eligible_for_integration"]
    assert "stale" in decision["reasons"][0]


def test_bad_model_outputs_fail_closed():
    with pytest.raises(ValueError, match="Invalid point"):
        evaluate(history(), lambda _: ([float("nan")] * 7, None))


def test_non_daily_import_cannot_silently_shift_weekdays():
    data = history()
    data["1"]["dates"][10] = data["1"]["dates"][9]
    with pytest.raises(ValueError, match="uninterrupted"):
        evaluate(data)


def test_model_requires_accuracy_and_interval_evidence_before_selection():
    data = history()
    data["1"]["values"] = list(range(100, 240))

    def trend_model(values):
        points = [values[-1] + h for h in range(1, 8)]
        points[0] += 3  # one of seven actuals outside the nominal 80% band
        return points, [[p - 2, p + 2] for p in points]

    result = evaluate(data, trend_model)
    decision = promotion_decision(result, date(2026, 9, 18), date(2026, 9, 18))
    assert decision["candidate"] == "timesfm_2.5"
    assert decision["eligible_for_integration"]

    def uninformative_intervals(values):
        points, _ = trend_model(values)
        return points, [[0, 100000] for _ in points]

    result = evaluate(data, uninformative_intervals)
    decision = promotion_decision(result, date(2026, 9, 18), date(2026, 9, 18))
    assert decision["candidate"] == "seasonal_naive"

import json
from datetime import date

from backend.app.services import forecast


def test_get_outlet_order_forecast_returns_none_when_no_artifact_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(forecast, "OUTLET_FORECAST_PATH", tmp_path / "missing.json")
    assert forecast.get_outlet_order_forecast() is None


def test_get_outlet_order_forecast_reads_the_artifact_and_maps_outlet_names(monkeypatch, tmp_path):
    # live_track_record has its own tests (test_forecast_outcomes.py) -- stub
    # it here so this test stays scoped to the artifact-reading/mapping logic
    # and never touches the real appdb.
    monkeypatch.setattr(forecast.forecast_outcomes, "get_track_record", lambda: None)
    artifact = {
        "generated_at": "2026-11-04T03:48:46+00:00",
        "horizon_days": 7,
        "source": {"latest_source_local": "2026-10-31T17:34:52", "end_exclusive": "2026-10-31",
                   "source_age_days": 4, "is_stale": True},
        "model": {"checkpoint": "google/timesfm-2.5-200m-pytorch"},
        "outlets": {
            "1": {"history_tail": {"dates": ["2026-10-30"], "values": [967]},
                  "forecast_dates": ["2026-10-31"], "point": [980.6], "q10": [888.8], "q90": [1069.1]},
            "999": {"history_tail": {"dates": [], "values": []},
                    "forecast_dates": [], "point": [], "q10": [], "q90": []},
        },
    }
    path = tmp_path / "outlet_forecast.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(forecast, "OUTLET_FORECAST_PATH", path)

    result = forecast.get_outlet_order_forecast()
    assert result["source"]["is_stale"] is True
    by_store = {o["store_id"]: o for o in result["outlets"]}
    assert by_store[1]["outlet"] == "Celeste Daily Colombo 6"
    assert by_store[1]["point"] == [980.6]
    assert by_store[1]["accuracy"]["wape_pct"] == 5.59
    # An outlet id not in CELESTE_STORE_NAMES still comes through, just unlabeled -- never dropped silently.
    assert by_store[999]["outlet"] == "Store 999"
    # ...and an unrecognised outlet id has no measured accuracy row either -- None, not a crash.
    assert by_store[999]["accuracy"] is None
    assert result["accuracy"]["overall"]["wape_pct"] == 7.48
    assert result["accuracy"]["live_track_record"] is None


def _trend_row(month, value, is_partial):
    return {"month": month, "value": value, "is_partial": is_partial}


def test_unpartial_month_end_leaves_a_genuinely_incomplete_month_alone():
    trend = [_trend_row("2026-09", 100, False), _trend_row("2026-10", 50, True)]
    # Feed stopped on the 20th -- the month's own last day (31) was never reached.
    result = forecast._unpartial_month_end(trend, date(2026, 10, 20))
    assert result[-1]["is_partial"] is True


def test_unpartial_month_end_clears_partial_when_the_calendar_month_end_was_reached():
    trend = [_trend_row("2026-09", 100, False), _trend_row("2026-10", 207_456_085.67, True)]
    # Latest order is on Oct 31 itself, October's real last day, even though
    # that day's own feed cuts off mid-afternoon (see module docstring).
    result = forecast._unpartial_month_end(trend, date(2026, 10, 31))
    assert result[-1]["is_partial"] is False
    assert result[-1]["month"] == "2026-10"


def test_unpartial_month_end_handles_empty_trend_and_missing_date():
    assert forecast._unpartial_month_end([], date(2026, 10, 31)) == []
    trend = [_trend_row("2026-10", 1, True)]
    assert forecast._unpartial_month_end(trend, None) == trend


def test_history_tail_keeps_only_complete_months_and_the_last_n():
    trend = [_trend_row(f"2026-{m:02d}", m * 10, False) for m in range(1, 9)] + [_trend_row("2026-09", 999, True)]
    result = forecast._history_tail(trend, months=6)
    assert [row["month"] for row in result] == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert result[-1]["value"] == 80

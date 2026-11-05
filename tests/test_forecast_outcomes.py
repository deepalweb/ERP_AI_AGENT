"""Outlet order-forecast outcome tracking -- record_predictions(),
record_actuals(), get_track_record(). Pure unit tests against an isolated
appdb (appdb_reset); record_actuals() talks to the reporting DB too, so its
test monkeypatches reporting.run_query rather than hitting a live database."""

from datetime import date, timedelta

from backend.app.db import appdb
from backend.app.services import forecast_outcomes as fo

ARTIFACT = {
    "generated_at": "2026-11-04T03:48:46+00:00",
    "outlets": [
        {"store_id": 1, "forecast_dates": ["2026-11-01", "2026-11-02"], "point": [980.6, 1010.2],
         "q10": [888.8, 900.0], "q90": [1069.1, 1100.0]},
        {"store_id": 2, "forecast_dates": ["2026-11-01"], "point": [500.0], "q10": [450.0], "q90": [550.0]},
    ],
}


def test_record_predictions_writes_one_row_per_outlet_per_day(appdb_reset, monkeypatch):
    monkeypatch.setattr(fo, "_SCHEMA_READY", False)
    fo.record_predictions(ARTIFACT)
    rows = appdb.run_query("SELECT * FROM outlet_forecast_outcomes ORDER BY store_id, forecast_date")
    assert len(rows) == 3
    assert rows[0]["store_id"] == 1
    assert rows[0]["forecast_date"] == "2026-11-01"
    assert rows[0]["predicted_orders"] == 980.6
    assert rows[0]["actual_orders"] is None


def test_record_predictions_does_not_overwrite_an_already_recorded_date(appdb_reset, monkeypatch):
    monkeypatch.setattr(fo, "_SCHEMA_READY", False)
    fo.record_predictions(ARTIFACT)
    # A later run re-predicting the same future date with a different number
    # must not clobber the first prediction made for that date.
    later = {"generated_at": "2026-11-05T00:00:00+00:00",
              "outlets": [{"store_id": 1, "forecast_dates": ["2026-11-01"], "point": [1500.0], "q10": [1], "q90": [2]}]}
    fo.record_predictions(later)
    rows = appdb.run_query(
        "SELECT * FROM outlet_forecast_outcomes WHERE store_id = 1 AND forecast_date = '2026-11-01'"
    )
    assert len(rows) == 1
    assert rows[0]["predicted_orders"] == 980.6


def test_record_predictions_handles_no_artifact():
    fo.record_predictions(None)  # must not raise


def test_record_actuals_grades_past_dates_but_not_today(appdb_reset, monkeypatch):
    monkeypatch.setattr(fo, "_SCHEMA_READY", False)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    fo.record_predictions({
        "generated_at": "x",
        "outlets": [{"store_id": 1, "forecast_dates": [yesterday, today], "point": [100.0, 200.0],
                     "q10": [90.0, 190.0], "q90": [110.0, 210.0]}],
    })
    monkeypatch.setattr(fo.reporting, "run_query", lambda q, p: [{"n": 95}])
    fo.record_actuals()
    rows = {r["forecast_date"]: r for r in appdb.run_query("SELECT * FROM outlet_forecast_outcomes")}
    assert rows[yesterday]["actual_orders"] == 95
    assert rows[yesterday]["evaluated_at"] is not None
    assert rows[today]["actual_orders"] is None  # today isn't over yet


def test_get_track_record_returns_none_with_no_graded_predictions(appdb_reset, monkeypatch):
    monkeypatch.setattr(fo, "_SCHEMA_READY", False)
    assert fo.get_track_record() is None


def test_get_track_record_computes_wape_over_graded_predictions(appdb_reset, monkeypatch):
    monkeypatch.setattr(fo, "_SCHEMA_READY", False)
    d1, d2 = "2026-11-01", "2026-11-02"
    fo.record_predictions({
        "generated_at": "x",
        "outlets": [{"store_id": 1, "forecast_dates": [d1, d2], "point": [100.0, 200.0],
                     "q10": [90.0, 190.0], "q90": [110.0, 210.0]}],
    })
    monkeypatch.setattr(fo.reporting, "run_query", lambda q, p: [{"n": 110}] if p["start"] == d1 else [{"n": 180}])
    fo.record_actuals()

    result = fo.get_track_record(window_days=30)
    assert result["graded_predictions"] == 2
    assert result["earliest_date"] == d1
    assert result["latest_date"] == d2
    # |100-110| + |200-180| = 30 ; actual total = 290 -> 30/290 = 10.34%
    assert result["wape_pct"] == 10.34

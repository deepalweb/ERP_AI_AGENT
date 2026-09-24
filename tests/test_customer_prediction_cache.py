import json
from datetime import UTC, datetime

from backend.app.db import appdb
from backend.app.services import brain
from backend.app.services import customer_predictions as cp


def test_legacy_unscoped_snapshots_are_not_read(appdb_reset, monkeypatch):
    monkeypatch.setattr(cp, "_SCHEMA_READY", False)
    monkeypatch.setattr(cp, "_LIFECYCLE_SCHEMA_READY", False)
    appdb.run_query("CREATE TABLE customer_predictions (partner_id INTEGER)")
    appdb.run_query("INSERT INTO customer_predictions VALUES (123)")
    assert cp.get_summary()["as_of_date"] is None
    assert cp.get_daily_forecast() == []
    assert cp.get_lifecycle_summary() is None
    assert appdb.run_query("SELECT partner_id FROM customer_predictions")[0]["partner_id"] == 123


def test_legacy_home_card_hidden_until_scoped_scan(monkeypatch):
    row = {
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "investigations": "[]",
        "tracking": "[]",
        "customer_finding": json.dumps({"approaching_count": 500}),
    }
    monkeypatch.setattr(appdb, "run_query", lambda *args, **kwargs: [row])
    assert brain._fresh_cached_brief()["customer_finding"] is None
    row["customer_finding"] = json.dumps(
        {"approaching_count": 500, "analytics_contract": cp.ANALYTICS_CONTRACT}
    )
    assert brain._fresh_cached_brief()["customer_finding"]["approaching_count"] == 500

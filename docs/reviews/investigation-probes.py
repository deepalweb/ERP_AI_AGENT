"""Historical review probes: characterize pre-fix defects, using only temporary SQLite.

Run from the repository root with:
.venv/Scripts/python.exe -c "import runpy; runpy.run_path('docs/reviews/investigation-probes.py')"
No network or production DB access.
Assertions describe pre-fix defects and are expected to fail after the fixes.
Current regressions live in tests/test_investigation_integrity.py.
"""
import json
import os
import tempfile
from datetime import date
from unittest.mock import patch

from backend.app.db import appdb
from backend.app.services import action_outcomes, case_engine, finding_actions
from mcp_server.tools import intelligence


def finding(metric_id, category="critical"):
    return {
        "id": metric_id + "-2026-09-01", "metric_id": metric_id,
        "domain": "inventory", "category": category,
        "priority": "high", "confirm": {"current": 100},
    }


results = {}
with tempfile.TemporaryDirectory(prefix="celeste-review-") as directory:
    with patch.dict(os.environ, {"APP_DATABASE_URL": f"sqlite:///{directory}/review.db"}):
        appdb.get_engine.cache_clear()
        case_engine._SCHEMA_READY = False
        finding_actions._SCHEMA_READY = False
        try:
            with patch.object(case_engine, "_business_today", return_value=date(2026, 9, 1)):
                ad_hoc = finding("completed_stock_count_variance-all-2026-08-01_2026-09-01")
                cid = case_engine.save_ad_hoc_case(ad_hoc)
            for day in (2, 3):
                with patch.object(case_engine, "_business_today", return_value=date(2026, 9, day)):
                    case_engine.sync_cases([], [], [], data_fresh=True)
            case = case_engine.get_case(cid)
            assert (case["status"], case["resolution_verdict"]) == ("resolved", "recovered")
            results["ad_hoc_without_remeasurement"] = "resolved/recovered after two empty daily scans"

            finding_actions.set_action("target-edit", True, "completed", "promotion",
                target_metric_id="revenue", target_value=100, target_comparison=">=")
            appdb.run_query("UPDATE finding_actions SET completed_at='2026-07-15T00:00:00' WHERE finding_id='target-edit'")
            with patch.object(intelligence, "get_trend", return_value=[{"month":"2026-08", "value":150, "is_partial":False}]):
                action_outcomes.check_outcomes()
                finding_actions.set_action("target-edit", True, "completed", "promotion",
                    target_metric_id="revenue", target_value=200, target_comparison=">=")
                action_outcomes.check_outcomes()
            row = appdb.run_query("SELECT target_value,outcome_value,outcome_verdict FROM finding_actions WHERE finding_id='target-edit'")[0]
            assert row == {"target_value":200.0, "outcome_value":150.0, "outcome_verdict":"met"}
            results["edited_target_retains_verdict"] = row

            with patch.object(case_engine, "_business_today", return_value=date(2026, 9, 4)):
                other = finding("other-opportunity", "opportunity")
                other_id = case_engine.save_ad_hoc_case(other)
            finding_actions.set_action("unrelated-action", True, "completed", "separate promotion",
                target_metric_id="revenue", target_value=100, target_comparison=">=")
            appdb.run_query("UPDATE finding_actions SET completed_at='2026-07-15T00:00:00' WHERE finding_id='unrelated-action'")
            with patch.object(intelligence, "get_trend", return_value=[{"month":"2026-08", "value":150, "is_partial":False}]):
                action_outcomes.check_outcomes()
            case_engine.designate_case_objective_from_action(other_id, "unrelated-action")
            other_case = case_engine.get_case(other_id)
            assert other_case["objective_source_finding_id"] == "unrelated-action"
            assert case_engine.OpportunityPolicy().resolve(other_case) == "captured"
            results["foreign_action_objective"] = "unlinked action accepted; policy returns captured"

            trend = [{"month":str(i), "value":value, "is_partial":False} for i,value in enumerate([0,0,10])]
            anomaly = intelligence.detect_anomaly(trend, "higher_is_bad")
            assert anomaly["category"] == "normal"
            results["zero_baseline_spike"] = {k:anomaly[k] for k in ("category", "pct_change_vs_baseline", "z_score")}

            results["unavailable_freshness_gate"] = {"status":"unavailable", "passed_as_data_fresh": "unavailable" != "stale"}
        finally:
            appdb.get_engine().dispose()
            appdb.get_engine.cache_clear()
            case_engine._SCHEMA_READY = False
            finding_actions._SCHEMA_READY = False
print(json.dumps(results, indent=2))

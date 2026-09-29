"""Explicit successful-detector input for pre-existing lifecycle scenarios.

These tests describe clean scans, not missing/failed detectors. New coverage
regressions use the real sync entry point directly to test the unknown path.
"""
from backend.app.services import case_engine
from mcp_server.tools.measurement import observation


def sync_covered_cases(*args, **kwargs):
    cases = case_engine.list_cases(limit=200)["items"]
    findings = [*args, *(kwargs.get(key) for key in ("investigations", "state_findings", "short_term_findings"))]
    measurements = []
    if not any(findings):
        for case in cases:
            if case["status"] not in ("open", "verification_pending", "reopened"):
                continue
            period = None
            if case["horizon"] == "monthly":
                metric = case_engine.intelligence.METRICS.get(case["metric_id"])
                if metric is not None:
                    complete = [r for r in case_engine.intelligence.get_trend(metric) if not r.get("is_partial")]
                    period = complete[-1]["month"] if complete else None
            measurements.append(observation(case["metric_id"], case["horizon"], "measured_healthy", period=period))
    kwargs["measurements"] = measurements
    return case_engine.sync_cases(*args, **kwargs)

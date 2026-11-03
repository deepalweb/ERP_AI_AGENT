from datetime import date

from mcp_server.tools import operation_flow as flow


def _daily(stage="Picking", *, day=1, sample=15, p90=12, outlet="Outlet A", store_id=1,
           baseline_sample=250, baseline_p95=4):
    return {
        "store_id": store_id, "outlet": outlet, "stage": stage,
        "order_day": date(2026, 10, day), "daily_sample": sample,
        "daily_p90_minutes": p90, "baseline_sample": baseline_sample,
        "baseline_p95_minutes": baseline_p95, "as_of": date(2026, 10, 7),
    }


def test_bottleneck_requires_repeated_daily_threshold_and_sample():
    rows = [_daily(day=day, sample=20) for day in (5, 6, 7)]

    found = flow._repeated_bottlenecks(rows, {"Picking"})

    assert len(found) == 1
    assert found[0]["slow_days"] == 3
    assert found[0]["threshold_minutes"] == 10


def test_bottleneck_ignores_one_slow_day_and_tiny_daily_volume():
    rows = [_daily(day=5, sample=20), _daily(day=6, sample=20, p90=9), _daily(day=7, sample=9)]

    assert flow._repeated_bottlenecks(rows, {"Picking"}) == []


def test_stage_and_dispatch_findings_keep_unknown_source_freshness(monkeypatch):
    rows = [_daily(stage="Waiting for dispatch", day=day, sample=20) for day in (5, 6, 7)]
    monkeypatch.setattr(flow, "_flow_rows", lambda: rows)

    finding = flow._dispatch_wait_finding()

    assert finding["metric_id"] == "operation_flow_dispatch_wait"
    assert finding["operation_flow_rows"][0]["stage"] == "Waiting for dispatch"
    assert finding["root_cause"]["checks"][-1]["verdict"] == "unknown"


def test_operation_flow_unknown_freshness_has_explicit_source_reason():
    record = {"metric_id": "operation_flow_stage_bottleneck", "freshness": "fresh"}

    from mcp_server.tools import measurement
    token = measurement._coverage.set(record)
    try:
        measurement.source_as_of("2026-10-07", verifies_source=False,
                                 unknown_reason="Stage event times do not prove mirror refresh time.")
    finally:
        measurement._coverage.reset(token)

    assert record["freshness"] == "unknown"
    assert record["reason"] == "Stage event times do not prove mirror refresh time."


def test_snapshot_can_report_measured_result_without_claiming_freshness(monkeypatch):
    from mcp_server.tools import measurement

    def detector():
        measurement.source_as_of(
            "2026-10-07", verifies_source=False,
            unknown_reason="Reporting mirror refresh time is unknown.",
        )
        return None

    batch = measurement.run_snapshot(
        "operation_flow_stage_bottleneck", detector,
        unknown_freshness_is_measured=True,
    )

    assert batch.measurements[0]["measurement_status"] == "measured_healthy"
    assert batch.measurements[0]["freshness"] == "unknown"

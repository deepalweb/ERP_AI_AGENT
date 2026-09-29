"""Detector coverage travels with findings; absence alone is never a measurement.

Scope is the detector's actual scope key (scoped detectors encode it in metric_id).
Freshness describes the measured source, not the time this envelope was created.
"""
import logging
from contextvars import ContextVar
from datetime import UTC, date, datetime

_coverage = ContextVar("detector_coverage", default=None)
logger = logging.getLogger(__name__)


class FindingBatch(list):
    def __init__(self, findings=(), measurements=()):
        super().__init__(findings)
        self.measurements = list(measurements)


def observation(metric_id, horizon, status, *, period=None, reason=None,
                freshness="fresh", coverage="complete", scope="company", value=None):
    return {
        "detector_id": metric_id, "metric_id": metric_id, "scope": scope,
        "horizon": horizon, "period": period, "measurement_status": status,
        "coverage": coverage, "freshness": freshness, "reason": reason,
        "observed_at": datetime.now(UTC).isoformat(), "value": value,
    }


def healthy(measurements, metric_id, horizon):
    return next((m for m in measurements or []
                 if m["metric_id"] == metric_id and m["horizon"] == horizon
                 and m["measurement_status"] == "measured_healthy"
                 and m["coverage"] == "complete" and m["freshness"] == "fresh"), None)


def source_unavailable(reason):
    current = _coverage.get()
    if current is not None:
        current.update(measurement_status="measurement_unavailable", coverage="incomplete",
                       freshness="unknown", reason=reason)


def source_as_of(value, *, verifies_source=True):
    if not value:
        source_unavailable("Source has no observation timestamp.")
        return
    observed = date.fromisoformat(str(value)[:10])
    current = _coverage.get()
    if current is not None:
        current["period"] = observed.isoformat()
        if not verifies_source:
            current.update(freshness="unknown", reason="Sales recency does not establish inventory refresh coverage.")
            return
        current["freshness"] = "fresh"
        current["reason"] = None
        if (datetime.now(UTC).date() - observed).days > 1:
            current.update(freshness="stale", reason="Source observation is stale.")


def run_snapshot(metric_id, detector):
    record = observation(metric_id, "snapshot", "measured_healthy", period=datetime.now(UTC).date().isoformat(),
                         freshness="unknown", reason="The source supplies no freshness watermark.")
    token = _coverage.set(record)
    try:
        finding = detector()
        if finding:
            record.update(measurement_status="measured_actionable", value=finding["confirm"]["current"])
            finding["measurement"] = record.copy()
        elif record["freshness"] != "fresh":
            record["measurement_status"] = "measurement_unavailable"
        return FindingBatch([finding] if finding else [], [record])
    except Exception:
        logger.exception("Snapshot detector failed: %s", metric_id)
        record.update(measurement_status="measurement_unavailable", coverage="incomplete",
                      freshness="unknown", reason="The detector could not read its source; retry the scan.")
        return FindingBatch([], [record])
    finally:
        _coverage.reset(token)


def combine(batches):
    return FindingBatch([f for batch in batches for f in batch],
                        [m for batch in batches for m in getattr(batch, "measurements", [])])

"""Cached Home reads and a single background refresh per process.

The existing scan lock serializes writes across processes. Failed refreshes
retain the prior evidence and require explicit retry; no empty fallback.
"""

import logging
from datetime import UTC, datetime
from threading import Lock, Thread

from backend.app.services import brain

logger = logging.getLogger(__name__)
_guard = Lock()
_thread = None
_refresh = {"status": "idle", "error": None}


def refresh_state():
    with _guard:
        return dict(_refresh)


def request_refresh(*, retry=False):
    global _thread
    with _guard:
        if _thread and _thread.is_alive():
            return dict(_refresh)
        if _refresh["status"] == "failed" and not retry:
            return dict(_refresh)
        _refresh.update(status="running", error=None, started_at=datetime.now(UTC).isoformat())

        def run():
            try:
                brain.run_daily_scan(force=retry)
            except Exception:
                logger.exception("Home briefing refresh failed")
                with _guard:
                    _refresh.update(
                        status="failed", error="The briefing refresh failed. Retry to check again."
                    )
            else:
                with _guard:
                    _refresh.update(status="idle", error=None)

        _thread = Thread(target=run, name="home-brief-refresh", daemon=True)
        _thread.start()
        return dict(_refresh)


def get_brief():
    brain._ensure_schema()
    brief = brain._fresh_cached_brief(allow_stale=True)
    state = refresh_state()
    if brief is None or brief["cache_status"] == "stale":
        state = request_refresh()
    if brief is None:
        return {
            "status": "error" if state["status"] == "failed" else "loading",
            "detail": state.get("error") or "Preparing the first briefing from reporting data.",
            "refresh": state,
        }
    return {"status": "ok", **brief, "refresh": state}


def presentation(brief):
    """Order existing backend scores; never synthesize business values."""
    result = {}
    for horizon, findings in (
        ("long", [*brief["investigations"], *brief["state_findings"]]),
        ("week", brief["short_term_findings"]),
    ):
        ranked = sorted(findings, key=lambda f: f.get("business_impact_score") or 0, reverse=True)
        counts = {name: 0 for name in ("critical", "high", "medium", "positive", "unclassified")}
        for finding in findings:
            level = (
                "positive"
                if finding.get("category") == "opportunity"
                else finding.get("priority", "unclassified")
            )
            counts[level if level in counts else "unclassified"] += 1
        result[horizon] = {
            "ranked_ids": [f["id"] for f in ranked],
            "priority_ids": [f["id"] for f in ranked[:3]],
            "severity_counts": counts,
        }
    return result

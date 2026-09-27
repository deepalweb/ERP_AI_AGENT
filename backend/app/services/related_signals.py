"""Cross-domain investigation, v1: "does this revenue case have a related
signal at the same outlet, same month?" -- Investigate V3's next step per
the user-approved design (2026-09-27).

Deliberately narrow, matching the approved scope exactly:
- v1 only supports revenue -> cancellation_rate, same outlet + same month.
  Not a generic graph engine -- RELATED_METRIC_CANDIDATES below is the
  entire configuration surface for widening this later (e.g. adding
  stock_availability once revenue<->cancellation has been inspected
  against real cases).
- Computed fresh on every Case Detail fetch, never persisted. A
  `case_relationships` table already exists (case_engine.py) and is
  generic enough to hold this later, but the user explicitly asked not to
  persist until the relationship's usefulness is proven with real
  managers -- so this module never writes to it.
- Never claims "confirmed" for a cross-domain relationship -- that verdict
  is reserved for same-metric internal evidence (segment_concentration_
  checks). Two tiers only, matching the approved design:
    "corroborated"  -- both metrics independently confirm the same
                        segment (their own segment_concentration_checks
                        both fire "confirmed").
    "point_in_time" -- the related metric's own concentration check can't
                        run at all (e.g. a rate-mode breakdown -- see
                        evidence_engine.segment_concentration_checks'
                        own docstring for why cancellation_rate's store
                        breakdown always comes back "unknown" there), but
                        its breakdown's own top (largest single-segment)
                        contributor still names the same segment. A real,
                        weaker signal -- not hidden, but never worded as
                        causal or as strong as "corroborated".
- Skips the LLM entirely: calls intelligence.investigate() +
  evidence_engine.build_trend_evidence() directly rather than
  investigation.build_investigation() (which would also run
  impact_engine and _generate_recommendations() -- unneeded work, and an
  LLM call neither wanted nor affordable on every Case Detail page load).
"""

from backend.app.db import appdb
from mcp_server.tools import evidence_engine, intelligence

RELATED_METRIC_CANDIDATES = {
    "revenue": ["cancellation_rate"],
}


def _active_case_id_for(metric_id: str) -> str | None:
    rows = appdb.run_query(
        "SELECT case_id FROM business_cases WHERE metric_id=:m AND status IN ('open','verification_pending','reopened') "
        "ORDER BY last_seen_at DESC LIMIT 1",
        {"m": metric_id},
    )
    return rows[0]["case_id"] if rows else None


def _related_metric_evidence(metric_id: str) -> dict | None:
    """Cheap (no LLM, no impact/recommendation work) read of a metric's
    current period, breakdowns, and evidence checks -- everything this
    module needs to compare against a primary case, nothing else."""
    result = intelligence.investigate(metric_id)
    if "error" in result:
        return None
    anomaly = result["anomaly"]
    if not anomaly.get("latest_month"):
        return None
    checks = evidence_engine.build_trend_evidence(
        metric_id, anomaly, result["breakdowns"], result["trend"], result["persistence"],
        anomaly.get("category"), include_memory=False,
    )["checks"]
    return {
        "metric_id": metric_id,
        "metric": result["metric"],
        "period": anomaly["latest_month"],
        "breakdowns": result["breakdowns"],
        "checks": checks,
    }


def related_signals_for(metric_id: str, period: str | None, checks: list[dict]) -> list[dict]:
    """`checks` is the PRIMARY case's own root_cause.checks (already
    computed, already on the case) -- this never recomputes the primary,
    only looks up candidate related metrics to compare it against."""
    candidates = RELATED_METRIC_CANDIDATES.get(metric_id)
    if not candidates or not period:
        return []

    primary_store_check = next(
        (c for c in checks if c.get("source") == "store_concentration" and c.get("verdict") == "confirmed"), None
    )
    if not primary_store_check:
        return []  # no confirmed anchor segment on the primary -- nothing to compare against
    primary_segments = set(primary_store_check.get("segments") or [])
    if not primary_segments:
        return []

    related = []
    for related_metric_id in candidates:
        ev = _related_metric_evidence(related_metric_id)
        if ev is None or ev["period"] != period:
            continue  # different (or no) month -- not the same period, skip silently, no weak fallback

        store_check = next((c for c in ev["checks"] if c.get("source") == "store_concentration"), None)
        if store_check and store_check.get("verdict") == "confirmed":
            overlap = primary_segments & set(store_check.get("segments") or [])
            if overlap:
                segment = sorted(overlap)[0]
                related.append({
                    "metric_id": related_metric_id,
                    "label": ev["metric"]["label"],
                    "tier": "corroborated",
                    "scope": segment,
                    "period": ev["period"],
                    "case_id": _active_case_id_for(related_metric_id),
                    "detail": f"{ev['metric']['label']} independently concentrates at {segment} in {ev['period']} "
                    f"-- the same outlet and month as this revenue change.",
                })
            # The related metric's own concentration check ran and gave a
            # real answer (confirmed elsewhere, or explicitly no evidence
            # found) -- that's a real, checked result, not a gap, so it's
            # wrong to fall through to the weaker top-contributor guess
            # below. Only a genuinely unable-to-check verdict ("unknown",
            # e.g. a rate-mode breakdown) earns the weaker fallback.
            continue
        if store_check and store_check.get("verdict") == "no_evidence_found":
            continue

        store_breakdown = next((b for b in ev["breakdowns"] if b.get("dimension") == "store"), None)
        top = (store_breakdown or {}).get("top_contributors") or []
        if top and top[0].get("name") in primary_segments:
            segment = top[0]["name"]
            related.append({
                "metric_id": related_metric_id,
                "label": ev["metric"]["label"],
                "tier": "point_in_time",
                "scope": segment,
                "period": ev["period"],
                "case_id": _active_case_id_for(related_metric_id),
                "detail": f"{ev['metric']['label']}'s largest single-outlet movement this month was also at "
                f"{segment} -- the same outlet as this revenue change, though this isn't a confirmed "
                f"concentration ({(store_breakdown or {}).get('label', 'this breakdown').lower()} is measured "
                f"as a rate, not yet supported by the concentration check).",
            })

    return related

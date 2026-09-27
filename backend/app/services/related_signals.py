"""Cross-domain investigation, v1: "does this revenue case have a related
signal at the same outlet, same month?" -- Investigate V3's next step per
the user-approved design (2026-09-27), wording revised same day per
direct feedback on the live card ("point-in-time" read as internal
jargon; a manager needs a plain sentence plus an explicit "cause not
established" qualifier on every card, not a tier name).

Deliberately narrow, matching the approved scope exactly:
- v1 candidates: revenue -> cancellation_rate (same granularity, monthly
  trend vs. monthly trend) and revenue -> stock_availability (a coarser
  join: monthly trend vs. a single-day snapshot). RELATED_METRIC_
  CANDIDATES is the entire configuration surface for widening this later
  -- not a generic graph engine.
- Computed fresh on every Case Detail fetch, never persisted. A
  `case_relationships` table already exists (case_engine.py) and is
  generic enough to hold this later, but the user explicitly asked not to
  persist until the relationship's usefulness is proven with real
  managers -- so this module never writes to it.
- Never claims "confirmed" for a cross-domain relationship -- that verdict
  is reserved for same-metric internal evidence (segment_concentration_
  checks). Every related signal carries an explicit "cause not
  established" qualifier; internally there are three distinct reasons a
  signal ends up in the weaker tier, each named honestly in its own
  `detail` text rather than collapsed into one generic "weaker" label:
    "corroborated"  -- both metrics independently confirm the same
                        segment, same granularity (their own
                        segment_concentration_checks both fire
                        "confirmed"). Still labeled "cause not
                        established", never "confirmed" cause.
    "point_in_time" -- the related metric's own concentration check
                        can't run at all (e.g. cancellation_rate's store
                        breakdown is rate-mode -- see evidence_engine.
                        segment_concentration_checks' own docstring for
                        why), but its breakdown's own top contributor
                        still names the same segment.
    "snapshot"      -- the related signal is a single-day snapshot (e.g.
                        stock_availability), not a trend over the same
                        month -- a coarser granularity mismatch than
                        point_in_time's measurement-mode limitation, so
                        this is kept separate even when the snapshot's
                        own concentration check DOES confirm the same
                        outlet; the snapshot date is always shown
                        alongside the primary's month so the timing gap
                        is visible, never hidden.
- Skips the LLM entirely: calls intelligence.investigate() +
  evidence_engine.build_trend_evidence() directly rather than
  investigation.build_investigation() (which would also run
  impact_engine and _generate_recommendations() -- unneeded work, and an
  LLM call neither wanted nor affordable on every Case Detail page load).
"""

from backend.app.db import appdb
from backend.app.services.stock_alerts import get_stock_alerts
from mcp_server.tools import evidence_engine, intelligence

RELATED_METRIC_CANDIDATES = {
    "revenue": ["cancellation_rate", "stock_availability"],
}

# Presentation only -- never exposes the internal tier name to a manager.
# Every qualifier ends "cause not established" per direct feedback on the
# live card: this must be obvious on each signal, not just in the section
# subtitle above the list.
QUALIFIER_FOR_TIER = {
    "corroborated": "Independently corroborated · cause not established",
    "point_in_time": "Related signal observed · cause not established",
    "snapshot": "Point-in-time snapshot · cause not established",
}


def _active_case_id_for(metric_id: str) -> str | None:
    rows = appdb.run_query(
        "SELECT case_id FROM business_cases WHERE metric_id=:m AND status IN ('open','verification_pending','reopened') "
        "ORDER BY last_seen_at DESC LIMIT 1",
        {"m": metric_id},
    )
    return rows[0]["case_id"] if rows else None


def _movement_word(moved_up: bool | None) -> str:
    if moved_up is None:
        return "changed"
    return "increased" if moved_up else "decreased"


def _make_signal(*, metric_id, label, tier, segment, headline, detail, case_id) -> dict:
    return {
        "metric_id": metric_id,
        "label": label,
        "tier": tier,
        "qualifier": QUALIFIER_FOR_TIER[tier],
        "scope": segment,
        "headline": headline,
        "detail": detail,
        "case_id": case_id,
    }


def _related_metric_evidence(metric_id: str) -> dict | None:
    """Cheap (no LLM, no impact/recommendation work) read of a trend
    metric's current period, breakdowns, and evidence checks -- everything
    this module needs to compare against a primary case, nothing else."""
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
        "moved_up": anomaly.get("moved_up"),
        "breakdowns": result["breakdowns"],
        "checks": checks,
    }


def _cancellation_signal(period: str, primary_segments: set[str]) -> dict | None:
    ev = _related_metric_evidence("cancellation_rate")
    if ev is None or ev["period"] != period:
        return None  # different (or no) month -- not the same period, no weak fallback either

    word = _movement_word(ev["moved_up"])
    store_check = next((c for c in ev["checks"] if c.get("source") == "store_concentration"), None)

    if store_check and store_check.get("verdict") == "confirmed":
        overlap = primary_segments & set(store_check.get("segments") or [])
        if not overlap:
            return None
        segment = sorted(overlap)[0]
        return _make_signal(
            metric_id="cancellation_rate", label=ev["metric"]["label"], tier="corroborated", segment=segment,
            headline=f"{ev['metric']['label']} {word} at {segment} in {ev['period']} — the same outlet and month as this revenue change.",
            detail=f"Both this revenue change and {ev['metric']['label'].lower()} independently concentrate at "
            f"{segment} in {ev['period']}, checked separately by each metric's own concentration test.",
            case_id=_active_case_id_for("cancellation_rate"),
        )
    if store_check and store_check.get("verdict") == "no_evidence_found":
        return None  # a real, checked answer that isn't a match -- not a gap, don't guess

    # verdict is "unknown" (the check structurally can't run, e.g. a
    # rate-mode breakdown) -- fall back to the breakdown's own top
    # (largest single-segment) contributor.
    store_breakdown = next((b for b in ev["breakdowns"] if b.get("dimension") == "store"), None)
    top = (store_breakdown or {}).get("top_contributors") or []
    if not top or top[0].get("name") not in primary_segments:
        return None
    segment = top[0]["name"]
    return _make_signal(
        metric_id="cancellation_rate", label=ev["metric"]["label"], tier="point_in_time", segment=segment,
        headline=f"{ev['metric']['label']} {word} at {segment} in {ev['period']} — the same outlet and month as this revenue change.",
        detail=f"{ev['metric']['label']}'s largest single-outlet movement this month was also at {segment}. "
        f"This is a weaker signal than a confirmed concentration: cancellation rate is measured per outlet as a "
        f"rate, which this app's concentration check doesn't run the same math against as a raw total.",
        case_id=_active_case_id_for("cancellation_rate"),
    )


def _stock_availability_signal(revenue_period: str, primary_segments: set[str]) -> dict | None:
    alerts = get_stock_alerts()
    as_of_date = alerts.get("as_of_date")
    if not as_of_date:
        return None
    segments = alerts.get("segments_by_outlet") or []
    by_outlet = [
        {"name": s["outlet"], "current": s["out_of_stock"], "delta": s["out_of_stock"]}
        for s in segments if s.get("out_of_stock", 0) > 0
    ]
    if not by_outlet:
        return None
    breakdowns = [{"dimension": "store", "label": "Store", "mode": "contribution", "baseline_month": None,
                   "total": sum(o["current"] for o in by_outlet), "top_contributors": by_outlet}]
    checks = evidence_engine.build_snapshot_evidence(breakdowns, as_of_date)["checks"]
    store_check = next((c for c in checks if c.get("source") == "store_concentration"), None)

    # Present iff the primary's own outlet has ANY out-of-stock products
    # today, whether or not it's the outlet with the most -- the point is
    # "was this outlet affected at all during the revenue month," not "is
    # it the single worst outlet company-wide". Always tier "snapshot":
    # even when store_check itself says "confirmed", a single day's
    # reading can't establish presence across the whole month the revenue
    # case describes, so this never gets the "corroborated" tier
    # cancellation_rate can reach.
    match = next((o for o in by_outlet if o["name"] in primary_segments), None)
    if not match:
        return None
    segment = match["name"]
    concentrated_note = (
        f" ({segment} was also the most-affected outlet company-wide.)"
        if store_check and store_check.get("verdict") == "confirmed" and segment in (store_check.get("segments") or [])
        else ""
    )
    return _make_signal(
        metric_id="stock_availability", label="Stock availability", tier="snapshot", segment=segment,
        headline=f"Stock availability issues were observed at {segment} on {as_of_date} — during the "
        f"{revenue_period} revenue-decline month.",
        detail=f"{match['current']} product(s) were out of stock at {segment} as of {as_of_date}, a single-day "
        f"snapshot, not a measurement across the full {revenue_period} month this revenue change describes."
        f"{concentrated_note} This does not establish that stock availability caused the revenue change.",
        case_id=None,  # snapshot findings aren't tracked as their own persistent case the way trend metrics are
    )


def related_signals_for(metric_id: str, period: str | None, checks: list[dict]) -> list[dict]:
    """`checks` is the PRIMARY case's own root_cause.checks (already
    computed, already on the case) -- this never recomputes the primary,
    only looks up candidate related signals to compare it against."""
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
    if "cancellation_rate" in candidates:
        signal = _cancellation_signal(period, primary_segments)
        if signal:
            related.append(signal)
    if "stock_availability" in candidates:
        signal = _stock_availability_signal(period, primary_segments)
        if signal:
            related.append(signal)
    return related

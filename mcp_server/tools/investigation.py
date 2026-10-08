"""Turns a metric's raw findings (intelligence.py) into the narrated Investigation
object the frontend renders as a card: confirm -> where/why -> impact -> recommend.

scan_all() is the "24/7 analyst" entry point — it runs every registered metric
through the same pipeline and returns only the ones worth a human's attention
(category != 'normal'). Nothing here is specific to any one metric; add a new
metric to intelligence.METRICS and it gets investigated the same way.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from functools import partial

from openai import OpenAI

from mcp_server.tools import evidence_engine, impact_engine, intelligence, measurement, model_config
from mcp_server.tools import recommendations as recommendation_schema

CATEGORY_ORDER = {"critical": 0, "opportunity": 1}
logger = logging.getLogger(__name__)
SEVERITY_BY_CATEGORY = {"critical": "high", "opportunity": "positive"}

# How each metric is presented in the Explore feed's domain grouping. Lives
# here (not in intelligence.py) since domain is about presentation, not
# detection — mirrors the existing split where intelligence.py stays
# metric-agnostic and investigation.py does all the narration.
METRIC_DOMAIN = {
    "cancellation_rate": "order_customer",
    "stockout_cancellation_rate": "inventory",
    "return_rate": "order_customer",
    "order_volume": "order_customer",
    "average_order_value": "order_customer",
    "customer_retention_rate": "order_customer",
    "revenue": "financial",
    "gross_margin": "financial",
    "delayed_delivery_rate": "delivery",
    "dispatch_wait_minutes": "delivery",
    "uber_failure_rate": "delivery",
    "pickme_cancellation_rate": "delivery",
    "supplier_fill_rate": "supplier",
    "purchase_price_variance": "supplier",
    "supplier_concentration": "supplier",
}

# Metrics with a verified fulfillment-stage mechanism behind their store
# breakdown (see backend/app/services/operations.py's
# get_stage_timing_by_store) — a HOW stage is only ever attached for these,
# never guessed for a metric with no confirmed mechanism.
HOW_CAPABLE_METRICS = {"delayed_delivery_rate"}

# Metrics with a short_term_trend_sql defined in intelligence.py — the two
# operationally-urgent ones (cancellation spikes, delivery delays) where a
# monthly comparison catches a problem too late to act on. Deliberately not
# every metric: proving the trailing-window pattern here first before
# extending it (e.g. to supplier_fill_rate) is a separate step.
SHORT_TERM_METRICS = ["cancellation_rate", "delayed_delivery_rate"]


def _priority_for(anomaly: dict) -> str:
    """Deterministic priority tier from the anomaly's own magnitude — not an
    LLM judgment call, so it's always traceable back to a stated rule (same
    posture as health.py's _status_for). Three real bands for a critical
    finding, all off the same pct_change_vs_baseline/z_score magnitude
    already computed by detect_anomaly() — nothing below ANOMALY_PCT_THRESHOLD
    (15%) ever reaches here at all, so 'medium' is genuinely the floor, not a
    padded-in fourth tier. An opportunity is never urgent in the same sense,
    so it gets its own label rather than borrowing the bad-news scale."""
    category = anomaly.get("category")
    if category == "opportunity":
        return "worth_pursuing"
    pct = anomaly.get("pct_change_vs_baseline")
    z = anomaly.get("z_score")
    if (pct is not None and abs(pct) >= 40) or (pct is None and z is not None and abs(z) >= 3):
        return "critical"
    if (pct is not None and abs(pct) >= 25) or (pct is None and z is not None and abs(z) >= 2):
        return "high"
    return "medium"


def _estimate_how(metric_id: str, anomaly: dict, breakdowns: list, horizon: str = "monthly"):
    # get_stage_timing_by_store expects a "YYYY-MM" key (backend/app/services/
    # operations.py's _month_bounds splits on "-" expecting exactly year-month)
    # — it has no trailing-window equivalent yet, so this is monthly-only for
    # now rather than crashing on a trailing period's date-range label.
    if metric_id not in HOW_CAPABLE_METRICS or horizon != "monthly":
        return None

    store_breakdown = next((b for b in breakdowns if b["dimension"] == "store"), None)
    if not store_breakdown:
        return None

    flagged_stores = [c["name"] for c in store_breakdown["top_contributors"] if c["delta"] > 0][:3]
    if not flagged_stores:
        return None

    # Imported lazily to avoid a module-load-time dependency from
    # mcp_server.tools onto backend.app.services for metrics that never need it.
    from backend.app.services.operations import get_stage_timing_by_store

    return get_stage_timing_by_store(flagged_stores, anomaly["latest_month"])


def _get_client() -> OpenAI:
    api_key = os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("NVIDIA_BASE_URL")
    # Recommendations/synthesis are optional enrichments. Do not keep the
    # whole scan (and its case-write lock) waiting through long SDK defaults
    # and retries when a provider stalls. Existing callers retain their
    # honest [] / None fallback; deterministic evidence is still published.
    return OpenAI(api_key=api_key, base_url=base_url, timeout=45.0, max_retries=0)


def _get_model() -> str:
    return model_config.model_name()


def _headline(metric: dict, anomaly: dict) -> str:
    direction_word = "increased" if anomaly["moved_up"] else "decreased"
    pct = anomaly.get("pct_change_vs_baseline")
    pct_str = f" {abs(pct):.0f}%" if pct is not None else ""
    return f"{metric['label']}{pct_str} {direction_word} vs its usual range"


def _generate_recommendations(metric: dict, anomaly: dict, breakdowns: list, impact, evidence_pkg=None):
    """One grounded, non-agentic LLM call: reason over the evidence already gathered
    and propose actions. The model is explicitly told not to introduce facts beyond
    the evidence payload, since these recommendations are shown as trustworthy,
    evidence-backed guidance, not free chat.

    evidence_pkg (when present) is the OUTPUT of evidence_engine.build_trend_evidence/
    build_snapshot_evidence — a deterministic evidence package, never something
    the LLM is asked to determine itself. The prompt below is explicit about
    which verdicts can be stated as fact vs. hedged vs. not mentioned at all,
    so a 'possible_contributor' can't get upgraded into a stated cause just
    because the model is writing fluent prose."""
    evidence = {
        "metric": metric,
        "anomaly": anomaly,
        "breakdowns": breakdowns,
        "impact": impact,
        "root_cause": evidence_pkg["checks"] if evidence_pkg else None,
        "historical_context": evidence_pkg.get("historical_context") if evidence_pkg else None,
        "data_quality": evidence_pkg.get("data_quality") if evidence_pkg else None,
        "memory": evidence_pkg.get("memory") if evidence_pkg else None,
    }
    prompt = (
        "You are a business analyst writing recommendations from evidence only. "
        "Given this investigation evidence as JSON, return a JSON array of 2-4 "
        "recommended actions, most important first. Each item must be exactly: "
        '{"action": string, "owner": string, "priority": "high"|"medium"|"low", '
        '"expected_benefit": string}. '
        "'owner' is a plausible responsible team or role (e.g. 'Logistics team', "
        "'Inventory team', 'Operations manager') inferred from which dimension "
        "(store/product/reason/customer) the evidence points at. Name the specific "
        "segments that appear in the breakdowns rather than speaking generically. "
        "'expected_benefit' is a short, grounded statement of the likely outcome if "
        "the action is taken — if `impact` is present in the evidence, base it on "
        "that figure (e.g. 'could recover roughly N orders / Rs. X per month'); if "
        "`impact` is null, give a qualitative outcome tied only to the evidence "
        "already provided (e.g. 'should reduce cancellations concentrated at "
        "<store>'). Never invent a number that isn't derivable from the evidence. "
        "If `metric.notes` describes a methodology caveat (e.g. an estimate under "
        "current costs rather than a historical realized figure, or a coverage "
        "limitation), you must respect it in both 'action' and 'expected_benefit': "
        "never describe the metric's own movement, or the benefit of acting on it, "
        "as a confirmed realized profit/loss or as more certain than that caveat "
        "allows. A stock-quantity discrepancy is evidence requiring investigation, "
        "never a confirmed inventory-loss explanation, unless a check in `root_cause` "
        "says otherwise. "
        "IMPORTANT: each entry in `breakdowns` is an INDEPENDENT aggregate over the "
        "whole business — the top store and the top reason were computed separately "
        "and do not mean that reason is what happened at that store. Never combine "
        "a segment from one breakdown with a segment from another (e.g. never say "
        "'address <reason> at <store>') unless the evidence explicitly ties them "
        "together. Write a recommendation about a breakdown's top segment using "
        "only that breakdown's own dimension. "
        "If `root_cause` is present, it lists specific checks Celeste already ran "
        "against real data, each with a verdict: 'confirmed' (state this as "
        "established fact — you may say it caused or drove the move), "
        "'possible_contributor' (mention only with hedged language like 'may also "
        "be a factor' — never state it as the cause), 'related_not_independent' "
        "(a different metric moved the same way, but only because it shares the same "
        "underlying order data — e.g. revenue and order volume from the same orders; "
        "mention only as background if at all, e.g. 'order volume moved the same way, "
        "which follows directly from the revenue change' — never present it as a "
        "second, independent piece of supporting evidence), 'no_evidence_found' or "
        "'unknown' (do not mention as a cause at all; these mean it was checked and "
        "NOT supported, or couldn't be checked — this includes a case where a "
        "different metric confirmed the same segment but moved the OPPOSITE "
        "direction, source 'conflicting_signal': do not use that metric to support "
        "this finding's explanation). Do not propose any cause that isn't "
        "listed in `root_cause`, and do not claim causation where the evidence only "
        "shows correlation. Multiple correlated metrics agreeing is never sufficient "
        "on its own to call a cause 'confirmed' — only a check whose own verdict is "
        "already 'confirmed' may be stated as fact. "
        "If `historical_context` is present, it describes whether this finding is a "
        "one-off ('single_period_move'), part of a longer run in the same direction "
        "('consistent_declining'/'consistent_improving', with `consecutive_periods` "
        "showing how many), or a rebound right after one ('recovering'). If "
        "`consecutive_periods` is 2 or more, reflect the persistence explicitly (e.g. "
        "'third consecutive month of decline') rather than writing as if this were the "
        "first time it's happened — a repeated problem calls for more urgency than a "
        "one-off. If the pattern is 'recovering', say so as a rebound, not a new problem. "
        "If `data_quality` has verdict 'low_confidence', say so plainly (e.g. 'based on "
        "limited history so far') rather than presenting the finding with full confidence. "
        "If `memory` is present, it describes the most recent PRIOR time this exact "
        "metric was flagged: what was recommended then (`prior_recommendations`), "
        "what a team recorded actually doing about it (`action_taken_by_team`, only "
        "present if someone logged it), and what happened as a result "
        "(`outcome_verdict`: 'improved' means the deviation shrank, 'worsened' means "
        "it grew, 'unchanged' means neither, 'pending' means it's too soon to know). "
        "If `outcome_verdict` is 'worsened' or 'unchanged', do not simply repeat a "
        "`prior_recommendations` action that already failed to work — either propose "
        "something genuinely different, or if the evidence still points at the same "
        "fix, explicitly say why it's worth trying again (e.g. new evidence, or it "
        "was never actually acted on per `action_taken_by_team`). If `outcome_verdict` "
        "is 'improved', you may suggest reinforcing what already worked rather than "
        "replacing it. Never claim credit for an outcome — describe it neutrally as "
        "something that was observed. If `memory` is null, this is the first time "
        "this metric has been flagged (or there's no prior period yet) — do not "
        "reference history that wasn't provided. "
        "Do not invent facts not present in "
        "the evidence. Return ONLY the JSON array, no prose, no markdown fences.\n\n"
        f"Evidence:\n{json.dumps(evidence, default=str)}"
    )
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_get_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return recommendation_schema.parse(response.choices[0].message.content or "[]",
            allowed_refs={c["source"] for c in (evidence_pkg or {}).get("checks", [])})
    except Exception:
        return recommendation_schema.Recommendations()


def generate_daily_synthesis(investigations: list[dict]) -> dict | None:
    """The one step in this pipeline that reasons ACROSS findings instead of
    within one: every other LLM call here (_generate_recommendations) only
    ever sees a single metric's own evidence. This looks at the whole scan
    and asks whether evidence_engine.cross_metric_corroboration_checks found
    two or more findings that independently confirm the same segment -- the
    "delivery-partner failure rate AND revenue both concentrated at the same
    3 outlets" shape of result -- and if so, writes the one paragraph that
    connects them, instead of leaving a reader to notice the pattern across
    10 separately-worded findings themselves.

    Deliberately does NOT run when nothing is corroborated: forcing a
    "synthesis" out of unrelated findings would be exactly the kind of
    invented causal story this whole evidence layer exists to prevent. No
    connected cluster this scan -> no narrative, not a fabricated one. Needs
    at least 2 corroborated findings, not 1: a single finding can't be
    "connected" to anything by itself.

    Which findings count as connected is decided here, deterministically,
    from each investigation's own root_cause.checks (already computed by
    cross_metric_corroboration_checks) -- never left to the model, so it
    can't hallucinate a metric_id that wasn't actually corroborated. The
    model's only job is turning the already-verified overlap into a
    sentence. Gates specifically on verdict == "possible_contributor" (the
    only tier that function ever emits for this source), never on the
    source name alone -- a "related_not_independent" match (e.g. revenue
    and order_volume agreeing, which is guaranteed by their shared formula,
    not a new fact) or a "conflicting_signal" match must not trigger a
    synthesis narrative; only genuinely independent, same-direction
    agreement counts as a connected story worth writing."""
    connected = [
        inv for inv in investigations
        if any(c["source"] == "cross_metric_corroboration" and c["verdict"] == "possible_contributor"
               for c in inv["root_cause"]["checks"])
    ]
    if len(connected) < 2:
        return None

    summary = [
        {
            "metric_id": inv["metric_id"],
            "title": inv["title"],
            "category": inv["category"],
            "domain": inv["domain"],
            "corroboration": next(
                c["detail"] for c in inv["root_cause"]["checks"] if c["source"] == "cross_metric_corroboration"
            ),
        }
        for inv in connected
    ]
    prompt = (
        "You are a business analyst. Each finding below was independently confirmed "
        "by Celeste's evidence engine to share the same underlying segment(s) as at "
        "least one other finding in this list -- see each item's `corroboration` "
        "field, which states exactly what was confirmed and by which other "
        "finding(s). This already-verified overlap is the ONLY basis you may use to "
        "connect them. Write ONE short paragraph (2-4 sentences, plain business "
        "language, no markdown, no bullet points, no headline) explaining what the "
        "combined pattern suggests is really going on, referencing only the metrics "
        "and segments named in the data below. Do not invent a mechanism, cause, or "
        "number that isn't explicitly stated in the `corroboration` field -- if the "
        "data doesn't say WHY the segments overlap, don't guess why; just state that "
        "they overlap and what that means for where to look first. Return ONLY the "
        "paragraph text, nothing else.\n\n"
        f"Findings:\n{json.dumps(summary, default=str)}"
    )
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_get_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        narrative = (response.choices[0].message.content or "").strip()
        if not narrative:
            return None
    except Exception:
        return None

    return {"narrative": narrative, "metric_ids": [inv["metric_id"] for inv in connected]}


def _trend_measurement(metric_id, horizon, result):
    anomaly = result["anomaly"]
    category = anomaly.get("category")
    status = ("measured_actionable" if category in ("critical", "opportunity")
              else "measured_healthy" if category == "normal" else "measurement_unavailable")
    freshness = "fresh"
    if horizon == "weekly":
        as_of = result.get("as_of")
        if as_of is None or (datetime.now(UTC).date() - as_of.date()).days > 1:
            freshness = "unknown" if as_of is None else "stale"
    latest = next((r for r in result.get("trend", []) if r["month"] == anomaly.get("latest_month")), {})
    if "denominator" in latest and latest["denominator"] <= 0:
        status = "measurement_unavailable"
    return measurement.observation(
        metric_id, horizon, status, period=anomaly.get("latest_month"),
        value=anomaly.get("latest_value"),
        freshness=freshness,
        coverage="complete" if category else "incomplete",
        reason="Insufficient complete observations or exposure." if status == "measurement_unavailable" else None,
    )


def build_investigation(metric_id: str, *, enrich=True):
    result = intelligence.investigate(metric_id)
    if "error" in result:
        return result

    metric, anomaly, trend = result["metric"], result["anomaly"], result["trend"]

    # anomaly has no "category" key at all when detect_anomaly() short-circuits
    # on insufficient_data (fewer than 2 complete months) — caught live when a
    # newly-added or sparsely-populated metric (e.g. supplier_fill_rate, whose
    # source is a narrow rolling-window view) hadn't accumulated enough
    # history yet. .get() instead of [...] so this degrades to "no_finding"
    # like any other non-actionable verdict, rather than crashing scan_all()
    # for every metric via the shared ThreadPoolExecutor.
    category = anomaly.get("category")
    if category not in ("critical", "opportunity"):
        return {"metric_id": metric_id, "category": category, "status": "no_finding",
                "measurement": _trend_measurement(metric_id, "monthly", result)}

    breakdowns = result["breakdowns"]
    impact = impact_engine.compute_impact(metric_id, intelligence.METRICS[metric_id], anomaly, trend, horizon="monthly")
    how = _estimate_how(metric_id, anomaly, breakdowns, horizon="monthly")
    # Evidence gathered BEFORE recommendations, not after — so the model
    # always has whatever evidence exists before it writes anything, never
    # the other way around.
    evidence = evidence_engine.build_trend_evidence(
        metric_id, anomaly, breakdowns, trend, result["persistence"], category
    )
    recommendations = (_generate_recommendations(metric, anomaly, breakdowns, impact, evidence)
                       if enrich else recommendation_schema.Recommendations(status="pending"))
    priority = _priority_for(anomaly)
    impact_score = impact_engine.business_impact_score(priority, result["persistence"], impact, evidence["data_quality"])

    return {
        "id": f"{metric_id}-{anomaly['latest_month']}",
        "measurement": _trend_measurement(metric_id, "monthly", result),
        "metric_id": metric_id,
        "domain": METRIC_DOMAIN.get(metric_id, "order_customer"),
        "priority": priority,
        "business_impact_score": impact_score,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "memory": evidence["memory"],
        "horizon": "monthly",
        # Already computed above for the anomaly/impact checks — exposed here
        # too so a Situation Card can render a real multi-month trend line
        # without an extra live intelligence.investigate() call (that path is
        # 3-14s per metric; this is free since scan_all() already paid for it).
        "trend": trend,
        # Same free-exposure reasoning as trend — used by the frontend's
        # business-impact ranking (consecutive_periods as a persistence
        # multiplier) instead of re-deriving it.
        "persistence": result["persistence"],
        # Exposed for the same reason as trend above — a breakdown chart needs
        # to know whether a positive delta is good or bad for THIS metric
        # (independent of whether this particular finding is critical/
        # opportunity), which only metric.direction carries.
        "metric": metric,
        "title": _headline(metric, anomaly),
        "category": anomaly["category"],
        "severity": SEVERITY_BY_CATEGORY[anomaly["category"]],
        "detected": date.today().isoformat(),
        "confirm": {
            "period": anomaly["latest_month"],
            "current": anomaly["latest_value"],
            "baseline": anomaly["baseline_mean"],
            "pct_change": anomaly["pct_change_vs_baseline"],
            "unit": metric["unit"],
            # Already computed by detect_anomaly(), just never exposed past
            # this point before -- Celeste Investigate Phase 3 revenue audit.
            # pct_change alone can overstate a move when the baseline months
            # varied a lot (e.g. a business's early ramp-up period pulling
            # the mean down); z_score is the honest counter-check -- a large
            # pct_change with a comparatively small z_score means the
            # baseline itself was too volatile for the percentage to read as
            # a clean signal. No new definition: this is the SAME z-score
            # detect_anomaly already uses to help decide is_significant.
            "z_score": anomaly.get("z_score"),
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": how,
        "recommendations": recommendations,
        "recommendation_generation": {"status": getattr(recommendations, "generation_status", "generated"),
                                      "rejected_count": getattr(recommendations, "rejected_count", 0)},
        "status": "completed",
    }


def scan_all():
    """Each metric's investigation is independent (its own DB queries, and its
    own LLM recommendation call if it's flagged) — run them concurrently rather
    than one after another. Verified sequentially this took ~100s dominated by
    per-flagged-metric LLM calls stacking up; threads give real concurrency
    here since both the DB driver and the OpenAI HTTP client release the GIL
    while waiting on I/O."""
    with ThreadPoolExecutor(max_workers=len(intelligence.METRICS)) as executor:
        results = list(executor.map(partial(_scan_metric, horizon="monthly"), intelligence.METRICS))

    investigations = [inv for inv in results if inv.get("status") == "completed"]
    # Second pass, after every metric's own evidence is built: check whether
    # two independently-computed findings from this same scan corroborate
    # each other (see evidence_engine.cross_metric_corroboration_checks) --
    # can only run once every investigation in the batch already exists, so
    # it can't live inside build_investigation()'s per-metric, parallel path.
    evidence_engine.cross_metric_corroboration_checks(investigations)
    with ThreadPoolExecutor(max_workers=max(1, len(investigations))) as executor:
        list(executor.map(_enrich_final_evidence, investigations))
    investigations.sort(key=lambda i: CATEGORY_ORDER.get(i["category"], 2))
    return measurement.FindingBatch(investigations, [r["measurement"] for r in results if "measurement" in r])


def _enrich_final_evidence(finding):
    confirm = finding["confirm"]
    anomaly = {"latest_month": confirm["period"], "latest_value": confirm["current"],
               "baseline_mean": confirm["baseline"], "pct_change_vs_baseline": confirm["pct_change"],
               "z_score": confirm.get("z_score"), "category": finding["category"],
               "moved_up": confirm["current"] > confirm["baseline"]}
    evidence = {**finding["root_cause"], "historical_context": finding.get("persistence"),
                "data_quality": finding.get("data_quality"), "memory": finding.get("memory")}
    recs = _generate_recommendations(finding["metric"], anomaly, finding["breakdowns"], finding["impact"], evidence)
    finding["recommendations"] = recs
    finding["recommendation_generation"] = {"status": getattr(recs, "generation_status", "generated"),
                                             "rejected_count": getattr(recs, "rejected_count", 0)}


def _scan_metric(metric_id, *, horizon):
    try:
        return (build_investigation(metric_id, enrich=False) if horizon == "monthly"
                else build_trailing_investigation(metric_id))
    except Exception:
        logger.exception("Trend detector failed: %s (%s)", metric_id, horizon)
        return {"metric_id": metric_id, "status": "no_finding", "measurement": measurement.observation(
            metric_id, horizon, "measurement_unavailable", coverage="incomplete", freshness="unknown",
            reason="The detector could not complete its source measurement; retry the scan.",
        )}


def build_trailing_investigation(metric_id: str):
    """Trailing 7-day-window counterpart to build_investigation() — same
    narration (title, breakdowns, recommendations), computed over the
    trailing window instead of a calendar month. Reuses every helper below
    unchanged except where a helper's underlying data source is genuinely
    monthly-only (impact pricing, delivery-stage timing) — those are passed
    horizon="weekly" and honestly return None rather than fabricate a
    monthly-shaped answer from a non-monthly period."""
    result = intelligence.investigate_trailing(metric_id)
    if "error" in result:
        return result

    metric, anomaly, trend = result["metric"], result["anomaly"], result["trend"]

    # Same insufficient_data guard as build_investigation() above.
    category = anomaly.get("category")
    if category not in ("critical", "opportunity"):
        return {"metric_id": metric_id, "category": category, "status": "no_finding",
                "measurement": _trend_measurement(metric_id, "weekly", result)}

    breakdowns = result["breakdowns"]
    impact = impact_engine.compute_impact(metric_id, intelligence.METRICS[metric_id], anomaly, trend, horizon="weekly")
    how = _estimate_how(metric_id, anomaly, breakdowns, horizon="weekly")
    # Same evidence-before-recommendations ordering as build_investigation().
    # No memory here — tracked_findings has no rows for trailing/weekly
    # periods (see brain.py), so build_trend_evidence's own memory lookup
    # would just return None; passing persistence is still worthwhile since
    # classify_persistence() works on a trailing trend the same way.
    evidence = evidence_engine.build_trend_evidence(
        metric_id, anomaly, breakdowns, trend, result["persistence"], category, include_memory=False
    )
    recommendations = _generate_recommendations(metric, anomaly, breakdowns, impact, evidence)
    priority = _priority_for(anomaly)
    impact_score = impact_engine.business_impact_score(priority, result["persistence"], impact, evidence["data_quality"])

    as_of_date = result["as_of"].date().isoformat() if result.get("as_of") else date.today().isoformat()

    return {
        "id": f"{metric_id}-trailing-{as_of_date}",
        "recommendation_generation": {"status": getattr(recommendations, "generation_status", "generated"),
                                      "rejected_count": getattr(recommendations, "rejected_count", 0)},
        "measurement": _trend_measurement(metric_id, "weekly", result),
        "metric_id": metric_id,
        "domain": METRIC_DOMAIN.get(metric_id, "order_customer"),
        "priority": priority,
        "business_impact_score": impact_score,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "horizon": "weekly",
        # The data's own latest date, not wall-clock today — exposed so
        # weekly_streaks.py can count consecutive *scanned* days a metric
        # stayed flagged without re-deriving it from the id string.
        "as_of_date": as_of_date,
        "trend": trend,
        "persistence": result["persistence"],
        "metric": metric,
        "title": _headline(metric, anomaly),
        "category": anomaly["category"],
        "severity": SEVERITY_BY_CATEGORY[anomaly["category"]],
        "detected": date.today().isoformat(),
        "confirm": {
            "period": anomaly["latest_month"],
            "current": anomaly["latest_value"],
            "baseline": anomaly["baseline_mean"],
            "pct_change": anomaly["pct_change_vs_baseline"],
            "unit": metric["unit"],
            # Already computed by detect_anomaly(), just never exposed past
            # this point before -- Celeste Investigate Phase 3 revenue audit.
            # pct_change alone can overstate a move when the baseline months
            # varied a lot (e.g. a business's early ramp-up period pulling
            # the mean down); z_score is the honest counter-check -- a large
            # pct_change with a comparatively small z_score means the
            # baseline itself was too volatile for the percentage to read as
            # a clean signal. No new definition: this is the SAME z-score
            # detect_anomaly already uses to help decide is_significant.
            "z_score": anomaly.get("z_score"),
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": how,
        "recommendations": recommendations,
        "status": "completed",
    }


def scan_short_term():
    """Weekly counterpart to scan_all() — only runs the metrics registered
    in SHORT_TERM_METRICS (those with a short_term_trend_sql defined), not
    every metric in intelligence.METRICS."""
    with ThreadPoolExecutor(max_workers=max(len(SHORT_TERM_METRICS), 1)) as executor:
        results = list(executor.map(partial(_scan_metric, horizon="weekly"), SHORT_TERM_METRICS))

    findings = [inv for inv in results if inv.get("status") == "completed"]
    findings.sort(key=lambda i: CATEGORY_ORDER.get(i["category"], 2))
    return measurement.FindingBatch(findings, [r["measurement"] for r in results if "measurement" in r])

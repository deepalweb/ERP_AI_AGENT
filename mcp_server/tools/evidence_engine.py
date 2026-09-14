"""The deterministic evidence layer behind every finding: own-breakdown
concentration, cross-metric corroboration, historical persistence, prior
outcome memory, and data-quality confidence. Every check here returns one
of a small fixed vocabulary of verdicts, never a free-text guess — the LLM
in investigation.py's _generate_recommendations() only narrates what's
already been verified here, it never decides what's true.

This module owns everything investigation.py used to compute ad hoc
(_segment_concentration_checks, _current_total_concentration_checks,
_stock_availability_check, _get_finding_memory — moved here unchanged) plus
two new signals: data_quality_check (sample size + staleness — the
exact class of gap that let a thinned-out source view crash the whole
nightly scan silently) and dark_outlet_check (does a flagged store also
have zero orders in the last 30 days).

Two orchestrators match the two real finding shapes in this codebase —
build_trend_evidence for a month-over-month (or trailing-window) anomaly,
build_snapshot_evidence for a point-in-time state finding (state_findings.py)
that has no baseline/trend/memory concept at all. Forcing both into one
shape would mean fabricating a "memory" or "historical_context" for
findings that structurally can't have one.

Previously, trend evidence only ran for metrics in an explicit allowlist
(EVIDENCE_ROOT_CAUSE_METRICS) because a rate/composition-mode breakdown
produces nothing but "unknown" verdicts — but segment_concentration_checks
already handles that gracefully (an honest, explained "unknown" per
breakdown, not a crash or a fabricated number). The allowlist was hiding
truthful "unknown" results instead of showing them, so it's gone: every
flagged finding now gets a real evidence package, even when large parts of
it honestly read "unknown".
"""

# The first transparent rule for "is this segment a confirmed driver of the
# move" — a stated cutoff, not a judgment call, same posture as
# investigation._priority_for's pct-change bands. Deliberately NOT meant to
# be a permanent universal constant: validate it against real flagged
# findings, then allow metric-specific thresholds if 60% turns out too
# strict or too loose for a particular metric's segment count/volatility.
CONCENTRATION_THRESHOLD_PCT = 60

# Below this many complete months, detect_anomaly() still fires (its own
# floor is 2) but the baseline is thin enough that the finding's confidence
# should be flagged, not presented with the same certainty as a
# well-supported one.
LOW_CONFIDENCE_MONTH_FLOOR = 3


def segment_concentration_checks(anomaly: dict, breakdowns: list, trend: list) -> list[dict]:
    """For each of the metric's own "contribution"-mode breakdowns (raw,
    summable values — e.g. revenue by store, cancellation COUNT by
    product), checks whether the move is concentrated in one or a few
    segments. Walks the top contributors (already sorted toward the
    direction of the move by _rank_contributors) accumulating their share
    of the TOTAL move until either the threshold is crossed or 3 segments
    have been used — matching "72% of the decline came from two locations"
    in spirit: concentration can be a small group, not just a single
    segment.

    "rate"-mode breakdowns (e.g. cancellation_rate by store: each store's
    OWN rate, in percentage points) and "composition"-mode breakdowns
    (e.g. cancellation reason: each reason's SHARE of cancellations) are
    deliberately NOT run through this same math — checked against real
    data before building this: a store's rate delta isn't additive across
    stores the way a raw count is (a tiny store with a huge rate swing
    would look "concentrated" while barely moving the company-wide rate),
    and a reason's share can shift even when the total cancellation count
    doesn't move at all — that's a mix-shift question, not a driver of the
    metric's own movement. Forcing both through the same ratio would
    produce a percentage that LOOKS as rigorous as the real one but isn't
    — so they get an honest 'unknown' instead, naming why.

    The total move is read from the metric's own trend series (current
    month vs. the same baseline month get_breakdown() itself used), using
    each month's numerator (a rate metric's raw count, e.g. cancelled
    orders) when present — the correct like-for-like total for a raw-count
    breakdown — and falling back to the plain value for a non-rate metric
    like revenue. Never summed from the breakdown's own top_contributors,
    which is truncated to the top 5 (TOP_N) and would silently understate
    the true total for a high-cardinality dimension like product or
    customer."""
    if not breakdowns:
        return []

    # Monthly breakdowns key their baseline as "baseline_month"; trailing
    # (weekly) breakdowns use "baseline_period_label" instead (a date-range
    # string, not a "YYYY-MM" key) — check both so this works on either
    # entry point (build_investigation / build_trailing_investigation)
    # rather than silently reporting "unknown" for every weekly finding.
    baseline_month = breakdowns[0].get("baseline_month") or breakdowns[0].get("baseline_period_label")
    latest_month = anomaly.get("latest_month")
    by_month = {m["month"]: m for m in trend}
    latest_row = by_month.get(latest_month)
    baseline_row = by_month.get(baseline_month)

    checks = []
    if latest_row is None or baseline_row is None or baseline_month is None:
        for b in breakdowns:
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "unknown",
                "detail": f"Could not determine the total {b['label'].lower()}-level movement to check "
                f"concentration against.",
            })
        return checks

    total_current = latest_row.get("numerator", latest_row.get("value"))
    total_baseline = baseline_row.get("numerator", baseline_row.get("value"))
    total_delta = total_current - total_baseline
    move_word = "increase" if total_delta > 0 else "decline"

    for b in breakdowns:
        if b["mode"] != "contribution":
            what_it_measures = "each segment's own rate" if b["mode"] == "rate" else "each segment's share of the mix"
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "unknown",
                "detail": f"This breakdown ({b['label'].lower()}, {b['mode']} mode) measures "
                f"{what_it_measures} rather than a raw contribution to the total — not yet supported by "
                f"this concentration check, since its values aren't directly comparable to the metric's "
                f"overall movement.",
            })
            continue

        contributors = b.get("top_contributors") or []
        if not contributors or not total_delta:
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "no_evidence_found",
                "detail": f"No {b['label'].lower()}-level data available to check concentration.",
            })
            continue

        cumulative_ratio = 0.0
        used = []
        for c in contributors[:3]:
            ratio = (c["delta"] / total_delta) if total_delta else 0
            if ratio <= 0:
                break  # this segment moved the OPPOSITE way from the overall trend — not a driver
            cumulative_ratio += ratio
            used.append(c)
            if cumulative_ratio * 100 >= CONCENTRATION_THRESHOLD_PCT:
                break

        cumulative_pct = round(cumulative_ratio * 100, 1)
        if used and cumulative_pct >= CONCENTRATION_THRESHOLD_PCT:
            names = ", ".join(c["name"] for c in used)
            # A used segment's own delta can exceed the net total delta in
            # magnitude when other segments moved the OPPOSITE way and
            # partially offset it — caught live on pickme_cancellation_rate
            # (one store's improvement was 156% of the net change, because
            # two smaller stores got slightly worse over the same period).
            # The underlying ratio is real, not a bug, but "156% of the
            # decline" reads like an error — phrase it honestly instead of
            # printing a percentage over 100.
            if cumulative_pct > 100:
                detail = (
                    f"{'One segment' if len(used) == 1 else f'{len(used)} segments'} ({names}) moved "
                    f"by more than the entire net {move_word} on its own — other segments moved the "
                    f"opposite way over the same period, partially offsetting it."
                )
            else:
                detail = (
                    f"{cumulative_pct:.0f}% of the {move_word} came from "
                    f"{'one segment' if len(used) == 1 else f'{len(used)} segments'}: {names}."
                )
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "confirmed",
                "detail": detail,
                "segments": [c["name"] for c in used],
                "concentration_pct": cumulative_pct,
            })
        else:
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "no_evidence_found",
                "detail": f"Checked — no single {b['label'].lower()} or small group accounts for a majority "
                f"of the {move_word}; the top segments cover only {max(cumulative_pct, 0):.0f}% of it.",
            })

    return checks


def current_total_concentration_checks(breakdowns: list) -> list[dict]:
    """Same >=60% concentration rule as segment_concentration_checks, for a
    current-state finding (state_findings.py) that has no baseline/trend —
    there's no "move" to attribute, so this asks "which segment holds most
    of the CURRENT total," the closest analogous evidence question a
    snapshot can actually answer.

    Only meaningful when a breakdown's own contributors are a genuine (or
    at least declared via an explicit "total") decomposition of a larger
    whole — e.g. dead-stock value by product. It is deliberately NOT applied
    to every state finding: a breakdown whose contributors already ARE the
    complete list of affected items by construction (e.g. "these are the
    products that are out of stock") would report a trivially-true 100%
    concentration that isn't a real finding — see state_findings.py for
    which breakdowns this is and isn't wired into."""
    checks = []
    for b in breakdowns:
        if b["mode"] != "contribution":
            what_it_measures = "each segment's own rate" if b["mode"] == "rate" else "each segment's share of the mix"
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "unknown",
                "detail": f"This breakdown ({b['label'].lower()}, {b['mode']} mode) measures "
                f"{what_it_measures} rather than a raw contribution to the total — not supported by this "
                f"concentration check.",
            })
            continue

        contributors = b.get("top_contributors") or []
        total = b.get("total")
        if total is None:
            total = sum(c["current"] for c in contributors)
        if not contributors or not total:
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "no_evidence_found",
                "detail": f"No {b['label'].lower()}-level data available to check concentration.",
            })
            continue

        cumulative_ratio = 0.0
        used = []
        for c in sorted(contributors, key=lambda c: c["current"], reverse=True)[:3]:
            ratio = (c["current"] / total) if total else 0
            if ratio <= 0:
                continue
            cumulative_ratio += ratio
            used.append(c)
            if cumulative_ratio * 100 >= CONCENTRATION_THRESHOLD_PCT:
                break

        cumulative_pct = round(cumulative_ratio * 100, 1)
        if used and cumulative_pct >= CONCENTRATION_THRESHOLD_PCT:
            names = ", ".join(c["name"] for c in used)
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "confirmed",
                "detail": f"{cumulative_pct:.0f}% of the current total is concentrated in "
                f"{'one segment' if len(used) == 1 else f'{len(used)} segments'}: {names}.",
                "segments": [c["name"] for c in used],
                "concentration_pct": cumulative_pct,
            })
        else:
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "no_evidence_found",
                "detail": f"Checked — no single {b['label'].lower()} or small group accounts for a majority "
                f"of the total; the top segments cover only {max(cumulative_pct, 0):.0f}% of it.",
            })

    return checks


def stock_availability_check(breakdowns: list) -> dict:
    """Cross-references a metric's declining product breakdown against
    today's stock-alert data — a real join over two already-computed
    datasets, not a guess. Only counts products whose OWN delta moved in
    the declining direction (a product that grew despite being flagged
    low-stock elsewhere isn't evidence of anything here)."""
    product_breakdown = next((b for b in breakdowns if b["dimension"] == "product"), None)
    if not product_breakdown or not product_breakdown.get("top_contributors"):
        return {
            "source": "stock_availability",
            "verdict": "unknown",
            "detail": "No product-level breakdown available to check against stock alerts.",
        }

    from backend.app.services.stock_alerts import get_stock_alerts

    alerts = get_stock_alerts()
    if not alerts.get("as_of_date"):
        return {"source": "stock_availability", "verdict": "unknown", "detail": "Stock alert data unavailable."}

    low_stock_names = {i["name"] for i in alerts.get("top_out_of_stock", [])}
    low_stock_names |= {i["name"] for i in alerts.get("top_low_stock", [])}
    declining_names = {c["name"] for c in product_breakdown["top_contributors"] if c["delta"] < 0}
    matched = sorted(declining_names & low_stock_names)

    if not matched:
        return {
            "source": "stock_availability",
            "verdict": "no_evidence_found",
            "detail": "Checked current stock alerts — none of the top declining products are currently "
            "out of stock or low stock.",
        }

    return {
        "source": "stock_availability",
        "verdict": "possible_contributor",
        "detail": f"{len(matched)} of the top declining products {'is' if len(matched) == 1 else 'are'} "
        f"currently out of stock or low stock. This is a real overlap, not proof of causation — "
        f"availability may be a contributing factor.",
        "segments": matched,
    }


def dark_outlet_check(breakdowns: list) -> dict:
    """Cross-metric signal: does a top concentrated store in this metric's
    own breakdown also currently have zero orders in the last 30 days
    (state_findings.py's outlet_no_orders finding)? A real, cheap
    corroboration — a store with no orders at all trivially explains a
    revenue/order_volume drop concentrated there, distinct from a store
    that's merely down but still trading."""
    store_breakdown = next((b for b in breakdowns if b["dimension"] == "store"), None)
    if not store_breakdown or not store_breakdown.get("top_contributors"):
        return {
            "source": "dark_outlet",
            "verdict": "unknown",
            "detail": "No store-level breakdown available to check against outlet activity.",
        }

    from backend.app.services.operations import get_dark_outlets

    dark = set(get_dark_outlets())
    if not dark:
        return {
            "source": "dark_outlet",
            "verdict": "no_evidence_found",
            "detail": "Checked — none of Celeste's outlets currently have zero orders in the last 30 days.",
        }

    flagged_names = {c["name"] for c in store_breakdown["top_contributors"]}
    matched = sorted(flagged_names & dark)
    if not matched:
        return {
            "source": "dark_outlet",
            "verdict": "no_evidence_found",
            "detail": "Checked — the stores flagged in this breakdown are still trading; the current "
            "zero-order outlet(s) aren't among them.",
        }

    return {
        "source": "dark_outlet",
        "verdict": "confirmed",
        "detail": f"{', '.join(matched)} currently {'has' if len(matched) == 1 else 'have'} zero orders in "
        f"the last 30 days — a real, separately-confirmed cause for this store's contribution.",
        "segments": matched,
    }


def data_quality_check(trend: list[dict]) -> dict:
    """Flags a finding built on a thin trend before a reader mistakes a
    freshly-populated or sparse source for a fully-trustworthy one. This is
    the signal that would have surfaced supplier_fill_rate's issue
    (its source view had thinned to 1 complete month) as a visible flag on
    the finding, instead of only failing loudly when detect_anomaly's
    stricter 2-month floor wasn't even met."""
    complete = [m for m in trend if not m["is_partial"]]
    months_available = len(complete)
    if months_available < LOW_CONFIDENCE_MONTH_FLOOR:
        return {
            "source": "data_quality",
            "verdict": "low_confidence",
            "months_available": months_available,
            "detail": f"Only {months_available} complete month(s) of history behind this finding — "
            f"the baseline comparison is less reliable than usual.",
        }
    return {
        "source": "data_quality",
        "verdict": "sufficient",
        "months_available": months_available,
        "detail": f"{months_available} complete months of history behind this finding.",
    }


def snapshot_data_quality_check(as_of_date) -> dict:
    """Staleness-only counterpart for a point-in-time state finding — there's
    no "sample size" concept for a snapshot, just how current it is.
    as_of_date arrives in whichever shape its caller already had on hand —
    an ISO date string (state_findings.get_stock_alerts's as_of_date) or a
    raw datetime (state_findings._dead_overstock_finding's as_of row) —
    normalized to a plain date here rather than forcing every caller to
    convert first."""
    from datetime import date as _date

    if as_of_date is None:
        return {"source": "data_quality", "verdict": "unknown", "detail": "No snapshot date available."}

    if isinstance(as_of_date, str):
        as_of = _date.fromisoformat(as_of_date)
    elif hasattr(as_of_date, "date"):
        as_of = as_of_date.date()
    else:
        as_of = as_of_date
    age_days = (_date.today() - as_of).days
    if age_days > 2:
        return {
            "source": "data_quality",
            "verdict": "low_confidence",
            "age_days": age_days,
            "detail": f"This snapshot is {age_days} days old — may not reflect the current state.",
        }
    return {
        "source": "data_quality",
        "verdict": "sufficient",
        "age_days": age_days,
        "detail": f"Snapshot is current (as of {age_days} day(s) ago).",
    }


def summarize(checks: list[dict]) -> dict:
    """Buckets a flat checks list by verdict — the direct answer to "what
    did we check but NOT find" and "what remains unknown": that information
    already exists per-check, but a reader (or the LLM) shouldn't have to
    filter a flat list themselves to see it."""
    summary = {"confirmed": [], "possible_contributor": [], "no_evidence_found": [], "unknown": []}
    for c in checks:
        verdict = c.get("verdict")
        if verdict in summary:
            summary[verdict].append(c["source"])
    return summary


def get_finding_memory(metric_id: str, current_period: str) -> dict | None:
    """The most recent PRIOR time this same metric was flagged — what was
    recommended then, whether a manager recorded acting on it
    (finding_actions.py), and what actually happened (tracking.py's
    verdict) — fed to _generate_recommendations() so it doesn't repeat
    advice already known not to have worked, or stay silent about
    something already in progress.

    This isn't new data: Home.jsx's findPriorFinding() already prints this
    same "last time, in <period> — outcome: <verdict>" line next to every
    card. The gap this closes is that the LLM writing the recommendation
    text has never been shown it. Using get_most_recent_prior (not the
    resolved-only lookup) keeps this in sync with exactly what's on
    screen, including the still-open/"pending" case.

    Lazily imported to avoid a module-load-time dependency from
    mcp_server.tools onto backend.app.services for metrics that never
    reach this path. Only wired into the monthly path — tracked_findings
    has no rows for trailing/weekly periods at all (see brain.py: only
    `investigations` is passed to tracking.record_new_findings), so there
    is nothing to remember there yet; weekly_streaks.py is that horizon's
    own memory mechanism already."""
    from backend.app.services import finding_actions, tracking

    prior = tracking.get_most_recent_prior(metric_id, before_period=current_period)
    if prior is None:
        return None

    memory = {"prior_period": prior["period"], "prior_recommendations": prior["recommendations"]}
    if prior["status"] == "resolved":
        memory["outcome_verdict"] = prior["verdict"]
        memory["outcome_detail"] = (
            f"Deviation from baseline went from {prior['detected_pct_change']}% to "
            f"{prior['outcome_pct_change']}% by {prior['outcome_period']}."
        )
    else:
        memory["outcome_verdict"] = "pending"
        memory["outcome_detail"] = "Still being monitored — the outcome isn't known yet."

    prior_finding_id = f"{metric_id}-{prior['period']}"
    action = finding_actions.get_actions_for([prior_finding_id]).get(prior_finding_id)
    if action and action.get("action_taken"):
        memory["action_taken_by_team"] = action["action_taken"]
    return memory


def build_trend_evidence(metric_id: str, anomaly: dict, breakdowns: list, trend: list, persistence: dict,
                          category: str, include_memory: bool = True) -> dict:
    """Full evidence package for a trend finding (build_investigation /
    build_trailing_investigation). Runs unconditionally now — no more
    per-metric allowlist (see this module's docstring for why); a metric
    whose breakdowns are all rate/composition mode simply comes back with
    honest 'unknown' checks instead of being skipped.

    include_memory=False for the trailing/weekly path: tracked_findings
    only ever has monthly ("YYYY-MM") periods (build_investigation is the
    only caller of tracking.record_new_findings), while a trailing
    anomaly's "latest_month" is a date-range label like 'Jul 26 – Aug 02'
    — passing that as get_finding_memory's before_period would compare a
    date-range string against "YYYY-MM" strings, which isn't a meaningful
    comparison. Rather than return a technically-not-wrong-but-not-really-
    right answer, this path skips memory entirely, same as before this
    module existed."""
    checks = segment_concentration_checks(anomaly, breakdowns, trend)
    if category == "critical":
        checks.append(stock_availability_check(breakdowns))
        checks.append(dark_outlet_check(breakdowns))

    return {
        "checks": checks,
        "summary": summarize(checks),
        "data_quality": data_quality_check(trend),
        "historical_context": persistence,
        "memory": get_finding_memory(metric_id, anomaly.get("latest_month")) if include_memory else None,
    }


def build_snapshot_evidence(breakdowns: list, as_of_date) -> dict:
    """Full evidence package for a point-in-time state finding
    (state_findings.py). No memory/historical_context — these findings
    aren't tracked over time (no baseline/period concept to track against),
    same documented exclusion already in state_findings.py."""
    checks = current_total_concentration_checks(breakdowns)
    return {
        "checks": checks,
        "summary": summarize(checks),
        "data_quality": snapshot_data_quality_check(as_of_date),
    }

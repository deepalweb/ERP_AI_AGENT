"""The deterministic evidence layer behind every finding: own-breakdown
concentration, cross-metric corroboration, historical persistence, prior
outcome memory, and data-quality confidence. Every check here returns one
of a small fixed vocabulary of verdicts, never a free-text guess — the LLM
in investigation.py's _generate_recommendations() only narrates what's
already been verified here, it never decides what's true. The vocabulary is
"confirmed" (own-breakdown concentration only — the one check with a
same-metric, ratio-based statistical basis), "possible_contributor" (a real
signal, never proof of causation), "related_not_independent" (two metrics
moved together for a purely mathematical/definitional reason — shown for
context, never counted as support), "no_evidence_found", and "unknown".
cross_metric_corroboration_checks never emits "confirmed" — see that
function's own docstring for why co-location across findings is evidence of
an affected segment, not evidence of a cause.

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

from mcp_server.tools import intelligence

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


def segment_concentration_checks(
    anomaly: dict, breakdowns: list, trend: list, total_delta_unit: str | None = None
) -> list[dict]:
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

    IMPORTANT, a real defect this now labels explicitly (move_span, below):
    "the same baseline month get_breakdown() itself used" is always the
    single PRIOR calendar month — a different, usually much smaller
    quantity than detect_anomaly's baseline_mean (the mean of every
    complete month before the latest one), which is what the case's own
    headline/confirm/impact actually compare against. A concentration
    check's "X% of the increase came from Store Y" was previously worded
    as if "the increase" were that same headline figure — it isn't. Every
    detail string below now names the exact two months being compared
    (move_span) so a reader can't mistake a month-over-month concentration
    finding for an explanation of the headline's differently-based percentage.
    customer.

    total_delta_unit (Phase 3 analytical validation, Check 2 -- "display each
    comparison's ... absolute amount ... together"): the caller resolves this
    once from the metric definition (intelligence.METRICS[metric_id].unit,
    or "count" when is_rate, since a rate metric's own total_delta is read
    from numerator -- a raw count -- not its percentage value) and passes it
    through so a check's absolute total_delta can be formatted correctly by
    the frontend's existing formatByUnit, without this function doing its
    own metric lookup or the frontend guessing a unit."""
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
    # Celeste Investigate Phase 3 revenue audit (confirmed defect): this
    # check's own "increase"/"decline" is the single-prior-month move
    # (baseline_month -> latest_month, e.g. Jul -> Aug) -- a DIFFERENT,
    # usually much smaller quantity than detect_anomaly's own baseline_mean
    # comparison (the mean of every complete month before latest_month),
    # which is what the case's headline/confirm/impact actually describe.
    # Caught on a real revenue case: the headline read "85% increase vs its
    # usual range" (Rs 92.2M, latest vs. the 6-month mean), while this
    # check's own total_delta was only Rs 3.48M (Aug vs. Jul alone) -- so a
    # "96% of the increase came from Store X" line, read next to that
    # headline, implied 96% of the Rs 92.2M, when it actually explained 96%
    # of a number 26x smaller. The move_span label below makes the compared
    # period explicit so this can't be misread as explaining the headline's
    # own (differently-based) percentage -- no change to the underlying
    # month-over-month computation itself, which is a legitimate signal on
    # its own terms, just previously mislabeled.
    move_span = f"{baseline_month} to {latest_month}"

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
                    f"by more than the entire net {move_word} from {move_span} on its own — other "
                    f"segments moved the opposite way over the same period, partially offsetting it."
                )
            else:
                detail = (
                    f"{cumulative_pct:.0f}% of the {move_word} from {move_span} came from "
                    f"{'one segment' if len(used) == 1 else f'{len(used)} segments'}: {names}."
                )
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "confirmed",
                "detail": detail,
                "segments": [c["name"] for c in used],
                "concentration_pct": cumulative_pct,
                "total_delta": round(total_delta, 4),
                "total_delta_unit": total_delta_unit,
            })
        else:
            # Same label-can-be-a-phrase grammar fix as current_total_
            # concentration_checks' own no_evidence_found branch above.
            checks.append({
                "source": f"{b['dimension']}_concentration",
                "verdict": "no_evidence_found",
                "detail": f"Checked — no single record or small group within {b['label'].lower()} accounts for "
                f"a majority of the {move_word} from {move_span}; the top segments cover only "
                f"{max(cumulative_pct, 0):.0f}% of it.",
                "total_delta": round(total_delta, 4),
                "total_delta_unit": total_delta_unit,
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
                # b["label"] is sometimes a full descriptive phrase (e.g.
                # "Products with no recorded cost"), not a bare singular
                # noun -- an earlier version of this sentence assumed the
                # latter ("no single products with no recorded cost or
                # small group accounts..."), a real grammar defect caught
                # live. "record" is deliberately generic so this reads
                # correctly regardless of which shape the label is.
                "verdict": "no_evidence_found",
                "detail": f"Checked — no single record or small group within {b['label'].lower()} accounts for "
                f"a majority of the total; the top segments cover only {max(cumulative_pct, 0):.0f}% of it.",
            })

    return checks


def stock_availability_check(breakdowns: list, moved_up: bool) -> dict:
    """Cross-references a metric's product breakdown against today's
    stock-alert data — a real join over two already-computed datasets,
    not a guess.

    Only counts products whose OWN delta moved in the SAME direction as
    the metric's own bad move (a product that moved the opposite way
    isn't evidence of anything here). This is direction-aware, not
    hardcoded to "declining", because a product's role in a contribution
    breakdown flips depending on what the breakdown counts: revenue's
    product breakdown sums completed SALES, so a product driving a
    revenue decline shows up as a NEGATIVE delta (its sales fell) — but
    cancellation_rate's product breakdown counts CANCELLED orders, so a
    product driving a cancellation-rate increase shows up as a POSITIVE
    delta (more cancellations attributed to it, not fewer). Hardcoding
    `delta < 0` silently only ever worked for the first case; caught live
    tracing a real cancellation-driven-by-a-stockout scenario, where a
    growing product would never match despite being the exact cause a
    reader would want confirmed. This function is only ever called when
    the metric has already moved in its own bad direction (category ==
    "critical", see build_trend_evidence), so `moved_up` alone says which
    delta sign is the culprit for THIS call: an increase (moved_up=True,
    e.g. cancellation_rate) or a decrease (moved_up=False, e.g. revenue)."""
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
    contributing_names = {
        c["name"] for c in product_breakdown["top_contributors"] if (c["delta"] > 0 if moved_up else c["delta"] < 0)
    }
    matched = sorted(contributing_names & low_stock_names)

    if not matched:
        return {
            "source": "stock_availability",
            "verdict": "no_evidence_found",
            "detail": "Checked current stock alerts — none of the top contributing products are currently "
            "out of stock or low stock.",
        }

    return {
        "source": "stock_availability",
        "verdict": "possible_contributor",
        "detail": f"{len(matched)} of the top contributing products {'is' if len(matched) == 1 else 'are'} "
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


def shrinkage_loss_damage_check(breakdowns: list, start_date: str, end_date: str) -> dict:
    """Completed Stock-Count Variance & Shrinkage Investigation: cross-
    references a variance-by-outlet breakdown against the SEPARATE
    picking-loss/damage signal (loss_and_damage.get_loss_and_damage) for
    the SAME explicit date range the variance was computed over (passed in
    by the caller -- breakdown dicts carry no period field of their own,
    and get_loss_and_damage()'s own default ("last month") would silently
    mismatch the variance window otherwise) -- same posture as stock_
    availability_check (a real overlap between two independently-computed
    datasets), NOT dark_outlet_check's posture. dark_outlet_check is
    allowed to say "confirmed" because zero orders is a DEFINITIONAL cause
    of zero revenue there; a store showing both stock-count variance and
    elevated picking loss/damage has no such guaranteed mechanical link --
    both could stem from the same root problem, or be unrelated. Verdict
    ceiling is therefore "possible_contributor", worded explicitly as
    correlation, never cause."""
    store_breakdown = next((b for b in breakdowns if b["dimension"] == "store"), None)
    if not store_breakdown or not store_breakdown.get("top_contributors"):
        return {
            "source": "shrinkage_loss_damage",
            "verdict": "unknown",
            "detail": "No store-level breakdown available to check against loss & damage data.",
        }

    from backend.app.services.loss_and_damage import get_loss_and_damage

    loss = get_loss_and_damage(start_date=start_date, end_date=end_date)
    flagged_by_loss = {r["outlet"] for r in loss.get("shortage_by_store", []) if r.get("qty")}
    flagged_by_damage = {r["outlet"] for r in loss.get("damaged_by_store", []) if r.get("qty")}
    flagged = flagged_by_loss | flagged_by_damage
    if not flagged:
        return {
            "source": "shrinkage_loss_damage",
            "verdict": "no_evidence_found",
            "detail": "Checked loss & damage data for this period — no outlets show elevated picking "
            "shortages or damaged-goods lines.",
        }

    variance_names = {c["name"] for c in store_breakdown["top_contributors"]}
    matched = sorted(variance_names & flagged)
    if not matched:
        return {
            "source": "shrinkage_loss_damage",
            "verdict": "no_evidence_found",
            "detail": "Checked loss & damage data for this period — the outlet(s) driving this variance "
            "don't show elevated picking shortages or damage.",
        }

    return {
        "source": "shrinkage_loss_damage",
        "verdict": "possible_contributor",
        "detail": f"{', '.join(matched)} also {'shows' if len(matched) == 1 else 'show'} elevated picking "
        f"shortages or damaged-goods lines this period — a real overlap, not proof that one caused "
        f"the other.",
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


def _independence_group(metric_id: str) -> str:
    """A metric's declared independence_group (see MetricDef's own
    docstring), or its own id when none is declared -- a metric nobody has
    explicitly grouped is treated as independent of everything else by
    default, rather than guessed at. Falls back to the bare metric_id for an
    id intelligence.METRICS doesn't recognize (defensive only -- every
    metric a real investigation carries is registered there)."""
    metric = intelligence.METRICS.get(metric_id)
    return (metric.independence_group if metric else None) or metric_id


def _is_independence_reviewed(metric_id: str) -> bool:
    """Conservative gate: an unrecognized or never-inspected metric (the
    dataclass default, independence_reviewed=False) is not allowed to
    strengthen OR contradict another finding -- see MetricDef.
    independence_reviewed's own docstring for why the group-default alone
    isn't safe."""
    metric = intelligence.METRICS.get(metric_id)
    return bool(metric and metric.independence_reviewed)


def _expected_relationship(metric_id: str, other_metric_id: str) -> str | None:
    """A declared, opt-in same_direction/opposite_direction expectation
    between these two SPECIFIC metrics (see MetricDef.expected_relationship),
    checked from either side since only one of the pair may have declared
    it. None -- by far the common case -- means no claim is made about how
    they should relate, which cross_metric_corroboration_checks below
    treats as "say nothing" for a category mismatch, not "assume conflict"."""
    metric = intelligence.METRICS.get(metric_id)
    if metric and other_metric_id in (metric.expected_relationship or {}):
        return metric.expected_relationship[other_metric_id]
    other = intelligence.METRICS.get(other_metric_id)
    if other and metric_id in (other.expected_relationship or {}):
        return other.expected_relationship[metric_id]
    return None


def cross_metric_corroboration_checks(investigations: list[dict]) -> None:
    """Looks ACROSS every finding from the same scan_all() batch for a
    stronger signal than any one finding's own breakdown can give: does a
    genuinely different, independently-computed metric's confirmed
    concentration also point at the same named segment (store/product/
    customer/supplier), over the SAME period, in the SAME direction? Two
    metrics agreeing this way -- e.g. Uber's own failure-rate tracking and
    PickMe's own cancellation tracking, two entirely separate courier
    companies' systems, both naming Colombo 6 -- is real corroboration from
    an independent source. This is the generalized version of what
    stock_availability_check/dark_outlet_check already do (cross-reference a
    breakdown's top segments against one specific other signal); this
    extends the same idea to every other metric's own confirmed evidence
    instead of one hardcoded source.

    Four guards keep this from overclaiming, each addressing a real failure
    mode found live before this version shipped:

    1. INDEPENDENCE. Two metrics sharing a MetricDef.independence_group
       (revenue/order_volume/average_order_value/cancellation_rate all draw
       from the same sale_order rows -- see intelligence.py) are NOT treated
       as independent evidence of each other, no matter how many of them
       agree -- agreeing is expected, even guaranteed, by their shared
       formula, not a new fact about the business. This is still real,
       useful information (a reader may want to know order_volume moved the
       same way as revenue), so it's kept, just under its own verdict
       ("related_not_independent") that never strengthens a case and is
       explicitly excluded from generate_daily_synthesis's gate.

    1b. REVIEW STATUS. Even a metric in its own independence_group (i.e. not
       grouped with anything) only participates if MetricDef.
       independence_reviewed is True -- someone has actually inspected its
       SQL and confirmed it doesn't share a formula/base table with other
       metrics. An unreviewed metric (the dataclass default) is excluded
       from every bucket below: it cannot strengthen a case as
       "independent" corroboration, and it cannot be reported as
       "conflicting" either, since neither claim is responsible without
       having checked.

    2. EXPLANATORY RELEVANCE, KEPT SEPARATE FROM INDEPENDENCE. Not being in
       the same independence_group only means two metrics aren't
       arithmetically linked -- it says nothing about whether one actually
       bears on explaining the other. Two independent metrics matching
       category (both "critical", or both "opportunity") at the same
       segment is a real, worth-keeping OBSERVATION (co-location), but is
       NOT on its own treated as explanatory support: revenue and
       uber_failure_rate both naming Colombo 6 doesn't mean either explains
       the other unless someone has actually reviewed and declared that
       relationship. A "possible_contributor"/cross_metric_corroboration
       claim (or a "conflicting_signal" claim) therefore requires a
       MetricDef.expected_relationship entry EXPLICITLY declared between
       these two specific metrics:
         - declared "same_direction" + matching category -> supporting
         - declared "same_direction" + differing category -> conflicting
           (it was expected to move together and didn't)
         - declared "opposite_direction" + differing category -> supporting
           (the expected, consistent pattern)
         - declared "opposite_direction" + matching category -> conflicting
         - NO declaration at all, either category outcome -> neither. Both
           metrics' own confirmed concentration checks stay exactly as they
           are (the affected-segment observation is preserved); no
           cross-metric check is added in either direction, so nothing here
           manufactures a claim -- support or contradiction -- that was
           never actually reviewed. Live case that motivated tightening
           this further: revenue and uber_failure_rate previously
           corroborated purely from matching category with no declared
           relationship between them at all -- an unsupported explanatory
           claim from co-location alone.

    3. TIME WINDOW + DATA QUALITY. Only investigations whose confirm.period
       matches exactly participate (comparing August's revenue against
       July's order_volume would compare two different events without
       saying so), and a finding with data_quality.verdict == "low_confidence"
       doesn't participate on either side -- thin evidence shouldn't get to
       corroborate, or be corroborated.

    Never emits "confirmed": co-location across independent findings is
    evidence something is happening in the same place at the same time, not
    proof of what caused it (requirement: never upgrade a finding to a
    confirmed root cause solely because correlated metrics agree). Matches
    by (dimension, segment name) -- dimension ids (store/product/customer/
    supplier/reason) are shared vocabulary across every MetricDef, all
    resolving to the same underlying entities, so a name match across two
    metrics' breakdowns is a real join, not a coincidence, though it's still
    a NAME match rather than a database entity id -- see this module's
    known-limitations notes (tests/test_evidence_engine.py) for that gap.

    Mutates each investigation's root_cause.checks/summary in place rather
    than returning a new structure -- scan_all() already holds the
    references its caller will return.

    Deliberately scoped to the monthly scan_all() batch only -- not the
    trailing/weekly path (a much smaller, separate metric set over a
    different window)."""
    by_segment: dict[tuple[str, str], list[dict]] = {}
    for inv in investigations:
        if (inv.get("data_quality") or {}).get("verdict") == "low_confidence":
            continue
        period = (inv.get("confirm") or {}).get("period")
        if not period:
            continue
        entry = {"inv": inv, "group": _independence_group(inv["metric_id"]), "period": period, "category": inv.get("category")}
        for check in inv["root_cause"]["checks"]:
            if not check["source"].endswith("_concentration") or check["verdict"] != "confirmed":
                continue
            dimension = check["source"][: -len("_concentration")]
            for name in check.get("segments", []):
                by_segment.setdefault((dimension, name), []).append(entry)

    for inv in investigations:
        if (inv.get("data_quality") or {}).get("verdict") == "low_confidence":
            continue
        own_metric = inv["metric_id"]
        own_group = _independence_group(own_metric)
        own_period = (inv.get("confirm") or {}).get("period")
        own_category = inv.get("category")
        if not own_period:
            continue

        independent: dict[str, set[str]] = {}
        dependent: dict[str, set[str]] = {}
        conflicting: dict[str, set[str]] = {}
        for check in inv["root_cause"]["checks"]:
            if not check["source"].endswith("_concentration") or check["verdict"] != "confirmed":
                continue
            dimension = check["source"][: -len("_concentration")]
            for name in check.get("segments", []):
                for other in by_segment.get((dimension, name), []):
                    other_inv = other["inv"]
                    other_metric = other_inv["metric_id"]
                    if other_metric == own_metric or other["period"] != own_period:
                        continue
                    if not (_is_independence_reviewed(own_metric) and _is_independence_reviewed(other_metric)):
                        continue  # unreviewed metric -- refuse to strengthen OR contradict
                    label = other_inv["metric"]["label"]
                    if other["group"] == own_group:
                        dependent.setdefault(name, set()).add(label)
                        continue
                    same_category = other["category"] == own_category
                    relationship = _expected_relationship(own_metric, other_metric)
                    # Independence (different group, both reviewed) is a
                    # necessary but NOT sufficient condition for an
                    # explanatory claim -- a declared relationship for THIS
                    # specific pair is required before matching (or
                    # mismatched) category becomes support (or conflict).
                    # No declaration -> no claim, regardless of category.
                    if relationship == "same_direction":
                        bucket = independent if same_category else conflicting
                    elif relationship == "opposite_direction":
                        bucket = independent if not same_category else conflicting
                    else:
                        bucket = None
                    if bucket is not None:
                        bucket.setdefault(name, set()).add(label)

        if independent:
            parts = [f"{name} (also flagged by {', '.join(sorted(labels))})" for name, labels in independent.items()]
            inv["root_cause"]["checks"].append({
                "source": "cross_metric_corroboration",
                "verdict": "possible_contributor",
                "detail": "A different, independently-computed metric's own confirmed breakdown also "
                "points at the same segment(s), over the same period, in the same direction -- a real "
                "overlap, not proof of causation: " + "; ".join(parts) + ".",
                "segments": sorted(independent.keys()),
            })
            inv["root_cause"]["summary"]["possible_contributor"].append("cross_metric_corroboration")

        if dependent:
            parts = [f"{name} (moved with {', '.join(sorted(labels))})" for name, labels in dependent.items()]
            inv["root_cause"]["checks"].append({
                "source": "related_metric_agreement",
                "verdict": "related_not_independent",
                "detail": "These metrics share the same underlying order data, so their agreement is "
                "expected rather than new evidence -- shown for context, not counted as corroboration: "
                + "; ".join(parts) + ".",
                "segments": sorted(dependent.keys()),
            })
            inv["root_cause"]["summary"]["related_not_independent"].append("related_metric_agreement")

        if conflicting:
            parts = [f"{name} (moved the opposite way in {', '.join(sorted(labels))})" for name, labels in conflicting.items()]
            inv["root_cause"]["checks"].append({
                "source": "conflicting_signal",
                "verdict": "no_evidence_found",
                "detail": "Checked — a different, independent metric also confirmed the same segment(s) "
                "over the same period, but moved the OPPOSITE direction, so it doesn't support this "
                "finding's explanation: " + "; ".join(parts) + ".",
                "segments": sorted(conflicting.keys()),
            })
            inv["root_cause"]["summary"]["no_evidence_found"].append("conflicting_signal")


def summarize(checks: list[dict]) -> dict:
    """Buckets a flat checks list by verdict — the direct answer to "what
    did we check but NOT find" and "what remains unknown": that information
    already exists per-check, but a reader (or the LLM) shouldn't have to
    filter a flat list themselves to see it."""
    summary = {
        "confirmed": [], "possible_contributor": [], "related_not_independent": [],
        "no_evidence_found": [], "unknown": [],
    }
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
    metric_def = intelligence.METRICS.get(metric_id)
    # A rate metric's total_delta comes from numerator (a raw count, e.g.
    # cancelled orders) -- never its own percent unit -- see
    # segment_concentration_checks' own total_delta_unit docstring note.
    total_delta_unit = "count" if (metric_def and metric_def.is_rate) else (metric_def.unit if metric_def else None)
    checks = segment_concentration_checks(anomaly, breakdowns, trend, total_delta_unit)
    if category == "critical":
        checks.append(stock_availability_check(breakdowns, anomaly["moved_up"]))
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

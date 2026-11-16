"""Fresh-produce production at the central fresh warehouse (FWH/CFWH01).

Celeste repacks fresh produce (and some fresh-adjacent goods) into retail
units via Odoo MRP, scheduled as one manufacturing order per product per
"round" (morning/evening) via mrp_production.fresh_delivery_round -- a
Celeste-specific field, so this is a dedicated operational concept, not
generic MRP noise.

Two distinct, confirmed problems, each its own finding:

1. Missed rounds: the vast majority of scheduled rounds never get confirmed
   at all -- verified live (2026-10-27) that 32,395 of 33,673 draft
   fresh-round productions are scheduled before the most recent completed
   one, and 30,536 of those are overdue by more than a week. This is not a
   reporting lag (completion lag for rounds that DO finish is p99 ~4.6 days,
   so a 7-day buffer before calling one "missed" is generous) and it is not
   routine cleanup (only 232 mrp_production rows are ever cancelled,
   company-wide, all time) -- these just sit abandoned. Confirmed with the
   user that an unconfirmed round means that product was never made for that
   round, not a parallel confirmation path this app can't see.

2. Output shortfall on rounds that DO complete: 27% of completed rounds
   (30 days) produce a different quantity than planned, skewed hard toward
   under-producing (483 under vs 111 over, of 2,229 completed). Most
   completed rounds match exactly (median deviation is 0%), so this is not
   rounding noise -- the real population worth flagging is the tail: p90
   deviation is 50%, p95 is 71.7%.

3. Cycle time: the gap between when fresh production is demanded and when
   it is actually finished. There is no separate "demand submitted" event
   to compare against -- verified live that mrp_production.date_start is
   set within seconds of the parent fresh_record.create_date (the demand
   record), so date_start IS effectively the demand-submission moment, and
   (date_finished - date_start) already measures demand-to-ready lead time.
   Morning and evening rounds run on genuinely different tempos (verified:
   evening's own p95 is ~18h, morning's is ~92h over a 46-day baseline) --
   a flat threshold would either spam evening or never catch morning, so
   this flags the slowest 5% WITHIN each round's own distribution for the
   reporting window, not a shared absolute cutoff (verified: 113 of 2,229
   completed rounds, 69 distinct products, over 30 days).

   Two other asks from this same request were checked and found untraceable
   with real data, not built: (a) "which employee does the most production"
   -- fresh_scan_log (the per-unit scan event log) has only 2 distinct
   user_id values ever, one being a shared device account ("FWHPro",
   147,963 scans) -- it is not per-employee identity. mrp_production's
   write_uid is a real, varied proxy (who closed the order out) but is used
   nowhere here since no threshold logic was asked for it, just a question
   of who to credit/blame, which this app should not assert without being
   asked for that specific view. (b) "wastage of production" -- stock_scrap
   (Odoo's dedicated scrap/wastage model) has zero rows linked to any
   fresh-round production, ever; the only real proxy is BOM raw-material
   consumption variance (same signal verified during the Price Intelligence
   plan's Phase 5 grounding), which is a different claim ("used more/less
   raw material than planned") from literal recorded wastage.

All findings are company-wide (one location: FWH/CFWH01, warehouse_id 29 --
verified this is the only source location for every fresh-round production,
and it isn't mapped to any single outlet's res_store), so there is no
outlet breakdown here, only product and round (morning/evening).
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from backend.app.db import appdb, reporting
from mcp_server.tools import evidence_engine, impact_engine, measurement

WINDOW_DAYS = 30
# p99 completion lag for rounds that DO finish is ~4.6 days (verified live,
# 60-day lookback) -- 7 days leaves margin so a still-in-flight round is
# never mistaken for a missed one.
OVERDUE_BUFFER_DAYS = 7
# Combined floor+ratio cut, same style as dispatch_overage.py: median
# deviation on completed rounds is exactly 0% (most match plan), so this
# excludes that routine population and keeps the real tail (p90 is 50%,
# p95 is 71.7% deviation).
MIN_SHORTFALL_PCT = 20
MIN_SHORTFALL_UNITS = 5
# Flags the slowest 5% within each round's own cycle-time distribution for
# the window, not a shared absolute cutoff -- verified evening and morning
# rounds run on genuinely different tempos (p95 ~18h vs ~92h over a 46-day
# baseline), so one flat threshold would either spam evening or miss morning.
CYCLE_TIME_PERCENTILE = 0.95

MISSED_ROUNDS_SQL = f"""
    WITH bounds AS (SELECT MAX(date_start) AS as_of FROM mrp_production WHERE state = 'done')
    SELECT mp.id, mp.name, mp.origin, mp.fresh_delivery_round AS round, mp.date_start, mp.product_qty AS qty,
           pt.name->>'en_US' AS product, pt.default_code AS sku, pt.celeste_cost_price_ex_vat AS unit_cost,
           ru.login AS responsible
    FROM mrp_production mp
    JOIN bounds b ON true
    JOIN product_product pp ON pp.id = mp.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN res_users ru ON ru.id = mp.user_id
    WHERE mp.fresh_delivery_round IS NOT NULL AND mp.state = 'draft'
      AND mp.date_start < b.as_of - INTERVAL '{OVERDUE_BUFFER_DAYS} days'
      AND mp.date_start >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
"""

SHORTFALL_SQL = f"""
    WITH bounds AS (SELECT MAX(date_start) AS as_of FROM mrp_production WHERE state = 'done')
    SELECT mp.id, mp.name, mp.origin, mp.fresh_delivery_round AS round, mp.date_start, mp.date_finished,
           mp.product_qty AS planned_qty, mp.qty_producing AS produced_qty,
           pt.name->>'en_US' AS product, pt.default_code AS sku, pt.celeste_cost_price_ex_vat AS unit_cost,
           ru.login AS responsible, wu.login AS completed_by
    FROM mrp_production mp
    JOIN bounds b ON true
    JOIN product_product pp ON pp.id = mp.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN res_users ru ON ru.id = mp.user_id
    LEFT JOIN res_users wu ON wu.id = mp.write_uid
    WHERE mp.fresh_delivery_round IS NOT NULL AND mp.state = 'done' AND mp.product_qty > 0
      AND mp.date_start >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
      AND ABS(mp.qty_producing - mp.product_qty) >= {MIN_SHORTFALL_UNITS}
      AND ABS(mp.qty_producing - mp.product_qty) / mp.product_qty * 100 >= {MIN_SHORTFALL_PCT}
"""

CYCLE_TIME_SQL = f"""
    WITH bounds AS (SELECT MAX(date_start) AS as_of FROM mrp_production WHERE state = 'done' AND fresh_delivery_round IS NOT NULL),
    pool AS (
        SELECT mp.id, mp.fresh_delivery_round AS round,
               EXTRACT(EPOCH FROM (mp.date_finished - mp.date_start)) / 3600.0 AS cycle_hours
        FROM mrp_production mp, bounds
        WHERE mp.fresh_delivery_round IS NOT NULL AND mp.state = 'done' AND mp.date_finished IS NOT NULL
          AND mp.date_start >= bounds.as_of - INTERVAL '{WINDOW_DAYS} days'
    ),
    round_cut AS (
        SELECT round, PERCENTILE_CONT({CYCLE_TIME_PERCENTILE}) WITHIN GROUP (ORDER BY cycle_hours) AS cutoff_hours
        FROM pool GROUP BY round
    )
    SELECT mp.id, mp.name, mp.origin, mp.fresh_delivery_round AS round, mp.date_start, mp.date_finished,
           p.cycle_hours, rc.cutoff_hours, mp.product_qty AS qty,
           pt.name->>'en_US' AS product, pt.default_code AS sku,
           ru.login AS responsible, wu.login AS completed_by
    FROM mrp_production mp
    JOIN pool p ON p.id = mp.id
    JOIN round_cut rc ON rc.round = p.round
    JOIN product_product pp ON pp.id = mp.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN res_users ru ON ru.id = mp.user_id
    LEFT JOIN res_users wu ON wu.id = mp.write_uid
    WHERE p.cycle_hours > rc.cutoff_hours
"""


def _date_of(value):
    return value.date().isoformat() if value is not None else None


def _timestamp_of(value):
    return value.isoformat(sep=" ", timespec="minutes") if value is not None else None


def _breakdown(rows, key_fn, value_fn, label, dimension):
    totals = defaultdict(lambda: {"current": 0.0, "lines": 0})
    for row in rows:
        bucket = totals[key_fn(row)]
        bucket["current"] += value_fn(row)
        bucket["lines"] += 1
    return {
        "dimension": dimension, "label": label, "mode": "contribution", "baseline_month": None,
        "top_contributors": sorted(
            [{"name": name, "current": v["current"], "lines": v["lines"]} for name, v in totals.items()],
            key=lambda c: -c["current"],
        ),
    }


def _missed_rounds_finding(rows: list[dict]) -> dict | None:
    if not rows:
        return None

    lines = [{
        "id": r["id"], "document": r["name"], "batch_reference": r["origin"],
        "date": _date_of(r["date_start"]), "scheduled_at": _timestamp_of(r["date_start"]), "round": r["round"],
        "product": r["product"], "sku": r["sku"], "qty": float(r["qty"]),
        "responsible": r["responsible"],
        "unit_cost": float(r["unit_cost"]) if r["unit_cost"] is not None else None,
    } for r in rows]
    as_of_date = date.today().isoformat()
    total_missed_units = sum(l["qty"] for l in lines)
    products = {l["sku"] or l["product"] for l in lines}
    priced = [l for l in lines if l["unit_cost"]]
    financial_impact = round(sum(l["qty"] * l["unit_cost"] for l in priced), 2) if priced else None
    valued_units = sum(l["qty"] for l in priced)

    checks = [
        {"source": "round_completion", "verdict": "confirmed",
         "detail": f"{len(lines)} fresh-round production order(s) in the last {WINDOW_DAYS} days were scheduled "
                   f"more than {OVERDUE_BUFFER_DAYS} days ago and are still in draft -- well past the ~4.6-day "
                   f"p99 completion lag for rounds that do finish, so this isn't still-in-progress work."},
        {"source": "cancellation_check", "verdict": "no_evidence_found",
         "detail": "These are not being deliberately cancelled either -- only 232 mrp_production records are "
                   "ever cancelled, company-wide, all time. They are left in draft indefinitely."},
        {"source": "root_cause", "verdict": "unknown",
         "detail": "Not established: why confirmation isn't happening -- understaffing at the fresh warehouse, "
                   "a planning tool that over-proposes rounds, or a process gap in converting the draft order "
                   "into actual repack work."},
    ]

    impact = {
        "kind": "fresh_production_missed",
        "financial_impact_rs": financial_impact,
        "count": len(lines),
        "units_affected": total_missed_units,
        "distinct_products": len(products),
        "window_days": WINDOW_DAYS,
        "valued_units_pct": round(valued_units / total_missed_units * 100, 1) if total_missed_units else None,
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of_date,
        "detail": (
            f"{len(lines)} fresh-round production order(s), {total_missed_units:g} units, across "
            f"{len(products)} product(s) never confirmed in the last {WINDOW_DAYS} days"
            + (f" (~Rs. {financial_impact:,.0f} at catalog cost, {len(priced)} of {len(lines)} lines priced)."
               if financial_impact else ", value not established (no catalog cost on most affected products).")
        ),
    }

    if len(lines) >= 2000 or total_missed_units >= 100000:
        priority = "critical"
    elif len(lines) >= 500 or total_missed_units >= 25000:
        priority = "high"
    else:
        priority = "medium"

    data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    return {
        "id": f"fresh_production_missed-{as_of_date}",
        "metric_id": "fresh_production_missed",
        "domain": "inventory",
        "title": f"Fresh production never confirmed: {len(lines)} round(s), {total_missed_units:g} units, last {WINDOW_DAYS} days",
        "category": "critical",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": as_of_date,
        "confirm": {"period": as_of_date, "current": len(lines), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(lines, lambda l: l["product"], lambda l: l["qty"], "Product", "product"),
            _breakdown(lines, lambda l: l["round"], lambda l: l["qty"], "Round", "round"),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Fresh production missed",
        "fresh_production_missed_rows": lines,
    }


def _shortfall_finding(rows: list[dict]) -> dict | None:
    if not rows:
        return None

    lines = []
    for r in rows:
        planned = float(r["planned_qty"])
        produced = float(r["produced_qty"])
        lines.append({
            "id": r["id"], "document": r["name"], "batch_reference": r["origin"],
            "date": _date_of(r["date_start"]), "scheduled_at": _timestamp_of(r["date_start"]),
            "completed_at": _timestamp_of(r["date_finished"]), "round": r["round"],
            "product": r["product"], "sku": r["sku"],
            "planned_qty": planned, "produced_qty": produced,
            "shortfall_qty": round(planned - produced, 2),
            "pct_dev": round(abs(produced - planned) / planned * 100, 1) if planned else None,
            "responsible": r["responsible"], "completed_by": r["completed_by"],
            "unit_cost": float(r["unit_cost"]) if r["unit_cost"] is not None else None,
        })
    as_of_date = date.today().isoformat()
    under = [l for l in lines if l["shortfall_qty"] > 0]
    over = [l for l in lines if l["shortfall_qty"] < 0]
    total_shortfall_units = sum(l["shortfall_qty"] for l in under)
    products = {l["sku"] or l["product"] for l in lines}
    priced = [l for l in under if l["unit_cost"]]
    financial_impact = round(sum(l["shortfall_qty"] * l["unit_cost"] for l in priced), 2) if priced else None
    valued_units = sum(l["shortfall_qty"] for l in priced)

    checks = [
        {"source": "deviation_magnitude", "verdict": "confirmed",
         "detail": f"{len(lines)} completed fresh-round production(s) in the last {WINDOW_DAYS} days deviated "
                   f"at least {MIN_SHORTFALL_PCT}% and {MIN_SHORTFALL_UNITS} units from their planned quantity -- "
                   f"median deviation across all completed rounds is 0%, so this is the real tail, not rounding."},
        {"source": "direction", "verdict": "confirmed",
         "detail": f"{len(under)} under-produced vs {len(over)} over-produced -- skewed toward shortfall, "
                   "meaning outlets receive less fresh stock than the plan assumed."},
        {"source": "root_cause", "verdict": "unknown",
         "detail": "Not established: whether shortfall is raw-material availability, yield loss during repack, "
                   "or the plan itself being set too high for what can realistically be sourced same-day."},
    ]

    impact = {
        "kind": "fresh_production_shortfall",
        "financial_impact_rs": financial_impact,
        "count": len(under),
        "units_affected": abs(total_shortfall_units),
        "distinct_products": len(products),
        "window_days": WINDOW_DAYS,
        "valued_units_pct": round(valued_units / total_shortfall_units * 100, 1) if total_shortfall_units else None,
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of_date,
        "detail": (
            f"{len(under)} round(s) under-produced, {total_shortfall_units:g} units short, across "
            f"{len(products)} product(s) in the last {WINDOW_DAYS} days"
            + (f" (~Rs. {financial_impact:,.0f} at catalog cost, {len(priced)} of {len(under)} lines priced)."
               if financial_impact else ", value not established (no catalog cost on most affected products).")
        ),
    }

    if total_shortfall_units >= 5000 or (financial_impact or 0) >= 50000:
        priority = "high"
    else:
        priority = "medium"

    data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    return {
        "id": f"fresh_production_shortfall-{as_of_date}",
        "metric_id": "fresh_production_shortfall",
        "domain": "inventory",
        "title": f"Fresh production output off plan: {len(lines)} round(s), last {WINDOW_DAYS} days",
        "category": "warning",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": as_of_date,
        "confirm": {"period": as_of_date, "current": len(lines), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(lines, lambda l: l["product"], lambda l: abs(l["shortfall_qty"]), "Product", "product"),
            _breakdown(lines, lambda l: l["round"], lambda l: abs(l["shortfall_qty"]), "Round", "round"),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Fresh production shortfall",
        "fresh_production_shortfall_rows": lines,
    }


def _cycle_time_finding(rows: list[dict]) -> dict | None:
    if not rows:
        return None

    lines = []
    for r in rows:
        lines.append({
            "id": r["id"], "document": r["name"], "batch_reference": r["origin"],
            "date": _date_of(r["date_start"]), "scheduled_at": _timestamp_of(r["date_start"]),
            "completed_at": _timestamp_of(r["date_finished"]), "round": r["round"],
            "product": r["product"], "sku": r["sku"], "qty": float(r["qty"]),
            "cycle_hours": round(float(r["cycle_hours"]), 1),
            "round_cutoff_hours": round(float(r["cutoff_hours"]), 1),
            "responsible": r["responsible"], "completed_by": r["completed_by"],
        })
    as_of_date = date.today().isoformat()
    products = {l["sku"] or l["product"] for l in lines}
    by_round = {l["round"] for l in lines}
    cutoffs = ", ".join(f"{r}: {next(l['round_cutoff_hours'] for l in lines if l['round'] == r):g}h" for r in sorted(by_round))

    checks = [
        {"source": "demand_to_start_gap", "verdict": "confirmed",
         "detail": "mrp_production.date_start is set within seconds of the parent fresh_record's creation "
                   "(the demand submission) -- verified live, so the completion lag measured here already is "
                   "the demand-to-ready lead time, not just an internal scheduling window."},
        {"source": "per_round_baseline", "verdict": "confirmed",
         "detail": f"Flags only the slowest {round((1 - CYCLE_TIME_PERCENTILE) * 100)}% within each round's own "
                   f"distribution for this window, since morning and evening run on genuinely different tempos "
                   f"({cutoffs} cutoffs this window) -- not a shared flat threshold."},
        {"source": "root_cause", "verdict": "unknown",
         "detail": "Not established: whether slow completion is raw-material availability, staffing at the "
                   "fresh warehouse, or a backlog from the missed-rounds problem spilling into the ones that do "
                   "get confirmed."},
    ]

    impact = {
        "kind": "fresh_production_cycle_time",
        "financial_impact_rs": None,
        "count": len(lines),
        "units_affected": None,
        "distinct_products": len(products),
        "window_days": WINDOW_DAYS,
        "valued_units_pct": None,
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of_date,
        "detail": (
            f"{len(lines)} completed fresh-round production(s), across {len(products)} product(s), took "
            f"longer than the slowest {round((1 - CYCLE_TIME_PERCENTILE) * 100)}% of their own round type "
            f"in the last {WINDOW_DAYS} days ({cutoffs})."
        ),
    }

    priority = "high" if len(lines) >= 200 else "medium"

    data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    return {
        "id": f"fresh_production_cycle_time-{as_of_date}",
        "metric_id": "fresh_production_cycle_time",
        "domain": "inventory",
        "title": f"Fresh production running slow: {len(lines)} round(s), last {WINDOW_DAYS} days",
        "category": "warning",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": as_of_date,
        "confirm": {"period": as_of_date, "current": len(lines), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(lines, lambda l: l["product"], lambda l: l["cycle_hours"], "Product", "product"),
            _breakdown(lines, lambda l: l["round"], lambda l: l["cycle_hours"], "Round", "round"),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Fresh production cycle time",
        "fresh_production_cycle_time_rows": lines,
    }


def _missed_rounds_snapshot():
    rows = reporting.run_query(MISSED_ROUNDS_SQL)
    measurement.source_as_of(date.today().isoformat())
    return _missed_rounds_finding(rows)


def _shortfall_snapshot():
    rows = reporting.run_query(SHORTFALL_SQL)
    measurement.source_as_of(date.today().isoformat())
    return _shortfall_finding(rows)


def _cycle_time_snapshot():
    rows = reporting.run_query(CYCLE_TIME_SQL)
    measurement.source_as_of(date.today().isoformat())
    return _cycle_time_finding(rows)


def _resolve_weight_window(month: str | None) -> tuple[str, str, int | None]:
    """Returns (start_inclusive, end_exclusive, window_days) as ISO date
    strings -- a specific calendar month ("2026-09") resolves to that
    month's own boundaries (window_days None, since it isn't a rolling
    window); omitted, it resolves to the rolling last WINDOW_DAYS days,
    anchored to the latest scan actually in the table (not wall-clock
    today, since the reporting mirror can lag real time -- same convention
    as every other trailing-window query in this app)."""
    if month:
        year, mo = (int(x) for x in month.split("-"))
        start = date(year, mo, 1)
        end = date(year + 1, 1, 1) if mo == 12 else date(year, mo + 1, 1)
        return start.isoformat(), end.isoformat(), None
    rows = reporting.run_query("SELECT MAX(scan_time) AS as_of FROM fresh_scan_log")
    as_of = rows[0]["as_of"] if rows else None
    if as_of is None:
        today = date.today().isoformat()
        return today, today, WINDOW_DAYS
    as_of_date = as_of.date() if hasattr(as_of, "date") else date.fromisoformat(as_of)
    return (as_of_date - timedelta(days=WINDOW_DAYS)).isoformat(), (as_of_date + timedelta(days=1)).isoformat(), WINDOW_DAYS


def _average_weight_sql(start: str, end_exclusive: str) -> str:
    # start/end_exclusive are our own resolved ISO date strings (either a
    # calendar month's real boundaries or a DB-derived rolling window),
    # never user input -- safe to inline, same posture as this module's
    # other SQL constants embedding fixed internal values via f-string.
    return f"""
        SELECT pt.id AS product_tmpl_id, pt.name->>'en_US' AS product, pt.default_code AS sku,
               COUNT(*) AS scan_count,
               AVG(fsl.scanned_weight_g) AS avg_weight_g,
               MIN(fsl.scanned_weight_g) AS min_weight_g,
               MAX(fsl.scanned_weight_g) AS max_weight_g
        FROM fresh_scan_log fsl
        JOIN product_product pp ON pp.id = fsl.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE fsl.scanned_weight_g > 0
          AND fsl.scan_time >= '{start}' AND fsl.scan_time < '{end_exclusive}'
        GROUP BY pt.id, pt.name, pt.default_code
        ORDER BY scan_count DESC
    """

# The promised weight range ("Common Beans 200G - 250G") exists nowhere as a
# structured field -- product_template.fresh_min/max_weight_g is populated
# for 0 products, and fresh_scan_log's own min_weight_g/max_weight_g columns
# are always 0 across all 721K rows (verified live, 2026-11-05). This is the
# ONLY place the promised range exists, so it's parsed from the name text.
# Verified against the 112 distinct fresh-production product names live:
# 95 parse to a real two-sided range, a handful ("Green Grapes 250G") state
# a single weight with no range, and a few ("Rambutan (10 Fruits)", a
# packaging SKU) have no weight at all -- those last two groups get no band
# below (None), never an invented one.
_WEIGHT_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g)?\s*-\s*(\d+(?:\.\d+)?)\s*(kg|g)\b", re.IGNORECASE)


def _parse_weight_band_g(name: str) -> tuple[float, float] | None:
    if not name:
        return None
    m = _WEIGHT_RANGE_RE.search(name)
    if not m:
        return None
    v1, u1, v2, u2 = m.groups()
    u1 = u1 or u2
    g1 = float(v1) * (1000 if u1.lower() == "kg" else 1)
    g2 = float(v2) * (1000 if u2.lower() == "kg" else 1)
    return (min(g1, g2), max(g1, g2))


_SCHEMA_READY = False


def _ensure_weight_band_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS fresh_product_weight_bands (
            product_tmpl_id INTEGER PRIMARY KEY,
            sku TEXT,
            product TEXT NOT NULL,
            min_g REAL NOT NULL,
            max_g REAL NOT NULL,
            mid_g REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    _SCHEMA_READY = True


def sync_weight_bands():
    """Refreshes the fixed per-product weight-band reference (Operations
    request, 2026-11-05: a pre-defined band/target per product, not
    re-derived from text on every read). Still sourced from the product's
    own name -- that's the only place the promised range exists -- but
    parsed here once and stored, so the compliance calculation below reads
    a fixed table instead of re-running regex on every request. Upserts by
    product_tmpl_id, so a later name edit updates the stored band rather
    than leaving a stale one; a product with no parsable band is simply
    left out, never given an invented one."""
    _ensure_weight_band_schema()
    rows = reporting.run_query("""
        SELECT DISTINCT pt.id AS product_tmpl_id, pt.default_code AS sku, pt.name->>'en_US' AS product
        FROM fresh_scan_log fsl
        JOIN product_product pp ON pp.id = fsl.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
    """)
    now = datetime.utcnow().isoformat()
    for r in rows:
        band = _parse_weight_band_g(r["product"])
        if not band:
            continue
        min_g, max_g = band
        appdb.run_query(
            """
                INSERT INTO fresh_product_weight_bands
                    (product_tmpl_id, sku, product, min_g, max_g, mid_g, updated_at)
                VALUES (:id, :sku, :product, :min_g, :max_g, :mid_g, :now)
                ON CONFLICT (product_tmpl_id) DO UPDATE SET
                    sku = excluded.sku, product = excluded.product, min_g = excluded.min_g,
                    max_g = excluded.max_g, mid_g = excluded.mid_g, updated_at = excluded.updated_at
            """,
            {"id": r["product_tmpl_id"], "sku": r["sku"], "product": r["product"],
             "min_g": min_g, "max_g": max_g, "mid_g": round((min_g + max_g) / 2, 1), "now": now},
        )


def get_weight_bands() -> dict[int, dict]:
    _ensure_weight_band_schema()
    return {r["product_tmpl_id"]: r for r in appdb.run_query("SELECT * FROM fresh_product_weight_bands")}


# Correct/Below/Above is judged against a window centered on each
# product's own ACTUAL average scanned weight (avg_weight_g) -- not Target
# (the band midpoint) -- sized as a FRACTION of that product's OWN promised
# band half-width. Operations request, 2026-11-06: deliberately measures
# consistency around what the line is actually producing, not accuracy
# against the promised weight -- centering on Target would also show
# whether the average itself is drifting off-spec (e.g. Common Tomatoes
# averaging 177.8g against a 175g target); centering on Avg does not, by
# construction, since Avg is always the center of its own distribution.
# A flat +/-10% of Target was tried first and rejected before this: checked
# live against all 101 banded products, 2026-11-06, and it was stricter
# than the product's own band for only 48 of them -- the other 53 (anything
# whose own band is narrower than +/-10%, e.g. every 250-300g/500-600g
# product at exactly +/-9.1%) came out LOOSER than their own promised spec.
# Scaling the window width by a fixed fraction of each product's own band
# half-width avoids that -- it's narrower than the original band for every
# product, by the same relative amount.
CORRECT_WINDOW_FRACTION_OF_BAND = 0.75


def _band_compliance_sql(windows: list[tuple[int, float, float]], start: str, end_exclusive: str) -> str:
    # Values here are our own computed (int id, float, float) tuples, never
    # user input, so inlining them as a literal VALUES list is safe and
    # avoids array-parameter binding -- same posture as this module's other
    # SQL constants embedding fixed internal constants via f-string.
    values_sql = ", ".join(f"({pid}, {lo}, {hi})" for pid, lo, hi in windows)
    return f"""
        WITH windowtbl(product_tmpl_id, lo_g, hi_g) AS (VALUES {values_sql})
        SELECT pt.id AS product_tmpl_id,
               COUNT(*) FILTER (WHERE fsl.scanned_weight_g < w.lo_g) AS below,
               COUNT(*) FILTER (WHERE fsl.scanned_weight_g > w.hi_g) AS above,
               COUNT(*) FILTER (WHERE fsl.scanned_weight_g BETWEEN w.lo_g AND w.hi_g) AS correct
        FROM fresh_scan_log fsl
        JOIN product_product pp ON pp.id = fsl.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN windowtbl w ON w.product_tmpl_id = pt.id
        WHERE fsl.scanned_weight_g > 0
          AND fsl.scan_time >= '{start}' AND fsl.scan_time < '{end_exclusive}'
        GROUP BY pt.id
    """


def get_average_weight_by_product(month: str | None = None) -> dict:
    """Average scanned pack weight per product, straight from the scale/
    barcode scan log (fresh_scan_log) -- requested by Operations, 2026-11-05.
    This is the real per-pack weight the production team's scale captures at
    scan time, not mrp_production's unit count: product_template.weight is
    unpopulated for virtually every product (835 of 52,024 production rows
    have a nonzero value), and mrp_production itself tracks quantity in
    "Units", never kg -- verified live the same day.

    month: an explicit calendar month ("2026-09") to report on instead of
    the default rolling last WINDOW_DAYS days -- see
    _resolve_weight_window's docstring. Added 2026-11-06 on request to
    check a specific past month rather than only "the last 30 days."

    Also classifies each product's scans as correct_pct/below_pct/above_pct
    against a window CENTERED ON avg_weight_g (this product's own actual
    average, not its Target/band midpoint), sized at
    CORRECT_WINDOW_FRACTION_OF_BAND of that same product's own band
    half-width -- see CORRECT_WINDOW_FRACTION_OF_BAND's comment for why
    Avg, not Target, and why a fraction of the band rather than a flat
    percentage. band_g/band_mid_g (Target, still reported for reference)
    are read from the fresh_product_weight_bands table (see
    sync_weight_bands's docstring), not re-parsed from the name on every
    request. A product with no stored band gets `band_g: None`/
    `band_mid_g: None` and no percentages, never an invented 0%. The
    compliance count is pushed into SQL as a second query (not pulled into
    Python row-by-row) because a raw per-scan pull of this window measured
    ~6s live, 2026-11-05 -- too slow for an on-demand page load; the
    aggregated query runs in under a second.

    A live read, not a finding -- there's no threshold or anomaly here, just
    the plain average/compliance rate the scan log already has full history
    for."""
    start, end_exclusive, window_days = _resolve_weight_window(month)
    rows = reporting.run_query(_average_weight_sql(start, end_exclusive))
    band_by_id = get_weight_bands()
    products = [
        {
            "product_tmpl_id": r["product_tmpl_id"],
            "product": r["product"],
            "sku": r["sku"],
            "scan_count": r["scan_count"],
            "avg_weight_g": round(float(r["avg_weight_g"]), 1),
            "min_weight_g": round(float(r["min_weight_g"]), 1),
            "max_weight_g": round(float(r["max_weight_g"]), 1),
            "band_g": (band_by_id[r["product_tmpl_id"]]["min_g"], band_by_id[r["product_tmpl_id"]]["max_g"])
            if r["product_tmpl_id"] in band_by_id else None,
            "band_mid_g": band_by_id[r["product_tmpl_id"]]["mid_g"] if r["product_tmpl_id"] in band_by_id else None,
            "correct_pct": None,
            "below_pct": None,
            "above_pct": None,
        }
        for r in rows
    ]

    windows = []
    for p in products:
        if p["band_g"] is None:
            continue
        min_g, max_g = p["band_g"]
        half_width = (max_g - min_g) / 2 * CORRECT_WINDOW_FRACTION_OF_BAND
        center = p["avg_weight_g"]  # actual average, not Target -- see CORRECT_WINDOW_FRACTION_OF_BAND's comment
        windows.append((p["product_tmpl_id"], round(center - half_width, 1), round(center + half_width, 1)))
    if windows:
        compliance = {
            r["product_tmpl_id"]: r
            for r in reporting.run_query(_band_compliance_sql(windows, start, end_exclusive))
        }
        for p in products:
            c = compliance.get(p["product_tmpl_id"])
            if not c:
                continue
            total = c["below"] + c["above"] + c["correct"]
            if total:
                p["below_pct"] = round(c["below"] / total * 100, 1)
                p["above_pct"] = round(c["above"] / total * 100, 1)
                p["correct_pct"] = round(c["correct"] / total * 100, 1)

    for p in products:
        del p["product_tmpl_id"]
    return {
        "window_days": window_days,
        "month": month,
        "period_start": start,
        "period_end_exclusive": end_exclusive,
        "products": products,
    }


def build_fresh_production_findings() -> measurement.FindingBatch:
    # Each wrapped in run_snapshot (not a bare list) so a round/product that
    # stops being flagged still gets a "measured_healthy" measurement this
    # cycle -- without it, the case engine's active-case sweep (case_engine.
    # sync_cases) never sees ANY measurement for these metric_ids once the
    # finding goes quiet, so the case can never advance its verification
    # window or even show it was re-checked (see measurement.healthy()).
    return measurement.combine([
        measurement.run_snapshot("fresh_production_missed", _missed_rounds_snapshot),
        measurement.run_snapshot("fresh_production_shortfall", _shortfall_snapshot),
        measurement.run_snapshot("fresh_production_cycle_time", _cycle_time_snapshot),
    ])

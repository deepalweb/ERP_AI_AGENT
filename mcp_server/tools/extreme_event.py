"""Single-event magnitude detector for warehouse-to-outlet Missing Item lines.

transfer_discrepancy.py's recurrence rule (MIN_DAYS_APPEARED=2) deliberately
ignores one-off events, so a single catastrophic line -- e.g. 1,000 units of
one SKU on one picking -- has no detector of its own. This is that detector.

Thresholds are data-derived, not picked blind. Over the 6-month history
(2,138 Missing Item lines, 4 outlets), a line's quantity relative to its own
product's median line quantity has p99 = 11x; 10x is that cut, rounded. The
20-unit floor sits near the pooled p90 (19 units) so single-unit noise on
small-pack SKUs can't trip it. Both are revisit-after-pilot numbers, not
settled ones.

One case covers the whole issue, not one case per line: the flagged lines
roll up into a single finding with outlet and product breakdowns and a row
table, so management reviews the pattern once.

Only Missing Item is covered. Flagged lines are labeled "verify the receiving
record", never "loss": the data shows a quantity gap on one line, not whether
it was a real shortage or a counting/unit-entry error.
"""

from collections import defaultdict
from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools import evidence_engine, impact_engine, measurement

RATIO_TO_PRODUCT_MEDIAN = 10
MIN_UNITS = 20
MIN_PRODUCT_LINES = 5
THIN_HISTORY_UNITS = 60
WINDOW_DAYS = 30
QTY_TOLERANCE = 0.001
ISSUE_METRIC_ID = "extreme_event_missing_item"

EXTREME_EVENT_SQL = """
    WITH scoped AS (
        SELECT sms.id AS shortage_id, sms.product_id, rs.id AS store_id, rs.name AS outlet,
               sp.id AS picking_id, sp.name AS picking, sp.state AS picking_state, sp.origin AS origin,
               sl.complete_name AS source_location, dl.complete_name AS dest_location,
               sp.scheduled_date AS scheduled_at, sp.date_done AS done_at, sp.create_date AS picking_created_at,
               cu.login AS created_by, wu.login AS last_edited_by,
               ru.login AS recorded_by, sms.create_date AS created_at,
               sms.quantity AS qty, sms.expected_qty AS expected_qty, sms.received_qty AS received_qty,
               pt.name->>'en_US' AS product_name, pt.default_code AS sku,
               pt.celeste_cost_price_ex_vat AS unit_cost
        FROM stock_move_shortage sms
        JOIN stock_picking sp ON sp.id = sms.picking_id
        JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
        JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
        JOIN stock_location sl ON sl.id = sp.location_id
        LEFT JOIN stock_location dl ON dl.id = sp.location_dest_id
        JOIN shortage_reason sr ON sr.id = sms.reason_id
        JOIN product_product pp ON pp.id = sms.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN res_users cu ON cu.id = sp.create_uid
        LEFT JOIN res_users wu ON wu.id = sp.write_uid
        LEFT JOIN res_users ru ON ru.id = sms.create_uid
        WHERE spt.code = 'incoming' AND starts_with(sl.complete_name, 'Transits/')
          AND rs.id = ANY(:stores) AND sr.name = 'Missing Item'
          AND sp.state = 'done'
    ),
    baseline AS (
        SELECT product_id, COUNT(*) AS n_lines,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY qty) AS median_qty
        FROM scoped GROUP BY product_id
    )
    SELECT s.*, b.n_lines, b.median_qty
    FROM scoped s JOIN baseline b USING (product_id)
    WHERE s.created_at >= (SELECT MAX(created_at) FROM scoped) - make_interval(days => :window_days)
      AND s.qty >= :min_units
      AND (
            (b.n_lines >= :min_lines AND s.qty >= :ratio * b.median_qty)
         OR (b.n_lines < :min_lines AND s.qty >= :thin_units)
      )
    ORDER BY s.qty DESC
"""


def _iso(value):
    return value.isoformat() if value is not None else None


def _line_row(r: dict) -> dict:
    qty = float(r["qty"])
    expected = float(r["expected_qty"] or 0)
    received = float(r["received_qty"] or 0)
    median = float(r["median_qty"])
    has_history = r["n_lines"] >= MIN_PRODUCT_LINES
    flags = []
    if abs(qty - (expected - received)) > QTY_TOLERANCE:
        flags.append("Quantity does not match expected minus received")
    return {
        "id": r["shortage_id"],
        "date": r["created_at"].date().isoformat(),
        "outlet": r["outlet"],
        "product": r["product_name"],
        "sku": r["sku"],
        "receipt": r["picking"],
        "receipt_status": r["picking_state"],
        "expected_qty": expected,
        "received_qty": received,
        "recorded_qty": qty,
        "vs_usual": (f"{qty / median:.0f}x usual ({median:g})" if has_history and median else "No usual baseline"),
        "recorded_by": r["recorded_by"],
        "receipt_created_by": r["created_by"],
        "source_location": r["source_location"],
        "destination_location": r["dest_location"],
        "scheduled_at": _iso(r["scheduled_at"]),
        "done_at": _iso(r["done_at"]),
        "unit_cost": float(r["unit_cost"]) if r["unit_cost"] is not None else None,
        "flags": flags,
    }


def _breakdown(lines, key_fn, label, dimension):
    totals = defaultdict(lambda: {"current": 0.0, "lines": 0})
    for line in lines:
        bucket = totals[key_fn(line)]
        bucket["current"] += line["recorded_qty"]
        bucket["lines"] += 1
    return {
        "dimension": dimension, "label": label, "mode": "contribution", "baseline_month": None,
        "top_contributors": sorted(
            [{"name": name, "current": v["current"], "lines": v["lines"]} for name, v in totals.items()],
            key=lambda c: -c["current"],
        ),
    }


def _extreme_event_finding():
    rows = reporting.run_query(EXTREME_EVENT_SQL, {
        "stores": list(CELESTE_STORE_IDS), "window_days": WINDOW_DAYS,
        "min_lines": MIN_PRODUCT_LINES, "min_units": MIN_UNITS, "ratio": RATIO_TO_PRODUCT_MEDIAN,
        "thin_units": THIN_HISTORY_UNITS,
    })
    if not rows:
        # Zero rows is the clean/healthy result here (the SQL already filters
        # to the threshold), not "can't tell" -- the query ran, live, right
        # now, so today genuinely is this measurement's freshness.
        measurement.source_as_of(date.today().isoformat())
        return None

    lines = [_line_row(r) for r in rows]
    as_of_date = max(line["date"] for line in lines)
    measurement.source_as_of(as_of_date)
    total_units = sum(line["recorded_qty"] for line in lines)
    outlets = {line["outlet"] for line in lines}
    products = {line["sku"] or line["product"] for line in lines}
    priced = [line for line in lines if line["unit_cost"]]
    financial_impact = round(sum(line["recorded_qty"] * line["unit_cost"] for line in priced), 2) if priced else None
    mismatched = sum(1 for line in lines if "Quantity does not match expected minus received" in line["flags"])

    checks = [
        {"source": "extreme_magnitude", "verdict": "confirmed",
         "detail": f"{len(lines)} transfer lines in the last {WINDOW_DAYS} days recorded at least "
                   f"{MIN_UNITS} units missing and far above the product's usual line size, across "
                   f"{len(outlets)} outlet(s). Each is a single-event outlier, not a recurring pattern."},
        {"source": "record_verification", "verdict": "unknown",
         "detail": "Not established: whether these quantities are real shortages or receiving-record errors "
                   "(count or unit entry). Check each receipt before treating it as a loss."},
        {"source": "outbound_leg", "verdict": "unknown",
         "detail": "Not established: who sent this stock out of the source warehouse. The outbound leg of these "
                   "transfers is not linked to the receipts in Odoo, so senders cannot be named from this data."},
    ]
    if mismatched:
        checks.append({"source": "record_consistency", "verdict": "unknown",
                       "detail": f"{mismatched} lines record a quantity that does not equal expected minus received. "
                                 "Check which figure is correct."})

    impact = {
        "kind": "transfer_discrepancy",
        "financial_impact_rs": financial_impact,
        "count": len(lines),
        "units_affected": total_units,
        "distinct_products": len(products),
        "window_days": WINDOW_DAYS,
        "line_rate": None,
        "unit_rate": None,
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of_date,
        "detail": (
            f"{len(lines)} transfer lines, {total_units:g} units, across {len(outlets)} outlet(s) in the last "
            f"{WINDOW_DAYS} days"
            + (f" (~Rs. {financial_impact:,.0f} at catalog cost, {len(priced)} of {len(lines)} lines priced)."
               if financial_impact else ", value not established (no catalog cost).")
        ),
    }
    # Priority scales with the size of the worst single line in the batch, not a fixed
    # "medium": a 1,000-unit single event is a bigger loss than most recurring patterns,
    # and a flat priority hid that. Cuts are multiples of MIN_UNITS (the detector's own
    # floor), so a line ~10x or ~5x the minimum that trips this detector at all is what
    # earns the next priority step up; the Rs cuts mirror that same 2x spread. Revisit
    # after a pilot, like the detector's own thresholds above.
    largest_line_qty = max((line["recorded_qty"] for line in lines), default=0)
    if largest_line_qty >= 10 * MIN_UNITS or (financial_impact or 0) >= 50000:
        priority = "critical"
    elif largest_line_qty >= 5 * MIN_UNITS or (financial_impact or 0) >= 15000:
        priority = "high"
    else:
        priority = "medium"
    data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    finding = {
        "id": f"{ISSUE_METRIC_ID}-{as_of_date}",
        "metric_id": ISSUE_METRIC_ID,
        "domain": "inventory",
        "title": f"Unusually large Missing Item transfers: {len(lines)} lines across {len(outlets)} outlet(s), last {WINDOW_DAYS} days",
        "category": "critical",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": date.today().isoformat(),
        "confirm": {"period": as_of_date, "current": len(lines), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(lines, lambda line: line["outlet"], "Outlet", "outlet"),
            _breakdown(lines, lambda line: line["product"], "Product", "product"),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Missing Item",
        "extreme_rows": lines,
    }
    return finding


def build_extreme_event_findings() -> measurement.FindingBatch:
    return measurement.combine([measurement.run_snapshot(ISSUE_METRIC_ID, _extreme_event_finding)])

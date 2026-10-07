"""Dispatch-side magnitude detector: outbound transfer lines sent far above their plan.

transfer_discrepancy.py and extreme_event.py both cover the receiving side -- what
arrived short, extra, or damaged, compared to what was received. Neither covers the
other end of the same transfer: a line the warehouse sent well above what the transfer
note planned. That gap shows up as "Sent above plan" on a transfer finding's company-wide
accountability panel, but as data only -- it never becomes a case, so nobody is alerted.
This is that detector.

Thresholds are data-derived, not picked blind, the same way extreme_event.py's are.
Over the 6-month history (7,827 outbound lines sent above their plan, Celeste outlets),
a combined cut of >=50 units over plan AND >=5x the planned quantity keeps the top ~0.3%
(25 of 7,827 lines) -- genuinely rare, not routine rounding (the median over-plan line is
+3 units, 1.5x plan). Revisit after a pilot, like extreme_event's own thresholds.

"Sent without any plan line at all" was checked too (zero occurrences in 6 months across
the same lines) -- common enough to decide it isn't worth a detector of its own; a rule
that can never fire isn't a detector, so it stays a plain count on the accountability panel.

One case covers the whole issue, not one case per line, same as extreme_event.py: the
flagged lines roll up into a single finding with outlet and product breakdowns and a row
table, so management reviews the pattern once.

Flagged lines are labeled "verify the dispatch", never "theft" or "waste": the data shows
a quantity sent well above plan, not whether it is a real top-up, a picking error, or a
unit mismatch on the transfer note (e.g. cases entered as pieces).
"""

from collections import defaultdict
from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools import evidence_engine, impact_engine, measurement
from mcp_server.tools.transfer_discrepancy import WINDOW_DAYS

MIN_OVER_UNITS = 50   # 6-month p99.5 of (sent - plan) on over-plan lines
MIN_RATIO = 5         # 6-month p95-p99 of sent/plan on over-plan lines; combined with the floor, top ~0.3%
ISSUE_METRIC_ID = "dispatch_sent_above_plan"

DISPATCH_OVERAGE_SQL = f"""
    WITH rcv AS (
        SELECT sp.origin, sp.name AS receipt, sp.date_done AS receipt_done_at, rs.id AS store_id, rs.name AS outlet
        FROM stock_picking sp
        JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
        JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
        JOIN stock_location sl ON sl.id = sp.location_id
        CROSS JOIN (SELECT MAX(create_date) AS as_of FROM stock_move_shortage) b
        WHERE spt.code = 'incoming' AND sl.complete_name LIKE 'Transits/%'
          AND sp.state = 'done' AND rs.id IN {CELESTE_STORE_IDS} AND sp.origin IS NOT NULL
          AND sp.create_date >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
    )
    SELECT m.id AS move_id, op.name AS transfer_out, op.date_done AS dispatched_at,
           rcv.receipt, rcv.receipt_done_at, rcv.outlet, rcv.store_id,
           pt.name->>'en_US' AS product, pt.default_code AS sku,
           m.product_uom_qty AS plan_qty, m.quantity AS sent_qty,
           pt.celeste_cost_price_ex_vat AS unit_cost,
           pcu.login AS planned_by, out_done.login AS dispatched_by
    FROM rcv
    JOIN stock_picking op ON op.name = rcv.origin
    JOIN stock_move m ON m.picking_id = op.id
    JOIN product_product pp ON pp.id = m.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN res_users pcu ON pcu.id = op.create_uid
    LEFT JOIN LATERAL (
        SELECT u.login FROM mail_message mm
        JOIN mail_tracking_value tv ON tv.mail_message_id = mm.id
        JOIN ir_model_fields f ON f.id = tv.field_id AND f.name = 'state' AND f.model = 'stock.picking'
        LEFT JOIN res_partner p ON p.id = mm.author_id
        LEFT JOIN res_users u ON u.partner_id = p.id
        WHERE mm.model = 'stock.picking' AND mm.res_id = op.id AND tv.new_value_char = 'Done'
        ORDER BY mm.date DESC LIMIT 1) out_done ON TRUE
    WHERE m.product_uom_qty > 0 AND m.quantity > m.product_uom_qty
      AND (m.quantity - m.product_uom_qty) >= {MIN_OVER_UNITS}
      AND m.quantity >= {MIN_RATIO} * m.product_uom_qty
"""


def _date_of(value):
    return value.date().isoformat() if value is not None else None


def _line_row(r: dict) -> dict:
    plan = float(r["plan_qty"])
    sent = float(r["sent_qty"])
    return {
        "id": r["move_id"],
        "date": _date_of(r["dispatched_at"]) or _date_of(r["receipt_done_at"]),
        "outlet": r["outlet"],
        "product": r["product"],
        "sku": r["sku"],
        "transfer_out": r["transfer_out"],
        "receipt": r["receipt"],
        "plan_qty": plan,
        "sent_qty": sent,
        "over_qty": round(sent - plan),
        "ratio": round(sent / plan, 1) if plan else None,
        "planned_by": r["planned_by"],
        "dispatched_by": r["dispatched_by"],
        "unit_cost": float(r["unit_cost"]) if r["unit_cost"] is not None else None,
    }


def _breakdown(lines, key_fn, label, dimension):
    totals = defaultdict(lambda: {"current": 0.0, "lines": 0})
    for line in lines:
        bucket = totals[key_fn(line)]
        bucket["current"] += line["over_qty"]
        bucket["lines"] += 1
    return {
        "dimension": dimension, "label": label, "mode": "contribution", "baseline_month": None,
        "top_contributors": sorted(
            [{"name": name, "current": v["current"], "lines": v["lines"]} for name, v in totals.items()],
            key=lambda c: -c["current"],
        ),
    }


def build_dispatch_overage_findings() -> list[dict]:
    rows = reporting.run_query(DISPATCH_OVERAGE_SQL)
    if not rows:
        return []

    lines = [_line_row(r) for r in rows]
    as_of_date = max((l["date"] for l in lines if l["date"]), default=date.today().isoformat())
    total_over = sum(l["over_qty"] for l in lines)
    outlets = {l["outlet"] for l in lines}
    products = {l["sku"] or l["product"] for l in lines}
    priced = [l for l in lines if l["unit_cost"]]
    financial_impact = round(sum(l["over_qty"] * l["unit_cost"] for l in priced), 2) if priced else None
    valued_units = sum(l["over_qty"] for l in priced)

    checks = [
        {"source": "dispatch_magnitude", "verdict": "confirmed",
         "detail": f"{len(lines)} outbound line(s) in the last {WINDOW_DAYS} days were sent at least {MIN_RATIO}x "
                   f"their planned quantity and at least {MIN_OVER_UNITS} units over plan -- the top ~0.3% of all "
                   f"outbound lines by this measure over the last 6 months, not routine rounding."},
        {"source": "record_verification", "verdict": "unknown",
         "detail": "Not established: whether the extra quantity is a real top-up, a picking error, or a unit "
                   "mismatch on the transfer note (e.g. cases entered as pieces). Check the outbound note before "
                   "treating it as intentional or as loss."},
        {"source": "demand_basis", "verdict": "unknown",
         "detail": "Not established: whether the planned quantity itself was right for the outlet's actual demand. "
                   "This flags a gap between plan and what was sent, not a judgement on the plan."},
    ]

    impact = {
        "kind": "dispatch_over_plan",
        "financial_impact_rs": financial_impact,
        "count": len(lines),
        "units_affected": total_over,
        "distinct_products": len(products),
        "valued_units_pct": round(valued_units / total_over * 100, 1) if total_over else None,
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of_date,
        "detail": (
            f"{len(lines)} outbound line(s), {total_over:g} units over plan, across {len(outlets)} outlet(s) "
            f"in the last {WINDOW_DAYS} days"
            + (f" (~Rs. {financial_impact:,.0f} at catalog cost, {len(priced)} of {len(lines)} lines priced)."
               if financial_impact else ", value not established (no catalog cost).")
        ),
    }

    # Same style as extreme_event.py's magnitude-based priority: cuts are multiples of
    # the detector's own floor (MIN_OVER_UNITS), so a line ~10x/~2x the floor is what
    # earns the next priority step. Revisit after a pilot.
    largest_over = max((l["over_qty"] for l in lines), default=0)
    if largest_over >= 10 * MIN_OVER_UNITS or (financial_impact or 0) >= 50000:
        priority = "critical"
    elif largest_over >= 2 * MIN_OVER_UNITS or (financial_impact or 0) >= 15000:
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
        "title": f"Warehouse sent far above plan: {len(lines)} line(s) across {len(outlets)} outlet(s), last {WINDOW_DAYS} days",
        "category": "critical",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": date.today().isoformat(),
        "confirm": {"period": as_of_date, "current": len(lines), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(lines, lambda l: l["outlet"], "Outlet", "outlet"),
            _breakdown(lines, lambda l: l["product"], "Product", "product"),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Sent above plan",
        "dispatch_rows": lines,
    }
    finding["measurement"] = measurement.observation(
        ISSUE_METRIC_ID, "snapshot", "measured_actionable",
        scope=ISSUE_METRIC_ID, period=as_of_date, freshness="fresh", value=len(lines),
    )
    return [finding]

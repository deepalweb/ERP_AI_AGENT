"""Warehouse-to-outlet transfer discrepancies, one case per reason.

A discrepancy is a shortage line on an incoming receipt whose source is a
Transits/ location, at one of the Celeste outlets. Each reason (Missing Item,
Extra Quantity, ...) is one issue: the case groups the affected lines by
outlet and by product, and shows each outlet's rate against the volume it
received over the window.

Outlet attribution joins res_store on rs.warehouse_id = spt.warehouse_id. That
is not unique per outlet: many partner-brand res_store rows share an outlet's
warehouse_id. It is safe here only because no two Celeste outlets share a
warehouse_id, and every query filters on the specific rs.id. Keep it that way.
"""

from collections import defaultdict
from datetime import UTC, date, datetime

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools import evidence_engine, impact_engine, measurement

WINDOW_DAYS = 14
MIN_INCIDENTS = 3       # Ethul Kotte's real "Extra Quantity" complaint was 4 incidents over 3 days in 5 real days
MIN_DAYS_APPEARED = 2   # never flag a single-day burst as recurring

_SCOPE_JOINS = f"""
        FROM stock_move_shortage sms
        JOIN stock_picking sp ON sp.id = sms.picking_id
        JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
        JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
        JOIN stock_location sl ON sl.id = sp.location_id
        JOIN shortage_reason sr ON sr.id = sms.reason_id
        JOIN product_product pp ON pp.id = sms.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_picking op ON op.name = sp.origin
        LEFT JOIN res_users rcu ON rcu.id = sp.create_uid
        LEFT JOIN res_users rwu ON rwu.id = sp.write_uid
        LEFT JOIN res_users scu ON scu.id = sms.create_uid
        LEFT JOIN res_users ocu ON ocu.id = op.create_uid
        LEFT JOIN res_users owu ON owu.id = op.write_uid
        LEFT JOIN LATERAL (
            SELECT SUM(m.product_uom_qty) AS plan_qty, SUM(m.quantity) AS sent_qty
            FROM stock_move m WHERE m.picking_id = op.id AND m.product_id = sms.product_id
        ) out_m ON TRUE
        LEFT JOIN LATERAL (
            SELECT SUM(q.quantity) AS on_hand_qty, SUM(q.reserved_quantity) AS reserved_qty
            FROM stock_quant q WHERE q.location_id = sl.id AND q.product_id = sms.product_id
        ) git ON sl.usage = 'transit'
        LEFT JOIN LATERAL (
            SELECT u.login FROM mail_message m
            JOIN mail_tracking_value tv ON tv.mail_message_id = m.id
            JOIN ir_model_fields f ON f.id = tv.field_id AND f.name = 'state' AND f.model = 'stock.picking'
            LEFT JOIN res_partner p ON p.id = m.author_id
            LEFT JOIN res_users u ON u.partner_id = p.id
            WHERE m.model = 'stock.picking' AND m.res_id = op.id AND tv.new_value_char = 'Done'
            ORDER BY m.date DESC LIMIT 1) out_done ON TRUE
        LEFT JOIN LATERAL (
            SELECT u.login FROM mail_message m
            JOIN mail_tracking_value tv ON tv.mail_message_id = m.id
            JOIN ir_model_fields f ON f.id = tv.field_id AND f.name = 'state' AND f.model = 'stock.picking'
            LEFT JOIN res_partner p ON p.id = m.author_id
            LEFT JOIN res_users u ON u.partner_id = p.id
            WHERE m.model = 'stock.picking' AND m.res_id = sp.id AND tv.new_value_char = 'Done'
            ORDER BY m.date DESC LIMIT 1) in_done ON TRUE
        CROSS JOIN (SELECT MAX(create_date) AS as_of FROM stock_move_shortage) b
        WHERE spt.code = 'incoming' AND sl.complete_name LIKE 'Transits/%'
          AND rs.id IN {CELESTE_STORE_IDS}
          AND sms.create_date >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
"""

TRANSFER_LINES_SQL = f"""
    SELECT rs.id AS store_id, rs.name AS outlet, sr.name AS reason, sr.reason_type AS reason_type,
           pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           sms.product_id AS product_id, sms.stock_move_id AS receipt_move_id, sp.id AS receipt_id,
           sl.id AS source_location_id, sl.complete_name AS source_location, sl.usage AS source_location_usage,
           git.on_hand_qty AS git_on_hand_qty, git.reserved_qty AS git_reserved_qty,
           sms.id AS shortage_id, sms.quantity AS qty, sms.expected_qty AS expected_qty, sms.received_qty AS received_qty,
           sp.date_done AS receipt_done_at, sp.create_date AS receipt_created_at,
           rcu.login AS receipt_created_by, rwu.login AS receipt_last_edited_by, scu.login AS recorded_by,
           out_done.login AS out_completed_by, in_done.login AS receipt_completed_by,
           out_m.plan_qty AS plan_qty, out_m.sent_qty AS sent_qty,
           op.id AS transfer_out_id, op.name AS transfer_out, op.state AS out_state, op.date_done AS out_done_at,
           ocu.login AS out_created_by, owu.login AS out_last_edited_by,
           sms.create_date AS recorded_at,
           pt.celeste_cost_price_ex_vat AS unit_cost, sp.name AS receipt, sp.state AS receipt_state,
           (sms.uom_id = pt.uom_id) AS uom_matches_base
    {_SCOPE_JOINS}
"""

OBSERVED_DAYS_SQL = f"""
    SELECT COUNT(DISTINCT sp.create_date::date) AS days_observed
    FROM stock_picking sp
    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
    JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
    JOIN stock_location sl ON sl.id = sp.location_id
    CROSS JOIN (SELECT MAX(create_date) AS as_of FROM stock_move_shortage) b
    WHERE spt.code = 'incoming' AND sl.complete_name LIKE 'Transits/%'
      AND rs.id IN {CELESTE_STORE_IDS}
      AND sp.create_date >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
"""

# Denominator for each outlet's rate: units and lines it actually received.
# Only done moves, because shortage lines only exist for received pickings.
OUTLET_VOLUME_SQL = f"""
    SELECT rs.id AS store_id, COUNT(*) AS total_lines, SUM(sm.product_qty) AS total_qty,
           COUNT(*) FILTER (WHERE sm.product_uom IS DISTINCT FROM pt.uom_id) AS non_base_uom_moves
    FROM stock_move sm
    JOIN stock_picking sp ON sp.id = sm.picking_id
    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
    JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
    JOIN stock_location sl ON sl.id = sp.location_id
    JOIN product_product pp ON pp.id = sm.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    CROSS JOIN (SELECT MAX(create_date) AS as_of FROM stock_move_shortage) b
    WHERE spt.code = 'incoming' AND sl.complete_name LIKE 'Transits/%'
      AND rs.id IN {CELESTE_STORE_IDS} AND sm.state = 'done'
      AND sm.create_date >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
    GROUP BY rs.id
"""

_REASON_PHRASING = {
    "Extra Quantity": "Extra items recorded at outlet receiving",
    "Missing Item": "Missing items recorded at outlet receiving",
    "Product Damage": "Damaged items recorded on transfers",
}

_PROCESS_ORIGIN_UNKNOWN = {
    "source": "process_origin", "verdict": "unknown",
    "detail": "Not established: whether the discrepancy originates in warehouse picking, loading, transport, "
    "or outlet receiving/counting -- this data shows a documented-vs-received gap, not which stage produced it.",
}
_SOURCE_WAREHOUSE_UNKNOWN = {
    "source": "source_warehouse", "verdict": "unknown",
    "detail": "Not established: which source warehouse or route contributes most -- transfer routing in this "
    "data (transit location names) doesn't reliably resolve to one warehouse to attribute this to.",
}

# Company-wide, computed once per scan: a unit-of-measure mismatch is a
# plausible cause of weight/count gaps, so it gets a real check.
_UOM_CONSISTENCY_SQL = """
    SELECT COUNT(*) AS inconsistent_products FROM (
        SELECT product_id FROM stock_move_shortage GROUP BY product_id HAVING COUNT(DISTINCT uom_id) > 1
    ) t
"""


def _uom_consistency_check() -> dict:
    rows = reporting.run_query(_UOM_CONSISTENCY_SQL)
    inconsistent = rows[0]["inconsistent_products"] if rows else 0
    if inconsistent:
        return {"source": "uom_consistency", "verdict": "possible_contributor",
                "detail": f"{inconsistent} product(s) were recorded under more than one unit of measure across "
                "shortage lines -- a possible contributor for those specific products, not ruled out."}
    return {"source": "uom_consistency", "verdict": "no_evidence_found",
            "detail": "Checked: no product in this data was recorded under more than one unit of measure across "
            "shortage lines -- a unit-of-measure mismatch is not supported as a cause here."}


def _reason_key(reason: str) -> str:
    return reason.strip().lower().replace(" ", "_")


def _priority_for(days_appeared: int) -> str:
    if days_appeared >= 8:
        return "critical"
    if days_appeared >= 5:
        return "high"
    return "medium"


def _recurrence_check(days_appeared: int, days_observed: int) -> dict:
    ratio_text = f"{days_appeared} of the {days_observed} days in the window that had a warehouse transfer to a Celeste outlet"
    if days_appeared >= 5:
        return {"source": "recurrence", "verdict": "confirmed",
                "detail": f"Recorded on {ratio_text} -- a sustained pattern, not an isolated event."}
    return {"source": "recurrence", "verdict": "possible_contributor",
            "detail": f"Recorded on {ratio_text} -- recurring, but not yet on a majority of days."}


def _rate(part, whole):
    return round(part / whole * 100, 2) if whole else None


def _unit_basis_ok(lines, volume) -> bool:
    return all(l["uom_matches_base"] for l in lines) and not volume.get("non_base_uom_moves", 0)


def _sum_or_none(values):
    present = [float(v) for v in values if v is not None]
    return sum(present) if present else None


def _unique_lines(lines, key_fn):
    """Count each underlying movement once while retaining every reason row."""
    unique = {}
    for line in lines:
        key = key_fn(line)
        if key is not None:
            unique.setdefault(key, line)
    return list(unique.values())


def _transfer_movements(lines):
    # Outbound plan/sent is the transfer picking's product total, repeated on
    # each incoming move and shortage reason row.
    outbound = _unique_lines(
        lines,
        lambda line: (line.get("transfer_out_id"), line.get("product_id"))
        if line.get("transfer_out_id") is not None else None,
    )
    # Receipt quantities are specific to stock.move; multiple shortage reason
    # rows can reference the same move and repeat its expected/received values.
    inbound = _unique_lines(
        lines,
        lambda line: line.get("receipt_move_id")
        if line.get("receipt_move_id") is not None else (line.get("receipt_id"), line.get("product_id")),
    )
    return outbound, inbound


def _git_rows(lines):
    unique = _unique_lines(
        [line for line in lines if line.get("source_location_usage") == "transit"],
        lambda line: (line.get("source_location_id"), line.get("product_id")),
    )
    return [{
        "location": line.get("source_location"),
        "product": line.get("product_name"),
        "sku": line.get("sku"),
        "on_hand_units": float(line["git_on_hand_qty"] or 0),
        "reserved_units": float(line["git_reserved_qty"] or 0),
    } for line in unique]


def _source_location_rows(lines):
    movements = _unique_lines(
        lines,
        lambda line: (line.get("source_location_id"), line.get("product_id")),
    )
    grouped = defaultdict(list)
    for line in movements:
        grouped[(line.get("source_location"), line.get("source_location_usage"))].append(line)
    return [{
        "location": location,
        "usage": usage,
        "affected_products": len(rows),
        "current_on_hand_units": (sum(float(row.get("git_on_hand_qty") or 0) for row in rows)
                                  if usage == "transit" else None),
        "current_reserved_units": (sum(float(row.get("git_reserved_qty") or 0) for row in rows)
                                    if usage == "transit" else None),
    } for (location, usage), rows in sorted(grouped.items())]


def _related_transfer_outcomes(reason, case_lines, all_lines):
    affected = {(line.get("receipt_id"), line.get("product_id")) for line in case_lines}
    related = [line for line in all_lines
               if line["reason"] != reason
               and (line.get("receipt_id"), line.get("product_id")) in affected
               and line.get("receipt_state") == "done"]
    grouped = defaultdict(list)
    for line in related:
        grouped[(line["reason"], line.get("reason_type"))].append(line)
    return [{
        "reason": name,
        "reason_type": reason_type,
        "records": len(rows),
        "units": sum(float(row["qty"] or 0) for row in rows),
        "receipt_product_pairs": len({(row.get("receipt_id"), row.get("product_id")) for row in rows}),
    } for (name, reason_type), rows in sorted(grouped.items())]


def _outlet_rows(lines, outlet_volume):
    by_outlet = defaultdict(list)
    for line in lines:
        by_outlet[(line["store_id"], line["outlet"])].append(line)
    rows = []
    for (store_id, outlet), outlet_lines in by_outlet.items():
        outbound, inbound = _transfer_movements(outlet_lines)
        volume = outlet_volume.get(store_id, {})
        units = sum(float(l["qty"]) for l in outlet_lines)
        basis_ok = _unit_basis_ok(outlet_lines, volume)
        rows.append({
            "outlet": outlet,
            "lines": len(outlet_lines),
            "units": units,
            "received_units": volume.get("qty"),
            "received_lines": volume.get("lines"),
            "plan_units": _sum_or_none(l.get("plan_qty") for l in outbound),
            "sent_units": _sum_or_none(l.get("sent_qty") for l in outbound),
            "line_received_units": sum(float(l["received_qty"] or 0) for l in inbound),
            "physical_gap_units": sum(float(l["sent_qty"] or 0) for l in outbound) - sum(float(l["received_qty"] or 0) for l in inbound),
            "days": len({l["recorded_at"].date() for l in outlet_lines}),
            "line_rate": _rate(len(outlet_lines), volume.get("lines")),
            "unit_rate": _rate(units, volume.get("qty")) if basis_ok else None,
            "value_rs": round(sum(float(l["qty"]) * float(l["unit_cost"] or 0) for l in outlet_lines), 2),
        })
    return sorted(rows, key=lambda r: -r["units"])


def _transfer_rows(lines):
    grouped = defaultdict(list)
    for line in lines:
        grouped[(line["outlet"], line["sku"] or line["product_name"], line["product_name"])].append(line)
    rows = []
    for (outlet, sku, product), product_lines in grouped.items():
        outbound, inbound = _transfer_movements(product_lines)
        last = max(l["recorded_at"] for l in product_lines)
        rows.append({
            "id": f"{outlet}|{sku}",
            "outlet": outlet,
            "product": product,
            "sku": sku,
            "lines": len(product_lines),
            "units": sum(float(l["qty"]) for l in product_lines),
            "expected_units": sum(float(l["expected_qty"] or 0) for l in inbound),
            "received_units": sum(float(l["received_qty"] or 0) for l in inbound),
            "plan_units": _sum_or_none(l.get("plan_qty") for l in outbound),
            "sent_units": _sum_or_none(l.get("sent_qty") for l in outbound),
            "days": len({l["recorded_at"].date() for l in product_lines}),
            "last_seen": last.date().isoformat(),
            "value_rs": round(sum(float(l["qty"]) * float(l["unit_cost"] or 0) for l in product_lines), 2),
        })
    return sorted(rows, key=lambda r: -r["units"])


def _iso(value):
    return value.isoformat() if value is not None else None


def _trace_rows(lines):
    """One row per received-receipt shortage line, linking the outbound transfer
    note to the receipt that closed it. Odoo links them as receipt.origin ==
    outbound.name, which holds for every counted line (see the verification notes)."""
    rows = []
    for line in lines:
        rows.append({
            "id": line["shortage_id"],
            "transfer_out_id": line.get("transfer_out_id"),
            "product_id": line.get("product_id"),
            "receipt_id": line.get("receipt_id"),
            "receipt_move_id": line.get("receipt_move_id"),
            "source_location": line.get("source_location"),
            "source_location_usage": line.get("source_location_usage"),
            "git_on_hand_qty": float(line["git_on_hand_qty"]) if line.get("git_on_hand_qty") is not None else None,
            "git_reserved_qty": float(line["git_reserved_qty"]) if line.get("git_reserved_qty") is not None else None,
            "transfer_out": line["transfer_out"],
            "out_sent_on": _iso(line["out_done_at"]),
            "planned_by": line["out_created_by"],
            "out_sent_by": line["out_completed_by"],
            "receipt": line["receipt"],
            "receipt_state": line.get("receipt_state"),
            "received_on": _iso(line["receipt_done_at"]),
            "receipt_completed_by": line["receipt_completed_by"],
            "outlet": line["outlet"],
            "product": line["product_name"],
            "sku": line["sku"],
            "plan_qty": float(line["plan_qty"]) if line["plan_qty"] is not None else None,
            "sent_qty": float(line["sent_qty"]) if line["sent_qty"] is not None else None,
            "expected_qty": float(line["expected_qty"] or 0),
            "received_qty": float(line["received_qty"] or 0),
            "planning_gap_qty": (float(line["plan_qty"]) - float(line["sent_qty"])) if line["plan_qty"] is not None and line["sent_qty"] is not None else None,
            "missing_qty": float(line["qty"]),
            "recorded_by": line["recorded_by"],
            "recorded_on": _iso(line["recorded_at"]),
        })
    return sorted(rows, key=lambda r: (r["out_sent_on"] or "", r["transfer_out"] or ""))


def _excluded_rows(excluded):
    by_outlet = defaultdict(lambda: {"lines": 0, "units": 0.0, "states": set()})
    for line in excluded:
        bucket = by_outlet[line["outlet"]]
        bucket["lines"] += 1
        bucket["units"] += float(line["qty"])
        bucket["states"].add(line["receipt_state"])
    return sorted(
        [{"outlet": o, "lines": v["lines"], "units": v["units"], "states": sorted(v["states"])}
         for o, v in by_outlet.items()],
        key=lambda r: -r["units"],
    )


def _contributors(lines, key_fn):
    totals = defaultdict(lambda: {"current": 0.0, "lines": 0})
    for line in lines:
        bucket = totals[key_fn(line)]
        bucket["current"] += float(line["qty"])
        bucket["lines"] += 1
    return sorted(
        [{"name": name, "current": v["current"], "lines": v["lines"]} for name, v in totals.items()],
        key=lambda c: -c["current"],
    )


def build_transfer_discrepancy_findings() -> measurement.FindingBatch:
    lines = reporting.run_query(TRANSFER_LINES_SQL)
    if not lines:
        return measurement.FindingBatch()

    as_of = max(l["recorded_at"] for l in lines)
    as_of_date = as_of.date().isoformat()
    fresh = (datetime.now(UTC).date() - as_of.date()).days <= 2  # source lags 1-2 real days, see docs/reviews
    days_observed = reporting.run_query(OBSERVED_DAYS_SQL)[0]["days_observed"] or 0
    outlet_volume = {r["store_id"]: {"lines": r["total_lines"], "qty": float(r["total_qty"] or 0),
                                     "non_base_uom_moves": r["non_base_uom_moves"] or 0}
                     for r in reporting.run_query(OUTLET_VOLUME_SQL)}
    uom_check = _uom_consistency_check()
    data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)

    by_reason = defaultdict(list)
    for line in lines:
        by_reason[line["reason"]].append(line)

    findings, records = [], []
    # Every known reason, not just the ones with lines THIS scan -- a reason
    # that drops to zero incidents (e.g. "Product Damage" going quiet) must
    # still get a measured_healthy record below, or its open case can never
    # see a fresh measurement again and gets stuck forever (confirmed live,
    # 2026-11-03: transfer_discrepancy_product_damage's case hadn't been
    # touched since 2026-10-13 for exactly this reason).
    for reason in set(_REASON_PHRASING) | set(by_reason):
        reason_lines = by_reason.get(reason, [])
        # Only lines on received receipts count toward the headline. A cancelled
        # or not-yet-received receipt can carry a shortage record without any
        # goods having gone missing, so those are reported separately.
        excluded = [l for l in reason_lines if l["receipt_state"] != "done"]
        reason_lines = [l for l in reason_lines if l["receipt_state"] == "done"]
        incidents = len(reason_lines)
        days_appeared = len({l["recorded_at"].date() for l in reason_lines})
        metric_id = f"transfer_discrepancy_{_reason_key(reason)}"
        records.append(measurement.observation(
            metric_id, "snapshot",
            "measured_actionable" if incidents >= MIN_INCIDENTS and days_appeared >= MIN_DAYS_APPEARED else "measured_healthy",
            scope=metric_id, period=as_of_date, freshness="fresh" if fresh else "stale", value=float(incidents),
        ))
        if incidents < MIN_INCIDENTS or days_appeared < MIN_DAYS_APPEARED:
            continue

        outlets = {l["outlet"] for l in reason_lines}
        products = {l["sku"] or l["product_name"] for l in reason_lines}
        units = sum(float(l["qty"]) for l in reason_lines)
        priced = [l for l in reason_lines if l["unit_cost"]]
        financial_impact = round(sum(float(l["qty"]) * float(l["unit_cost"]) for l in priced), 2) if priced else None
        excluded_units = sum(float(l["qty"]) for l in excluded)
        days_observed_safe = max(days_observed, days_appeared)
        total_volume_lines = sum(v["lines"] for v in outlet_volume.values())
        total_volume_qty = sum(v["qty"] for v in outlet_volume.values())
        checks = [_recurrence_check(days_appeared, days_observed_safe), _PROCESS_ORIGIN_UNKNOWN,
                  _SOURCE_WAREHOUSE_UNKNOWN, uom_check]
        if excluded:
            checks.append({
                "source": "receipt_status", "verdict": "unknown",
                "detail": f"{len(excluded)} {'line' if len(excluded) == 1 else 'lines'} ({excluded_units:g} units) on receipts that were cancelled or "
                "not yet received are excluded from the figures above. Confirm whether the goods arrived.",
            })
        priority = _priority_for(days_appeared)
        outbound_movements, inbound_movements = _transfer_movements(reason_lines)
        plan_total = _sum_or_none(l.get("plan_qty") for l in outbound_movements)
        sent_total = _sum_or_none(l.get("sent_qty") for l in outbound_movements)
        received_on_lines = sum(float(l["received_qty"] or 0) for l in inbound_movements)
        impact = {
            "kind": "transfer_discrepancy",
            "plan_units": plan_total,
            "sent_units": sent_total,
            "line_received_units": received_on_lines,
            "planning_gap_units": (plan_total - sent_total) if plan_total is not None and sent_total is not None else None,
            "transit_gap_units": (sent_total - received_on_lines) if sent_total is not None else None,
            "total_gap_units": (plan_total - received_on_lines) if plan_total is not None else None,
            "financial_impact_rs": financial_impact,
            "count": incidents,
            "units_affected": units,
            "distinct_products": len(products),
            "line_rate": _rate(incidents, total_volume_lines),
            "unit_rate": _rate(units, total_volume_qty) if all(
                _unit_basis_ok(reason_lines, v) for v in outlet_volume.values()) else None,
            "unit_rate_numerator_units": round(units, 3),
            "unit_rate_denominator_units": round(total_volume_qty, 3),
            "unit_rate_denominator_label": f"all Celeste outlet transfer receipts in the {WINDOW_DAYS}-day window",
            "window_days": WINDOW_DAYS,
            "unit_rate_coverage_pct": round(
                sum(float(l["qty"]) for l in reason_lines if l["uom_matches_base"]) / units * 100, 1
            ) if units else None,
            "confidence": impact_engine.CONFIDENCE_ESTIMATED,
            "month": as_of_date,
            "lines_total": incidents,
            "lines_priced": len(priced),
            "valued_units_pct": round(sum(float(l["qty"]) for l in priced) / units * 100, 1) if units else None,
            "excluded_lines": len(excluded),
            "excluded_units": excluded_units,
            "detail": (
                f"{incidents} received-receipt reason records ({units:g} units recorded under this reason) across {len(outlets)} outlet(s) in the "
                f"{WINDOW_DAYS}-day window"
                + (f". Estimated Rs. {financial_impact:,.0f} at today's catalog cost; {len(priced)} of {incidents} "
                   "lines have a usable catalog cost, so this total is a lower bound." if financial_impact
                   else ". Value not established: no catalog cost on the affected products.")
            ),
        }
        # Days recorded in a window are not consecutive periods, so the impact
        # score's persistence multiplier does not apply here. Persistence is
        # shown on its own (see the persistence field below).
        business_impact_score = impact_engine.business_impact_score(priority, None, impact, data_quality)
        phrase = _REASON_PHRASING.get(reason, f'"{reason}" was recorded')
        findings.append({
            "id": f"{metric_id}-{as_of_date}",
            "metric_id": metric_id,
            "domain": "inventory",
            "title": f"{phrase} on warehouse transfers, across {len(outlets)} outlet(s)",
            "excluded_rows": _excluded_rows(excluded),
            "excluded_trace_rows": _trace_rows(excluded),
            "category": "critical",
            "severity": priority,
            "priority": priority,
            "business_impact_score": business_impact_score,
            "detected": date.today().isoformat(),
            "confirm": {"period": as_of_date, "current": incidents, "baseline": None, "pct_change": None, "unit": "count"},
            "breakdowns": [
                {"dimension": "outlet", "label": "Outlet", "mode": "contribution", "baseline_month": None,
                 "top_contributors": _contributors(reason_lines, lambda l: l["outlet"])},
                {"dimension": "product", "label": "Product", "mode": "contribution", "baseline_month": None,
                 "top_contributors": _contributors(reason_lines, lambda l: l["product_name"])},
            ],
            "impact": impact,
            "how": None,
            "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
            "data_quality": data_quality,
            "recommendations": [],
            "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
            "status": "completed",
            "store": None,
            "reason": reason,
            "persistence": {
                "days_appeared": days_appeared, "days_observed": days_observed_safe,
                "persistence_rate": _rate(days_appeared, days_observed_safe),
            },
            "outlet_rows": _outlet_rows(reason_lines, outlet_volume),
            "transfer_rows": _transfer_rows(reason_lines),
            "trace_rows": _trace_rows(reason_lines),
            "git_rows": _git_rows(reason_lines),
            "source_location_rows": _source_location_rows(reason_lines),
            "related_transfer_outcomes": _related_transfer_outcomes(reason, reason_lines, lines),
        })
    for finding in findings:
        finding["measurement"] = next(m for m in records if m["metric_id"] == finding["metric_id"])
    return measurement.FindingBatch(findings, records)

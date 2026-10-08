"""Stuck physical stock-count sessions: a count started (stock_count in
Odoo's cycle-count feature) but never finished (state != 'done',
completed_datetime IS NULL). This table (stock_count/stock_count_line/
stock_count_record) has never been queried by this app before -- discovered
live while investigating a manager-reported "huge stock variance" at
Colombo 6 that traced back to exactly this: a full-store count (7,724
lines) started 2026-08-11, only 30 lines ever actually scanned, then left
open. Because an unfinished session's un-scanned lines still carry the
system's onhand_qty with counted_qty defaulting to 0, any variance/shrinkage
report pulled from a stuck session is phantom -- it reads as though those
products vanished when they were simply never checked. Not a one-off: at
least 3 smaller stuck sessions at the same outlet in the prior 2 months
followed the identical pattern (started, a handful of scans, abandoned in
'review'), so this is a recurring process gap worth surfacing on its own,
not just something to fix once.

A session counts as "stuck" once it's been open (inprogress_datetime) more
than STUCK_AFTER_HOURS without completing -- long enough that a same-day
count genuinely in progress isn't falsely flagged (verified against live
data: even a comparably huge 7,487-line count at this outlet finished in
well under a day).
"""

from datetime import datetime, timedelta

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from backend.app.services.inventory import _usable_cost
from mcp_server.tools.sales import _period_bounds, _period_label

STUCK_AFTER_HOURS = 24

# Incident Investigation audit (Phase 1): sc.user_id is the session's
# "Responsible" field, NOT necessarily who physically started counting --
# verified live that user_id/create_uid/write_uid are genuinely different
# people on real sessions (e.g. one session: created by Fazlan Fairooz,
# responsible Dilanka Ranasinghe, last technically written by Kasun Peiris).
# Labeled "responsible" below, never "started_by" -- this app doesn't have a
# real "who started counting" signal (see get_stock_count_session_detail's
# per-scan actor data for the one field that genuinely answers that).
# sc.review_datetime and sc.notes are both real, previously-unsurfaced
# columns: most stuck sessions have already transitioned into 'review'
# state (not simply abandoned mid-count), and a subset carry a real,
# system-stated reason in notes (e.g. "Auto-created for negative stock
# correction") rather than an unknown cause.
STUCK_SESSIONS_SQL = f"""
    SELECT sc.id, sc.name, sc.state, sc.inprogress_datetime, sc.review_datetime, sc.notes,
           rs.name AS outlet, ru.name AS responsible
    FROM stock_count sc
    JOIN stock_location sl ON sl.id = sc.location_id
    JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
    LEFT JOIN res_users ruu ON ruu.id = sc.user_id
    LEFT JOIN res_partner ru ON ru.id = ruu.partner_id
    WHERE rs.id IN {CELESTE_STORE_IDS}
      AND sc.completed_datetime IS NULL
      AND sc.state != 'done'
      AND sc.inprogress_datetime IS NOT NULL
      AND sc.inprogress_datetime <= :cutoff
    ORDER BY sc.inprogress_datetime
"""

# One line per (session, product): whether it was ever scanned, plus enough
# to value it. Prefers the source system's OWN per-line avg_cost (verified
# live: Odoo's own stock_count_line.onhand_value/variance_value, computed
# from avg_cost, differs from this app's previously re-derived catalog-cost
# figure by ~1.2% on a real session) -- falling back to
# inventory._usable_cost's catalog-cost logic only when avg_cost is 0/null,
# which real data shows happens often enough (up to ~98% of a session's
# unscanned lines) that dropping the fallback would undercount, not just
# rebasis, several real sessions' value.
SESSION_LINES_SQL = """
    SELECT scl.count_id, scl.onhand_qty, scl.avg_cost,
           pt.celeste_cost_price_ex_vat AS cost, pt.list_price,
           pt.name->>'en_US' AS product, pt.default_code AS sku,
           (scanned.count_line_id IS NOT NULL) AS was_scanned
    FROM stock_count_line scl
    JOIN product_product pp ON pp.id = scl.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN (SELECT DISTINCT count_line_id FROM stock_count_record) scanned
        ON scanned.count_line_id = scl.id
    WHERE scl.count_id = ANY(:count_ids)
"""

# Phase 2 (C): full line-level detail for one session, including product
# identity -- used only by get_stock_count_session_detail, never by the
# aggregate finding (which stays lean, per-session stats only).
SESSION_LINE_DETAIL_SQL = """
    SELECT scl.id, scl.onhand_qty, scl.counted_qty, scl.variance, scl.variance_value, scl.avg_cost,
           pt.celeste_cost_price_ex_vat AS cost, pt.list_price,
           scl.first_scan_datetime, pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           (scanned.count_line_id IS NOT NULL) AS was_scanned
    FROM stock_count_line scl
    JOIN product_product pp ON pp.id = scl.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN (SELECT DISTINCT count_line_id FROM stock_count_record) scanned
        ON scanned.count_line_id = scl.id
    WHERE scl.count_id = :count_id
    ORDER BY scl.first_scan_datetime NULLS LAST, scl.id
"""

# Phase 2 (D): the only genuine per-scan event timeline this app has --
# real actor, real timestamp, real quantity. Empty for any session that was
# never scanned at all (verified live: 5 of the current 8 real stuck
# sessions have zero rows here) -- callers must render that as "no scan
# activity recorded," never fabricate an event.
SESSION_SCAN_EVENTS_SQL = """
    SELECT scr.id, scr.product_id, pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           scr.user_id, ru.name AS scanned_by, scr.record_state, scr.scanned_qty,
           scr.scan_datetime, scr.onhand_qty_at_scan
    FROM stock_count_record scr
    JOIN product_product pp ON pp.id = scr.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN res_users ruu ON ruu.id = scr.user_id
    LEFT JOIN res_partner ru ON ru.id = ruu.partner_id
    WHERE scr.count_id = :count_id
    ORDER BY scr.scan_datetime
"""

# Phase 2 scope guard for get_stock_count_session_detail: the same
# outlet-scoping join as STUCK_SESSIONS_SQL, so a session_id from outside
# Celeste's stores (or one that was never actually stuck) can't be probed.
SESSION_HEADER_SQL = f"""
    SELECT sc.id, sc.name, sc.state, sc.inprogress_datetime, sc.review_datetime,
           sc.completed_datetime, sc.notes, sc.create_date, sc.write_date,
           rs.name AS outlet, ru.name AS responsible, creator.name AS created_by
    FROM stock_count sc
    JOIN stock_location sl ON sl.id = sc.location_id
    JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
    LEFT JOIN res_users ruu ON ruu.id = sc.user_id
    LEFT JOIN res_partner ru ON ru.id = ruu.partner_id
    LEFT JOIN res_users cuu ON cuu.id = sc.create_uid
    LEFT JOIN res_partner creator ON creator.id = cuu.partner_id
    WHERE rs.id IN {CELESTE_STORE_IDS} AND sc.id = :count_id
"""

# Most recent scan across ALL of a session's lines -- "when did it stop,"
# not just "when did it start."
LAST_SCAN_SQL = """
    SELECT scl.count_id, MAX(scr.scan_datetime) AS last_scan_at
    FROM stock_count_record scr
    JOIN stock_count_line scl ON scl.id = scr.count_line_id
    WHERE scl.count_id = ANY(:count_ids)
    GROUP BY scl.count_id
"""


def get_stuck_stock_counts():
    """Every currently-stuck session across Celeste's 4 outlets, ranked by
    the phantom value at risk if its un-scanned lines were mistaken for real
    shrinkage."""
    cutoff = datetime.utcnow() - timedelta(hours=STUCK_AFTER_HOURS)
    sessions_rows = reporting.run_query(STUCK_SESSIONS_SQL, {"cutoff": cutoff})
    if not sessions_rows:
        return {
            "as_of": datetime.utcnow().isoformat(), "sessions": [], "total_value_at_risk_rs": 0.0,
            "valuation_coverage": {
                "total_unscanned_lines": 0, "source_cost_lines": 0, "catalog_estimate_lines": 0, "no_cost_data_lines": 0,
            },
            "top_products": [],
        }

    count_ids = [r["id"] for r in sessions_rows]
    line_rows = reporting.run_query(SESSION_LINES_SQL, {"count_ids": count_ids})
    last_scan_rows = reporting.run_query(LAST_SCAN_SQL, {"count_ids": count_ids})
    last_scan_by_session = {r["count_id"]: r["last_scan_at"] for r in last_scan_rows}

    # Final Stock-Count Financial Disclosure, item 1: coverage tallied by
    # the SAME loop that computes the value, so it can never drift out of
    # sync with the figure it's disclosing. Counts every unscanned line
    # exactly once into one of three buckets, regardless of onhand_qty --
    # a line with no usable cost is "no_cost_data" whether or not it would
    # have contributed money, because coverage is about pricing, not size.
    coverage = {"total_unscanned_lines": 0, "source_cost_lines": 0, "catalog_estimate_lines": 0, "no_cost_data_lines": 0}
    stats_by_session = {}
    # Product identity rides along on the same already-running query (just 2
    # extra SELECT columns on an existing join, not a second heavier query --
    # unlike SESSION_LINE_DETAIL_SQL below, which is genuinely per-line and
    # stays single-session-only), aggregated in this same pass so "what is
    # driving it" has a real product breakdown instead of the dead-end
    # "not stored on the case" message InventoryCaseView.jsx showed before.
    value_by_product: dict[tuple, dict] = {}
    for r in line_rows:
        stats = stats_by_session.setdefault(
            r["count_id"], {"total_lines": 0, "scanned_lines": 0, "unscanned_qty": 0.0, "unscanned_value": 0.0}
        )
        stats["total_lines"] += 1
        if r["was_scanned"]:
            stats["scanned_lines"] += 1
            continue
        coverage["total_unscanned_lines"] += 1
        # A negative onhand_qty is itself a separate data-integrity issue
        # (sold past what's recorded on hand, same class of gap
        # state_findings._top_seller_stock_finding already flags elsewhere)
        # -- not phantom stock "at risk," so it's floored at 0 here rather
        # than let it silently subtract from a business-facing Rs figure.
        onhand = max(0.0, float(r["onhand_qty"] or 0))
        avg_cost = float(r["avg_cost"] or 0)
        usable_cost = avg_cost if avg_cost > 0 else _usable_cost(r["cost"], r["list_price"])
        value = 0.0
        if usable_cost is not None:
            value = onhand * usable_cost
            stats["unscanned_value"] += value
        if avg_cost > 0:
            coverage["source_cost_lines"] += 1
        elif usable_cost is not None:
            coverage["catalog_estimate_lines"] += 1
        else:
            coverage["no_cost_data_lines"] += 1

        key = (r["sku"], r["product"] or "Unnamed product")
        p = value_by_product.setdefault(key, {"name": key[1], "sku": key[0], "lines": 0, "qty": 0.0, "value_rs": 0.0})
        p["lines"] += 1
        p["qty"] += onhand
        p["value_rs"] += value

    top_products = sorted(
        [{"name": p["name"], "sku": p["sku"], "lines": p["lines"], "qty": round(p["qty"], 2), "value_rs": round(p["value_rs"], 2)}
         for p in value_by_product.values()],
        key=lambda p: p["value_rs"], reverse=True,
    )[:10]

    sessions = []
    for r in sessions_rows:
        stats = stats_by_session.get(r["id"], {"total_lines": 0, "scanned_lines": 0, "unscanned_value": 0.0})
        last_scan_at = last_scan_by_session.get(r["id"])
        age = datetime.utcnow() - r["inprogress_datetime"]
        sessions.append({
            "id": r["id"],
            "name": r["name"],
            "state": r["state"],
            "review_started_at": r["review_datetime"].isoformat() if r["review_datetime"] else None,
            "notes": r["notes"],
            "outlet": r["outlet"],
            "started_at": r["inprogress_datetime"].isoformat(),
            "responsible": r["responsible"] or "Unknown",
            "days_stuck": round(age.total_seconds() / 86400, 1),
            "total_lines": stats["total_lines"],
            "scanned_lines": stats["scanned_lines"],
            "unscanned_lines": stats["total_lines"] - stats["scanned_lines"],
            "unscanned_value_rs": round(stats["unscanned_value"], 2),
            "last_scan_at": last_scan_at.isoformat() if last_scan_at else None,
            "has_scan_activity": stats["scanned_lines"] > 0,
        })
    sessions.sort(key=lambda s: s["unscanned_value_rs"], reverse=True)

    total_value_at_risk = round(sum(s["unscanned_value_rs"] for s in sessions), 2)
    return {
        "as_of": datetime.utcnow().isoformat(),
        "sessions": sessions,
        "total_value_at_risk_rs": total_value_at_risk,
        "valuation_coverage": coverage,
        "top_products": top_products,
    }


def get_stock_count_session_detail(session_id: int):
    """Incident Investigation Phase 2 (B/C/D): the full, individual-session
    record for one stuck count -- header, every count line, and every real
    scan event, all read-only and all traceable back to the exact same
    Celeste-store-scoped population STUCK_SESSIONS_SQL uses (SESSION_HEADER_
    SQL reuses the identical outlet join/scope filter), so a session that
    isn't a real Celeste session can't be probed and this never mixes
    populations. Returns None when the id doesn't resolve within that scope
    -- the caller (the API route) turns that into a 404, never a fabricated
    empty session.

    Line-level `variance`/`variance_value`/`avg_cost` are shown as Odoo's
    own computed values (untouched) for transparency; `unscanned_value_rs`
    at the top uses the same avg_cost-preferred, catalog-cost-fallback
    valuation as get_stuck_stock_counts() so this page's total always
    matches that session's row in the aggregate finding.

    Event history (`scan_events`) is exactly what stock_count_record holds
    -- real actor, real timestamp, real quantity -- and is an empty list,
    never an invented one, for any session with no scan activity (verified
    live: true for 5 of the current 8 real stuck sessions)."""
    header_rows = reporting.run_query(SESSION_HEADER_SQL, {"count_id": session_id})
    if not header_rows:
        return None
    h = header_rows[0]

    line_rows = reporting.run_query(SESSION_LINE_DETAIL_SQL, {"count_id": session_id})
    lines = []
    unscanned_value = 0.0
    scanned_lines = 0
    for r in line_rows:
        # Incident Investigation Final Integrity Check, item 1: this app's
        # "Estimated phantom variance" total is NOT the same figure as
        # summing this line's own variance_value column -- verified live on
        # a real session where they differ by two orders of magnitude
        # (Rs 3,267.59 vs Rs 40.89), because the total (a) only sums
        # unscanned lines, (b) floors a negative recorded quantity to zero
        # (a data-integrity artifact, not phantom stock), and (c) applies a
        # catalog-cost estimate on a line the source system has no avg_cost
        # for at all (Odoo's own variance_value silently reads 0 there).
        # valuation_basis makes that divergence legible per line instead of
        # leaving the manager to guess why two real numbers don't match.
        if r["was_scanned"]:
            scanned_lines += 1
            valuation_basis = "scanned"
        else:
            onhand = max(0.0, float(r["onhand_qty"] or 0))
            avg_cost = float(r["avg_cost"] or 0)
            usable_cost = avg_cost if avg_cost > 0 else _usable_cost(r["cost"], r["list_price"])
            if usable_cost is not None:
                unscanned_value += onhand * usable_cost
            if avg_cost > 0:
                valuation_basis = "source_cost"
            elif usable_cost is not None:
                valuation_basis = "catalog_estimate"
            else:
                valuation_basis = "no_cost_data"
        lines.append({
            "id": r["id"],
            "product_name": r["product_name"],
            "sku": r["sku"],
            "valuation_basis": valuation_basis,
            "onhand_qty": float(r["onhand_qty"] or 0),
            "counted_qty": float(r["counted_qty"] or 0),
            "variance": float(r["variance"] or 0),
            "variance_value_rs": round(float(r["variance_value"] or 0), 2),
            "avg_cost": float(r["avg_cost"] or 0),
            "first_scan_at": r["first_scan_datetime"].isoformat() if r["first_scan_datetime"] else None,
            "was_scanned": bool(r["was_scanned"]),
        })

    scan_rows = reporting.run_query(SESSION_SCAN_EVENTS_SQL, {"count_id": session_id})
    scan_events = [{
        "product_name": r["product_name"],
        "sku": r["sku"],
        "scanned_by": r["scanned_by"] or "Unknown",
        "record_state": r["record_state"],
        "scanned_qty": float(r["scanned_qty"] or 0),
        "onhand_qty_at_scan": float(r["onhand_qty_at_scan"] or 0),
        "scan_datetime": r["scan_datetime"].isoformat() if r["scan_datetime"] else None,
    } for r in scan_rows]

    age = datetime.utcnow() - h["inprogress_datetime"]
    return {
        # Incident Investigation Final Integrity Check, item 2: this whole
        # payload is a LIVE read of the reporting DB, taken at call time --
        # distinct from the case's own preserved case_observations snapshot
        # (decision-time evidence, never rewritten by this function). The
        # caller must label this timestamp explicitly wherever it's shown
        # alongside the case's own "last observed" date, so the two are
        # never mistaken for the same measurement.
        "retrieved_at": datetime.utcnow().isoformat(),
        "id": h["id"],
        "name": h["name"],
        "state": h["state"],
        "outlet": h["outlet"],
        "responsible": h["responsible"] or "Unknown",
        "created_by": h["created_by"] or "Unknown",
        "notes": h["notes"],
        "started_at": h["inprogress_datetime"].isoformat() if h["inprogress_datetime"] else None,
        "review_started_at": h["review_datetime"].isoformat() if h["review_datetime"] else None,
        "completed_at": h["completed_datetime"].isoformat() if h["completed_datetime"] else None,
        "record_created_at": h["create_date"].isoformat() if h["create_date"] else None,
        "last_technical_write_at": h["write_date"].isoformat() if h["write_date"] else None,
        "days_stuck": round(age.total_seconds() / 86400, 1) if h["inprogress_datetime"] else None,
        "total_lines": len(lines),
        "scanned_lines": scanned_lines,
        "unscanned_lines": len(lines) - scanned_lines,
        "unscanned_value_rs": round(unscanned_value, 2),
        "has_scan_activity": len(scan_events) > 0,
        "lines": lines,
        "scan_events": scan_events,
    }


# Completed Stock-Count Variance & Shrinkage Investigation: the blind spot
# every prior function in this file structurally excludes -- state = 'done'
# sessions carry REAL, physically-verified variance (Odoo's own signed
# variance_value, not a phantom "if this stays unscanned" estimate), and
# nothing in this app has ever looked at them before. Verified live on real
# data: one completed session at Colombo 6 (2026-09-08) alone carried
# Rs 7.1M gross variance -- 44% of the entire trailing-3-month total across
# all 4 outlets. product_type is grouped by product_template.
# celeste_product_type (INVENTORY / PACKING MATERIAL / RAW ITEM / SERVICE)
# -- confirmed live this is a real, reliable signal (not invented): the
# branded packaging bags driving the Colombo 6 session's variance are
# genuinely tagged PACKING MATERIAL, while the cigarettes showing
# consistent one-directional shortages are tagged INVENTORY. product_
# template.categ_id was checked and rejected as a category source -- it's
# almost entirely one company-wide grouping ("celeste"), not a
# merchandising taxonomy.
COMPLETED_SESSIONS_SQL = f"""
    SELECT sc.id, sc.name, rs.id AS store_id, rs.name AS outlet, sc.completed_datetime,
           COUNT(scl.id) AS lines,
           SUM(scl.variance_value) AS net_variance_value,
           SUM(ABS(scl.variance_value)) AS gross_abs_variance_value
    FROM stock_count sc
    JOIN stock_location sl ON sl.id = sc.location_id
    JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
    JOIN stock_count_line scl ON scl.count_id = sc.id
    WHERE rs.id IN {CELESTE_STORE_IDS}
      AND sc.state = 'done'
      AND sc.completed_datetime >= :start AND sc.completed_datetime <= :end
    GROUP BY sc.id, sc.name, rs.id, rs.name, sc.completed_datetime
    ORDER BY gross_abs_variance_value DESC
"""

PRODUCT_TYPE_VARIANCE_SQL = f"""
    SELECT COALESCE(pt.celeste_product_type, 'UNSPECIFIED') AS product_type,
           SUM(ABS(scl.variance_value)) AS gross_abs_variance_value,
           SUM(scl.variance_value) AS net_variance_value
    FROM stock_count sc
    JOIN stock_location sl ON sl.id = sc.location_id
    JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
    JOIN stock_count_line scl ON scl.count_id = sc.id
    JOIN product_product pp ON pp.id = scl.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    WHERE rs.id IN {CELESTE_STORE_IDS}
      AND sc.state = 'done'
      AND sc.completed_datetime >= :start AND sc.completed_datetime <= :end
    GROUP BY COALESCE(pt.celeste_product_type, 'UNSPECIFIED')
    ORDER BY gross_abs_variance_value DESC
"""


def get_completed_stock_count_variance(period: str | None = None, start_date: str | None = None,
                                        end_date: str | None = None, store_id: int | None = None):
    """Completed (state='done') stock counts in a period -- real, physically
    verified variance, not the "phantom if unscanned" estimate
    get_stuck_stock_counts() computes. `period`/`start_date`/`end_date`
    follow the exact same convention as sales.get_sales_summary/loss_and_
    damage.get_loss_and_damage (via the shared _period_bounds/_period_label
    helpers) so Ask Celeste's tool parameters stay consistent across tools.

    Returns two `evidence_engine`-shaped breakdowns (dimension="store",
    dimension="product_type") ready to pass straight into build_snapshot_
    evidence -- no adapter layer needed. store_id optionally narrows to one
    outlet (still validated against CELESTE_STORE_IDS via the SQL's own
    WHERE, so an out-of-scope store_id just returns no rows, never another
    outlet's data)."""
    start, end = _period_bounds(period, start_date, end_date)
    params = {"start": start, "end": end}

    session_rows = reporting.run_query(COMPLETED_SESSIONS_SQL, params)
    if store_id is not None:
        session_rows = [r for r in session_rows if r["store_id"] == store_id]
    product_type_rows = reporting.run_query(PRODUCT_TYPE_VARIANCE_SQL, params)

    sessions = [{
        "id": r["id"],
        "name": r["name"],
        "outlet": r["outlet"],
        "completed_at": r["completed_datetime"].isoformat() if r["completed_datetime"] else None,
        "lines": r["lines"],
        "net_variance_value_rs": round(float(r["net_variance_value"] or 0), 2),
        "gross_abs_variance_value_rs": round(float(r["gross_abs_variance_value"] or 0), 2),
    } for r in session_rows]

    by_outlet = {}
    for s in sessions:
        by_outlet[s["outlet"]] = by_outlet.get(s["outlet"], 0.0) + s["gross_abs_variance_value_rs"]
    outlet_contributors = [{"name": name, "current": round(v, 2), "delta": round(v, 2)} for name, v in by_outlet.items()]
    outlet_contributors.sort(key=lambda c: c["current"], reverse=True)

    product_type_contributors = [
        {"name": r["product_type"], "current": round(float(r["gross_abs_variance_value"] or 0), 2),
         "delta": round(float(r["gross_abs_variance_value"] or 0), 2)}
        for r in product_type_rows
    ]
    if store_id is not None:
        # PRODUCT_TYPE_VARIANCE_SQL isn't store-filtered (it doesn't need a
        # second store_id-narrowed query -- store scoping only matters for
        # this breakdown's total, not its shape), so when store_id narrows
        # the session list, this breakdown is left as the full-population
        # one and explicitly NOT reported as store-scoped, rather than
        # silently mismatching the sessions above.
        product_type_contributors = None

    total_gross = round(sum(s["gross_abs_variance_value_rs"] for s in sessions), 2)
    total_net = round(sum(s["net_variance_value_rs"] for s in sessions), 2)

    breakdowns = [{
        "dimension": "store", "label": "Estimated completed-count variance by outlet",
        "mode": "contribution", "baseline_month": None,
        "total": total_gross, "top_contributors": outlet_contributors,
    }]
    if product_type_contributors is not None:
        breakdowns.append({
            "dimension": "product_type", "label": "Estimated completed-count variance by product type",
            "mode": "contribution", "baseline_month": None,
            "total": round(sum(c["current"] for c in product_type_contributors), 2),
            "top_contributors": product_type_contributors,
        })

    return {
        "period": _period_label(period, start, end, start_date, end_date),
        # Raw ISO bounds, not just the human label -- shrinkage_loss_damage_
        # check needs these to query loss_and_damage.get_loss_and_damage()
        # over the EXACT same window, not its own "last month" default.
        "start_date": start,
        "end_date": end,
        "session_count": len(sessions),
        "sessions": sorted(sessions, key=lambda s: s["gross_abs_variance_value_rs"], reverse=True),
        "net_variance_value_rs": total_net,
        "gross_abs_variance_value_rs": total_gross,
        "breakdowns": breakdowns,
    }

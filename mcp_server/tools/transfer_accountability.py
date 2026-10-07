"""Who handled, and which products lost units, on warehouse-to-outlet transfers.

Measured on every done receipt move in the window, not only moves that carry a
recorded shortage, so each rate has its full handled volume as the denominator.
Missing = expected - received per move (never negative); extra = received - expected.

Each person appears twice, in the role they played: as the receiver who marked
the receipt done, and as the dispatcher who marked the outbound note done. A
missing unit is a gap between the two, so a person's rate is a pointer to check,
not a finding of fault. Shortage recording is not used here: it is logged under a
shared login for most lines.

Filters narrow the moves before aggregation. A product's recurring status is set
on the full window, so filtering to one outlet does not change which products are
recurring. Raw rows are cached briefly so filter changes do not re-run the query.
"""

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools.transfer_discrepancy import WINDOW_DAYS

NOT_RECORDED = "Not recorded"
RECURRING_MIN_MOVES = 3    # a product short on fewer moves is a one-off
RECURRING_MIN_OUTLETS = 2  # ...and only counts as recurring across more than one outlet
TOP_PRODUCTS = 15
CACHE_SECONDS = 300

MOVES_SQL = f"""
    SELECT rs.id AS store_id, rs.name AS outlet, sp.id AS receipt_id, sp.name AS receipt,
           sp.date_done AS done_at,
           sm.id AS move_id, pt.name->>'en_US' AS product, pt.default_code AS sku,
           sm.product_uom_qty AS expected, sm.quantity AS received,
           in_done.login AS receipt_completed_by, out_done.login AS dispatch_completed_by,
           op.name AS transfer_out,
           (SELECT COUNT(*) FROM stock_move om WHERE om.picking_id = op.id AND om.product_id = sm.product_id) AS out_lines
    FROM stock_move sm
    JOIN stock_picking sp ON sp.id = sm.picking_id
    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
    JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
    JOIN stock_location sl ON sl.id = sp.location_id
    JOIN product_product pp ON pp.id = sm.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN stock_picking op ON op.name = sp.origin
    LEFT JOIN LATERAL (
        SELECT u.login FROM mail_message m
        JOIN mail_tracking_value tv ON tv.mail_message_id = m.id
        JOIN ir_model_fields f ON f.id = tv.field_id AND f.name = 'state' AND f.model = 'stock.picking'
        LEFT JOIN res_partner p ON p.id = m.author_id
        LEFT JOIN res_users u ON u.partner_id = p.id
        WHERE m.model = 'stock.picking' AND m.res_id = sp.id AND tv.new_value_char = 'Done'
        ORDER BY m.date DESC LIMIT 1) in_done ON TRUE
    LEFT JOIN LATERAL (
        SELECT u.login FROM mail_message m
        JOIN mail_tracking_value tv ON tv.mail_message_id = m.id
        JOIN ir_model_fields f ON f.id = tv.field_id AND f.name = 'state' AND f.model = 'stock.picking'
        LEFT JOIN res_partner p ON p.id = m.author_id
        LEFT JOIN res_users u ON u.partner_id = p.id
        WHERE m.model = 'stock.picking' AND m.res_id = op.id AND tv.new_value_char = 'Done'
        ORDER BY m.date DESC LIMIT 1) out_done ON TRUE
    CROSS JOIN (SELECT MAX(create_date) AS as_of FROM stock_move_shortage) b
    WHERE spt.code = 'incoming' AND sl.complete_name LIKE 'Transits/%'
      AND rs.id IN {CELESTE_STORE_IDS} AND sp.state = 'done'
      AND sp.create_date >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
"""

# Outbound lines sent above plan. Quantity sent (quantity) above the planned quantity
# (product_uom_qty), on notes whose receipt is done in the window. Company-wide for the window.
OVER_PLAN_SQL = f"""
    WITH rcv AS (
        SELECT sp.origin, rs.id AS store_id, rs.name AS outlet
        FROM stock_picking sp
        JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
        JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
        JOIN stock_location sl ON sl.id = sp.location_id
        CROSS JOIN (SELECT MAX(create_date) AS as_of FROM stock_move_shortage) b
        WHERE spt.code = 'incoming' AND sl.complete_name LIKE 'Transits/%'
          AND sp.state = 'done' AND rs.id IN {CELESTE_STORE_IDS}
          AND sp.create_date >= b.as_of - INTERVAL '{WINDOW_DAYS} days'
          AND sp.origin IS NOT NULL
    )
    SELECT m.id AS move_id, op.name AS transfer_out, rcv.outlet, rcv.store_id,
           pt.name->>'en_US' AS product, pt.default_code AS sku,
           m.product_uom_qty AS plan, m.quantity AS sent, out_done.login AS dispatch_completed_by
    FROM rcv
    JOIN stock_picking op ON op.name = rcv.origin
    JOIN stock_move m ON m.picking_id = op.id
    JOIN product_product pp ON pp.id = m.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN LATERAL (
        SELECT u.login FROM mail_message mm
        JOIN mail_tracking_value tv ON tv.mail_message_id = mm.id
        JOIN ir_model_fields f ON f.id = tv.field_id AND f.name = 'state' AND f.model = 'stock.picking'
        LEFT JOIN res_partner p ON p.id = mm.author_id
        LEFT JOIN res_users u ON u.partner_id = p.id
        WHERE mm.model = 'stock.picking' AND mm.res_id = op.id AND tv.new_value_char = 'Done'
        ORDER BY mm.date DESC LIMIT 1) out_done ON TRUE
    WHERE m.quantity > m.product_uom_qty
"""

_cache = {"at": 0.0, "rows": None, "over": None}


@dataclass(frozen=True)
class Filters:
    outlets: frozenset = frozenset()   # store ids as strings
    products: frozenset = frozenset()  # product keys (see _product_key)
    people: frozenset = frozenset()    # logins, or NOT_RECORDED; matches either role
    date_from: str | None = None       # ISO yyyy-mm-dd, inclusive
    date_to: str | None = None         # ISO yyyy-mm-dd, inclusive
    recurring_only: bool = False

    @property
    def active(self) -> bool:
        return bool(self.outlets or self.products or self.people or self.date_from or self.date_to or self.recurring_only)


def _valid_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def make_filters(outlet=(), product=(), person=(), date_from=None, date_to=None, recurring_only=False) -> Filters:
    return Filters(
        outlets=frozenset(str(v) for v in outlet if str(v).strip()),
        products=frozenset(v for v in product if v),
        people=frozenset(v for v in person if v),
        date_from=_valid_date(date_from),
        date_to=_valid_date(date_to),
        recurring_only=bool(recurring_only),
    )


def _number(value) -> float:
    return float(value or 0)


def _rate(part: float, whole: float):
    return round(part / whole * 100, 2) if whole else None


def _missing(row) -> float:
    return max(_number(row["expected"]) - _number(row["received"]), 0.0)


def _product_key(row) -> str:
    return row["sku"] or row["product"] or "Unnamed product"


def _people(rows, key, total_missing):
    by_person = defaultdict(lambda: {"moves": 0, "expected": 0.0, "missing": 0.0, "extra": 0.0,
                                     "zero_lines": 0, "largest_line_missing": 0.0})
    for row in rows:
        person = by_person[row[key] or NOT_RECORDED]
        expected, received = _number(row["expected"]), _number(row["received"])
        missing = _missing(row)
        person["moves"] += 1
        person["expected"] += expected
        person["missing"] += missing
        person["extra"] += max(received - expected, 0.0)
        person["zero_lines"] += 1 if expected > 0 and received == 0 else 0
        person["largest_line_missing"] = max(person["largest_line_missing"], missing)
    out = []
    for name, p in by_person.items():
        out.append({
            "person": name,
            "moves": p["moves"],
            "expected_units": round(p["expected"]),
            "missing_units": round(p["missing"]),
            "extra_units": round(p["extra"]),
            "missing_rate_pct": _rate(p["missing"], p["expected"]),
            "zero_received_lines": p["zero_lines"],
            "largest_line_missing": round(p["largest_line_missing"]),
            "share_of_missing_pct": round(p["missing"] / total_missing * 100, 1) if total_missing else None,
        })
    return sorted(out, key=lambda r: -r["missing_units"])


def _product_totals(rows, total_missing, recurring_keys):
    """Every product with a gap in these rows, before the top-N cut."""
    by_product = defaultdict(lambda: {"product": None, "sku": None, "expected": 0.0, "missing": 0.0,
                                      "short_moves": 0, "outlets": set()})
    for row in rows:
        key = _product_key(row)
        product = by_product[key]
        product["product"] = row["product"] or "Unnamed product"
        product["sku"] = row["sku"]
        missing = _missing(row)
        product["expected"] += _number(row["expected"])
        product["missing"] += missing
        if missing > 0:
            product["short_moves"] += 1
            product["outlets"].add(row["store_id"])
    out = []
    for key, p in by_product.items():
        if p["missing"] <= 0:
            continue
        out.append({
            "key": key,
            "product": p["product"],
            "sku": p["sku"],
            "expected_units": round(p["expected"]),
            "missing_units": round(p["missing"]),
            "missing_rate_pct": _rate(p["missing"], p["expected"]),
            "short_moves": p["short_moves"],
            "outlets_short": len(p["outlets"]),
            "pattern": "recurring" if key in recurring_keys else "one-off",
            "share_of_missing_pct": round(p["missing"] / total_missing * 100, 1) if total_missing else None,
        })
    return sorted(out, key=lambda r: -r["missing_units"])


def _recurring_keys(rows) -> set:
    """Recurring is a fact about the full window, so it is computed before any filter."""
    short_moves = defaultdict(int)
    short_outlets = defaultdict(set)
    for row in rows:
        if _missing(row) > 0:
            key = _product_key(row)
            short_moves[key] += 1
            short_outlets[key].add(row["store_id"])
    return {k for k, n in short_moves.items() if n >= RECURRING_MIN_MOVES and len(short_outlets[k]) >= RECURRING_MIN_OUTLETS}


def _daily(rows, recurring_keys):
    """Missing units per receipt day, split into recurring products and everything else."""
    days = defaultdict(lambda: {"recurring": 0.0, "other": 0.0})
    for row in rows:
        if not row.get("done_at"):
            continue
        bucket = "recurring" if _product_key(row) in recurring_keys else "other"
        days[row["done_at"].date().isoformat()][bucket] += _missing(row)
    return [{"date": day, "recurring_units": round(v["recurring"]), "other_units": round(v["other"])}
            for day, v in sorted(days.items())]


def _matches(row, f: Filters, recurring_keys) -> bool:
    if f.outlets and str(row["store_id"]) not in f.outlets:
        return False
    if f.products and _product_key(row) not in f.products:
        return False
    if f.people:
        roles = {row["receipt_completed_by"] or NOT_RECORDED, row["dispatch_completed_by"] or NOT_RECORDED}
        if not roles & f.people:
            return False
    if f.date_from or f.date_to:
        if not row.get("done_at"):
            return False
        day = row["done_at"].date().isoformat()
        if (f.date_from and day < f.date_from) or (f.date_to and day > f.date_to):
            return False
    if f.recurring_only and _product_key(row) not in recurring_keys:
        return False
    return True


def _options(rows, recurring_keys) -> dict:
    outlets = {}
    people = set()
    products = {}
    dates = []
    for row in rows:
        outlets[str(row["store_id"])] = row["outlet"]
        people.add(row["receipt_completed_by"] or NOT_RECORDED)
        people.add(row["dispatch_completed_by"] or NOT_RECORDED)
        if row.get("done_at"):
            dates.append(row["done_at"].date().isoformat())
        if _missing(row) > 0:
            products[_product_key(row)] = row["product"] or "Unnamed product"
    return {
        "outlets": [{"id": k, "name": v} for k, v in sorted(outlets.items(), key=lambda kv: kv[1] or "")],
        "people": sorted(people, key=lambda p: (p == NOT_RECORDED, p)),
        "products": [{"key": k, "name": v} for k, v in sorted(products.items(), key=lambda kv: kv[1])],
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "recurring": sorted(recurring_keys),
    }


def summarize(rows, recurring_keys=None) -> dict:
    """Pure aggregation over move rows (see MOVES_SQL). Testable without a database."""
    if recurring_keys is None:
        recurring_keys = _recurring_keys(rows)
    expected = sum(_number(r["expected"]) for r in rows)
    missing = sum(_missing(r) for r in rows)
    extra = sum(max(_number(r["received"]) - _number(r["expected"]), 0.0) for r in rows)
    products = _product_totals(rows, missing, recurring_keys)
    return {
        "moves": len(rows),
        "receipts": len({r["receipt_id"] for r in rows}),
        "expected_units": round(expected),
        "missing_units": round(missing),
        "extra_units": round(extra),
        "missing_rate_pct": _rate(missing, expected),
        "receivers": _people(rows, "receipt_completed_by", missing),
        "dispatchers": _people(rows, "dispatch_completed_by", missing),
        "products": [{k: v for k, v in p.items() if k != "key"} for p in products[:TOP_PRODUCTS]],
        "daily": _daily(rows, recurring_keys),
    }


def _raw_rows():
    now = time.monotonic()
    if _cache["rows"] is None or now - _cache["at"] > CACHE_SECONDS:
        _cache["rows"] = reporting.run_query(MOVES_SQL)
        _cache["over"] = reporting.run_query(OVER_PLAN_SQL)
        _cache["at"] = now
    return _cache["rows"]


def over_plan_summary(rows) -> dict:
    """Outbound lines sent above their plan. Pure over OVER_PLAN_SQL rows."""
    by_dispatcher = defaultdict(lambda: {"lines": 0, "units_over": 0.0})
    lines = []
    for row in rows:
        over = _number(row["sent"]) - _number(row["plan"])
        who = row["dispatch_completed_by"] or NOT_RECORDED
        by_dispatcher[who]["lines"] += 1
        by_dispatcher[who]["units_over"] += over
        lines.append({
            "transfer_out": row["transfer_out"],
            "outlet": row["outlet"],
            "product": row["product"],
            "sku": row["sku"],
            "plan_qty": round(_number(row["plan"])),
            "sent_qty": round(_number(row["sent"])),
            "over_qty": round(over),
            "dispatch_completed_by": row["dispatch_completed_by"],
        })
    lines.sort(key=lambda r: -r["over_qty"])
    return {
        "lines": len(rows),
        "units_over": round(sum(r["over_qty"] for r in lines)),
        "by_dispatcher": sorted(
            ({"person": k, "lines": v["lines"], "units_over": round(v["units_over"])} for k, v in by_dispatcher.items()),
            key=lambda r: -r["units_over"],
        ),
        "top_lines": lines[:10],
    }


def not_on_plan_summary(rows) -> dict:
    """Receipt lines whose outbound note has no line for that product (sent without a plan)."""
    flagged = [r for r in rows if r["transfer_out"] is not None and not r["out_lines"]]
    return {
        "lines": len(flagged),
        "units_received": round(sum(_number(r["received"]) for r in flagged)),
    }


def build(filters: Filters | None = None) -> dict:
    f = filters or Filters()
    raw = _raw_rows()
    recurring_keys = _recurring_keys(raw)
    kept = [r for r in raw if _matches(r, f, recurring_keys)]
    unfiltered_missing = sum(_missing(r) for r in raw)
    unfiltered_expected = sum(_number(r["expected"]) for r in raw)
    return {
        "window_days": WINDOW_DAYS,
        **summarize(kept, recurring_keys),
        "scope": {
            "is_filtered": f.active,
            "unfiltered_expected_units": round(unfiltered_expected),
            "unfiltered_missing_units": round(unfiltered_missing),
        },
        "filters": {
            "outlets": sorted(f.outlets),
            "products": sorted(f.products),
            "people": sorted(f.people),
            "date_from": f.date_from,
            "date_to": f.date_to,
            "recurring_only": f.recurring_only,
        },
        "options": _options(raw, recurring_keys),
        "over_plan": over_plan_summary(_cache["over"] or []),
        "not_on_plan": not_on_plan_summary(raw),
    }

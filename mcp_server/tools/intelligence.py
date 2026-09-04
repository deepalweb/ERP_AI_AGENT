"""Generic metric-investigation engine.

This is not "the cancellation investigator" — it's one pipeline that works for
any KPI registered in METRICS. Each metric declares how to compute its monthly
trend and which business dimensions (product, store, customer, reason) it can
be broken down by. `investigate()` runs the same steps for all of them:

    trend -> anomaly check -> rank contributing segments per dimension

Adding a new investigable metric (margin, purchasing spend, churn, ...) means
adding a registry entry below, not writing a new investigation function.
"""

from dataclasses import dataclass, field
from datetime import date
from statistics import mean, pstdev
from typing import Literal, Optional

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

Direction = Literal["higher_is_bad", "higher_is_good"]
DimMode = Literal["contribution", "rate", "composition"]

MIN_SEGMENT_VOLUME = 15  # ignore segments too small to draw a conclusion from
ANOMALY_PCT_THRESHOLD = 15.0
ANOMALY_Z_THRESHOLD = 1.5
TOP_N = 5


def _month_key(dt) -> str:
    return dt.strftime("%Y-%m")


def _month_bounds(month_key: str):
    year, month = map(int, month_key.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _shift_month(month_key: str, delta: int) -> str:
    year, month = map(int, month_key.split("-"))
    idx = year * 12 + (month - 1) + delta
    y, m = divmod(idx, 12)
    return f"{y:04d}-{m + 1:02d}"


@dataclass
class Dimension:
    id: str
    label: str
    mode: DimMode
    # SQL producing rows keyed by `name`, plus `value` (contribution/rate-numerator-style
    # metrics) or `numerator`/`denominator` (rate mode). Must accept :start/:end binds
    # covering one month (see _month_bounds).
    sql: str


@dataclass
class MetricDef:
    id: str
    label: str
    unit: Literal["percent", "currency", "count"]
    direction: Direction
    trend_sql: str  # (month, numerator[, denominator]) across full history
    is_rate: bool = False
    dimensions: list[Dimension] = field(default_factory=list)
    notes: Optional[str] = None
    # If set, a "excess events" impact estimate can be translated into a business
    # quantity — currently only "lost_orders" (paired with the revenue/order_volume
    # metrics to price the excess). None means investigation.py skips impact estimation
    # for this metric rather than guessing a unit.
    impact_kind: Optional[Literal["lost_orders"]] = None


METRICS: dict[str, MetricDef] = {
    "cancellation_rate": MetricDef(
        id="cancellation_rate",
        label="Order cancellation rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        impact_kind="lost_orders",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month,
                   COUNT(*) FILTER (WHERE state = 'cancel') AS numerator,
                   COUNT(*) AS denominator
            FROM sale_order
            WHERE state IN ('sale', 'cancel') AND store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="rate",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name,
                           COUNT(*) FILTER (WHERE so.state = 'cancel') AS numerator,
                           COUNT(*) AS denominator
                    FROM sale_order so
                    LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state IN ('sale', 'cancel') AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
                """,
            ),
            Dimension(
                id="reason",
                label="Cancellation reason",
                mode="composition",
                sql=f"""
                    SELECT COALESCE(cancel_reason, 'Not recorded') AS name, COUNT(*) AS value
                    FROM sale_order
                    WHERE state = 'cancel' AND store_id IN {CELESTE_STORE_IDS}
                      AND date_order >= :start AND date_order < :end
                    GROUP BY COALESCE(cancel_reason, 'Not recorded')
                """,
            ),
            Dimension(
                id="product",
                label="Product",
                mode="contribution",
                sql=f"""
                    SELECT pt.name->>'en_US' AS name, COUNT(*) AS value
                    FROM sale_order_line sol
                    JOIN sale_order so ON so.id = sol.order_id
                    JOIN product_product pp ON pp.id = sol.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE so.state = 'cancel' AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY pt.name
                """,
            ),
        ],
    ),
    "revenue": MetricDef(
        id="revenue",
        label="Revenue",
        unit="currency",
        direction="higher_is_good",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month, SUM(amount_total) AS numerator
            FROM sale_order WHERE state = 'sale' AND store_id IN {CELESTE_STORE_IDS} GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="product",
                label="Product",
                mode="contribution",
                sql=f"""
                    SELECT pt.name->>'en_US' AS name, SUM(sol.price_subtotal) AS value
                    FROM sale_order_line sol
                    JOIN sale_order so ON so.id = sol.order_id
                    JOIN product_product pp ON pp.id = sol.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY pt.name
                """,
            ),
            Dimension(
                id="customer",
                label="Customer",
                mode="contribution",
                sql=f"""
                    SELECT rp.name AS name, SUM(so.amount_total) AS value
                    FROM sale_order so JOIN res_partner rp ON rp.id = so.partner_id
                    WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rp.name
                """,
            ),
            Dimension(
                id="store",
                label="Store",
                mode="contribution",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name, SUM(so.amount_total) AS value
                    FROM sale_order so LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
                """,
            ),
        ],
    ),
    "order_volume": MetricDef(
        id="order_volume",
        label="Confirmed order volume",
        unit="count",
        direction="higher_is_good",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month, COUNT(*) AS numerator
            FROM sale_order WHERE state = 'sale' AND store_id IN {CELESTE_STORE_IDS} GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="contribution",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name, COUNT(*) AS value
                    FROM sale_order so LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
                """,
            ),
            Dimension(
                id="customer",
                label="Customer",
                mode="contribution",
                sql=f"""
                    SELECT rp.name AS name, COUNT(*) AS value
                    FROM sale_order so JOIN res_partner rp ON rp.id = so.partner_id
                    WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rp.name
                """,
            ),
        ],
    ),
    "delayed_delivery_rate": MetricDef(
        id="delayed_delivery_rate",
        label="Severely delayed delivery rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        # Delay is measured against the picking's scheduled_date, but the typical gap
        # between scheduled and done is only ~2-3 minutes (a processing-queue artifact,
        # not customer-facing lateness) — so only gaps over 24h count as "delayed".
        # A bare "done after scheduled" definition would flag ~98% of all deliveries.
        trend_sql=f"""
            SELECT date_trunc('month', sp.date_done) AS month,
                   COUNT(*) FILTER (WHERE sp.date_done - sp.scheduled_date > INTERVAL '24 hours') AS numerator,
                   COUNT(*) AS denominator
            FROM stock_picking sp
            JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
            JOIN sale_order so ON so.id = sp.sale_id
            WHERE spt.code = 'outgoing' AND sp.state = 'done' AND sp.date_done IS NOT NULL
              AND so.store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="rate",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name,
                           COUNT(*) FILTER (WHERE sp.date_done - sp.scheduled_date > INTERVAL '24 hours') AS numerator,
                           COUNT(*) AS denominator
                    FROM stock_picking sp
                    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                    JOIN sale_order so ON so.id = sp.sale_id
                    LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE spt.code = 'outgoing' AND sp.state = 'done' AND sp.date_done IS NOT NULL
                      AND so.store_id IN {CELESTE_STORE_IDS}
                      AND sp.date_done >= :start AND sp.date_done < :end
                    GROUP BY rs.name
                """,
            ),
        ],
        notes="Delay measured against the picking's scheduled_date; only gaps over 24h count "
        "as 'delayed' — the typical processing gap is a few minutes and isn't meaningful lateness.",
    ),
    "customer_retention_rate": MetricDef(
        id="customer_retention_rate",
        label="Customer retention rate",
        unit="percent",
        direction="higher_is_good",
        is_rate=True,
        trend_sql=f"""
            WITH monthly_active AS (
                SELECT date_trunc('month', date_order) AS month, partner_id
                FROM sale_order WHERE state = 'sale' AND store_id IN {CELESTE_STORE_IDS}
                GROUP BY 1, 2
            ),
            bounds AS (SELECT MAX(month) AS max_month FROM monthly_active)
            SELECT
                (prev.month + INTERVAL '1 month') AS month,
                COUNT(*) FILTER (WHERE cur.partner_id IS NOT NULL) AS numerator,
                COUNT(*) AS denominator
            FROM monthly_active prev
            CROSS JOIN bounds b
            LEFT JOIN monthly_active cur
                ON cur.partner_id = prev.partner_id
               AND cur.month = prev.month + INTERVAL '1 month'
            WHERE prev.month + INTERVAL '1 month' <= b.max_month
            GROUP BY prev.month
            ORDER BY 1
        """,
        notes="Retention = share of a month's active customers who placed at least one "
        "more order the following month. No dimension breakdown: a customer isn't "
        "reliably attributable to one store, so a by-store split would be misleading.",
    ),
    # NOTE: a gross-margin metric was considered (sale_order_line has product_cost /
    # gross_profit columns) but cost data is only ~66% populated and effectively
    # unbacked before ~June 2026 (Feb cost totals Rs 44.8K against Rs 5.7M revenue) —
    # registering it now would produce a confident-sounding but false "margin
    # collapsed" story. Add it once cost data is verified complete.
    #
    # NOTE: a workforce/picker-productivity metric was considered (stock_picking has a
    # picker_id column) but it is 0% populated (0 of 373,706 outgoing pickings), and
    # hr_attendance has only 4 rows, entirely outside the current data window (Nov
    # 2025-Jan 2026 vs. the business's Apr-Aug 2026 range). No workforce metric until
    # that data actually exists.
}


def list_metrics():
    return [
        {
            "id": m.id,
            "label": m.label,
            "unit": m.unit,
            "direction": m.direction,
            "dimensions": [{"id": d.id, "label": d.label} for d in m.dimensions],
            "notes": m.notes,
        }
        for m in METRICS.values()
    ]


def get_trend(metric: MetricDef):
    rows = reporting.run_query(metric.trend_sql)
    trend = []
    for i, r in enumerate(rows):
        key = _month_key(r["month"])
        numerator = float(r["numerator"] or 0)
        # The most recent month may not be fully elapsed — a later month
        # existing at all proves every earlier one already is, so only the
        # last row can possibly be partial. Anchoring to the data itself
        # (not date.today()) avoids a stale DB snapshot looking "complete"
        # once the wall clock drifts into a later month than the data has.
        entry = {"month": key, "is_partial": i == len(rows) - 1}
        if metric.is_rate:
            denom = float(r.get("denominator") or 0)
            entry["value"] = round((numerator / denom * 100) if denom else 0.0, 4)
            entry["numerator"] = numerator
            entry["denominator"] = denom
        else:
            entry["value"] = round(numerator, 4)
        trend.append(entry)
    return trend


def detect_anomaly(trend: list[dict], direction: Direction):
    complete = [m for m in trend if not m["is_partial"]]
    if len(complete) < 2:
        return {"is_anomalous": False, "verdict": "insufficient_data", "latest_month": None}

    latest = complete[-1]
    baseline_vals = [m["value"] for m in complete[:-1]]
    baseline_mean = mean(baseline_vals)
    baseline_sd = pstdev(baseline_vals) if len(baseline_vals) > 1 else 0.0

    pct_change = ((latest["value"] - baseline_mean) / baseline_mean * 100) if baseline_mean else None
    z_score = ((latest["value"] - baseline_mean) / baseline_sd) if baseline_sd else None
    moved_up = latest["value"] > baseline_mean

    is_significant = (pct_change is not None and abs(pct_change) >= ANOMALY_PCT_THRESHOLD) or (
        z_score is not None and abs(z_score) >= ANOMALY_Z_THRESHOLD
    )
    moved_bad = (direction == "higher_is_bad" and moved_up) or (direction == "higher_is_good" and not moved_up)
    moved_good = (direction == "higher_is_bad" and not moved_up) or (direction == "higher_is_good" and moved_up)

    # category drives the 🔴/🟢 framing: a significant move in the bad direction is
    # something to fix, a significant move in the good direction is worth explaining
    # too (an opportunity), and small moves are just noise.
    if is_significant and moved_bad:
        category = "critical"
    elif is_significant and moved_good:
        category = "opportunity"
    else:
        category = "normal"

    return {
        "is_anomalous": category == "critical",
        "category": category,
        "verdict": "anomalous" if category == "critical" else category,
        "latest_month": latest["month"],
        "latest_value": latest["value"],
        "baseline_mean": round(baseline_mean, 4),
        "pct_change_vs_baseline": round(pct_change, 2) if pct_change is not None else None,
        "z_score": round(z_score, 2) if z_score is not None else None,
        "moved_up": moved_up,
    }


def _rows_by_name(dim: Dimension, month_key: str) -> dict:
    start, end = _month_bounds(month_key)
    rows = reporting.run_query(dim.sql, {"start": start, "end": end})
    return {r["name"]: r for r in rows}


def get_breakdown(dim: Dimension, current_month: str, moved_up: bool):
    baseline_month = _shift_month(current_month, -1)
    current = _rows_by_name(dim, current_month)
    baseline = _rows_by_name(dim, baseline_month)

    names = set(current) | set(baseline)
    contributors = []

    for name in names:
        cur, base = current.get(name), baseline.get(name)

        if dim.mode == "rate":
            cur_num, cur_den = float(cur["numerator"] or 0) if cur else 0, float(cur["denominator"] or 0) if cur else 0
            base_num, base_den = float(base["numerator"] or 0) if base else 0, float(base["denominator"] or 0) if base else 0
            if cur_den < MIN_SEGMENT_VOLUME and base_den < MIN_SEGMENT_VOLUME:
                continue
            cur_val = (cur_num / cur_den * 100) if cur_den else 0.0
            base_val = (base_num / base_den * 100) if base_den else 0.0
            contributors.append({"name": name, "current": round(cur_val, 2), "baseline": round(base_val, 2),
                                  "delta": round(cur_val - base_val, 2), "current_volume": int(cur_den)})
        else:  # contribution or composition — both are plain summable values
            cur_val = float(cur["value"] or 0) if cur else 0.0
            base_val = float(base["value"] or 0) if base else 0.0
            contributors.append({"name": name, "current": round(cur_val, 2), "baseline": round(base_val, 2),
                                  "delta": round(cur_val - base_val, 2)})

    if dim.mode == "composition":
        cur_total = sum(c["current"] for c in contributors) or 1
        base_total = sum(c["baseline"] for c in contributors) or 1
        for c in contributors:
            c["current_share_pct"] = round(c["current"] / cur_total * 100, 1)
            c["baseline_share_pct"] = round(c["baseline"] / base_total * 100, 1)
            c["delta"] = round(c["current_share_pct"] - c["baseline_share_pct"], 1)
        contributors.sort(key=lambda c: c["delta"], reverse=True)
    else:
        contributors.sort(key=lambda c: c["delta"], reverse=moved_up)

    return {
        "dimension": dim.id,
        "label": dim.label,
        "mode": dim.mode,
        "baseline_month": baseline_month,
        "top_contributors": contributors[:TOP_N],
    }


def investigate(metric_id: str):
    metric = METRICS.get(metric_id)
    if metric is None:
        return {"error": f"Unknown metric '{metric_id}'. Call list_metrics for available options."}

    trend = get_trend(metric)
    anomaly = detect_anomaly(trend, metric.direction)

    breakdowns = []
    if anomaly["latest_month"]:
        breakdowns = [
            get_breakdown(dim, anomaly["latest_month"], anomaly["moved_up"]) for dim in metric.dimensions
        ]

    return {
        "metric": {"id": metric.id, "label": metric.label, "unit": metric.unit, "direction": metric.direction},
        "trend": trend,
        "anomaly": anomaly,
        "breakdowns": breakdowns,
        "notes": metric.notes,
    }


def scan_attention():
    """Run every registered metric's anomaly check in one pass and return only
    the ones flagged critical or opportunity, each with its single strongest
    driver. This is the load-bearing piece behind "what needs my attention" /
    "executive summary" style questions — without it, answering those means
    the model calling investigate_metric once per registered metric, which
    both costs several tool-call rounds and returns far more detail (full
    trend + every dimension's full breakdown) than a scan needs.
    """
    flagged = []
    for metric in METRICS.values():
        trend = get_trend(metric)
        anomaly = detect_anomaly(trend, metric.direction)
        if anomaly["category"] not in ("critical", "opportunity"):
            continue

        top_driver = None
        for dim in metric.dimensions:
            breakdown = get_breakdown(dim, anomaly["latest_month"], anomaly["moved_up"])
            if breakdown["top_contributors"]:
                top = breakdown["top_contributors"][0]
                top_driver = {"dimension": breakdown["label"], "name": top["name"], "delta": top["delta"]}
                break

        flagged.append(
            {
                "metric_id": metric.id,
                "label": metric.label,
                "unit": metric.unit,
                "category": anomaly["category"],
                "latest_month": anomaly["latest_month"],
                "latest_value": anomaly["latest_value"],
                "pct_change_vs_baseline": anomaly["pct_change_vs_baseline"],
                "top_driver": top_driver,
            }
        )

    flagged.sort(key=lambda r: (r["category"] != "critical", -abs(r["pct_change_vs_baseline"] or 0)))

    return {
        "flagged": flagged,
        "metrics_checked": len(METRICS),
        "metrics_normal": len(METRICS) - len(flagged),
    }

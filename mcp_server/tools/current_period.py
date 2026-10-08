"""Provisional, equal-elapsed-period assessments alongside closed-month cases.

These are read-only observations. They never enter case resolution or action
outcome tracking. Each source supplies its own cutoff; wall-clock today is not
evidence that a reporting table was refreshed.
"""
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from threading import Lock
from time import monotonic

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools import intelligence

logger = logging.getLogger(__name__)
SCOPED_SALES = f"state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}"

# Raw facts, not averages of daily averages. Aggregation preserves each metric's
# existing numerator/denominator, inclusion rules and business scope.
SOURCES = {
    "revenue": f"SELECT date_order AS occurred_at, amount_total AS n, 1 AS d, partner_id AS entity FROM sale_order WHERE {SCOPED_SALES}",
    "order_volume": f"SELECT date_order AS occurred_at, 1 AS n, 1 AS d, partner_id AS entity FROM sale_order WHERE {SCOPED_SALES}",
    "average_order_value": f"SELECT date_order AS occurred_at, amount_total AS n, 1 AS d, partner_id AS entity FROM sale_order WHERE {SCOPED_SALES}",
    "customer_retention_rate": f"SELECT date_order AS occurred_at, 1 AS n, 1 AS d, partner_id AS entity FROM sale_order WHERE {SCOPED_SALES} AND partner_id IS NOT NULL",
    "cancellation_rate": f"""SELECT date_order AS occurred_at,
        CASE WHEN state='cancel' THEN 1 ELSE 0 END AS n, 1 AS d, partner_id AS entity
        FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES + ('cancel',)} AND store_id IN {CELESTE_STORE_IDS}""",
    "stockout_cancellation_rate": f"""SELECT date_order AS occurred_at,
        CASE WHEN state='cancel' AND cancel_info='Customer Request due to Out of Stock' THEN 1 ELSE 0 END AS n,
        1 AS d, partner_id AS entity
        FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES + ('cancel',)} AND store_id IN {CELESTE_STORE_IDS}""",
    "delayed_delivery_rate": f"""SELECT sp.date_done AS occurred_at,
        CASE WHEN sp.date_done-sp.scheduled_date > INTERVAL '24 hours' THEN 1 ELSE 0 END AS n,
        1 AS d, so.partner_id AS entity
        FROM stock_picking sp JOIN stock_picking_type spt ON spt.id=sp.picking_type_id
        JOIN sale_order so ON so.id=sp.sale_id
        WHERE spt.code='outgoing' AND sp.state='done' AND sp.date_done IS NOT NULL
        AND so.store_id IN {CELESTE_STORE_IDS}""",
    "uber_failure_rate": f"""SELECT create_date AS occurred_at,
        CASE WHEN state='failed' THEN 1 ELSE 0 END AS n,
        CASE WHEN state IN ('failed','completed','cancelled','accepted') THEN 1 ELSE 0 END AS d,
        store_id AS entity FROM uber_order WHERE store_id IN {CELESTE_STORE_IDS}""",
    "pickme_cancellation_rate": f"""SELECT create_date AS occurred_at,
        CASE WHEN state IN ('cancelled','failed','rejected') THEN 1 ELSE 0 END AS n,
        CASE WHEN state IN ('cancelled','failed','rejected','completed') THEN 1 ELSE 0 END AS d,
        store_id AS entity FROM pickme_order WHERE store_id IN {CELESTE_STORE_IDS}""",
    "gross_margin": f"""SELECT so.date_order AS occurred_at,
        CASE WHEN pt.celeste_cost_price_ex_vat > 0 THEN sol.price_subtotal-sol.product_uom_qty*pt.celeste_cost_price_ex_vat END AS n,
        CASE WHEN pt.celeste_cost_price_ex_vat > 0 THEN sol.price_subtotal END AS d,
        so.partner_id AS entity FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
        JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}""",
    # Mean, not the median intelligence.py's own trend_sql deliberately uses
    # for this metric (a real outlier -- one bad timestamp -- once distorted
    # a whole month's average to 4,670 minutes; see intelligence.METRICS'
    # own dispatch_wait_minutes notes). window_sql()'s generic engine only
    # supports SUM-based aggregation, so a median isn't available here the
    # same way -- the < 1440-minute (24h) bound is the guard instead, same
    # reasoning delayed_delivery_rate already uses for its own 24h cutoff: a
    # real dispatch wait past a full day is implausible for this business,
    # far more likely a bad timestamp than a genuine observation.
    "dispatch_wait_minutes": f"""SELECT so.dispatch_datetime AS occurred_at,
        EXTRACT(EPOCH FROM (so.dispatch_datetime - so.packing_datetime))/60 AS n, 1 AS d, so.partner_id AS entity
        FROM sale_order so WHERE {SCOPED_SALES}
          AND so.dispatch_datetime IS NOT NULL AND so.packing_datetime IS NOT NULL
          AND so.dispatch_datetime >= so.packing_datetime
          AND so.dispatch_datetime < so.packing_datetime + INTERVAL '1440 minutes'""",
    "supplier_fill_rate": "SELECT date AS occurred_at, qty_on_time AS n, qty_total AS d, partner_id AS entity FROM vendor_delay_report",
    "purchase_price_variance": """SELECT date_order AS occurred_at,
        CASE WHEN price_difference_percent != 0 THEN 1 ELSE 0 END AS n,
        1 AS d, partner_id AS entity FROM purchase_order
        WHERE state='purchase' AND price_difference_percent IS NOT NULL""",
    "supplier_concentration": "SELECT date_order AS occurred_at, amount_total AS n, 1 AS d, partner_id AS entity FROM purchase_order WHERE state='purchase'",
}


@lru_cache
def _read_engine():
    """Bound the new multi-source request even when reporting is unreachable."""
    url = reporting.get_engine().url
    if url.get_backend_name() == "postgresql":
        return create_engine(url, connect_args={"connect_timeout": 5},
                             pool_size=4, max_overflow=0, pool_timeout=5)
    return create_engine(url)


def _read_rows(sql):
    with _read_engine().connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '10000'"))
        return [dict(row._mapping) for row in connection.execute(text(sql))]


def window_sql(metric_id):
    # Only registry-owned SQL is interpolated. No identifier or SQL supplied by
    # a request is accepted. The latest record is included via an exclusive end
    # one microsecond later; earlier windows use the identical elapsed interval.
    source = SOURCES[metric_id]
    prefix = f"""WITH source AS ({source}),
        bounds AS (SELECT MAX(occurred_at)::timestamp AS as_of FROM source),
        months AS (SELECT DISTINCT date_trunc('month', occurred_at) AS start FROM source WHERE occurred_at IS NOT NULL),
        windows AS (
            SELECT start, as_of,
                LEAST(start + (as_of + INTERVAL '1 microsecond' - date_trunc('month', as_of)),
                      start + INTERVAL '1 month') AS finish,
                start + (as_of + INTERVAL '1 microsecond' - date_trunc('month', as_of))
                    <= start + INTERVAL '1 month' AS comparable
            FROM months CROSS JOIN bounds
        )"""
    if metric_id == "customer_retention_rate":
        return prefix + """,
            cohorts AS (SELECT date_trunc('month',occurred_at) AS month, entity,
                               MIN(occurred_at) AS first_seen FROM source GROUP BY 1,2),
            returning_customers AS (SELECT w.start, s.entity FROM windows w JOIN cohorts s
                                    ON s.month=w.start AND s.first_seen<w.finish)
            SELECT w.start, w.finish, w.as_of, w.comparable,
                   COUNT(r.entity) AS numerator, COUNT(p.entity) AS denominator
            FROM windows w LEFT JOIN cohorts p ON p.month=w.start-INTERVAL '1 month'
            LEFT JOIN returning_customers r ON r.start=w.start AND r.entity=p.entity
            GROUP BY w.start,w.finish,w.as_of,w.comparable ORDER BY w.start"""
    if metric_id == "supplier_concentration":
        return prefix + """, spend AS (
            SELECT w.start,w.finish,w.as_of,w.comparable,s.entity,SUM(s.n) AS value
            FROM windows w JOIN source s ON date_trunc('month',s.occurred_at)=w.start
                AND s.occurred_at>=w.start AND s.occurred_at<w.finish
            GROUP BY w.start,w.finish,w.as_of,w.comparable,s.entity)
            SELECT start,finish,as_of,comparable,MAX(value) AS numerator,SUM(value) AS denominator
            FROM spend GROUP BY start,finish,as_of,comparable ORDER BY start"""
    return prefix + """, totals AS (
        SELECT date_trunc('month',s.occurred_at) AS month,
               SUM(s.n) AS numerator,SUM(s.d) AS denominator
        FROM source s CROSS JOIN bounds b
        WHERE s.occurred_at < date_trunc('month',s.occurred_at)
            + (b.as_of + INTERVAL '1 microsecond' - date_trunc('month',b.as_of))
        GROUP BY 1)
        SELECT w.start,w.finish,w.as_of,w.comparable,t.numerator,t.denominator
        FROM windows w LEFT JOIN totals t ON t.month=w.start ORDER BY w.start"""


def _metric_meta(metric):
    return {"id": metric.id, "label": metric.label, "unit": metric.unit,
            "direction": metric.direction, "notes": metric.notes}


def assess(metric_id):
    metric = intelligence.METRICS[metric_id]
    result = {"metric_id": metric_id, "metric": _metric_meta(metric), "horizon": "month_to_date",
              "provisional": True, "category": None, "status": "unavailable", "trend": [],
              "scope": "Business-wide purchasing" if metric_id.startswith(("supplier_", "purchase_")) else "Celeste outlets"}
    if metric_id not in SOURCES:
        # A deliberate, honest gap, not a connectivity failure -- return_rate
        # (added 2026-09-29) is the first metric where numerator and
        # denominator genuinely can't share one per-row source: return_order
        # has no direct sale_order link in this schema (only a warehouse-
        # routing path), so this engine's per-row SUM(n)/SUM(d) model can't
        # express it without fabricating a join that isn't really there.
        # Reusing "unavailable" (an already-handled status) rather than a
        # new enum value the frontend hasn't been taught yet -- but with an
        # honest reason, not the misleading "database unreachable" text a
        # bare KeyError from window_sql() would otherwise produce.
        return {**result, "reason": "Month-to-date estimation isn't available for this metric -- "
                "its numerator and denominator come from sources with no direct per-record link."}
    try:
        rows = _read_rows(window_sql(metric_id))
    except PoolTimeoutError:
        logger.warning("Current-period connection pool busy: %s", metric_id)
        return {**result, "failure_kind": "pool_timeout",
                "reason": "The reporting connections are busy. Retry this measurement shortly."}
    except DBAPIError as error:
        cancelled = getattr(error.orig, "sqlstate", None) == "57014"
        kind = "query_timeout" if cancelled else "database_error"
        logger.warning("Current-period measurement unavailable: %s (%s)", metric_id, kind)
        return {**result, "failure_kind": kind,
                "reason": "The measurement exceeded its query time limit. Retry this measurement shortly." if cancelled
                else "The reporting database could not complete this measurement. Retry shortly."}
    except Exception:
        logger.exception("Current-period measurement failed: %s", metric_id)
        return {**result, "reason": "Source measurement unavailable. Retry when the reporting database is reachable."}
    if not rows:
        return {**result, "reason": "No dated records are available for this metric."}
    points = []
    for row in rows:
        numerator, denominator = row["numerator"], row["denominator"]
        ratio = metric.is_rate or metric_id in ("average_order_value", "dispatch_wait_minutes")
        value = (float(numerator or 0) / float(denominator) * (100 if metric.is_rate else 1)
                 if ratio and denominator and denominator > 0
                 else None if ratio or numerator is None else float(numerator))
        points.append({"month": row["start"].strftime("%Y-%m"), "value": value,
                       "denominator": float(denominator or 0), "is_partial": False,
                       "start": row["start"].isoformat(), "end_exclusive": row["finish"].isoformat(),
                       "comparable": bool(row["comparable"])})
    current = points[-1]
    eligible = [p for p in points if p["comparable"] and p["value"] is not None]
    anomaly = (intelligence.detect_anomaly(eligible, metric.direction, metric=metric)
               if current["value"] is not None else {})
    as_of = rows[-1]["as_of"]
    age = datetime.now(UTC) - (as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of)
    status = "measured" if anomaly.get("latest_month") == current["month"] else "insufficient_data"
    baseline = eligible[:-1] if current in eligible else eligible
    return {**result, "status": status, "category": anomaly.get("category"),
            "reason": ("The source contains a future timestamp; check its date quality." if age < timedelta(0)
                       else "More comparable history or valid denominator data is needed." if status != "measured"
                       else "No zero-baseline activation rule is configured for this metric." if anomaly.get("verdict") == "activation_rule_required"
                       else None),
            "source_data_through": as_of.isoformat(), "freshness": "unknown" if age < timedelta(0) else "stale" if age > timedelta(days=1) else "fresh",
            "comparison": {"start": current["start"], "end_exclusive": current["end_exclusive"],
                           "baseline_windows": [{"start": p["start"], "end_exclusive": p["end_exclusive"]} for p in baseline],
                           "excluded_short_months": [p["month"] for p in points if not p["comparable"]],
                           "method": "Mean of earlier months over matching elapsed durations; shorter months are excluded."},
            "confirm": {"period": current["month"], "current": current["value"],
                        "baseline": anomaly.get("baseline_mean"), "pct_change": anomaly.get("pct_change_vs_baseline"), "unit": metric.unit},
            "trend": eligible,
            "cohort_note": "Returning customers to date divided by the previous full month's customer cohort; earlier comparisons use the same elapsed duration." if metric_id == "customer_retention_rate" else None}


def _compute_all():
    # Initialize the cached engine before worker threads race on its first use.
    _read_engine()
    # Large historical joins contend for database memory/CPU. Two queries at
    # once leave pool headroom and reduce contention on the reporting server.
    with ThreadPoolExecutor(max_workers=2) as executor:
        items = list(executor.map(assess, intelligence.METRICS))
    order = {"critical": 0, "opportunity": 1, None: 2, "normal": 3}
    items.sort(key=lambda item: order[item["category"]])
    return {"status": "ok", "checked_at": datetime.now(UTC).isoformat(), "cache_seconds": 300,
            "items": items}


_guard = Lock()
_inflight = None
_cached = None
_expires = 0


def assess_all(*, refresh=False):
    """Share one run across overlapping reads AND explicit refresh requests."""
    global _inflight, _cached, _expires
    with _guard:
        if _inflight is not None:
            future, owner = _inflight, False
        elif not refresh and _cached is not None and monotonic() < _expires:
            return _cached
        else:
            future = _inflight = Future()
            owner = True
    if not owner:
        return future.result()
    try:
        result = _compute_all()
    except BaseException as error:
        with _guard:
            _inflight = None
            future.set_exception(error)
        raise
    with _guard:
        _cached, _expires = result, monotonic() + 300
        _inflight = None
        future.set_result(result)
    return result


def _clear_cache():
    global _cached, _expires
    with _guard:
        _cached, _expires = None, 0


assess_all.cache_clear = _clear_cache

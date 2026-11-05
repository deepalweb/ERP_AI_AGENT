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
from datetime import date, timedelta
from statistics import mean, pstdev
from typing import Literal, Optional

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

Direction = Literal["higher_is_bad", "higher_is_good"]
# "rate" = numerator/denominator*100, for percentage metrics (e.g. cancellation
# rate by store). "ratio" = numerator/denominator with no multiplier, for a
# per-unit currency/count value (e.g. average order value by store) — same
# numerator/denominator SQL shape as "rate", just not a percentage.
DimMode = Literal["contribution", "rate", "ratio", "composition"]

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
    unit: Literal["percent", "currency", "count", "minutes"]
    direction: Direction
    trend_sql: str  # (month, numerator[, denominator]) across full history
    is_rate: bool = False
    dimensions: list[Dimension] = field(default_factory=list)
    notes: Optional[str] = None
    # Trailing 7-day-window counterpart to trend_sql — see get_trailing_trend().
    # Deliberately a separate, explicit query (not a date_trunc('month'->'week')
    # swap): a trailing window buckets by days-ago from the data's own latest
    # point via a CTE, which is a genuinely different query shape. Only set for
    # metrics registered in investigation.SHORT_TERM_METRICS. Must return rows
    # keyed by periods_ago (0 = current window) plus numerator[, denominator]
    # and an as_of column (same on every row).
    short_term_trend_sql: Optional[str] = None
    # If set, a "excess events" impact estimate can be translated into a business
    # quantity — currently only "lost_orders" (paired with the revenue/order_volume
    # metrics to price the excess). None means investigation.py skips impact estimation
    # for this metric rather than guessing a unit.
    impact_kind: Optional[Literal["lost_orders"]] = None
    # Which metrics this one is NOT statistically independent of, for
    # evidence_engine.cross_metric_corroboration_checks. Two metrics sharing
    # a group means they're arithmetically/definitionally linked (same base
    # rows, or one is mechanically derived from another) — not just
    # correlated in practice. Left unset (empty string), a metric defaults
    # to being its own group, i.e. independent of everything else by
    # default; only metrics actually inspected and found to share a formula
    # or base population get grouped together. This is deliberately a small,
    # explicit safeguard against double-counting dependent metrics as
    # corroboration — not a general statistical independence model (no
    # covariance estimation, no confound detection). See that function's
    # docstring for exactly how it's used.
    independence_group: str = ""
    # Conservative gate on independence_group's own default: a metric
    # defaults to independence_group="" (independent of everything) purely
    # because nobody has grouped it, which is the WRONG default for a
    # brand-new, never-inspected metric — it would immediately start
    # corroborating other findings without anyone having checked whether it
    # actually shares a formula or base table with them. independence_
    # reviewed=False means "nobody has verified this metric's independence
    # relationships one way or the other yet" — evidence_engine.
    # cross_metric_corroboration_checks refuses to let an unreviewed metric
    # strengthen (or contradict) anything until this is flipped to True by
    # someone who has actually inspected its SQL, same posture as the
    # independence_group backfill below.
    independence_reviewed: bool = False
    # A declared, opt-in expectation about how this metric's movement
    # relates to a SPECIFIC other metric's, keyed by the other metric's id:
    # "same_direction" (both improving or both worsening together is the
    # expected/consistent pattern) or "opposite_direction" (one improving
    # while the other worsens is the expected/consistent pattern — e.g. a
    # mechanical trade-off). Deliberately sparse and manually declared, not
    # inferred: most metric pairs have no known relationship at all, and
    # evidence_engine must not manufacture a "conflicting signal" claim for
    # an undeclared pair just because their categories happen to differ —
    # see that function's docstring.
    expected_relationship: dict[str, str] = field(default_factory=dict)
    zero_baseline_threshold: float | None = None
    zero_baseline_min_exposure: int = 15


METRICS: dict[str, MetricDef] = {
    "cancellation_rate": MetricDef(
        id="cancellation_rate",
        zero_baseline_threshold=5.0,
        # Every metric below is marked reviewed=True as a one-time backfill:
        # each one's SQL was read end-to-end (this file, in full) to decide
        # independence_group membership, so "reviewed" is already true for
        # all of them, not a default being waived. A metric added later
        # starts unreviewed (the dataclass default) until someone does the
        # same inspection for it.
        independence_reviewed=True,
        label="Order cancellation rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        impact_kind="lost_orders",
        # Same base sale_order rows as revenue/order_volume/average_order_value
        # (cancel + the live states from the same table), and mechanically
        # trades off against order_volume by definition — a cancelled order
        # is excluded from order_volume's own SALE_ORDER_LIVE_STATES filter.
        # See independence_group's own docstring.
        independence_group="order_funnel",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month,
                   COUNT(*) FILTER (WHERE state = 'cancel') AS numerator,
                   COUNT(*) AS denominator
            FROM sale_order
            WHERE state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        short_term_trend_sql=f"""
            WITH bounds AS (
                SELECT MAX(date_order) AS as_of FROM sale_order
                WHERE state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND store_id IN {CELESTE_STORE_IDS}
            ),
            bucketed AS (
                SELECT so.state,
                       FLOOR(EXTRACT(EPOCH FROM (b.as_of - so.date_order)) / (7*86400))::int AS periods_ago,
                       b.as_of
                FROM sale_order so CROSS JOIN bounds b
                WHERE so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND so.store_id IN {CELESTE_STORE_IDS}
                  AND so.date_order >= b.as_of - INTERVAL '63 days'
            )
            SELECT periods_ago, MAX(as_of) AS as_of,
                   COUNT(*) FILTER (WHERE state = 'cancel') AS numerator, COUNT(*) AS denominator
            FROM bucketed GROUP BY periods_ago ORDER BY periods_ago
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
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND so.store_id IN {CELESTE_STORE_IDS}
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
    "stockout_cancellation_rate": MetricDef(
        id="stockout_cancellation_rate",
        zero_baseline_threshold=5.0,
        independence_reviewed=True,
        label="Out-of-stock cancellation rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        impact_kind="lost_orders",
        # Same base sale_order rows as cancellation_rate -- this is a SUBSET
        # of it (only the cancel_info='Customer Request due to Out of
        # Stock' cause), never independent corroboration of it.
        independence_group="order_funnel",
        # Outlet-operations investigation (2026-09-29): cancel_reason (what
        # cancellation_rate's own "reason" breakdown above already uses) has
        # essentially one real value in this data ("ITEM_ISSUE" on
        # everything) -- cancel_info is the genuinely granular field, and
        # checked live before registering this: "Customer Request due to
        # Out of Stock" alone is 3,862 cancellations, by far the single
        # largest cancellation cause in the whole business, bigger than
        # every other reason combined. cancellation_rate's own blended
        # number can't isolate this; a manager reading it has no way to
        # tell how much of it is stock-outs specifically.
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month,
                   COUNT(*) FILTER (WHERE state = 'cancel' AND cancel_info = 'Customer Request due to Out of Stock') AS numerator,
                   COUNT(*) AS denominator
            FROM sale_order
            WHERE state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="rate",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name,
                           COUNT(*) FILTER (WHERE so.state = 'cancel' AND so.cancel_info = 'Customer Request due to Out of Stock') AS numerator,
                           COUNT(*) AS denominator
                    FROM sale_order so
                    LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
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
                    WHERE so.state = 'cancel' AND so.cancel_info = 'Customer Request due to Out of Stock'
                      AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY pt.name
                """,
            ),
        ],
        notes="Scoped to the single cancel_info value 'Customer Request due to Out of Stock' -- "
        "the customer's own stated reason, not an inference. Domain-tagged as inventory, not "
        "order_customer like cancellation_rate: the fix for this lives in stock availability, "
        "not order handling.",
    ),
    "return_rate": MetricDef(
        id="return_rate",
        zero_baseline_threshold=5.0,
        independence_reviewed=True,
        label="Customer return rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        # Same order-funnel population as cancellation_rate/order_volume for
        # its denominator -- not independent corroboration of them.
        independence_group="order_funnel",
        # Outlet-operations investigation (2026-09-29): checked live before
        # registering -- non-supplier returns have grown every single month
        # (11 -> 55 -> 131 -> 191 -> 221 -> 219), a real, worsening,
        # company-wide trend, not noise. return_order has no direct
        # sale_order link (it's warehouse-side, via operation_type_id ->
        # stock_picking_type -> warehouse_id -> res_store, the same
        # multi-brand-per-warehouse fan-out documented in loss_and_damage.py)
        # -- rate is computed against that same month/store's total
        # confirmed order count, not a per-order join.
        trend_sql=f"""
            WITH orders AS (
                SELECT date_trunc('month', date_order) AS month, COUNT(*) AS total
                FROM sale_order
                WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
                GROUP BY 1
            ),
            returns AS (
                SELECT date_trunc('month', ro.date) AS month, COUNT(DISTINCT ro.id) AS n
                FROM return_order ro
                JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
                JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
                WHERE rs.id IN {CELESTE_STORE_IDS} AND NOT ro.is_supplier_return
                GROUP BY 1
            )
            SELECT o.month AS month, COALESCE(r.n, 0) AS numerator, o.total AS denominator
            FROM orders o LEFT JOIN returns r ON r.month = o.month
            ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="rate",
                sql=f"""
                    WITH orders AS (
                        SELECT rs.name AS outlet, COUNT(*) AS total
                        FROM sale_order so LEFT JOIN res_store rs ON rs.id = so.store_id
                        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
                          AND so.date_order >= :start AND so.date_order < :end
                        GROUP BY rs.name
                    ),
                    returns AS (
                        SELECT rs.name AS outlet, COUNT(DISTINCT ro.id) AS n
                        FROM return_order ro
                        JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
                        JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
                        WHERE rs.id IN {CELESTE_STORE_IDS} AND NOT ro.is_supplier_return
                          AND ro.date >= :start AND ro.date < :end
                        GROUP BY rs.name
                    )
                    SELECT o.outlet AS name, COALESCE(r.n, 0) AS numerator, o.total AS denominator
                    FROM orders o LEFT JOIN returns r ON r.outlet = o.outlet
                """,
            ),
            # 2026-10-01: return_order itself has no reason/cause field anywhere in
            # this schema (checked live) -- only return_order_line.product_id, so
            # "by reason" isn't buildable, only "by product". Contribution (a raw
            # line count per product), not rate -- there's no clean per-product
            # order-count denominator the way the store dimension has one.
            Dimension(
                id="product",
                label="Product",
                mode="contribution",
                sql=f"""
                    SELECT pt.name->>'en_US' AS name, COUNT(*) AS value
                    FROM return_order_line rol
                    JOIN return_order ro ON ro.id = rol.order_id
                    JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
                    JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
                    JOIN product_product pp ON pp.id = rol.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE rs.id IN {CELESTE_STORE_IDS} AND NOT ro.is_supplier_return
                      AND ro.date >= :start AND ro.date < :end
                    GROUP BY pt.name
                """,
            ),
        ],
        notes="Non-supplier returns (91% of all returns in this data) only -- excludes returns to "
        "a supplier, which are a purchasing concern, not a customer/fulfillment one. Rate is "
        "against that period's total confirmed order count, not a per-order match to a specific "
        "return (return_order has no direct sale_order link in this schema). The product "
        "breakdown is a count of return lines, not a return rate per product -- there's no "
        "reason/cause field recorded anywhere in this schema to break down by.",
    ),
    "revenue": MetricDef(
        id="revenue",
        independence_reviewed=True,
        label="Revenue",
        unit="currency",
        direction="higher_is_good",
        # SUM(amount_total) over the exact same sale_order rows order_volume
        # COUNTs and average_order_value AVGs -- revenue ~= order_volume x
        # AOV by construction, not empirical correlation.
        independence_group="order_funnel",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month, SUM(amount_total) AS numerator
            FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS} GROUP BY 1 ORDER BY 1
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
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
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
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
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
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
                """,
            ),
        ],
    ),
    "order_volume": MetricDef(
        id="order_volume",
        zero_baseline_threshold=15,
        independence_reviewed=True,
        label="Confirmed order volume",
        unit="count",
        direction="higher_is_good",
        independence_group="order_funnel",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month, COUNT(*) AS numerator
            FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS} GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="contribution",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name, COUNT(*) AS value
                    FROM sale_order so LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
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
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rp.name
                """,
            ),
        ],
    ),
    "delayed_delivery_rate": MetricDef(
        id="delayed_delivery_rate",
        zero_baseline_threshold=5.0,
        independence_reviewed=True,
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
        short_term_trend_sql=f"""
            WITH bounds AS (
                SELECT MAX(sp.date_done) AS as_of
                FROM stock_picking sp
                JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                JOIN sale_order so ON so.id = sp.sale_id
                WHERE spt.code = 'outgoing' AND sp.state = 'done' AND sp.date_done IS NOT NULL
                  AND so.store_id IN {CELESTE_STORE_IDS}
            ),
            bucketed AS (
                SELECT sp.date_done, sp.scheduled_date,
                       FLOOR(EXTRACT(EPOCH FROM (b.as_of - sp.date_done)) / (7*86400))::int AS periods_ago,
                       b.as_of
                FROM stock_picking sp
                JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                JOIN sale_order so ON so.id = sp.sale_id
                CROSS JOIN bounds b
                WHERE spt.code = 'outgoing' AND sp.state = 'done' AND sp.date_done IS NOT NULL
                  AND so.store_id IN {CELESTE_STORE_IDS}
                  AND sp.date_done >= b.as_of - INTERVAL '63 days'
            )
            SELECT periods_ago, MAX(as_of) AS as_of,
                   COUNT(*) FILTER (WHERE date_done - scheduled_date > INTERVAL '24 hours') AS numerator,
                   COUNT(*) AS denominator
            FROM bucketed GROUP BY periods_ago ORDER BY periods_ago
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
        # dispatch_wait_minutes (below) is mechanically derived from the same
        # stock_picking rows this metric reads -- a long dispatch wait is one
        # direct contributor to date_done landing late. Not independent
        # corroboration of each other; both sides of the pair are marked.
        independence_group="delivery_pipeline",
    ),
    "dispatch_wait_minutes": MetricDef(
        id="dispatch_wait_minutes",
        independence_reviewed=True,
        independence_group="delivery_pipeline",
        label="Average wait for dispatch after packing",
        unit="minutes",
        direction="higher_is_bad",
        # Outlet-operations investigation (2026-09-29): stage-timing data
        # already existed (operations.STAGE_TIMING_BY_STORE_SQL), but was
        # only ever computed reactively, as a "how" explanation attached
        # AFTER delayed_delivery_rate had already flagged a store -- never
        # its own tracked metric. Checked live before registering: dispatch
        # wait is 3-5x longer than picking+handoff+packing combined at every
        # outlet (5.8-7.9 real minutes vs. under 3 for the rest of the
        # pipeline) -- the dominant, currently-invisible-unless-something-
        # else-triggers-a-look delay stage, not a minor one.
        trend_sql=f"""
            SELECT date_trunc('month', so.dispatch_datetime) AS month,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (
                       ORDER BY EXTRACT(EPOCH FROM (so.dispatch_datetime - so.packing_datetime)) / 60
                   ) AS numerator
            FROM sale_order so
            WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
              AND so.dispatch_datetime IS NOT NULL AND so.packing_datetime IS NOT NULL
              AND so.dispatch_datetime >= so.packing_datetime
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                # median, not mean -- a handful of bad timestamps (a real
                # data-quality issue found while building this: one month's
                # average was distorted to 4,670 minutes by outliers, vs. a
                # real ~18-22 elsewhere) would otherwise swing an average
                # wildly. Same choice operations.STAGE_TIMING_BY_STORE_SQL
                # already made for the same underlying columns.
                mode="contribution",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name,
                           PERCENTILE_CONT(0.5) WITHIN GROUP (
                               ORDER BY EXTRACT(EPOCH FROM (so.dispatch_datetime - so.packing_datetime)) / 60
                           ) AS value
                    FROM sale_order so LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.dispatch_datetime IS NOT NULL AND so.packing_datetime IS NOT NULL
                      AND so.dispatch_datetime >= so.packing_datetime
                      AND so.dispatch_datetime >= :start AND so.dispatch_datetime < :end
                    GROUP BY rs.name
                """,
            ),
        ],
        notes="Time between an order finishing packing and actually leaving for delivery -- "
        "waiting on a rider/vehicle, not a picking or packing delay. Checked against real "
        "data before registering: this is the single largest stage in the fulfillment "
        "pipeline at every outlet, 3-5x picking+handoff+packing combined.",
    ),
    "uber_failure_rate": MetricDef(
        id="uber_failure_rate",
        zero_baseline_threshold=5.0,
        independence_reviewed=True,
        label="Uber order failure rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        impact_kind="lost_orders",
        # Denominator includes 'accepted' alongside 'completed'/'cancelled' —
        # checked live before shipping this and it mattered enormously:
        # 'completed' doesn't appear in the data at all before May 2026 (the
        # status convention changed partway through, almost certainly an
        # Uber-integration upgrade), so before that date every genuinely
        # successful order just stayed at 'accepted' forever. Confirmed via
        # the linked sale_order: 98.7% of ALL 'accepted' uber_order rows
        # (company-wide, every month) have sale_order.state = 'sale' — a
        # real completed sale, not something still in flight. Treating
        # 'accepted' as still-pending (the first version of this query)
        # produced a fake 98-99% "failure rate" for March/April 2026, which
        # would have shipped as a false two-month catastrophe; the real
        # rate for those months is ~10-11%, in line with the months next to
        # them. 'processing'/'scheduled' are excluded — genuinely tiny
        # (149 and 84 rows company-wide) and, unlike 'accepted', don't show
        # this pattern.
        trend_sql=f"""
            SELECT date_trunc('month', create_date) AS month,
                   COUNT(*) FILTER (WHERE state = 'failed') AS numerator,
                   COUNT(*) FILTER (WHERE state IN ('failed', 'completed', 'cancelled', 'accepted')) AS denominator
            FROM uber_order
            WHERE store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                # "contribution" (raw failed COUNT per store), not "rate" —
                # deliberately different from cancellation_rate's store
                # dimension: with only 5 known outlets of comparable scale
                # (not a high-cardinality dimension where one tiny store's
                # rate could swing wildly), a raw-count breakdown is both
                # meaningful AND usable by the root-cause concentration
                # check (see mcp_server/tools/evidence_engine.py) — a
                # rate-mode breakdown here would just report "unknown" for
                # no real reason.
                mode="contribution",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name, COUNT(*) AS value
                    FROM uber_order uo LEFT JOIN res_store rs ON rs.id = uo.store_id
                    WHERE uo.store_id IN {CELESTE_STORE_IDS} AND uo.state = 'failed'
                      AND uo.create_date >= :start AND uo.create_date < :end
                    GROUP BY rs.name
                """,
            ),
        ],
        notes="Scoped to Celeste's 5 own outlets, same as every other metric here — Uber/PickMe "
        "orders also flow through 22 partner-brand outlets (SPAR co-branded, Budget Kade, Red Bull, "
        "Fonterra, Nestle, UStore) that aren't part of Celeste's own retail business and aren't "
        "represented anywhere else in this app, so including them here would be inconsistent with "
        "the rest of the dashboard.",
    ),
    "pickme_cancellation_rate": MetricDef(
        id="pickme_cancellation_rate",
        zero_baseline_threshold=5.0,
        independence_reviewed=True,
        label="PickMe order cancellation rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        impact_kind="lost_orders",
        # 'rejected' folded into the numerator alongside 'cancelled'/'failed'
        # — all three mean the order didn't fulfill; checked live first
        # (rejected is a single row company-wide, doesn't skew anything).
        trend_sql=f"""
            SELECT date_trunc('month', create_date) AS month,
                   COUNT(*) FILTER (WHERE state IN ('cancelled', 'failed', 'rejected')) AS numerator,
                   COUNT(*) FILTER (WHERE state IN ('cancelled', 'failed', 'rejected', 'completed')) AS denominator
            FROM pickme_order
            WHERE store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="contribution",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name, COUNT(*) AS value
                    FROM pickme_order po LEFT JOIN res_store rs ON rs.id = po.store_id
                    WHERE po.store_id IN {CELESTE_STORE_IDS} AND po.state IN ('cancelled', 'failed', 'rejected')
                      AND po.create_date >= :start AND po.create_date < :end
                    GROUP BY rs.name
                """,
            ),
        ],
        notes="Scoped to Celeste's 5 own outlets, same as every other metric here — see "
        "uber_failure_rate's notes for why the wider partner-brand outlet network is excluded.",
    ),
    "return_rate": MetricDef(
        id="return_rate",
        independence_reviewed=True,
        label="Internal return rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        # Not "lost_orders" -- unlike a cancellation, a returned order DID
        # complete; there is no honest unit to translate an "excess return"
        # into here, so impact estimation is deliberately skipped (None).
        impact_kind=None,
        # Confirmed Odoo return_order states only -- same two-tier split
        # loss_and_damage.py's DAMAGE_CONFIRMED_STATES already uses:
        # 'draft'/'approval'/'to_dispatch'/'confirm' are not yet resolved;
        # 'done'/'refunded' are. is_supplier_return=false scopes this to
        # internal/customer returns, not supplier returns (out of scope,
        # same hold loss_and_damage.py's module docstring already applies).
        # Checked live (2026-11-03): return RATE (not just count) has
        # climbed roughly 33x from April (11 returns / 62,670 orders,
        # 0.018%) to October (520 / 88,337, 0.59%) -- genuinely
        # disproportionate to the ~1.4x order-volume growth over the same
        # months, not just "the business got bigger."
        trend_sql=f"""
            WITH returns AS (
                SELECT date_trunc('month', ro.date) AS month, COUNT(DISTINCT ro.id) AS n
                FROM return_order ro
                JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
                JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
                WHERE rs.id IN {CELESTE_STORE_IDS} AND ro.state IN ('done', 'refunded')
                  AND ro.is_supplier_return = false
                GROUP BY 1
            ),
            orders AS (
                SELECT date_trunc('month', date_order) AS month, COUNT(*) AS n
                FROM sale_order WHERE store_id IN {CELESTE_STORE_IDS} AND state IN {SALE_ORDER_LIVE_STATES}
                GROUP BY 1
            )
            SELECT o.month AS month, COALESCE(r.n, 0) AS numerator, o.n AS denominator
            FROM orders o LEFT JOIN returns r ON r.month = o.month
            ORDER BY o.month
        """,
        dimensions=[
            Dimension(
                id="store", label="Outlet", mode="contribution",
                sql=f"""
                    SELECT rs.name AS name, COUNT(DISTINCT ro.id) AS value
                    FROM return_order ro
                    JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
                    JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
                    WHERE rs.id IN {CELESTE_STORE_IDS} AND ro.state IN ('done', 'refunded')
                      AND ro.is_supplier_return = false
                      AND ro.date >= :start AND ro.date < :end
                    GROUP BY rs.name
                """,
            ),
            Dimension(
                # Role-wise accountability review, 2026-11-03: return_order.
                # responsible_id is real, individually-identifiable staff
                # (same people already found on damaged_order/stock_picking),
                # not a shared device login -- checked before adding this.
                id="person", label="Responsible", mode="contribution",
                sql=f"""
                    SELECT COALESCE(ru.login, 'Unassigned') AS name, COUNT(DISTINCT ro.id) AS value
                    FROM return_order ro
                    JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
                    JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
                    LEFT JOIN res_users ru ON ru.id = ro.responsible_id
                    WHERE rs.id IN {CELESTE_STORE_IDS} AND ro.state IN ('done', 'refunded')
                      AND ro.is_supplier_return = false
                      AND ro.date >= :start AND ro.date < :end
                    GROUP BY COALESCE(ru.login, 'Unassigned')
                """,
            ),
        ],
        notes="Internal/customer returns only (is_supplier_return=false) -- supplier returns are a "
        "purchasing-side signal, out of scope here per loss_and_damage.py's existing hold. Scoped to "
        "Celeste's 4 real outlets, same as every other metric here.",
    ),
    "customer_retention_rate": MetricDef(
        id="customer_retention_rate",
        independence_reviewed=True,
        label="Customer retention rate",
        unit="percent",
        direction="higher_is_good",
        is_rate=True,
        trend_sql=f"""
            WITH monthly_active AS (
                SELECT date_trunc('month', date_order) AS month, partner_id
                FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
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
    "gross_margin": MetricDef(
        id="gross_margin",
        independence_reviewed=True,
        # Metric Truthfulness Correction, Task 3: "Gross margin" implied a
        # realized historical figure. The numerator below applies TODAY's
        # catalog cost (product_template.celeste_cost_price_ex_vat, a single
        # current-value column, not a cost-history table) to every past
        # month's sales — so this is an estimate under current costs, not a
        # measured historical gross margin. Kept as the metric's own label
        # (not a Case-Detail-only disclaimer) so every surface that reads
        # metric.label — headlines, Investigate rows, the target-metric
        # picker, chat tools — carries the same corrected terminology from
        # one source, rather than some pages saying "Gross margin" and
        # others hedging it.
        label="Estimated gross margin (current-cost basis)",
        unit="percent",
        direction="higher_is_good",
        is_rate=True,
        trend_sql=f"""
            SELECT date_trunc('month', so.date_order) AS month,
                   SUM(sol.price_subtotal - sol.product_uom_qty * pt.celeste_cost_price_ex_vat)
                       FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS numerator,
                   SUM(sol.price_subtotal) FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS denominator
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            JOIN product_product pp ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="rate",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name,
                           SUM(sol.price_subtotal - sol.product_uom_qty * pt.celeste_cost_price_ex_vat)
                               FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS numerator,
                           SUM(sol.price_subtotal) FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS denominator
                    FROM sale_order_line sol
                    JOIN sale_order so ON so.id = sol.order_id
                    LEFT JOIN res_store rs ON rs.id = so.store_id
                    JOIN product_product pp ON pp.id = sol.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
                """,
            ),
        ],
        notes="Cost comes from product_template.celeste_cost_price_ex_vat — today's catalog cost, applied to "
        "every historical month alike, not a cost-history record. This figure does not establish realized "
        "historical gross margin; treat it as an estimate under current costs. Separately: cost-value "
        "COVERAGE is verified at 86-100% of revenue in every month of history (so the vast majority of "
        "revenue has SOME cost value to work with) — but coverage only means a cost value exists, never that "
        "it's the value that was actually true in that historical month. High coverage does not establish "
        "point-in-time accuracy. Store is the only dimension: a per-product breakdown would need a "
        "currency-sized volume threshold this engine's MIN_SEGMENT_VOLUME (built for order counts) doesn't "
        "give a meaningful one for, so it's deliberately not offered rather than risk a low-revenue, "
        "high-margin-swing product dominating the ranking.",
    ),
    "average_order_value": MetricDef(
        id="average_order_value",
        independence_reviewed=True,
        label="Average order value",
        unit="currency",
        direction="higher_is_good",
        independence_group="order_funnel",
        trend_sql=f"""
            SELECT date_trunc('month', date_order) AS month, AVG(amount_total) AS numerator
            FROM sale_order
            WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="store",
                label="Store",
                mode="ratio",
                sql=f"""
                    SELECT COALESCE(rs.name, 'Unknown store') AS name,
                           SUM(so.amount_total) AS numerator, COUNT(*) AS denominator
                    FROM sale_order so LEFT JOIN res_store rs ON rs.id = so.store_id
                    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
                      AND so.date_order >= :start AND so.date_order < :end
                    GROUP BY rs.name
                """,
            ),
        ],
        notes="Answers whether a revenue change is volume-driven or price-driven when "
        "read alongside order_volume. Only a per-store breakdown is offered — no "
        "per-product split, since AOV isn't attributable to a single line item the "
        "way a summable value like revenue is.",
    ),
    # NOTE: a workforce/picker-productivity metric was considered (stock_picking has a
    # picker_id column) but it is 0% populated (0 of 373,706 outgoing pickings), and
    # hr_attendance has only 4 rows, entirely outside the current data window (Nov
    # 2025-Jan 2026 vs. the business's Apr-Aug 2026 range). No workforce metric until
    # that data actually exists.
    #
    # Supplier metrics below are NOT scoped to CELESTE_STORE_IDS — purchasing is done
    # at the warehouse level, and a warehouse can serve several co-located storefront
    # labels (the same fan-out already documented in inventory.py/loss_and_damage.py),
    # so there's no clean per-outlet attribution for purchase orders. These are
    # business-wide purchasing signals, not scoped to the 5 Celeste retail outlets the
    # way sales metrics are.
    "supplier_fill_rate": MetricDef(
        id="supplier_fill_rate",
        independence_reviewed=True,
        label="Supplier on-time fill rate",
        unit="percent",
        direction="higher_is_good",
        is_rate=True,
        trend_sql="""
            SELECT date_trunc('month', date) AS month,
                   SUM(qty_on_time) AS numerator, SUM(qty_total) AS denominator
            FROM vendor_delay_report
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="supplier",
                label="Supplier",
                mode="rate",
                sql="""
                    SELECT COALESCE(rp.name, 'Unknown supplier') AS name,
                           SUM(vdr.qty_on_time) AS numerator, SUM(vdr.qty_total) AS denominator
                    FROM vendor_delay_report vdr
                    LEFT JOIN res_partner rp ON rp.id = vdr.partner_id
                    WHERE vdr.date >= :start AND vdr.date < :end
                    GROUP BY rp.name
                """,
            ),
        ],
        notes="qty_on_time / qty_total from vendor_delay_report — a blended measure: this "
        "table doesn't separate 'delivered late' from 'delivered short', so a drop here "
        "means a supplier's deliveries are less complete/timely, without distinguishing "
        "which. Business-wide, not scoped to individual outlets (see above). A low-volume "
        "supplier can occasionally show over 100% in the per-supplier breakdown — a source "
        "data quirk in vendor_delay_report, not a bug in this calculation.",
    ),
    "purchase_price_variance": MetricDef(
        id="purchase_price_variance",
        independence_reviewed=True,
        label="Purchase order price mismatch rate",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        # Redefined from an AVG(price_difference_percent) across ALL confirmed
        # POs to a RATE (% of POs with any nonzero mismatch) — checked live
        # before this change: the average was diluted by ~90% of POs having
        # zero difference, so it read as ~0% company-wide and would never
        # cross the anomaly threshold, even though the underlying share of
        # affected POs is real and was climbing (5.3% in June 2026, 10.3% in
        # July, 11.4% in August). price_difference_percent itself is zero for
        # every PO before June 2026 — this Odoo-side tracking is recent, so
        # there's no earlier history to compare against; expect the first
        # several months of this metric's trend to look like a "new problem
        # appearing" rather than a real behavior change.
        # No magnitude floor on what counts as a "mismatch" (any nonzero
        # price_difference_percent) — a simple, direct definition rather than
        # an unvalidated threshold; revisit if noise turns out to be an issue.
        trend_sql="""
            SELECT date_trunc('month', date_order) AS month,
                   COUNT(*) FILTER (WHERE price_difference_percent != 0) AS numerator,
                   COUNT(*) AS denominator
            FROM purchase_order
            WHERE state = 'purchase' AND price_difference_percent IS NOT NULL
            GROUP BY 1 ORDER BY 1
        """,
        dimensions=[
            Dimension(
                id="supplier",
                label="Supplier",
                # "contribution" using a raw COUNT of mismatched POs per
                # supplier — the previous version used AVG(...) in
                # "contribution" mode, which was the same category of bug
                # fixed elsewhere this session (an average isn't additive
                # across segments, so it was never usable for concentration
                # math even though it claimed to be contribution-mode).
                mode="contribution",
                sql="""
                    SELECT rp.name AS name, COUNT(*) AS value
                    FROM purchase_order po JOIN res_partner rp ON rp.id = po.partner_id
                    WHERE po.state = 'purchase' AND po.price_difference_percent != 0
                      AND po.date_order >= :start AND po.date_order < :end
                    GROUP BY rp.name
                """,
            ),
            # 2026-10-01: price_difference_percent is a PER-PO value, identical
            # across every line of the same order (checked live) -- original_price_unit
            # (which would give a true per-SKU price delta) is unpopulated in this
            # data. So this counts, per product, how many lines it appeared on
            # within a mismatched PO -- which products are most often bought via a
            # price-mismatched PO, not the size of any per-SKU price jump.
            Dimension(
                id="product",
                label="Product",
                mode="contribution",
                sql="""
                    SELECT pt.name->>'en_US' AS name, COUNT(*) AS value
                    FROM purchase_order_line pol
                    JOIN purchase_order po ON po.id = pol.order_id
                    JOIN product_product pp ON pp.id = pol.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE po.state = 'purchase' AND po.price_difference_percent != 0
                      AND po.date_order >= :start AND po.date_order < :end
                    GROUP BY pt.name
                """,
            ),
        ],
        notes="price_difference_percent is Odoo's own field for how much a confirmed purchase "
        "order's final price differed from its originally quoted price. This tracks the SHARE of "
        "purchase orders affected by any mismatch, not the average size of the mismatch — the "
        "average was too diluted by unaffected POs to ever register as anomalous. Business-wide, "
        "not scoped to individual outlets. Zero before June 2026 (this tracking is recent on the "
        "Odoo side, not a sign nothing happened before then).",
    ),
    "supplier_concentration": MetricDef(
        id="supplier_concentration",
        independence_reviewed=True,
        label="Top-supplier spend concentration",
        unit="percent",
        direction="higher_is_bad",
        is_rate=True,
        trend_sql="""
            WITH monthly AS (
                SELECT date_trunc('month', date_order) AS month, partner_id, SUM(amount_total) AS spend
                FROM purchase_order WHERE state = 'purchase'
                GROUP BY 1, 2
            ),
            ranked AS (
                SELECT month, spend, RANK() OVER (PARTITION BY month ORDER BY spend DESC) AS rnk
                FROM monthly
            ),
            totals AS (SELECT month, SUM(spend) AS total FROM monthly GROUP BY month)
            SELECT r.month, r.spend AS numerator, t.total AS denominator
            FROM ranked r JOIN totals t ON t.month = r.month
            WHERE r.rnk = 1
            ORDER BY r.month
        """,
        dimensions=[
            Dimension(
                id="supplier",
                label="Supplier",
                mode="composition",
                sql="""
                    SELECT rp.name AS name, SUM(po.amount_total) AS value
                    FROM purchase_order po JOIN res_partner rp ON rp.id = po.partner_id
                    WHERE po.state = 'purchase'
                      AND po.date_order >= :start AND po.date_order < :end
                    GROUP BY rp.name
                """,
            ),
        ],
        notes="Share of that month's total purchase spend going to the single "
        "highest-spend supplier that month — a rising trend signals growing "
        "dependency risk on one vendor. Business-wide, not scoped to individual outlets.",
    ),
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
            # Whether this metric has a trailing-7-day trend at all (see
            # short_term_trend_sql) -- the frontend's action-target form
            # uses this to only offer "weekly" measurement for a metric
            # that genuinely supports it, rather than letting a manager
            # request a cadence the backend would reject (Phase 2e Task 4:
            # never invent measurement semantics on the client).
            "supports_weekly_measurement": m.short_term_trend_sql is not None,
            "zero_baseline_rule": ({"absolute_threshold": m.zero_baseline_threshold,
                                    "minimum_exposure": m.zero_baseline_min_exposure}
                                   if m.zero_baseline_threshold is not None else None),
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


def get_trailing_trend(metric: MetricDef, window_days: int = 7):
    """Trailing-window counterpart to get_trend() — periods_ago=0 is the
    current window_days days ending at the data's own latest point (as_of),
    periods_ago=1..N are prior non-overlapping window_days-day windows used
    as the baseline. Returns the exact same {month, value, is_partial[,
    numerator, denominator]} shape get_trend() does — detect_anomaly() and
    classify_persistence() work on either without any changes, since they
    only look at value/is_partial. 'month' holds a human date-range string
    (e.g. 'Sep 03 – Sep 09') instead of a "YYYY-MM" key — there's no
    "current period still filling in" concept for a trailing window (it
    always ends exactly at the data's latest point), so is_partial is
    always False.

    Returns (trend, as_of) — as_of is exposed so a caller can also fetch a
    trailing breakdown (get_trailing_breakdown) anchored to the exact same
    point, without a second round-trip to recompute it."""
    rows = reporting.run_query(metric.short_term_trend_sql)
    if not rows:
        return [], None

    as_of = rows[0]["as_of"]
    trend = []
    for r in sorted(rows, key=lambda r: -r["periods_ago"]):  # oldest first, latest last
        periods_ago = r["periods_ago"]
        period_end = as_of - timedelta(days=window_days * periods_ago)
        period_start = period_end - timedelta(days=window_days)
        numerator = float(r["numerator"] or 0)
        entry = {"month": f"{period_start:%b %d} – {period_end:%b %d}", "is_partial": False}
        if metric.is_rate:
            denom = float(r.get("denominator") or 0)
            entry["value"] = round((numerator / denom * 100) if denom else 0.0, 4)
            entry["numerator"] = numerator
            entry["denominator"] = denom
        else:
            entry["value"] = round(numerator, 4)
        trend.append(entry)
    return trend, as_of


def detect_anomaly(trend: list[dict], direction: Direction, *, metric: MetricDef | None = None):
    complete = [m for m in trend if not m["is_partial"]]
    if metric and metric.is_rate:
        complete = [m for m in complete if m.get("denominator", 0) > 0]
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
    if baseline_mean == 0 and metric and metric.zero_baseline_threshold is not None:
        exposure = latest.get("denominator", latest["value"] if metric.unit == "count" else 0)
        is_significant = (abs(latest["value"]) >= metric.zero_baseline_threshold
                          and exposure >= metric.zero_baseline_min_exposure)
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
    if baseline_mean == 0 and latest["value"] != 0 and (metric is None or metric.zero_baseline_threshold is None):
        category = None  # no calibrated activation rule; never count this as healthy

    return {
        "is_anomalous": category == "critical",
        "category": category,
        "verdict": "activation_rule_required" if category is None else "anomalous" if category == "critical" else category,
        "latest_month": latest["month"],
        "latest_value": latest["value"],
        "baseline_mean": round(baseline_mean, 4),
        "pct_change_vs_baseline": round(pct_change, 2) if pct_change is not None else None,
        "z_score": round(z_score, 2) if z_score is not None else None,
        "moved_up": moved_up,
    }


def classify_persistence(trend: list[dict], direction: Direction):
    """Is the latest month's move an isolated blip, part of a multi-month
    streak in the same direction, or a recovery right after one?

    detect_anomaly only compares the latest month against the mean of
    everything before it — it can't tell "one bad month after a long
    recovery" from "the third bad month in a row", which is exactly the
    distinction that makes a business insight useful instead of just a
    number. This walks month-over-month direction instead of distance from
    a baseline, since a streak is about consecutive moves, not deviation.
    """
    complete = [m for m in trend if not m["is_partial"]]
    if len(complete) < 2:
        return {"pattern": "insufficient_data", "consecutive_periods": 0}

    values = [m["value"] for m in complete]
    months = [m["month"] for m in complete]

    def move_sign(prev, cur):
        if cur == prev:
            return 0
        raw_up = cur > prev
        is_bad = raw_up if direction == "higher_is_bad" else not raw_up
        return -1 if is_bad else 1  # -1 = bad move, +1 = good move

    moves = [move_sign(values[i], values[i + 1]) for i in range(len(values) - 1)]
    latest_move = moves[-1]

    if latest_move == 0:
        return {"pattern": "flat", "consecutive_periods": 0}

    streak = 0
    for m in reversed(moves):
        if m == latest_move:
            streak += 1
        else:
            break

    move_label = "declining" if latest_move == -1 else "improving"
    prior_move = moves[-streak - 1] if streak < len(moves) else None
    recovering = latest_move == 1 and prior_move == -1

    if recovering:
        pattern = "recovering"
    elif streak == 1:
        pattern = "single_period_move"
    else:
        pattern = f"consistent_{move_label}"

    return {
        "pattern": pattern,
        "direction": move_label,
        "consecutive_periods": streak,
        "period_range": f"{months[-(streak + 1)]} to {months[-1]}",
    }


def _rows_by_window(dim: Dimension, start: str, end: str) -> dict:
    rows = reporting.run_query(dim.sql, {"start": start, "end": end})
    return {r["name"]: r for r in rows}


def _rank_contributors(dim: Dimension, current_start: str, current_end: str,
                        baseline_start: str, baseline_end: str, moved_up: bool):
    """Shared core of get_breakdown()/get_trailing_breakdown() — given two
    explicit date windows (current vs. baseline), rank contributing
    segments. Extracted so a trailing 7-day window can reuse the exact same
    ranking logic as the monthly path without duplicating it; callers only
    differ in how they compute the 4 date bounds."""
    current = _rows_by_window(dim, current_start, current_end)
    baseline = _rows_by_window(dim, baseline_start, baseline_end)

    names = set(current) | set(baseline)
    contributors = []

    for name in names:
        cur, base = current.get(name), baseline.get(name)

        if dim.mode in ("rate", "ratio"):
            cur_num, cur_den = float(cur["numerator"] or 0) if cur else 0, float(cur["denominator"] or 0) if cur else 0
            base_num, base_den = float(base["numerator"] or 0) if base else 0, float(base["denominator"] or 0) if base else 0
            if cur_den < MIN_SEGMENT_VOLUME and base_den < MIN_SEGMENT_VOLUME:
                continue
            multiplier = 100 if dim.mode == "rate" else 1
            cur_val = (cur_num / cur_den * multiplier) if cur_den else 0.0
            base_val = (base_num / base_den * multiplier) if base_den else 0.0
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
        "top_contributors": contributors[:TOP_N],
    }


def get_breakdown(dim: Dimension, current_month: str, moved_up: bool):
    baseline_month = _shift_month(current_month, -1)
    current_start, current_end = _month_bounds(current_month)
    baseline_start, baseline_end = _month_bounds(baseline_month)
    result = _rank_contributors(dim, current_start, current_end, baseline_start, baseline_end, moved_up)
    result["baseline_month"] = baseline_month
    return result


def get_trailing_breakdown(dim: Dimension, as_of, moved_up: bool, window_days: int = 7):
    """Trailing-window counterpart to get_breakdown() — 'current' is the
    last window_days days ending at as_of (the metric's own trailing_trend
    as_of, not wall-clock now), 'baseline' is the window_days days before
    that. Same ranking logic, different bounds."""
    current_end = as_of
    current_start = as_of - timedelta(days=window_days)
    baseline_end = current_start
    baseline_start = baseline_end - timedelta(days=window_days)

    result = _rank_contributors(
        dim, current_start.isoformat(), current_end.isoformat(),
        baseline_start.isoformat(), baseline_end.isoformat(), moved_up,
    )
    result["baseline_period_label"] = f"{baseline_start:%b %d} – {baseline_end:%b %d}"
    return result


def get_month_to_date_breakdown(dim: Dimension, moved_up: bool = True):
    """Month-to-date counterpart to get_breakdown() -- 'current' is the
    in-progress month from its 1st through the data's own latest point
    (as_of), 'baseline' is the SAME elapsed portion of the prior month, not
    the prior month in full. Same elapsed-time-matched principle
    current_period.py already applies company-wide (see its own window_sql
    docstring) -- this is that same comparison per segment, reusing each
    MetricDef's existing dimension SQL unchanged (it already takes :start/
    :end bind params for a full month; nothing stops it taking a partial-
    month window instead). Built for OutletPerformancePanel (Home.jsx): a
    raw month-to-date ranking would repeat the exact "partial month reads as
    a decline" distortion get_trend()'s own is_partial flag exists to avoid
    -- comparing the SAME elapsed window on both sides is what makes this
    honest instead. as_of anchors to the data's own latest order (same
    principle brain.py's data_freshness and get_trailing_trend both use),
    never wall-clock time."""
    rows = reporting.run_query(
        "SELECT MAX(date_order) AS as_of FROM sale_order WHERE store_id = ANY(:stores)",
        {"stores": list(CELESTE_STORE_IDS)},
    )
    as_of = rows[0]["as_of"] if rows else None
    if as_of is None:
        return {"dimension": dim.id, "label": dim.label, "mode": dim.mode, "top_contributors": [], "as_of": None}

    current_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_end = as_of + timedelta(microseconds=1)
    baseline_start = (current_start.replace(year=current_start.year - 1, month=12) if current_start.month == 1
                       else current_start.replace(month=current_start.month - 1))
    baseline_end = baseline_start + (current_end - current_start)

    result = _rank_contributors(
        dim, current_start.isoformat(), current_end.isoformat(),
        baseline_start.isoformat(), baseline_end.isoformat(), moved_up,
    )
    result["as_of"] = as_of.isoformat()
    result["period_start"] = current_start.isoformat()
    result["baseline_period_label"] = f"{baseline_start:%b %d} - {baseline_end:%b %d}"
    return result


def investigate(metric_id: str):
    # Phase 5A: deliberately NOT cached here -- investigation.build_investigation()
    # calls this same function as the core of scan_all()'s real pipeline, which
    # MUST see fresh data on every rescan (the stale-evidence-conflict mechanism
    # depends on a rescan actually recomputing, not replaying a stale result).
    # Caching happens one layer up, in main.py's /intelligence/investigate/
    # {metric_id} route -- the ONLY caller that's a pure supplementary display.
    metric = METRICS.get(metric_id)
    if metric is None:
        return {"error": f"Unknown metric '{metric_id}'. Call list_metrics for available options."}

    trend = get_trend(metric)
    anomaly = detect_anomaly(trend, metric.direction, metric=metric)
    persistence = classify_persistence(trend, metric.direction)

    breakdowns = []
    if anomaly["latest_month"]:
        breakdowns = [
            get_breakdown(dim, anomaly["latest_month"], anomaly["moved_up"]) for dim in metric.dimensions
        ]

    return {
        "metric": {"id": metric.id, "label": metric.label, "unit": metric.unit, "direction": metric.direction,
                   "notes": metric.notes},
        "trend": trend,
        "anomaly": anomaly,
        "persistence": persistence,
        "breakdowns": breakdowns,
        "notes": metric.notes,
    }


def investigate_trailing(metric_id: str):
    """Trailing 7-day-window counterpart to investigate() — same shape,
    same anomaly/persistence engine, different period bucketing. Only
    callable for a metric with short_term_trend_sql defined."""
    metric = METRICS.get(metric_id)
    if metric is None:
        return {"error": f"Unknown metric '{metric_id}'. Call list_metrics for available options."}
    if metric.short_term_trend_sql is None:
        return {"error": f"'{metric_id}' has no short-term (trailing window) definition."}

    trend, as_of = get_trailing_trend(metric)
    anomaly = detect_anomaly(trend, metric.direction, metric=metric)
    persistence = classify_persistence(trend, metric.direction)

    breakdowns = []
    if as_of is not None and "moved_up" in anomaly:
        breakdowns = [
            get_trailing_breakdown(dim, as_of, anomaly["moved_up"]) for dim in metric.dimensions
        ]

    return {
        "metric": {"id": metric.id, "label": metric.label, "unit": metric.unit, "direction": metric.direction,
                   "notes": metric.notes},
        "trend": trend,
        "anomaly": anomaly,
        "persistence": persistence,
        "breakdowns": breakdowns,
        "as_of": as_of,
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
        anomaly = detect_anomaly(trend, metric.direction, metric=metric)
        if anomaly.get("category") not in ("critical", "opportunity"):
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
                "persistence": classify_persistence(trend, metric.direction),
                "top_driver": top_driver,
            }
        )

    flagged.sort(key=lambda r: (r["category"] != "critical", -abs(r["pct_change_vs_baseline"] or 0)))

    return {
        "flagged": flagged,
        "metrics_checked": len(METRICS),
        "metrics_normal": len(METRICS) - len(flagged),
    }

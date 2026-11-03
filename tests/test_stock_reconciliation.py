"""Confirmed sales with no stock deduction at all, clustered by episode.
_cluster_episodes() is pure/sync, tested directly -- same pattern as
test_operation_flow.py's _repeated_bottlenecks(). The key property under
test: an isolated missing-move order is noise and must never flag; only a
genuine cluster of orders in a short window (the real Oct 20 incident's
shape) should."""

from datetime import datetime

from mcp_server.tools import stock_reconciliation as sr


def _row(order_id, minute, outlet="Celeste Daily Colombo 6", product="Milk 1L"):
    return {
        "order_id": order_id, "outlet": outlet,
        "date_order": datetime(2026, 10, 20, 8, 50, minute),
        "product_name": product,
    }


def test_flags_a_genuine_cluster_of_orders_in_a_short_window():
    rows = [_row(i, i) for i in range(sr.MIN_CLUSTER_ORDERS)]  # minutes 0,1,2,3,4 -- all within GAP_MINUTES

    episodes = sr._cluster_episodes(rows)

    assert len(episodes) == 1
    assert len(episodes[0]) == sr.MIN_CLUSTER_ORDERS


def test_ignores_an_isolated_single_order():
    rows = [_row(1, 0)]

    assert sr._cluster_episodes(rows) == []


def test_ignores_a_handful_of_orders_below_the_cluster_floor():
    rows = [_row(i, i) for i in range(sr.MIN_CLUSTER_ORDERS - 1)]

    assert sr._cluster_episodes(rows) == []


def test_splits_two_episodes_separated_by_a_long_gap():
    first = [_row(i, i) for i in range(sr.MIN_CLUSTER_ORDERS)]
    second = [
        {**_row(100 + i, 0), "date_order": datetime(2026, 10, 20, 9, 30, i)}
        for i in range(sr.MIN_CLUSTER_ORDERS)
    ]

    episodes = sr._cluster_episodes(first + second)

    assert len(episodes) == 2
    assert len(episodes[0]) == sr.MIN_CLUSTER_ORDERS
    assert len(episodes[1]) == sr.MIN_CLUSTER_ORDERS


def test_chains_orders_within_gap_even_across_a_longer_total_span():
    # Each consecutive pair is within GAP_MINUTES, but the first and last
    # are further apart than GAP_MINUTES -- still one episode, matching the
    # real Oct 20 data (one episode spanned ~34 minutes via chained gaps).
    rows = [
        {**_row(i, 0), "date_order": datetime(2026, 10, 20, 8, i * (sr.GAP_MINUTES - 1))}
        for i in range(sr.MIN_CLUSTER_ORDERS)
    ]

    episodes = sr._cluster_episodes(rows)

    assert len(episodes) == 1
    assert len(episodes[0]) == sr.MIN_CLUSTER_ORDERS


def test_multiple_lines_on_the_same_order_count_as_one_order_not_duplicates():
    rows = [_row(1, 0, product="Milk"), _row(1, 0, product="Eggs")] + [_row(i, i) for i in range(2, sr.MIN_CLUSTER_ORDERS + 1)]

    episodes = sr._cluster_episodes(rows)

    assert len(episodes) == 1
    assert len(episodes[0]) == sr.MIN_CLUSTER_ORDERS  # order 1 counted once despite 2 lines

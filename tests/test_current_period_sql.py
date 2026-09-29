"""Optional local SQL execution checks (DuckDB); no reporting DB is contacted.

Run with duckdb on PYTHONPATH. These exercise aggregation and date boundaries;
they supplement, rather than claim to replace, a live PostgreSQL integration run.
"""
import pytest

from mcp_server.tools import current_period


@pytest.fixture
def sql_db():
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect(":memory:")
    for ddl in [
        "CREATE TABLE sale_order(id INT, date_order TIMESTAMP, state VARCHAR, store_id INT, partner_id INT, amount_total DOUBLE)",
        "CREATE TABLE sale_order_line(order_id INT, product_id INT, price_subtotal DOUBLE, product_uom_qty DOUBLE)",
        "CREATE TABLE product_product(id INT, product_tmpl_id INT)",
        "CREATE TABLE product_template(id INT, celeste_cost_price_ex_vat DOUBLE)",
        "CREATE TABLE stock_picking(id INT, date_done TIMESTAMP, scheduled_date TIMESTAMP, state VARCHAR, picking_type_id INT, sale_id INT)",
        "CREATE TABLE stock_picking_type(id INT, code VARCHAR)",
        "CREATE TABLE uber_order(create_date TIMESTAMP, state VARCHAR, store_id INT)",
        "CREATE TABLE pickme_order(create_date TIMESTAMP, state VARCHAR, store_id INT)",
        "CREATE TABLE purchase_order(date_order TIMESTAMP, state VARCHAR, partner_id INT, amount_total DOUBLE, price_difference_percent DOUBLE)",
        "CREATE TABLE vendor_delay_report(date TIMESTAMP, partner_id INT, qty_on_time DOUBLE, qty_total DOUBLE)",
    ]:
        connection.execute(ddl)
    connection.execute("INSERT INTO product_product VALUES (1,1)")
    connection.execute("INSERT INTO product_template VALUES (1,2)")
    connection.execute("INSERT INTO stock_picking_type VALUES (1,'outgoing')")
    dates = [f"2026-{month:02d}-{day:02d} 11:04:58" for month in (7, 8, 9) for day in ((1, 27, 28) if month < 9 else (1, 27))]
    for number, stamp in enumerate(dates, 1):
        connection.execute("INSERT INTO sale_order VALUES (?,?,'sale',1,1,100)", [number, stamp])
        connection.execute("INSERT INTO sale_order VALUES (?,?,'sale',999,1,999999)", [number + 1000, stamp])
        connection.execute("INSERT INTO sale_order_line VALUES (?,1,10,1)", [number])
        connection.execute("INSERT INTO stock_picking VALUES (?,CAST(? AS TIMESTAMP),CAST(? AS TIMESTAMP)-INTERVAL '48 hours','done',1,?)", [number, stamp, stamp, number])
        connection.execute("INSERT INTO uber_order VALUES (?,'failed',1),(?,'completed',1)", [stamp, stamp])
        connection.execute("INSERT INTO pickme_order VALUES (?,'cancelled',1),(?,'completed',1)", [stamp, stamp])
        connection.execute("INSERT INTO purchase_order VALUES (?,'purchase',1,100,0),(?,'purchase',2,100,1)", [stamp, stamp])
        connection.execute("INSERT INTO vendor_delay_report VALUES (?,1,2,4)", [stamp])
    yield connection
    connection.close()


def read_rows(connection, sql):
    cursor = connection.execute(sql)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


@pytest.mark.parametrize(("metric", "expected"), [
    ("revenue", 200), ("order_volume", 2), ("average_order_value", 100),
    ("customer_retention_rate", 100), ("cancellation_rate", 0),
    ("delayed_delivery_rate", 100), ("uber_failure_rate", 50),
    ("pickme_cancellation_rate", 50), ("gross_margin", 80),
    ("supplier_fill_rate", 50), ("purchase_price_variance", 50), ("supplier_concentration", 50),
])
def test_all_metric_queries_execute_and_preserve_definitions(sql_db, monkeypatch, metric, expected):
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: read_rows(sql_db, sql))
    result = current_period.assess(metric)
    assert result["status"] == "measured", result
    assert result["confirm"]["current"] == expected
    assert result["confirm"]["baseline"] == expected
    assert result["source_data_through"] == "2026-09-27T11:04:58"


def test_month_end_preserves_latest_data_and_excludes_short_comparison(sql_db, monkeypatch):
    sql_db.execute("DELETE FROM sale_order")
    sql_db.execute("""INSERT INTO sale_order VALUES
        (1,'2026-01-31 18:00:00','sale',1,1,100),
        (2,'2026-02-28 18:00:00','sale',1,1,500),
        (3,'2026-03-31 18:00:00','sale',1,1,200)""")
    monkeypatch.setattr(current_period, "_read_rows", lambda sql: read_rows(sql_db, sql))
    result = current_period.assess("revenue")
    assert result["confirm"]["current"] == 200
    assert result["confirm"]["baseline"] == 100
    assert result["comparison"]["excluded_short_months"] == ["2026-02"]

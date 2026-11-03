"""The app's own state — separate from reporting.py's connection to the
customer's live Odoo database. Nothing here ever touches Odoo data; this
is purely for things the app itself needs to remember (e.g. tracked
investigation findings), so it gets its own engine and its own file.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import anyio
from filelock import FileLock
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

_transaction_connection: ContextVar[Connection | None] = ContextVar(
    "appdb_transaction_connection", default=None
)


@lru_cache()
def get_engine() -> Engine:
    """Create and cache a SQLAlchemy engine using env var `APP_DATABASE_URL`.

    Falls back to a local SQLite file for development.
    """
    db_url = os.getenv("APP_DATABASE_URL") or "sqlite:///./app.db"
    # SQLite allows only one writer at a time; without a busy timeout, a
    # second connection trying to write while the scan's own transaction
    # (brain._compute_daily_brief, several minutes long) is open fails
    # immediately with "database is locked" instead of waiting briefly for
    # it to clear -- observed live, 2026-10-21, colliding with
    # notifications.acknowledge() on Home page load.
    connect_args = {"timeout": 30} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, future=True, connect_args=connect_args)


def case_store_lock(timeout: float = 600) -> FileLock:
    """A cross-process lock shared by scans and case/action mutations.

    Scans also use an application-database transaction so all their state
    changes publish together; the file lock still coordinates that work
    with action saves across processes.
    """
    configured = os.getenv("APP_SCAN_LOCK_PATH")
    database = get_engine().url.database or "app.db"
    path = Path(configured or f"{database}.scan.lock").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path), timeout=timeout)


@contextmanager
def transaction() -> Iterator[Connection]:
    """Route implicit run_query calls through one commit-or-rollback unit."""
    if _transaction_connection.get() is not None:
        raise RuntimeError("An appdb transaction is already active in this context.")
    with get_engine().begin() as conn:
        token = _transaction_connection.set(conn)
        try:
            yield conn
        finally:
            _transaction_connection.reset(token)


@contextmanager
def connection_for_bulk_write() -> Iterator[Connection]:
    """A writable connection for a bulk statement (executemany-style list of
    params) -- run_query only takes one params dict, not a list, so a bulk
    insert/delete can't go through it. Joins the enclosing appdb.transaction()
    if one is active, the same ambient connection run_query already joins, so
    it can't deadlock against it; opens and commits its own otherwise, same
    as run_query does when called outside a transaction."""
    ambient = _transaction_connection.get()
    if ambient is not None:
        yield ambient
    else:
        with get_engine().begin() as conn:
            yield conn


def run_query(query: str, params: dict | None = None, conn=None):
    """conn is optional -- when omitted (every existing call site), this
    opens and commits its own connection exactly as before. When a caller
    passes one (opened via get_engine().begin(), an actual SQL
    transaction), this participates in THAT transaction instead of
    committing on its own -- the caller owns the commit/rollback. This
    exists so a sequence of writes that must succeed or fail together
    (see case_engine.save_action_with_verification) can actually do so:
    without it, each write was its own independent commit, and a failure
    partway through the sequence left whatever had already committed in
    place -- a real partial-failure risk (Phase 2e post-acceptance audit,
    Task 1 point 1), not a hypothetical one."""
    if conn is None:
        conn = _transaction_connection.get()
    if conn is not None:
        result = conn.execute(text(query), params or {})
        if not result.returns_rows:
            return []
        return [dict(r._mapping) for r in result]
    engine = get_engine()
    with engine.connect() as c:
        result = c.execute(text(query), params or {})
        if not result.returns_rows:
            c.commit()
            return []
        return [dict(r._mapping) for r in result]


async def fetch(query: str, params: dict | None = None):
    """Async wrapper to run app-DB queries in a threadpool."""
    return await anyio.to_thread.run_sync(run_query, query, params)

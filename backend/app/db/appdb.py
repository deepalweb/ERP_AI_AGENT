"""The app's own state — separate from reporting.py's connection to the
customer's live Odoo database. Nothing here ever touches Odoo data; this
is purely for things the app itself needs to remember (e.g. tracked
investigation findings), so it gets its own engine and its own file.
"""

import os
from functools import lru_cache
from pathlib import Path

import anyio
from filelock import FileLock
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@lru_cache()
def get_engine() -> Engine:
    """Create and cache a SQLAlchemy engine using env var `APP_DATABASE_URL`.

    Falls back to a local SQLite file for development.
    """
    db_url = os.getenv("APP_DATABASE_URL") or "sqlite:///./app.db"
    return create_engine(db_url, future=True)


def case_store_lock(timeout: float = 600) -> FileLock:
    """A cross-process lock shared by everything that mutates the
    business_cases / case_observations / finding_actions tables as a unit:
    brain.run_daily_scan() holds this for a whole scan's duration (its
    original name here was _scan_lock, moved from brain.py so
    case_engine.py's action-save path can take the exact same lock rather
    than inventing a second one), and case_engine.
    save_action_with_verification() takes it too, with a much shorter
    timeout, around its evidence-version check + write -- closing the gap
    where a scan could land between the check and the save (see Phase 2e
    Task 1). Deliberately a separate file from the SQLite database itself,
    so a scan doesn't hold a DB write transaction open while waiting on
    reporting queries or the model."""
    configured = os.getenv("APP_SCAN_LOCK_PATH")
    database = get_engine().url.database or "app.db"
    path = Path(configured or f"{database}.scan.lock").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path), timeout=timeout)


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

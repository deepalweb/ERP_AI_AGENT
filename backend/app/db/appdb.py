"""The app's own state — separate from reporting.py's connection to the
customer's live Odoo database. Nothing here ever touches Odoo data; this
is purely for things the app itself needs to remember (e.g. tracked
investigation findings), so it gets its own engine and its own file.
"""

import os
from functools import lru_cache

import anyio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@lru_cache()
def get_engine() -> Engine:
    """Create and cache a SQLAlchemy engine using env var `APP_DATABASE_URL`.

    Falls back to a local SQLite file for development.
    """
    db_url = os.getenv("APP_DATABASE_URL") or "sqlite:///./app.db"
    return create_engine(db_url, future=True)


def run_query(query: str, params: dict | None = None):
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        if not result.returns_rows:
            conn.commit()
            return []
        return [dict(r._mapping) for r in result]


async def fetch(query: str, params: dict | None = None):
    """Async wrapper to run app-DB queries in a threadpool."""
    return await anyio.to_thread.run_sync(run_query, query, params)

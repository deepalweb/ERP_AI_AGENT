import functools
import inspect
import os
import time
from functools import lru_cache
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import anyio


@lru_cache()
def get_engine() -> Engine:
	"""Create and cache a SQLAlchemy engine using env var `REPORTING_DATABASE_URL`.

	Falls back to `DATABASE_URL`, then a local SQLite file for development.
	"""
	db_url = (
		os.getenv("REPORTING_DATABASE_URL")
		or os.getenv("DATABASE_URL")
		or "sqlite:///./reporting.db"
	)
	return create_engine(db_url, future=True)


def run_query(query: str, params: dict | None = None):
	engine = get_engine()
	with engine.connect() as conn:
		result = conn.execute(text(query), params or {})
		# Try to coerce rows to dicts when possible
		rows = [dict(r._mapping) for r in result]
		if not result.returns_rows:
			conn.commit()
		return rows


async def fetch_report(query: str, params: dict | None = None):
	"""Async wrapper to run DB queries in a threadpool."""
	return await anyio.to_thread.run_sync(run_query, query, params)


def ttl_cache(seconds: float):
    """Phase 5A: an in-process, time-expiring cache for a slow read-only
    reporting query -- the reporting mirror is already a periodic ETL that
    lags real time by days (see Home's own "Order data through... N days
    behind" disclosure), so serving a same-args call from up to `seconds`
    ago never makes the data meaningfully staler than it already is.

    Key: the wrapped function's name + its exact args/kwargs (via repr) --
    different params (e.g. a different period) get their own entry, never
    collide. Lifetime: `seconds`, fixed per call site. Invalidation: purely
    time-based expiry, checked on the next call after it lapses -- nothing
    here writes, so there is no event to invalidate on. Freshness: this
    NEVER rewrites a timestamp/as_of field already in the cached payload
    (those describe the underlying data's own period, computed once at
    query time, and stay correct for the life of the entry) -- it also
    never fabricates a "just now" label; callers get exactly what the
    underlying query returned, just not always freshly re-run.

    Works on both a sync and an async function, since this codebase's
    reporting-backed functions are a mix of both."""
    def decorator(fn):
        store: dict[str, tuple[float, object]] = {}

        def _key(args, kwargs) -> str:
            return repr((args, tuple(sorted(kwargs.items()))))

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                key = _key(args, kwargs)
                hit = store.get(key)
                if hit and hit[0] > time.monotonic():
                    return hit[1]
                result = await fn(*args, **kwargs)
                store[key] = (time.monotonic() + seconds, result)
                return result
            async_wrapper.cache_clear = store.clear  # same escape hatch functools.lru_cache offers -- used by tests
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            key = _key(args, kwargs)
            hit = store.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
            result = fn(*args, **kwargs)
            store[key] = (time.monotonic() + seconds, result)
            return result
        sync_wrapper.cache_clear = store.clear
        return sync_wrapper
    return decorator


async def fetch_schema():
		"""Return basic schema introspection: tables, columns, primary keys, foreign keys."""
		tables_q = """
		SELECT table_schema, table_name
		FROM information_schema.tables
		WHERE table_type='BASE TABLE'
			AND table_schema NOT IN ('pg_catalog','information_schema')
		ORDER BY table_schema, table_name
		"""

		columns_q = """
		SELECT table_schema, table_name, column_name, data_type, is_nullable, column_default
		FROM information_schema.columns
		WHERE table_schema NOT IN ('pg_catalog','information_schema')
		ORDER BY table_schema, table_name, ordinal_position
		"""

		pks_q = """
		SELECT kcu.table_schema, kcu.table_name, kcu.column_name
		FROM information_schema.table_constraints tc
		JOIN information_schema.key_column_usage kcu
			ON tc.constraint_name = kcu.constraint_name
		 AND tc.table_schema = kcu.table_schema
		WHERE tc.constraint_type = 'PRIMARY KEY'
		"""

		fks_q = """
		SELECT
			kcu.table_schema, kcu.table_name, kcu.column_name,
			ccu.table_schema AS foreign_table_schema,
			ccu.table_name AS foreign_table_name,
			ccu.column_name AS foreign_column_name
		FROM information_schema.table_constraints tc
		JOIN information_schema.key_column_usage kcu
			ON tc.constraint_name = kcu.constraint_name
		 AND tc.table_schema = kcu.table_schema
		JOIN information_schema.constraint_column_usage ccu
			ON tc.constraint_name = ccu.constraint_name
		 AND tc.table_schema = ccu.table_schema
		WHERE tc.constraint_type = 'FOREIGN KEY'
		"""

		tables = await fetch_report(tables_q)
		columns = await fetch_report(columns_q)
		pks = await fetch_report(pks_q)
		fks = await fetch_report(fks_q)

		return {
				"tables": tables,
				"columns": columns,
				"primary_keys": pks,
				"foreign_keys": fks,
		}

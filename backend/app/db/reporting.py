import os
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

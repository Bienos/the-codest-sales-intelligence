from __future__ import annotations

import os
import uuid

import pytest

from ri.db import connect
from ri.migrate import migrate

BASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:54329/postgres")


def _admin(url: str):
    import psycopg
    return psycopg.connect(url, autocommit=True)


@pytest.fixture(scope="session")
def db_url():
    """One throw-away database per test session, fully migrated."""
    try:
        admin = _admin(BASE_URL)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    name = f"ri_test_{uuid.uuid4().hex[:10]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = BASE_URL.rpartition("/")
    url = f"{base}/{name}"
    migrate(url)
    yield url
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


@pytest.fixture()
def conn(db_url):
    """Importers commit by design, so isolation = truncate all data tables after each test."""
    c = connect(db_url)
    try:
        yield c
    finally:
        c.rollback()
        tables = [r["tablename"] for r in c.execute(
            "select tablename from pg_tables where schemaname='public' and tablename not in ('schema_migrations','legacy_signals')")]
        if tables:
            c.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
            c.commit()
        c.close()

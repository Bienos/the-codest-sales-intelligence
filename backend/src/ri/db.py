"""Thin PostgreSQL access layer (psycopg 3). All state transitions go through explicit transactions."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

Conn = psycopg.Connection[dict[str, Any]]


def connect(url: str) -> Conn:
    return psycopg.connect(url, row_factory=dict_row, autocommit=False)  # type: ignore[return-value]


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10):
        self.url = url
        self._pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row}, open=True
        )

    @contextmanager
    def tx(self) -> Iterator[Conn]:
        with self._pool.connection() as conn:  # commits on success, rolls back on exception
            yield conn  # type: ignore[misc]

    def close(self) -> None:
        self._pool.close()


def jb(value: Any) -> Jsonb:
    return Jsonb(value)


def q1(conn: Conn, sql: str, params: Any = None) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def qa(conn: Conn, sql: str, params: Any = None) -> list[dict[str, Any]]:
    return conn.execute(sql, params).fetchall()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

"""Component heartbeats: the scheduler and worker record that they are alive, so 'is it running by itself?' has a factual answer."""
from __future__ import annotations

from .db import Conn, jb


def beat(conn: Conn, component: str, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO system_heartbeats(component, at, detail) VALUES (%s, now(), %s) ON CONFLICT (component) DO UPDATE SET at=now(), detail=EXCLUDED.detail",
                 (component, jb(detail or {})))

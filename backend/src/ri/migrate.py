"""Versioned SQL migrations with checksum verification and an advisory lock.

Forward-only by default (see docs/runbooks/RB-migrations.md). A previously applied migration whose
file changed on disk is a hard error - never silently re-run or edited history.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from .db import connect

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
LOCK_KEY = 7_281_001


def _files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def migrate(url: str, up_to: str | None = None) -> list[str]:
    applied_now: list[str] = []
    conn = connect(url)
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.commit()
        done = {r["version"]: r["checksum"] for r in conn.execute("SELECT * FROM schema_migrations")}
        for f in _files():
            version = f.stem
            sql = f.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if version in done:
                if done[version] != checksum:
                    raise RuntimeError(f"migration {version} was modified after being applied")
                continue
            if up_to and version > up_to:
                break
            try:
                conn.execute(sql)  # type: ignore[arg-type]
                conn.execute("INSERT INTO schema_migrations(version, checksum) VALUES (%s,%s)", (version, checksum))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied_now.append(version)
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        conn.commit()
    finally:
        conn.close()
    return applied_now


def main() -> None:  # pragma: no cover
    from .config import Settings

    applied = migrate(Settings.from_env().database_url)
    print("applied:", applied or "nothing to do")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

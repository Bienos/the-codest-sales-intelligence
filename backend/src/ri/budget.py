"""Hard-cap provider budgets (spec 14 §8, FR-009). Reserve before the call, settle after, release on failure.

A missing budget row means cap 0: nothing paid runs unless someone explicitly configured spend.
Free official APIs reserve 0 and are never blocked.
"""
from __future__ import annotations

from .db import Conn, q1
from .sources.base import BudgetExceeded


def set_cap(conn: Conn, provider: str, kind: str, period: str, cap: float, unit: str = "units") -> None:
    conn.execute(
        "INSERT INTO provider_budgets(provider, kind, period, cap, unit) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (provider, kind, period) DO UPDATE SET cap=EXCLUDED.cap, unit=EXCLUDED.unit",
        (provider, kind, period, cap, unit))


def reserve(conn: Conn, provider: str, kind: str, period: str, amount: float, *, key: str,
            ref_type: str | None = None, ref_id: str | None = None, purpose: str = "") -> bool:
    """Idempotent on `key`. Returns True when newly reserved, False when the key was already processed."""
    if q1(conn, "SELECT 1 FROM budget_ledger WHERE idempotency_key=%s", (key,)):
        return False
    if amount <= 0:
        conn.execute("INSERT INTO budget_ledger(provider, kind, period, entry, amount, ref_type, ref_id, purpose, idempotency_key) "
                     "VALUES (%s,%s,%s,'reserve',0,%s,%s,%s,%s)", (provider, kind, period, ref_type, ref_id, purpose, key))
        return True
    row = conn.execute(
        "UPDATE provider_budgets SET reserved = reserved + %s WHERE provider=%s AND kind=%s AND period=%s "
        "AND reserved + spent + %s <= cap RETURNING cap", (amount, provider, kind, period, amount)).fetchone()
    if not row:
        conn.execute("INSERT INTO budget_ledger(provider, kind, period, entry, amount, ref_type, ref_id, purpose, idempotency_key) "
                     "VALUES (%s,%s,%s,'denied',%s,%s,%s,%s,%s)", (provider, kind, period, amount, ref_type, ref_id, purpose, f"denied:{key}"))
        raise BudgetExceeded(f"{provider}/{kind}/{period}: reserving {amount} would exceed the hard cap")
    conn.execute("INSERT INTO budget_ledger(provider, kind, period, entry, amount, ref_type, ref_id, purpose, idempotency_key) "
                 "VALUES (%s,%s,%s,'reserve',%s,%s,%s,%s,%s)", (provider, kind, period, amount, ref_type, ref_id, purpose, key))
    return True


def settle(conn: Conn, provider: str, kind: str, period: str, reserved: float, actual: float, *, key: str) -> None:
    """Convert a reservation to actual spend. Actual above the reservation is recorded and may over-run the cap
    (provider billing is authoritative) - the NEXT reserve will then be denied."""
    if q1(conn, "SELECT 1 FROM budget_ledger WHERE idempotency_key=%s", (f"settle:{key}",)):
        return
    conn.execute("UPDATE provider_budgets SET spent = spent + LEAST(%s, cap - spent - GREATEST(reserved - %s, 0)), "
                 "reserved = GREATEST(reserved - %s, 0) WHERE provider=%s AND kind=%s AND period=%s",
                 (actual, reserved, reserved, provider, kind, period))
    conn.execute("INSERT INTO budget_ledger(provider, kind, period, entry, amount, idempotency_key) VALUES (%s,%s,%s,'settle',%s,%s)",
                 (provider, kind, period, actual, f"settle:{key}"))


def release(conn: Conn, provider: str, kind: str, period: str, amount: float, *, key: str) -> None:
    if q1(conn, "SELECT 1 FROM budget_ledger WHERE idempotency_key=%s", (f"release:{key}",)):
        return
    conn.execute("UPDATE provider_budgets SET reserved = GREATEST(reserved - %s, 0) WHERE provider=%s AND kind=%s AND period=%s",
                 (amount, provider, kind, period))
    conn.execute("INSERT INTO budget_ledger(provider, kind, period, entry, amount, idempotency_key) VALUES (%s,%s,%s,'release',%s,%s)",
                 (provider, kind, period, amount, f"release:{key}"))


def remaining(conn: Conn, provider: str, kind: str, period: str) -> float:
    r = q1(conn, "SELECT cap - reserved - spent AS rem FROM provider_budgets WHERE provider=%s AND kind=%s AND period=%s",
           (provider, kind, period))
    return float(r["rem"]) if r else 0.0

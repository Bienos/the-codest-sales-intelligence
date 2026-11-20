"""Import the original 64-rule tracker UNCHANGED as canonical baseline (spec 03 §2, FR-002)."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from ..db import Conn

REPO_ROOT = Path(__file__).resolve().parents[4]
BASELINE_CSV = REPO_ROOT / "reference" / "signals" / "The_Codest_Buying_Signal_Tracker_Original_64_Rules.csv"
EXPECTED_SHA256 = "c2ffda8e47d36858d5c0989d2bcd1729b178184195a7da969e4e6fd6c417b680"
EXPECTED_ROWS = 64
COLUMNS = [
    "ID", "Group", "Signal", "Strength (0-3)", "Fresh for (days)", "Window note",
    "How to spot it", "Who to contact", "Check it is real", "Pairs best with", "Stop effect",
]


class BaselineError(RuntimeError):
    pass


def load_rows(path: Path = BASELINE_CSV, expected_sha: str | None = EXPECTED_SHA256) -> tuple[list[dict[str, str]], str]:
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and sha != expected_sha:
        raise BaselineError(f"baseline CSV hash mismatch: {sha} != {expected_sha}")
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines(keepends=True))
    if reader.fieldnames != COLUMNS:
        raise BaselineError(f"unexpected columns: {reader.fieldnames}")
    rows = list(reader)
    if len(rows) != EXPECTED_ROWS:
        raise BaselineError(f"expected {EXPECTED_ROWS} rows, found {len(rows)}")
    ids = [r["ID"] for r in rows]
    if len(set(ids)) != len(ids):
        raise BaselineError("duplicate signal IDs in baseline")
    return rows, sha


def import_baseline(conn: Conn, path: Path = BASELINE_CSV, expected_sha: str | None = EXPECTED_SHA256) -> int:
    """Idempotent. Rows are immutable once stored (DB trigger); a differing re-import is an error."""
    rows, sha = load_rows(path, expected_sha)
    inserted = 0
    for i, r in enumerate(rows, start=1):
        cur = conn.execute("SELECT * FROM legacy_signals WHERE id=%s", (r["ID"],)).fetchone()
        vals = (r["Group"], r["Signal"], r["Strength (0-3)"], r["Fresh for (days)"], r["Window note"],
                r["How to spot it"], r["Who to contact"], r["Check it is real"], r["Pairs best with"], r["Stop effect"])
        if cur:
            existing = (cur["grp"], cur["signal"], cur["strength"], cur["fresh_for_days"], cur["window_note"],
                        cur["how_to_spot"], cur["who_to_contact"], cur["check_it_is_real"],
                        cur["pairs_best_with"], cur["stop_effect"])
            if existing != vals:
                raise BaselineError(f"{r['ID']} differs from stored immutable baseline")
            continue
        conn.execute(
            "INSERT INTO legacy_signals(id, ordinal, grp, signal, strength, fresh_for_days, window_note, how_to_spot,"
            " who_to_contact, check_it_is_real, pairs_best_with, stop_effect, source_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (r["ID"], i, *vals, sha),
        )
        inserted += 1
    return inserted

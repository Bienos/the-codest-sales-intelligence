import pytest
import psycopg

from ri.signals.baseline import import_baseline, load_rows, BaselineError, BASELINE_CSV
from ri.migrate import migrate


def test_csv_loads_64_rows_unchanged():
    rows, sha = load_rows()
    assert len(rows) == 64
    assert sha.startswith("c2ffda8e")
    fam = {r["ID"].split("-")[0] for r in rows}
    assert fam == {"REL", "SRC", "CAP", "PRG", "COM", "MAN", "REG", "NEG"}


def test_tampered_baseline_rejected(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(BASELINE_CSV.read_bytes() + b"\n")
    with pytest.raises(BaselineError):
        load_rows(p)


def test_import_idempotent_and_immutable(conn):
    assert import_baseline(conn) == 64
    assert import_baseline(conn) == 0
    assert conn.execute("select count(*) c from legacy_signals").fetchone()["c"] == 64
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("update legacy_signals set signal='x' where id='REL-01'")


def test_audit_is_append_only(conn):
    conn.execute("insert into audit_events(actor,operation,record_type) values ('t','x','y')")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("delete from audit_events")


def test_migrations_idempotent_and_checksummed(db_url):
    assert migrate(db_url) == []

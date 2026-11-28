"""Monday competitor digest: sent once a week to Slack and to the people who review moves, quiet weeks send nothing, no deal or account names."""
from datetime import datetime, timedelta, timezone

from ri import competitor_digest as cd
from ri import competitors as ct
from ri.notify.digest import ConsoleSink

MONDAY = datetime(2026, 11, 30, 7, 5, tzinfo=timezone.utc)
BASE = "https://ri.example"


class Mail(ConsoleSink):
    channel = "email"


class Broken:
    channel = "email"

    def send(self, recipient, text):
        raise OSError("smtp down")


def user(conn, email, role, active=True):
    conn.execute("INSERT INTO users(email, display_name, role, active) VALUES (%s,'X',%s,%s)", (email, role, active))


def candidate(conn, key="neontri", headline="Neontri opens an office in Stockholm"):
    return ct.add_move(conn, key, {"date": "2026-11-25", "type": "market", "headline": headline, "detail": None, "sowhat": None,
                                   "source": "https://neontri.com/news/stockholm", "grade": "official", "impact": 2}, "watch", confirm=False)


def test_quiet_week_and_wrong_time_send_nothing(conn):
    ct.load_seed(conn)
    slack = ConsoleSink()
    candidate(conn)
    assert cd.weekly_step(conn, base_url=BASE, slack=slack, now=MONDAY - timedelta(hours=1)) == {}        # before 07:00
    assert cd.weekly_step(conn, base_url=BASE, slack=slack, now=MONDAY + timedelta(days=1)) == {}         # Tuesday
    assert cd.weekly_step(conn, base_url=None, slack=slack, now=MONDAY) == {}                             # no address for the links
    assert not slack.sent
    conn.execute("UPDATE competitor_moves SET status='dismissed'")
    assert cd.weekly_step(conn, base_url=BASE, slack=slack, now=MONDAY) == {"competitor_digest": "quiet"}


def test_sent_once_per_week_to_slack_and_reviewers(conn):
    ct.load_seed(conn)
    user(conn, "mkt@thecodest.co", "marketing")
    user(conn, "boss@thecodest.co", "sales_manager")
    user(conn, "bdm@thecodest.co", "bdm")                  # reads the tracker, does not review: no email
    user(conn, "gone@thecodest.co", "marketing", active=False)
    mid = candidate(conn, headline="Boldare now leads with AI product teams")
    conn.execute("UPDATE competitor_moves SET competitor_key='boldare' WHERE id=%s", (mid,))
    candidate(conn)
    other = candidate(conn, headline="Neontri wins a payments client in Norway")
    ct.review_move(conn, other, status="confirmed", actor="mkt@x", so_what="They are moving into our Nordic accounts.")
    conn.execute("UPDATE competitor_moves SET reviewed_at=%s WHERE id=%s", (MONDAY - timedelta(days=2), other))
    conn.execute("INSERT INTO deal_competitors(crm_deal_id, competitor_key, role, noted_by, noted_at) VALUES (77,'neontri','lost_to','x',%s)", (MONDAY - timedelta(days=1),))
    slack, mail = ConsoleSink(), Mail()
    out = cd.weekly_step(conn, base_url=BASE, slack=slack, email=mail, now=MONDAY)
    assert out == {"competitor_digest_sent": 3}
    assert sorted(r for r, _ in mail.sent) == ["boss@thecodest.co", "mkt@thecodest.co"]
    text = slack.sent[0][1]
    assert "Waiting for review (2)" in text and "Boldare now leads with AI product teams" in text and f"{BASE}/competitors/review" in text
    assert "Confirmed this week (1)" in text and "So what: They are moving into our Nordic accounts." in text
    assert "Named on deals this week: Neontri (1)" in text and "77" not in text
    assert cd.weekly_step(conn, base_url=BASE, slack=slack, email=mail, now=MONDAY + timedelta(minutes=5)) == {"competitor_digest_duplicate": 3}
    assert len(slack.sent) == 1 and len(mail.sent) == 2
    cd.weekly_step(conn, base_url=BASE, slack=slack, email=mail, now=MONDAY + timedelta(days=7))           # next week: waiting moves are listed again
    assert len(slack.sent) == 2


def test_a_failing_sender_is_recorded_not_raised(conn):
    ct.load_seed(conn)
    user(conn, "mkt@thecodest.co", "marketing")
    candidate(conn)
    assert cd.weekly_step(conn, base_url=BASE, email=Broken(), now=MONDAY) == {"competitor_digest_failed": 1}
    row = conn.execute("SELECT status, error, kind FROM notifications_sent").fetchone()
    assert row["status"] == "failed" and "smtp down" in row["error"] and row["kind"] == "competitor_digest"


def test_email_sink_needs_host_and_sender(monkeypatch):
    monkeypatch.delenv("RI_SMTP_HOST", raising=False)
    assert cd.email_sink() is None
    monkeypatch.setenv("RI_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("RI_SMTP_FROM", "ri@thecodest.co")
    s = cd.email_sink()
    assert s.host == "smtp.gmail.com" and s.port == 587 and s.channel == "email"

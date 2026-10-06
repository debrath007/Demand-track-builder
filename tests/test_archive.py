"""Finished demands leave the lists and the overview 30 days after they finish; dates bring them back."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Demand, StageEvent
from app.services import loss_service
from app.services.demand_service import Period, period_for
from tests.conftest import Client

REF = "DM-000116"  # staffed (joined) in the seed


def finish(db: Session, days_ago: int) -> Demand:
    d = db.scalars(select(Demand).where(Demand.app_ref == REF)).one()
    when = datetime.now(UTC) - timedelta(days=days_ago)
    db.execute(update(StageEvent).where(StageEvent.demand_id == d.id).values(at=when))
    db.execute(update(Demand).where(Demand.id == d.id).values(created_at=when - timedelta(days=60)))
    db.commit()
    return d


def test_period_rules() -> None:
    today = date(2026, 10, 1)
    p = Period(today, 30)
    assert p.shows(date(2026, 1, 1), None) and p.shows(date(2026, 1, 1), today - timedelta(days=30))
    assert not p.shows(date(2026, 1, 1), today - timedelta(days=31))
    q = Period(today, 30, date(2026, 4, 1), date(2026, 6, 30))
    assert q.shows(date(2026, 3, 1), date(2026, 4, 2))  # finished inside the period
    assert q.shows(date(2026, 6, 30), None)  # raised on its last day, still open
    assert not q.shows(date(2026, 7, 1), None) and not q.shows(date(2026, 1, 1), date(2026, 3, 31))


def test_old_finished_demand_moves_to_archived(client: Client, db: Session) -> None:
    finish(db, 10)
    c = client.as_user("kavya")
    assert REF in c.get("/demands").text
    finish(db, 45)
    assert REF not in c.get("/demands").text
    assert REF in c.get("/demands?filter=archived").text and "Archived (1)" in c.get("/demands").text
    joined = (date.today() - timedelta(days=45)).isoformat()
    assert REF in c.get(f"/demands?start={joined}&end={joined}").text  # a period that covers it
    later = (date.today() - timedelta(days=20)).isoformat()
    assert REF not in c.get(f"/demands?start={later}").text
    assert REF in [x["app_ref"] for x in c.get(f"/api/demands?start={joined}").json()]
    assert c.get(f"/demands/{REF}").status_code == 200  # never deleted: its page still opens


def test_overview_keeps_to_the_period(client: Client, db: Session) -> None:
    today = date.today()
    before = loss_service.overview(db, 1, today)
    finish(db, 45)
    o = loss_service.overview(db, 1, today)
    assert (o.live, o.archived, o.open) == (before.live - 1, 1, before.open)
    wide = loss_service.overview(db, 1, today, period_for(db, 1, (today - timedelta(days=90)).isoformat()))
    assert wide.live == before.live and wide.archived == 0
    page = client.as_user("sanjay").get("/overview").text
    assert "Last 30 days" in page and "1 older finished demand not shown" in page
    page = client.as_user("sanjay").get(f"/overview?start={today - timedelta(days=90)}").text
    assert "Live at any time from" in page and 'name="start"' in page


def test_period_quick_picks() -> None:
    today = date(2026, 5, 20)
    assert [key for key, _, _ in Period(today).presets] == ["recent", "quarter", "year"]
    assert Period(today).preset == "recent"
    assert Period(today, start=date(2026, 4, 1)).preset == "quarter"
    assert Period(today, start=date(2026, 1, 1)).preset == "year"
    # anything else is the user's own dates
    assert Period(today, start=date(2026, 3, 3)).preset == "custom"
    assert Period(today, start=date(2026, 4, 1), end=date(2026, 5, 1)).preset == "custom"
    assert Period(date(2026, 12, 31)).presets[1][2] == date(2026, 10, 1)


def test_administrator_sets_the_archive_days(db: Session) -> None:
    from app.services import account_service

    acc = account_service.get_account(db, 1)
    data = {
        "grace_days": "3", "panel_timer_hours": "48", "aging_days": "30", "rejection_limit": "3",
        "margin_threshold": "30", "mail_time": "09:00", "timezone": acc.settings.timezone,
        "billable_hours_per_day": "8", "archive_after_days": "60",
    }  # fmt: skip
    account_service.update_thresholds(db, 1, data)
    assert period_for(db, 1).keep_days == 60

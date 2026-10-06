"""Reset the database contents and load the dummy data in seed/data.py."""

import secrets
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.account_config import DEFAULT_RULES
from app.core.db import Base
from app.models import (
    Account,
    BusinessUnit,
    Candidate,
    Demand,
    Escalation,
    EscalationEvent,
    GtdSubmission,
    Interview,
    InterviewerProfile,
    RateCard,
    StageEvent,
    User,
    UserAccount,
    UserPractice,
)
from seed import acme, data


def reset(db: Session) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.execute(text("ALTER SEQUENCE demand_ref_seq RESTART WITH 1"))


def load(db: Session, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    reset(db)

    account = Account(**data.ACCOUNT, mail_time=time(9, 0), config=data.CONFIG, active=True)
    db.add(account)
    db.flush()
    bus = {
        name: BusinessUnit(
            account_id=account.id,
            name=name,
            active=True,
            delivery_head_name=data.DELIVERY_HEADS[name][0],
            delivery_head_email=data.DELIVERY_HEADS[name][1],
        )
        for name in data.BUSINESS_UNITS
    }
    db.add_all(bus.values())
    db.flush()

    users: dict[str, User] = {}
    for key, name, email, role, level, scope, bu_names, practices, iv in data.USERS:
        u = User(name=name, email=email, phone=data.PHONES.get(key), active=True)
        u.memberships = [
            UserAccount(account_id=account.id, role=role, level=level, visibility_scope=scope, active=True)
        ]
        u.business_units = [bus[b] for b in bu_names]
        u.practice_links = [UserPractice(practice=p) for p in practices]
        if iv:
            u.interviewer_profile = InterviewerProfile(
                practices=practices, skills=iv[0], max_grade=iv[1], active=True
            )
        users[key] = u
    db.add_all(users.values())
    db.flush()
    admin = users["kavya"]

    demands: dict[str, Demand] = {}
    for ref, owner, bu, name, practice, grade, start, status, req, extra in data.DEMANDS:
        fields: dict[str, Any] = {**data.DEFAULTS, **extra}  # type: ignore[dict-item]
        fields["client_rate"] = data.BILL_RATES.get(ref, fields["client_rate"])
        fields["client_rate"] = Decimal(str(fields["client_rate"]))
        d = Demand(
            app_ref=ref,
            account_id=account.id,
            bu_id=bus[bu].id,
            owner_id=users[owner].id,
            name=name,
            practice=practice,
            grade=grade,
            start_date=start,
            status=status,
            submitted_at=None if status == "draft" else now - timedelta(days=10),
            **fields,
        )
        db.add(d)
        db.flush()
        demands[ref] = d
        db.add(
            StageEvent(
                demand_id=d.id, from_stage=None, to_stage=status, origin="app", actor_id=users[owner].id
            )
        )
        if req:
            db.add(
                GtdSubmission(
                    demand_id=d.id,
                    gtd_req_id=req,
                    submitted_by=admin.id,
                    submitted_at=now - timedelta(days=9),
                )
            )

    for ref, etype, esc_level, due_in, detail in data.ESCALATIONS:
        # L2 means the due date has passed with no response.
        due = now + timedelta(days=-abs(due_in) if esc_level == 2 else due_in)
        opened = due - timedelta(days=account.l1_sla_days + (account.l2_sla_days if esc_level == 2 else 0))
        esc = Escalation(
            account_id=account.id, demand_id=demands[ref].id,
            type=etype, level=esc_level, status="open", detail=detail,
            opened_at=opened, due_at=due, notified_level=esc_level,  # seeded as already mailed
            severity=DEFAULT_RULES[etype][1].value, responsible=DEFAULT_RULES[etype][0].value,
        )  # fmt: skip
        db.add(esc)
        db.flush()
        db.add(EscalationEvent(escalation_id=esc.id, kind="opened", level=1, at=opened, note=detail))
        if esc_level == 2:
            db.add(EscalationEvent(escalation_id=esc.id, kind="promoted", level=2, at=due - timedelta(days=3),
                                   note="L1 due date passed"))  # fmt: skip

    for channel, factor in data.CHANNEL_FACTOR.items():
        for region, rf in data.REGION_FACTOR.items():
            for grade, base in data.GRADE_COST.items():
                cost = Decimal(str(round(base * factor * rf, 2)))
                db.add(
                    RateCard(
                        account_id=account.id,
                        grade=grade,
                        practice=None,
                        region=region,
                        channel=channel,
                        cost_rate=cost,
                        effective_from=data.RATES_FROM,
                        created_by=admin.id,
                    )
                )

    for ref, cand, rnd, iv_key, when in data.INTERVIEWS:
        d = demands[ref]
        c = Candidate(
            account_id=account.id, demand_id=d.id, name=cand, channel="fte", current_stage="Internal panel"
        )
        db.add(c)
        db.flush()
        db.add(
            Interview(
                demand_id=d.id,
                candidate_id=c.id,
                round=rnd,
                interviewer_id=users[iv_key].id,
                scheduled_at=datetime(*when, tzinfo=UTC),
                feedback_token=secrets.token_urlsafe(32),
                status="scheduled",
            )
        )

    # A candidate staffing mentioned whose requisition nobody has confirmed yet.
    db.add(Candidate(account_id=account.id, demand_id=None, name="Candidate F", source="manual"))

    load_acme(db, users, now)

    # Postgres owns app refs: continue the sequence after the seeded ones.
    db.execute(
        text("SELECT setval('demand_ref_seq', (SELECT max(substring(app_ref from 4)::int) FROM demands))")
    )
    db.commit()


def load_acme(db: Session, discover_users: dict[str, User], now: datetime) -> None:
    """The second account (seed/acme.py). Shared people get a membership, not a new login."""
    account = Account(**acme.ACCOUNT, mail_time=time(8, 30), config=acme.CONFIG, active=True)
    db.add(account)
    db.flush()
    bus = {
        name: BusinessUnit(
            account_id=account.id,
            name=name,
            active=True,
            delivery_head_name=acme.DELIVERY_HEADS[name][0],
            delivery_head_email=acme.DELIVERY_HEADS[name][1],
        )
        for name in acme.BUSINESS_UNITS
    }
    db.add_all(bus.values())
    db.flush()

    users: dict[str, User] = {}
    for key, name, email, role, level, scope, bu_names, practices, iv in acme.USERS:
        u = discover_users.get(key) or User(name=name, email=email, phone=acme.PHONES.get(key), active=True)
        u.memberships.append(
            UserAccount(account_id=account.id, role=role, level=level, visibility_scope=scope, active=True)
        )
        u.business_units = [*u.business_units, *(bus[b] for b in bu_names)]
        u.practice_links = [*u.practice_links, *(UserPractice(practice=p) for p in practices)]
        if iv:
            profile = u.interviewer_profile or InterviewerProfile(practices=[], skills=[], active=True)
            profile.practices = [*profile.practices, *practices]
            profile.skills = list(dict.fromkeys([*profile.skills, *iv[0]]))
            u.interviewer_profile = profile
        db.add(u)
        users[key] = u
    db.flush()
    admin = users["grace"]

    demands: dict[str, Demand] = {}
    for ref, owner, bu, name, practice, grade, start, status, req, extra in acme.DEMANDS:
        fields: dict[str, Any] = {**acme.DEFAULTS, **extra}
        fields["client_rate"] = Decimal(str(fields["client_rate"]))
        d = Demand(
            app_ref=ref,
            account_id=account.id,
            bu_id=bus[bu].id,
            owner_id=users[owner].id,
            name=name,
            practice=practice,
            grade=grade,
            start_date=start,
            status=status,
            submitted_at=None if status == "draft" else now - timedelta(days=8),
            **fields,
        )
        db.add(d)
        db.flush()
        demands[ref] = d
        db.add(
            StageEvent(demand_id=d.id, from_stage=None, to_stage=status, origin="app", actor_id=d.owner_id)
        )
        if req:
            db.add(
                GtdSubmission(
                    demand_id=d.id,
                    gtd_req_id=req,
                    submitted_by=admin.id,
                    submitted_at=now - timedelta(days=7),
                )
            )

    for channel, factor in acme.CHANNEL_FACTOR.items():
        for grade, base in acme.GRADE_COST.items():
            db.add(
                RateCard(
                    account_id=account.id,
                    grade=grade,
                    practice=None,
                    region="US",
                    channel=channel,
                    cost_rate=Decimal(str(round(base * factor, 2))),
                    effective_from=acme.RATES_FROM,
                    created_by=admin.id,
                )
            )

    for ref, cand, rnd, iv_key, when in acme.INTERVIEWS:
        d = demands[ref]
        c = Candidate(account_id=account.id, demand_id=d.id, name=cand, channel="partner")
        db.add(c)
        db.flush()
        db.add(
            Interview(
                demand_id=d.id,
                candidate_id=c.id,
                round=rnd,
                interviewer_id=users[iv_key].id,
                scheduled_at=datetime(*when, tzinfo=UTC),
                feedback_token=secrets.token_urlsafe(32),
                status="scheduled",
            )
        )

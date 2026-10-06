"""Escalations: anything missed escalates by default (flow-artifact §9).

Triggers (§9.1):
  not submitted   in an admin mail, still no GTD requisition ID at the next working day's mail time
  missing         sent to GTD, not in the BCM sheet after the grace period   } opened by
  dropped         in the previous BCM sheet, gone from the latest            } reconciliation
  incorrect       the BCM sheet marks it "In Correct Demnad"                 }
  aging           no stage change for the account's aging days (linked demands)
  past start      requested start date has passed and there's no DOJ on or before it
  rejection limit panel rejections recorded in the app reach the account's limit (confirmed 24 Sep)
  panel SLA       no feedback within the account's panel hours of a scheduled interview's time

Who acts (§9.2, redesigned 1 Oct): each trigger has a rule in Account settings: on or off, the
*responsible* party (GTD admin team, demand owner or interviewer), a severity and the steps to take.
The responsible person acts; everyone else is only informed.

- L1: the escalation opens with the rule's severity. The responsible person is mailed what to do and
  by when (working days per severity). The demand owner and the GTD team admin are copied.
- L2: the due date passed with no response. Leadership and the BU's delivery head are informed; the
  same responsible person still has to act and is reminded once a day until they do.
- The responsible person closes it with a reason and an action. Asking for more time keeps its level.
  A late-feedback escalation closes itself when the feedback arrives.

`sweep` runs every two hours: open new escalations, mark overdue ones L2, close cleared feedback
ones, then mail whoever hasn't been told yet and send the day's reminders. Opening and mailing are
separate (`notified_level`), so escalations opened by reconciliation are mailed by the next sweep and
a failed mail is retried.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import mail
from app.core.account_config import OTHER_REASON
from app.core.config import get_settings
from app.core.enums import (
    FINISHED,
    DemandStatus,
    EscalationEventKind,
    EscalationStatus,
    EscalationType,
    InterviewOutcome,
    InterviewStatus,
    ResolutionAction,
    Responsible,
    Role,
    Severity,
    StageOrigin,
)
from app.core.security import Actor
from app.core.workdays import add_working_days
from app.models import (
    Account,
    BusinessUnit,
    Candidate,
    Demand,
    Escalation,
    EscalationEvent,
    ExcelImport,
    ExcelRow,
    GtdSubmission,
    Interview,
    StageEvent,
    User,
    leads_bu,
    member_of,
)
from app.services.demand_service import record_stage

# Escalations about the GTD link itself; "resubmit" only makes sense for these.
LINK_TYPES = frozenset(
    {EscalationType.NOT_SUBMITTED, EscalationType.MISSING, EscalationType.DROPPED, EscalationType.INCORRECT}
)
# Stages where the BCM sheet is driving progress, so a long silence means the demand is stuck.
AGING_STAGES = frozenset(
    {
        DemandStatus.LINKED,
        DemandStatus.COVERAGE_REQUIRED,
        DemandStatus.INTERVIEWING,
        DemandStatus.PANEL_SELECTED,
        DemandStatus.PROFILES_WITH_CLIENT,
        DemandStatus.OFFER_IN_PROCESS,
        DemandStatus.OFFER_IN_MARKET,
    }
)
GTD_TEAM_ROLES = (Role.ADMIN, Role.ADMIN_TEAM)


class EscalationError(ValueError):
    pass


def _tz(account: Account) -> ZoneInfo:
    return ZoneInfo(account.settings.timezone)


def due_at(account: Account, opened: datetime, severity: str = Severity.MEDIUM.value) -> datetime:
    """End of the account's working day, N working days after `opened`; N comes from the severity."""
    tz = _tz(account)
    days = account.settings.response_days.get(severity, 2)
    day = add_working_days(opened.astimezone(tz).date(), days)
    return datetime.combine(day, time(23, 59), tz)


def _event(
    db: Session,
    esc: Escalation,
    kind: EscalationEventKind,
    actor_id: int | None = None,
    note: str | None = None,
    at: datetime | None = None,
) -> None:
    db.add(
        EscalationEvent(
            escalation_id=esc.id,
            kind=kind.value,
            level=esc.level,
            actor_id=actor_id,
            note=note,
            at=at or datetime.now(UTC),
        )
    )


def open_escalation(
    db: Session, account: Account, demand: Demand, type_: EscalationType, detail: str, now: datetime
) -> Escalation | None:
    """Open an escalation by the account's rule for this trigger. None when the trigger is switched
    off, or one of this type is already open for the demand."""
    rule = account.settings.rule_for(type_.value)
    if not rule.enabled:
        return None
    existing = db.scalar(
        select(Escalation.id).where(
            Escalation.demand_id == demand.id,
            Escalation.type == type_.value,
            Escalation.status == EscalationStatus.OPEN.value,
        )
    )
    if existing is not None:
        return None
    severity = rule.severity
    if type_ is EscalationType.PAST_START and demand.position_type == "Billable":
        severity = Severity.HIGH  # revenue is being lost every working day
    esc = Escalation(
        demand_id=demand.id,
        type=type_.value,
        level=1,
        status=EscalationStatus.OPEN.value,
        detail=detail,
        opened_at=now,
        due_at=due_at(account, now, severity.value),
        notified_level=0,
        severity=severity.value,
        responsible=rule.responsible.value,
        account_id=account.id,
    )
    db.add(esc)
    db.flush()
    _event(db, esc, EscalationEventKind.OPENED, note=detail, at=now)
    return esc


def open_row_escalation(
    db: Session,
    account: Account,
    *,
    req_id: str,
    name: str | None,
    owner_id: int | None,
    detail: str,
    now: datetime,
) -> Escalation | None:
    """A BCM sheet row whose requisition no demand is linked to. None when the trigger is off, one is
    already open for this requisition, or a person already said to ignore it."""
    type_ = EscalationType.UNLINKED_ROW
    rule = account.settings.rule_for(type_.value)
    if not rule.enabled:
        return None
    seen = db.scalars(
        select(Escalation).where(
            Escalation.account_id == account.id,
            Escalation.type == type_.value,
            Escalation.sheet_req_id == req_id,
        )
    ).all()
    if any(e.status == EscalationStatus.OPEN.value or e.resolved_by is not None for e in seen):
        return None  # open already, or closed by a person as "not ours"
    esc = Escalation(
        account_id=account.id,
        demand_id=None,
        sheet_req_id=req_id,
        sheet_name=name,
        sheet_owner_id=owner_id,
        type=type_.value,
        level=1,
        status=EscalationStatus.OPEN.value,
        detail=detail,
        opened_at=now,
        due_at=due_at(account, now, rule.severity.value),
        notified_level=0,
        severity=rule.severity.value,
        responsible=rule.responsible.value,
    )
    db.add(esc)
    db.flush()
    _event(db, esc, EscalationEventKind.OPENED, note=detail, at=now)
    return esc


def close_linked_rows(
    db: Session, account: Account, linked: set[str], in_sheet: set[str], now: datetime
) -> int:
    """Row escalations answer themselves once the requisition is linked, or it leaves the sheet."""
    n = 0
    for esc in db.scalars(
        select(Escalation).where(
            Escalation.account_id == account.id,
            Escalation.type == EscalationType.UNLINKED_ROW.value,
            Escalation.status == EscalationStatus.OPEN.value,
        )
    ):
        if esc.sheet_req_id in linked:
            _close(db, esc, None, "Linked to a demand", ResolutionAction.NO_ACTION, None, now)
            n += 1
        elif esc.sheet_req_id not in in_sheet:
            _close(db, esc, None, "No longer in the BCM sheet", ResolutionAction.NO_ACTION, None, now)
            n += 1
    return n


def open_for(db: Session, demand_ids: list[int]) -> dict[int, list[Escalation]]:
    out: dict[int, list[Escalation]] = {}
    if demand_ids:
        for e in db.scalars(
            select(Escalation).where(
                Escalation.demand_id.in_(demand_ids), Escalation.status == EscalationStatus.OPEN.value
            )
        ):
            if e.demand_id is not None:
                out.setdefault(e.demand_id, []).append(e)
    return out


# --- Facts the triggers read ------------------------------------------------------------------------


def last_stage_change(db: Session, demand_ids: list[int]) -> dict[int, datetime]:
    if not demand_ids:
        return {}
    rows = db.execute(
        select(StageEvent.demand_id, func.max(StageEvent.at))
        .where(StageEvent.demand_id.in_(demand_ids))
        .group_by(StageEvent.demand_id)
    ).all()
    return {demand_id: at for demand_id, at in rows}


def current_doj(db: Session, account_id: int) -> dict[int, date | None]:
    """Date of joining per demand: from the latest BCM sheet (the row of the demand's current
    requisition) when it has one, otherwise the expected date the demand owner recorded."""
    out: dict[int, date | None] = {
        demand_id: doj
        for demand_id, doj in db.execute(
            select(Demand.id, Demand.expected_doj).where(
                Demand.account_id == account_id, Demand.expected_doj.is_not(None)
            )
        )
    }
    latest = db.scalar(
        select(ExcelImport.id)
        .where(ExcelImport.account_id == account_id)
        .order_by(ExcelImport.imported_at.desc(), ExcelImport.id.desc())
        .limit(1)
    )
    if latest is None:
        return out
    rows = db.execute(
        select(GtdSubmission.demand_id, ExcelRow.doj)
        .join(ExcelRow, ExcelRow.submission_id == GtdSubmission.id)
        .where(ExcelRow.import_id == latest)
    ).all()
    for demand_id, doj in rows:
        if doj is not None or demand_id not in out:
            out[demand_id] = doj
    return out


def notified_at(db: Session, demand_ids: list[int]) -> dict[int, datetime]:
    """When each demand last went out in an admin mail."""
    if not demand_ids:
        return {}
    rows = db.execute(
        select(StageEvent.demand_id, func.max(StageEvent.at))
        .where(StageEvent.demand_id.in_(demand_ids), StageEvent.to_stage == DemandStatus.NOTIFIED.value)
        .group_by(StageEvent.demand_id)
    ).all()
    return {demand_id: at for demand_id, at in rows}


def panel_rejections(db: Session, demand_ids: list[int]) -> dict[int, int]:
    if not demand_ids:
        return {}
    rows = db.execute(
        select(Interview.demand_id, func.count())
        .where(Interview.demand_id.in_(demand_ids), Interview.outcome == InterviewOutcome.REJECT.value)
        .group_by(Interview.demand_id)
    ).all()
    return {demand_id: n for demand_id, n in rows}


def overdue_panels(
    db: Session, account: Account, now: datetime
) -> dict[int, list[tuple[Interview, Candidate]]]:
    """Scheduled interviews with a known time whose feedback is past the panel SLA, by requisition."""
    cutoff = now - timedelta(hours=account.panel_timer_hours)
    rows = db.execute(
        select(Interview, Candidate)
        .join(Candidate, Candidate.id == Interview.candidate_id)
        .where(
            Candidate.account_id == account.id,
            Interview.demand_id.is_not(None),
            Interview.status == InterviewStatus.SCHEDULED.value,
            Interview.scheduled_at.is_not(None),
            Interview.scheduled_at < cutoff,
        )
        .order_by(Interview.scheduled_at)
    ).all()
    out: dict[int, list[tuple[Interview, Candidate]]] = {}
    for iv, c in rows:
        out.setdefault(iv.demand_id, []).append((iv, c))
    return out


def _past_start(d: Demand, doj: date | None, today: date) -> bool:
    start = d.loss_from  # a replacement isn't late until its leaver has gone
    return bool(
        start
        and start < today
        and d.status_enum not in FINISHED
        and d.status_enum is not DemandStatus.DRAFT
        and (doj is None or doj > start)
    )


def is_cleared(db: Session, account: Account, esc: Escalation, demand: Demand | None, now: datetime) -> bool:
    """Has the reason for this escalation gone away? Then "no further action" is allowed."""
    t = esc.type_enum
    if demand is None:  # a sheet row: cleared once a demand is linked to that requisition
        return (
            db.scalar(select(GtdSubmission.id).where(GtdSubmission.gtd_req_id == esc.sheet_req_id))
            is not None
        )
    s = demand.status_enum
    if t is EscalationType.NOT_SUBMITTED:
        return s not in (DemandStatus.SUBMITTED, DemandStatus.NOTIFIED)
    if t is EscalationType.MISSING:
        return s is not DemandStatus.MISSING
    if t is EscalationType.DROPPED:
        return s is not DemandStatus.DROPPED
    if t is EscalationType.INCORRECT:
        return s is not DemandStatus.INCORRECT
    if t is EscalationType.AGING:
        last = last_stage_change(db, [demand.id]).get(demand.id)
        return s not in AGING_STAGES or (last is not None and last > esc.opened_at)
    if t is EscalationType.PAST_START:
        today = now.astimezone(_tz(account)).date()
        return not _past_start(demand, current_doj(db, account.id).get(demand.id), today)
    if t is EscalationType.PANEL_SLA:
        return not overdue_panels(db, account, now).get(demand.id)
    return False  # rejection limit: a person decides what happens next


# --- The sweep --------------------------------------------------------------------------------------


@dataclass
class SweepResult:
    opened: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    mails: int = 0

    @property
    def message(self) -> str:
        return f"{len(self.opened)} opened, {len(self.promoted)} overdue (L2), {self.mails} mails sent"


def open_triggered(db: Session, account: Account, now: datetime, result: SweepResult) -> None:
    tz = _tz(account)
    today = now.astimezone(tz).date()
    demands = list(
        db.scalars(
            select(Demand)
            .where(Demand.account_id == account.id, Demand.status.notin_([s.value for s in FINISHED]))
            .options(selectinload(Demand.submissions))
        )
    )
    ids = [d.id for d in demands]
    mailed = notified_at(db, ids)
    changed = last_stage_change(db, ids)
    doj = current_doj(db, account.id)
    rejected = panel_rejections(db, ids)
    overdue = overdue_panels(db, account, now)

    def raise_(d: Demand, t: EscalationType, detail: str) -> None:
        if open_escalation(db, account, d, t, detail, now) is not None:
            result.opened.append(f"{d.app_ref} {t.label.lower()}")

    for d in demands:
        s = d.status_enum
        # Not submitted: in a mail, no ID by the next working day's mail time.
        if s is DemandStatus.NOTIFIED and not d.submissions and d.id in mailed:
            sent = mailed[d.id].astimezone(tz)
            cutoff = datetime.combine(add_working_days(sent.date(), 1), account.mail_time, tz)
            if now >= cutoff:
                detail = f"In the admin mail of {sent:%d %b}; no GTD requisition ID by {cutoff:%d %b %H:%M}"
                raise_(d, EscalationType.NOT_SUBMITTED, detail)
        # Aging: linked demands with no stage change for the account's aging days.
        if s in AGING_STAGES and d.id in changed:
            quiet = (now - changed[d.id]).days
            if quiet >= account.aging_days:
                since = changed[d.id].astimezone(tz)
                raise_(
                    d, EscalationType.AGING, f"{s.label}, no stage change since {since:%d %b} ({quiet} days)"
                )
        # Rejection limit: too many panel rejections on one requisition.
        n = rejected.get(d.id, 0)
        if n >= account.rejection_limit:
            raise_(
                d,
                EscalationType.REJECTION_LIMIT,
                f"{n} candidates rejected by the panel (limit {account.rejection_limit})",
            )
        # Panel SLA: a scheduled interview with no feedback in time.
        panels = overdue.get(d.id)
        if panels:
            iv, c = panels[0]
            raise_(
                d,
                EscalationType.PANEL_SLA,
                f"{iv.round} for {c.name} on {iv.scheduled_at:%d %b}: no feedback after "
                f"{account.panel_timer_hours} h" + (f" (+{len(panels) - 1} more)" if len(panels) > 1 else ""),
            )
        # Past start date: no DOJ, or DOJ after the requested start.
        if _past_start(d, doj.get(d.id), today):
            late = (today - d.start_date).days  # type: ignore[operator]
            j = doj.get(d.id)
            when = f"DOJ {j:%d %b}, {(j - d.start_date).days} days after start" if j else "no DOJ"  # type: ignore[operator]
            detail = f"Start {d.start_date:%d %b} · {s.label.lower()} · {when} · {late} days late"
            raise_(d, EscalationType.PAST_START, detail)


def promote_overdue(db: Session, account: Account, now: datetime, result: SweepResult) -> None:
    """No response by the due date: it becomes L2. The responsible person keeps the action."""
    overdue = db.scalars(
        select(Escalation)
        .where(
            Escalation.account_id == account.id,
            Escalation.status == EscalationStatus.OPEN.value,
            Escalation.level == 1,
            Escalation.due_at < now,
        )
        .options(selectinload(Escalation.events))
    )
    for esc in overdue:
        esc.level = 2
        _event(db, esc, EscalationEventKind.PROMOTED, note="No response by the due date", at=now)
        d = db.get(Demand, esc.demand_id) if esc.demand_id else None
        result.promoted.append(f"{d.app_ref if d else esc.sheet_req_id} {esc.type_enum.label.lower()}")


def close_cleared_feedback(db: Session, account: Account, now: datetime) -> int:
    """A late-feedback escalation is answered by submitting the feedback: close it once it's in."""
    n = 0
    for esc in db.scalars(
        select(Escalation)
        .join(Demand)
        .where(
            Demand.account_id == account.id,
            Escalation.status == EscalationStatus.OPEN.value,
            Escalation.type == EscalationType.PANEL_SLA.value,
        )
    ):
        d = db.get_one(Demand, esc.demand_id)
        if is_cleared(db, account, esc, d, now):
            _close(db, esc, None, "Feedback submitted", ResolutionAction.NO_ACTION, None, now)
            n += 1
    return n


def sweep(db: Session, account_id: int, now: datetime | None = None) -> SweepResult:
    now = now or datetime.now(UTC)
    account = db.get_one(Account, account_id)
    result = SweepResult()
    open_triggered(db, account, now, result)
    close_cleared_feedback(db, account, now)
    promote_overdue(db, account, now, result)
    db.commit()
    result.mails = notify_pending(db, account, now) + remind_overdue(db, account, now)
    return result


# --- Who hears about it ------------------------------------------------------------------------------


@dataclass
class Audience:
    to: list[str]  # the responsible person or people: they act
    cc: list[str]  # informed only
    names: list[str]  # for the screen: "Demand owner (Priya N.) acts · informed: GTD team admin"
    acts: str = ""  # who acts, in words


def _users(db: Session, account_id: int, *roles: Role) -> list[User]:
    return list(db.scalars(select(User).where(member_of(account_id, *roles))))


def responsible_people(db: Session, account: Account, esc: Escalation, demand: Demand | None) -> list[User]:
    """The people who have to act on this escalation."""
    who = esc.responsible_enum
    people: list[User] = []
    if demand is None or who is Responsible.GTD_TEAM:  # a sheet row has no owner yet: the GTD team's
        people = _users(db, account.id, *GTD_TEAM_ROLES)
    elif who is Responsible.DEMAND_OWNER:
        owner = db.get_one(User, demand.owner_id)
        people = [owner] if owner.active else []
    else:
        ids = db.scalars(
            select(Interview.interviewer_id).where(
                Interview.demand_id == demand.id,
                Interview.status == InterviewStatus.SCHEDULED.value,
                Interview.interviewer_id.is_not(None),
            )
        )
        people = list(db.scalars(select(User).where(User.id.in_(set(ids)), User.active)))
    # Nobody to act (owner left, interviewer not assigned): it falls to the GTD team admin.
    return people or _users(db, account.id, Role.ADMIN)


def audience(db: Session, account: Account, esc: Escalation, demand: Demand | None) -> Audience:
    cfg = account.settings
    bu = db.get_one(BusinessUnit, demand.bu_id) if demand else None
    # The demand's owner; for a sheet row, the sheet's originator when they are a known owner.
    owner_id = demand.owner_id if demand else esc.sheet_owner_id
    owner = db.get(User, owner_id) if owner_id else None
    people = responsible_people(db, account, esc, demand)
    to = [u.email for u in people]
    label = esc.responsible_enum.label if demand else Responsible.GTD_TEAM.label
    acts = f"{label} ({', '.join(u.name for u in people)})"
    informed, cc = [], []
    if owner is not None and owner.active and owner.email not in to:
        informed.append("demand owner")
        cc.append(owner.email)
    team_admin = [u.email for u in _users(db, account.id, Role.ADMIN) if u.email not in to]
    if team_admin:
        informed.append("GTD team admin")
        cc += team_admin
    if esc.level == 2:
        if cfg.l2_inform_leadership:
            informed.append(cfg.escalation_owners.get("L2", "leadership"))
            # only the leadership who cover this demand's BU (all of it, or chosen BUs including it)
            leaders = db.scalars(select(User).where(leads_bu(account.id, demand.bu_id if demand else None)))
            cc += [u.email for u in leaders]
        if cfg.l2_inform_delivery_head and bu is not None and bu.delivery_head_email:
            head = cfg.escalation_owners.get("L1", "delivery head")
            informed.append(f"{head} ({bu.delivery_head_name})" if bu.delivery_head_name else head)
            cc.append(bu.delivery_head_email)
    cc = sorted(set(cc) - set(to))
    names = [f"{acts} acts"] + ([f"informed: {', '.join(informed)}"] if informed else [])
    return Audience(to, cc, names, acts)


def notify_pending(db: Session, account: Account, now: datetime) -> int:
    """Mail each group of responsible people once about every escalation they haven't heard about yet
    (L1: action needed; L2: overdue, with leadership and the delivery head informed)."""
    pending = list(
        db.scalars(
            select(Escalation)
            .where(
                Escalation.account_id == account.id,
                Escalation.status == EscalationStatus.OPEN.value,
                Escalation.notified_level < Escalation.level,
            )
            .order_by(Escalation.level.desc(), Escalation.opened_at)
        )
    )
    today = now.astimezone(_tz(account)).date()
    sent = _mail_groups(db, account, pending, now, reminder=False)
    for esc in pending:
        if esc.notified_level == 2:
            esc.last_reminded_on = today  # the overdue mail counts as today's reminder
    db.commit()
    return sent


def remind_overdue(db: Session, account: Account, now: datetime) -> int:
    """Once a day, remind the responsible person about each overdue (L2) escalation they still owe."""
    today = now.astimezone(_tz(account)).date()
    due = [
        esc
        for esc in db.scalars(
            select(Escalation)
            .where(
                Escalation.account_id == account.id,
                Escalation.status == EscalationStatus.OPEN.value,
                Escalation.level == 2,
                Escalation.notified_level >= 2,
                Escalation.due_at < now,
            )
            .order_by(Escalation.opened_at)
        )
        if esc.last_reminded_on is None or esc.last_reminded_on < today
    ]
    sent = _mail_groups(db, account, due, now, reminder=True)
    for esc in due:
        esc.last_reminded_on = today
    db.commit()
    return sent


def _mail_groups(
    db: Session, account: Account, escalations: list[Escalation], now: datetime, *, reminder: bool
) -> int:
    groups: dict[tuple[int, tuple[str, ...]], list[tuple[Escalation, Demand | None, Audience]]] = defaultdict(
        list
    )
    for esc in escalations:
        d = db.get(Demand, esc.demand_id) if esc.demand_id else None
        a = audience(db, account, esc, d)
        groups[(esc.level, tuple(sorted(a.to)))].append((esc, d, a))
    sent = 0
    for (level, to), items in groups.items():
        # A daily reminder goes to the responsible person only; nobody else needs it again.
        cc = [] if reminder else sorted({c for _, _, a in items for c in a.cc} - set(to))
        mail.send(_compose(account, level, items, list(to), cc, reminder=reminder))
        for esc, _, a in items:
            if not reminder:
                esc.notified_level = esc.level
            note = "Reminded " + a.acts if reminder else "Mailed: " + " · ".join(a.names)
            _event(db, esc, EscalationEventKind.NOTIFIED, note=note, at=now)
        db.commit()  # per group, so a later failure doesn't re-send earlier mails
        sent += 1
    return sent


def _compose(
    account: Account,
    level: int,
    items: list[tuple[Escalation, Demand | None, Audience]],
    to: list[str],
    cc: list[str],
    *,
    reminder: bool = False,
) -> mail.Mail:
    cfg = account.settings
    link = f"{get_settings().app_base_url}/escalations"
    plural = "s" if len(items) > 1 else ""
    state = "Reminder: overdue" if reminder else ("Overdue" if level == 2 else "Action needed")
    top = max((e.severity_enum for e, _, _ in items), key=lambda v: ["low", "medium", "high"].index(v.value))
    subject = (
        f"[Demand Tracker] {account.name} · {state} · {len(items)} escalation{plural} · {top.label} severity"
    )
    tz = _tz(account)
    if level == 1:
        intro = f"{len(items)} escalation{plural} need{'' if plural else 's'} your action."
    else:
        intro = (
            f"{len(items)} escalation{plural} {'are' if plural else 'is'} overdue: no response by the due "
            "date. The responsible person still has to act and is reminded every day until they do."
        )
    text = [intro, ""]
    rows = []
    for esc, d, a in items:
        due = esc.due_at.astimezone(tz)
        steps = cfg.rule_for(esc.type).steps
        when = f"Respond by {due:%d %b}" if level == 1 else f"Was due {due:%d %b}"
        text += [
            f"{esc.type_enum.label} · {esc.severity_enum.label} severity",
            f"  Demand: {d.app_ref} {d.gtd_req_id or ''} {d.name}" if d else f"  Sheet row: {esc.subject}",
            f"  What happened: {esc.detail or ''}",
            f"  Who acts: {a.acts}",
            f"  Steps: {steps}",
            f"  {when}.",
            "",
        ]
        rows.append(
            f"<tr><td style='padding:8px 10px;vertical-align:top'><b>{escape(esc.type_enum.label)}</b><br>"
            f"{escape(esc.severity_enum.label)} severity</td>"
            f"<td style='padding:8px 10px'><span style='font-family:monospace'>"
            f"{escape(d.app_ref if d else esc.sheet_req_id or '')}</span> "
            f"{escape(d.name if d else esc.sheet_name or '')}<br>"
            f"<span style='color:#5E5A50'>{escape(esc.detail or '')}</span><br>"
            f"<b>Who acts:</b> {escape(a.acts)}<br><b>Steps:</b> {escape(steps)}</td>"
            f"<td style='padding:8px 10px;vertical-align:top'>{escape(when)}</td></tr>"
        )
    informed = "Others on this mail are copied for information only; they don't need to act."
    text += [f"Respond with a reason and an action: {link}", informed]
    html = (
        f"<p style='font:15px sans-serif'>{escape(intro)}</p>"
        "<table style='border-collapse:collapse;font:14px sans-serif'>" + "".join(rows) + "</table>"
        f"<p style='font:14px sans-serif'><a href='{link}'>Respond on the Escalations screen</a><br>"
        f"<span style='color:#5E5A50'>{informed}</span></p>"
    )
    return mail.Mail(to=to, cc=cc, subject=subject, text="\n".join(text), html=html)


# --- Resolving ---------------------------------------------------------------------------------------


def can_resolve(actor: Actor, esc: Escalation, demand: Demand | None) -> bool:
    """Only the responsible party responds: the GTD admin team (and its admin) for what is theirs to
    do, the demand owner for their own demand. Late feedback is answered by the interviewer giving it;
    the GTD team may also close it (e.g. after reassigning the interview). Leadership never acts."""
    if esc.status != EscalationStatus.OPEN.value:
        return False
    if demand is not None and esc.responsible_enum is Responsible.DEMAND_OWNER:
        return actor.id == demand.owner_id
    return actor.role in GTD_TEAM_ROLES


def given_more_time(esc: Escalation, now: datetime | None = None) -> bool:
    """The responder extended the due date and that date hasn't passed: nothing is needed from them yet."""
    now = now or datetime.now(UTC)
    last = esc.events[-1] if esc.events else None
    return (
        esc.status == EscalationStatus.OPEN.value
        and last is not None
        and last.kind == EscalationEventKind.EXTENDED.value
        and esc.due_at > now
    )


def close_past_start_on_new_date(db: Session, actor: Actor, demand: Demand) -> bool:
    """The owner moved the start date to the future on the demand page: that answers an open past-start
    escalation the same way "Revise the start date" does. True if one was closed."""
    esc = db.scalar(
        select(Escalation).where(
            Escalation.demand_id == demand.id,
            Escalation.status == EscalationStatus.OPEN.value,
            Escalation.type == EscalationType.PAST_START.value,
        )
    )
    account = db.get_one(Account, demand.account_id)
    now = datetime.now(UTC)
    if esc is None or not can_resolve(actor, esc, demand) or not is_cleared(db, account, esc, demand, now):
        return False
    comment = f"Start date revised to {demand.start_date:%d %b %Y} on the demand page"
    _close(db, esc, actor.id, OTHER_REASON, ResolutionAction.NEW_START, comment, now)
    return True


def who_acts(esc: Escalation) -> str:
    who = esc.responsible_enum
    if esc.demand_id is None:
        return "the GTD admin team"
    if who is Responsible.DEMAND_OWNER:
        return "the demand owner"
    return "the GTD admin team" if who is Responsible.GTD_TEAM else "the interviewer (by giving feedback)"


def allowed_actions(
    db: Session, account: Account, esc: Escalation, demand: Demand | None
) -> list[ResolutionAction]:
    if demand is None:  # a sheet row: matched on the Reconciliation page, or declared not ours here
        return [ResolutionAction.NO_ACTION]
    actions = [ResolutionAction.EXTEND, ResolutionAction.CLOSE]
    if esc.type_enum is EscalationType.PAST_START:
        actions.insert(0, ResolutionAction.NEW_START)
    if esc.type_enum in LINK_TYPES and demand.status_enum not in FINISHED:
        # Resubmit as it is (GTD lost it), or back to the owner to correct it first.
        actions[0:0] = [ResolutionAction.RETURN, ResolutionAction.RESUBMIT]
    if is_cleared(db, account, esc, demand, datetime.now(UTC)):
        actions.append(ResolutionAction.NO_ACTION)
    return actions


def resolve(
    db: Session,
    actor: Actor,
    esc_id: int,
    *,
    reason: str,
    action: str,
    comment: str | None,
    extend_to: date | None = None,
    now: datetime | None = None,
) -> Escalation:
    now = now or datetime.now(UTC)
    esc = db.get(Escalation, esc_id)
    demand = db.get(Demand, esc.demand_id) if esc and esc.demand_id else None
    if esc is None or esc.account_id != actor.account_id:
        raise EscalationError("Escalation not found.")
    if esc.status != EscalationStatus.OPEN.value:
        raise EscalationError("This escalation is already resolved.")
    if not can_resolve(actor, esc, demand):
        raise EscalationError(f"This escalation is for {who_acts(esc)} to respond to.")
    account = db.get_one(Account, actor.account_id)
    if reason not in account.settings.reasons_for(esc.type):
        raise EscalationError("Choose a reason.")
    if reason == OTHER_REASON and not (comment or "").strip():
        raise EscalationError("With 'Other', say what the reason is in the comment.")
    try:
        act = ResolutionAction(action)
    except ValueError as e:
        raise EscalationError("Choose an action.") from e
    if act not in allowed_actions(db, account, esc, demand):
        raise EscalationError(f"'{act.label}' isn't available for this escalation.")
    comment = (comment or "").strip() or None
    note = f"{reason}" + (f": {comment}" if comment else "")
    if demand is None:  # a sheet row declared not ours: closed, and not raised again for this requisition
        _close(db, esc, actor.id, reason, act, comment, now)
        db.commit()
        return esc

    if act is ResolutionAction.EXTEND:
        today = now.astimezone(_tz(account)).date()
        if extend_to is None or extend_to <= today:
            raise EscalationError("Pick a new due date after today.")
        # More time, with a reason. The level stays: an overdue escalation doesn't go quiet again.
        esc.due_at = datetime.combine(extend_to, time(23, 59), _tz(account))
        _event(db, esc, EscalationEventKind.EXTENDED, actor.id, f"Due {extend_to:%d %b %Y}. {note}", now)
        db.commit()
        return esc

    if act is ResolutionAction.NEW_START:
        today = now.astimezone(_tz(account)).date()
        if extend_to is None or extend_to <= today:
            raise EscalationError("Give the revised start date, after today.")
        demand.start_date = extend_to
        comment = f"Start date revised to {extend_to:%d %b %Y}" + (f". {comment}" if comment else "")

    if act is ResolutionAction.RESUBMIT:
        # Back into the next admin mail; the new GTD ID will chain to the old one (gtd_service).
        record_stage(db, demand, DemandStatus.SUBMITTED, actor.id, StageOrigin.APP)
    elif act is ResolutionAction.RETURN:
        # The owner corrects and resubmits; it then goes into the admin mail as a resubmission.
        record_stage(db, demand, DemandStatus.RETURNED, actor.id, StageOrigin.APP)
        _mail_owner_returned(db, demand, reason, comment)
    elif act is ResolutionAction.CLOSE:
        record_stage(db, demand, DemandStatus.CLOSED, actor.id, StageOrigin.APP)
    _close(db, esc, actor.id, reason, act, comment, now)
    if act in (ResolutionAction.CLOSE, ResolutionAction.RESUBMIT, ResolutionAction.RETURN):
        # The demand's other link problems are settled by the same decision.
        for other in db.scalars(
            select(Escalation).where(
                Escalation.demand_id == demand.id,
                Escalation.status == EscalationStatus.OPEN.value,
                Escalation.id != esc.id,
            )
        ):
            if act is ResolutionAction.CLOSE or other.type_enum in LINK_TYPES:
                _close(
                    db, other, actor.id, reason, act, f"With {esc.type_enum.label.lower()} escalation", now
                )
    db.commit()
    return esc


def _mail_owner_returned(db: Session, demand: Demand, reason: str, comment: str | None) -> None:
    owner = db.get_one(User, demand.owner_id)
    if not owner.active:
        return
    link = f"{get_settings().app_base_url}/demands/{demand.app_ref}"
    text = (
        f"{demand.app_ref} ({demand.name}) was sent back to you for correction.\n"
        f"Reason: {reason}"
        + (f"\nWhat to fix: {comment}" if comment else "")
        + f"\n\nCorrect it and resubmit; it then goes back to the GTD admin team for GTD:\n{link}"
    )
    mail.send(
        mail.Mail(to=[owner.email], subject=f"[{demand.app_ref}] Please correct and resubmit", text=text)
    )


def _close(
    db: Session,
    esc: Escalation,
    actor_id: int | None,
    reason: str,
    act: ResolutionAction,
    comment: str | None,
    now: datetime,
) -> None:
    esc.status = EscalationStatus.RESOLVED.value
    esc.reason, esc.action, esc.comment = reason, act.value, comment
    esc.resolved_by, esc.resolved_at = actor_id, now
    _event(
        db,
        esc,
        EscalationEventKind.RESOLVED,
        actor_id,
        f"{act.label}. {reason}" + (f": {comment}" if comment else ""),
        now,
    )


def listing(
    db: Session,
    account_id: int,
    *,
    status: str = "open",
    level: int | None = None,
    type_: str | None = None,
    owner_id: int | None = None,
    bu_ids: frozenset[int] | None = None,
) -> list[tuple[Escalation, Demand | None]]:
    """Escalations of the account; for a demand owner (owner_id), only those on their own demands; for
    leadership over chosen BUs (bu_ids), only those on demands of those BUs (sheet rows have no BU)."""
    stmt = (
        select(Escalation, Demand)
        .outerjoin(Demand, Demand.id == Escalation.demand_id)
        .where(Escalation.account_id == account_id)
        .options(
            selectinload(Demand.submissions), selectinload(Demand.owner), selectinload(Demand.business_unit)
        )
    )
    if status in (EscalationStatus.OPEN.value, EscalationStatus.RESOLVED.value):
        stmt = stmt.where(Escalation.status == status)
    if level in (1, 2):
        stmt = stmt.where(Escalation.level == level)
    if type_:
        stmt = stmt.where(Escalation.type == type_)
    if owner_id is not None:  # their own demands, and sheet rows that name them as the originator
        stmt = stmt.where(or_(Demand.owner_id == owner_id, Escalation.sheet_owner_id == owner_id))
    if bu_ids is not None:
        stmt = stmt.where(Demand.bu_id.in_(bu_ids))
    order =(Escalation.due_at,) if status == "open" else (Escalation.resolved_at.desc(),)
    return [(e, d) for e, d in db.execute(stmt.order_by(*order, Escalation.id)).all()]

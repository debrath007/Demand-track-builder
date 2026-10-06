"""Demands as each actor is allowed to see them.

`visible_demands` is the single scope filter every demand query goes through (screens, JSON API and
later the jobs), driven by the actor's visibility scope, account and BUs.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, false, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import storage
from app.core.account_config import AccountConfig
from app.core.enums import (
    FINISHED,
    LINK_PROBLEMS,
    DemandStatus,
    EscalationStatus,
    MainStage,
    Role,
    Scope,
    StageOrigin,
)
from app.core.security import Actor
from app.models import Account, BusinessUnit, Demand, Escalation, Interview, StageEvent
from app.schemas.raise_demand import DemandForm


def visible_demands(actor: Actor) -> Select[tuple[Demand]]:
    stmt = select(Demand).where(Demand.account_id == actor.account_id)
    match actor.scope:
        case Scope.FULL:
            return stmt
        case Scope.OWN:
            return stmt.where(Demand.owner_id == actor.id)
        case Scope.OWN_BU_READ:
            return stmt.where(or_(Demand.owner_id == actor.id, Demand.bu_id.in_(actor.bu_ids)))
        case Scope.BU_READ:
            return stmt.where(Demand.bu_id.in_(actor.bu_ids))
        case Scope.ASSIGNED_INTERVIEWS:
            assigned = select(Interview.demand_id).where(Interview.interviewer_id == actor.id)
            return stmt.where(Demand.id.in_(assigned))
        case Scope.APP_CONTROLS:
            return stmt.where(false())  # the Administrator runs the app's controls and sees no demands
    raise AssertionError(actor.scope)


def can_edit(actor: Actor, demand: Demand) -> bool:
    """Visibility is wider than edit rights: BU read-only viewers and leadership can't change a demand."""
    return demand.account_id == actor.account_id and (demand.owner_id == actor.id or actor.role is Role.ADMIN)


def account_today(db: Session, account_id: int) -> date:
    tz = db.get(Account, account_id).settings.timezone  # type: ignore[union-attr]
    return datetime.now(ZoneInfo(tz)).date()


FILTERS = (
    {"all": "All", "attention": "Needs attention"}
    | {m.value: m.label for m in MainStage}
    | {"archived": "Archived"}
)
OPEN_MAIN = (MainStage.COVERAGE, MainStage.SELECTION, MainStage.ALLOC_PENDING)


@dataclass(frozen=True)
class Period:
    """Which demands a list or the overview shows.

    With no dates chosen: everything still open, plus what finished (joined or abandoned) in the last
    `keep_days` days; older finished demands are archived. With dates chosen: every demand that was
    live at some point between them, archived ones included.
    """

    today: date
    keep_days: int = 30
    start: date | None = None
    end: date | None = None

    @property
    def chosen(self) -> bool:
        return self.start is not None or self.end is not None

    def archived(self, finished: date | None) -> bool:
        return finished is not None and (self.today - finished).days > self.keep_days

    def shows(self, created: date, finished: date | None) -> bool:
        if not self.chosen:
            return not self.archived(finished)
        if self.end is not None and created > self.end:
            return False
        return not (self.start is not None and finished is not None and finished < self.start)

    @property
    def presets(self) -> list[tuple[str, str, date | None]]:
        """Quick picks above the date fields: (key, label, start). A start alone runs to today."""
        quarter = date(self.today.year, 3 * ((self.today.month - 1) // 3) + 1, 1)
        return [
            ("recent", f"Last {self.keep_days} days", None),
            ("quarter", "This quarter", quarter),
            ("year", "This year", date(self.today.year, 1, 1)),
        ]

    @property
    def preset(self) -> str:
        """Which quick pick is showing, or "custom" for any other dates."""
        if not self.chosen:
            return "recent"
        if self.end is None:
            for key, _, start in self.presets[1:]:
                if self.start == start:
                    return key
        return "custom"

    @property
    def label(self) -> str:
        if not self.chosen:
            return f"Open, or finished in the last {self.keep_days} days"
        a = f"{self.start:%d %b %Y}" if self.start else "the start"
        b = f"{self.end:%d %b %Y}" if self.end else "today"
        return f"Live at any time from {a} to {b}"


def period_for(db: Session, account_id: int, start: str = "", end: str = "") -> Period:
    """The period a screen asked for; dates that don't parse are ignored."""

    def parse(raw: str) -> date | None:
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    a, b = parse(start), parse(end)
    if a and b and a > b:
        a, b = b, a
    account = db.get_one(Account, account_id)
    return Period(account_today(db, account_id), account.settings.archive_after_days, a, b)


def finished_dates(db: Session, demands: list[Demand]) -> dict[int, date]:
    """The day each finished demand reached its final stage (joined, cancelled or closed)."""
    done = {d.id: d.status for d in demands if d.status_enum in FINISHED}
    out: dict[int, date] = {}
    if not done:
        return out
    for e in db.scalars(
        select(StageEvent).where(StageEvent.demand_id.in_(done)).order_by(StageEvent.at, StageEvent.id)
    ):
        if e.to_stage == done[e.demand_id]:
            out[e.demand_id] = e.at.date()  # the latest one wins
    for d in demands:
        # A joined proactive, non-billable position isn't finished while it still costs the account.
        if d.id in out and d.is_proactive_nb and d.status_enum is DemandStatus.STAFFED:
            if d.billable_from is None:
                del out[d.id]
            else:
                out[d.id] = max(out[d.id], d.billable_from)
    return out


@dataclass
class DemandRow:
    demand: Demand
    req_id: str | None
    status_label: str
    chip: str
    note: str
    attention: bool
    read_only: bool
    open_escalations: list[Escalation]
    doj: date | None = None  # from the BCM sheet, or the owner's expected date
    finished_on: date | None = None  # joined or abandoned on this day; None while it is open

    @property
    def main(self) -> MainStage:
        """The main stage; `status_label` is the sub-stage (or the open escalation) within it."""
        return self.demand.status_enum.main


def _describe(d: Demand, escs: list[Escalation], today: date) -> tuple[str, str, str, bool]:
    """Status chip text, colour, the one-line note under the title, and whether it needs attention."""
    s = d.status_enum
    label, chip = s.label, s.chip
    past_start = bool(d.start_date and d.start_date < today and s not in FINISHED)
    notes = {
        DemandStatus.DRAFT: "Not submitted yet",
        DemandStatus.SUBMITTED: "Goes out in the next admin mail",
        DemandStatus.NOTIFIED: "In the admin mail, waiting for GTD entry",
        DemandStatus.SENT_TO_GTD: "On GTD, waiting for the BCM sheet",
        DemandStatus.MISSING: "Not in the BCM sheet after the grace period",
        DemandStatus.DROPPED: "Was in the BCM sheet, gone from the latest one",
        DemandStatus.INCORRECT: "BCM sheet marks it as an incorrect demand",
        DemandStatus.RETURNED: "Sent back to you for correction: fix it and resubmit",
    }
    note = notes.get(s, "")
    if escs:
        top = max(escs, key=lambda e: (e.level, e.opened_at))
        label, chip = f"{top.type_enum.short} · escalated L{top.level}", "esc"
        note = note or (top.detail or "")
    elif past_start and chip != "esc":
        chip = "risk"
    if past_start:
        late = (today - d.start_date).days  # type: ignore[operator]
        note = f"{late} days past start date" + (f" · {note}" if note else "")
    attention = bool(escs) or past_start or s in LINK_PROBLEMS or s is DemandStatus.RETURNED
    return label, chip, note, attention


def demand_rows(db: Session, actor: Actor, today: date | None = None) -> list[DemandRow]:
    """Every demand the actor may see, described for the list screens."""
    today = today or account_today(db, actor.account_id)
    stmt = visible_demands(actor).options(
        selectinload(Demand.submissions), selectinload(Demand.business_unit), selectinload(Demand.owner)
    )
    demands = list(db.scalars(stmt.order_by(Demand.app_ref.desc())))

    escs: dict[int, list[Escalation]] = {}
    if demands:
        for e in db.scalars(
            select(Escalation).where(
                Escalation.demand_id.in_([d.id for d in demands]),
                Escalation.status == EscalationStatus.OPEN.value,
            )
        ):
            if e.demand_id is not None:
                escs.setdefault(e.demand_id, []).append(e)

    from app.services.escalation_service import current_doj  # it imports this module
    from app.services.margin_service import notes as offer_notes  # margin_service imports this module too
    from app.services.pipeline_service import notes as panel_notes  # pipeline_service imports this module

    joining = current_doj(db, actor.account_id)
    finished = finished_dates(db, demands)
    progress = panel_notes(db, demands) | offer_notes(db, demands)
    rows = []
    for d in demands:
        label, chip, note, attention = _describe(d, escs.get(d.id, []), today)
        if d.id in progress and not note:
            note = progress[d.id]
        elif d.id in progress:
            note = f"{note} · {progress[d.id]}"
        rows.append(
            DemandRow(
                demand=d,
                req_id=d.gtd_req_id,
                status_label=label,
                chip=chip,
                note=note,
                attention=attention,
                read_only=not can_edit(actor, d),
                open_escalations=escs.get(d.id, []),
                doj=joining.get(d.id),
                finished_on=finished.get(d.id),
            )
        )
    return rows


def filter_rows(
    rows: list[DemandRow], filter_key: str = "all", bu_id: int | None = None, period: Period | None = None
) -> list[DemandRow]:
    """`period` hides archived demands (or keeps to the chosen dates); without one nothing is hidden."""
    if bu_id:
        rows = [r for r in rows if r.demand.bu_id == bu_id]
    if filter_key == "archived":
        return [r for r in rows if period is not None and period.archived(r.finished_on)]
    if period is not None:
        rows = [r for r in rows if period.shows(r.demand.created_at.date(), r.finished_on)]
    if filter_key == "attention":
        return [r for r in rows if r.attention]
    if filter_key in {m.value for m in MainStage}:
        return [r for r in rows if r.main.value == filter_key]
    return rows


def filter_counts(rows: list[DemandRow], period: Period | None = None) -> dict[str, int]:
    return {key: len(filter_rows(rows, key, None, period)) for key in FILTERS}


def summary(rows: list[DemandRow]) -> dict[str, int]:
    """The cards above the list: open demands, by main stage, and those needing attention."""
    open_rows = [r for r in rows if r.main in OPEN_MAIN]
    return {
        "open": len(open_rows),
        "coverage": sum(r.main is MainStage.COVERAGE for r in rows),
        "selection": sum(r.main is MainStage.SELECTION for r in rows),
        "alloc_pending": sum(r.main is MainStage.ALLOC_PENDING for r in rows),
        "attention": sum(r.attention for r in open_rows),
    }


# --- Intake: raise, edit, submit (Phase 2) ---------------------------------------------------------

# Locked once it's in the admin mail; a demand sent back for correction opens up again.
EDITABLE = frozenset({DemandStatus.DRAFT, DemandStatus.SUBMITTED, DemandStatus.RETURNED})


class DemandError(ValueError):
    pass


def get_visible(db: Session, actor: Actor, app_ref: str) -> Demand | None:
    """A demand the actor may see, or None (callers answer 404, never 403, so refs don't leak)."""
    return db.scalar(
        visible_demands(actor)
        .where(Demand.app_ref == app_ref)
        .options(
            selectinload(Demand.submissions), selectinload(Demand.business_unit), selectinload(Demand.owner)
        )
    )


def can_change(actor: Actor, demand: Demand) -> bool:
    return can_edit(actor, demand) and demand.status_enum in EDITABLE


def can_revise_dates(actor: Actor, demand: Demand) -> bool:
    """Once a demand is on GTD its details are locked, but its dates still move: the owner (or the GTD
    team admin) may change the start date and, for a replacement, the leaver's last working day."""
    s = demand.status_enum
    return can_edit(actor, demand) and s not in EDITABLE and s not in FINISHED


def revise_dates(
    db: Session, actor: Actor, demand: Demand, start: date | None, lwd: date | None
) -> list[str]:
    """Change the start date and last working day. Returns what changed, in words ([] if nothing)."""
    if not can_revise_dates(actor, demand):
        raise DemandError("Only the demand's owner changes its dates, while the demand is open.")
    if start is None:
        raise DemandError("Enter the start date.")
    replacement = demand.type == "Replacement"
    if replacement and lwd is None:
        raise DemandError("Enter the leaver's last working day.")
    changes = []
    if start != demand.start_date:
        was = f"{demand.start_date:%d %b %Y}" if demand.start_date else "not set"
        changes.append(f"Start date: {was} → {start:%d %b %Y}")
        demand.start_date = start
    if replacement and lwd != demand.lwd:
        was = f"{demand.lwd:%d %b %Y}" if demand.lwd else "not set"
        changes.append(f"Last working day: {was} → {lwd:%d %b %Y}")
        demand.lwd = lwd
    db.flush()
    return changes


def record_stage(
    db: Session,
    demand: Demand,
    to: DemandStatus,
    actor_id: int | None,
    origin: StageOrigin = StageOrigin.APP,
    import_id: int | None = None,
) -> bool:
    """Every status change goes through here so it is written as a stage event. True if it changed."""
    before = demand.status
    if before == to.value:
        return False
    demand.status = to.value
    db.add(
        StageEvent(
            demand_id=demand.id,
            from_stage=before,
            to_stage=to.value,
            origin=origin.value,
            actor_id=actor_id,
            import_id=import_id,
        )
    )
    return True


def _resolve_bu(db: Session, actor: Actor, form: DemandForm) -> int:
    if actor.role is Role.DEMAND_OWNER:
        if len(actor.bu_ids) != 1:
            raise DemandError("Your user has no single business unit. Ask the admin to fix your access.")
        return next(iter(actor.bu_ids))  # a demand owner raises for their own BU only
    bu = db.get(BusinessUnit, form.bu_id) if form.bu_id else None
    if bu is None or bu.account_id != actor.account_id or not bu.active:
        raise DemandError("Choose a business unit.")
    return bu.id


def _check(cfg: AccountConfig, form: DemandForm, *, submit: bool, today: date) -> None:
    problems = []
    for label, value, allowed in (
        ("Practice", form.practice, cfg.practices),
        ("Grade", form.grade, cfg.grades),
        ("Category", form.category, cfg.categories),
        ("Region", form.region, cfg.regions),
        ("Work mode", form.work_mode, cfg.work_modes),
    ):
        if value is not None and value not in allowed:
            problems.append(f"{label} '{value}' isn't in the account's list")
    if submit:
        missing = form.missing_for_submit()
        if missing:
            problems.append("Needed before submitting: " + ", ".join(missing))
        if form.start_date and form.start_date < today:
            problems.append("Requested start date is in the past")
    if problems:
        raise DemandError(". ".join(problems) + ".")


def _apply(demand: Demand, form: DemandForm) -> None:
    for field in (
        "name", "practice", "grade", "category", "type", "replaced_resource", "lwd", "position_type",
        "client_interview_required",
        "primary_skills", "secondary_skills", "exp_min", "exp_max", "start_date", "region",
        "location", "work_mode", "hiring_manager",
    ):  # fmt: skip
        setattr(demand, field, getattr(form, field))
    if form.client_rate is not None:  # blank keeps the rate on file (demand owners can't see it)
        demand.client_rate = form.client_rate


Upload = tuple[str, bytes]  # (file name, content) of a job description


def _check_jd(jd: Upload | None) -> None:
    if jd is not None:
        try:
            storage.check(jd[0], jd[1], storage.JD_EXTENSIONS)
        except storage.StorageError as e:
            raise DemandError(f"Job description not saved: {e}.") from e


def _store_jd(demand: Demand, jd: Upload | None) -> None:
    if jd is not None:
        demand.jd_path = storage.save(f"jd/{demand.app_ref}", jd[0], jd[1], storage.JD_EXTENSIONS)


def create_demands(
    db: Session, actor: Actor, form: DemandForm, *, submit: bool, jd: Upload | None = None
) -> list[Demand]:
    """Save (draft) or submit. One demand per position: N positions make N demands with their own refs."""
    account = db.get_one(Account, actor.account_id)
    today = account_today(db, actor.account_id)
    bu_id = _resolve_bu(db, actor, form)
    _check(account.settings, form, submit=submit, today=today)
    _check_jd(jd)
    created = []
    for _ in range(form.positions):
        d = Demand(
            account_id=actor.account_id, bu_id=bu_id, owner_id=actor.id, status=DemandStatus.DRAFT.value
        )
        _apply(d, form)
        db.add(d)
        db.flush()  # Postgres assigns app_ref here
        _store_jd(d, jd)
        db.add(StageEvent(demand_id=d.id, from_stage=None, to_stage=DemandStatus.DRAFT.value,
                          origin=StageOrigin.APP.value, actor_id=actor.id))  # fmt: skip
        if submit:
            _submit(db, actor, d)
        created.append(d)
    db.commit()
    return created


def update_demand(
    db: Session, actor: Actor, demand: Demand, form: DemandForm, *, submit: bool, jd: Upload | None = None
) -> Demand:
    if not can_change(actor, demand):
        raise DemandError("This demand can't be changed any more: it's already with the admin.")
    _check_jd(jd)
    account = db.get_one(Account, actor.account_id)
    _check(account.settings, form, submit=submit or demand.status_enum is DemandStatus.SUBMITTED,
           today=account_today(db, actor.account_id))  # fmt: skip
    if actor.role is not Role.DEMAND_OWNER:
        demand.bu_id = _resolve_bu(db, actor, form)
    _apply(demand, form)
    _store_jd(demand, jd)
    if submit:
        _submit(db, actor, demand)
    db.commit()
    return demand


def _submit(db: Session, actor: Actor, demand: Demand) -> None:
    if demand.status_enum in (DemandStatus.DRAFT, DemandStatus.RETURNED):
        record_stage(db, demand, DemandStatus.SUBMITTED, actor.id)
        demand.submitted_at = datetime.now(UTC)
        db.flush()
        from app.services import notify_service  # it imports this module

        notify_service.safely(notify_service.send_landed, db, demand)


def stage_history(db: Session, demand: Demand) -> list[StageEvent]:
    return list(
        db.scalars(
            select(StageEvent).where(StageEvent.demand_id == demand.id).order_by(StageEvent.at, StageEvent.id)
        )
    )

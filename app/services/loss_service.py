"""Start date and revenue loss (flow-artifact §10), and the leadership overview's numbers.

    days late          = (DOJ or today) − requested start date, when positive; counted to today at most
    working days late  = working days from the start date up to that end (weekends excluded)
    revenue lost       = hourly client bill rate × billable hours per day × working days late

Only live demands count: past draft, not cancelled or closed. A staffed demand that joined late keeps
its loss. A DOJ still in the future adds a projection on top of the loss to date. Demands with no
bill rate are counted as late but their loss is unknown, and the overview says how many.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import DemandStatus, MainStage
from app.core.workdays import is_working_day
from app.models import Account, BusinessUnit, Demand
from app.services.demand_service import Period, finished_dates
from app.services.escalation_service import current_doj

NOT_LIVE = frozenset({DemandStatus.DRAFT, DemandStatus.CANCELLED, DemandStatus.CLOSED})


def working_days(start: date, end: date) -> int:
    """Working days d with start <= d < end."""
    n, d = 0, start
    while d < end:
        n += is_working_day(d)
        d += timedelta(days=1)
    return n


@dataclass
class Loss:
    demand: Demand
    doj: date | None
    days_late: int  # calendar days, to today at most
    working_days_late: int
    lost: Decimal | None  # to date; None when the bill rate is missing
    projected: Decimal | None  # extra loss until a future DOJ

    @property
    def filled(self) -> bool:
        """Joined, or has a DOJ on or before the start: not at risk any more."""
        return self.demand.status_enum is DemandStatus.STAFFED or (
            self.doj is not None and self.demand.loss_from is not None and self.doj <= self.demand.loss_from
        )


def losses(db: Session, account_id: int, today: date) -> list[Loss]:
    account = db.get_one(Account, account_id)
    hours = Decimal(str(account.settings.billable_hours_per_day))
    doj = current_doj(db, account_id)
    out = []
    for d in db.scalars(
        select(Demand)
        .where(Demand.account_id == account_id, Demand.status.notin_([s.value for s in NOT_LIVE]))
        .options(selectinload(Demand.submissions), selectinload(Demand.business_unit))
    ):
        start = d.loss_from  # the start date, or the day after the leaver's last working day if later
        if start is None or d.is_proactive_nb:  # proactive, non-billable: a cost, never revenue lost
            continue
        j = doj.get(d.id)
        end = min(j, today) if j else today
        if end <= start:
            continue
        wd = working_days(start, end)
        daily = d.client_rate * hours if d.client_rate is not None else None
        projected = None
        if j and j > today and daily is not None:
            projected = daily * working_days(max(today, start), j)
        out.append(Loss(d, j, (end - start).days, wd, daily * wd if daily is not None else None, projected))
    return sorted(out, key=lambda x: (-x.days_late, x.demand.app_ref))


# --- Leadership overview -----------------------------------------------------------------------------

# Pipeline groups, in the order the demand moves through them.
# The pipeline is read by main stage (the five stages leadership talks in); each bar breaks down into
# its sub-stages.
GROUPS: list[tuple[str, str, tuple[DemandStatus, ...]]] = [(m.value, m.label, m.subs) for m in MainStage]
GROUP_OF = {s: s.main.value for s in DemandStatus}
OPEN_GROUPS = (MainStage.COVERAGE.value, MainStage.SELECTION.value, MainStage.ALLOC_PENDING.value)
# Still looking for someone: linked to GTD and sourcing, nobody in the panel yet.
NEED_COVERAGE = (DemandStatus.LINKED, DemandStatus.COVERAGE_REQUIRED)
# The four kinds of open position, in a fixed order (the order is the chart's colour order).
MIX = ("New · billable", "Replacement · billable", "New · non-billable", "Replacement · non-billable")


@dataclass
class Slice:
    name: str
    total: int = 0
    open: int = 0
    by_group: dict[str, int] = field(default_factory=dict)
    lost: Decimal = Decimal(0)

    @property
    def mix(self) -> str:
        labels = {k: label.lower() for k, label, _ in GROUPS}
        return " · ".join(f"{n} {labels[k]}" for k, n in self.by_group.items() if n)


@dataclass
class Overview:
    today: date
    pipeline: list[tuple[str, str, int]]  # main stage: key, label, count
    subs: dict[str, list[tuple[str, int]]]  # main stage key → (sub-stage name, count), non-empty only
    mix: dict[str, int]  # open positions: new or replacement × billable or non-billable
    open: int
    live: int
    need_coverage: int
    at_risk: list[Loss]  # past start, not filled
    lost_to_date: Decimal
    projected: Decimal
    missing_rates: int
    by_practice: list[Slice]
    by_bu: list[Slice]
    hours_per_day: float
    period: Period
    demands: list[Demand]  # everything in view, for the page to slice
    loss_of: dict[int, Loss]
    archived: int  # finished demands left out because they are older than the period shows


def overview(
    db: Session,
    account_id: int,
    today: date,
    period: Period | None = None,
    bu_ids: frozenset[int] | None = None,
) -> Overview:
    """The account's picture. Without dates: open demands and those finished within the archive window.
    With dates: demands that were live at some point in the period. `bu_ids` narrows it to those
    business units (leadership over chosen BUs); everything below follows from that one query."""
    account = db.get_one(Account, account_id)
    period = period or Period(today, account.settings.archive_after_days)
    stmt = (
        select(Demand)
        .where(Demand.account_id == account_id, Demand.status != DemandStatus.DRAFT.value)
        .options(selectinload(Demand.business_unit), selectinload(Demand.owner))
    )
    if bu_ids is not None:
        stmt = stmt.where(Demand.bu_id.in_(bu_ids))
    everything = list(db.scalars(stmt))
    finished = finished_dates(db, everything)
    demands = [d for d in everything if period.shows(d.created_at.date(), finished.get(d.id))]
    shown = {d.id for d in demands}
    counts = {key: 0 for key, _, _ in GROUPS}
    by_sub: dict[DemandStatus, int] = {}
    for d in demands:
        counts[GROUP_OF[d.status_enum]] += 1
        by_sub[d.status_enum] = by_sub.get(d.status_enum, 0) + 1
    subs: dict[str, list[tuple[str, int]]] = {}
    for m in MainStage:
        merged: dict[str, int] = {}  # two statuses can share a sub-stage name (GTD creation pending)
        for st in m.subs:
            if by_sub.get(st):
                merged[st.label] = merged.get(st.label, 0) + by_sub[st]
        subs[m.value] = list(merged.items())

    mix = dict.fromkeys(MIX, 0)
    for d in demands:
        if GROUP_OF[d.status_enum] in OPEN_GROUPS:
            kind = "Replacement" if d.type == "Replacement" else "New"
            mix[f"{kind} · {'non-billable' if d.position_type == 'Non-billable' else 'billable'}"] += 1

    loss = [x for x in losses(db, account_id, today) if x.demand.id in shown]
    lost_by_demand = {x.demand.id: x.lost or Decimal(0) for x in loss}

    def slices(key_of: object) -> list[Slice]:
        acc: dict[str, Slice] = {}
        for d in demands:
            name = key_of(d) or "—"  # type: ignore[operator]
            s = acc.setdefault(name, Slice(name, by_group={k: 0 for k, _, _ in GROUPS}))
            g = GROUP_OF[d.status_enum]
            s.total += 1
            s.open += g in OPEN_GROUPS
            s.by_group[g] += 1
            s.lost += lost_by_demand.get(d.id, Decimal(0))
        return sorted(acc.values(), key=lambda s: (-s.total, s.name))

    return Overview(
        today=today,
        pipeline=[(k, label, counts[k]) for k, label, _ in GROUPS],
        subs=subs,
        mix=mix,
        open=sum(counts[k] for k in OPEN_GROUPS),
        live=len(demands),
        need_coverage=sum(by_sub.get(st, 0) for st in NEED_COVERAGE),
        at_risk=[x for x in loss if not x.filled],
        lost_to_date=sum((x.lost for x in loss if x.lost is not None), Decimal(0)),
        projected=sum((x.projected for x in loss if x.projected is not None), Decimal(0)),
        missing_rates=sum(x.lost is None for x in loss),
        by_practice=slices(lambda d: d.practice),
        by_bu=slices(lambda d: d.business_unit.name),
        hours_per_day=account.settings.billable_hours_per_day,
        period=period,
        demands=demands,
        loss_of={x.demand.id: x for x in loss},
        archived=sum(period.archived(finished.get(d.id)) for d in everything if d.id not in shown),
    )


# --- Non-billable positions against the agreed cap ---------------------------------------------------------


@dataclass
class NbRow:
    bu: BusinessUnit
    used: int  # open non-billable demands (submitted onwards, not staffed out or closed)
    by_practice: dict[str, int]

    @property
    def over(self) -> bool:
        return self.bu.nb_cap is not None and self.used > self.bu.nb_cap


def non_billable_by_bu(db: Session, account_id: int) -> list[NbRow]:
    """Per business unit: open non-billable (proactive) positions against the cap the GTD team admin set."""
    counts: dict[int, dict[str, int]] = {}
    for d in db.scalars(
        select(Demand).where(
            Demand.account_id == account_id,
            Demand.position_type == "Non-billable",
            Demand.status.notin_([s.value for s in NOT_LIVE]),
        )
    ):
        per = counts.setdefault(d.bu_id, {})
        per[d.practice or "—"] = per.get(d.practice or "—", 0) + 1
    bus = db.scalars(
        select(BusinessUnit)
        .where(BusinessUnit.account_id == account_id, BusinessUnit.active)
        .order_by(BusinessUnit.name)
    )
    return [NbRow(b, sum(counts.get(b.id, {}).values()), counts.get(b.id, {})) for b in bus]

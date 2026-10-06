"""Offer margin approvals (flow-artifact §8).

When a demand reaches Offer in process, its candidate's offer is priced:
    margin = (client bill rate − vendor cost rate) ÷ client bill rate
with the cost from the rate card in force on the offer date. At or above the account's cut-off (30% for
Discover) the GTD team admin approves or declines; below it, leadership decides at their
discretion (confirmed 24 Sep). Every decision records approver, margin and time.

An offer whose bill rate, supply channel or rate card entry is missing waits unpriced, with the reason
shown, and is re-priced whenever the approvals screen loads or the rate card changes.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.account_config import AccountConfig
from app.core.config import get_settings
from app.core.enums import FINISHED, PROGRESS_ORDER, ApprovalRoute, Decision, DemandStatus, Role, StageOrigin
from app.core.security import Actor
from app.models import Account, Candidate, Demand, OfferApproval, User, leads_bu, member_of
from app.services import interview_service, notify_service, rate_card_service
from app.services.demand_service import record_stage

OFFER_STAGES = (DemandStatus.OFFER_IN_PROCESS, DemandStatus.OFFER_IN_MARKET)


class ApprovalError(ValueError):
    pass


def channel_for_source(cfg: AccountConfig, source: str | None) -> str | None:
    """Map the BCM sheet's Source cell (e.g. 'VMS', 'Sogeti') to a supply channel key."""
    s = (source or "").strip().casefold()
    if not s:
        return None
    for c in cfg.supply_channels:
        names = [c.key.casefold(), c.label.casefold(), c.sheet_marker.casefold()]
        if any(s == n or s in n.split() or n in s for n in names if n):
            return c.key
    return None


def _local_date(account: Account, when: datetime) -> date:
    return when.astimezone(ZoneInfo(account.settings.timezone)).date()


def price(db: Session, account: Account, approval: OfferApproval, demand: Demand) -> None:
    """Fill bill, cost, margin and route, or leave it unpriced with the reason."""
    on = approval.priced_on or _local_date(account, approval.created_at or datetime.now(UTC))
    approval.priced_on = on
    approval.bill_rate = demand.client_rate
    approval.cost_rate = approval.margin_pct = approval.route = None
    cfg = account.settings
    channel_label = next(
        (c.label for c in cfg.supply_channels if c.key == approval.channel), approval.channel
    )
    if not demand.client_rate:
        approval.blocked_reason = "The demand has no client bill rate. Add it on the demand."
        return
    if not approval.channel:
        approval.blocked_reason = "The supply channel isn't known. Set it on this offer."
        return
    rate = rate_card_service.lookup(
        db, account.id, grade=demand.grade or "", practice=demand.practice, region=demand.region or "",
        channel=approval.channel, on=on,
    )  # fmt: skip
    if rate is None:
        approval.blocked_reason = (
            f"No rate card entry for {demand.grade} · {demand.practice} · {demand.region} · {channel_label} "
            f"on {on:%d %b %Y}."
        )
        return
    bill = Decimal(demand.client_rate)
    margin = ((bill - rate.cost_rate) / bill * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    approval.cost_rate, approval.margin_pct = rate.cost_rate, margin
    approval.route = (
        ApprovalRoute.ADMIN if margin >= account.margin_threshold else ApprovalRoute.LEADERSHIP
    ).value
    approval.blocked_reason = None


def ensure_offer(
    db: Session, account: Account, demand: Demand, candidate_name: str, channel: str | None
) -> OfferApproval | None:
    """Create the candidate (if new) and a pending approval for their offer, once per candidate."""
    names = interview_service.split_names(candidate_name)
    if not names:
        return None
    cand = interview_service.ensure_candidate(db, account.id, demand, names[0], channel=channel)
    already = db.scalar(select(OfferApproval.id).where(OfferApproval.candidate_id == cand.id))
    if already is not None:
        return None  # its stage already says where the approval is; a re-import mustn't reset it
    cand.current_stage = DemandStatus.OFFER_IN_PROCESS.label
    approval = OfferApproval(demand_id=demand.id, candidate_id=cand.id, channel=channel or cand.channel,
                             created_at=datetime.now(UTC))  # fmt: skip
    db.add(approval)
    price(db, account, approval, demand)
    db.flush()
    notify_service.safely(_mail_raised, db, account, demand, cand, approval)
    return approval


def _team_admins(db: Session, account_id: int) -> list[str]:
    return [u.email for u in db.scalars(select(User).where(member_of(account_id, Role.ADMIN)))]


def _numbers(approval: OfferApproval) -> str:
    if approval.margin_pct is None:
        return f"It can't be priced yet: {approval.blocked_reason}"
    return f"Bill {approval.bill_rate}/h, vendor cost {approval.cost_rate}/h, margin {approval.margin_pct}%."


def _mail_raised(
    db: Session, account: Account, demand: Demand, cand: Candidate, approval: OfferApproval
) -> None:
    """An offer needs a decision: mail whoever decides it. The GTD team admin is always notified."""
    owner = db.get_one(User, demand.owner_id)
    admins = _team_admins(db, account.id)
    if approval.route == ApprovalRoute.LEADERSHIP.value:
        to = [u.email for u in db.scalars(select(User).where(leads_bu(account.id, demand.bu_id)))]
        who = f"Below the {account.margin_threshold:g}% cut-off: leadership decides."
    else:  # at or above the cut-off, or not priced yet: the demand owner
        to = [owner.email] if owner.active else []
        who = "The demand owner decides." if approval.route else "The demand owner decides once it is priced."
    to = to or admins
    cc = sorted(({owner.email} if owner.active else set()) | set(admins) - set(to))
    ref = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    mail.send(
        mail.Mail(
            to=to,
            cc=[c for c in cc if c not in to],
            subject=f"[{ref}] Offer approval needed for {cand.name}",
            text=(
                f"An offer for {cand.name} on {demand.app_ref} ({demand.name}) needs a decision.\n"
                f"{_numbers(approval)}\n{who}\n\n"
                "Steps: open Offer approvals, check the margin, then approve or decline:\n"
                f"{get_settings().app_base_url}/approvals\n\n"
                "Others on this mail are notified only."
            ),
        )
    )


def request(db: Session, actor: Actor, demand_id: int, candidate_name: str, channel: str) -> OfferApproval:
    """The admin raises an approval by hand (e.g. the BCM sheet row had no candidate name)."""
    demand = db.get(Demand, demand_id)
    if demand is None or demand.account_id != actor.account_id:
        raise ApprovalError("Demand not found.")
    if demand.status_enum not in OFFER_STAGES:
        raise ApprovalError(f"{demand.app_ref} isn't at the offer stage.")
    account = db.get_one(Account, actor.account_id)
    if channel not in {c.key for c in account.settings.supply_channels}:
        raise ApprovalError("Choose the supply channel.")
    approval = ensure_offer(db, account, demand, candidate_name, channel)
    if approval is None:
        raise ApprovalError("Enter the candidate's name (an approval for that candidate already exists?).")
    db.commit()
    return approval


# Where a candidate can be put forward for an offer: the panel has selected, or the client has.
ASK_STAGES = (
    DemandStatus.PANEL_SELECTED,
    DemandStatus.PROFILES_WITH_CLIENT,
    DemandStatus.OFFER_IN_PROCESS,
    DemandStatus.OFFER_IN_MARKET,
)


def askable(db: Session, demand: Demand) -> list[Candidate]:
    """Candidates the panel selected on this demand who have no offer approval yet."""
    if demand.status_enum not in ASK_STAGES:
        return []
    have = set(db.scalars(select(OfferApproval.candidate_id).where(OfferApproval.demand_id == demand.id)))
    out = []
    for c, ivs in interview_service.for_demand(db, demand.id):
        if c.id not in have and c.client_outcome != "reject" and any(iv.outcome == "select" for iv in ivs):
            out.append(c)
    return out


def ask(db: Session, actor: Actor, demand: Demand, candidate_id: int, channel: str) -> OfferApproval:
    """The demand owner raises the offer approval for a selected candidate, instead of waiting for
    the BCM sheet to show the offer. It is priced and routed like any other (ensure_offer mails it)."""
    if demand.account_id != actor.account_id or actor.id != demand.owner_id:
        raise ApprovalError("Only the demand's owner asks for its offer approval.")
    cand = next((c for c in askable(db, demand) if c.id == candidate_id), None)
    if cand is None:
        raise ApprovalError("Pick a candidate the panel has selected, with no approval yet.")
    account = db.get_one(Account, actor.account_id)
    if channel not in {c.key for c in account.settings.supply_channels}:
        raise ApprovalError("Choose the supply channel the candidate comes through.")
    approval = ensure_offer(db, account, demand, cand.name, channel)
    if approval is None:
        raise ApprovalError("An approval for this candidate already exists.")
    db.commit()
    return approval


def set_channel(db: Session, actor: Actor, approval_id: int, channel: str) -> OfferApproval:
    approval, demand, account = _pending(db, actor, approval_id)
    if channel not in {c.key for c in account.settings.supply_channels}:
        raise ApprovalError("Choose the supply channel.")
    approval.channel = channel
    price(db, account, approval, demand)
    db.commit()
    return approval


def reprice_pending(db: Session, account_id: int) -> int:
    account = db.get_one(Account, account_id)
    n = 0
    for approval in db.scalars(
        select(OfferApproval)
        .join(Demand)
        .where(Demand.account_id == account_id, OfferApproval.decision.is_(None))
    ):
        before = (approval.margin_pct, approval.route, approval.blocked_reason)
        price(db, account, approval, db.get_one(Demand, approval.demand_id))
        n += before != (approval.margin_pct, approval.route, approval.blocked_reason)
    db.commit()
    return n


def _pending(db: Session, actor: Actor, approval_id: int) -> tuple[OfferApproval, Demand, Account]:
    approval = db.get(OfferApproval, approval_id)
    demand = db.get(Demand, approval.demand_id) if approval else None
    if approval is None or demand is None or demand.account_id != actor.account_id:
        raise ApprovalError("Offer approval not found.")
    if approval.decision is not None:
        raise ApprovalError("This offer has already been decided.")
    return approval, demand, db.get_one(Account, actor.account_id)


def can_decide(actor: Actor, approval: OfferApproval, demand: Demand) -> bool:
    """At or above the cut-off the demand's owner decides; below it, leadership over the demand's BU."""
    if approval.decision is not None or approval.route is None:
        return False
    if approval.route == ApprovalRoute.LEADERSHIP.value:
        return actor.role is Role.LEADERSHIP and (actor.bu_limit is None or demand.bu_id in actor.bu_limit)
    return actor.id == demand.owner_id


def decide(db: Session, actor: Actor, approval_id: int, decision: str, comment: str | None) -> OfferApproval:
    approval, demand, account = _pending(db, actor, approval_id)
    price(db, account, approval, demand)  # decide on current numbers
    if approval.route is None:
        raise ApprovalError(f"Can't decide yet: {approval.blocked_reason}")
    if not can_decide(actor, approval, demand):
        who = ApprovalRoute(approval.route).decider
        cut = f"{account.margin_threshold:g}%"
        raise ApprovalError(f"A {approval.margin_pct}% margin offer is decided by {who} (cut-off {cut}).")
    try:
        d = Decision(decision)
    except ValueError as e:
        raise ApprovalError("Approve or decline.") from e
    comment = (comment or "").strip() or None
    below = approval.route == ApprovalRoute.LEADERSHIP.value
    if not comment and (d is Decision.DECLINED or below):
        raise ApprovalError(
            "Add a comment: why it's declined."
            if d is Decision.DECLINED
            else "Add a comment: why the exception."
        )
    approval.decision, approval.comment = d.value, comment
    approval.approver_id, approval.decided_at = actor.id, datetime.now(UTC)
    cand = db.get_one(Candidate, approval.candidate_id)
    cand.current_stage = "Offer approved" if d is Decision.APPROVED else "Offer declined"
    _mail_decision(db, account, demand, cand, approval, actor)
    db.commit()
    return approval


def _mail_decision(
    db: Session, account: Account, demand: Demand, cand: Candidate, approval: OfferApproval, actor: Actor
) -> None:
    """The decision goes to the GTD team admin (always notified) and to the demand owner when someone
    else decided."""
    owner = db.get_one(User, demand.owner_id)
    to = set(_team_admins(db, account.id)) | ({owner.email} if owner.active else set())
    to.discard(actor.email)
    approved = approval.decision == Decision.APPROVED.value
    word = "approved" if approved else "declined"
    ref = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    by = actor.name.rstrip(".")
    text = f"The offer for {cand.name} on {demand.app_ref} ({demand.name}) was {word} by {by}.\n"
    text += f"{_numbers(approval)}\n"
    if approval.comment:
        text += f"Reason: {approval.comment}\n"
    text += (
        "Next: staffing makes the offer. Once it is accepted, the demand owner records the expected "
        "date of joining on the demand; the BCM sheet confirms it.\n"
        if approved
        else "Staffing goes back to the other candidates.\n"
    )
    text += f"\n{get_settings().app_base_url}/demands/{demand.app_ref}"
    mail.send(mail.Mail(to=sorted(to), subject=f"[{ref}] Offer for {cand.name} {word}", text=text))


@dataclass
class OfferStatus:
    candidate: str
    state: str  # what the owner sees
    chip: str
    detail: str | None
    approval: OfferApproval  # rates and margin: only for those who may see them


def notes(db: Session, demands: list[Demand]) -> dict[int, str]:
    """One line per demand at the offer stage for the demands list: where its latest offer stands."""
    out = {}
    for d in demands:
        if d.status_enum in OFFER_STAGES and (offers := for_demand(db, d.id)):
            o = offers[-1]
            out[d.id] = f"Offer for {o.candidate}: {o.state.lower()}" + (
                f", {o.detail}" if o.state == "Waiting for approval" else ""
            )
    return out


def for_demand(db: Session, demand_id: int) -> list[OfferStatus]:
    """Where each offer on the demand stands, in words a demand owner may read."""
    out = []
    rows = db.execute(
        select(OfferApproval, Candidate)
        .join(Candidate, Candidate.id == OfferApproval.candidate_id)
        .where(OfferApproval.demand_id == demand_id)
        .order_by(OfferApproval.created_at)
    ).all()
    for a, c in rows:
        who = db.get(User, a.approver_id).name if a.approver_id else None  # type: ignore[union-attr]
        when = a.decided_at.strftime("%d %b") if a.decided_at else ""
        if a.decision == Decision.APPROVED.value:
            out.append(OfferStatus(c.name, "Approved", "done", f"by {who}, {when}", a))
        elif a.decision == Decision.DECLINED.value:
            out.append(OfferStatus(c.name, "Declined", "esc", f"by {who}, {when}: {a.comment}", a))
        elif a.route == ApprovalRoute.ADMIN.value:
            out.append(OfferStatus(c.name, "Waiting for approval", "risk", "with the demand owner", a))
        elif a.route == ApprovalRoute.LEADERSHIP.value:
            out.append(OfferStatus(c.name, "Waiting for approval", "risk", "with leadership", a))
        else:
            out.append(
                OfferStatus(c.name, "Being priced", "gray", "the GTD admin team is completing the details", a)
            )
    return out


@dataclass
class Board:
    mine: list[tuple[OfferApproval, Demand, Candidate]]  # waiting for this actor
    others: list[tuple[OfferApproval, Demand, Candidate]]  # waiting for someone else to decide
    blocked: list[tuple[OfferApproval, Demand, Candidate]]
    decided: list[tuple[OfferApproval, Demand, Candidate]]
    without_request: list[Demand]  # at the offer stage with no approval yet


def board(db: Session, actor: Actor) -> Board:
    reprice_pending(db, actor.account_id)
    rows = db.execute(
        select(OfferApproval, Demand, Candidate)
        .join(Demand, Demand.id == OfferApproval.demand_id)
        .join(Candidate, Candidate.id == OfferApproval.candidate_id)
        .where(Demand.account_id == actor.account_id)
        .order_by(OfferApproval.created_at.desc())
    ).all()
    if actor.role is Role.DEMAND_OWNER:  # a demand owner sees the offers on their own demands only
        rows = [r for r in rows if r[1].owner_id == actor.id]
    elif actor.bu_limit is not None:  # leadership over chosen BUs: those BUs' offers only
        rows = [r for r in rows if r[1].bu_id in actor.bu_limit]
    b = Board([], [], [], [], [])
    for a, d, c in rows:
        item = (a, d, c)
        if a.decision is not None:
            b.decided.append(item)
        elif a.route is None:
            b.blocked.append(item)
        elif can_decide(actor, a, d):
            b.mine.append(item)
        else:
            b.others.append(item)
    has = {d.id for _, d, _ in rows}
    b.without_request = (
        []
        if actor.role is not Role.ADMIN
        else [
            d
            for d in db.scalars(
                select(Demand)
                .where(
                    Demand.account_id == actor.account_id,
                    Demand.status == DemandStatus.OFFER_IN_PROCESS.value,
                )
                .order_by(Demand.app_ref)
            )
            if d.id not in has
        ]
    )
    return b


# --- After approval: offer accepted, joining date --------------------------------------------------


def can_set_joining(db: Session, actor: Actor, demand: Demand) -> bool:
    """The demand's owner, once an offer on it has been approved and it isn't finished."""
    if actor.id != demand.owner_id or demand.status_enum in (*FINISHED, DemandStatus.DRAFT):
        return False
    return (
        db.scalar(
            select(OfferApproval.id).where(
                OfferApproval.demand_id == demand.id, OfferApproval.decision == Decision.APPROVED.value
            )
        )
        is not None
    )


def set_joining_date(db: Session, actor: Actor, demand: Demand, when: date | None) -> None:
    """The offer was accepted: the owner records the expected date of joining. The demand moves to
    "offer made, joining awaited" now; the BCM sheet's date replaces this one when it arrives."""
    if not can_set_joining(db, actor, demand):
        raise ApprovalError("The demand's owner records the joining date, once an offer is approved.")
    if when is None:
        raise ApprovalError("Enter the expected date of joining.")
    demand.expected_doj = when
    if demand.status_enum in PROGRESS_ORDER and PROGRESS_ORDER.index(
        demand.status_enum
    ) < PROGRESS_ORDER.index(DemandStatus.OFFER_IN_MARKET):
        record_stage(db, demand, DemandStatus.OFFER_IN_MARKET, actor.id, StageOrigin.APP)
    account = db.get_one(Account, demand.account_id)
    ref = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    late = ""
    if demand.start_date and when > demand.start_date:
        late = f" That is {(when - demand.start_date).days} days after the requested start."
    notify_service.safely(
        mail.send,
        mail.Mail(
            to=_team_admins(db, account.id),
            cc=[actor.email],
            subject=f"[{ref}] Offer accepted, joining {when:%d %b %Y}",
            text=(
                f"{actor.name} recorded that the offer on {demand.app_ref} ({demand.name}) was accepted, "
                f"with an expected date of joining of {when:%d %b %Y}.{late}\n"
                "The BCM sheet's date of joining replaces this one when it arrives.\n\n"
                f"{get_settings().app_base_url}/demands/{demand.app_ref}"
            ),
        ),
    )
    db.commit()


# --- Margin calculator (what-if) ------------------------------------------------------------------------


@dataclass
class WhatIf:
    channel: str
    label: str
    cost: Decimal | None  # None: no rate card entry for this combination today
    margin_pct: Decimal | None
    decides: str | None  # who would approve an offer at this margin
    min_bill: Decimal | None  # lowest bill rate that still meets the account's margin cut-off


def what_if(
    db: Session,
    account: Account,
    *,
    grade: str,
    practice: str | None,
    region: str,
    bill: Decimal | None,
    on: date,
) -> list[WhatIf]:
    """For each supply channel: today's vendor cost, the margin at this bill rate, who would approve
    an offer at that margin, and the lowest bill rate that reaches the cut-off."""
    threshold = Decimal(account.margin_threshold)
    out = []
    for c in account.settings.supply_channels:
        rate = rate_card_service.lookup(
            db, account.id, grade=grade, practice=practice or None, region=region, channel=c.key, on=on
        )
        if rate is None:
            out.append(WhatIf(c.key, c.label, None, None, None, None))
            continue
        cost = Decimal(rate.cost_rate)
        margin = decides = None
        if bill:
            margin = ((bill - cost) / bill * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            decides = "Demand owner" if margin >= threshold else "Leadership"
        min_bill = None
        if threshold < 100:
            min_bill = (cost / (1 - threshold / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        out.append(WhatIf(c.key, c.label, cost, margin, decides, min_bill))
    return out

"""Leadership and the GTD team admin: account overview. Fill speed, pipeline, and revenue lost
to missed start dates."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import EscalationStatus, MainStage, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, Demand, Escalation, OfferApproval
from app.services import costing_service, loss_service, reconcile_service, workflow_service
from app.services.account_service import get_account
from app.services.demand_service import account_today, period_for
from app.services.escalation_service import current_doj

router = APIRouter(tags=["leadership"])
guard = require_screen("overview")


def _open_escalations(db: Session, account_id: int, bu_ids: frozenset[int] | None = None) -> dict[int, int]:
    stmt = select(Escalation.level, func.count()).where(
        Escalation.account_id == account_id, Escalation.status == EscalationStatus.OPEN.value
    )
    if bu_ids is not None:  # leadership over chosen BUs: their demands' escalations only
        stmt = stmt.join(Demand, Demand.id == Escalation.demand_id).where(Demand.bu_id.in_(bu_ids))
    return {level: n for level, n in db.execute(stmt.group_by(Escalation.level)).all()}


@router.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    start: str = "",
    end: str = "",
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    today = account_today(db, actor.account_id)
    period = period_for(db, actor.account_id, start, end)
    o = loss_service.overview(db, actor.account_id, today, period, actor.bu_limit)
    # Offers this viewer decides: below the cut-off for leadership, at or above for the GTD team admin.
    route = "leadership" if actor.role is Role.LEADERSHIP else "admin"
    waiting_stmt = (
        select(func.count())
        .select_from(OfferApproval)
        .join(Demand)
        .where(
            Demand.account_id == actor.account_id,
            OfferApproval.decision.is_(None),
            OfferApproval.route == route,
        )
    )
    if actor.bu_limit is not None:
        waiting_stmt = waiting_stmt.where(Demand.bu_id.in_(actor.bu_limit))
    waiting = db.scalar(waiting_stmt)
    return render(
        request,
        "leadership_dashboard/index.html",
        actor,
        db,
        o=o,
        latest=reconcile_service.latest_import(db, actor.account_id),
        escalations=_open_escalations(db, actor.account_id, actor.bu_limit),
        offers_waiting=waiting or 0,
        nb=loss_service.non_billable_by_bu(db, actor.account_id),
        ov=_page_data(db, o),
        sets_caps=actor.role is Role.ADMIN,
    )


# Where the overview names a stage differently from the rest of the app.
SHOWN_AS = {MainStage.ALLOC_PENDING: "Client onboarding pending"}


def _shown(stage: MainStage) -> str:
    return SHOWN_AS.get(stage, stage.label)


def _overview_layout() -> dict[str, Any]:
    """The workflow boxes under the overview's stage names, so a click there still matches the ring."""
    rename = {m.label: _shown(m) for m in MainStage}
    lay = workflow_service.layout()
    for st in [*lay["stages"], lay["abandoned"]]:
        st["label"] = rename.get(st["label"], st["label"])
    return lay


# What each main stage means, in plain words, under its name on the overview.
MEANS = {
    MainStage.COVERAGE: "Raised, not yet with a candidate: being approved on GTD, or profiles being sourced",
    MainStage.SELECTION: "Candidates are in panel or client interviews",
    MainStage.ALLOC_PENDING: "A candidate is chosen: offer being approved, or joining awaited",
    MainStage.ALLOC_DONE: "The candidate has joined",
    MainStage.ABANDONED: "Cancelled or closed without a hire",
}


def _page_data(db: Session, o: loss_service.Overview) -> dict[str, Any]:
    """Everything static/overview.js needs: one row per position in view, and the order things show in."""
    account = get_account(db, o.demands[0].account_id) if o.demands else None
    doj = current_doj(db, account.id) if account else {}
    escs: dict[int, list[dict[str, Any]]] = {}
    if o.demands:
        for e in db.scalars(
            select(Escalation)
            .where(
                Escalation.demand_id.in_([d.id for d in o.demands]),
                Escalation.status == EscalationStatus.OPEN.value,
            )
            .order_by(Escalation.level.desc(), Escalation.opened_at)
        ):
            if e.demand_id is not None:
                escs.setdefault(e.demand_id, []).append({"t": e.type_enum.label, "l": e.level})
    costs = costing_service.costs(db, account.id, o.today) if account else {}
    rows = []
    for d in sorted(o.demands, key=lambda d: d.app_ref):
        loss = o.loss_of.get(d.id)
        cost = costs.get(d.id)
        kind = "Replacement" if d.type == "Replacement" else "New"
        rows.append(
            {
                "ref": d.app_ref,
                "req": d.gtd_req_id,
                "name": d.name,
                "stage": _shown(d.status_enum.main),
                "sub": d.status_enum.label,
                "bu": d.business_unit.name,
                "owner": d.owner.name,
                "owner_phone": d.owner.phone,
                "practice": d.practice or "—",
                "type": f"{kind} · {'non-billable' if d.position_type == 'Non-billable' else 'billable'}",
                "start": f"{d.start_date:%d %b %Y}" if d.start_date else None,
                "joining": f"{doj[d.id]:%d %b %Y}" if doj.get(d.id) else None,
                "open": d.status_enum.main.value in loss_service.OPEN_GROUPS,
                "late": loss is not None and not loss.filled,
                "esc": escs.get(d.id, []),
                "costing": "Non-billable cost" if cost is not None else "",
                "cost": float(cost.so_far) if cost is not None and cost.so_far is not None else 0,
                "cost_month": float(cost.monthly) if cost is not None and cost.monthly is not None else 0,
                "cost_active": cost is not None and cost.active,
                "cost_rate": float(cost.rate) if cost is not None and cost.rate is not None else None,
                "cost_source": cost.source if cost is not None else None,
                "cost_days": cost.working_days if cost is not None else 0,
                "cost_until": f"{cost.until:%d %b %Y}" if cost is not None and cost.until else None,
                "resource": cost.resource if cost is not None else None,
                "grade": d.grade or "—",
                "escd": "Escalated" if d.id in escs else "",
                "pstart": "Past start" if loss is not None and not loss.filled else "",
                "days_late": loss.days_late if loss is not None and not loss.filled else 0,
                "lost": float(loss.lost) if loss is not None and loss.lost is not None else 0,
                "norate": loss is not None and loss.lost is None,
            }
        )
    return {
        "rows": rows,
        "order": {
            "stage": [_shown(m) for m in MainStage],
            "bu": sorted({r["bu"] for r in rows}),
            "type": list(loss_service.MIX),
            "practice": list(account.settings.practices) if account else [],
        },
        # The ring shows open positions only: joined and abandoned ones aren't counted in it.
        "ring_stages": [_shown(MainStage(v)) for v in loss_service.OPEN_GROUPS],
        "means": {_shown(m): text for m, text in MEANS.items()},
        "layout": _overview_layout(),
        "hours": f"{o.hours_per_day:g}",
        "month_days": costing_service.MONTH_DAYS,
        "caps": {
            b.name: b.nb_cap
            for b in db.scalars(select(BusinessUnit).where(BusinessUnit.account_id == account.id))
        }
        if account
        else {},
    }


@router.post("/overview/nb-caps")
async def save_nb_caps(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The GTD team admin sets the agreed number of non-billable positions per business unit."""
    if actor.role is not Role.ADMIN:
        return RedirectResponse("/overview?err=Only+the+GTD+team+admin+sets+the+caps#nb", status_code=303)
    form = await request.form()
    for bu in db.scalars(select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id)):
        raw = str(form.get(f"cap_{bu.id}") or "").strip()
        if raw and (not raw.isdigit() or int(raw) > 500):
            return RedirectResponse(f"/overview?err={bu.name}:+enter+a+whole+number#nb", status_code=303)
        bu.nb_cap = int(raw) if raw else None
    db.commit()
    return RedirectResponse("/overview?msg=Non-billable+caps+saved#nb", status_code=303)


@router.get("/api/overview")
def overview_json(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> dict[str, Any]:
    o = loss_service.overview(db, actor.account_id, account_today(db, actor.account_id), None, actor.bu_limit)
    return {
        "as_of": o.today,
        "open": o.open,
        "need_coverage": o.need_coverage,
        "lost_to_date": o.lost_to_date,
        "projected_to_doj": o.projected,
        "missing_bill_rates": o.missing_rates,
        "pipeline": {key: n for key, _, n in o.pipeline},
        "sub_stages": {key: dict(rows) for key, rows in o.subs.items()},
        "at_risk": [
            {
                "app_ref": x.demand.app_ref,
                "gtd_req_id": x.demand.gtd_req_id,
                "start_date": x.demand.start_date,
                "doj": x.doj,
                "days_late": x.days_late,
                "working_days_late": x.working_days_late,
                "lost": x.lost,
            }
            for x in o.at_risk
        ],  # fmt: skip
    }

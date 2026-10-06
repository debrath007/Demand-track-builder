"""Flows 6 and 8: rate card, offer margin approvals, revenue loss, leadership overview.
Phase 5 exit: leadership numbers match a manual calculation from the (sample) BCM sheet."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig
from app.core.security import Actor, actor_from_user
from app.models import Account, Demand, OfferApproval, RateCard, User
from app.services import loss_service, margin_service, rate_card_service
from app.services.margin_service import ApprovalError
from app.services.rate_card_service import RateCardError
from seed import sample_sheet
from seed.data import CONFIG
from tests.conftest import Client, user_id

SHEET_DAY = date(2026, 9, 24)


def actor(db: Session, who: str) -> Actor:
    return actor_from_user(db, db.get_one(User, user_id(who)))


def by_ref(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def import_sample(
    client: Client, day: date = SHEET_DAY, rows: list[sample_sheet.Row] | None = None, title: bool = True
) -> None:
    data = sample_sheet.build(rows or sample_sheet.ROWS, title=title)
    r = client.as_user("farah").post(
        "/imports",
        data={"sheet_date": day.isoformat()},
        files={"file": ("dp.xlsx", data, "application/octet-stream")},
    )
    assert r.status_code == 303, r.text


def offer_for(db: Session, ref: str) -> OfferApproval:
    db.expire_all()
    return db.scalars(select(OfferApproval).where(OfferApproval.demand_id == by_ref(db, ref).id)).one()


# --- Rate card ------------------------------------------------------------------------------------


def add(db: Session, **kw: object) -> RateCard:
    base: dict[str, object] = {"grade": "C1", "practice": "", "region": "US", "channel": "sogeti",
                               "cost_rate": "61", "effective_from": date(2026, 10, 1)}  # fmt: skip
    base.update(kw)
    return rate_card_service.add_rate(db, 1, user_id("kavya"), **base)  # type: ignore[arg-type]


def test_new_rate_takes_over_and_closes_the_old_one(db: Session) -> None:
    add(db)
    old = db.scalars(
        select(RateCard).where(
            RateCard.grade == "C1",
            RateCard.channel == "sogeti",
            RateCard.region == "US",
            RateCard.effective_from == date(2026, 1, 1),
        )  # fmt: skip
    ).one()
    assert old.effective_to == date(2026, 9, 30)

    def cost_on(day: date) -> Decimal | None:
        r = rate_card_service.lookup(db, 1, grade="C1", practice=None, region="US", channel="sogeti", on=day)
        return r.cost_rate if r else None

    assert cost_on(date(2026, 9, 30)) == Decimal("60.00")
    assert cost_on(date(2026, 10, 1)) == Decimal("61.00")


def test_overlapping_rates_are_refused(db: Session) -> None:
    # A practice-specific key has no seeded rate, so only these two rows exist for it.
    add(db, effective_from=date(2026, 10, 1), effective_to=date(2026, 10, 31), practice="DMN-FS")
    with pytest.raises(RateCardError, match="Overlaps"):
        add(db, effective_from=date(2026, 10, 15), effective_to=date(2026, 11, 15), practice="DMN-FS")
    # A bounded rate can't be dropped into the middle of an open-ended one either.
    with pytest.raises(RateCardError, match="Overlaps"):
        add(db, effective_from=date(2026, 10, 1), effective_to=date(2026, 10, 31), grade="D2")


def test_practice_specific_rate_wins(db: Session) -> None:
    add(db, practice="DMN-FS", cost_rate="50", effective_from=date(2026, 1, 1))
    kw = {"grade": "C1", "region": "US", "channel": "sogeti", "on": date(2026, 9, 1)}
    assert rate_card_service.lookup(db, 1, practice="DMN-FS", **kw).cost_rate == Decimal("50.00")  # type: ignore[union-attr, arg-type]
    assert rate_card_service.lookup(db, 1, practice="CCA-FS", **kw).cost_rate == Decimal("60.00")  # type: ignore[union-attr, arg-type]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"grade": "Z9"}, "Grade"),
        ({"channel": "carrier pigeon"}, "supply channel"),
        ({"cost_rate": "abc"}, "number"),
        ({"cost_rate": "-5"}, "between"),
        ({"effective_to": date(2026, 9, 1)}, "before the start"),
    ],
)
def test_rate_validation(db: Session, change: dict[str, object], message: str) -> None:
    with pytest.raises(RateCardError, match=message):
        add(db, **change)


def test_end_a_rate(db: Session) -> None:
    row = add(db, grade="E1", channel="fte", cost_rate="99", effective_from=date(2027, 1, 1))
    rate_card_service.end_rate(db, 1, row.id, date(2027, 6, 30))
    assert db.get_one(RateCard, row.id).effective_to == date(2027, 6, 30)
    with pytest.raises(RateCardError, match="before it starts"):
        rate_card_service.end_rate(db, 1, row.id, date(2026, 12, 1))


def test_rate_card_screen(client: Client) -> None:
    client.as_user("anil")
    assert "Sogeti" in client.get("/rate-card").text
    r = client.post("/rate-card", data={"channel": "fte", "region": "CA", "grade": "E1", "practice": "",
                                        "cost_rate": "120", "effective_from": "2027-01-01"})  # fmt: skip
    assert "msg=Rate+added" in r.headers["location"] or "msg=Rate%20added" in r.headers["location"]
    assert client.as_user("farah").get("/rate-card").status_code == 403  # GTD team admin only


# --- Offer approvals ------------------------------------------------------------------------------


def test_channel_from_sheet_source() -> None:
    cfg = AccountConfig.model_validate(CONFIG)
    assert margin_service.channel_for_source(cfg, "VMS") == "subcon_vms"
    assert margin_service.channel_for_source(cfg, "Sogeti") == "sogeti"
    assert margin_service.channel_for_source(cfg, "FTE") == "fte"
    assert margin_service.channel_for_source(cfg, None) is None


def test_offers_from_the_sheet_are_priced_and_routed(client: Client, db: Session) -> None:
    import_sample(client)
    above = offer_for(db, "DM-000131")  # B1 Sogeti: cost 45.00, bill 80 → 43.75%
    assert (above.channel, above.cost_rate, above.bill_rate) == ("sogeti", Decimal("45.00"), Decimal("80.00"))
    assert above.margin_pct == Decimal("43.75") and above.route == "admin"
    below = offer_for(db, "DM-000121")  # C2 subcon: cost 71.40, bill 95 → 24.84%
    assert below.margin_pct == Decimal("24.84") and below.route == "leadership"
    import_sample(client, SHEET_DAY + timedelta(days=1), title=False)  # re-import: no second approval
    assert (
        db.scalar(
            select(OfferApproval.id).where(OfferApproval.demand_id == by_ref(db, "DM-000131").id).offset(1)
        )
        is None
    )


def test_demand_owner_decides_at_or_above_cutoff_only(client: Client, db: Session) -> None:
    import_sample(client)
    rahul = actor(db, "rahul")  # owner of DM-000131 (43.75%)
    with pytest.raises(ApprovalError, match="decided by the demand owner"):
        margin_service.decide(db, actor(db, "kavya"), offer_for(db, "DM-000131").id, "approved", None)
    with pytest.raises(ApprovalError, match="decided by the demand owner"):  # another owner can't either
        margin_service.decide(db, actor(db, "neha"), offer_for(db, "DM-000131").id, "approved", None)
    a = margin_service.decide(db, rahul, offer_for(db, "DM-000131").id, "approved", None)
    assert (a.decision, a.approver_id) == ("approved", user_id("rahul")) and a.decided_at is not None
    with pytest.raises(ApprovalError, match="already been decided"):
        margin_service.decide(db, rahul, a.id, "declined", "changed my mind")
    # below the cut-off even the demand's own owner can't: leadership decides
    with pytest.raises(ApprovalError, match="decided by leadership"):
        margin_service.decide(db, actor(db, "priya"), offer_for(db, "DM-000121").id, "approved", "ok")


def test_leadership_exception_needs_a_comment(client: Client, db: Session) -> None:
    import_sample(client)
    lead = actor(db, "sanjay")
    below = offer_for(db, "DM-000121")
    with pytest.raises(ApprovalError, match="why the exception"):
        margin_service.decide(db, lead, below.id, "approved", "")
    with pytest.raises(ApprovalError, match="decided by the demand owner"):
        margin_service.decide(db, lead, offer_for(db, "DM-000131").id, "approved", "fine")
    a = margin_service.decide(db, lead, below.id, "approved", "Strategic account, client pays in Q4")
    assert a.decision == "approved" and a.margin_pct == Decimal("24.84")


def test_decline_needs_a_comment(client: Client, db: Session) -> None:
    import_sample(client)
    with pytest.raises(ApprovalError, match="why it's declined"):
        margin_service.decide(db, actor(db, "rahul"), offer_for(db, "DM-000131").id, "declined", None)


def test_unpriced_offer_waits_then_reprices(client: Client, db: Session) -> None:
    d = by_ref(db, "DM-000131")
    d.client_rate = None
    db.commit()
    import_sample(client)
    a = offer_for(db, "DM-000131")
    assert a.route is None and "no client bill rate" in (a.blocked_reason or "")
    with pytest.raises(ApprovalError, match="Can't decide yet"):
        margin_service.decide(db, actor(db, "kavya"), a.id, "approved", None)
    d = by_ref(db, "DM-000131")
    d.client_rate = Decimal("80")
    db.commit()
    assert margin_service.reprice_pending(db, 1) == 1
    assert offer_for(db, "DM-000131").route == "admin"


def test_missing_rate_card_entry_blocks_until_added(client: Client, db: Session) -> None:
    for r in db.scalars(select(RateCard).where(RateCard.grade == "B1", RateCard.channel == "sogeti")):
        db.delete(r)
    db.commit()
    import_sample(client)
    a = offer_for(db, "DM-000131")
    assert "No rate card entry for B1" in (a.blocked_reason or "")
    client.as_user("anil").post(
        "/rate-card",
        data={
            "channel": "sogeti",
            "region": "US",
            "grade": "B1",
            "cost_rate": "44",
            "effective_from": "2026-01-01",
        },
    )
    a = offer_for(db, "DM-000131")
    assert a.route == "admin" and a.cost_rate == Decimal("44.00")


def test_margin_uses_the_rate_on_the_offer_date(client: Client, db: Session) -> None:
    import_sample(client)
    offered_on = offer_for(db, "DM-000131").priced_on
    assert offered_on is not None
    add(db, grade="B1", channel="sogeti", cost_rate="70", effective_from=offered_on + timedelta(days=1))
    margin_service.reprice_pending(db, 1)
    assert offer_for(db, "DM-000131").cost_rate == Decimal("45.00")  # later rate doesn't touch this offer


def test_admin_raises_approval_by_hand(client: Client, db: Session) -> None:
    rows = [r if r[1] != "BPTONE" else r for r in sample_sheet.ROWS]
    names = sample_sheet.CANDIDATES.pop("BPTONE")
    try:
        import_sample(client, rows=rows)
    finally:
        sample_sheet.CANDIDATES["BPTONE"] = names
    d = by_ref(db, "DM-000131")
    assert db.scalar(select(OfferApproval.id).where(OfferApproval.demand_id == d.id)) is None
    client.as_user("kavya")
    assert "At offer stage, no approval yet" in client.get("/approvals").text
    client.post(
        "/approvals/request", data={"demand_id": str(d.id), "candidate": "Candidate Z", "channel": "sogeti"}
    )
    assert offer_for(db, "DM-000131").margin_pct == Decimal("43.75")


def test_approvals_screen_by_role(client: Client, db: Session) -> None:
    import_sample(client)
    admin_page = client.as_user("kavya").get("/approvals").text  # notified of all; decides none
    assert "Candidate B" in admin_page and "43.8%" in admin_page and "with leadership" in admin_page
    assert "With the demand owner (Rahul K.)" in admin_page and 'value="approved"' not in admin_page
    owner_page = client.as_user("rahul").get("/approvals").text  # his own demand's offer, his to decide
    assert "Candidate B" in owner_page and 'value="approved"' in owner_page and "$45.00" in owner_page
    assert "Candidate A" not in owner_page  # Priya's demand
    priya_page = client.as_user("priya").get("/approvals").text  # hers is below the cut-off
    assert "Candidate A" in priya_page and "with leadership" in priya_page
    assert 'value="approved"' not in priya_page
    lead_page = client.as_user("sanjay").get("/approvals").text
    assert "Candidate A" in lead_page and "Why the exception" in lead_page
    a = offer_for(db, "DM-000121")
    r = client.post(f"/approvals/{a.id}/decide", data={"decision": "approved", "comment": "Strategic"})
    assert "msg=Offer" in r.headers["location"]
    for who in ("farah", "vikram"):
        assert client.as_user(who).get("/approvals").status_code == 403


# --- Revenue loss and the leadership overview (Phase 5 exit) ----------------------------------------


def test_working_days() -> None:
    assert loss_service.working_days(date(2026, 9, 21), date(2026, 9, 28)) == 5  # Mon → next Mon
    assert loss_service.working_days(date(2026, 9, 26), date(2026, 9, 28)) == 0  # weekend only


def manual_working_days(start: date, end: date) -> int:
    return sum((start + timedelta(days=i)).weekday() < 5 for i in range((end - start).days))


def test_phase5_exit_leadership_numbers_match_manual_calculation(client: Client, db: Session) -> None:
    import_sample(client)
    o = loss_service.overview(db, 1, SHEET_DAY)
    hours = Decimal(8)

    # By hand, from the sample sheet and the seeded bill rates:
    #   DIT7AF DM-000121  start 03 Aug, no DOJ           $95/h
    #   IXT3SF DM-000117  start 01 Sep, DOJ 15 Oct       $88/h  (loss to date counts to 24 Sep)
    #   0ZTQQE DM-000118  start 01 Sep, DOJ 15 Oct       $88/h
    #   Y7TR36 DM-000126  start 04 Sep, no DOJ           $90/h
    #   43TUIX DM-000116  start 01 Sep, joined 08 Sep    $88/h  (staffed: loss realised, not at risk)
    expected = {
        "DM-000121": (date(2026, 8, 3), Decimal(95)),
        "DM-000117": (date(2026, 9, 1), Decimal(88)),
        "DM-000118": (date(2026, 9, 1), Decimal(88)),
        "DM-000126": (date(2026, 9, 4), Decimal(90)),
    }
    at_risk = {x.demand.app_ref: x for x in o.at_risk}
    assert set(at_risk) == set(expected)
    total = Decimal(0)
    for ref, (start, rate) in expected.items():
        wd = manual_working_days(start, SHEET_DAY)
        assert at_risk[ref].days_late == (SHEET_DAY - start).days
        assert at_risk[ref].lost == rate * hours * wd, ref
        total += rate * hours * wd
    staffed_late = Decimal(88) * hours * manual_working_days(date(2026, 9, 1), date(2026, 9, 8))
    assert o.lost_to_date == total + staffed_late
    assert at_risk["DM-000121"].days_late == 52 and at_risk["DM-000121"].lost == Decimal("28880")

    # Projected: the two offers in market keep losing until their 15 Oct DOJ.
    to_doj = manual_working_days(SHEET_DAY, date(2026, 10, 15))
    assert o.projected == 2 * Decimal(88) * hours * to_doj

    # Pipeline and headline counts, by hand from the seed + sample sheet.
    pipeline = {k: n for k, _, n in o.pipeline}
    # DM-000142 has a scheduled panel interview, so it's Interviewing, not waiting for coverage.
    assert pipeline == {"coverage": 8, "selection": 3, "alloc_pending": 4, "alloc_done": 1, "abandoned": 1}
    # ... and the sub-stages inside each main stage
    assert dict(o.subs["selection"]) == {"Panel interview": 1, "Client interview in progress": 2}
    # DM-000151 was waiting for its GTD ID; the sheet's W3NX5A row is clearly it, so it links itself.
    assert dict(o.subs["coverage"])["Sourcing profiles"] == 4 and sum(dict(o.subs["coverage"]).values()) == 8
    assert (o.open, o.live, o.need_coverage, o.missing_rates) == (15, 17, 4, 0)

    cca = next(s for s in o.by_practice if s.name == "CCA-FS")
    assert cca.total == 9
    payments = next(s for s in o.by_bu if s.name == "PAYMENTS")
    payments_lost = [at_risk[r].lost for r in ("DM-000121", "DM-000117", "DM-000118")]
    assert payments.lost == sum(x for x in payments_lost if x is not None) + staffed_late


def test_missing_bill_rate_is_reported_not_guessed(client: Client, db: Session) -> None:
    d = by_ref(db, "DM-000126")
    d.client_rate = None
    db.commit()
    import_sample(client)
    o = loss_service.overview(db, 1, SHEET_DAY)
    assert o.missing_rates == 1
    assert next(x for x in o.at_risk if x.demand.app_ref == "DM-000126").lost is None


def test_overview_screen_for_leadership_and_admin_owner(client: Client) -> None:
    import_sample(client)
    page = client.as_user("sanjay").get("/overview")
    assert page.status_code == 200
    assert "Revenue at risk" not in page.text and "Open positions" in page.text and "DIT7AF" in page.text
    assert client.as_user("sanjay").get("/").headers["location"] == "/overview"
    admin = client.as_user("kavya")
    assert admin.get("/").headers["location"] == "/demands"  # admin still lands on the demands
    assert "Open positions" in admin.get("/overview").text
    for who in ("farah", "priya"):
        assert client.as_user(who).get("/overview").status_code == 403


def test_billable_hours_is_an_account_setting(client: Client, db: Session) -> None:
    import_sample(client)
    acc = db.get_one(Account, 1)
    cfg = acc.settings
    cfg.billable_hours_per_day = 7.5
    acc.settings = cfg
    db.commit()
    o = loss_service.overview(db, 1, SHEET_DAY)
    x = next(x for x in o.at_risk if x.demand.app_ref == "DM-000121")
    assert x.lost == Decimal(95) * Decimal("7.5") * manual_working_days(date(2026, 8, 3), SHEET_DAY)


# --- Rate card bulk upload --------------------------------------------------------------------------

UPLOAD = (
    "Channel,Region,Grade,Practice,Cost per hour,From,Until\n"
    "Sogeti,US,C1,Any,$63.50,2027-01-01,\n"
    "subcon_vms,US,C2,DMN-FS,70,2027-01-01,2027-12-31\n"
)


def test_upload_rates_csv(client: Client, db: Session) -> None:
    client.as_user("anil")
    r = client.post("/rate-card/upload", files={"file": ("rates.csv", UPLOAD.encode(), "text/csv")})
    assert "2+rates+imported" in r.headers["location"] or "2%20rates%20imported" in r.headers["location"]
    kw = {"region": "US", "on": date(2027, 2, 1)}
    c1 = rate_card_service.lookup(db, 1, grade="C1", practice=None, channel="sogeti", **kw)  # type: ignore[arg-type]
    assert c1 is not None and c1.cost_rate == Decimal("63.50")
    old = rate_card_service.lookup(
        db, 1, grade="C1", practice=None, channel="sogeti", region="US", on=date(2026, 12, 31)
    )
    assert old is not None and old.effective_to == date(2026, 12, 31)  # superseded, not edited
    dmn = rate_card_service.lookup(db, 1, grade="C2", practice="DMN-FS", channel="subcon_vms", **kw)  # type: ignore[arg-type]
    assert dmn is not None and dmn.effective_to == date(2027, 12, 31)


def test_upload_rates_xlsx(client: Client, db: Session) -> None:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(rate_card_service.UPLOAD_COLUMNS)
    ws.append(["FTE external hire", "CA", "E1", "Any", 101, date(2027, 3, 1), None])
    buf = io.BytesIO()
    wb.save(buf)
    client.as_user("anil").post(
        "/rate-card/upload", files={"file": ("rates.xlsx", buf.getvalue(), "application/octet-stream")}
    )
    r = rate_card_service.lookup(
        db, 1, grade="E1", practice=None, channel="fte", region="CA", on=date(2027, 3, 1)
    )
    assert r is not None and r.cost_rate == Decimal("101.00")


def test_upload_is_all_or_nothing(client: Client, db: Session) -> None:
    bad = UPLOAD + "Carrier pigeon,US,C1,Any,10,2027-01-01,\nSogeti,US,Z9,Any,10,2027-01-01,\n"
    before = db.scalar(select(func.count()).select_from(RateCard))
    r = client.as_user("anil").post(
        "/rate-card/upload", files={"file": ("rates.csv", bad.encode(), "text/csv")}
    )
    location = r.headers["location"]
    assert "Nothing+imported" in location or "Nothing%20imported" in location
    assert "Row+4" in location or "Row%204" in location
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(RateCard)) == before


def test_upload_needs_the_columns(client: Client) -> None:
    r = client.as_user("anil").post(
        "/rate-card/upload", files={"file": ("r.csv", b"Grade,Cost\nC1,5\n", "text/csv")}
    )
    assert "Missing+columns" in r.headers["location"] or "Missing%20columns" in r.headers["location"]


def test_export_is_the_upload_template(client: Client) -> None:
    r = client.as_user("anil").get("/rate-card/export.csv")
    lines = r.text.splitlines()
    assert lines[0] == ",".join(rate_card_service.UPLOAD_COLUMNS)
    assert "Sogeti,US,C1,Any,60.00,2026-01-01," in lines

"""Phase 1 exit: each persona sees only the data its access allows."""

from sqlalchemy.orm import Session

from app.core.security import actor_from_user
from app.models import User
from app.services.demand_service import visible_demands
from tests.conftest import Client, bu_id, user_id

PAYMENTS_REFS = {f"DM-000{n}" for n in (151, 150, 142, 139, 121, 118, 117, 116)}


def refs(client: Client) -> dict[str, dict]:
    r = client.get("/api/demands")
    assert r.status_code == 200, r.text
    return {d["app_ref"]: d for d in r.json()}


def test_demand_owner_sees_only_own_demands(client: Client) -> None:
    seen = refs(client.as_user("priya"))
    assert set(seen) == PAYMENTS_REFS
    assert all(d["owner"] == "Priya N." for d in seen.values())


def test_demand_owner_cannot_see_another_bu(client: Client) -> None:
    seen = refs(client.as_user("priya"))
    assert not any(d["business_unit"] != "PAYMENTS" for d in seen.values())
    assert "DM-000149" not in seen  # CARDS


def test_own_scope_hides_same_bu_colleagues(client: Client) -> None:
    seen = refs(client.as_user("neha"))
    assert set(seen) == {"DM-000144", "DM-000146"}


def test_bu_read_only_sees_colleagues_but_cannot_edit(client: Client) -> None:
    seen = refs(client.as_user("rahul"))
    assert set(seen) == {"DM-000149", "DM-000135", "DM-000131", "DM-000144", "DM-000146"}
    assert seen["DM-000144"]["read_only"] is True
    assert seen["DM-000149"]["read_only"] is False


def test_full_account_roles_see_everything(client: Client) -> None:
    for who in ("kavya", "farah"):
        seen = refs(client.as_user(who))
        assert len(seen) == 18, who
        assert {d["business_unit"] for d in seen.values()} == {"CARDS", "BANKING", "PAYMENTS", "DATA"}


def test_leadership_sees_only_their_business_units(client: Client) -> None:
    sanjay = refs(client.as_user("sanjay"))
    assert {d["business_unit"] for d in sanjay.values()} == {"PAYMENTS", "DATA"}
    rakesh = refs(client.as_user("rakesh"))
    assert {d["business_unit"] for d in rakesh.values()} == {"CARDS", "BANKING"}
    assert len(sanjay) + len(rakesh) == 18


def test_owners_see_the_bill_rate_on_their_own_demands_only(client: Client) -> None:
    assert all("client_rate" in d for d in refs(client.as_user("priya")).values())  # all her own
    rahul = refs(client.as_user("rahul"))  # his own, plus the rest of CARDS read-only
    assert {("client_rate" in d) for d in rahul.values() if d["read_only"]} == {False}
    assert all("client_rate" in d for d in rahul.values() if not d["read_only"])
    assert all("client_rate" in d for d in refs(client.as_user("kavya")).values())
    assert all("client_rate" not in d for d in refs(client.as_user("farah")).values())  # GTD admin team


def test_leadership_sees_but_cannot_edit(client: Client) -> None:
    assert all(d["read_only"] for d in refs(client.as_user("sanjay")).values())
    assert not any(d["read_only"] for d in refs(client.as_user("kavya")).values())


def test_interviewer_sees_only_demands_with_their_interviews(db: Session) -> None:
    vikram = actor_from_user(db, db.get_one(User, user_id("vikram")))
    anita = actor_from_user(db, db.get_one(User, user_id("anita")))
    assert {d.app_ref for d in db.scalars(visible_demands(vikram))} == {"DM-000142", "DM-000144"}
    assert list(db.scalars(visible_demands(anita))) == []


def test_widening_scope_takes_effect_immediately(client: Client) -> None:
    neha = user_id("neha")
    client.as_user("anil")
    form = {
        "name": "Neha T.",
        "email": "neha.t@example.com",
        "role": "demand_owner",
        "level": "C2",
        "scope": "own_bu_read",
        "bu_ids": [str(bu_id("CARDS"))],
    }
    assert client.post(f"/users/{neha}", data=form).status_code == 303
    seen = refs(client.as_user("neha"))
    assert "DM-000149" in seen and seen["DM-000149"]["read_only"]


def test_demands_page_shows_only_own_rows(client: Client) -> None:
    html = client.as_user("priya").get("/demands").text
    assert "DM-000151" in html and "DM-000149" not in html
    assert "My demands" in html

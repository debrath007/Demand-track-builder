"""Account overview: one ring that narrows on every click, and the workflow diagram."""

import json
import re
from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import DemandStatus, MainStage
from app.services import loss_service, workflow_service
from tests.conftest import Client


def page_data(html: str) -> dict:  # type: ignore[type-arg]
    m = re.search(r"window\.OV = (\{.*?\});</script>", html, re.S)
    assert m, "the overview page carries its data"
    return json.loads(m.group(1))  # type: ignore[no-any-return]


def test_every_status_is_a_box_in_the_workflow() -> None:
    lay = workflow_service.layout()
    boxes = {b for s in lay["stages"] for b in s["subs"]} | {
        p[0] for s in lay["stages"] for p in s["problems"]
    }
    boxes |= set(lay["abandoned"]["subs"])
    assert boxes == {s.label for s in DemandStatus}
    assert [s["label"] for s in lay["stages"]][0] == "Resourcing In Progress"
    assert set(workflow_service.NEXT) == set(DemandStatus)
    for stage, path in workflow_service.PATH.items():
        assert all(s.main is stage for s in path)


def test_overview_page_carries_every_position(client: Client, db: Session) -> None:
    html = client.as_user("kavya").get("/overview").text
    assert "Show workflow" in html and "/static/overview.js" in html and "/static/workflow.js" in html
    assert "Coverage Required" not in html
    data = page_data(html)
    o = loss_service.overview(db, 1, date.today())
    assert all(r["owner_phone"].startswith("+1") for r in data["rows"])  # every owner can be called
    assert len(data["rows"]) == o.live and sum(r["open"] for r in data["rows"]) == o.open
    assert sum(r["late"] for r in data["rows"]) == len(o.at_risk)
    assert sum(r["pstart"] == "Past start" for r in data["rows"]) == len(o.at_risk)
    assert all(r["days_late"] > 0 for r in data["rows"] if r["late"])
    by_ref = {r["ref"]: r for r in data["rows"]}
    assert {"t": "Past start date", "l": 2} in by_ref["DM-000121"]["esc"]  # seeded, overdue
    assert by_ref["DM-000121"]["escd"] == "Escalated"
    assert all((r["escd"] == "Escalated") == bool(r["esc"]) for r in data["rows"])
    assert round(sum(r["lost"] for r in data["rows"]), 2) == float(o.lost_to_date)
    shown = {"Allocation Pending": "Client onboarding pending"}  # the overview's own name for it
    assert data["order"]["stage"] == [shown.get(m.label, m.label) for m in MainStage]
    # the ring counts open positions only
    assert data["ring_stages"] == ["Resourcing In Progress", "Selection In Progress", "Client onboarding pending"]
    assert {r["stage"] for r in data["rows"] if r["open"]} <= set(data["ring_stages"])
    # the workflow uses the same names, so a click on it still matches the ring
    assert [s["label"] for s in data["layout"]["stages"]] == data["order"]["stage"][:4]
    assert {r["type"] for r in data["rows"]} <= set(data["order"]["type"])
    row = data["rows"][0]
    assert {"ref", "name", "stage", "sub", "bu", "owner", "practice", "type", "start", "lost"} <= set(row)


def test_overview_is_not_for_demand_owners(client: Client) -> None:
    assert client.as_user("priya").get("/overview").status_code == 403


def test_demand_page_has_its_workflow(client: Client) -> None:
    html = client.as_user("priya").get("/demands/DM-000121").text
    assert "Show workflow" in html and "wfDiagram(" in html
    assert '"sub": "Offer approval pending"' in html and '"stage": "Allocation Pending"' in html
    assert "<strong>Next:</strong> The offer is approved" in html
    assert '"t": "Past start date"' in html  # its open escalation rides on the diagram

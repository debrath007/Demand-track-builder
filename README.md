# Demand Tracker

Tracks client positions (demands) from the moment a demand owner raises them until the candidate is
onboarded and billing starts: GTD submission, BCM sheet reconciliation, escalations, offer approvals and
interviews. Built for Discover NA first; every client-specific rule is account configuration.

Specs: [`docs/spec/flow-artifact.md`](docs/spec/flow-artifact.md) (flows),
[`docs/spec/techstack.md`](docs/spec/techstack.md) (stack, data model),
[`docs/spec/plan.md`](docs/spec/plan.md) (phases). Answers to the open questions and design decisions:
[`docs/decisions.md`](docs/decisions.md).

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Decisions and setup | Repo, CI; open questions answered 24 Sep (`docs/decisions.md`) |
| 1 | Foundation | **Done**: all tables + first migration, View-as switcher, User access, Account settings, demands list with scope filtering, seed |
| 2 | Intake and GTD submission | **Done**: Raise demand (draft/submit, N positions, JD upload), demand page with history, GTD queue (link IDs, CSV for GTD entry), daily admin mail + scheduler |
| 3 | BCM sheet import and reconciliation | **Done**: upload with column mapping in settings, snapshot rows, exact / name-prefix / fuzzy matching, missing and dropped detection with escalations, match or create from row |
| 4 | Escalation engine | **Done**: not submitted / aging / past start triggers, two-hourly sweep, L1 → L2 promotion, mails to the BU delivery head and leadership, Escalations screen with resolve (resubmit, extend, close, no further action) and audit trail |
| 5 | Rate card, approvals, leadership view | **Done**: dated rate card, offers priced from the BCM sheet and routed by the margin cut-off, revenue loss, leadership overview |
| 6 | Interviews | **Done**: candidates mapped to requisitions (from the BCM sheet, panelists, Karat stub), panelist recommendations, L2 requests approved by the demand owner, scheduling placeholders, invite + no-sign-in feedback link, CVs, interviewer alerts, rejection-limit and panel-SLA escalations |
| 7 | Production readiness | Deferred to last (SSO, AWS, backups, UAT) |
| 8 | Second account | **Done**: role per account membership, account switcher, platform Accounts screen (create from blank or a copy of another account's settings, deactivate), Acme Insurance seeded with its own BCM sheet format; exit test onboards a third client through the screens only |

## Run locally (this machine)

```bat
scripts\db-start.cmd
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m seed
.venv\Scripts\python -m uvicorn app.main:app --port 8010 --reload
```

Open http://localhost:8010 and use **View as** in the sidebar to switch persona.

Or just run `scripts
un.cmd`: it starts the database if needed and serves the app in that window.

`.env` holds `DATABASE_URL` and `TEST_DATABASE_URL` (see `.env.example`). With Docker instead:
`docker compose up`, then `docker compose exec app python -m seed`.

## Checks

```bat
.venv\Scripts\ruff check . && .venv\Scripts\ruff format --check .
.venv\Scripts\mypy app seed tests
.venv\Scripts\python -m pytest
```

Tests drop and rebuild `TEST_DATABASE_URL` with Alembic, then reload the seed before every test.

## Layout

Every screen is a router file, a template folder and a service. Routers stay thin; logic lives in
services so scheduled jobs can reuse it.

```
app/core/        config, db, enums (the vocabulary), account_config (per-client rules),
                 security (Actor, current_user, role guards), nav (menu per role)
app/models/      SQLAlchemy models, one class per table
app/services/    demand_service (scope filter, intake), gtd_service, notify_service (admin mail),
                 excel_parser, import_service, reconcile_service, escalation_service,
                 user_service, account_service
app/jobs/        scheduler (daily admin mail)
app/routers/     view_switcher, my_demands, raise_demand, gtd_queue, excel_import, reconciliation,
                 user_access, account_settings
app/templates/   base.html + one folder per screen
migrations/      Alembic
seed/            Discover NA + Acme Insurance dummy data (python -m seed); sample BCM sheets
                 (python -m seed.sample_sheet, python -m seed.sample_sheet_acme)
tests/
```

## Seeded personas

| Persona | User | Sees |
|---|---|---|
| Demand owner | Priya N. (PAYMENTS) | Own demands |
| Demand owner | Rahul K. (CARDS) | Own + CARDS read-only |
| Demand owner | Neha T., Meera S., Arjun D. | Own demands |
| GTD team admin | Kavya R. | Full account; asks the Administrator for control changes |
| GTD admin team | Farah Q., Deepak L. | Full account; GTD queue, BCM sheet import, reconciliation, escalations |
| Leadership | Sanjay M. | PAYMENTS and DATA only, read-only (all of Acme) |
| Leadership | Rakesh S. | CARDS and BANKING only, read-only |
| Interviewer | Vikram P., Anita G. | Demands with interviews assigned to them |
| Administrator | Anil V. | App controls only: User access, Account settings, Rate card, Accounts. No demands |

**Acme Insurance** (second account): Grace H. (admin demand owner), Tomas R. (admin team), Lena W. (CLAIMS) and Marco B. (POLICY, own + BU read). Nadia K. is Acme's interviewer. Sanjay M. (leadership) works in both accounts and switches between them in the sidebar; an interviewer belongs to one account only. Rosa D. is Acme's Administrator. Administrators add client accounts.

All names are placeholders. Never commit a real BCM sheet: it carries candidate personal data.

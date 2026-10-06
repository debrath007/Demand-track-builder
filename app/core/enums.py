"""The app's fixed vocabulary.

These are the concepts the code reasons about. Anything that differs by client (BU names, practices,
how a BCM sheet status maps to a stage) is account configuration instead, see `Account.config`.
"""

from enum import StrEnum


class Role(StrEnum):
    DEMAND_OWNER = "demand_owner"
    ADMIN = "admin"  # "GTD team admin" in the UI: heads the GTD admin team
    ADMIN_TEAM = "admin_team"  # "GTD admin team": the manual GTD and BCM sheet work
    LEADERSHIP = "leadership"
    INTERVIEWER = "interviewer"
    # Runs the app's controls (settings, access, rate card) on request. Sees no demands.
    ADMINISTRATOR = "administrator"

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]


ROLE_LABELS = {
    Role.DEMAND_OWNER: "Demand owner",
    Role.ADMIN: "GTD team admin",
    Role.ADMIN_TEAM: "GTD admin team",
    Role.LEADERSHIP: "Leadership",
    Role.INTERVIEWER: "Interviewer",
    Role.ADMINISTRATOR: "Administrator",
}


class Scope(StrEnum):
    """What slice of the account a user sees. Set on the User access page, never by login."""

    OWN = "own"
    OWN_BU_READ = "own_bu_read"
    FULL = "full"
    BU_READ = "bu_read"  # leadership over some business units only
    ASSIGNED_INTERVIEWS = "assigned_interviews"
    APP_CONTROLS = "app_controls"

    @property
    def label(self) -> str:
        return SCOPE_LABELS[self][0]

    @property
    def description(self) -> str:
        return SCOPE_LABELS[self][1]


SCOPE_LABELS = {
    Scope.OWN: ("Own demands", "Only demands they raised"),
    Scope.OWN_BU_READ: ("Own + BU read-only", "Can view other demands in their BU(s)"),
    Scope.FULL: ("Full account", "All BUs, all demands"),
    Scope.BU_READ: ("Chosen BUs", "Only the demands, escalations and offers of their business units"),
    Scope.ASSIGNED_INTERVIEWS: ("Assigned interviews", "Plus alerts for new requisitions in their skills"),
    Scope.APP_CONTROLS: ("App controls", "Settings, access and rate card; no demands"),
}

# Which scopes each role may hold. A single allowed scope means the role is locked to it.
# Enforced three times: here (UI options), in user_service (writes) and by a CHECK constraint.
ALLOWED_SCOPES: dict[Role, tuple[Scope, ...]] = {
    Role.DEMAND_OWNER: (Scope.OWN, Scope.OWN_BU_READ),
    Role.ADMIN: (Scope.FULL,),
    Role.ADMIN_TEAM: (Scope.FULL,),
    Role.LEADERSHIP: (Scope.FULL, Scope.BU_READ),
    Role.INTERVIEWER: (Scope.ASSIGNED_INTERVIEWS,),
    Role.ADMINISTRATOR: (Scope.APP_CONTROLS,),
}


class DemandStatus(StrEnum):
    # Before GTD (set by the app)
    DRAFT = "draft"
    SUBMITTED = "submitted"
    NOTIFIED = "notified"
    SENT_TO_GTD = "sent_to_gtd"
    RETURNED = "returned"  # sent back to the demand owner to correct, then resubmit
    # Linking (set by reconciliation)
    LINKED = "linked"
    MISSING = "missing"
    DROPPED = "dropped"
    INCORRECT = "incorrect"
    # Coverage (BCM sheet status, mapped through account config)
    COVERAGE_REQUIRED = "coverage_required"
    # Interview progress, set by the app from panel records (the BCM sheet lags; see pipeline_service)
    INTERVIEWING = "interviewing"
    PANEL_SELECTED = "panel_selected"
    PROFILES_WITH_CLIENT = "profiles_with_client"
    OFFER_IN_PROCESS = "offer_in_process"
    OFFER_IN_MARKET = "offer_in_market"
    STAFFED = "staffed"
    # End
    CANCELLED = "cancelled"
    CLOSED = "closed"

    @property
    def label(self) -> str:
        return STATUS_META[self][0]

    @property
    def chip(self) -> str:
        return STATUS_META[self][1]

    @property
    def phase(self) -> str:
        return STATUS_META[self][2]

    @property
    def main(self) -> "MainStage":
        """The main stage this sub-stage belongs to."""
        return MAIN_OF[self]

    @property
    def full(self) -> str:
        """Main stage and sub-stage together: "Resourcing In Progress · GTD approval pending"."""
        return f"{MAIN_OF[self].label} · {STATUS_META[self][0]}"


# sub-stage name, chip colour, phase
STATUS_META: dict[DemandStatus, tuple[str, str, str]] = {
    DemandStatus.DRAFT: ("Draft", "gray", "before_gtd"),
    DemandStatus.SUBMITTED: ("GTD creation pending", "gray", "before_gtd"),
    DemandStatus.NOTIFIED: ("GTD creation pending", "gray", "before_gtd"),
    DemandStatus.SENT_TO_GTD: ("GTD approval pending", "blue", "before_gtd"),
    DemandStatus.RETURNED: ("Correction required", "risk", "before_gtd"),
    DemandStatus.LINKED: ("GTD approved", "teal", "linking"),
    DemandStatus.MISSING: ("GTD approval overdue", "esc", "linking"),
    DemandStatus.DROPPED: ("Removed from sheet", "esc", "linking"),
    DemandStatus.INCORRECT: ("Marked incorrect", "esc", "linking"),
    DemandStatus.COVERAGE_REQUIRED: ("Sourcing profiles", "teal", "coverage"),
    DemandStatus.INTERVIEWING: ("Panel interview", "teal", "coverage"),
    DemandStatus.PANEL_SELECTED: ("Panel selected", "blue", "coverage"),
    DemandStatus.PROFILES_WITH_CLIENT: ("Client interview in progress", "teal", "coverage"),
    DemandStatus.OFFER_IN_PROCESS: ("Offer approval pending", "blue", "coverage"),
    DemandStatus.OFFER_IN_MARKET: ("Offer made, joining awaited", "blue", "coverage"),
    DemandStatus.STAFFED: ("Joined", "done", "coverage"),
    DemandStatus.CANCELLED: ("Cancelled in sheet", "gray", "end"),
    DemandStatus.CLOSED: ("Closed by owner or GTD team", "gray", "end"),
}


class MainStage(StrEnum):
    """The five stages leadership talks in (the names Acquisition Central uses). Every demand status is
    a sub-stage of exactly one of them."""

    COVERAGE = "coverage"
    SELECTION = "selection"
    ALLOC_PENDING = "alloc_pending"
    ALLOC_DONE = "alloc_done"
    ABANDONED = "abandoned"

    @property
    def label(self) -> str:
        return MAIN_STAGE_META[self][0]

    @property
    def chip(self) -> str:
        return MAIN_STAGE_META[self][1]

    @property
    def subs(self) -> tuple["DemandStatus", ...]:
        return tuple(s for s in DemandStatus if MAIN_OF[s] is self)


MAIN_STAGE_META = {
    MainStage.COVERAGE: ("Resourcing In Progress", "teal"),
    MainStage.SELECTION: ("Selection In Progress", "blue"),
    MainStage.ALLOC_PENDING: ("Allocation Pending", "blue"),
    MainStage.ALLOC_DONE: ("Allocation Completed", "done"),
    MainStage.ABANDONED: ("Abandoned", "gray"),
}

MAIN_OF: dict[DemandStatus, MainStage] = {
    DemandStatus.DRAFT: MainStage.COVERAGE,
    DemandStatus.SUBMITTED: MainStage.COVERAGE,
    DemandStatus.NOTIFIED: MainStage.COVERAGE,
    DemandStatus.SENT_TO_GTD: MainStage.COVERAGE,
    DemandStatus.RETURNED: MainStage.COVERAGE,
    DemandStatus.LINKED: MainStage.COVERAGE,
    DemandStatus.MISSING: MainStage.COVERAGE,
    DemandStatus.DROPPED: MainStage.COVERAGE,
    DemandStatus.INCORRECT: MainStage.COVERAGE,
    DemandStatus.COVERAGE_REQUIRED: MainStage.COVERAGE,
    DemandStatus.INTERVIEWING: MainStage.SELECTION,
    DemandStatus.PANEL_SELECTED: MainStage.SELECTION,
    DemandStatus.PROFILES_WITH_CLIENT: MainStage.SELECTION,
    DemandStatus.OFFER_IN_PROCESS: MainStage.ALLOC_PENDING,
    DemandStatus.OFFER_IN_MARKET: MainStage.ALLOC_PENDING,
    DemandStatus.STAFFED: MainStage.ALLOC_DONE,
    DemandStatus.CANCELLED: MainStage.ABANDONED,
    DemandStatus.CLOSED: MainStage.ABANDONED,
}

BEFORE_GTD = frozenset(s for s in DemandStatus if s.phase == "before_gtd")
LINK_PROBLEMS = frozenset({DemandStatus.MISSING, DemandStatus.DROPPED, DemandStatus.INCORRECT})
# No further work expected: not "open", never "past start".
FINISHED = frozenset({DemandStatus.STAFFED, DemandStatus.CANCELLED, DemandStatus.CLOSED})

# Coverage stages in the order a demand moves through them. The app may move a demand forward on
# interview evidence; the BCM sheet may move it anywhere (pipeline_service decides who wins).
PROGRESS_ORDER: tuple[DemandStatus, ...] = (
    DemandStatus.LINKED,
    DemandStatus.COVERAGE_REQUIRED,
    DemandStatus.INTERVIEWING,
    DemandStatus.PANEL_SELECTED,
    DemandStatus.PROFILES_WITH_CLIENT,
    DemandStatus.OFFER_IN_PROCESS,
    DemandStatus.OFFER_IN_MARKET,
    DemandStatus.STAFFED,
)
APP_PROGRESS = frozenset({DemandStatus.INTERVIEWING, DemandStatus.PANEL_SELECTED})

# Stages a BCM sheet status may map to (account settings → status mapping).
SHEET_STAGES: tuple[DemandStatus, ...] = (
    DemandStatus.COVERAGE_REQUIRED,
    DemandStatus.PROFILES_WITH_CLIENT,
    DemandStatus.OFFER_IN_PROCESS,
    DemandStatus.OFFER_IN_MARKET,
    DemandStatus.STAFFED,
    DemandStatus.CANCELLED,
    DemandStatus.INCORRECT,
)


class EscalationType(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    MISSING = "missing"
    DROPPED = "dropped"
    INCORRECT = "incorrect"
    AGING = "aging"
    REJECTION_LIMIT = "rejection_limit"
    PANEL_SLA = "panel_sla"
    PAST_START = "past_start"
    UNLINKED_ROW = "unlinked_row"  # a BCM sheet row whose requisition no demand in the app is linked to

    @property
    def label(self) -> str:
        return {
            "unlinked_row": "In BCM sheet, not in the app",
            "not_submitted": "Not submitted",
            "missing": "Missing from sheet",
            "dropped": "Dropped from sheet",
            "incorrect": "Incorrect demand",
            "aging": "Aging",
            "rejection_limit": "Rejection limit",
            "panel_sla": "Panel SLA",
            "past_start": "Past start date",
        }[self.value]

    @property
    def short(self) -> str:
        """For status chips: 'Missing · escalated L1'."""
        return {"missing": "Missing", "dropped": "Dropped", "past_start": "Past start"}.get(
            self.value, self.label
        )


class Severity(StrEnum):
    """How urgent an escalation is. Sets how long the responsible person has to respond."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class Responsible(StrEnum):
    """Who has to act on an escalation. Everyone else is only informed."""

    GTD_TEAM = "gtd_team"
    DEMAND_OWNER = "demand_owner"
    INTERVIEWER = "interviewer"

    @property
    def label(self) -> str:
        return RESPONSIBLE_LABELS[self.value]


RESPONSIBLE_LABELS = {
    "gtd_team": "GTD admin team",
    "demand_owner": "Demand owner",
    "interviewer": "Interviewer",
}


class EscalationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class ResolutionAction(StrEnum):
    RESUBMIT = "resubmit"  # back into the next admin mail; the new GTD ID chains to the old one
    EXTEND = "extend"  # stays open at its level with a new due date
    CLOSE = "close"  # the demand is closed
    NO_ACTION = "no_action"  # the condition has already cleared (e.g. the ID was linked late)
    RETURN = "return"  # back to the demand owner to correct; they resubmit
    NEW_START = "new_start"  # past start: the owner gives a revised start date

    @property
    def label(self) -> str:
        return {
            "resubmit": "Resubmit in next admin mail",
            "extend": "Extend due date",
            "close": "Close demand",
            "no_action": "No further action (condition cleared)",
            "return": "Send back to demand owner for correction",
            "new_start": "Revise the start date",
        }[self.value]


class EscalationEventKind(StrEnum):
    OPENED = "opened"
    NOTIFIED = "notified"
    PROMOTED = "promoted"
    EXTENDED = "extended"
    RESOLVED = "resolved"


class StageOrigin(StrEnum):
    APP = "app"
    IMPORT = "import"


class RowOutcome(StrEnum):
    """What reconciliation made of one BCM sheet row."""

    MATCHED = "matched"  # tier 1: its requisition ID is linked to a demand
    PREFIX = "prefix"  # tier 2: [DM-…] in the name; linked automatically
    CONFIRMED = "confirmed"  # tier 3 or manual: a person matched it, or created a demand from it
    SUGGESTED = "suggested"  # unmatched, with fuzzy suggestions waiting for a person
    UNMATCHED = "unmatched"  # unmatched, no suggestion
    CONFLICT = "conflict"  # its [DM-…] demand is already linked to a different requisition ID
    DUPLICATE = "duplicate"  # the same requisition ID appears earlier in the sheet
    INVALID = "invalid"  # no requisition ID
    SUPERSEDED = "superseded"  # an ID the demand has since replaced (resubmitted); ignored

    @property
    def needs_person(self) -> bool:
        return self in (RowOutcome.SUGGESTED, RowOutcome.UNMATCHED, RowOutcome.CONFLICT)


class ApprovalRoute(StrEnum):
    """Who decides an offer. At or above the margin cut-off: the demand's owner (the stored value is
    still "admin" from when the GTD team admin decided these). Below it: leadership."""

    ADMIN = "admin"
    LEADERSHIP = "leadership"

    @property
    def decider(self) -> str:
        return "the demand owner" if self is ApprovalRoute.ADMIN else "leadership"


class Decision(StrEnum):
    APPROVED = "approved"
    DECLINED = "declined"


class InterviewOutcome(StrEnum):
    SELECT = "select"
    REJECT = "reject"
    HOLD = "hold"


class InterviewStatus(StrEnum):
    REQUESTED = "requested"  # L2 asked for by the panelist; waiting for the demand owner
    DECLINED = "declined"  # the demand owner said no to the extra round
    OPEN = "open"  # needed, but staffing hasn't scheduled it or no interviewer is known yet
    SCHEDULED = "scheduled"  # interviewer assigned (time may still be unknown); feedback link issued
    COMPLETED = "completed"  # recommendation recorded

    @property
    def label(self) -> str:
        return {
            "requested": "Waiting for demand owner",
            "declined": "Extra round declined",
            "open": "To be scheduled",
            "scheduled": "Scheduled",
            "completed": "Feedback in",
        }[self.value]


class InterviewSource(StrEnum):
    APP = "app"
    KARAT = "karat"


class CandidateSource(StrEnum):
    SHEET = "sheet"  # the BCM sheet's candidate name on the requisition's row
    MANUAL = "manual"  # added by the GTD admin team or a panelist
    KARAT = "karat"


def check_in(column: str, values: type[StrEnum] | tuple[StrEnum, ...]) -> str:
    """SQL for a CHECK constraint limiting a text column to an enum's values."""
    items = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({items})"

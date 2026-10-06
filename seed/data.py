"""Dummy data: one account (Discover NA), its four BUs, a user for every role, demands in every phase.

Names and candidates are placeholders. Requisition codes and titles mirror the prototype; nothing
here comes from a real BCM sheet (those carry candidate personal data and are never committed).
"""

from datetime import date

ACCOUNT = {
    "name": "Discover NA",
    "grace_days": 3,
    "l1_sla_days": 2,
    "l2_sla_days": 3,
    "panel_timer_hours": 48,
    "aging_days": 14,
    "rejection_limit": 3,
    "margin_threshold": 30,
}

CONFIG = {
    "timezone": "America/Chicago",
    "practices": ["CCA-FS", "DCX-FS", "DMN-FS", "TES-FS", "ADM-FS", "Cloud-Java", "Cloud-MF", "Cloud-APM"],
    "grades": ["A5", "B1", "B2", "C1", "C2", "D1", "D2", "E1"],
    "regions": ["US", "CA"],
    "work_modes": ["Onsite", "Hybrid", "Remote"],
    "categories": ["Open", "Proactive"],
    "supply_channels": [
        {
            "key": "gtd_supply",
            "label": "GTD supply",
            "sheet_marker": "GTD Supply Suggested",
            "needs_sourcing_req": False,
        },
        {"key": "sogeti", "label": "Sogeti", "sheet_marker": "Sogeti Supply", "needs_sourcing_req": False},
        {
            "key": "subcon_vms",
            "label": "Subcon VMS",
            "sheet_marker": "Subcon VMS Shortlists",
            "needs_sourcing_req": True,
        },
        {"key": "fte", "label": "FTE external hire", "sheet_marker": "FTE", "needs_sourcing_req": True},
    ],
    # flow-artifact.md §6.2, from the 09-Sep sheet. "In Correct Demnad" is spelled as the sheet has it.
    "status_mapping": [
        {"status_group": "Work in Progress", "status": "Coverage Required", "stage": "coverage_required"},
        {
            "status_group": "Profiles with Client",
            "status": "CI to be Scheduled",
            "stage": "profiles_with_client",
        },
        {
            "status_group": "Offer in Market/Process",
            "status": "Offer in Process",
            "stage": "offer_in_process",
        },
        {"status_group": "Offer in Market/Process", "status": "Offer in Market", "stage": "offer_in_market"},
        {"status_group": "Staffed", "status": "Allocation Pending", "stage": "staffed"},
        {"status_group": "Cancelled/Abandon", "status": "Demand to be Cancelled", "stage": "cancelled"},
        {"status_group": "", "status": "In Correct Demnad", "stage": "incorrect"},
    ],
    "escalation_owners": {"L1": "LOB delivery head", "L2": "Account leadership"},
}

BUSINESS_UNITS = ["CARDS", "BANKING", "PAYMENTS", "DATA"]

# LOB delivery head per BU: owner of L1 escalations (mailed, no login). Placeholder people.
DELIVERY_HEADS = {
    "CARDS": ("Asha P.", "asha.p@example.com"),
    "BANKING": ("Daniel W.", "daniel.w@example.com"),
    "PAYMENTS": ("Ritu S.", "ritu.s@example.com"),
    "DATA": ("Omar F.", "omar.f@example.com"),
}

# key, name, email, role, level, scope, BUs, practices, interviewer (skills, max grade)
USERS = [
    ("priya", "Priya N.", "priya.n@example.com", "demand_owner", "C2", "own", ["PAYMENTS"], [], None),
    ("rahul", "Rahul K.", "rahul.k@example.com", "demand_owner", "D1", "own_bu_read", ["CARDS"], [], None),
    ("neha", "Neha T.", "neha.t@example.com", "demand_owner", "C2", "own", ["CARDS"], [], None),
    ("meera", "Meera S.", "meera.s@example.com", "demand_owner", "C2", "own", ["BANKING"], [], None),
    ("arjun", "Arjun D.", "arjun.d@example.com", "demand_owner", "C1", "own", ["DATA"], [], None),
    ("kavya", "Kavya R.", "kavya.r@example.com", "admin", "D2", "full", BUSINESS_UNITS, [], None),
    ("farah", "Farah Q.", "farah.q@example.com", "admin_team", "B2", "full", BUSINESS_UNITS, [], None),
    ("deepak", "Deepak L.", "deepak.l@example.com", "admin_team", "C1", "full", BUSINESS_UNITS, [], None),
    # Leadership is split by BU: Sanjay has PAYMENTS and DATA here (and all of Acme), Rakesh the rest.
    ("sanjay", "Sanjay M.", "sanjay.m@example.com", "leadership", "E1", "bu_read", ["PAYMENTS", "DATA"], [], None),
    (
        "vikram",
        "Vikram P.",
        "vikram.p@example.com",
        "interviewer",
        "D1",
        "assigned_interviews",
        ["PAYMENTS", "CARDS"],
        ["CCA-FS"],
        (["Java", "Spring Boot", "AWS"], "D1"),
    ),
    (
        "anita",
        "Anita G.",
        "anita.g@example.com",
        "interviewer",
        "C2",
        "assigned_interviews",
        ["CARDS"],
        ["DMN-FS"],
        (["Mainframe", "COBOL"], "C1"),
    ),
    # Runs the app's controls on request; sees no demands. Adds and deactivates client accounts.
    ("anil", "Anil V.", "anil.v@example.com", "administrator", "E1", "app_controls", [], [], None),
    # Last, so everyone above keeps their id.
    ("rakesh", "Rakesh S.", "rakesh.s@example.com", "leadership", "E1", "bu_read", ["CARDS", "BANKING"], [], None),
]

# Demand owners' cell numbers: 555-01xx, the range kept for fiction, so nobody real gets a call.
PHONES = {
    "priya": "+13125550101",
    "rahul": "+13125550102",
    "neha": "+13125550103",
    "meera": "+13125550104",
    "arjun": "+13125550105",
}

J, S, A = "Java", "Spring Boot", "AWS"

# ref, owner, BU, name, practice, grade, start, status, req id, extra fields
DEMANDS = [
    # PAYMENTS · Priya (the prototype's "My demands")
    (
        "DM-000151",
        "priya",
        "PAYMENTS",
        "Senior Java Full Stack Developer (Java + Spring Boot + AWS)",
        "CCA-FS",
        "D1",
        date(2026, 10, 27),
        "submitted",
        None,
        {"primary_skills": [J, S, A], "exp_min": 10, "exp_max": 14},
    ),
    (
        "DM-000150",
        "priya",
        "PAYMENTS",
        "Java Full Stack Developer (Java + Spring Boot + AWS)",
        "CCA-FS",
        "C2",
        date(2026, 10, 27),
        "draft",
        None,
        {"primary_skills": [J, S, A], "exp_min": 6, "exp_max": 10},
    ),
    (
        "DM-000142",
        "priya",
        "PAYMENTS",
        "#7-Senior Java Full Stack Developer",
        "CCA-FS",
        "D1",
        date(2026, 10, 27),
        "coverage_required",
        "2ZT7KP",
        {"primary_skills": [J, S, A]},
    ),
    (
        "DM-000139",
        "priya",
        "PAYMENTS",
        "Java AWS Developer Payments Chicago",
        "CCA-FS",
        "C1",
        date(2026, 10, 12),
        "missing",
        "7QWZ2L",
        {"primary_skills": [J, A]},
    ),
    (
        "DM-000121",
        "priya",
        "PAYMENTS",
        "01-Senior Salesforce Developer Payments Chicago",
        "DCX-FS",
        "C2",
        date(2026, 8, 3),
        "offer_in_process",
        "DIT7AF",
        {"primary_skills": ["Salesforce"]},
    ),
    (
        "DM-000118",
        "priya",
        "PAYMENTS",
        "*09-Java AWS Developer Payments Chicago",
        "CCA-FS",
        "C1",
        date(2026, 9, 1),
        "offer_in_market",
        "0ZTQQE",
        {"primary_skills": [J, A]},
    ),
    (
        "DM-000117",
        "priya",
        "PAYMENTS",
        "*05-Java AWS Developer Payments Chicago",
        "CCA-FS",
        "C1",
        date(2026, 9, 1),
        "offer_in_market",
        "IXT3SF",
        {"primary_skills": [J, A]},
    ),
    (
        "DM-000116",
        "priya",
        "PAYMENTS",
        "*06-Java AWS Developer Payments Chicago",
        "CCA-FS",
        "C1",
        date(2026, 9, 1),
        "staffed",
        "43TUIX",
        {"primary_skills": [J, A]},
    ),
    # CARDS · Rahul (own + BU read-only) and Neha
    (
        "DM-000149",
        "rahul",
        "CARDS",
        "Cards Mainframe Developer · Chicago",
        "DMN-FS",
        "B1",
        date(2026, 11, 3),
        "notified",
        None,
        {"primary_skills": ["Mainframe", "COBOL"]},
    ),
    (
        "DM-000135",
        "rahul",
        "CARDS",
        "Cards Mainframe Developer",
        "DMN-FS",
        "C1",
        date(2026, 10, 20),
        "coverage_required",
        "1UT9PL",
        {"primary_skills": ["Mainframe", "COBOL"]},
    ),
    (
        "DM-000131",
        "rahul",
        "CARDS",
        "Cards Mainframe Developer",
        "DMN-FS",
        "B1",
        date(2026, 10, 1),
        "offer_in_process",
        "BPTONE",
        {"primary_skills": ["Mainframe"]},
    ),
    (
        "DM-000144",
        "neha",
        "CARDS",
        "#02-Java Full Stack Developer",
        "CCA-FS",
        "C2",
        date(2026, 10, 27),
        "profiles_with_client",
        "D6T03K",
        {"primary_skills": [J, S, A]},
    ),
    (
        "DM-000146",
        "neha",
        "CARDS",
        "#3-Senior Java Full Stack Developer",
        "CCA-FS",
        "D1",
        date(2026, 11, 10),
        "coverage_required",
        "8NTHV6",
        {"primary_skills": [J, S, A]},
    ),
    # BANKING · Meera
    (
        "DM-000148",
        "meera",
        "BANKING",
        "Deposits Java Developer · Canada",
        "CCA-FS",
        "C2",
        date(2026, 11, 17),
        "sent_to_gtd",
        "EKT8HQ",
        {"primary_skills": [J, S], "region": "CA", "location": "Toronto"},
    ),
    (
        "DM-000126",
        "meera",
        "BANKING",
        "Banking Shared Services Mainframes",
        "TES-FS",
        "C1",
        date(2026, 9, 4),
        "profiles_with_client",
        "Y7TR36",
        {"primary_skills": ["Mainframe", "Testing"]},
    ),
    (
        "DM-000110",
        "meera",
        "BANKING",
        "Deposits Platform Engineer",
        "ADM-FS",
        "C1",
        date(2026, 9, 15),
        "cancelled",
        "3HQK9W",
        {},
    ),
    # DATA · Arjun
    (
        "DM-000147",
        "arjun",
        "DATA",
        "Data Engineer (Spark + Snowflake)",
        "DMN-FS",
        "C1",
        date(2026, 11, 3),
        "submitted",
        None,
        {"primary_skills": ["Spark", "Snowflake", "Python"]},
    ),
    (
        "DM-000133",
        "arjun",
        "DATA",
        "Senior Data Engineer",
        "DMN-FS",
        "D1",
        date(2026, 10, 13),
        "incorrect",
        "M2PX6D",
        {"primary_skills": ["Spark", "AWS"]},
    ),
]

# Vendor cost per hour: base by grade × channel factor, US; CA a little lower. Made-up numbers.
GRADE_COST = {"A5": 35, "B1": 45, "B2": 52, "C1": 60, "C2": 68, "D1": 80, "D2": 92, "E1": 105}
CHANNEL_FACTOR = {"gtd_supply": 0.85, "sogeti": 1.0, "subcon_vms": 1.05, "fte": 0.95}
REGION_FACTOR = {"US": 1.0, "CA": 0.9}
RATES_FROM = date(2026, 1, 1)

# Client bill rates per hour where they differ from the default. Chosen so the two offers in process
# land on either side of the 30% cut-off: DIT7AF (C2 subcon) below, BPTONE (B1 Sogeti) above.
BILL_RATES = {"DM-000121": 95, "DM-000131": 80, "DM-000142": 125, "DM-000146": 125, "DM-000151": 125,
              "DM-000118": 88, "DM-000117": 88, "DM-000116": 88, "DM-000126": 90}  # fmt: skip

DEFAULTS = {
    "category": "Open",
    "type": "New",
    "position_type": "Billable",
    "region": "US",
    "location": "Chicago",
    "work_mode": "Hybrid",
    "client_rate": 95,
}

# demand ref, type, level, days until due (negative = overdue), detail
ESCALATIONS = [
    ("DM-000139", "missing", 1, -1, "Sent to GTD, not in the BCM sheet after the grace period"),
    ("DM-000149", "not_submitted", 1, 2, "In yesterday's mail, no requisition ID yet"),
    ("DM-000121", "past_start", 2, 3, "Start 03 Aug · offer in process · no DOJ"),
    ("DM-000118", "rejection_limit", 1, 1, "5 FTE candidates rejected before offer"),
    ("DM-000135", "aging", 1, 4, "Coverage required, no profile progressed in 2 weeks"),
    ("DM-000133", "incorrect", 1, 2, "BCM sheet status: In Correct Demnad"),
]

# demand ref, candidate placeholder, round, interviewer key, scheduled (y, m, d, h, min) in UTC
INTERVIEWS = [
    ("DM-000142", "Candidate A", "L2", "vikram", (2026, 9, 25, 15, 30)),
    ("DM-000144", "Candidate B", "L2", "vikram", (2026, 9, 26, 19, 0)),
]

"""A made-up second client, Acme Insurance, set up only with what Account settings can hold.

It is deliberately unlike Discover NA: another time zone, other business units, practices, grade
names, supply channels, BCM sheet columns and statuses, rating dimensions and thresholds. Sanjay
(leadership) works in both accounts with one login; interviewers belong to one account only.
All names are placeholders.

Demand refs DM-000101..106 sit below Discover's seeded range, so new demands still continue after
Discover's highest ref.
"""

from datetime import date

ACCOUNT = {
    "name": "Acme Insurance",
    "grace_days": 5,
    "l1_sla_days": 3,
    "l2_sla_days": 5,
    "panel_timer_hours": 72,
    "aging_days": 21,
    "rejection_limit": 4,
    "margin_threshold": 25,
}

CONFIG = {
    "timezone": "America/New_York",
    "practices": ["APP-ENG", "DATA-ENG", "QA-AUTO"],
    "grades": ["L2", "L3", "L4", "L5", "L6"],
    "regions": ["US"],
    "work_modes": ["Onsite", "Hybrid", "Remote"],
    "categories": ["Committed", "Pipeline"],
    "supply_channels": [
        {"key": "bench", "label": "Internal bench", "sheet_marker": "Internal", "needs_sourcing_req": False},
        {
            "key": "partner",
            "label": "Partner network",
            "sheet_marker": "Partner",
            "needs_sourcing_req": False,
        },
        {"key": "contract", "label": "Contractor", "sheet_marker": "Contract", "needs_sourcing_req": True},
    ],
    "status_mapping": [
        {"status_group": "Pipeline", "status": "Sourcing", "stage": "coverage_required"},
        {"status_group": "Pipeline", "status": "Screening", "stage": "coverage_required"},
        {"status_group": "Client", "status": "Client Review", "stage": "profiles_with_client"},
        {"status_group": "Offer", "status": "Offer Pending", "stage": "offer_in_process"},
        {"status_group": "Offer", "status": "Offer Accepted", "stage": "offer_in_market"},
        {"status_group": "Closed", "status": "Joined", "stage": "staffed"},
        {"status_group": "Closed", "status": "Withdrawn", "stage": "cancelled"},
        {"status_group": "", "status": "Invalid Request", "stage": "incorrect"},
    ],
    "dp_columns": {
        "req_id": "Req #",
        "demand_request_name": "Position Title",
        "status": "Stage",
        "status_group": "Stage Group",
        "originator": "Requested By",
        "practice": "Practice Area",
        "grade": "Level",
        "region": "Country",
        "start_date": "Need By",
        "gettalent_req_id": "ATS ID",
        "source": "Supply Source",
        "candidate_name": "Candidate",
        "candidate_details": "Candidate Notes",
        "doj": "Joining Date",
    },
    "dp_blank_values": ["N/A", "TBD"],
    "interview_ratings": ["Coding", "System design", "Collaboration", "Insurance domain"],
    "escalation_owners": {"L1": "Practice lead", "L2": "Client partner"},
    "billable_hours_per_day": 7.5,
}

BUSINESS_UNITS = ["CLAIMS", "POLICY", "DIGITAL"]
DELIVERY_HEADS = {
    "CLAIMS": ("Hana K.", "hana.k@example.com"),
    "POLICY": ("Victor N.", "victor.n@example.com"),
    "DIGITAL": ("Ines M.", "ines.m@example.com"),
}

# Demand owners' cell numbers (555-01xx, kept for fiction).
PHONES = {"lena": "+14155550111", "marco": "+14155550112"}

# key, name, email, role, level, scope, BUs, practices, interviewer (skills, max grade).
# "sanjay" already exists (Discover): he gets a second membership, not a second login. Interviewers
# belong to one account only, so Acme has its own (Nadia).
USERS = [
    ("grace", "Grace H.", "grace.h@example.com", "admin", "L6", "full", BUSINESS_UNITS, [], None),
    ("rosa", "Rosa D.", "rosa.d@example.com", "administrator", "L6", "app_controls", [], [], None),
    ("tomas", "Tomas R.", "tomas.r@example.com", "admin_team", "L3", "full", BUSINESS_UNITS, [], None),
    ("lena", "Lena W.", "lena.w@example.com", "demand_owner", "L5", "own", ["CLAIMS"], [], None),
    ("marco", "Marco B.", "marco.b@example.com", "demand_owner", "L5", "own_bu_read", ["POLICY"], [], None),
    ("sanjay", "", "sanjay.m@example.com", "leadership", "L6", "full", BUSINESS_UNITS, [], None),
    (
        "nadia",
        "Nadia K.",
        "nadia.k@example.com",
        "interviewer",
        "L5",
        "assigned_interviews",
        ["CLAIMS", "DIGITAL"],
        ["APP-ENG"],
        (["Java", "Spring Boot", "AWS", "Kotlin"], "L5"),
    ),
]

J, S, A = "Java", "Spring Boot", "AWS"

# ref, owner, BU, name, practice, grade, start, status, req id, extra fields
DEMANDS = [
    (
        "DM-000101",
        "lena",
        "CLAIMS",
        "Claims Platform Java Engineer",
        "APP-ENG",
        "L4",
        date(2026, 11, 2),
        "coverage_required",
        "AC1001",
        {"primary_skills": [J, S, A], "client_rate": 92},
    ),
    (
        "DM-000102",
        "lena",
        "CLAIMS",
        "Claims Data Engineer (Spark)",
        "DATA-ENG",
        "L3",
        date(2026, 11, 9),
        "sent_to_gtd",
        "AC1002",
        {"primary_skills": ["Spark", "Python"], "client_rate": 80},
    ),
    (
        "DM-000103",
        "marco",
        "POLICY",
        "Policy Admin QA Automation Lead",
        "QA-AUTO",
        "L5",
        date(2026, 10, 19),
        "offer_in_process",
        "AC1003",
        {"primary_skills": ["Selenium", "Java"], "client_rate": 100},
    ),
    (
        "DM-000104",
        "marco",
        "POLICY",
        "Policy Portal React Developer",
        "APP-ENG",
        "L3",
        date(2026, 11, 16),
        "submitted",
        None,
        {"primary_skills": ["React", "TypeScript"], "client_rate": 78},
    ),
    (
        "DM-000105",
        "grace",
        "DIGITAL",
        "Digital Mobile Engineer",
        "APP-ENG",
        "L4",
        date(2026, 10, 26),
        "profiles_with_client",
        "AC1005",
        {"primary_skills": ["Kotlin", "Swift"], "client_rate": 95, "client_interview_required": True},
    ),
    (
        "DM-000106",
        "lena",
        "CLAIMS",
        "Claims Intake Analyst",
        "DATA-ENG",
        "L2",
        date(2026, 9, 1),
        "draft",
        None,
        {"primary_skills": ["SQL"], "client_rate": 60},
    ),
]

DEFAULTS = {
    "category": "Committed",
    "type": "New",
    "position_type": "Billable",
    "region": "US",
    "location": "Hartford",
    "work_mode": "Hybrid",
}

GRADE_COST = {"L2": 40, "L3": 50, "L4": 62, "L5": 75, "L6": 90}
CHANNEL_FACTOR = {"bench": 0.8, "partner": 1.0, "contract": 1.1}
RATES_FROM = date(2026, 1, 1)

# demand ref, candidate placeholder, round, interviewer key, scheduled (y, m, d, h, min) in UTC
INTERVIEWS = [("DM-000101", "Candidate P", "L1", "nadia", (2026, 9, 28, 14, 0))]

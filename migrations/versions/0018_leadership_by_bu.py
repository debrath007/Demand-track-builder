"""Leadership can be limited to chosen business units (visibility "bu_read") instead of the full account.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPES_OLD = ("own", "own_bu_read", "full", "assigned_interviews", "app_controls")
TAIL = (
    " OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews')"
    " OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))"
    " OR (role = 'administrator' AND visibility_scope = 'app_controls')"
)
MATCH_OLD = "(role IN ('admin', 'admin_team', 'leadership') AND visibility_scope = 'full')" + TAIL
MATCH_NEW = (
    "(role IN ('admin', 'admin_team') AND visibility_scope = 'full')"
    " OR (role = 'leadership' AND visibility_scope IN ('full', 'bu_read'))" + TAIL
)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _swap(name: str, rule: str) -> None:
    op.drop_constraint(op.f(f"ck_user_accounts_{name}"), "user_accounts", type_="check")
    op.create_check_constraint(op.f(f"ck_user_accounts_{name}"), "user_accounts", rule)


def upgrade() -> None:
    _swap("scope_valid", _in("visibility_scope", (*SCOPES_OLD, "bu_read")))
    _swap("scope_matches_role", MATCH_NEW)


def downgrade() -> None:
    # Fails while any leadership is limited to chosen BUs: widen them to the full account first.
    _swap("scope_matches_role", MATCH_OLD)
    _swap("scope_valid", _in("visibility_scope", SCOPES_OLD))

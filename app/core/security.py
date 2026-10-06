"""Who is acting, and what they may see.

`current_user` resolves the acting user. Today it reads the dev-only "View as" cookie; in Phase 7 it
reads the SSO session instead. Nothing downstream changes, because every screen depends on `Actor`,
and an Actor's role, BUs, practices and scope always come from the User access page.

A person can belong to several accounts with a different role in each. The Actor is always one
person *in one account*: the account picked with the switcher (cookie), else their first.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.db import get_db
from app.core.enums import Role, Scope
from app.models import User, UserAccount

VIEW_AS_COOKIE = "dt_view_as"
ACCOUNT_COOKIE = "dt_account"


class AccessRemoved(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_403_FORBIDDEN, "Your access has been removed. Ask the admin to restore it."
        )


@dataclass(frozen=True)
class Actor:
    id: int
    name: str
    email: str
    role: Role
    scope: Scope
    level: str | None
    account_id: int
    account_name: str
    bu_ids: frozenset[int]
    bu_names: tuple[str, ...]
    practices: tuple[str, ...]
    accounts: tuple[tuple[int, str], ...] = ()  # every account this person may switch to

    @property
    def is_full(self) -> bool:
        return self.scope is Scope.FULL

    @property
    def bu_limit(self) -> frozenset[int] | None:
        """For leadership over chosen BUs: those BUs, on every account-wide screen. Otherwise None."""
        return self.bu_ids if self.scope is Scope.BU_READ else None

    @property
    def can_see_bill_rate(self) -> bool:
        """Rates across the whole account: the GTD team admin and leadership. See `sees_rates`."""
        return self.role in (Role.ADMIN, Role.LEADERSHIP)

    def sees_rates(self, owner_id: int | None) -> bool:
        """Bill rate, vendor cost and margin: the GTD team admin and leadership for every demand, and a
        demand owner for their own."""
        return self.can_see_bill_rate or (owner_id is not None and owner_id == self.id)

    @property
    def access_summary(self) -> str:
        where = "All BUs" if self.is_full else (", ".join(self.bu_names) or "No BU")
        return f"{self.scope.label} · {where}"


def usable_memberships(user: User) -> list[UserAccount]:
    return [m for m in user.memberships if m.active and m.account.active]


def actor_from_user(db: Session, user: User, account_id: int | None = None) -> Actor:
    if not user.active:
        raise AccessRemoved()
    usable = usable_memberships(user)
    if not usable:
        if any(m.account.active for m in user.memberships):
            raise AccessRemoved()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not assigned to an account.")
    m = next((x for x in usable if x.account_id == account_id), usable[0])
    account = m.account
    bus = [b for b in user.business_units if b.account_id == account.id]
    return Actor(
        id=user.id,
        name=user.name,
        email=user.email,
        role=m.role_enum,
        scope=m.scope_enum,
        level=m.level,
        account_id=account.id,
        account_name=account.name,
        bu_ids=frozenset(b.id for b in bus),
        bu_names=tuple(b.name for b in bus),
        practices=tuple(p for p in user.practices if p in set(account.settings.practices)),
        accounts=tuple((x.account_id, x.account.name) for x in usable),
    )


def _load_user(db: Session, user_id: int) -> User | None:
    return db.scalar(
        select(User)
        .where(User.id == user_id)
        .options(
            selectinload(User.memberships).selectinload(UserAccount.account),
            selectinload(User.business_units),
            selectinload(User.practice_links),
        )
    )


def default_view_user_id(db: Session) -> int | None:
    """Who the switcher shows before anyone picks: the first active demand owner."""
    owner = select(UserAccount.user_id).where(UserAccount.role == Role.DEMAND_OWNER.value, UserAccount.active)
    return db.scalar(
        select(User.id).where(User.active, User.id.in_(owner)).order_by(User.id).limit(1)
    ) or db.scalar(select(User.id).where(User.active).order_by(User.id).limit(1))


def current_user(request: Request, db: Session = Depends(get_db)) -> Actor:
    settings = get_settings()
    if not settings.view_switcher_enabled:
        # Phase 7: resolve the SSO session here.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in is not configured yet.")

    raw = request.cookies.get(VIEW_AS_COOKIE, "")
    user_id = int(raw) if raw.isdigit() else default_view_user_id(db)
    user = _load_user(db, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No users yet. Run the seed script.")
    raw_account = request.cookies.get(ACCOUNT_COOKIE, "")
    return actor_from_user(db, user, int(raw_account) if raw_account.isdigit() else None)


def require_role(*roles: Role) -> Callable[..., Actor]:
    """Router/route dependency: only these roles may enter."""

    def guard(actor: Actor = Depends(current_user)) -> Actor:
        if actor.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This page isn't available for your role.")
        return actor

    return guard


def require_screen(key: str) -> Callable[..., Actor]:
    """Guard a screen with the roles that have it in their menu (app.core.nav), so the two never drift."""
    from app.core.nav import BY_KEY

    return require_role(*BY_KEY[key].labels)

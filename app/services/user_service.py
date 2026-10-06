"""User access: who exists, their role, BUs, practices and what they see.

Rules (flow-artifact §1.1):
- GTD team admins, their GTD admin team and leadership always see the full account, all BUs. Fixed.
- A demand owner belongs to exactly one BU and sees their own demands; the admin may widen that to
  own + BU read-only.
- Interviewers see assigned interviews; their skills, practices and max grade live on their profile.
- Deactivating removes access immediately; demands and history stay.
- Role, visibility and level are per account (UserAccount). One person can work in several accounts
  with one login; adding an existing person to another account gives them a membership there.
- An interviewer belongs to exactly one account: nobody who works in another account can be an
  interviewer here, and an interviewer can't be added to another account in any role.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import ALLOWED_SCOPES, Role, Scope
from app.core.security import Actor
from app.models import Account, BusinessUnit, InterviewerProfile, User, UserAccount, UserPractice
from app.schemas.user_access import UserForm


class UserAccessError(ValueError):
    pass


@dataclass
class Member:
    """A person as seen from one account: their membership there plus who they are."""

    user: User
    m: UserAccount

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def name(self) -> str:
        return self.user.name

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def role(self) -> str:
        return self.m.role

    @property
    def role_enum(self) -> Role:
        return self.m.role_enum

    @property
    def visibility_scope(self) -> str:
        return self.m.visibility_scope

    @property
    def scope_enum(self) -> Scope:
        return self.m.scope_enum

    @property
    def level(self) -> str | None:
        return self.m.level

    @property
    def active(self) -> bool:
        return self.user.active and self.m.active

    @property
    def business_units(self) -> list[BusinessUnit]:
        return [b for b in self.user.business_units if b.account_id == self.m.account_id]

    @property
    def practices(self) -> list[str]:
        """Only this account's practices; the person may hold others elsewhere."""
        here = set(self.m.account.settings.practices)
        return [p for p in self.user.practices if p in here]

    @property
    def interviewer_profile(self) -> InterviewerProfile | None:
        return self.user.interviewer_profile

    @property
    def other_accounts(self) -> list[str]:
        return [
            x.account.name for x in self.user.memberships if x.account_id != self.m.account_id and x.active
        ]


def effective_scope(role: Role, requested: Scope | None) -> Scope:
    allowed = ALLOWED_SCOPES[role]
    return requested if requested in allowed else allowed[0]


def list_users(db: Session, account_id: int) -> list[Member]:
    rows = db.execute(
        select(User, UserAccount)
        .join(UserAccount, (UserAccount.user_id == User.id) & (UserAccount.account_id == account_id))
        .options(
            selectinload(User.business_units),
            selectinload(User.practice_links),
            selectinload(User.interviewer_profile),
            selectinload(User.memberships).selectinload(UserAccount.account),
        )
        .order_by((User.active & UserAccount.active).desc(), User.name)
    ).all()
    return [Member(u, m) for u, m in rows]


def get_user(db: Session, account_id: int, user_id: int) -> Member:
    m = db.get(UserAccount, (user_id, account_id))
    if m is None:
        raise UserAccessError("User not found in this account.")
    return Member(m.user, m)


def interviewer_conflict(user: User, account_id: int, role: Role) -> str | None:
    """Why this person can't hold this role in this account, when an interviewer would span two accounts."""
    others = [x for x in user.memberships if x.account_id != account_id]
    if not others:
        return None
    rule = "An interviewer belongs to one account only."
    iv = next((x for x in others if x.role == Role.INTERVIEWER.value), None)
    if iv is not None:
        return f"{user.name} is an interviewer in {iv.account.name}. {rule}"
    if role is Role.INTERVIEWER:
        return f"{user.name} already works in {', '.join(x.account.name for x in others)}. {rule}"
    return None


def _apply(db: Session, actor: Actor, user: User, m: UserAccount, form: UserForm) -> None:
    account = db.get_one(Account, actor.account_id)
    if user.id is not None and (why := interviewer_conflict(user, actor.account_id, form.role)):
        raise UserAccessError(why)
    account_bus = list(
        db.scalars(
            select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id, BusinessUnit.active)
        )
    )
    clash = db.scalar(
        select(User.id).where(func.lower(User.email) == form.email.lower(), User.id != (user.id or 0))
    )
    if clash:
        raise UserAccessError(f"{form.email} is already a user.")

    user.name = form.name
    user.email = form.email
    m.role = form.role.value
    m.level = form.level
    scope = effective_scope(form.role, form.scope)
    m.visibility_scope = scope.value

    # Business units and practices of the person's other accounts are left as they are.
    others = [b for b in user.business_units if b.account_id != actor.account_id]
    if scope is Scope.FULL:
        user.business_units = others + account_bus  # full account: every BU
    else:
        chosen = [b for b in account_bus if b.id in set(form.bu_ids)]
        if form.role is Role.DEMAND_OWNER and len(chosen) != 1:
            raise UserAccessError("A demand owner belongs to exactly one business unit.")
        if scope is Scope.BU_READ and not chosen:
            raise UserAccessError("Choose the business units this person sees.")
        user.business_units = others + chosen

    here = set(account.settings.practices)
    kept = [p for p in user.practices if p not in here]
    user.practice_links = [UserPractice(practice=p) for p in dict.fromkeys(kept + list(form.practices))]

    if form.role is Role.INTERVIEWER:
        profile = user.interviewer_profile or InterviewerProfile()
        elsewhere = [p for p in (profile.practices or []) if p not in here]
        profile.practices = list(dict.fromkeys(elsewhere + list(form.practices)))
        profile.skills = list(form.skills)
        profile.max_grade = form.max_grade
        profile.active = True
        user.interviewer_profile = profile
    elif user.interviewer_profile is not None and not any(
        x.role == Role.INTERVIEWER.value and x.account_id != actor.account_id for x in user.memberships
    ):
        user.interviewer_profile.active = False


# An account must always keep one of each: someone to run the app's controls, someone to run the work.
MUST_KEEP_ONE = (Role.ADMINISTRATOR, Role.ADMIN)


def _guard_last(db: Session, actor: Actor, user_id: int, leaving: Role | None) -> None:
    """Refuse when this person is the account's last active holder of a must-keep role."""
    if leaving not in MUST_KEEP_ONE:
        return
    others = db.scalar(
        select(func.count())
        .select_from(UserAccount)
        .join(User, User.id == UserAccount.user_id)
        .where(
            UserAccount.account_id == actor.account_id,
            UserAccount.role == leaving.value,
            UserAccount.active,
            User.active,
            UserAccount.user_id != user_id,
        )
    )
    if not others:
        raise UserAccessError(f"The account needs at least one active {leaving.label}.")


def create_user(db: Session, actor: Actor, form: UserForm) -> Member:
    """Add someone to this account. Someone who already works in another account keeps one login and
    gets a membership here, with its own role."""
    existing = db.scalar(select(User).where(func.lower(User.email) == form.email.lower()))
    if existing is not None:
        if existing.membership(actor.account_id) is not None:
            raise UserAccessError(f"{form.email} is already a user in this account.")
        user = existing
        form = form.model_copy(update={"name": existing.name, "email": existing.email})
    else:
        user = User(created_by=actor.id, active=True)
        db.add(user)
    m = UserAccount(account_id=actor.account_id, active=True)
    user.memberships.append(m)
    _apply(db, actor, user, m, form)
    db.commit()
    return Member(user, m)


def update_user(db: Session, actor: Actor, user_id: int, form: UserForm) -> Member:
    member = get_user(db, actor.account_id, user_id)
    if member.active and form.role is not member.role_enum:
        _guard_last(db, actor, user_id, member.role_enum)
    _apply(db, actor, member.user, member.m, form)
    db.commit()
    return member


def set_active(db: Session, actor: Actor, user_id: int, active: bool) -> Member:
    """Access to this account only; the person's other accounts are untouched."""
    member = get_user(db, actor.account_id, user_id)
    if not active:
        if user_id == actor.id:
            raise UserAccessError("You can't deactivate yourself.")
        _guard_last(db, actor, user_id, member.role_enum)
    member.m.active = active
    if active:
        member.user.active = True
    db.commit()
    return member

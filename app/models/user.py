from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ColumnElement, ForeignKey, Index, String, and_, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import Role, Scope, check_in
from app.models.account import Account, BusinessUnit


class User(Base):
    """A person. What they do and see is set per account on their membership (UserAccount), from the
    User access page; login only identifies them."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))  # cell, E.164 (+13125550101)
    # False blocks sign-in everywhere. Access to one account is UserAccount.active.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    accounts: Mapped[list[Account]] = relationship(
        secondary="user_accounts", order_by=Account.id, viewonly=True
    )
    business_units: Mapped[list[BusinessUnit]] = relationship(
        secondary="user_business_units", order_by=BusinessUnit.id
    )
    practice_links: Mapped[list["UserPractice"]] = relationship(cascade="all, delete-orphan")
    interviewer_profile: Mapped["InterviewerProfile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    memberships: Mapped[list["UserAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="UserAccount.account_id"
    )

    def membership(self, account_id: int) -> "UserAccount | None":
        return next((m for m in self.memberships if m.account_id == account_id), None)

    @property
    def practices(self) -> list[str]:
        return sorted(p.practice for p in self.practice_links)


# Emails are unique regardless of case.
Index("uq_users_email_lower", func.lower(User.email), unique=True)


class UserAccount(Base):
    """A person's membership of one account: their role, visibility and level there."""

    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint(check_in("role", Role), name="role_valid"),
        CheckConstraint(check_in("visibility_scope", Scope), name="scope_valid"),
        # The access rule itself, enforced by the database (mirrors enums.ALLOWED_SCOPES).
        CheckConstraint(
            "(role IN ('admin', 'admin_team') AND visibility_scope = 'full')"
            " OR (role = 'leadership' AND visibility_scope IN ('full', 'bu_read'))"
            " OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews')"
            " OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))"
            " OR (role = 'administrator' AND visibility_scope = 'app_controls')",
            name="scope_matches_role",
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    level: Mapped[str | None] = mapped_column(String(10))
    visibility_scope: Mapped[str] = mapped_column(String(24))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    user: Mapped[User] = relationship(back_populates="memberships")
    account: Mapped[Account] = relationship()

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    @property
    def scope_enum(self) -> Scope:
        return Scope(self.visibility_scope)


def member_of(account_id: int, *roles: Role, active: bool = True) -> ColumnElement[bool]:
    """Filter for select(User): members of the account (optionally with these roles), active by default."""
    sub = select(UserAccount.user_id).where(UserAccount.account_id == account_id)
    if roles:
        sub = sub.where(UserAccount.role.in_([r.value for r in roles]))
    if active:
        sub = sub.where(UserAccount.active)
        return and_(User.active, User.id.in_(sub))
    return User.id.in_(sub)


def leads_bu(account_id: int, bu_id: int | None) -> ColumnElement[bool]:
    """Filter for select(User): the account's active leadership who cover this BU, i.e. the whole
    account, or chosen BUs that include it. With no BU (e.g. a BCM sheet row): full-account ones only."""
    lead = select(UserAccount.user_id).where(
        UserAccount.account_id == account_id,
        UserAccount.role == Role.LEADERSHIP.value,
        UserAccount.active,
    )
    full = lead.where(UserAccount.visibility_scope == Scope.FULL.value)
    if bu_id is None:
        return and_(User.active, User.id.in_(full))
    covering = lead.where(
        UserAccount.visibility_scope == Scope.BU_READ.value,
        UserAccount.user_id.in_(select(UserBusinessUnit.user_id).where(UserBusinessUnit.bu_id == bu_id)),
    )
    return and_(User.active, or_(User.id.in_(full), User.id.in_(covering)))


class UserBusinessUnit(Base):
    __tablename__ = "user_business_units"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    bu_id: Mapped[int] = mapped_column(ForeignKey("business_units.id"), primary_key=True)


class UserPractice(Base):
    __tablename__ = "user_practices"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    practice: Mapped[str] = mapped_column(String(40), primary_key=True)


class InterviewerProfile(Base):
    __tablename__ = "interviewer_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    practices: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    max_grade: Mapped[str | None] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="interviewer_profile")

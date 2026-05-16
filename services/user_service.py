"""User + team provisioning service — v2 async + Postgres.

These functions sit behind ``wl team add|list|remove``. They run under
the admin/service connection (``WORKLOG_DB_SERVICE_ROLE_KEY``) because
``CREATE ROLE`` and team-membership writes require ownership the
per-user roles don't have.

Note on ``CREATE ROLE``: the role name is shape-validated (same regex as
``services.storage.postgres._validate_role``) before being inlined into
the SQL, because Postgres identifiers cannot be parameter-bound.
Passwords ARE parameter-bound via psycopg, so they're safe from
injection even though the role itself is not.
"""

from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from db.models import Team, TeamMember, TeamRole, User

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UserExists(RuntimeError):
    pass


class UserNotFound(RuntimeError):
    pass


def _role_name(display_name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", display_name.lower()).strip("_")
    if not base or not _ROLE_NAME.match(f"wl_{base}"):
        base = f"user_{uuid.uuid4().hex[:8]}"
    return f"wl_{base}_{uuid.uuid4().hex[:6]}"


async def create_user(
    session: AsyncSession,
    *,
    display_name: str,
    email: str | None = None,
    pg_role: str | None = None,
    create_pg_role: bool = True,
    password: str | None = None,
) -> tuple[User, str | None]:
    """Insert a users row and (optionally) provision the Postgres role.

    Returns (user, generated_password_or_None). The caller is responsible
    for handing the password to the new user out of band.
    """
    if email:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            raise UserExists(f"user with email {email!r} already exists")

    role = pg_role or _role_name(display_name)
    if not _ROLE_NAME.match(role):
        raise ValueError(f"invalid Postgres role name: {role!r}")

    generated_password: str | None = None
    if create_pg_role:
        generated_password = password or secrets.token_urlsafe(24)
        # Role name is shape-validated above; safe to inline.
        await session.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD :pwd "
                f"NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT"
            ),
            {"pwd": generated_password},
        )

    user = User(pg_role=role, display_name=display_name, email=email)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user, generated_password


async def get_by_pg_role(session: AsyncSession, pg_role: str) -> User | None:
    return (
        await session.execute(select(User).where(User.pg_role == pg_role))
    ).scalar_one_or_none()


async def list_users(session: AsyncSession) -> list[User]:
    return list(
        (await session.execute(select(User).order_by(User.created_at))).scalars()
    )


async def remove_user(session: AsyncSession, *, pg_role: str) -> None:
    user = await get_by_pg_role(session, pg_role)
    if not user:
        raise UserNotFound(pg_role)
    if not _ROLE_NAME.match(pg_role):
        raise ValueError(f"invalid Postgres role name: {pg_role!r}")
    await session.delete(user)
    await session.flush()
    # Drop the Postgres role last so DELETE on the users row can roll back
    # if anything else in the transaction fails.
    await session.execute(text(f"DROP ROLE IF EXISTS {pg_role}"))


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


async def create_team(
    session: AsyncSession, *, name: str, owner: User
) -> Team:
    team = Team(name=name)
    session.add(team)
    await session.flush()
    session.add(
        TeamMember(team_id=team.id, user_id=owner.id, role=TeamRole.OWNER.value)
    )
    await session.flush()
    await session.refresh(team)
    return team


async def add_member(
    session: AsyncSession,
    *,
    team: Team,
    user: User,
    role: TeamRole = TeamRole.MEMBER,
) -> TeamMember:
    member = TeamMember(team_id=team.id, user_id=user.id, role=role.value)
    session.add(member)
    await session.flush()
    return member


async def list_teams(session: AsyncSession) -> list[Team]:
    return list(
        (await session.execute(select(Team).order_by(Team.created_at))).scalars()
    )

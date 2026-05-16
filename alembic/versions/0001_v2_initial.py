"""v2 initial schema — identity, workstreams, raw events, agent traces, RLS.

Revision ID: 0001_v2_initial
Revises:
Create Date: 2026-05-16

Phase 5 foundation. Memory tables land in phase 6 (0002_v2_memory_tables).

Tables created:
    users, teams, team_members,
    workstreams, raw_events, agent_traces

Plus:
    current_user_id() SQL helper used by RLS policies.
    RLS enabled on every table; per-table policies sketched in
    v2 architecture.md §11.2.

The upgrade is one transactional unit; the downgrade drops everything
in reverse order. No data preservation — v2 is a fresh-start backend
(v1 lives on the v1 branch / v1-final tag).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_v2_initial"
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Reusable SQL chunks (kept inline so the migration is reviewable in one read)
# ---------------------------------------------------------------------------

CURRENT_USER_FN = """
CREATE OR REPLACE FUNCTION current_user_id() RETURNS UUID
LANGUAGE sql STABLE AS $$
    SELECT id FROM users WHERE pg_role = current_user
$$;
"""

DROP_CURRENT_USER_FN = "DROP FUNCTION IF EXISTS current_user_id();"


RLS_POLICIES_UP = [
    # ----- users -----
    "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY users_self_select ON users FOR SELECT
       USING (id = current_user_id() OR id IN
              (SELECT u.id FROM users u
                 JOIN team_members tm ON tm.user_id = u.id
                WHERE tm.team_id IN
                      (SELECT team_id FROM team_members
                        WHERE user_id = current_user_id())))""",
    "CREATE POLICY users_no_delete ON users FOR DELETE USING (false)",

    # ----- teams -----
    "ALTER TABLE teams ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY teams_member_select ON teams FOR SELECT
       USING (id IN (SELECT team_id FROM team_members
                       WHERE user_id = current_user_id()))""",

    # ----- team_members -----
    "ALTER TABLE team_members ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY tm_member_select ON team_members FOR SELECT
       USING (team_id IN (SELECT team_id FROM team_members
                            WHERE user_id = current_user_id()))""",

    # ----- workstreams -----
    "ALTER TABLE workstreams ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY ws_select ON workstreams FOR SELECT
       USING (
            owner_id = current_user_id()
         OR (visibility = 'team' AND team_id IN
                (SELECT team_id FROM team_members
                   WHERE user_id = current_user_id()))
       )""",
    """CREATE POLICY ws_insert ON workstreams FOR INSERT
       WITH CHECK (owner_id = current_user_id())""",
    """CREATE POLICY ws_modify ON workstreams FOR UPDATE
       USING (owner_id = current_user_id())
       WITH CHECK (owner_id = current_user_id())""",
    "CREATE POLICY ws_no_delete ON workstreams FOR DELETE USING (false)",

    # ----- raw_events -----
    "ALTER TABLE raw_events ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY re_select ON raw_events FOR SELECT
       USING (
            owner_id = current_user_id()
         OR (workstream_id IS NOT NULL AND workstream_id IN
                (SELECT id FROM workstreams
                  WHERE visibility = 'team' AND team_id IN
                    (SELECT team_id FROM team_members
                       WHERE user_id = current_user_id())))
       )""",
    """CREATE POLICY re_insert ON raw_events FOR INSERT
       WITH CHECK (owner_id = current_user_id())""",
    # raw events are immutable archive — no UPDATE or DELETE policy is created,
    # which under RLS denies the operation by default.

    # ----- agent_traces -----
    "ALTER TABLE agent_traces ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY traces_select ON agent_traces FOR SELECT
       USING (actor_id IS NULL OR actor_id = current_user_id())""",
    """CREATE POLICY traces_insert ON agent_traces FOR INSERT
       WITH CHECK (actor_id IS NULL OR actor_id = current_user_id())""",
    """CREATE POLICY traces_update ON agent_traces FOR UPDATE
       USING (actor_id IS NULL OR actor_id = current_user_id())""",
]


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ----- identity --------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pg_role", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), unique=True, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "team_members",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role", sa.String(), nullable=False, server_default="member"
        ),
        sa.CheckConstraint(
            "role IN ('owner','admin','member')", name="ck_team_members_role"
        ),
    )

    # ----- workstreams -----------------------------------------------------
    op.create_table(
        "workstreams",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(), nullable=False, unique=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(), nullable=False, server_default="active"
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id"),
            nullable=True,
        ),
        sa.Column(
            "visibility",
            sa.String(),
            nullable=False,
            server_default="private",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','paused','completed','archived')",
            name="ck_workstreams_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('private','team')",
            name="ck_workstreams_visibility",
        ),
    )

    # ----- raw_events ------------------------------------------------------
    op.create_table(
        "raw_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "workstream_id",
            sa.BigInteger(),
            sa.ForeignKey("workstreams.id"),
            nullable=True,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "kind IN ('capture','reflection','event_connected','status_update',"
            "'summary_generated','voided','git_commit','slack_message',"
            "'meeting_segment','terminal_history','github_event')",
            name="ck_raw_events_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_raw_events_metadata_object",
        ),
    )
    op.create_index("ix_raw_events_ts", "raw_events", ["ts"])
    op.create_index("ix_raw_events_ws_kind", "raw_events", ["workstream_id", "kind"])
    op.create_index("ix_raw_events_kind", "raw_events", ["kind"])
    op.create_index("ix_raw_events_owner_ts", "raw_events", ["owner_id", "ts"])
    op.create_index(
        "ix_raw_events_content_tsv",
        "raw_events",
        ["content_tsv"],
        postgresql_using="gin",
    )

    # ----- agent_traces ----------------------------------------------------
    op.create_table(
        "agent_traces",
        sa.Column(
            "trace_id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "events",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('running','ok','error')", name="ck_agent_traces_status"
        ),
    )
    op.create_index(
        "ix_traces_agent_started", "agent_traces", ["agent_name", "started_at"]
    )

    # ----- helper + RLS ----------------------------------------------------
    op.execute(CURRENT_USER_FN)
    for stmt in RLS_POLICIES_UP:
        op.execute(stmt)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    op.execute(DROP_CURRENT_USER_FN)
    op.drop_index("ix_traces_agent_started", table_name="agent_traces")
    op.drop_table("agent_traces")
    op.drop_index("ix_raw_events_content_tsv", table_name="raw_events")
    op.drop_index("ix_raw_events_owner_ts", table_name="raw_events")
    op.drop_index("ix_raw_events_kind", table_name="raw_events")
    op.drop_index("ix_raw_events_ws_kind", table_name="raw_events")
    op.drop_index("ix_raw_events_ts", table_name="raw_events")
    op.drop_table("raw_events")
    op.drop_table("workstreams")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("users")

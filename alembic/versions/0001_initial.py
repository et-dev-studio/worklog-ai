"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workstreams",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False, unique=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("ACTIVE", "PAUSED", "COMPLETED", "ARCHIVED", name="workstreamstatus"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("type", sa.Enum("CAPTURE", "REFLECTION", "EVENT_CONNECTED", "STATUS_UPDATE", "SUMMARY_GENERATED", name="eventtype"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("workstream_id", sa.String(), sa.ForeignKey("workstreams.id"), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.CheckConstraint("metadata IS NULL OR json_valid(metadata)", name="ck_events_metadata_json"),
    )
    op.create_table(
        "reflections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("event_id", sa.String(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "summaries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("summaries")
    op.drop_table("reflections")
    op.drop_table("events")
    op.drop_table("workstreams")

"""add hot-path indexes

Revision ID: 0002_add_indexes
Revises: 0001_initial
Create Date: 2026-05-07
"""
from alembic import op


revision = "0002_add_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_events_timestamp", "events", ["timestamp"])
    op.create_index("ix_events_workstream_type", "events", ["workstream_id", "type"])
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_reflections_event_id", "reflections", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_reflections_event_id", table_name="reflections")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_index("ix_events_workstream_type", table_name="events")
    op.drop_index("ix_events_timestamp", table_name="events")

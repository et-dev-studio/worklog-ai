"""widen events.type CHECK to allow VOIDED

Revision ID: 0003_voided_event_type
Revises: 0002_add_indexes
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_voided_event_type"
down_revision = "0002_add_indexes"
branch_labels = None
depends_on = None


NEW_VALUES = ("CAPTURE", "REFLECTION", "EVENT_CONNECTED", "STATUS_UPDATE", "SUMMARY_GENERATED", "VOIDED")
OLD_VALUES = ("CAPTURE", "REFLECTION", "EVENT_CONNECTED", "STATUS_UPDATE", "SUMMARY_GENERATED")


def _recreate_events(values: tuple[str, ...]) -> None:
    enum = sa.Enum(*values, name="eventtype")
    with op.batch_alter_table("events") as batch:
        batch.alter_column("type", type_=enum, existing_type=enum)


def upgrade() -> None:
    _recreate_events(NEW_VALUES)


def downgrade() -> None:
    _recreate_events(OLD_VALUES)

"""tags + event_tags

Revision ID: 0004_tags
Revises: 0003_voided_event_type
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_tags"
down_revision = "0003_voided_event_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "event_tags",
        sa.Column("event_id", sa.String(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("tag_id", sa.String(), sa.ForeignKey("tags.id"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "tag_id", name="pk_event_tags"),
    )
    op.create_index("ix_event_tags_tag_id", "event_tags", ["tag_id"])
    op.create_index("ix_event_tags_event_id", "event_tags", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_tags_event_id", table_name="event_tags")
    op.drop_index("ix_event_tags_tag_id", table_name="event_tags")
    op.drop_table("event_tags")
    op.drop_table("tags")

"""events FTS5 mirror with triggers

Revision ID: 0005_events_fts
Revises: 0004_tags
Create Date: 2026-05-07
"""
from alembic import op


revision = "0005_events_fts"
down_revision = "0004_tags"
branch_labels = None
depends_on = None


_UPGRADE_STATEMENTS = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
    "content, event_id UNINDEXED, tokenize='porter unicode61')",
    "CREATE TRIGGER IF NOT EXISTS events_ai_fts AFTER INSERT ON events BEGIN "
    "INSERT INTO events_fts(content, event_id) VALUES (new.content, new.id); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS events_ad_fts AFTER DELETE ON events BEGIN "
    "DELETE FROM events_fts WHERE event_id = old.id; "
    "END",
    "CREATE TRIGGER IF NOT EXISTS events_au_fts AFTER UPDATE OF content ON events BEGIN "
    "DELETE FROM events_fts WHERE event_id = old.id; "
    "INSERT INTO events_fts(content, event_id) VALUES (new.content, new.id); "
    "END",
    "INSERT INTO events_fts(content, event_id) SELECT content, id FROM events",
]

_DOWNGRADE_STATEMENTS = [
    "DROP TRIGGER IF EXISTS events_au_fts",
    "DROP TRIGGER IF EXISTS events_ad_fts",
    "DROP TRIGGER IF EXISTS events_ai_fts",
    "DROP TABLE IF EXISTS events_fts",
]


def upgrade() -> None:
    bind = op.get_bind()
    for stmt in _UPGRADE_STATEMENTS:
        bind.exec_driver_sql(stmt)


def downgrade() -> None:
    bind = op.get_bind()
    for stmt in _DOWNGRADE_STATEMENTS:
        bind.exec_driver_sql(stmt)

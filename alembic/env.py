"""Alembic env for worklog v2 (Postgres only).

Migrations always run under the admin/service role — DDL + CREATE POLICY
require ownership the per-user roles do not have. Reads
``WORKLOG_DB_SERVICE_ROLE_KEY`` first; falls back to ``WORKLOG_DB_URL``
only for local-dev convenience when the contributor's per-user role
also owns the schema (Supabase's local stack does this by default).

URL driver is coerced to the sync ``psycopg`` driver because alembic
itself is synchronous; the app session uses asyncpg via
services.storage.postgres.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import context
from sqlalchemy import engine_from_config, pool

from db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


_ASYNC_PREFIXES = ("postgresql+asyncpg://", "postgresql+asyncpg+")


def _coerce_sync_driver(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    for prefix in _ASYNC_PREFIXES:
        if url.startswith(prefix):
            tail = url[url.index("://") + 3 :]
            return f"postgresql+psycopg://{tail}"
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    raise RuntimeError(
        f"Unsupported alembic URL: {url!r}. Expected a postgresql URL."
    )


def _resolve_url() -> str:
    raw = (
        os.environ.get("WORKLOG_DB_SERVICE_ROLE_KEY")
        or os.environ.get("WORKLOG_DB_URL")
    )
    if not raw:
        raise RuntimeError(
            "Set WORKLOG_DB_SERVICE_ROLE_KEY (preferred) or WORKLOG_DB_URL "
            "before running alembic."
        )
    return _coerce_sync_driver(raw)


if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

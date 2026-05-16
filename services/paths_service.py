"""Filesystem-path resolver for worklog v2.

Database connection strings have moved to environment variables consumed
by services.storage.postgres (WORKLOG_DB_URL,
WORKLOG_DB_SERVICE_ROLE_KEY) — v2 is Postgres-only, there is no on-disk
SQLite file to resolve a path to. The v1 db_path() / db_url() helpers
are intentionally gone; callers should use services.storage instead.

What this module still owns:
- WORKLOG_HOME — root for runtime files (exports, daemon state,
  notifications log, embeddings cache, active-workstream pointer).
- WORKLOG_PROMPTS — prompt templates (defaults to <repo>/prompts).
- WORKLOG_CONFIG — config TOML location.
"""

from __future__ import annotations

import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """Repository root. Code-bundled assets (prompts, config, alembic) live here."""
    return _PROJECT_ROOT


def home() -> Path:
    """User-data root. Defaults to repo root for solo dev convenience."""
    env = os.getenv("WORKLOG_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _PROJECT_ROOT


def data_dir() -> Path:
    path = home() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir() -> Path:
    path = home() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def embeddings_cache_dir() -> Path:
    """Where sentence-transformers caches downloaded model weights (phase 6)."""
    path = home() / "embeddings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    env = os.getenv("WORKLOG_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / "config" / "config.toml"


def prompts_dir() -> Path:
    env = os.getenv("WORKLOG_PROMPTS")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / "prompts"


def daemon_pid_path() -> Path:
    return data_dir() / "worklog-daemon.pid"


def daemon_heartbeat_path() -> Path:
    return data_dir() / "worklog-daemon.heartbeat"


def daemon_last_job_path() -> Path:
    return data_dir() / "worklog-daemon.last_job"


def notifications_log_path() -> Path:
    return data_dir() / "notifications.log"


def active_workstream_path() -> Path:
    """Local pointer to the user's currently active workstream.

    Per-user UI state, not authoritative — Postgres holds the workstream
    rows. This file simply remembers which row to default to on `wl add`.
    """
    return data_dir() / ".active_workstream"

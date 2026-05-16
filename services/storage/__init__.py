"""Storage backend for worklog v2 (Postgres + pgvector via Supabase)."""

from services.storage.postgres import get_session, init_engine, shutdown_engine

__all__ = ["get_session", "init_engine", "shutdown_engine"]

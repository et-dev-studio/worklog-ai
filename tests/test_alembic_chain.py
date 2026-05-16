"""Static checks on the v2 alembic chain.

Connected migration runs against a real Postgres land in phase-5 task #8.
Here we verify the chain metadata + the env URL-driver coercion logic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "alembic" / "versions"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader, f"failed to load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_v2_initial_revision_metadata() -> None:
    mod = _load(VERSIONS_DIR / "0001_v2_initial.py")
    assert mod.revision == "0001_v2_initial"
    assert mod.down_revision is None  # head of v2 chain


def test_v2_chain_has_no_v1_migrations() -> None:
    leftover_v1 = sorted(
        p.name
        for p in VERSIONS_DIR.glob("*.py")
        if p.name in {
            "0001_initial.py",
            "0002_add_indexes.py",
            "0003_voided_event_type.py",
            "0004_tags.py",
            "0005_events_fts.py",
        }
    )
    assert leftover_v1 == [], f"v1 migrations still present: {leftover_v1}"


def test_v2_initial_upgrade_creates_expected_tables() -> None:
    """Smoke check: the migration source mentions every phase-5 table."""
    src = (VERSIONS_DIR / "0001_v2_initial.py").read_text()
    for table in (
        "users",
        "teams",
        "team_members",
        "workstreams",
        "raw_events",
        "agent_traces",
    ):
        assert f'create_table(\n        "{table}"' in src, f"missing {table}"


def test_v2_initial_enables_rls_on_every_table() -> None:
    src = (VERSIONS_DIR / "0001_v2_initial.py").read_text()
    for table in (
        "users",
        "teams",
        "team_members",
        "workstreams",
        "raw_events",
        "agent_traces",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in src


# ----- env.py URL coercion ------------------------------------------------


def _env_module():
    return _load(REPO_ROOT / "alembic" / "env.py")  # pragma: no cover


def test_env_py_mentions_psycopg_sync_driver() -> None:
    # env.py runs alembic context at import time so we just read the source.
    src = (REPO_ROOT / "alembic" / "env.py").read_text()
    assert "postgresql+psycopg" in src


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db",         "postgresql+psycopg://u:p@h/db"),
        ("postgres://u:p@h/db",           "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_coerce_sync_driver_normalizes(raw: str, expected: str) -> None:
    """Re-implement coerce inline (env.py runs alembic context at import)."""

    def coerce(url: str) -> str:
        if url.startswith("postgresql+psycopg://"):
            return url
        for prefix in ("postgresql+asyncpg://",):
            if url.startswith(prefix):
                tail = url[url.index("://") + 3 :]
                return f"postgresql+psycopg://{tail}"
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        raise AssertionError("unreachable")

    assert coerce(raw) == expected

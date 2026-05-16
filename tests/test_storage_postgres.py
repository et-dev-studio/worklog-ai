"""Pure-function unit tests for services.storage.postgres.

Async / real-Postgres paths land in phase-5 task #8 (fixtures + RLS).
"""

from __future__ import annotations

import pytest

from services.storage import postgres


def test_normalize_db_url_keeps_asyncpg_prefix() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/db"
    assert postgres._normalize_db_url(url) == url


def test_normalize_db_url_upgrades_postgresql_scheme() -> None:
    assert (
        postgres._normalize_db_url("postgresql://u:p@h:5432/db")
        == "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_normalize_db_url_upgrades_postgres_scheme() -> None:
    assert (
        postgres._normalize_db_url("postgres://u:p@h:5432/db")
        == "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_normalize_db_url_rejects_other_scheme() -> None:
    with pytest.raises(postgres.StorageMisconfigured):
        postgres._normalize_db_url("sqlite:///wl.db")


def test_resolve_db_url_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKLOG_DB_URL", raising=False)
    with pytest.raises(postgres.StorageMisconfigured):
        postgres._resolve_db_url()


def test_resolve_db_url_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKLOG_DB_URL", "postgres://u:p@h:5432/db")
    assert postgres._resolve_db_url() == "postgresql+asyncpg://u:p@h:5432/db"


def test_pool_size_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKLOG_DB_POOL_SIZE", raising=False)
    assert postgres._pool_size() == 5
    monkeypatch.setenv("WORKLOG_DB_POOL_SIZE", "20")
    assert postgres._pool_size() == 20


def test_pool_size_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKLOG_DB_POOL_SIZE", "twenty")
    with pytest.raises(postgres.StorageMisconfigured):
        postgres._pool_size()


@pytest.mark.parametrize(
    "role",
    ["wl_user", "User1", "_admin", "u123"],
)
def test_validate_role_accepts_identifiers(role: str) -> None:
    assert postgres._validate_role(role) == role


@pytest.mark.parametrize(
    "role",
    [
        "wl-user",          # hyphen
        "1user",            # leading digit
        "user; DROP TABLE", # injection attempt
        "",                 # empty
        "user space",       # whitespace
    ],
)
def test_validate_role_rejects_non_identifiers(role: str) -> None:
    with pytest.raises(postgres.StorageMisconfigured):
        postgres._validate_role(role)

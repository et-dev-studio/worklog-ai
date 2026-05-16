"""Phase-5 skeleton tests.

This file keeps the test suite non-empty while phase 5 rewrites
the v1 SQLite stack as v2 Postgres. Each new v2 module ships
with real tests that replace these imports-only checks.

v1 tests are preserved under _legacy_tests_v1/ for reference and
on the `v1` branch / `v1-final` tag.
"""

from __future__ import annotations


def test_services_package_loads_dotenv_on_import() -> None:
    """services/__init__.py is the canonical .env load site."""
    import importlib
    import sys

    # Reimport so the module-level load_dotenv call re-runs cleanly.
    for name in [m for m in sys.modules if m == "services" or m.startswith("services.")]:
        del sys.modules[name]
    services = importlib.import_module("services")
    # The module body imports load_dotenv at module scope, which means
    # python-dotenv must be installed (no try/except guard).
    assert hasattr(services, "load_dotenv")


def test_storage_package_exports_session_helpers() -> None:
    from services.storage import postgres

    assert hasattr(postgres, "get_session")
    assert hasattr(postgres, "init_engine")
    assert hasattr(postgres, "shutdown_engine")


def test_v2_models_register_expected_tables() -> None:
    from db.models import Base

    expected = {
        "users",
        "teams",
        "team_members",
        "workstreams",
        "raw_events",
        "agent_traces",
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    assert not missing, f"missing v2 tables in Base.metadata: {missing}"


def test_workstream_title_is_immutable_after_set() -> None:
    """v1 invariant ported (db/models.py:100)."""
    import pytest

    from db.models import Workstream

    ws = Workstream(title="AUTH-1 oauth")
    # First set during construction is permitted.
    assert ws.title == "AUTH-1 oauth"
    with pytest.raises(PermissionError):
        ws.title = "renamed"


def test_workstream_status_check_constraint_values() -> None:
    from db.models import Workstream

    constraints = {c.name for c in Workstream.__table__.constraints if c.name}
    assert "ck_workstreams_status" in constraints
    assert "ck_workstreams_visibility" in constraints


def test_raw_event_kind_enum_covers_v1_and_v2_kinds() -> None:
    from db.models import RawEventKind

    v1_kinds = {
        "capture",
        "reflection",
        "event_connected",
        "status_update",
        "summary_generated",
        "voided",
    }
    v2_new = {
        "git_commit",
        "slack_message",
        "meeting_segment",
        "terminal_history",
        "github_event",
    }
    values = {k.value for k in RawEventKind}
    assert v1_kinds <= values
    assert v2_new <= values

"""Phase-5 skeleton tests.

This file keeps the test suite non-empty while phase 5 rewrites
the v1 SQLite stack as v2 Postgres. Each new v2 module ships
with real tests that replace these imports-only checks.

v1 tests are preserved under _legacy_tests_v1/ for reference and
on the `v1` branch / `v1-final` tag.
"""

from __future__ import annotations


def test_storage_package_exports_session_helpers() -> None:
    from services.storage import postgres

    assert hasattr(postgres, "get_session")
    assert hasattr(postgres, "init_engine")
    assert hasattr(postgres, "shutdown_engine")

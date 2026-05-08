from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._fake_inference_server import FakeInferenceServer

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_server():
    srv = FakeInferenceServer()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def _run_cli(
    args: list[str],
    home: Path,
    inference_url: str | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["WORKLOG_HOME"] = str(home)
    if inference_url is not None:
        env["WORKLOG_INFERENCE_URL"] = inference_url
    else:
        env.pop("WORKLOG_INFERENCE_URL", None)
    env.pop("BITNET_CMD", None)
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_e2e_capture_summary_export_with_fake_inference(tmp_path: Path, fake_server) -> None:
    home = tmp_path / "wl"
    init = _run_cli(["init-db"], home)
    assert init.returncode == 0, init.stderr

    create = _run_cli(["workstream", "create", "PLATFORM-1 redis pool"], home)
    assert create.returncode == 0, create.stderr

    add = _run_cli(["add", "investigating redis pool drops", "--yes"], home)
    assert add.returncode == 0, add.stderr
    assert "Captured event:" in add.stdout

    summary = _run_cli(
        ["summary", "--yes", "--export-format", "markdown"],
        home,
        inference_url=fake_server.base_url,
    )
    assert summary.returncode == 0, summary.stderr
    assert "summary line one" in summary.stdout

    export_dir = home / "exports"
    md_files = list(export_dir.rglob("*.md"))
    assert md_files, "expected markdown export file"
    contents = md_files[0].read_text(encoding="utf-8")
    assert "summary line one" in contents


def test_summary_hard_fails_when_inference_unreachable(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    init = _run_cli(["init-db"], home)
    assert init.returncode == 0
    # Point at a port nothing's listening on.
    result = _run_cli(
        ["summary", "--yes"],
        home,
        inference_url="http://127.0.0.1:1/v1",
    )
    assert result.returncode == 3, result.stdout
    assert "inference unavailable" in result.stderr.lower()


def test_non_tty_add_does_not_hang(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _run_cli(["init-db"], home)
    result = _run_cli(["add", "piped capture"], home, stdin="")
    assert result.returncode == 0, result.stderr
    assert "Captured event:" in result.stdout


def test_reflect_requires_tty(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _run_cli(["init-db"], home)
    result = _run_cli(["reflect"], home, stdin="")
    assert result.returncode == 2
    assert "interactive terminal" in result.stderr

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_fake_bitnet(tmp_path: Path) -> Path:
    script = tmp_path / "fake_bitnet.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--help" ]]; then\n'
        '  echo "fake bitnet"\n'
        "  exit 0\n"
        "fi\n"
        "INPUT=$(cat)\n"
        'echo "- summary line one"\n'
        'echo "- summary line two"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _run_cli(args: list[str], home: Path, bitnet: Path | None, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["WORKLOG_HOME"] = str(home)
    if bitnet is not None:
        env["BITNET_CMD"] = str(bitnet)
    else:
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


def test_e2e_capture_summary_export_with_fake_inference(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    bitnet = _make_fake_bitnet(tmp_path)

    init = _run_cli(["init-db"], home, bitnet=None)
    assert init.returncode == 0, init.stderr

    create = _run_cli(["workstream", "create", "PLATFORM-1 redis pool"], home, bitnet=None)
    assert create.returncode == 0, create.stderr

    add = _run_cli(["add", "investigating redis pool drops", "--yes"], home, bitnet=None)
    assert add.returncode == 0, add.stderr
    assert "Captured event:" in add.stdout

    summary = _run_cli(["summary", "--yes", "--export-format", "markdown"], home, bitnet=bitnet)
    assert summary.returncode == 0, summary.stderr
    assert "summary line one" in summary.stdout

    export_dir = home / "exports"
    md_files = list(export_dir.rglob("*.md"))
    assert md_files, "expected markdown export file"
    contents = md_files[0].read_text(encoding="utf-8")
    assert "summary line one" in contents


def test_summary_hard_fails_when_bitnet_unset(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    init = _run_cli(["init-db"], home, bitnet=None)
    assert init.returncode == 0
    result = _run_cli(["summary", "--yes"], home, bitnet=None)
    assert result.returncode == 3, result.stdout
    assert "BITNET_CMD not set" in result.stderr


def test_non_tty_add_does_not_hang(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _run_cli(["init-db"], home, bitnet=None)
    result = _run_cli(["add", "piped capture"], home, bitnet=None, stdin="")
    assert result.returncode == 0, result.stderr
    assert "Captured event:" in result.stdout


def test_reflect_requires_tty(tmp_path: Path) -> None:
    home = tmp_path / "wl"
    _run_cli(["init-db"], home, bitnet=None)
    result = _run_cli(["reflect"], home, bitnet=None, stdin="")
    assert result.returncode == 2
    assert "interactive terminal" in result.stderr

from pathlib import Path

from typer.testing import CliRunner

from cli.main import app
from services.daemon_service import DaemonService


runner = CliRunner()


def test_connect_no_data_flow() -> None:
    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 0
    assert "No events available." in result.stdout


def test_daemon_status_stopped_with_temp_files(tmp_path) -> None:
    pid = tmp_path / "daemon.pid"
    hb = tmp_path / "daemon.hb"
    service = DaemonService(str(pid), str(hb))
    assert service.status() == "stopped"
    service.heartbeat()
    assert hb.exists()

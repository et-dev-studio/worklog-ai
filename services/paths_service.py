from __future__ import annotations

import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_root() -> Path:
    return _PROJECT_ROOT


def home() -> Path:
    env = os.getenv("WORKLOG_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _PROJECT_ROOT


def data_dir() -> Path:
    path = home() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "worklog.db"


def db_url() -> str:
    return f"sqlite:///{db_path()}"


def exports_dir() -> Path:
    path = home() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    env = os.getenv("WORKLOG_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return home() / "config" / "config.toml"


def prompts_dir() -> Path:
    env = os.getenv("WORKLOG_PROMPTS")
    if env:
        return Path(env).expanduser().resolve()
    return home() / "prompts"


def daemon_pid_path() -> Path:
    return data_dir() / "worklog-daemon.pid"


def daemon_heartbeat_path() -> Path:
    return data_dir() / "worklog-daemon.heartbeat"


def daemon_last_job_path() -> Path:
    return data_dir() / "worklog-daemon.last_job"


def notifications_log_path() -> Path:
    return data_dir() / "notifications.log"


def active_workstream_path() -> Path:
    return data_dir() / ".active_workstream"

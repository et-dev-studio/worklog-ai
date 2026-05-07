# Changelog

## Phase 2 — v1 Checklist Closure & Test Coverage (PR #3)

### Tests
- New `tests/test_e2e_workflow.py`: end-to-end workflow under `WORKLOG_HOME` with a fake `BITNET_CMD` shell stub; exercises capture → summary → markdown export, hard-fail on missing `BITNET_CMD`, non-TTY add, and reflect-requires-TTY paths.
- New `tests/test_daemon_lifecycle.py`: daemon start/stop/status/restart, heartbeat + last-job tracking, stale-PID cleanup, SIGKILL fallback after SIGTERM is ignored, double-start returns `already_running`.
- Expanded `tests/test_cli_daemon.py`: smoke coverage for every CLI command (init-db idempotency, workstream create/list/set-status, status, resume id validation, add explicit and non-TTY paths, connect with no data and non-TTY-without-ids, summary invalid date, notify-test fallback to log).
- Expanded `tests/test_services.py`: archived/completed workstreams excluded from suggestions, reflection priority `fixed` keyword subtracts, unresolved capture excludes already-reflected events, summary/connection audit events emit valid JSON metadata, day-boundary correctness, JSON1 metadata constraint rejects malformed JSON, export `ALL` writes both markdown and json.

### Tooling
- `scripts/pre-commit.sh` runs `pytest -q` inside the BitNet venv (`WORKLOG_VENV` overrides path); install with `ln -sf "$(pwd)/scripts/pre-commit.sh" .git/hooks/pre-commit`.

### Docs
- `docs/v1_release_checklist.md`: every item ticked with file/test references.
- `README.md`: full CLI surface documented; `--yes` semantics, `WORKLOG_HOME`, notification fallback path, hard-fail behavior, pre-commit hook install.
- `CHANGELOG.md` (this file).

## Phase 1 — Correctness Fixes (PR #2)

### Fixed
- **Enum case** (`services/event_service.py`): workstream-suggestion filter switched from `["active", "paused"]` string literals to `[WorkstreamStatus.ACTIVE, WorkstreamStatus.PAUSED]` enum refs so it matches the uppercase Alembic-DDL storage. Suggestions had been silently empty in any non-test database.
- **Title-immutability listener** (`db/models.py`): handles SQLAlchemy `NEVER_SET` / `NO_VALUE` sentinels so `Workstream(...)` construction no longer trips `PermissionError` during `__init__`.
- **Naive vs aware datetimes** (`services/event_service.py`): `_as_utc` helper normalizes DB-loaded timestamps before subtracting `datetime.now(UTC)`, eliminating `TypeError` once the enum-case fix unmasked the scoring path.
- **Day-boundary edge** (`services/summary_service.py`): exclusive `< start_of_next_day` boundary; events at `23:59:59.999999` are no longer dropped.
- **Hand-rolled JSON metadata** (`services/reflection_service.py`): replaced with `json.dumps`.

### Hardened
- **`WORKLOG_HOME` paths_service** (`services/paths_service.py` + dependents): central resolver for data dir, db url, exports, prompts, config, daemon files. Prompts and Alembic anchored via `__file__` so the daemon subprocess survives any `cwd`.
- **TTY guards + `--yes`/`-y` flags** (`cli/prompts.py`, `cli/main.py`): `add`, `connect`, `reflect`, `summary` no longer hang on `questionary.ask()` in pipes/cron/CI.
- **Robust notifications** (`services/notification_service.py`): detects `notify-send` via `shutil.which`, falls back to `data/notifications.log` + stderr; `daemon-start` logs WARN if missing; new `notify-test` command.
- **Inference hard-fail** (`services/inference_service.py`): `InferenceUnavailable` exception replaces placeholder strings. `reflect` and `summary` run `healthcheck()` first, exit code 3 with actionable error when `BITNET_CMD` is unset/unhealthy. No more fake summaries persisted as real data. Inference subprocess now also kills on timeout.
- **`init-db` runs Alembic** (`cli/main.py`): replaced `Base.metadata.create_all` with `alembic upgrade head` to eliminate ORM/migration drift.
- **Daemon supervision** (`services/daemon_service.py`): `stop()` polls SIGTERM exit then escalates to SIGKILL after 5 s; `scheduler-run` installs SIGTERM/SIGINT handlers and shuts APScheduler cleanly; subprocess `cwd=project_root` + env passthrough.
- **Job-health-aware status**: scheduler writes `last_job` per successful run; `status()` flags `degraded` when `last_job` exceeds 2× the largest configured interval, even if process heartbeat is fresh.
- **Differentiated reminders** (`services/scheduler_service.py`): lunch lists top 3 unresolved item titles; evening shows count + summary hint.
- **`resume` validates workstream id** before writing `data/.active_workstream`.
- **PromptService**: friendly error when a template file is missing.

### Schema
- New migration `alembic/versions/0002_add_indexes.py`: `ix_events_timestamp`, `ix_events_workstream_type`, `ix_events_type`, `ix_reflections_event_id`.

### Tests
- `conftest.py` at repo root puts the project on `sys.path` for pytest.
- New regression `test_suggestions_match_real_db_enum_storage` exercises the active/paused filter against real enum DDL storage.

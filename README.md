# worklog-ai

Local-first engineering work memory system. Capture work events as you go, run AI-driven reflection on unresolved items, and generate human-reviewed daily standup summaries — all from the terminal, all on your own machine.

## Quick start

```bash
pip install -r requirements.txt
python -m cli.main init-db
python -m cli.main workstream create "AUTH-1421 OAuth redirect fixes"
python -m cli.main resume <WORKSTREAM_ID>
python -m cli.main add "Investigating Redis timeout"
python -m cli.main reflect
python -m cli.main summary
python -m cli.main status
python -m cli.main daemon-start
```

## CLI commands

- `init-db` — initialize the SQLite schema by running Alembic migrations to head
- `add <text> [--workstream-id ID] [--yes/-y]` — capture an event; prompts for workstream attach unless `--yes`/non-TTY
- `connect [--event-id ID] [--workstream-id ID] [--yes/-y]` — link an event to a workstream and emit an audit event; in non-interactive mode both ids must be passed explicitly
- `reflect` — run an interactive reflection session over the top unresolved capture events (TTY required)
- `summary [--day YYYY-MM-DD] [--export-format markdown|json|terminal|all] [--yes/-y]` — generate, review, edit, and export the daily summary; defaults to today
- `resume <WORKSTREAM_ID>` — set the active workstream context for future captures (id is validated)
- `status` — show active workstreams and pending reflections
- `daemon-start | daemon-stop | daemon-restart | daemon-status` — manage the background scheduler daemon
- `notify-test [MESSAGE]` — fire a notification through the same path used by reminders, useful for verifying delivery
- `workstream create <TITLE> [--summary TEXT]` — create a workstream (titles are immutable after creation)
- `workstream list` — list all workstreams
- `workstream set-status <WORKSTREAM_ID> <active|paused|completed|archived>` — change status, emits a `STATUS_UPDATE` audit event

### Non-interactive use

`add`, `connect`, and `summary` accept `--yes`/`-y` and detect non-TTY stdin so they exit cleanly when run from cron, pipes, or CI. `reflect` requires a TTY by design and exits with code 2 otherwise.

## Config

Scheduler reminder times are configurable in `config/config.toml`. Example:

```toml
[lunch]
time = "13:00"

[evening]
time = "18:00"
```

The daemon supervises these jobs and exposes job-health-aware status: `daemon-status` reports `degraded` if either the heartbeat is stale or no scheduled job has run within `2× max(interval)`.

### Notifications

Reminders are dispatched via `notify-send`. When the binary isn't available (e.g. WSL2, headless servers, containers), Worklog falls back to:

1. Appending the message to `<WORKLOG_HOME>/data/notifications.log`
2. Printing to stderr if attached to a TTY

`daemon-start` prints a `WARN` once if `notify-send` is missing so you know reminders are using the fallback path.

## Storage layout

By default, all runtime data lives in `<repo_root>/data/`. Set `WORKLOG_HOME` to relocate everything (database, exports, daemon state, notification log, active-workstream pointer):

```bash
export WORKLOG_HOME="$HOME/.local/share/worklog-ai"
```

Code-bundled assets (`prompts/`, `alembic/`, `config/`) stay anchored to the repository regardless of `WORKLOG_HOME`.

## Inference

Worklog drives reflection and summarization through a local inference binary. Set `BITNET_CMD` to point at it:

```bash
export BITNET_CMD="/path/to/bitnet_infer"
```

The binary must accept the prompt on stdin and emit the response on stdout. `BITNET_CMD --help` is used as a healthcheck.

If `BITNET_CMD` is unset or the healthcheck fails, `reflect` and `summary` exit with code 3 and an actionable error; no placeholder text is ever persisted as a real summary.

## Development

```bash
source ~/BitNet/BitEnv/bin/activate   # project venv
pip install -r requirements.txt
pytest -q                              # all tests
alembic upgrade head                   # apply migrations against the configured DB
alembic revision --autogenerate -m "description"
```

### Pre-commit hook

A pytest-driven pre-commit hook lives at `scripts/pre-commit.sh`. To install it:

```bash
ln -sf "$(pwd)/scripts/pre-commit.sh" .git/hooks/pre-commit
```

The hook activates the BitNet venv (override via `WORKLOG_VENV`) and aborts the commit on test failure. Use `git commit --no-verify` only when intentionally bypassing.

## Architecture

See `CLAUDE.md` for layered architecture, data flows, and operational constraints.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Always activate venv before running any Python command in this project:

```bash
source ~/BitNet/BitEnv/bin/activate
```

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize the SQLite database
python -m cli.main init-db

# Run tests
pytest
pytest tests/test_services.py::test_event_capture  # single test

# Run database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Start/stop the background daemon
python -m cli.main daemon-start
python -m cli.main daemon-stop
```

## Architecture

**worklog-ai** is a local-first, event-sourced work memory tool for engineers. It captures work events into an immutable log, then uses AI-driven reflection and summarization to produce daily summaries.

### Layers

**`db/`** — SQLAlchemy models and session factory (SQLite at `data/worklog.db`).
- Four core tables: `workstreams`, `events`, `reflections`, `summaries`
- All user actions produce append-only `events` with a typed `EventType` enum (CAPTURE, REFLECTION, EVENT_CONNECTED, STATUS_UPDATE, SUMMARY_GENERATED)
- Workstream titles are immutable after creation — enforced via a SQLAlchemy event listener in `models.py`. This listener is global, so it fires during migrations too; be careful when writing repair scripts.

**`services/`** — All business logic. Each file is a single-concern module.
- `event_service.py`: Captures events; scores workstream suggestions via a weighted continuity algorithm (keyword overlap, recency, prior attachments, semantic similarity).
- `reflection_service.py`: Scores unresolved events by priority (short text, no workstream, investigative keywords) and surfaces the top N for interactive Q&A.
- `inference_service.py`: Subprocess wrapper for a local BitNet binary. If `BITNET_CMD` env var is unset, all inference returns placeholder strings — safe for dev/test.
- `daemon_service.py`: PID-file + heartbeat lifecycle (`data/worklog-daemon.pid`, `data/worklog-daemon.heartbeat`). Health is measured by heartbeat age (< 180s = healthy).
- `scheduler_service.py`: APScheduler cron jobs; times come from `config/config.toml`.

**`agents/`** — Thin wrappers that combine `inference_service` + `prompt_service`.
- `reflection_agent.py`: Produces exactly 3 reflection questions for an event.
- `summary_agent.py`: Multi-step pipeline — group → categorize → summarize.

**`cli/main.py`** — Typer app. All user-facing commands live here. Commands that need interactive input (Questionary prompts) require a real TTY; they will fail in non-interactive environments (see known issues).

**`prompts/`** — Plain-text templates loaded by `prompt_service.py`. Edit these to change AI behavior without touching Python.

**`alembic/`** — Migrations. The ORM and migration schema can drift if new enum values are added to Python without a corresponding migration — always generate a migration alongside model changes.

### Key data flows

1. `add "<text>"` → `event_service.capture_event()` → scores active workstreams → inserts event row → optionally links to a workstream.
2. `reflect` → `reflection_service.get_pending()` → sorts by priority score → `reflection_agent.generate_questions()` → interactive Q&A → inserts reflection rows.
3. `summary` → `summary_agent.run()` (group → categorize → summarize via inference) → human approval/edit → `export_service` writes markdown/JSON.
4. Daemon: `scheduler_service` fires at times in `config.toml` → sends `notify-send` desktop notification → user runs `reflect` or `summary` manually.

### Environment variables

| Variable | Purpose |
|---|---|
| `BITNET_CMD` | Path to local inference binary. Unset = placeholder mode (no AI output). |

### Known architectural constraints (from QA findings in `docs/v1_breaking_features.md`)

- CLI assumes TTY; piping or automation (e.g., cron calling `reflect`) will hang or crash.
- Title immutability listener blocks admin/migration repairs; disable it explicitly when writing data-fix scripts.
- Enum schema drift risk: always create an Alembic migration when adding `EventType` or `WorkstreamStatus` values.
- Inference has no retry logic; a crashed `BITNET_CMD` subprocess surfaces as an empty string, not an exception.
- Daemon health is heartbeat-based, not job-health-based — a scheduler that silently stops firing won't be flagged as degraded.

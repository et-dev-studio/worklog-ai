# worklog-ai

Local-first engineering work memory. Capture work events from the terminal, run AI-driven reflection on unresolved items, generate human-reviewed daily standup summaries, and search the whole log with FTS — all on your machine, all from the shell.

## Install

```bash
pip install -e .[dev]            # editable install; exposes `wl` and `worklog`
wl init-db                       # runs Alembic upgrade head
wl --install-completion bash     # bash | zsh | fish | powershell
```

## Quick start

```bash
wl workstream create "AUTH-1421 OAuth redirect fixes"
wl resume <WORKSTREAM_ID>
wl add "Investigating Redis timeout" --tag bug
wl event search redis
wl reflect
wl summary --day today
wl daemon-start
wl daemon-dashboard --watch 5
```

`wl` and `python -m cli.main` are equivalent. `wl a` is a hidden alias of `wl add`.

## CLI surface

### Capture and review

- `wl add <text|-> [--workstream-id ID] [--tag T ...] [--yes/-y] [--quiet/-q]` — capture an event. `-` reads from stdin. `--quiet`/`--yes` skips prompts; in non-TTY shells the active workstream pointer is used. `--tag` may be passed multiple times.
- `wl undo [EVENT_ID] [--reason TEXT]` — append a `VOIDED` audit event referencing the original (defaults to the most recent capture). Voided events are filtered from `event list` / `event search` / `summary` by default; pass `--type capture --type voided` to see both.
- `wl resume <WORKSTREAM_ID>` — set the active workstream pointer (id is validated).
- `wl status [--json]` — active workstreams + pending reflection count.

### Browse the log

- `wl event list [--day today|yesterday|-3d|this-week|last-week|YYYY-MM-DD] [--ws ID] [--tag NAME] [--type T ...] [--limit N] [--json]`
- `wl event show <ID> [--json]` — single event with attached tags and metadata.
- `wl event search <QUERY> [--limit N] [--json]` — FTS5 search across event content.
- `wl event tag <ID> <TAG> [TAG ...]` — attach one or more tags.

### Tags

- `wl tag list [--json]` — every tag with its event count.
- `wl tag show <NAME> [--json]` — events carrying the tag.

### Workstreams

- `wl workstream create <TITLE> [--summary TEXT]` — create (titles are immutable after creation).
- `wl workstream list [--json]`
- `wl workstream set-status <WORKSTREAM_ID> <active|paused|completed|archived>` — emits a `STATUS_UPDATE` audit event.

### Reflection / summary / connect

- `wl reflect` — interactive Q&A over top unresolved capture events. TTY required (exit 2 otherwise). Healthcheck runs first; unreachable inference server → exit 3.
- `wl summary [--day SPEC] [--export-format markdown|json|terminal|all] [--yes/-y]` — generate, review, edit, export.
- `wl connect [--event-id ID] [--workstream-id ID] [--yes/-y]` — link an event to a workstream + emit `event_connected` audit event. Non-interactive mode requires both ids.

### Daemon

- `wl daemon-start | daemon-stop | daemon-restart | daemon-status [--json]`
- `wl daemon-dashboard [--watch N] [--json]` — schedules with next-run timestamps, pending reflections, last 10 events.
- `wl notify-test [MESSAGE]` — verify the notification path.

### Diagnostics

- `wl doctor [--json]` — DB path, Alembic head, BITNET healthcheck, daemon status + last-job age, notify-send availability, prompts dir, missing prompts, active workstream, completion install hint.

## JSON output

Read commands accept `--json` for machine-readable output: `status`, `workstream list`, `event list/show/search`, `tag list/show`, `daemon-status`, `daemon-dashboard`, `doctor`. Output is JSON (objects or arrays) on stdout — nothing else — so it pipes cleanly into `jq`.

## Non-interactive use

`add`, `connect`, and `summary` accept `--yes`/`-y` and detect non-TTY stdin so they exit cleanly when run from cron, pipes, or CI. `reflect` requires a TTY by design and exits with code 2 otherwise.

```bash
echo "ad-hoc note" | wl add - --quiet
wl summary --yes --day yesterday > /tmp/standup.md
```

## Config

Scheduler reminder times in `config/config.toml`:

```toml
[lunch]
time = "13:00"

[evening]
time = "18:00"
```

The daemon supervises these jobs. `daemon-status` reports `degraded` when either the heartbeat is stale or no scheduled job has run within `2× max(interval)`.

### Notifications

Reminders are dispatched via `notify-send`. When the binary isn't available (e.g. WSL2, headless servers, containers), Worklog falls back to:

1. Appending the message to `<WORKLOG_HOME>/data/notifications.log`
2. Printing to stderr if attached to a TTY

`daemon-start` prints a `WARN` once if `notify-send` is missing so you know reminders are using the fallback path.

## Storage layout

By default, all runtime data lives under `<repo_root>/data/`. Set `WORKLOG_HOME` to relocate everything (database, exports, daemon state, notifications log, active-workstream pointer):

```bash
export WORKLOG_HOME="$HOME/.local/share/worklog-ai"
```

Code-bundled assets (`prompts/`, `alembic/`, `config/`) stay anchored to the repository regardless of `WORKLOG_HOME`.

## Inference

Worklog talks to an OpenAI-compatible Chat Completions HTTP server (`/v1/chat/completions` + `/v1/models`). The agents send structured `{role, content}` messages and let the server apply the model's chat template. Worklog is backend-agnostic — anything speaking the OpenAI API works: `llama-server` (recommended for BitNet), ollama, vLLM, OpenAI, or Anthropic via LiteLLM.

### Local default — `llama-server` + BitNet

```bash
# In one terminal, leave it running:
./scripts/start-inference.sh

# In your shell rc:
export WORKLOG_INFERENCE_URL="http://127.0.0.1:8080/v1"
```

`scripts/start-inference.sh` wraps `llama-server` with sensible defaults; tune via env vars (`BITNET_DIR`, `BITNET_MODEL`, `INFERENCE_HOST`, `INFERENCE_PORT`, `INFERENCE_CTX`, `INFERENCE_THREADS`, `INFERENCE_API_KEY`). For a long-lived setup, register the script as a systemd-user unit — see [`docs/inference-systemd.md`](docs/inference-systemd.md).

### Configuration

| Variable | Purpose |
|---|---|
| `WORKLOG_INFERENCE_URL` | Base URL ending in `/v1`. Default `http://127.0.0.1:8080/v1`. |
| `WORKLOG_INFERENCE_MODEL` | Model id to send. Auto-detected from `GET /v1/models` if unset. |
| `WORKLOG_INFERENCE_API_KEY` | Optional bearer token, sent as `Authorization: Bearer ...`. |
| `WORKLOG_INFERENCE_TIMEOUT` | Per-call timeout in seconds (default 120). |

### Switching backends

```bash
# Ollama
export WORKLOG_INFERENCE_URL="http://127.0.0.1:11434/v1"
export WORKLOG_INFERENCE_MODEL="llama3.1"

# OpenAI
export WORKLOG_INFERENCE_URL="https://api.openai.com/v1"
export WORKLOG_INFERENCE_MODEL="gpt-4o-mini"
export WORKLOG_INFERENCE_API_KEY="sk-..."
```

If the server is unreachable, returns a non-2xx, or yields an empty completion, `reflect` and `summary` exit with code 3 and an actionable error. No placeholder text is ever persisted as a real summary.

## Development

```bash
source ~/BitNet/BitEnv/bin/activate   # project venv
pip install -e .[dev]
pytest -q                              # all tests
alembic upgrade head                   # apply migrations against the configured DB
alembic revision --autogenerate -m "description"
```

### Pre-commit hook

A pytest-driven pre-commit hook lives at `scripts/pre-commit.sh`. To install:

```bash
ln -sf "$(pwd)/scripts/pre-commit.sh" .git/hooks/pre-commit
```

The hook activates the BitNet venv (override via `WORKLOG_VENV`) and aborts the commit on test failure. Use `git commit --no-verify` only when intentionally bypassing.

## Architecture

See `CLAUDE.md` for layered architecture, data flows, and operational constraints.

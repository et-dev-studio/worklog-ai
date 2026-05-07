# v1 Go/No-Go Release Checklist

Status as of Phase 2 (PR #2/#3 covers UX work; checklist closed for correctness, governance, runtime, and quality gates).

## Governance
- [x] Workstream titles are immutable after creation (human-defined only). — `db/models.py` `prevent_title_mutation`
- [x] AI paths do not rename workstreams. — `services/workstream_service.py::rename` always raises `PermissionError`
- [x] Lifecycle ownership remains human-driven. — only `set_status` mutates state; emits `STATUS_UPDATE` audit event

## Event Sourcing
- [x] Capture events are append-only. — no `UPDATE` paths in `EventService`
- [x] Corrections represented as new events. — connection, reflection, status changes all create new `Event` rows
- [x] Event linkage emits `event_connected` audit event with structured metadata. — `services/event_service.py::connect_event` (regression: `tests/test_services.py::test_event_connected_emits_audit_event_with_json_metadata`)
- [x] Summary generation emits `summary_generated` audit event. — `services/summary_service.py::save_summary` (regression: `test_summary_emits_summary_generated_audit_event`)

## Reflection
- [x] Reflection priority includes: short text, no workstream, no status update, investigating/fixed keywords. — `services/reflection_service.py::reflection_priority`
- [x] `wl reflect` processes unresolved events interactively. — TTY-guarded; non-TTY exits with code 2
- [x] Reminder notifications include unresolved counts. — `services/scheduler_service.py` lunch lists top 3 + count, evening shows count + summary hint

## Continuity Matching
- [x] Matching uses keyword overlap. — `_score_workstream` keyword_overlap term
- [x] Matching uses exact technical terms. — `exact_technical_terms` term
- [x] Matching uses recent activity recency. — `recent_activity` term (14-day decay)
- [x] Matching uses prior attachment history. — `prior_attachments` term (capped at 20)
- [x] Matching uses temporal proximity and recent semantic overlap. — `semantic_similarity` + `temporal_proximity` terms over last 20 events

## Summary & Export
- [x] Human approval required (`yes/no/edit`) before summary persistence. — `cli/main.py::summary`
- [x] Grouped summary context uses workstream titles. — `services/summary_service.py::grouped_context`
- [x] Export supports markdown, terminal, and json. — `services/export_service.py` `ExportFormat`

## Runtime
- [x] Local inference path configurable via `BITNET_CMD`.
- [x] Inference healthcheck validates command availability. — wired into `reflect`/`summary`; failure surfaces exit code 3
- [x] Scheduler times are config-driven (`config.toml`).
- [x] Daemon supports start/stop/restart/status and heartbeat health. — plus `last_job` heartbeat → job-health-aware `degraded` status

## Quality Gates
- [x] Service tests pass. — `tests/test_services.py` (17 tests)
- [x] CLI smoke tests pass. — `tests/test_cli_daemon.py` (11 tests) + `tests/test_e2e_workflow.py` (4 tests)
- [x] Migration scripts match model constraints. — `init-db` runs Alembic `upgrade head`; no more `create_all` drift

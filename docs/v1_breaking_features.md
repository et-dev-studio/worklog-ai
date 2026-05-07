# Worklog AI v1 Breaking Features (QA Break Test Findings)

This document captures architecture-level and implementation-level breakpoints discovered by adversarial QA review.

Resolution status was added during the Phase 1/2 hardening work (see `CHANGELOG.md`).

## Test Environment Findings

1. **Test suite is not runnable in a clean environment without dependency bootstrap**
   - **Repro:** `python -m pytest -q`
   - **Observed:** `ModuleNotFoundError` for `typer` and `sqlalchemy`.
   - **Impact:** CI/local confidence is broken by default; regressions can slip through.
   - **Severity:** High
   - **Status:** Partially resolved — `conftest.py` injects the project on `sys.path`; tests run cleanly inside the documented `~/BitNet/BitEnv` venv. A clean-OS dependency bootstrap step belongs to Phase 3 (`pyproject.toml` + `pip install -e .`).

---

## Data & Schema Breakpoints

2. **Enum schema drift risk between ORM and migration**
   - ORM enums use lowercase runtime values (e.g., `active`, `capture`) while migration enum DDL uses uppercase labels (`ACTIVE`, `CAPTURE`).
   - **Impact:** inserts/reads can fail or drift depending on backend enum behavior and future migrations.
   - **Severity:** High
   - **Status:** ✅ Resolved (Phase 1) — `services/event_service.py:69` switched to enum refs; regression test `test_suggestions_match_real_db_enum_storage`.

3. **`json_valid()` DB constraint can break on SQLite builds lacking JSON1**
   - `events.metadata` has `CheckConstraint("metadata IS NULL OR json_valid(metadata)")`.
   - Some SQLite builds lack JSON functions.
   - **Impact:** schema creation/migration can fail at runtime on some Linux distros.
   - **Severity:** High
   - **Status:** Open. SQLite builds shipped with Python 3.12 on Linux include JSON1; flagged for ops docs. Will revisit if a non-JSON1 environment surfaces.

4. **Model-level title immutability listener is global and can break bulk sync/migrations**
   - Title set listener raises `PermissionError` on mutation everywhere.
   - **Impact:** admin repair, data migration, or import jobs cannot update bad legacy titles without bypass hacks.
   - **Severity:** Medium
   - **Status:** Mitigated (Phase 1) — listener now skips `NEVER_SET`/`NO_VALUE` so initial assignment works. Bulk-repair scripts must still disable the listener explicitly; documented in `CLAUDE.md`.

---

## Event Sourcing & Domain Logic Breakpoints

5. **Event metadata serialization is inconsistent across services**
   - Some services use `json.dumps`, others still hand-roll JSON-like strings.
   - **Impact:** malformed metadata risk, parser inconsistency, downstream automation fragility.
   - **Severity:** Medium
   - **Status:** ✅ Resolved (Phase 1) — `reflection_service` now uses `json.dumps`; covered by `test_event_connected_emits_audit_event_with_json_metadata` and `test_summary_emits_summary_generated_audit_event`.

6. **Reflection scoring uses fixed literals and brittle keyword matching**
   - Hardcoded `"investigating"`/`"fixed"`; no stemming, synonyms, or language robustness.
   - **Impact:** priority quality degrades quickly with natural user variation.
   - **Severity:** Medium
   - **Status:** Open (deferred to v1.1 — semantic matching).

7. **Continuity matching algorithm can overfit noisy token overlap**
   - Weighted lexical overlap + recency + history, no embedding/semantic disambiguation.
   - **Impact:** false-positive workstream suggestions in multi-project days.
   - **Severity:** Medium
   - **Status:** Open (deferred to v1.1 — embeddings + rerank).

---

## CLI / UX Breakpoints

8. **Interactive CLI commands can deadlock/headless-fail in non-TTY contexts**
   - `questionary` prompts in `add`, `connect`, `reflect`, `summary` assume interactive stdin.
   - **Impact:** automation scripts and cron usage break/hang.
   - **Severity:** High
   - **Status:** ✅ Resolved (Phase 1) — `cli/prompts.py` helpers, `--yes/-y` flags on `add`/`connect`/`summary`, `reflect` requires TTY by design (exit code 2). Verified by `test_non_tty_add_does_not_hang`, `test_reflect_requires_tty`, `test_connect_non_tty_requires_explicit_ids`.

9. **`scheduler-run` infinite loop has no graceful shutdown coordination**
   - Loop sleeps forever and depends on external SIGTERM; no internal stop flag or cleanup callback.
   - **Impact:** orphaned scheduler states and brittle daemon management.
   - **Severity:** Medium
   - **Status:** ✅ Resolved (Phase 1) — `scheduler-run` installs SIGTERM/SIGINT handlers, shuts APScheduler down cleanly. Verified by `test_full_daemon_lifecycle_via_cli`.

10. **Daemon health = heartbeat age, not scheduler/job health**
    - Heartbeat can update even if scheduled jobs silently fail.
    - **Impact:** false healthy status (`running`) while reminders are non-functional.
    - **Severity:** High
    - **Status:** ✅ Resolved (Phase 1) — scheduler writes `last_job` per successful run; `DaemonService.status()` returns `degraded` when `last_job` exceeds 2× max configured interval.

11. **Daemon restart may race with old process teardown**
    - `restart()` does stop+start without wait/verification of old process termination.
    - **Impact:** duplicate workers/pid confusion on slow shutdown.
    - **Severity:** Medium
    - **Status:** ✅ Resolved (Phase 1) — `stop()` polls SIGTERM exit (5 s) then escalates to SIGKILL. Verified by `test_sigkill_fallback_when_process_ignores_sigterm`.

---

## Inference Runtime Breakpoints

12. **Healthcheck assumes `BITNET_CMD --help` is valid for all local runtimes**
    - Many binaries don’t support `--help` or return non-zero by design.
    - **Impact:** false negatives even when inference would work.
    - **Severity:** Medium
    - **Status:** Open — tracked for v1.1 (configurable healthcheck contract).

13. **Inference subprocess timeout and retry policy is shallow**
    - Fixed timeout/retries, no backoff/jitter, no kill escalation path, no output size guard.
    - **Impact:** hangs, partial outputs, and hard-to-debug failures under load.
    - **Severity:** Medium
    - **Status:** Mitigated (Phase 1) — added `proc.kill()` on `asyncio.TimeoutError`. Backoff/jitter and output-size guard remain v1.1 work.

---

## Summary / Export Breakpoints

14. **Summary normalization can mangle model output semantics**
    - Non-bullet lines are forced into bullets, which may flatten headings/context.
    - **Impact:** loss of structure and standup readability regressions.
    - **Severity:** Medium
    - **Status:** Open — flagged for v1.1 prompt/agent revision.

15. **Export `ALL` mode mixes terminal side effects with file writes**
    - No transactional behavior; if one export path fails, system may partially export.
    - **Impact:** inconsistent artifact state.
    - **Severity:** Low-Medium
    - **Status:** Open — flagged for v1.1; v1 scope considered acceptable (test `test_export_all_writes_both_formats` confirms happy-path coverage).

---

## Operational & Release Breakpoints

16. **`init-db` (`create_all`) bypasses Alembic migration discipline**
    - Schema can diverge from migration history if model and Alembic get out of sync.
    - **Impact:** environment drift and upgrade fragility.
    - **Severity:** High
    - **Status:** ✅ Resolved (Phase 1) — `init-db` now invokes `alembic.command.upgrade(cfg, "head")`. Verified by `test_init_db_idempotent`.

17. **No end-to-end "real workflow" QA test covering daemon+scheduler+reflect+summary**
    - Current tests are unit/smoke level and do not validate long-running behavior.
    - **Impact:** latent integration bugs likely in production usage.
    - **Severity:** Medium
    - **Status:** ✅ Resolved (Phase 2) — `tests/test_e2e_workflow.py` exercises capture → summary → export under a fake `BITNET_CMD`; `tests/test_daemon_lifecycle.py` covers daemon start/stop/restart, SIGKILL fallback, and last-job tracking.

---

## Recommended v2 Priorities (derived from breakpoints)

1. ✅ Enforce migration-first schema lifecycle (remove/limit `create_all` in prod path).
2. ✅ Add non-interactive CLI flags and fully headless mode for all commands.
3. Replace lexical matching with semantic matching (embeddings/rerank). — v1.1
4. ✅ Harden daemon supervisor semantics (health probes, shutdown handshake, restart safety).
5. ✅ Standardize metadata as typed JSON objects across all event writers.
6. Add dependency bootstrap + CI gate so tests run in clean environment. — v1.1 (`pyproject.toml` lands in Phase 3)

# Worklog AI v1 Breaking Features (QA Break Test Findings)

This document captures architecture-level and implementation-level breakpoints discovered by adversarial QA review.

## Test Environment Findings

1. **Test suite is not runnable in a clean environment without dependency bootstrap**
   - **Repro:** `python -m pytest -q`
   - **Observed:** `ModuleNotFoundError` for `typer` and `sqlalchemy`.
   - **Impact:** CI/local confidence is broken by default; regressions can slip through.
   - **Severity:** High

---

## Data & Schema Breakpoints

2. **Enum schema drift risk between ORM and migration**
   - ORM enums use lowercase runtime values (e.g., `active`, `capture`) while migration enum DDL uses uppercase labels (`ACTIVE`, `CAPTURE`).
   - **Impact:** inserts/reads can fail or drift depending on backend enum behavior and future migrations.
   - **Severity:** High

3. **`json_valid()` DB constraint can break on SQLite builds lacking JSON1**
   - `events.metadata` has `CheckConstraint("metadata IS NULL OR json_valid(metadata)")`.
   - Some SQLite builds lack JSON functions.
   - **Impact:** schema creation/migration can fail at runtime on some Linux distros.
   - **Severity:** High

4. **Model-level title immutability listener is global and can break bulk sync/migrations**
   - Title set listener raises `PermissionError` on mutation everywhere.
   - **Impact:** admin repair, data migration, or import jobs cannot update bad legacy titles without bypass hacks.
   - **Severity:** Medium

---

## Event Sourcing & Domain Logic Breakpoints

5. **Event metadata serialization is inconsistent across services**
   - Some services use `json.dumps`, others still hand-roll JSON-like strings.
   - **Impact:** malformed metadata risk, parser inconsistency, downstream automation fragility.
   - **Severity:** Medium

6. **Reflection scoring uses fixed literals and brittle keyword matching**
   - Hardcoded `"investigating"`/`"fixed"`; no stemming, synonyms, or language robustness.
   - **Impact:** priority quality degrades quickly with natural user variation.
   - **Severity:** Medium

7. **Continuity matching algorithm can overfit noisy token overlap**
   - Weighted lexical overlap + recency + history, no embedding/semantic disambiguation.
   - **Impact:** false-positive workstream suggestions in multi-project days.
   - **Severity:** Medium

---

## CLI / UX Breakpoints

8. **Interactive CLI commands can deadlock/headless-fail in non-TTY contexts**
   - `questionary` prompts in `add`, `connect`, `reflect`, `summary` assume interactive stdin.
   - **Impact:** automation scripts and cron usage break/hang.
   - **Severity:** High

9. **`scheduler-run` infinite loop has no graceful shutdown coordination**
   - Loop sleeps forever and depends on external SIGTERM; no internal stop flag or cleanup callback.
   - **Impact:** orphaned scheduler states and brittle daemon management.
   - **Severity:** Medium

10. **Daemon health = heartbeat age, not scheduler/job health**
   - Heartbeat can update even if scheduled jobs silently fail.
   - **Impact:** false healthy status (`running`) while reminders are non-functional.
   - **Severity:** High

11. **Daemon restart may race with old process teardown**
   - `restart()` does stop+start without wait/verification of old process termination.
   - **Impact:** duplicate workers/pid confusion on slow shutdown.
   - **Severity:** Medium

---

## Inference Runtime Breakpoints

12. **Healthcheck assumes `BITNET_CMD --help` is valid for all local runtimes**
   - Many binaries don’t support `--help` or return non-zero by design.
   - **Impact:** false negatives even when inference would work.
   - **Severity:** Medium

13. **Inference subprocess timeout and retry policy is shallow**
   - Fixed timeout/retries, no backoff/jitter, no kill escalation path, no output size guard.
   - **Impact:** hangs, partial outputs, and hard-to-debug failures under load.
   - **Severity:** Medium

---

## Summary / Export Breakpoints

14. **Summary normalization can mangle model output semantics**
   - Non-bullet lines are forced into bullets, which may flatten headings/context.
   - **Impact:** loss of structure and standup readability regressions.
   - **Severity:** Medium

15. **Export `ALL` mode mixes terminal side effects with file writes**
   - No transactional behavior; if one export path fails, system may partially export.
   - **Impact:** inconsistent artifact state.
   - **Severity:** Low-Medium

---

## Operational & Release Breakpoints

16. **`init-db` (`create_all`) bypasses Alembic migration discipline**
   - Schema can diverge from migration history if model and Alembic get out of sync.
   - **Impact:** environment drift and upgrade fragility.
   - **Severity:** High

17. **No end-to-end “real workflow” QA test covering daemon+scheduler+reflect+summary**
   - Current tests are unit/smoke level and do not validate long-running behavior.
   - **Impact:** latent integration bugs likely in production usage.
   - **Severity:** Medium

---

## Recommended v2 Priorities (derived from breakpoints)

1. Enforce migration-first schema lifecycle (remove/limit `create_all` in prod path).
2. Add non-interactive CLI flags and fully headless mode for all commands.
3. Replace lexical matching with semantic matching (embeddings/rerank).
4. Harden daemon supervisor semantics (health probes, shutdown handshake, restart safety).
5. Standardize metadata as typed JSON objects across all event writers.
6. Add dependency bootstrap + CI gate so tests run in clean environment.

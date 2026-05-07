# v1 Go/No-Go Release Checklist

## Governance
- [ ] Workstream titles are immutable after creation (human-defined only).
- [ ] AI paths do not rename workstreams.
- [ ] Lifecycle ownership remains human-driven.

## Event Sourcing
- [ ] Capture events are append-only.
- [ ] Corrections represented as new events.
- [ ] Event linkage emits `event_connected` audit event with structured metadata.
- [ ] Summary generation emits `summary_generated` audit event.

## Reflection
- [ ] Reflection priority includes: short text, no workstream, no status update, investigating/fixed keywords.
- [ ] `wl reflect` processes unresolved events interactively.
- [ ] Reminder notifications include unresolved counts.

## Continuity Matching
- [ ] Matching uses keyword overlap.
- [ ] Matching uses exact technical terms.
- [ ] Matching uses recent activity recency.
- [ ] Matching uses prior attachment history.
- [ ] Matching uses temporal proximity and recent semantic overlap.

## Summary & Export
- [ ] Human approval required (`yes/no/edit`) before summary persistence.
- [ ] Grouped summary context uses workstream titles.
- [ ] Export supports markdown, terminal, and json.

## Runtime
- [ ] Local inference path configurable via `BITNET_CMD`.
- [ ] Inference healthcheck validates command availability.
- [ ] Scheduler times are config-driven (`config.toml`).
- [ ] Daemon supports start/stop/restart/status and heartbeat health.

## Quality Gates
- [ ] Service tests pass.
- [ ] CLI smoke tests pass.
- [ ] Migration scripts match model constraints.

# worklog-ai

Local-first engineering work memory system.

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

- `init-db` initialize SQLite schema
- `add` capture event (with suggested workstream attach prompts)
- `connect` link event to workstream + audit event
- `reflect` run unresolved reflection session
- `summary` generate, review, edit, and export markdown/json
- `resume` set active workstream context for future captures
- `status` show active workstreams and pending reflections
- `daemon-start|daemon-stop|daemon-status` manage background scheduler daemon
- `workstream create|list|set-status`

## Config

Scheduler reminder times are configurable in `config/config.toml`.

## Inference

Set `BITNET_CMD` to run local model subprocess:

```bash
export BITNET_CMD="/path/to/bitnet_infer"
```

If unset, inference falls back to a placeholder response for local dev.

# Leaf-to-Branch Implementation Plan

## Leaf (foundational units)
- Data models (`db/models.py`) with immutable event-focused schema.
- DB session and engine setup (`db/session.py`) using SQLAlchemy.
- Prompt stubs in `prompts/`.

## Branch (composed services)
- Event capture service (`services/event_service.py`).
- Workstream lifecycle service (`services/workstream_service.py`).
- Inference contract wrapper (`services/inference_service.py`).

## Canopy (user-facing behavior)
- Reflection and summary agents (`agents/`).
- Typer CLI (`cli/main.py`) with init/add/workstream commands.

## Next Iterations
- Add scheduler + notification services.
- Add connect/resume/status/summary/reflect commands.
- Add tests and migration tooling.
